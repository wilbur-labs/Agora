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


def _same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError):  # RuntimeError: symlink loop on older Pythons
        return False


def _is_provisioned_worktree(git: str, workspace: Path) -> bool:
    """True only if `workspace` is a linked git worktree whose own top-level is
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
    try:
        result = subprocess.run(
            [git, "-C", str(workspace), "rev-parse",
             "--show-toplevel", "--absolute-git-dir", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) != 3:  # expect exactly the three requested paths
        return False
    toplevel, git_dir, common_dir = lines[0], lines[1], lines[2]
    if not _same_path(toplevel, workspace):
        return False
    # --git-common-dir may be relative to the git dir's parent; anchor it.
    common = Path(common_dir)
    if not common.is_absolute():
        common = workspace / common
    return not _same_path(git_dir, common)


def _rev_parse(git: str, cwd: Path, args: list[str]) -> str | None:
    """Run `git -C cwd rev-parse <args>` and return stripped stdout, or None if
    git errored/timed out/exited nonzero (indeterminate)."""
    try:
        result = subprocess.run(
            [git, "-C", str(cwd), "rev-parse", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _is_repo_root(git: str, path: Path) -> bool:
    """True only if `path` is a repository's own main worktree root — its
    `--show-toplevel` is itself AND its git dir equals its common dir (so it is
    NOT a linked worktree of some other repository). Used to prove a project
    root genuinely owns its repository before trusting its common dir."""
    top = _rev_parse(git, path, ["--show-toplevel"])
    if not top or not _same_path(top, path):
        return False
    git_dir = _rev_parse(git, path, ["--absolute-git-dir"])
    common = _rev_parse(git, path, ["--path-format=absolute", "--git-common-dir"])
    return bool(git_dir and common and _same_path(git_dir, common))


def is_ready_worktree(workspace: Path, project_root: Path, expected_branch: str) -> bool:
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
    git = shutil.which("git")
    if git is None:
        return False
    if not _is_provisioned_worktree(git, workspace):
        return False
    # The project root must genuinely own its repository: its own main-worktree
    # root, not nested inside an ancestor checkout and not itself a linked
    # worktree of another repo — otherwise root_common would resolve to that
    # other repository and one of its worktrees on the expected branch would pass.
    if not _is_repo_root(git, project_root):
        return False
    ws_common = _rev_parse(git, workspace, ["--path-format=absolute", "--git-common-dir"])
    root_common = _rev_parse(git, project_root, ["--path-format=absolute", "--git-common-dir"])
    if not ws_common or not root_common or not _same_path(ws_common, root_common):
        return False
    branch = _rev_parse(git, workspace, ["--abbrev-ref", "HEAD"])
    if branch != expected_branch:
        return False
    return bool(_rev_parse(git, workspace, ["--verify", "HEAD"]))


def list_changes(workspace: Path) -> list[dict]:
    """Return the worktree's changed/untracked files vs HEAD (best effort).

    Scoped to the workspace's own repository: if the workspace is not itself a
    git top-level, returns no changes rather than reporting an ancestor repo's.
    """
    git = shutil.which("git")
    if git is None:
        return []
    if not _is_provisioned_worktree(git, workspace):
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


def resolve_confined(workspace: Path, rel_path: str) -> Path:
    """Resolve `rel_path` to an existing file strictly confined to `workspace`."""
    base = workspace.resolve()
    target = (base / rel_path).resolve()
    if not target.is_relative_to(base):
        raise PermissionError("path escapes the workspace")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


def read_file(workspace: Path, rel_path: str) -> dict:
    """Return a bounded, redacted text preview of a file confined to `workspace`."""
    target = resolve_confined(workspace, rel_path)
    raw = target.read_bytes()[: MAX_FILE_BYTES + 1]
    truncated = len(raw) > MAX_FILE_BYTES
    text = redact_text(raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace"))
    return {"path": rel_path, "content": text, "truncated": truncated}
