"""Delivery visibility: what files a run produced in its Git worktree.

Read-only. Two different questions live here, and keeping them apart is the
whole point:

- ``list_changes`` answers "what is changed in this worktree **right now**".
  That is the truth only while a run is still active.
- ``snapshot_changes`` freezes that list once, when a run reaches a terminal
  state, so every later read can answer "what did **this run** deliver".

Serving a finished run's file list from the live worktree is wrong in three
ordinary situations: after a handoff commit the worktree is clean and the list
reads empty; a later run in the same worktree gets its changes attributed to the
earlier one; and two runs in one worktree show an identical list. So a terminal
run is served from its snapshot, and a terminal run with no snapshot reports
exactly that rather than falling back to the worktree.

Also returns a bounded, redacted text preview of one confined file.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from agora.environments import Environment, LocalEnvironment

from .security import redact_text

MAX_FILE_BYTES = 64 * 1024
MAX_FILES = 200
# Digest limit for a run's snapshot, deliberately far above the 64 KiB *preview*
# limit: a digest is what lets a later read prove a file still holds what the run
# delivered, and a file with no digest cannot be opened under that run at all.
# Hashing is cheap, so keep this generous enough that ordinary deliverables —
# built pages, bundles, generated docs — stay openable.
SNAPSHOT_HASH_LIMIT = 8 * 1024 * 1024
_GIT_TIMEOUT = 10
#: argv[0] for every git call here. The environment resolves it -- on a remote one
#: the local PATH says nothing about whether git is there, so a `shutil.which`
#: pre-check would be answering about the wrong machine.
_GIT = "git"

_STATUS_LABELS = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "?": "untracked",
    "!": "ignored",
}


def _same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError):  # RuntimeError: symlink loop on older Pythons
        return False


def _is_provisioned_worktree(env: Environment, workspace: Path) -> bool:
    return provisioned_worktree_check(env, workspace)[0]


def provisioned_worktree_check(
    env: Environment, workspace: Path
) -> tuple[bool, str | None]:
    """``(is_provisioned, unavailable_reason)``.

    Two answers, because they call for opposite responses. ``False`` with a reason
    means git itself never ran or timed out and nothing was established about the
    workspace; ``False`` with no reason means git ran and the workspace is not a
    provisioned worktree. Callers that only report a boolean use
    :func:`_is_provisioned_worktree` and fail closed on both, which is right for a
    gate; callers that write the outcome down for a human need the difference, or
    a missing git gets recorded as a verdict about the operator's workspace.

    True only if `workspace` is a linked git worktree whose own top-level is
    exactly `workspace` — i.e. a provisioned per-adapter worktree, not a repo
    root, the project root, or a plain nested directory.

    Fail closed. Deliverable discovery and handoff must operate strictly on a
    run's own worktree, never on the surrounding repository. Two conditions:

    - ``--show-toplevel`` equals `workspace`, so we are not merely nested inside
      an ancestor repo (whose repo-wide changes we must never report/commit).
    - the per-worktree git dir differs from the shared common dir, which is true
      only for a *linked* worktree. A main worktree — including a bare project
      root or the outer Agora checkout — has them equal and is rejected, so a
      workspace mistakenly mapped onto (or aliased to) a repo root can never
      have its whole repository staged and committed.
    """
    result = env.run(
        [_GIT, "-C", str(workspace), "rev-parse",
         "--show-toplevel", "--absolute-git-dir", "--git-common-dir"],
        timeout=_GIT_TIMEOUT,
    )
    if not result.ran:
        return False, f"git is not available ({result.reason()})"
    if result.outcome == "timeout":
        return False, "git did not answer in time"
    if not result.ok:
        # git ran and refused. That is *usually* "not a repository", but the same
        # exit 128 covers `safe.directory` refusing a repo it does not trust and an
        # unreadable `.git`. The three are not separable without parsing a message
        # git may translate, so this reports what was actually established -- the
        # check did not pass -- and leaves the caller to phrase it as such rather
        # than as a verdict about the directory.
        return False, None
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) != 3:  # expect exactly the three requested paths
        return False, None
    toplevel, git_dir, common_dir = lines[0], lines[1], lines[2]
    if not _same_path(toplevel, workspace):
        return False, None
    # --git-common-dir may be relative to the git dir's parent; anchor it.
    common = Path(common_dir)
    if not common.is_absolute():
        common = workspace / common
    return not _same_path(git_dir, common), None


def _rev_parse_result(
    env: Environment, cwd: Path, args: list[str]
) -> tuple[str | None, str | None]:
    """``(stdout, unavailable_reason)``. The reason is set only when git did not run
    or did not finish — a non-zero exit is git answering, and is left unclassified for
    the same reason :func:`provisioned_worktree_check` leaves it unclassified."""
    result = env.run([_GIT, "-C", str(cwd), "rev-parse", *args], timeout=_GIT_TIMEOUT)
    if result.ok:
        return result.stdout.strip(), None
    if not result.ran:
        return None, f"git is not available ({result.reason()})"
    if result.outcome == "timeout":
        return None, "git did not answer in time"
    return None, None


def _repo_root_check(env: Environment, path: Path) -> tuple[bool, str | None]:
    """True only if `path` is a repository's own main worktree root — its
    `--show-toplevel` is itself AND its git dir equals its common dir (so it is
    NOT a linked worktree of some other repository). Used to prove a project
    root genuinely owns its repository before trusting its common dir."""
    top, why = _rev_parse_result(env, path, ["--show-toplevel"])
    if why:
        return False, why
    if not top or not _same_path(top, path):
        return False, None
    git_dir, why = _rev_parse_result(env, path, ["--absolute-git-dir"])
    if why:
        return False, why
    common, why = _rev_parse_result(env, path, ["--path-format=absolute", "--git-common-dir"])
    if why:
        return False, why
    return bool(git_dir and common and _same_path(git_dir, common)), None


def is_ready_worktree(
    workspace: Path, project_root: Path, expected_branch: str, *,
    env: Environment | None = None,
) -> bool:
    """Boolean form of :func:`ready_worktree_check`, for gates that only fail closed."""
    return ready_worktree_check(workspace, project_root, expected_branch, env=env)[0]


def ready_worktree_check(
    workspace: Path, project_root: Path, expected_branch: str, *,
    env: Environment | None = None,
) -> tuple[bool, str | None]:
    """Positive, fail-closed check that `workspace` is exactly the provisioned
    linked worktree for a run: its own top-level, a *linked* worktree, sharing
    the project root's repository, checked out on `expected_branch`, with a HEAD.

    This is the authoritative readiness gate used before launching a process and
    before reading/committing deliverables. It binds a workspace to its project
    and adapter, so a correctly-shaped but *unrelated* worktree (or the wrong
    branch) is rejected — not just a foreign directory. Anything indeterminate
    (git missing, timeout, error, unexpected output) returns False: an agent must
    never run, and deliverables must never be read or committed, on a workspace
    we cannot positively prove is its own provisioned worktree.
    """
    env = env or LocalEnvironment()
    provisioned, unavailable = provisioned_worktree_check(env, workspace)
    if unavailable is not None:
        # git never ran, or ran out of time. Nothing about the workspace was
        # established, and the caller that writes this into a record needs to say so
        # instead of reporting a verdict it does not have.
        return False, unavailable
    if not provisioned:
        return False, None
    # Every step below is one more git call, and any of them can be the one where git
    # goes away or hangs. The reason has to survive all of them: a tuple that carries
    # unavailability only from its first call has a channel narrower than its
    # contract, which is this project's most-repeated defect wearing a new hat.
    #
    # The project root must genuinely own its repository: its own main-worktree
    # root, not nested inside an ancestor checkout and not itself a linked
    # worktree of another repo — otherwise root_common would resolve to that
    # other repository and one of its worktrees on the expected branch would pass.
    is_root, why = _repo_root_check(env, project_root)
    if why:
        return False, why
    if not is_root:
        return False, None
    ws_common, why = _rev_parse_result(
        env, workspace, ["--path-format=absolute", "--git-common-dir"]
    )
    if why:
        return False, why
    root_common, why = _rev_parse_result(
        env, project_root, ["--path-format=absolute", "--git-common-dir"]
    )
    if why:
        return False, why
    if not ws_common or not root_common or not _same_path(ws_common, root_common):
        return False, None
    branch, why = _rev_parse_result(env, workspace, ["--abbrev-ref", "HEAD"])
    if why:
        return False, why
    if branch != expected_branch:
        return False, None
    head, why = _rev_parse_result(env, workspace, ["--verify", "HEAD"])
    if why:
        return False, why
    return bool(head), None


def _read_changes(workspace: Path, env: Environment) -> tuple[list[dict], bool] | None:
    """``(files, truncated)`` for the worktree's changes vs HEAD, or None if the
    answer could not be obtained (no git, not a provisioned worktree, git error
    or timeout).

    None and `[]` are different answers and callers must keep them apart: `[]`
    is "verified clean", None is "we do not know". Collapsing them is how a
    failed `git status` gets frozen into a run's record as "changed nothing".

    ``truncated`` is True when more than ``MAX_FILES`` files changed, so callers
    can say "at least these" instead of implying the list is complete.
    """
    if not _is_provisioned_worktree(env, workspace):
        return None
    # `-z`: NUL-separated, never quoted, and a rename's two paths are two
    # separate records. Without it the format is ambiguous by construction — a
    # file may legitimately be called `a -> b.txt` (git quotes neither the space
    # nor the arrow), and one called ` spaced.txt ` loses its own name to a
    # strip(). Guessing where the path starts and ends silently recorded a file
    # that does not exist, so its size, digest and contents were unreachable and
    # the real change was left out of review.
    result = env.run(
        [_GIT, "-C", str(workspace), "status", "--porcelain=v1", "-z",
         "--untracked-files=all"],
        timeout=_GIT_TIMEOUT,
    )
    if not result.ok:
        return None
    files: list[dict] = []
    truncated = False
    for code, path in _porcelain_z_entries(result.stdout):
        if len(files) >= MAX_FILES:
            truncated = True
            break
        target = workspace / path
        files.append({
            "path": path,
            "status": _STATUS_LABELS.get(code, code or "changed"),
            "size": target.stat().st_size if target.is_file() else None,
        })
    files.sort(key=lambda item: item["path"])
    return files, truncated


def _porcelain_z_entries(stdout: str) -> list[tuple[str, str]]:
    """Parse ``git status --porcelain=v1 -z`` into (status code, path) pairs.

    The format, exactly: each record is ``XY <path>`` terminated by NUL, and a
    rename or copy (``R``/``C``) is followed by **one more** NUL-terminated
    record holding the *original* path. Nothing is quoted and nothing is
    escaped, so the path is whatever bytes lie between the third character and
    the NUL — including spaces, arrows and quotes.

    **Both halves of a rename are returned.** The source record used to be
    consumed and thrown away, so a snapshot said "R archive/prod.yaml" and
    nothing at all about ``config/prod.yaml`` having stopped existing — review,
    approval and any later restore were left unaware that a file had been
    removed. A copy is different: its source is untouched, so only the new path
    is a change.
    """
    entries: list[tuple[str, str]] = []
    removed: list[str] = []
    records = [record for record in stdout.split("\0") if record]
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        # XY: the index state and the **worktree** state. Reading only X
        # reported `MD` — staged as modified, then deleted from the worktree —
        # as "modified", so the snapshot and the approval screen showed a file
        # that is not there any more, and a later step could act on it as though
        # it were. The worktree column is what describes the disk; the index
        # column is the answer only when the worktree has nothing to say.
        index_state, worktree_state = record[0], record[1]
        code = (
            worktree_state if worktree_state not in (" ", "")
            else index_state
        ).strip()[:1]
        path = record[3:]
        source = None
        if index_state in ("R", "C") or worktree_state in ("R", "C"):
            # The next record is the source path of this rename or copy.
            if index < len(records):
                source = records[index]
            index += 1
        if path:
            entries.append((code, path))
        if source and (index_state == "R" or worktree_state == "R"):
            removed.append(source)
    # A rename's source is gone from the worktree — **unless something put it
    # back**. `git mv old new` followed by a new `old` produces both a rename and
    # an untracked `old`, and adding the deletion anyway made the snapshot say
    # the same file was deleted and present at once. Collected and filtered
    # against the paths git actually reported.
    reported = {path for _code, path in entries}
    entries.extend(("D", source) for source in removed if source not in reported)
    return entries


def _porcelain_path(entry: str) -> str:
    """The path git means, from one **quoted** ``status --porcelain`` entry.

    Kept for readers that cannot pass ``-z``. The snapshot path does pass it, and
    that is the parse to trust: this one has to guess where a path ends, and a
    file may legitimately be called ``a -> b.txt``.

    Two things this has to undo, and stripping quotes does neither:

    * **C-style escapes.** With ``core.quotePath`` — the default — a name with
      any non-ASCII byte comes back quoted and octal-escaped, so
      ``日本語.txt`` arrives as ``"\346\227\245..."``. Taken literally, the
      snapshot recorded a path that does not exist: no size, no digest, and the
      real file could not be reached by anything downstream. On this user's
      machines a Japanese filename is not an edge case.
    * **A rename arrow inside a quoted name.** ``old -> new`` is the separator,
      but a file can be *called* ``a -> b``. Splitting the raw line first turned
      such a name into a path that was never touched.
    """
    text = entry.strip()
    if text.startswith('"'):
        # A quoted entry is one C-quoted string; a rename is `"old" -> "new"` and
        # the arrow only counts *between* them.
        closing = _closing_quote(text)
        if closing is not None:
            remainder = text[closing + 1:].lstrip()
            if remainder.startswith("->"):
                return _porcelain_path(remainder[2:])
            return _unescape_c_quoted(text[1:closing])
    if " -> " in text:
        return _porcelain_path(text.split(" -> ", 1)[1])
    return text


def _closing_quote(text: str) -> int | None:
    """Index of the quote that closes the one at position 0, escapes respected."""
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index
        index += 1
    return None


def _unescape_c_quoted(body: str) -> str:
    """Undo git's C-style quoting, including its octal byte escapes.

    Octal escapes are **bytes** of the path's own encoding (UTF-8 for these
    repositories), so they are collected and decoded together — decoding them one
    at a time would turn every multi-byte character into mojibake, which is the
    same defect one layer down.
    """
    simple = {"n": b"\n", "t": b"\t", "r": b"\r", "\\": b"\\", '"': b'"',
              "a": b"\a", "b": b"\b", "f": b"\f", "v": b"\v"}
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(body):
            break
        marker = body[index]
        if marker in simple:
            out.extend(simple[marker])
            index += 1
            continue
        if marker.isdigit():
            digits = body[index:index + 3]
            try:
                out.append(int(digits, 8))
            except ValueError:
                out.extend(("\\" + digits).encode("utf-8"))
            index += 3
            continue
        out.extend(marker.encode("utf-8"))
        index += 1
    return out.decode("utf-8", errors="replace")


def list_changes(workspace: Path, *, env: Environment | None = None) -> list[dict]:
    """Return the worktree's changed/untracked files vs HEAD (best effort).

    Scoped to the workspace's own repository: if the workspace is not itself a
    git top-level, returns no changes rather than reporting an ancestor repo's.
    Best-effort by design — for a run's permanent record use ``snapshot_changes``,
    which reports collection failure instead of hiding it as "no changes".
    """
    read = _read_changes(workspace, env or LocalEnvironment())
    return read[0] if read is not None else []


def _file_sha256(target: Path) -> str | None:
    """Digest of a file's bytes, or None when there is nothing to digest — it is
    unreadable, not a regular file (deleted, a directory, a device), or above
    ``SNAPSHOT_HASH_LIMIT``.

    None therefore means "no verifiable identity", not merely "big file", so
    readers must fail closed on it rather than trusting whatever is at the path
    now — a later run can delete and recreate the same path."""
    raw = _read_bounded(target)
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _read_bounded(target: Path) -> bytes | None:
    """A file's bytes, or None if it is not a regular file, cannot be read, or
    exceeds ``SNAPSHOT_HASH_LIMIT``.

    Reads ``limit + 1`` bytes from one open handle rather than checking
    ``stat().st_size`` and then reading: a later run rewriting the same path can
    grow the file between those two steps, which would let an arbitrarily large
    file through a size check that had already passed.
    """
    try:
        if not target.is_file():
            return None
        with target.open("rb") as handle:
            raw = handle.read(SNAPSHOT_HASH_LIMIT + 1)
    except OSError:
        return None
    return None if len(raw) > SNAPSHOT_HASH_LIMIT else raw


def _identity(workspace: Path, item: dict) -> dict:
    """A comparable identity for one changed entry.

    A digest is the strong form. When there is none — the entry is a deletion,
    not a regular file, or above the hash limit — fall back to (size, mtime_ns),
    because otherwise two *different* no-digest entries both compare as None and
    an earlier run's deletion would be re-attributed to every later run.
    """
    target = workspace / item["path"]
    digest = _file_sha256(target)
    if digest is not None:
        return {"status": item["status"], "sha256": digest}
    try:
        stat = target.stat()
        stamp: tuple[int, int] | None = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        stamp = None  # genuinely absent (a deletion)
    return {"status": item["status"], "sha256": None, "stamp": stamp}


def baseline_state(
    workspace: Path, *, env: Environment | None = None
) -> tuple[dict[str, dict], bool] | None:
    """``(identities, complete)`` for what was already changed in this worktree
    *before* a run starts, or None when the worktree cannot be read.

    The dispatcher may queue another run on a task that is already RUNNING, and
    a retry reuses the same worktree, so a worktree is not guaranteed to be clean
    at launch. Without this baseline the next run's snapshot would permanently
    claim the previous run's files as its own.

    ``complete`` is False when the pre-run state itself hit ``MAX_FILES``. The
    baseline is then only a prefix, and subtracting it cannot separate this run's
    work from what was already there.
    """
    environment = env or LocalEnvironment()
    read = _read_changes(workspace, environment)
    if read is None:
        return None
    files, truncated = read
    # The commit the worktree was on. A run that *commits* its work leaves a
    # clean `git status`, so comparing status alone reported "delivered nothing"
    # — automatic review saw no files, and handoff then bound a commit nothing
    # had actually reviewed. With this, the snapshot can ask what moved.
    return (
        {item["path"]: _identity(workspace, item) for item in files},
        not truncated,
        _head_commit(workspace, environment),
    )


def _head_commit(workspace: Path, env: Environment) -> str | None:
    """The commit HEAD points at, or None when it cannot be read."""
    result = env.run(
        [_GIT, "-C", str(workspace), "rev-parse", "HEAD"], timeout=_GIT_TIMEOUT
    )
    if not result.ok:
        return None
    head = (result.stdout or "").strip()
    return head or None


def _committed_during_run(
    workspace: Path, env: Environment, before_head: str
) -> list[dict] | None:
    """Files whose content moved between ``before_head`` and HEAD.

    None when the comparison cannot be made — an unborn branch, a rewritten
    history, a git that would not answer. "I could not tell" is not "nothing
    happened", so the caller says so rather than reporting an empty list.
    """
    now = _head_commit(workspace, env)
    if now is None or now == before_head:
        return []
    result = env.run(
        [_GIT, "-C", str(workspace), "diff", "--name-status", "-z",
         f"{before_head}..{now}"],
        timeout=_GIT_TIMEOUT,
    )
    if not result.ok:
        return None
    fields = [field for field in (result.stdout or "").split("\0") if field]
    committed: list[dict] = []
    index = 0
    while index + 1 < len(fields):
        status, path = fields[index][:1], fields[index + 1]
        index += 2
        if status in ("R", "C") and index < len(fields):
            # A rename's second path is the new one.
            path = fields[index]
            index += 1
        committed.append({
            "path": path,
            "status": _STATUS_LABELS.get(status, status or "changed"),
            "size": None,
            "sha256": None,
            "committed_during_run": True,
        })
    return committed


# An explicit, complete "the worktree held nothing before this run" baseline.
# Pass this rather than None when there genuinely is no prior state: at the
# ``baseline`` parameter None means *the baseline could not be established*, and
# conflating the two is how an earlier run's files get frozen as this run's.
CLEAN_BASELINE: tuple[dict[str, dict], bool] = ({}, True)


def snapshot_changes(
    workspace: Path, baseline: tuple[dict[str, dict], bool] | None, *,
    env: Environment | None = None,
) -> dict | None:
    """Freeze what *this run* delivered, as it reaches a terminal state.

    Returns ``{"files": [...], "truncated": bool}``, or None when the delivery
    cannot be established — so a failed `git status` is never frozen as "this run
    changed nothing", and `files == []` means verified-clean.

    ``truncated`` is True when more than ``MAX_FILES`` files changed. The list is
    then "at least these", and a path absent from it is *unknown* rather than
    known-not-delivered.

    ``baseline`` is required, and each of its three shapes is a distinct answer:

    - a tuple with ``complete=True`` — subtract it; entries unchanged since then
      belong to an earlier run.
    - a tuple with ``complete=False`` — the pre-run state itself hit MAX_FILES, so
      it cannot be subtracted reliably: returns None.
    - **None** — the pre-run state could not be read at all (a git error or
      timeout). A reused worktree may already hold an earlier run's files, and
      without the baseline they are indistinguishable from this run's work, so
      this also returns None. Callers with genuinely no prior state pass
      ``CLEAN_BASELINE``, never None.
    """
    if baseline is None:
        return None
    environment = env or LocalEnvironment()
    read = _read_changes(workspace, environment)
    if read is None:
        return None
    # Two shapes are accepted: the pair this used to be, and the triple that also
    # records the commit the worktree was on. The pair means no commit was
    # recorded, so work this run committed cannot be attributed — stated below
    # rather than reported as "nothing delivered".
    before, complete, *recorded_head = baseline
    before_head = recorded_head[0] if recorded_head else None
    if not complete:
        return None
    files, truncated = read
    delivered: list[dict] = []
    for item in files:
        identity = _identity(workspace, item)
        if before.get(item["path"]) == identity:
            continue  # unchanged since before this run started
        item["sha256"] = identity["sha256"]
        delivered.append(item)
    # A path the baseline held and the final list does not is a change too — the
    # run deleted it, moved it, or committed it away. Iterating only what git
    # still reports recorded such a run as having delivered *nothing*, so review,
    # approval and any later restore never learned the file had gone. Skipped
    # when the list was truncated: an absent path may simply be one of the
    # entries that did not fit, and inventing a deletion is worse than silence.
    if not truncated:
        present = {item["path"] for item in files}
        for path in sorted(before):
            if path in present:
                continue
            # **Why** it left the list decides what this row says. A file can
            # stop being reported because it was deleted, or because the run put
            # it back the way HEAD has it — reverted, or committed. Calling the
            # second one "deleted" told review and approval to expect a file
            # that is sitting right there, and any restore step built on that
            # would have removed it.
            # `exists()` answers False for a file this process cannot reach —
            # the ACL case CR-43 measured on this very machine — and for a
            # dangling symlink. Calling either "deleted" tells review and any
            # restore step that a file that is still there should be gone.
            # `lstat` distinguishes the three answers: there, not there, and
            # cannot tell.
            try:
                (workspace / path).lstat()
                still_there: bool | None = True
            except FileNotFoundError:
                still_there = False
            except OSError:
                still_there = None
            delivered.append({
                "path": path,
                "status": (
                    "no longer differs" if still_there
                    else "unreadable" if still_there is None
                    else _STATUS_LABELS.get("D", "deleted")
                ),
                "size": None,
                "sha256": None,
                # Said plainly: this row is derived from the path no longer being
                # reported, not from a status git printed for it.
                "gone_since_baseline": True,
                "exists": still_there,
            })
    committed_unknown = False
    if before_head:
        committed = _committed_during_run(workspace, environment, before_head)
        if committed is None:
            committed_unknown = True
        else:
            known = {item["path"] for item in delivered}
            delivered.extend(item for item in committed if item["path"] not in known)
    result: dict = {"files": delivered, "truncated": truncated}
    if committed_unknown or not before_head:
        # Said out loud: this run may have committed work that is not in the list
        # above, and the comparison that would have found it could not be made.
        result["commits_unread"] = True
    return result


def resolve_confined(workspace: Path, rel_path: str) -> Path:
    """Resolve `rel_path` to an existing file strictly confined to `workspace`."""
    base = workspace.resolve()
    target = (base / rel_path).resolve()
    if not target.is_relative_to(base):
        raise PermissionError("path escapes the workspace")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


def digest_of(workspace: Path, rel_path: str) -> str | None:
    """Digest of one confined file as it stands right now, or None when it is
    missing, unreadable, escapes the workspace, or exceeds the hash limit.
    Used to check a file still holds what a run recorded delivering."""
    try:
        return _file_sha256(resolve_confined(workspace, rel_path))
    except (FileNotFoundError, PermissionError, OSError):
        return None


def read_bytes_verified(
    workspace: Path, rel_path: str, expected_sha256: str
) -> bytes | None:
    """Read a confined file **once** and return those bytes only if they hash to
    ``expected_sha256``; None on mismatch.

    Verifying one read and then serving a second read (or handing a path to a
    response that opens it later) leaves a window in which the file can change
    in between, so the old run would serve the new bytes. The caller must build
    both the preview and the raw response from the bytes returned here.
    """
    target = resolve_confined(workspace, rel_path)
    # Bounded read from a single handle. A recorded digest means the file was
    # within SNAPSHOT_HASH_LIMIT when the run ended, so anything larger now has
    # already changed; reading it in full to discover that would let a later run
    # swap in a huge file and exhaust memory on a request for the old run.
    raw = _read_bounded(target)
    if raw is None:
        return None
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        return None
    return raw


def preview_of(rel_path: str, raw: bytes) -> dict:
    """The same bounded, redacted preview ``read_file`` returns, from bytes the
    caller already holds and has verified."""
    truncated = len(raw) > MAX_FILE_BYTES
    text = redact_text(raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace"))
    return {"path": rel_path, "content": text, "truncated": truncated}


def read_file(workspace: Path, rel_path: str) -> dict:
    """Return a bounded, redacted text preview of a file confined to `workspace`."""
    target = resolve_confined(workspace, rel_path)
    raw = target.read_bytes()[: MAX_FILE_BYTES + 1]
    truncated = len(raw) > MAX_FILE_BYTES
    text = redact_text(raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace"))
    return {"path": rel_path, "content": text, "truncated": truncated}
