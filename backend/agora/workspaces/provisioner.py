"""Safe, explicit linked-worktree provisioning."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agora.execution.security import redact_text
from agora.projects import Project, ProjectRegistry

from .models import ProvisionRequest, ProvisionResult, WorkspaceState, WorkspaceStatus


class WorkspaceError(RuntimeError):
    pass


class WorkspaceConflictError(WorkspaceError):
    pass


class WorkspaceValidationError(WorkspaceError):
    pass


class WorkspaceUnavailableError(WorkspaceError):
    pass


class WorkspaceProvisioner:
    def __init__(
        self,
        projects: ProjectRegistry,
        *,
        allowed_workspace_roots: list[Path] | None = None,
        git_command: str = "git",
        timeout_seconds: int = 60,
    ):
        self.projects = projects
        self.allowed_workspace_roots = [path.expanduser().resolve() for path in (allowed_workspace_roots or [])]
        self.git_command = git_command
        self.timeout_seconds = timeout_seconds
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._active: set[tuple[str, str]] = set()
        self._errors: dict[tuple[str, str], str] = {}

    def status(self, project_id: str, adapter: str) -> WorkspaceStatus:
        project, workspace = self._resolve(project_id, adapter)
        key = (project_id, adapter)
        if key in self._active:
            return self._status(project, adapter, workspace, WorkspaceState.PROVISIONING)
        return self._inspect(project, adapter, workspace)

    def status_all(self, project_id: str) -> list[WorkspaceStatus]:
        project = self.projects.get(project_id)
        return [self.status(project_id, adapter) for adapter in sorted(project.workspaces)]

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        project, workspace = self._resolve(request.project_id, request.adapter)
        key = (request.project_id, request.adapter)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = self._inspect(project, request.adapter, workspace)
            if current.state == WorkspaceState.READY:
                return ProvisionResult(status=current, created=False)
            if current.state == WorkspaceState.FOREIGN:
                raise WorkspaceConflictError("Workspace contains unmanaged files or a different worktree")
            if shutil.which(self.git_command) is None:
                raise WorkspaceUnavailableError("git is not installed or not on PATH")
            if not current.source_is_git:
                raise WorkspaceValidationError("Project root is not a Git repository")

            self._active.add(key)
            self._errors.pop(key, None)
            try:
                workspace = workspace.resolve()
                self._assert_allowed(project, workspace)
                try:
                    if workspace.exists():
                        if any(workspace.iterdir()):
                            raise WorkspaceConflictError("Workspace became non-empty before provisioning")
                        workspace.rmdir()
                except OSError as exc:
                    raise WorkspaceConflictError(
                        f"Workspace directory changed unexpectedly: {type(exc).__name__}"
                    ) from exc
                workspace.parent.mkdir(parents=True, exist_ok=True)
                branch = self._branch(request.project_id, request.adapter)
                listings = await self._git(project.root, ["worktree", "list", "--porcelain"])
                for item in self._parse_worktrees(listings):
                    if item.get("branch") == f"refs/heads/{branch}":
                        raise WorkspaceConflictError("Workspace branch is already checked out in another worktree")
                    if self._same_path(item.get("worktree"), workspace):
                        raise WorkspaceConflictError("Workspace path is registered with stale Git worktree metadata")

                branch_exists = await self._branch_exists(project.root, branch)
                args = ["worktree", "add", str(workspace), branch]
                if not branch_exists:
                    args = ["worktree", "add", "-b", branch, str(workspace), "HEAD"]
                await self._git(project.root, args)
                ready = self._inspect(project, request.adapter, workspace)
                if ready.state != WorkspaceState.READY:
                    raise WorkspaceValidationError("Git reported success but the workspace could not be verified")
                return ProvisionResult(status=ready, created=True)
            except WorkspaceError as exc:
                self._errors[key] = redact_text(str(exc))[:1000]
                raise
            finally:
                self._active.discard(key)

    def _resolve(self, project_id: str, adapter: str) -> tuple[Project, Path]:
        project = self.projects.get(project_id)
        if adapter not in project.workspaces:
            raise KeyError(f"Project has no {adapter} workspace")
        workspace = project.workspaces[adapter].expanduser().resolve()
        self._assert_allowed(project, workspace)
        return project, workspace

    def _assert_allowed(self, project: Project, workspace: Path) -> None:
        roots = [project.root.expanduser().resolve(), *self.allowed_workspace_roots]
        if not any(workspace.is_relative_to(root) for root in roots):
            raise WorkspaceValidationError("Workspace is outside the configured allowed roots")
        if workspace == project.root.resolve():
            raise WorkspaceValidationError("Workspace may not replace the project root checkout")

    def _inspect(self, project: Project, adapter: str, workspace: Path) -> WorkspaceStatus:
        key = (project.project_id, adapter)
        source_common = self._git_sync(project.root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        source_is_git = source_common is not None
        if not workspace.exists() or (workspace.is_dir() and not any(workspace.iterdir())):
            state = WorkspaceState.ERROR if key in self._errors else WorkspaceState.MISSING
            return self._status(project, adapter, workspace, state, source_is_git=source_is_git, error=self._errors.get(key))
        if not workspace.is_dir():
            return self._status(project, adapter, workspace, WorkspaceState.FOREIGN, source_is_git=source_is_git)

        common = self._git_sync(workspace, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        branch = self._git_sync(workspace, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        head = self._git_sync(workspace, ["rev-parse", "HEAD"])
        expected = self._branch(project.project_id, adapter)
        if source_common and common and self._same_path(source_common, Path(common)) and branch == expected and head:
            return self._status(project, adapter, workspace, WorkspaceState.READY, branch=branch, head_sha=head, source_is_git=True)
        return self._status(project, adapter, workspace, WorkspaceState.FOREIGN, branch=branch, head_sha=head, source_is_git=source_is_git)

    def _status(
        self,
        project: Project,
        adapter: str,
        workspace: Path,
        state: WorkspaceState,
        *,
        branch: str | None = None,
        head_sha: str | None = None,
        source_is_git: bool | None = None,
        error: str | None = None,
    ) -> WorkspaceStatus:
        if source_is_git is None:
            source_is_git = self._git_sync(project.root, ["rev-parse", "--git-dir"]) is not None
        return WorkspaceStatus(
            project_id=project.project_id, adapter=adapter, state=state, path=str(workspace),
            branch=branch, head_sha=head_sha, error=error, source_is_git=source_is_git,
        )

    async def _branch_exists(self, root: Path, branch: str) -> bool:
        try:
            await self._git(root, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
            return True
        except WorkspaceValidationError:
            return False

    async def _git(self, cwd: Path, args: list[str]) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                self.git_command, *args, cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise WorkspaceUnavailableError("git is not installed or not on PATH") from None
        except OSError as exc:
            raise WorkspaceUnavailableError(f"Could not start git: {type(exc).__name__}") from None
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError):
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            raise WorkspaceUnavailableError("Git operation timed out") from None
        if process.returncode != 0:
            message = redact_text(stderr.decode("utf-8", errors="replace").splitlines()[0] if stderr else "unknown error")
            raise WorkspaceValidationError(f"git {args[0]} failed ({process.returncode}): {message[:500]}")
        return stdout.decode("utf-8", errors="replace").strip()

    def _git_sync(self, cwd: Path, args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                [self.git_command, *args], cwd=str(cwd), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    @staticmethod
    def _branch(project_id: str, adapter: str) -> str:
        digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:8]
        return f"agora/workspace/{project_id.lower()}-{digest}/{adapter}"

    @staticmethod
    def _parse_worktrees(output: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in [*output.splitlines(), ""]:
            if not line:
                if current: items.append(current); current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return items

    @staticmethod
    def _same_path(left: str | Path | None, right: str | Path) -> bool:
        if left is None:
            return False
        return Path(left).resolve() == Path(right).resolve()
