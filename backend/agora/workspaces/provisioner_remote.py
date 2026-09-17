"""Remote (SSH) linked-worktree provisioning — W3-a unit U3.

The local :class:`agora.workspaces.provisioner.WorkspaceProvisioner` runs git through
an ``Environment`` but does its *filesystem* work — mkdir, rmdir, listing, writing
``.gitignore``, and the symlink-resolving confinement check — directly against THIS
host with ``pathlib``. Point it at a remote environment and it would create local
directories while asking a remote git to build a worktree elsewhere, and confine paths
against the wrong filesystem (the upstream ``__init__`` says exactly this). So the
remote path is a separate implementation, kept out of the core the same way
:mod:`agora.environments_remote` is.

What is reused, not reimplemented: the frozen models (``ProvisionRequest`` /
``ProvisionResult`` / ``WorkspaceState`` / ``WorkspaceStatus``), the branch naming
(``worktree_branch``), the worktree-list parser and the error classes — all imported
from the local provisioner so the two share one definition of "conflict" vs
"validation" vs "unavailable".

What is remote (the eight local assumptions from the U3 plan, each now going through
an async seam):

* git runs via :class:`agora.environments.AsyncExecutionEnvironment` (async run) — the
  remote SSH environment U2 built.
* filesystem work — realpath / is_empty_dir / write_file / mkdir / rmdir — goes through
  the :class:`agora.environments.AsyncFilesystem` the remote environment exposes as
  ``env.fs``. Its safety semantics (verified realpath, complete-proof emptiness,
  non-recursive mkdir, non-escalating rmdir) are what make the remote blast radius
  bounded (FR-5).
* the confinement check resolves paths on the REMOTE host (``fs.realpath``), so a
  remote symlink that escapes the allowed root is seen and refused — not masked by a
  client-side ``Path.resolve()`` that never touched the remote filesystem.

D2 form-2: the user pre-creates ``remote_project_root``; this class NEVER creates the
root. A missing root raises rather than being provisioned — a recursive mkdir that
rebuilt it is the exact vulnerability the plan and the ``AsyncFilesystem.mkdir``
contract (non-recursive) guard against.

TOCTOU (CR-12, decided A): confinement here defends against operator error and
misconfiguration to the same standard as the local version — a malicious agent racing
directory swaps between check and use is out of scope (it needs OS-level isolation the
local version does not have either). Documented, not silently ignored.
"""
from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

from agora.environments import AsyncExecutionEnvironment, AsyncFilesystem
from agora.execution.security import redact_text
from agora.projects import Project, ProjectRegistry

from .models import ProvisionRequest, ProvisionResult, WorkspaceState, WorkspaceStatus
from .naming import worktree_branch
from .provisioner import (
    WorkspaceConflictError,
    WorkspaceProvisioner,
    WorkspaceUnavailableError,
    WorkspaceValidationError,
    _uninspectable,
)

# Process-wide provisioning locks keyed by (project_id, adapter), SHARED with the local
# provisioner's map so a local and a remote provisioner for the same workspace still
# serialize (Phase 5 Invariant 6 — one workspace provisioned once at a time, whichever
# environment does it).
from .provisioner import _WORKSPACE_LOCKS


