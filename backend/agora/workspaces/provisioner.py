"""Safe, explicit linked-worktree provisioning."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agora.execution.security import redact_text
from agora.projects import Project, ProjectRegistry

from .models import ProvisionRequest, ProvisionResult, WorkspaceState, WorkspaceStatus
from .naming import worktree_branch


# Process-wide provisioning locks keyed by (project_id, adapter). Shared across
# every WorkspaceProvisioner instance (the cached workspace-API one and any
# built per request) so concurrent provisioning of one workspace serializes
# in-process, per Phase 5 Invariant 6.
_WORKSPACE_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


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
        self._locks = _WORKSPACE_LOCKS  # process-wide, shared across instances
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

    async def ensure_source_repo(self, project_id: str) -> bool:
        """Ensure the project root is its own Git repository that can source
        worktrees, initializing one if needed.

        Idempotent. A project root nested inside an outer repository (e.g. under
        ``<agora>/projects/<id>``) is NOT treated as already-a-repo — only a root
        that is its own ``--show-toplevel`` counts — so ``git init`` gives it a
        private ``.git`` and provisioning never sources worktrees from the outer
        Agora checkout. It also guarantees a HEAD: a root that is its own repo but
        has an unborn HEAD (no commits) still gets the seed commit, so
        ``git worktree add ... HEAD`` has a base. A fresh init ignores ``.agora/``
        (control-plane state, not project code). Raises WorkspaceUnavailableError
        if git is missing.
        """
        project = self.projects.get(project_id)
        root = project.root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        # These inspections shell out to git synchronously; keep them off the
        # event loop since ensure_source_repo runs on the async request path.
        if not await asyncio.to_thread(self._source_is_own_repo, root):
            await self._git(root, ["init"])
        # Guarantee a HEAD (covers a fresh init and a pre-existing own repo whose
        # HEAD is still unborn); an existing HEAD makes this a no-op.
        if await asyncio.to_thread(self._git_sync, root, ["rev-parse", "--verify", "HEAD"]) is None:
            # Fail closed rather than sweeping pre-existing staged files into the
            # seed commit: `git commit --only` does not restrict paths on an
            # initial (unborn-HEAD) commit, so the only safe guarantee is to
            # refuse when the index already holds anything.
            staged = await asyncio.to_thread(self._git_sync, root, ["ls-files"])
            if staged is None:  # indeterminate (timeout/error): fail closed
                raise WorkspaceValidationError(
                    "Could not inspect the project index before seeding"
                )
            if staged:
                raise WorkspaceValidationError(
                    "Project root has staged changes; commit or clear them before provisioning"
                )
            gitignore = root / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(".agora/\n", encoding="utf-8")
            await self._git(root, ["add", ".gitignore"])
            await self._git(root, [
                "-c", "user.email=agora@localhost", "-c", "user.name=Agora",
                "commit", "-m", "Initialize Agora project", "--allow-empty",
            ])
        return True

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        project, workspace = self._resolve(request.project_id, request.adapter)
        key = (request.project_id, request.adapter)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # _inspect runs several blocking git subprocesses; keep it off the
            # event loop (provision runs on the async request path).
            current = await asyncio.to_thread(self._inspect, project, request.adapter, workspace)
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
                ready = await asyncio.to_thread(self._inspect, project, request.adapter, workspace)
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

    def _source_is_own_repo(self, root: Path) -> bool:
        """True only if `root` genuinely owns its repository: its own main-worktree
        top-level, not nested inside an ancestor repository AND not itself a linked
        worktree of another repo. Without this, provisioning could `git worktree
        add` against — and execution could trust — the outer repository's common
        Git directory (e.g. the Agora checkout)."""
        toplevel = self._git_sync(root, ["rev-parse", "--show-toplevel"])
        if not toplevel or not self._same_path(toplevel, root):
            return False
        git_dir = self._git_sync(root, ["rev-parse", "--absolute-git-dir"])
        common = self._git_sync(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        return bool(git_dir and common and self._same_path(git_dir, common))

    def _inspect(self, project: Project, adapter: str, workspace: Path) -> WorkspaceStatus:
        key = (project.project_id, adapter)
        source_is_git = self._source_is_own_repo(project.root)
        source_common = (
            self._git_sync(project.root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
            if source_is_git else None
        )
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
            source_is_git = self._source_is_own_repo(project.root)
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

    _branch = staticmethod(worktree_branch)

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
