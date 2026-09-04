"""Safe, explicit linked-worktree provisioning."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agora.environments import Environment, LocalEnvironment
from agora.execution.security import redact_text
from agora.projects import Project, ProjectRegistry

from .models import ProvisionRequest, ProvisionResult, WorkspaceState, WorkspaceStatus
from .naming import worktree_branch


# Process-wide provisioning locks keyed by (project_id, adapter). Shared across
# every WorkspaceProvisioner instance (the cached workspace-API one and any
# built per request) so concurrent provisioning of one workspace serializes
# in-process, per Phase 5 Invariant 6.
_WORKSPACE_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _uninspectable(reason: str) -> str:
    """One phrasing for one fact, used wherever a probe comes back unknown.

    Two wordings for the same obstacle is not only untidy: `_with_stored` has to
    recognise a repeat, and it can only do that if the sentence is the same sentence.
    """
    return f"workspace could not be inspected ({reason})"


def _with_stored(stored: str | None, current: str) -> str:
    """Both, when there are both. A stored error is what a past provisioning attempt
    hit; ``current`` is what this inspection just failed to establish. Reporting only
    the stored one sends the operator to fix a cause that may be long gone while the
    present obstacle goes unmentioned, and reporting only the current one throws away
    the reason the workspace was left in this state."""
    if not stored:
        return current
    if stored == current:
        # The same obstacle, seen twice. A provisioning attempt that failed *because*
        # the path was unreadable stores that cause; the next inspection hits it again
        # and would otherwise present one fact as a stale one plus a current one.
        return stored
    return f"{stored}; {current}"


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
        environment: Environment | None = None,
    ):
        self.projects = projects
        # Where git runs and where paths are *looked at*. Only those two.
        #
        # What is still local, and what W3 has to deal with rather than inherit:
        # every filesystem *mutation* here (`root.mkdir`, `workspace.parent.mkdir`,
        # `workspace.rmdir`, writing `.gitignore`), the directory listings, and --
        # the one that matters for safety -- `_assert_allowed`, which resolves
        # symlinks and compares roots on *this* host. Point a remote Environment at
        # this class today and it will create local directories while asking a
        # remote git to build a worktree somewhere else, and confine paths against
        # the wrong filesystem. So: not "swap the implementation and this file is
        # done". See agora/environments.py and tasks/agora/adr/0003.
        self.env = environment or LocalEnvironment()
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
            # Only a definite absence earns a write. `exists()` answered False for an
            # unreadable file too, and that answer would have overwritten it.
            if self.env.probe(gitignore).absent:
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
            # Ask the environment, not this machine's PATH. `shutil.which` answers
            # about the process's own host, which is the wrong host as soon as the
            # environment is not the local one -- the last local-only assumption
            # left in this file.
            version = await asyncio.to_thread(
                self.env.run, [self.git_command, "--version"], timeout=self.timeout_seconds
            )
            if not version.ok:
                # `.ran` was the wrong test: it is true for a timeout, so a git that
                # started and hung passed this check and failed later as "Project
                # root is not a Git repository" -- a statement about the project,
                # from a fact about git. `git --version` exits zero whenever git
                # works at all, so anything else belongs here.
                if not version.ran:
                    raise WorkspaceUnavailableError(
                        f"git is not installed or not on PATH ({version.reason()})"
                    )
                raise WorkspaceUnavailableError(f"git is not usable ({version.reason()})")
            if not current.source_is_git:
                raise WorkspaceValidationError("Project root is not a Git repository")

            self._active.add(key)
            self._errors.pop(key, None)
            try:
                workspace = workspace.resolve()
                self._assert_allowed(project, workspace)
                pre = self.env.probe(workspace)
                if pre.unknown:
                    # Not "it changed": nothing at all was established about it. Fail
                    # closed either way, but do not send the operator looking for a
                    # race that never happened.
                    raise WorkspaceConflictError(_uninspectable(pre.reason()))
                try:
                    if pre.present:
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
        probe = self.env.probe(workspace)
        if probe.unknown:
            # `Path.exists()` used to answer False here, which sent an unreadable
            # worktree back as MISSING -- "provision it" -- when the operator's
            # actual next move is to fix a permission. ERROR widens to mean "this
            # state could not be established", and the reason says which kind it is.
            return self._status(
                project, adapter, workspace, WorkspaceState.ERROR,
                source_is_git=source_is_git,
                error=_with_stored(self._errors.get(key), _uninspectable(probe.reason())),
            )
        empty = False
        if probe.is_dir():
            try:
                empty = not any(workspace.iterdir())
            except OSError as exc:
                # Stat succeeded and the listing did not: still "could not find
                # out", so it must not fall through to MISSING either.
                return self._status(
                    project, adapter, workspace, WorkspaceState.ERROR,
                    source_is_git=source_is_git,
                    error=_with_stored(
                        self._errors.get(key),
                        f"workspace could not be listed ({type(exc).__name__})",
                    ),
                )
        if probe.absent or empty:
            state = WorkspaceState.ERROR if key in self._errors else WorkspaceState.MISSING
            return self._status(project, adapter, workspace, state, source_is_git=source_is_git, error=self._errors.get(key))
        if not probe.is_dir():
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
        """Run one git command through the environment, off the event loop.

        ``Environment.run`` is synchronous by design (one implementation per
        environment instead of two that can drift), so the await is a thread hop.
        The three failures stay three different exceptions: git absent and git too
        slow are ``WorkspaceUnavailableError`` -- nothing is wrong with the
        workspace -- while git saying no is a ``WorkspaceValidationError`` about it.
        """
        result = await asyncio.to_thread(
            self.env.run,
            [self.git_command, *args],
            cwd=cwd,
            timeout=self.timeout_seconds,
        )
        if result.outcome == "unstartable":
            if isinstance(result.error, FileNotFoundError):
                raise WorkspaceUnavailableError("git is not installed or not on PATH")
            raise WorkspaceUnavailableError(f"Could not start git: {result.reason()}")
        if result.outcome == "timeout":
            raise WorkspaceUnavailableError("Git operation timed out")
        if result.returncode != 0:
            first_line = result.stderr.splitlines()[0] if result.stderr.strip() else "unknown error"
            message = redact_text(first_line)
            raise WorkspaceValidationError(f"git {args[0]} failed ({result.returncode}): {message[:500]}")
        return result.stdout.strip()

    def _git_sync(self, cwd: Path, args: list[str]) -> str | None:
        """A git query whose answer may be "we could not find out".

        ``None`` covers every non-answer -- git missing, timeout, spawn failure,
        non-zero exit -- and it is deliberately not split further here: every caller
        of this method is a *positive* proof step (is this a repo root, is this the
        expected branch) that must fail closed on anything indeterminate. The
        distinction that matters is kept one level up, in ``_git``.
        """
        result = self.env.run([self.git_command, *args], cwd=cwd, timeout=10)
        return result.stdout.strip() if result.ok else None

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
