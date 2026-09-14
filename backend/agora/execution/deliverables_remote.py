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
        entries = await self._status_entries(workspace)
        if entries is None:
            return []
        return entries

    async def snapshot_changes(
        self, workspace: Path, baseline: "tuple[dict, bool] | None"
    ) -> list[dict]:
        """Freeze what THIS run delivered, subtracting a pre-run ``baseline``.

        ``baseline`` mirrors the local contract's three shapes: ``(before, complete)``
        with ``complete=True`` subtracts entries already present before the run;
        ``complete=False`` (the pre-run state itself hit MAX_FILES, cannot be subtracted
        reliably) and ``None`` (pre-run state unreadable — a reused worktree may hold an
        earlier run's files) both yield ``[]`` (nothing this run can be proven to own),
        never a guess. Identity here is (path, status): the remote content digest is
        U4's read path, not recomputed per entry over SSH for every snapshot."""
        if baseline is None:
            return []
        before, complete = baseline[0], baseline[1]
        if not complete:
            return []
        entries = await self._status_entries(workspace)
        if entries is None:
            return []
        delivered: list[dict] = []
        for item in entries:
            if before.get(item["path"]) == item["status"]:
                continue  # unchanged since before this run started
            delivered.append(item)
        return delivered

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
        entries = await self._status_entries(workspace)
        if entries is None:
            raise OSError("cannot read remote worktree status before handoff — refusing to commit")
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

    async def _status_entries(self, workspace: Path) -> list[dict] | None:
        """``git status --porcelain=v1 -z`` on the remote, parsed with the SHARED
        upstream parser, capped at MAX_FILES. ``None`` when git could not answer (the
        caller decides whether that is ``[]`` or a reported failure). Read-only, retried."""
        cwd = PurePosixPath(_as_posix(workspace))
        result = await self.env.run(
            [_GIT, "-C", str(cwd), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            timeout=_GIT_TIMEOUT,
            read_only=True,
        )
        if not result.ok:
            return None
        files: list[dict] = []
        for code, path in _porcelain_z_entries(result.stdout):
            if len(files) >= MAX_FILES:
                break
            # size is not fetched here: a per-file remote stat for every entry is a
            # round-trip the change LIST does not need; content/size come from read_file.
            files.append({
                "path": path,
                "status": _STATUS_LABELS.get(code, code or "changed"),
            })
        files.sort(key=lambda item: item["path"])
        return files

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
