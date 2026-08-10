"""Delivery visibility: list the files a run produced in its Git worktree.

Read-only. Reports the worktree's current changes (added/modified/untracked vs
HEAD) so an operator can see what an agent actually delivered, and returns a
bounded, redacted text preview of one confined file.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .security import redact_text

MAX_FILE_BYTES = 64 * 1024
MAX_FILES = 200
_GIT_TIMEOUT = 10

_STATUS_LABELS = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "?": "untracked",
    "!": "ignored",
}


def list_changes(workspace: Path) -> list[dict]:
    """Return the worktree's changed/untracked files vs HEAD (best effort)."""
    git = shutil.which("git")
    if git is None:
        return []
    try:
        result = subprocess.run(
            [git, "-C", str(workspace), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    files: list[dict] = []
    for line in result.stdout.splitlines():
        if not line.strip() or len(line) < 4:
            continue
        code = line[:2].strip()[:1]
        rest = line[3:]
        # Renamed/copied entries look like "old -> new"; keep the new path.
        path = rest.split(" -> ")[-1].strip().strip('"')
        target = workspace / path
        files.append({
            "path": path,
            "status": _STATUS_LABELS.get(code, code or "changed"),
            "size": target.stat().st_size if target.is_file() else None,
        })
        if len(files) >= MAX_FILES:
            break
    files.sort(key=lambda item: item["path"])
    return files


def read_file(workspace: Path, rel_path: str) -> dict:
    """Return a bounded, redacted text preview of a file confined to `workspace`."""
    base = workspace.resolve()
    target = (base / rel_path).resolve()
    if not target.is_relative_to(base):
        raise PermissionError("path escapes the workspace")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    raw = target.read_bytes()[: MAX_FILE_BYTES + 1]
    truncated = len(raw) > MAX_FILE_BYTES
    text = redact_text(raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace"))
    return {"path": rel_path, "content": text, "truncated": truncated}
