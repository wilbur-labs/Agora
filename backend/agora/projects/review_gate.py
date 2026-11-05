"""What a project's history says about the commit review gate (workspace CR-75 slice 2).

The gate itself lives in claude-workspace (`scripts/review-gate.sh`): a `pre-commit` hook
refuses content no review read, and `commit-msg` writes `Reviewed-by: codex|kiro` or
`Review-skipped: <reason>` plus `Review-patch: <patch-id>`. This module is the check
afterwards (design: docs/CR75-slice2-review-gate.md, part D): it reads the commits made
since the gate was installed and sorts them into

  reviewed    `Reviewed-by: codex|kiro…` and a Review-patch equal to the commit's own
  skipped     `Review-skipped:` and a matching Review-patch
  merge       two or more parents (the gate does not speak for merges)
  unrecorded  anything else: no trailer (a `--no-verify` commit), a human
              `Reviewed-by: <name>`, or a Review-patch that is not this commit's patch
              (a trailer carried along by an amend or a hand-resolved replay)

What it can see and what it cannot: it sees a commit that skipped the hooks. It cannot
see a forged stamp or trailer, nor a whitespace-only change made under a carried-along
trailer (`git patch-id --stable` ignores whitespace, which is also what keeps a clean
rebase "reviewed"; such a change needs `--no-verify`, a deliberate bypass — kiro review
round 3) — the gate stops forgetting, not cheating, and nothing
here may describe it as more. A read that fails is said as failed, never as zero.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Commits read at most; beyond that the result says so.
LIMIT = 500
_TIMEOUT = 20
#: The diff options the gate's hooks use for Review-patch. Any difference and no id
#: would ever match: keep in step with claude-workspace scripts/review-gate/lib.sh.
DIFF_OPTS = ("--binary", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv",
             "--no-relative", "--diff-algorithm=myers", "--src-prefix=a/", "--dst-prefix=b/")
_GATE_REVIEWERS = {"codex", "kiro"}
_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")

#: patch-id by (repository, commit sha): a commit's patch does not change, but a clone's
#: own attributes can change how its diff is drawn, so the key is per repository.
_patch_ids: dict[tuple[str, str], str] = {}
#: Commits per `git log` call: 100 SHA-256 ids stay far under Windows' command-line limit.
_BATCH = 100


@dataclass(frozen=True)
class ReviewGateRead:
    #: "not_installed" | "read" | "unreadable"
    state: str
    reason: str | None = None
    reviewed: int = 0
    skipped: int = 0
    merges: int = 0
    unrecorded: int = 0
    #: More commits than LIMIT since the install: only the latest LIMIT were read.
    truncated: bool = False

    def payload(self) -> dict[str, Any]:
        if self.state != "read":
            return {"state": self.state, "reason": self.reason}
        return {"state": "read", "reviewed": self.reviewed, "skipped": self.skipped,
                "merges": self.merges, "unrecorded": self.unrecorded, "truncated": self.truncated}


NOT_INSTALLED = ReviewGateRead(state="not_installed")


def _git(repo: Path, *args: str, stdin: bytes | None = None, check: bool = True) -> bytes:
    done = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(repo), *args],
                          input=stdin, capture_output=True, timeout=_TIMEOUT, check=False)
    if check and done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return done.stdout


def _stat(path: Path) -> os.stat_result | None:
    """None only when the path is not there. Any other failure (no permission, a share
    that went away) raises: "could not look" must not read as "not installed" (kiro
    review round 2)."""
    try:
        return path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _common_dir(repo: Path) -> Path | None:
    """The repository's git common dir, without starting git for the usual case."""
    dot_git = repo / ".git"
    st = _stat(dot_git)
    if st is None:
        return None
    if stat.S_ISDIR(st.st_mode):
        return dot_git
    # A worktree's `.git` file: ask git where the shared directory is.
    out = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(out.decode("utf-8").strip())


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    done = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
                          capture_output=True, timeout=_TIMEOUT, check=False)
    if done.returncode in (0, 1):
        return done.returncode == 0
    raise RuntimeError(done.stderr.decode("utf-8", "replace").strip() or "git merge-base failed")


def _trailers(block: str) -> list[tuple[str, str]]:
    found = []
    for line in block.split("\x01"):
        key, sep, value = line.partition(":")
        if sep:
            found.append((key.strip(), value.strip()))
    return found