class RemoteWorkspaceProvisioner:
    """Provision a git linked-worktree on a remote host over SSH.

    Mirrors the local provisioner's public behaviour (ensure_source_repo / provision /
    is_ready_worktree / teardown_worktree) but every filesystem and git operation is
    async and crosses the SSH seam. See the module docstring for the reuse boundary."""

    def __init__(
        self,
        projects: ProjectRegistry,
        environment: AsyncExecutionEnvironment,
        filesystem: AsyncFilesystem,
        *,
        remote_project_root: Path,
        allowed_workspace_roots: list[Path] | None = None,
        git_command: str = "git",
        timeout_seconds: int = 60,
    ) -> None:
        self.projects = projects
        self.env = environment
        self.fs = filesystem
        # The user-provisioned remote root (D2 form-2). Kept as a POSIX path; NEVER
        # created by this class.
        self._remote_project_root = PurePosixPath(_as_posix(remote_project_root))
        # Allowed roots are remote POSIX paths; resolved lazily against the remote fs
        # in _assert_allowed (a client-side resolve would be the wrong filesystem).
        self._allowed_workspace_roots = [
            PurePosixPath(_as_posix(p)) for p in (allowed_workspace_roots or [])
        ]
        self.git_command = git_command
        self.timeout_seconds = timeout_seconds
        self._locks = _WORKSPACE_LOCKS
        self._active: set[tuple[str, str]] = set()
        self._errors: dict[tuple[str, str], str] = {}

    # --- public API (mirrors RemoteProvisioner contract) ---

    async def ensure_source_repo(self, project_id: str) -> bool:
        """Ensure the remote project root is its own git repo that can source
        worktrees, initializing one if needed. Idempotent.

        Unlike the local version this does NOT ``mkdir`` the root: D2 form-2 requires
        the user to pre-create it. A missing root is a definite, actionable error, kept
        apart from "could not observe" (the fsprobe rule) — a probe that comes back
        unknown fails closed as unavailable, not as "create it"."""
        project = self.projects.get(project_id)
        root = PurePosixPath(_as_posix(project.root))
        await self._verify_configured_root(root)

        probe = await self.env.probe(root)
        if probe.absent:
            raise WorkspaceValidationError(
                f"Remote project root does not exist: {root} — create it first "
                "(Agora never creates the project root)"
            )
        if probe.unknown:
            raise WorkspaceUnavailableError(_uninspectable(probe.reason()))
        if probe.is_dir() is False:
            raise WorkspaceValidationError(f"Remote project root is not a directory: {root}")

        # Decide whether to `git init` from a DEFINITE, unambiguous signal — not from
        # git's exit 128, which conflates "not a repository" with "dubious ownership",
        # "corrupt config" and other errors. The unambiguous signal is the typed state
        # of the `.git` entry via the fs probe (only ENOENT is absence; unreadable
        # raises):
        #   - .git definitely ABSENT  -> a fresh directory: init it.
        #   - .git UNREADABLE          -> fail closed (cannot tell).
        #   - .git PRESENT             -> must already be its OWN repo; if not, it is a
        #                                 nested/broken state we refuse to init over.
        git_entry = await self.env.probe(root / ".git")
        if git_entry.unknown:
            raise WorkspaceUnavailableError(
                f"cannot determine repository state of {root} (.git unreadable)"
            )
        if git_entry.absent:
            await self._git(root, ["init"])
        elif not await self._source_is_own_repo(root):
            raise WorkspaceValidationError(
                f"{root} has a .git entry but is not its own repository — refusing to init over it"
            )

        if await self._git_definitely_absent(
            root, ["rev-parse", "--verify", "--quiet", "HEAD"], absent_codes=(1,)
        ):
            # No HEAD yet (git ran in a valid repo and --quiet exited 1): seed a commit so
            # `worktree add ... HEAD` has a base. An indeterminate result (128 "not a
            # repo"/read error, timeout, unstartable) raised above rather than seeding.
            # Fail closed on a pre-existing populated index (the local version's rule) —
            # a seed commit must never sweep in staged files that are not ours.
            staged = await self._git_query(root, ["ls-files"])
            if staged is None:
                raise WorkspaceValidationError(
                    "Could not inspect the remote project index before seeding"
                )
            if staged:
                raise WorkspaceValidationError(
                    "Remote project root has staged changes; commit or clear them first"
                )
            gitignore = root / ".gitignore"
            # Only a DEFINITE absence earns a write — an unknown probe (unreadable) must
            # not be overwritten (fsprobe rule, mirrored from the local version).
            gi_probe = await self.env.probe(gitignore)
            if gi_probe.absent:
                await self.fs.write_file(gitignore, b".agora/\n")
            await self._git(root, ["add", ".gitignore"])
            await self._git(root, [
                "-c", "user.email=agora@localhost", "-c", "user.name=Agora",
                "commit", "-m", "Initialize Agora project", "--allow-empty",
            ])
        return True

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        project, workspace = await self._resolve(request.project_id, request.adapter)
        await self._verify_configured_root(PurePosixPath(_as_posix(project.root)))
        key = (request.project_id, request.adapter)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = await self._inspect(project, request.adapter, workspace)
            if current.state == WorkspaceState.READY:
                return ProvisionResult(status=current, created=False)
            if current.state == WorkspaceState.FOREIGN:
                raise WorkspaceConflictError(
                    "Workspace contains unmanaged files or a different worktree"
                )

            version = await self.env.run(
                [self.git_command, "--version"], timeout=self.timeout_seconds, read_only=True
            )
            if not version.ok:
                if not version.ran:
                    raise WorkspaceUnavailableError(
                        f"git is not installed or not on PATH ({version.reason()})"
                    )
                raise WorkspaceUnavailableError(f"git is not usable ({version.reason()})")
            if not current.source_is_git:
                raise WorkspaceValidationError("Remote project root is not a Git repository")

            self._active.add(key)
            self._errors.pop(key, None)
            try:
                await self._assert_allowed(project, workspace)
                pre = await self.env.probe(workspace)
                if pre.unknown:
                    raise WorkspaceConflictError(_uninspectable(pre.reason()))
                if pre.present:
                    # Present: must be an empty directory we can take over. is_empty_dir
                    # is a complete proof — a permission/transport failure raises rather
                    # than being read as empty (which would let us provision over it).
                    if not await self.fs.is_empty_dir(workspace):
                        raise WorkspaceConflictError("Workspace became non-empty before provisioning")
                    await self.fs.rmdir(workspace)
                # Create ONLY the workspace's immediate parent, non-recursively, after
                # confirming the root still exists — a recursive mkdir could rebuild a
                # deleted root (D2 form-2). The parent is inside the confined tree.
                await self._ensure_parent_dir(workspace, PurePosixPath(_as_posix(project.root)))

                root = PurePosixPath(_as_posix(project.root))
                branch = worktree_branch(request.project_id, request.adapter)
                listings = await self._git(root, ["worktree", "list", "--porcelain"])
                for item in WorkspaceProvisioner._parse_worktrees(listings):
                    if item.get("branch") == f"refs/heads/{branch}":
                        raise WorkspaceConflictError(
                            "Workspace branch is already checked out in another worktree"
                        )
                    if await self._same_remote_path(item.get("worktree"), workspace):
                        raise WorkspaceConflictError(
                            "Workspace path is registered with stale Git worktree metadata"
                        )

                branch_exists = await self._branch_exists(root, branch)
                args = ["worktree", "add", str(workspace), branch]
                if not branch_exists:
                    args = ["worktree", "add", "-b", branch, str(workspace), "HEAD"]
                await self._git(root, args)

                ready = await self._inspect(project, request.adapter, workspace)
                if ready.state != WorkspaceState.READY:
                    raise WorkspaceValidationError(
                        "Git reported success but the workspace could not be verified"
                    )
                return ProvisionResult(status=ready, created=True)
            except (WorkspaceConflictError, WorkspaceValidationError, WorkspaceUnavailableError) as exc:
                self._errors[key] = redact_text(str(exc))[:1000]
                raise
            finally:
                self._active.discard(key)

    async def status(self, project_id: str, adapter: str) -> WorkspaceStatus:
        """The remote worktree's state for one adapter — the async counterpart of the
        local provisioner's ``status``, asked on the host. Raises ``KeyError`` for an
        unknown project or adapter, like the local one."""
        project, workspace = await self._resolve(project_id, adapter)
        return await self._inspect(project, adapter, workspace)

    async def is_ready_worktree(
        self, workspace: Path, project_root: Path, expected_branch: str
    ) -> bool:
        """Whether the remote ``workspace`` is a ready linked worktree of
        ``project_root`` on ``expected_branch``. Fail-closed: any indeterminate git
        answer is not-ready, never a guess.

        NOTE (Q4): the local counterpart is a synchronous module-level function in
        ``execution/deliverables.py`` (four sync call sites). Step 3 wires those call
        sites to delegate to THIS async version for remote environments; this method is
        the single remote implementation they will share (no second copy of the
        readiness rule)."""
        ws = PurePosixPath(_as_posix(workspace))
        root = PurePosixPath(_as_posix(project_root))
        # A linked worktree is never the main worktree: if the workspace resolves to the
        # project root itself, it is the source checkout, not a linked worktree — reject
        # before any metadata match (git worktree list includes the main worktree, so
        # the checks below would otherwise pass for workspace == root on a matching branch).
        if await self._same_remote_path(ws, root):
            return False
        # (a) the source root must itself be a verified own-repo — a shared common-dir
        # is only meaningful relative to a real source repository.
        if not await self._source_is_own_repo(root):
            return False
        source_common = await self._git_query(
            root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
        if source_common is None:
            return False
        # (b) the workspace must be a WORKTREE ROOT (its own --show-toplevel is itself),
        # not merely a subdirectory of the source repo that inherits the same common-dir,
        # branch and HEAD. Without this, /root/subdir would read as READY.
        ws_toplevel = await self._git_query(ws, ["rev-parse", "--show-toplevel"])
        if ws_toplevel is None or not await self._same_remote_path(ws_toplevel, ws):
            return False
        common = await self._git_query(
            ws, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
        branch = await self._git_query(ws, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        head = await self._git_query(ws, ["rev-parse", "HEAD"])
        if not (
            common
            and await self._same_remote_path(source_common, common)
            and branch == expected_branch
            and head
        ):
            return False
        # (c) the workspace must be REGISTERED as a linked worktree of the source root —
        # the authoritative record, not just matching metadata.
        listings = await self._git_query(root, ["worktree", "list", "--porcelain"])
        if listings is None:
            return False
        for item in WorkspaceProvisioner._parse_worktrees(listings):
            if await self._same_remote_path(item.get("worktree"), ws):
                return True
        return False

    async def teardown_worktree(self, project_id: str, adapter: str) -> None:
        """Q5=C: remove the per-adapter worktree after handoff. Idempotent.

        Verifies OWNERSHIP before removing — a worktree is torn down only if it is
        actually registered as a linked worktree of this project's root on the expected
        branch. Never deletes by path or branch name alone (that could remove content
        Agora did not create). If the worktree is not ours or already gone, this is a
        no-op. The caller decides WHETHER to call this (config cleanup flag); this
        method decides only that removal is safe."""
        project = self.projects.get(project_id)
        root = PurePosixPath(_as_posix(project.root))
        if adapter not in project.workspaces:
            return
        workspace = PurePosixPath(_as_posix(project.workspaces[adapter]))
        expected = worktree_branch(project_id, adapter)

        # Confinement FIRST, same as provisioning: the configured root must match, and
        # the workspace must resolve INSIDE the allowed roots on the remote. Git's own
        # registration check does not enforce Agora's allowed-directory boundary — a
        # clean, correctly-branched worktree of the same repo sitting OUTSIDE the allowed
        # roots would otherwise pass ownership and be deleted. _assert_allowed raises on
        # an outside/escaping path.
        await self._verify_configured_root(root)
        await self._assert_allowed(project, workspace)

        if not await self.is_ready_worktree(workspace, root, expected):
            # Not a verified worktree of ours (or already gone) — refuse to delete.
            return
        # `git worktree remove` validates registration itself and refuses a dirty tree;
        # that is the ownership-respecting removal, not a raw rmdir of the path.
        await self._git(root, ["worktree", "remove", str(workspace)])

    # --- internals ---

    async def _resolve(self, project_id: str, adapter: str) -> tuple[Project, PurePosixPath]:
        project = self.projects.get(project_id)
        if adapter not in project.workspaces:
            raise KeyError(f"Project has no {adapter} workspace")
        workspace = PurePosixPath(_as_posix(project.workspaces[adapter]))
        await self._assert_allowed(project, workspace)
        return project, workspace

    async def _assert_allowed(self, project: Project, workspace: PurePosixPath) -> None:
        """Confinement, resolved on the REMOTE host. ``fs.realpath`` walks the path
        component-by-component with lstat, following symlinks explicitly and failing
        closed on anything unobservable — so a remote symlink escaping the allowed root
        is revealed and refused, not masked (Step 1). Comparison is by POSIX path
        COMPONENTS, never string prefix (``/a/bc`` is not under ``/a/b``)."""
        project_root = PurePosixPath(_as_posix(project.root))
        real_ws = await self.fs.realpath(workspace)
        allowed = [project_root, *self._allowed_workspace_roots]
        real_allowed = [await self.fs.realpath(root) for root in allowed]
        if not any(_is_within(real_ws, root) for root in real_allowed):
            raise WorkspaceValidationError("Workspace is outside the configured allowed roots")
        real_root = await self.fs.realpath(project_root)
        if real_ws == real_root:
            raise WorkspaceValidationError("Workspace may not replace the project root checkout")

    async def _ensure_parent_dir(self, workspace: PurePosixPath, project_root: PurePosixPath) -> None:
        """Ensure the workspace's parent directory chain exists, creating each MISSING
        level one at a time from the project root DOWN — never the root itself (D2
        form-2). The default workspace ``<root>/.agora/workspaces/<adapter>`` needs two
        intermediate levels the user did not pre-create; a single non-recursive mkdir of
        the immediate parent would fail because ``.agora`` is absent. So:

          - the parent must be WITHIN the project root (confinement already enforced for
            the workspace; assert the invariant here too);
          - the root must still exist (fail-closed if it vanished — never re-create it);
          - walk root -> ... -> parent, creating only the levels that are definitely
            absent, each with a non-recursive mkdir (so a race that removed an ancestor
            surfaces as a mkdir failure, not a silent recursive rebuild)."""
        parent = workspace.parent
        real_root = await self.fs.realpath(project_root)
        real_parent = await self.fs.realpath(parent)
        if real_parent == real_root:
            # Parent IS the root: re-verify it exists and never create it.
            await self._require_root_dir(project_root)
            return
        # The parent must sit within an authorized base — the project root OR one of the
        # explicitly-allowed workspace roots (a workspace outside the project tree but in
        # an allowed root is legitimate and already passed _assert_allowed). Find the
        # containing base; its existence is the precondition we build down from.
        real_allowed = [await self.fs.realpath(r) for r in self._allowed_workspace_roots]
        bases = [real_root, *real_allowed]
        containing = next((b for b in bases if _is_within(real_parent, b)), None)
        if containing is None:
            raise WorkspaceValidationError(
                f"Workspace parent {parent} is outside the project root and allowed roots"
            )
        if containing == real_root:
            # Only the project root carries the D2 form-2 "never create, must pre-exist"
            # guarantee; re-verify it. An allowed workspace root is Agora-managed space.
            await self._require_root_dir(project_root)
        else:
            # An allowed workspace root IS Agora-managed: create it if definitely absent,
            # but NON-recursively — if its own parent is missing, mkdir fails and we do
            # NOT build above the allowed base (codex round-4). Unreadable -> fail closed.
            base_probe = await self.env.probe(containing)
            if base_probe.absent:
                await self.fs.mkdir(containing)
            elif base_probe.unknown:
                raise WorkspaceConflictError(_uninspectable(base_probe.reason()))
            elif base_probe.is_dir() is False:
                raise WorkspaceConflictError(f"Allowed workspace root is not a directory: {containing}")
        # Build the chain from the containing base down to the parent, creating each
        # definitely-absent level with a non-recursive mkdir (a race that removed an
        # ancestor surfaces as a mkdir failure, never a silent recursive rebuild).
        rel = real_parent.relative_to(containing)
        current = containing
        for part in rel.parts:
            current = current / part
            probe = await self.env.probe(current)
            if probe.absent:
                await self.fs.mkdir(current)  # non-recursive; its parent exists by construction
            elif probe.unknown:
                raise WorkspaceConflictError(_uninspectable(probe.reason()))
            elif probe.is_dir() is False:
                raise WorkspaceConflictError(f"Workspace parent path component is not a directory: {current}")

    async def _require_root_dir(self, project_root: PurePosixPath) -> None:
        """Re-verify the project root is present and a directory; NEVER create it
        (D2 form-2). A root that vanished mid-provision is a fail-closed error."""
        root_probe = await self.env.probe(project_root)
        if root_probe.absent:
            raise WorkspaceValidationError(
                f"Project root disappeared during provisioning: {project_root} "
                "(Agora never creates the project root)"
            )
        if root_probe.unknown:
            raise WorkspaceConflictError(_uninspectable(root_probe.reason()))
        if root_probe.is_dir() is False:
            raise WorkspaceValidationError(f"Project root is not a directory: {project_root}")

    async def _inspect(
        self, project: Project, adapter: str, workspace: PurePosixPath
    ) -> WorkspaceStatus:
        key = (project.project_id, adapter)
        root = PurePosixPath(_as_posix(project.root))
        source_is_git = await self._source_is_own_repo(root)

        probe = await self.env.probe(workspace)
        if probe.unknown:
            return self._status(
                project, adapter, workspace, WorkspaceState.ERROR,
                source_is_git=source_is_git,
                error=_uninspectable(probe.reason()),
            )
        empty = False
        if probe.is_dir():
            try:
                empty = await self.fs.is_empty_dir(workspace)
            except OSError as exc:
                return self._status(
                    project, adapter, workspace, WorkspaceState.ERROR,
                    source_is_git=source_is_git,
                    error=f"workspace could not be listed ({type(exc).__name__})",
                )
        if probe.absent or empty:
            state = WorkspaceState.ERROR if key in self._errors else WorkspaceState.MISSING
            return self._status(
                project, adapter, workspace, state,
                source_is_git=source_is_git, error=self._errors.get(key),
            )
        if probe.is_dir() is False:
            return self._status(
                project, adapter, workspace, WorkspaceState.FOREIGN, source_is_git=source_is_git
            )

        expected = worktree_branch(project.project_id, adapter)
        if await self.is_ready_worktree(workspace, root, expected):
            branch = await self._git_query(
                workspace, ["symbolic-ref", "--quiet", "--short", "HEAD"]
            )
            head = await self._git_query(workspace, ["rev-parse", "HEAD"])
            return self._status(
                project, adapter, workspace, WorkspaceState.READY,
                branch=branch, head_sha=head, source_is_git=True,
            )
        branch = await self._git_query(workspace, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        head = await self._git_query(workspace, ["rev-parse", "HEAD"])
        return self._status(
            project, adapter, workspace, WorkspaceState.FOREIGN,
            branch=branch, head_sha=head, source_is_git=source_is_git,
        )

    def _status(
        self,
        project: Project,
        adapter: str,
        workspace: PurePosixPath,
        state: WorkspaceState,
        *,
        branch: str | None = None,
        head_sha: str | None = None,
        source_is_git: bool = False,
        error: str | None = None,
    ) -> WorkspaceStatus:
        return WorkspaceStatus(
            project_id=project.project_id, adapter=adapter, state=state,
            path=str(workspace), branch=branch, head_sha=head_sha,
            error=error, source_is_git=source_is_git,
        )

    async def _source_is_own_repo(self, root: PurePosixPath) -> bool:
        """True only if ``root`` genuinely owns its repository (its own main-worktree
        top-level, not nested in an ancestor repo and not itself a linked worktree).
        Same rule as the local version, over async git. Any indeterminate git result is
        a plain ``False`` here — this is only ever a read-only classifier (``_inspect``,
        and the "has .git but is it own?" refusal in ensure_source_repo); nothing writes
        based on its ``False`` (the init decision is gated on the typed ``.git`` probe)."""
        toplevel = await self._git_query(root, ["rev-parse", "--show-toplevel"])
        if not toplevel or not await self._same_remote_path(toplevel, root):
            return False
        git_dir = await self._git_query(root, ["rev-parse", "--absolute-git-dir"])
        common = await self._git_query(
            root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
        return bool(git_dir and common and await self._same_remote_path(git_dir, common))

    async def _branch_exists(self, root: PurePosixPath, branch: str) -> bool:
        """Whether ``branch`` exists. A DEFINITE negative (show-ref ran, exited non-zero)
        means it does not; an indeterminate git result RAISES rather than being read as
        "absent" — otherwise a timed-out check would wrongly unlock ``worktree add -b``."""
        return not await self._git_definitely_absent(
            root, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], absent_codes=(1,)
        )

    async def _git(self, cwd: PurePosixPath, args: list[str]) -> str:
        """Run one git command on the remote, off the SSH channel's failure modes kept
        apart: git absent / too slow are ``WorkspaceUnavailableError`` (nothing wrong
        with the workspace); git saying no is ``WorkspaceValidationError`` about it.

        NOT declared read_only: these run inside provisioning where a retry could double
        a side effect (worktree add, commit). Read-only *queries* use ``_git_query``."""
        result = await self.env.run(
            [self.git_command, *args], cwd=cwd, timeout=self.timeout_seconds
        )
        if result.outcome == "unstartable":
            raise WorkspaceUnavailableError(f"Could not start git: {result.reason()}")
        if result.outcome == "timeout":
            raise WorkspaceUnavailableError("Git operation timed out")
        if result.returncode != 0:
            first_line = result.stderr.splitlines()[0] if result.stderr.strip() else "unknown error"
            raise WorkspaceValidationError(
                f"git {args[0]} failed ({result.returncode}): {redact_text(first_line)[:500]}"
            )
        return result.stdout.strip()

    async def _git_query(self, cwd: PurePosixPath, args: list[str]) -> str | None:
        """A git query whose answer may be "could not find out" -> ``None``. Read-only,
        so a transport failure IS retried (``read_only=True``). Every caller is a
        positive proof step that fails closed on ``None``."""
        result = await self.env.run(
            [self.git_command, *args], cwd=cwd, timeout=10, read_only=True
        )
        return result.stdout.strip() if result.ok else None

    async def _verify_configured_root(self, project_root: PurePosixPath) -> None:
        """The configured ``remote_project_root`` (D2 form-2, user-provisioned) and the
        registry's ``project.root`` must name the SAME remote directory. If they differ,
        every operation below (init, git cwd, confinement) would act on ``project.root``
        while the config's guarantees are about ``_remote_project_root`` — Agora could
        initialize or modify a repo OUTSIDE the configured boundary. Resolve both on the
        remote and require equality before any operation proceeds."""
        real_configured = await self.fs.realpath(self._remote_project_root)
        real_project = await self.fs.realpath(project_root)
        if real_configured != real_project:
            raise WorkspaceValidationError(
                f"Configured remote_project_root ({self._remote_project_root}) does not "
                f"resolve to the project root ({project_root}) on the remote host"
            )

    async def _git_definitely_absent(
        self, cwd: PurePosixPath, args: list[str], *, absent_codes: tuple[int, ...]
    ) -> bool:
        """A read-only proof-of-existence query. Returns True ONLY when git ran and
        exited with one of ``absent_codes`` — the exit code(s) THIS query uses to mean a
        definite negative (e.g. ``show-ref --verify`` exits 1 for a missing ref). EVERY
        other outcome fails closed with ``WorkspaceUnavailableError``:

          - could-not-start / timeout — never observed the answer;
          - ANY other non-zero exit (126/127 shell errors, 128 repo/read errors, ...) —
            git failed for a reason that is NOT "the thing is absent", so reading it as
            absence would wrongly unlock a write (init / worktree add -b). "Absent" must
            be a specific proven code, not merely "not zero"."""
        result = await self.env.run(
            [self.git_command, *args], cwd=cwd, timeout=10, read_only=True
        )
        if result.ok:
            return False  # exists
        if not result.ran:
            raise WorkspaceUnavailableError(f"git could not start: {result.reason()}")
        if result.outcome == "timeout":
            raise WorkspaceUnavailableError("git query timed out — cannot prove absence")
        if result.returncode in absent_codes:
            return True  # the specific negative this query is defined to produce
        raise WorkspaceUnavailableError(
            f"git query failed with an indeterminate exit {result.returncode} — cannot prove absence"
        )

    async def _same_remote_path(self, left: str | PurePosixPath | None, right) -> bool:
        """Two remote paths are the same iff their remote-realpath'd forms are equal.
        ``None`` (a git query that found nothing) is never equal to anything."""
        if left is None or right is None:
            return False
        real_left = await self.fs.realpath(PurePosixPath(_as_posix(left)))
        real_right = await self.fs.realpath(PurePosixPath(_as_posix(right)))
        return real_left == real_right


def _as_posix(path) -> str:
    """A remote path is POSIX. A ``str`` passes through (already remote form); a
    ``PurePath`` uses its own ``as_posix`` so a Windows client's ``\\`` never leaks."""
    if isinstance(path, str):
        return path
    return path.as_posix()


def _is_within(candidate: PurePosixPath, root: PurePosixPath) -> bool:
    """True iff ``candidate`` is ``root`` or below it, compared by POSIX path COMPONENTS
    — ``/a/bc`` is NOT within ``/a/b`` even though the string is a prefix."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
