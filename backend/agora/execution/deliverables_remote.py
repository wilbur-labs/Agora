"""Remote (SSH) delivery visibility + handoff — W3-a unit U4.

The remote counterpart of :mod:`agora.execution.deliverables`: what a run changed in
its REMOTE worktree, a frozen snapshot of that, a bounded/redacted preview of one
confined remote file, and a handoff commit — all async over the SSH seam (FR-6).

Reused unchanged from the local module (no second implementation): the porcelain-z
parser (``_porcelain_z_entries`` — pure string logic, same git format on any host),
the status labels, and the ``MAX_FILES`` / ``MAX_FILE_BYTES`` bounds. Output redaction
(NFR-4) reuses ``redact_text``. Only the transport differs: git via the async
``AsyncExecutionEnvironment.run`` and file bytes via the ``AsyncFilesystem`` (U3's
``env.fs``); confinement is resolved on the REMOTE (``fs.realpath``), never with a
local ``Path.resolve`` against a path that lives on another host.

Error modes match the frozen contract (``contracts/remote-deliverables.py``): a git
failure yields an empty change list (``[]`` — indistinguishable from clean, as
upstream ``list_changes``), NOT an exception; ``read_file`` raises
FileNotFoundError/PermissionError/OSError for a specific unreadable file (the caller
skips it).
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from agora.environments import AsyncExecutionEnvironment, AsyncFilesystem
from agora.execution.deliverables import (
    MAX_FILE_BYTES,
    MAX_FILES,
    _STATUS_LABELS,
    _porcelain_z_entries,
)
from agora.execution.security import redact_text

_GIT = "git"
_GIT_TIMEOUT = 10
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _as_posix(path) -> str:
    if isinstance(path, str):
        return path
    return path.as_posix()


class RemoteDeliverables:
    """Delivery visibility + handoff for a remote worktree. Constructed with the run's
    async environment and its filesystem seam (U3's ``env.fs``)."""

    def __init__(self, environment: AsyncExecutionEnvironment, filesystem: AsyncFilesystem) -> None:
        self.env = environment
        self.fs = filesystem

    async def list_changes(self, workspace: Path) -> list[dict]:
        """The remote worktree's changed/untracked files vs HEAD (best effort).

        A git failure returns ``[]`` — indistinguishable from clean, exactly as the
        local ``list_changes``; a run's permanent record uses ``snapshot_changes``,
        which reports collection failure instead of hiding it. Read-only, so the git
        query may be retried on a transport error."""
        result = await self._status_entries(workspace)
        if result is None:
            return []
        entries, _truncated = result  # a best-effort view drops the truncation flag
        return entries

    async def status_entries(self, workspace: Path) -> "tuple[list[dict], bool] | None":
        """``git status`` on the remote as ``(entries, truncated)``, or ``None`` when git
        could not answer — kept apart from ``[]`` so a caller can say "could not read"
        instead of "clean"."""
        return await self._status_entries(workspace)

    async def head_commit(self, workspace: Path) -> str | None:
        """``HEAD`` of the remote worktree, or ``None`` when git would not say."""
        cwd = PurePosixPath(_as_posix(workspace))
        result = await self.env.run(
            [_GIT, "-C", str(cwd), "rev-parse", "HEAD"], timeout=_GIT_TIMEOUT, read_only=True,
        )
        if not result.ok:
            return None
        head = (result.stdout or "").strip()
        return head or None

    async def _digests(self, workspace: Path, paths: list[str]) -> "dict[str, str] | None":
        """Content digests of ``paths`` in one round trip (``sha256sum`` on the host).
        ``None`` when the command would not run at all; a path that could not be hashed
        (deleted, a directory) is simply absent from the map."""
        if not paths:
            return {}
        cwd = PurePosixPath(_as_posix(workspace))
        result = await self.env.run(
            ["sha256sum", "--", *[str(cwd / path) for path in paths]],
            timeout=_GIT_TIMEOUT, read_only=True,
        )
        # Semantic success, not "the process ran": a timeout, a missing file or a
        # partial read leaves ``sha256sum`` non-zero, and an incomplete map would let a
        # file this run rewrote pass as unchanged. All or nothing.
        if not result.ok:
            return None
        digests: dict[str, str] = {}
        prefix = str(cwd).rstrip("/") + "/"
        stdout = result.stdout or ""
        for line in stdout.splitlines():
            # GNU coreutils escapes a name holding a newline or backslash and marks the
            # line with a leading backslash; undo that so the path matches git's.
            escaped = line.startswith("\\")
            if escaped:
                line = line[1:]
            if len(line) < 66:
                continue
            digest, _sep, name = line[:64], line[64:66], line[66:]
            if not _HEX64.fullmatch(digest):
                continue
            name = name[1:] if name.startswith("*") else name
            if escaped:
                name = name.replace("\\n", "\n").replace("\\\\", "\\")
            if name.startswith(prefix):
                name = name[len(prefix):]
            if name:
                digests[name] = digest
        # All or nothing once the tool answered: a line that did not parse back to a
        # requested path is a digest we do not have, and an identity compared without
        # it would let a rewritten file pass as unchanged.
        if stdout.strip() and any(path not in digests for path in paths):
            return None
        return digests

    @staticmethod
    def _identity(item: dict, digests: dict[str, str]) -> dict:
        """A comparable identity for one entry: its status and content digest. Without
        a digest (a deletion, a directory) the status alone — the same fallback shape
        the local ``_identity`` takes when it has nothing stronger."""
        digest = digests.get(item["path"])
        return {"status": item["status"], "sha256": digest} if digest else {"status": item["status"]}

    async def baseline_state(self, workspace: Path) -> "tuple[dict[str, dict], bool, str | None] | None":
        """The remote worktree *before* a run starts: ``path -> identity`` (status and
        content digest), whether the read was whole, and ``HEAD``. ``None`` when the
        worktree or the digests cannot be read — then the run's snapshot is ``None``
        too, "could not establish", never "changed nothing". Mirrors the local
        ``baseline_state``: a file modified before the run and modified again by it
        differs by digest, and a run that commits is found by the moved HEAD."""
        result = await self._status_entries(workspace)
        if result is None:
            return None
        entries, truncated = result
        digests = await self._digests(workspace, [e["path"] for e in entries if e["status"] != "deleted"])
        if digests is None:
            return None
        before = {item["path"]: self._identity(item, digests) for item in entries}
        head = await self.head_commit(workspace)
        if head is None:
            # Without the starting commit, a run that commits its work would later read
            # as "changed nothing" from a clean tree. Not a baseline — fail closed.
            return None
        return (before, not truncated, head)

    async def snapshot_changes(
        self, workspace: Path, baseline: "tuple | None"
    ) -> "dict | None":
        """Freeze what THIS run delivered, subtracting a pre-run ``baseline``.

        Returns ``{"files": [...], "truncated": bool}``, or ``None`` when the delivery
        cannot be established — matching the local ``snapshot_changes`` contract exactly
        (store.finish requires a dict with a ``files`` list; ``None`` is the distinct
        "could not establish", never frozen as "changed nothing"). ``truncated`` is True
        when more than ``MAX_FILES`` files changed: the list is then "at least these",
        and a path absent from it is UNKNOWN, not known-not-delivered.

        ``baseline``'s shapes are distinct answers, as local: ``(before, complete=True,
        head)`` — subtract it, and files committed since ``head`` are this run's too;
        ``complete=False`` — the pre-run state itself hit MAX_FILES → ``None``;
        ``None`` — pre-run state unreadable → ``None``. Each delivered entry carries its
        content digest so the file can later be proven to be what the run left.
        """
        if baseline is None:
            return None
        before, complete, *recorded = baseline
        head_before = recorded[0] if recorded else None
        if not complete:
            return None
        result = await self._status_entries(workspace)
        if result is None:
            return None
        entries, truncated = result
        # What the run committed is its delivery as much as what it left uncommitted,
        # and it is asked first: a path that was dirty before the run and is clean now
        # because the run committed it is a committed file, not "no longer differs".
        committed: list[dict] = []
        if head_before is not None:
            head_now = await self.head_commit(workspace)
            if head_now is None:
                return None  # cannot tell whether history moved — not "it did not"
            if head_now != head_before:
                found = await self._committed_between(workspace, head_before, head_now)
                if found is None:
                    return None
                committed = found
        committed_paths = {item["path"] for item in committed}
        to_hash = [e["path"] for e in entries if e["status"] != "deleted"]
        to_hash += [c["path"] for c in committed if c["status"] != "deleted" and c["path"] not in to_hash]
        digests = await self._digests(workspace, to_hash)
        if digests is None:
            return None
        delivered: list[dict] = []
        for item in entries:
            identity = self._identity(item, digests)
            prior = before.get(item["path"])
            # A baseline written before identities carried digests holds the bare status
            # string; it is compared as it was recorded, not treated as "changed".
            unchanged = prior == item["status"] if isinstance(prior, str) else prior == identity
            if unchanged:
                continue  # unchanged since before this run started
            delivered.append({**item, "sha256": identity.get("sha256")})
        if not truncated:
            present = {item["path"] for item in entries}
            for path in sorted(before):
                if path in present or path in committed_paths:
                    continue
                probe = await self.env.probe(PurePosixPath(_as_posix(workspace)) / path)
                if probe.unknown:
                    status = "unreadable"
                elif probe.absent:
                    status = "deleted"
                else:
                    status = "no longer differs"
                delivered.append({"path": path, "status": status, "sha256": None})
        listed = {item["path"] for item in delivered}
        for item in committed:
            if item["path"] in listed:
                continue  # still dirty too: the live entry, with its current digest, stands
            delivered.append({**item, "sha256": digests.get(item["path"]) if item["status"] != "deleted" else None})
        delivered.sort(key=lambda item: item["path"])
        return {"files": delivered, "truncated": truncated}

    async def _committed_between(self, workspace: Path, before: str, now: str) -> "list[dict] | None":
        """Files whose committed content changed between two commits, labelled as
        committed during the run. ``None`` when git would not compare them."""
        cwd = PurePosixPath(_as_posix(workspace))
        result = await self.env.run(
            [_GIT, "-C", str(cwd), "diff", "--name-status", "-z", f"{before}..{now}"],
            timeout=_GIT_TIMEOUT, read_only=True,
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
                old_path, path = path, fields[index]
                index += 1
                if status == "R":
                    # A rename is a deletion of the old path as much as an arrival at
                    # the new one; a reader who only sees the new name never learns
                    # the old file is gone.
                    committed.append({
                        "path": old_path, "status": "deleted", "size": None, "sha256": None,
                        "committed_during_run": True,
                    })
            committed.append({
                "path": path,
                "status": _STATUS_LABELS.get(status, status or "changed"),
                "size": None,
                "sha256": None,
                "committed_during_run": True,
            })
        return committed

    async def read_file(self, workspace: Path, path: str) -> dict:
        """A bounded, redacted UTF-8 preview of one file confined to the remote
        ``workspace``. Confinement is resolved ON THE REMOTE: the target's realpath must
        stay within the workspace's realpath (a symlink escaping the worktree is
        revealed by U3's component-walking realpath, not masked). A missing/unreadable
        file raises (caller skips it), never returns empty content as if clean."""
        ws_posix = PurePosixPath(_as_posix(workspace))
        base = await self.fs.realpath(ws_posix)
        target = await self.fs.realpath(ws_posix / path)
        base_posix = PurePosixPath(_as_posix(base))
        target_posix = PurePosixPath(_as_posix(target))
        if not _is_within(target_posix, base_posix):
            raise PermissionError(f"path escapes the remote workspace: {path}")
        # A PREVIEW read (truncate=True): U3's read_file returns at most this many bytes
        # and never raises for size — reading MAX_FILE_BYTES+1 lets us DETECT truncation
        # (a large deliverable must still return a bounded preview, not raise).
        raw = await self.fs.read_file(target_posix, max_bytes=MAX_FILE_BYTES + 1, truncate=True)
        truncated = len(raw) > MAX_FILE_BYTES
        text = redact_text(raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace"))
        return {"path": path, "content": text, "truncated": truncated}

    async def handoff_commit(self, workspace: Path, message: str) -> dict:
        """Commit the remote worktree's changes and bind the result back to the control
        plane (B1). Returns ``{"commit": <hash>, "files": [<paths>]}``.

        The committed file list is captured from ``git status`` BEFORE the commit (after
        commit the worktree is clean and status is empty). ``git add -A`` then commit;
        the commit hash is read from ``rev-parse HEAD``. These are MUTATING git calls, so
        they are NOT retried on a transport error (``read_only`` left default False) — a
        commit that half-happened must not be blindly repeated."""
        # Capture the file list BEFORE committing (post-commit status is clean). A status
        # FAILURE here is NOT "no files": committing on it would bind a handoff whose file
        # list is a lie. list_changes' git-fail->[] tolerance is for a best-effort VIEW,
        # never for a handoff that writes a record — so abort before any mutation.
        result = await self._status_entries(workspace)
        if result is None:
            raise OSError("cannot read remote worktree status before handoff — refusing to commit")
        entries, truncated = result
        if truncated:
            # The status list is capped at MAX_FILES but `git add -A` would commit
            # ALL changes — the returned file list would then be a lie (fewer paths
            # than committed). A handoff record that undercounts what it bound is
            # worse than none, so fail closed before any mutation rather than commit
            # with an incomplete manifest.
            raise OSError(
                f"remote worktree has more than {MAX_FILES} changes — refusing to bind a "
                "handoff whose file list would be incomplete"
            )
        files = [e["path"] for e in entries]
        cwd = PurePosixPath(_as_posix(workspace))
        await self._git(cwd, ["add", "-A"])
        await self._git(cwd, [
            "-c", "user.email=agora@localhost", "-c", "user.name=Agora",
            "commit", "-m", message, "--allow-empty",
        ])
        commit = (await self._git(cwd, ["rev-parse", "HEAD"])).strip()
        return {"commit": commit, "files": files}

    # --- internals ---

    async def _status_entries(self, workspace: Path) -> "tuple[list[dict], bool] | None":
        """``git status --porcelain=v1 -z`` on the remote, parsed with the SHARED
        upstream parser, capped at MAX_FILES. Returns ``(entries, truncated)`` where
        ``truncated`` is True iff the parser still had entries past the cap — the list
        is then "at least these", matching local ``_read_changes``. ``None`` when git
        could not answer (the caller decides whether that is ``[]`` or a reported
        failure). Read-only, retried."""
        cwd = PurePosixPath(_as_posix(workspace))
        result = await self.env.run(
            [_GIT, "-C", str(cwd), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            timeout=_GIT_TIMEOUT,
            read_only=True,
        )
        if not result.ok:
            return None
        files: list[dict] = []
        truncated = False
        for code, path in _porcelain_z_entries(result.stdout):
            if len(files) >= MAX_FILES:
                # More entries exist than the cap holds: the list is a floor, not the
                # whole set. A path absent from it is UNKNOWN, not known-not-changed.
                truncated = True
                break
            # size is not fetched here: a per-file remote stat for every entry is a
            # round-trip the change LIST does not need; content/size come from read_file.
            files.append({
                "path": path,
                "status": _STATUS_LABELS.get(code, code or "changed"),
            })
        files.sort(key=lambda item: item["path"])
        return files, truncated

    async def _git(self, cwd: PurePosixPath, args: list[str]) -> str:
        """Run one MUTATING git command on the remote (add/commit). Not read_only: a
        transport retry could double a side effect. Raises OSError on failure so a
        broken handoff is loud, never a silent partial commit."""
        result = await self.env.run(
            [_GIT, "-C", str(cwd), *args], timeout=_GIT_TIMEOUT
        )
        if not result.ok:
            raise OSError(
                f"remote git {args[0] if args else '?'} failed: {result.reason()} "
                f"{redact_text(result.stderr)[:300]}"
            )
        return result.stdout


def _is_within(candidate: PurePosixPath, root: PurePosixPath) -> bool:
    """True iff ``candidate`` is ``root`` or below it, by POSIX components (never a
    string prefix — ``/a/bc`` is not within ``/a/b``)."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