def _patch_ids_for(repo: Path, common: Path, shas: list[str]) -> dict[str, str]:
    """Each commit's patch-id against its first parent; "none" for an empty change."""
    where = os.path.normcase(str(common))
    missing = [s for s in shas if (where, s) not in _patch_ids]
    for start in range(0, len(missing), _BATCH):
        batch = missing[start:start + _BATCH]
        # --root: a root commit's diff against the empty tree, whatever log.showRoot says.
        log = _git(repo, "log", "-p", "--root", "--no-walk=unsorted", "--format=commit %H", *DIFF_OPTS, *batch)
        ids = _git(repo, "patch-id", "--stable", stdin=log).decode("utf-8")
        found = {}
        for line in ids.splitlines():
            parts = line.split()
            if len(parts) == 2:
                found[parts[1]] = parts[0]
        for sha in batch:
            _patch_ids[(where, sha)] = found.get(sha, "none")
    return {s: _patch_ids[(where, s)] for s in shas}


def read_review_gate(repo_path: str | None) -> ReviewGateRead:
    """The gate's record for the repository at ``repo_path`` (a task's ``primary_repo``)."""
    if not repo_path:
        return NOT_INSTALLED
    repo = Path(repo_path).expanduser()
    if not repo.is_absolute():
        # Relative to what? The server's directory is not the task's; guessing could read
        # another repository's record (kiro review round 2).
        return ReviewGateRead(state="unreadable", reason="primary_repo is not an absolute path")
    try:
        common = _common_dir(repo)
        if common is None:
            return NOT_INSTALLED
        marker = common / "agora-review" / "installed"
        if _stat(marker) is None:
            return NOT_INSTALLED
        installed = json.loads(marker.read_text(encoding="utf-8"))
        # `null`, `[]`, `{}`, `{"commit": ""}`: an install record that does not say where
        # the gate started is unreadable — never read as "count everything".
        if not isinstance(installed, dict) or "commit" not in installed:
            return ReviewGateRead(state="unreadable", reason="install record has no commit field")
        since = installed["commit"]
        if since is not None and not (isinstance(since, str) and _SHA.fullmatch(since)):
            return ReviewGateRead(state="unreadable", reason="install record has no usable commit")
        head = _git(repo, "rev-parse", "-q", "--verify", "HEAD^{commit}", check=False).decode().strip()
        if not head:
            # Empty only if git can list refs and there are none; a git that cannot read
            # the repository at all must not come back as "nothing committed" (kiro
            # review round 3).
            if since is None and not _git(repo, "for-each-ref", "--count=1").strip():
                return ReviewGateRead(state="read")
            return ReviewGateRead(state="unreadable", reason="no HEAD commit could be read")
        if since is not None:
            # A branch that does not contain the install commit (a worktree on an older
            # line) would count its pre-install history as after (kiro review round 2).
            if not _is_ancestor(repo, since, head):
                return ReviewGateRead(state="unreadable",
                                      reason="this branch does not contain the commit the gate was installed at")
            rev_range = [f"{since}..{head}"]
        else:
            rev_range = [head]
        raw = _git(repo, "log", f"--max-count={LIMIT + 1}",
                   "--format=%H%x00%P%x00%(trailers:key=Reviewed-by,key=Review-skipped,"
                   "key=Review-patch,unfold,separator=%x01)%x00", *rev_range).decode("utf-8", "replace")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return ReviewGateRead(state="unreadable", reason=str(exc)[:200] or type(exc).__name__)

    fields = raw.split("\x00")
    commits = []
    for i in range(0, len(fields) - 2, 3):
        sha = fields[i].strip()
        if sha:
            commits.append((sha, fields[i + 1].split(), _trailers(fields[i + 2].strip())))
    truncated = len(commits) > LIMIT
    commits = commits[:LIMIT]
    try:
        patches = _patch_ids_for(repo, common, [sha for sha, parents, _ in commits if len(parents) < 2])
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return ReviewGateRead(state="unreadable", reason=str(exc)[:200] or type(exc).__name__)

    counts = {"reviewed": 0, "skipped": 0, "merges": 0, "unrecorded": 0}
    for sha, parents, trailers in commits:
        if len(parents) >= 2:
            counts["merges"] += 1
            continue
        recorded_patch = [v for k, v in trailers if k == "Review-patch"]
        matches = len(recorded_patch) == 1 and recorded_patch[0] == patches.get(sha)
        reviewers = [v for k, v in trailers if k == "Reviewed-by"]
        skipped = [v for k, v in trailers if k == "Review-skipped" and v]  # an empty reason is none
        by_gate = any(v and {r.strip() for r in v.split(",")} <= _GATE_REVIEWERS for v in reviewers)
        if matches and skipped:
            counts["skipped"] += 1
        elif matches and by_gate:
            counts["reviewed"] += 1
        else:
            counts["unrecorded"] += 1
    return ReviewGateRead(state="read", truncated=truncated, **counts)
