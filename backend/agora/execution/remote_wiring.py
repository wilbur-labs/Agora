"""Where a run executes: the adapter says (W3-a U5, the half that was missing).

An adapter configured with ``bridge_mode: ssh`` carries a :class:`RemoteAdapterConfig`.
Every run of that adapter executes on that host, in the project's worktree *there*, and
its worktree is provisioned, read and committed there. Every other adapter is local,
exactly as before. This module is the one place that turns that config into the three
remote collaborators the dispatcher and the routers need — the environment (SSH exec +
SFTP), the provisioner and the deliverables reader — so nothing else constructs a
:class:`RemoteEnvironment` and the choice is made by one predicate, :func:`is_remote`.

One instance per use. A :class:`RemoteEnvironment` holds one asyncssh connection on one
event loop (CR-4); a run builds its components when it starts and closes them when it
settles, a request builds and closes its own. Nothing is cached across loops.

Why per adapter and not per project (CR-2, deferred): the config already binds an
adapter to a host, and the mockup's "environment" tab is a later layer over exactly this
fact. A project that names a remote adapter as its worker runs remotely; the review must
then run on the same host, because a review reads the worker's worktree — see
:func:`same_remote_host`, which the dispatcher asks before queuing one.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from agora.environments_remote import RemoteEnvironment, RemoteFilesystem
from agora.projects import ProjectRegistry
from agora.workspaces.provisioner_remote import RemoteWorkspaceProvisioner

from .adapters import ExecutionAdapter, RemoteAdapterConfig
from .deliverables_remote import RemoteDeliverables
from .models import REMOTE_TARGET_KEY


def is_remote(adapter: ExecutionAdapter | None) -> bool:
    """Whether runs of this adapter execute on another host."""
    return adapter is not None and adapter.remote is not None


def remote_workspace_of(adapter: ExecutionAdapter, project) -> PurePosixPath:
    """The project's worktree for this adapter, as a path on the remote host.

    Taken from the project's ``workspaces`` map like the local case, but never
    ``expanduser().resolve()``d: those would read *this* machine's filesystem, and on
    Windows would turn ``/srv/x`` into ``D:\\srv\\x``. Confinement under the configured
    remote project root is the remote provisioner's job, asked on the host itself.
    """
    remote = adapter.remote
    if remote is not None and remote.remote_workspace:
        # The adapter's own ``remote_workspace`` is the authority: it is a string the
        # config loader never resolved. The registry resolves every project path on
        # *this* machine (``ProjectRegistry._resolve_path``), which on Windows turns an
        # absolute POSIX path into one on the current drive — a project's workspaces
        # map cannot be trusted to hold the remote path intact there.
        return PurePosixPath(remote.remote_workspace)
    key = adapter.workspace_key
    if key not in project.workspaces:
        raise KeyError(f"Project has no {key} workspace")
    return PurePosixPath(Path(project.workspaces[key]).as_posix())


def same_remote_host(left: RemoteAdapterConfig, right: RemoteAdapterConfig) -> bool:
    """Whether two adapters reach the same account on the same machine — the
    condition under which one can read a worktree the other wrote."""
    return (left.ssh_host, left.ssh_port, left.ssh_user) == (right.ssh_host, right.ssh_port, right.ssh_user)


def remote_target_record(config: RemoteAdapterConfig) -> dict[str, Any]:
    """What ``queue`` writes into a remote run's identity metadata (``REMOTE_TARGET_KEY``):
    the place it executes, fixed at that moment. No credential reference — that is
    looked up by adapter name when the run is read, and refused if it no longer
    reaches the same place."""
    record = {
        "host": config.ssh_host, "port": config.ssh_port, "user": config.ssh_user,
        "workspace": config.remote_workspace, "project_root": config.remote_project_root,
    }
    if config.environment_id:
        # A registered environment (CR-4): later reads take its credentials from the
        # registry by this id, not from the adapter's config.yaml section.
        record["environment_id"] = config.environment_id
    return record


def remote_target_of(run) -> dict[str, Any] | None:
    """The place a run executed, from its own record; ``None`` for a local run — or
    for a remote run recorded before the target was written, which then reads as
    local and fails closed at the worktree checks rather than guessing a host."""
    raw = (run.result_metadata or {}).get(REMOTE_TARGET_KEY)
    if not isinstance(raw, dict):
        return None
    if not all(isinstance(raw.get(key), str) and raw.get(key) for key in ("host", "user", "workspace", "project_root")):
        return None
    return raw


class RemoteTargetMismatch(RuntimeError):
    """The adapter no longer reaches the place a run executed."""


class EnvironmentLookup(Protocol):
    """The registered-environment store, as far as execution needs it."""

    def get(self, environment_id: str) -> Any: ...


async def initialise_if_empty(components: "RemoteComponents", project_id: str) -> None:
    """Make a registered environment's root a repository — ONLY when it is an empty,
    pre-created directory (D2 form-2). A directory holding files but no ``.git`` is NOT
    initialised: ``git init`` plus a seed commit of ``.gitignore`` would give every
    worktree a project without its sources (review). Such a root, an unreadable one, or
    a missing one is left alone and the provision that follows says what is wrong."""
    try:
        empty = await components.filesystem.is_empty_dir(Path(components.config.remote_project_root))
    except Exception:  # noqa: BLE001 - cannot tell: do not initialise
        return
    if empty:
        await components.provisioner.ensure_source_repo(project_id)


def remote_path_within(path: str, root: str) -> bool:
    """Whether POSIX ``path`` lies strictly inside ``root``, by path COMPONENTS (``/a/bc``
    is not under ``/a/b``). Lexical only — the provisioner re-checks on the host with
    symlinks resolved; this is the early, honest refusal at register time."""
    child, parent = PurePosixPath(path), PurePosixPath(root)
    if not (child.is_absolute() and parent.is_absolute()) or ".." in child.parts:
        return False
    return child != parent and child.parts[: len(parent.parts)] == parent.parts


def on_registered_environment(adapter: ExecutionAdapter, record: Any) -> ExecutionAdapter:
    """``adapter`` (the agent CLI) pointed at a registered environment (CR-4): its
    remote section — host, credential source, host-key settings — comes from the
    registry record instead of config.yaml.

    The record's ``remote_workspace`` is the directory for this environment's worktrees;
    each adapter gets its own subdirectory, because each worktree is on its adapter's
    own branch — a reviewer sharing the worker's worktree would fail the branch check
    before it ever launched (codex review)."""
    config = record.to_adapter_config()
    workspace = PurePosixPath(config.remote_workspace) / adapter.workspace_key
    return replace(adapter, bridge_mode="ssh", remote=config.model_copy(
        update={"remote_workspace": str(workspace)},
    ))


def adapter_for_recorded_target(
    adapter: ExecutionAdapter | None,
    target: dict[str, Any],
    environments: EnvironmentLookup | None = None,
) -> ExecutionAdapter:
    """The adapter as it must be used to read a run recorded at ``target``: the current
    adapter's credentials and host-key settings, the *recorded* host, account and paths.
    Refuses — never substitutes — when the adapter is gone, is local now, or points at
    a different account or host.

    A run queued on a registered environment (its target carries ``environment_id``)
    takes those settings from the registry by that id instead — and is refused the
    same way when the environment is gone or now reaches another account or host."""
    environment_id = target.get("environment_id")
    if environment_id:
        if adapter is None:
            raise RemoteTargetMismatch("this run's execution adapter is no longer configured")
        if environments is None:
            # Not "unregistered": this caller simply cannot look. Said as itself.
            raise RemoteTargetMismatch(
                f"this run executed on registered environment {environment_id!r}, and the "
                f"environment registry is not available here to read it"
            )
        record = environments.get(environment_id)
        if record is None:
            raise RemoteTargetMismatch(
                f"this run executed on registered environment {environment_id!r}, which is "
                f"no longer registered"
            )
        adapter = on_registered_environment(adapter, record)
    if adapter is None or adapter.remote is None:
        raise RemoteTargetMismatch(
            "this run executed on a remote host, and its adapter is no longer configured as remote"
        )
    current = adapter.remote
    if (current.ssh_host, int(current.ssh_port), current.ssh_user) != (target["host"], int(target.get("port", 22)), target["user"]):
        raise RemoteTargetMismatch(
            f"this run executed on {target['user']}@{target['host']}, and adapter {adapter.name!r} now "
            f"reaches {current.ssh_user}@{current.ssh_host}"
        )
    pinned = current.model_copy(update={
        "remote_workspace": target["workspace"], "remote_project_root": target["project_root"],
    })
    return replace(adapter, remote=pinned)


class _RemoteProjectView:
    """The project registry as the remote provisioner must see it.

    ``ProjectRegistry`` resolves every project path on *this* machine — on Windows a
    POSIX root becomes ``D:/srv/...`` — and the provisioner reads ``project.root`` and
    ``project.workspaces``. This view answers with the adapter's configured remote
    root and worktree instead, unresolved. Only ``get`` is needed by the provisioner.
    """

    def __init__(self, projects: ProjectRegistry, adapter: ExecutionAdapter, config: RemoteAdapterConfig) -> None:
        self._projects = projects
        self._adapter = adapter
        self._config = config

    def get(self, project_id: str):
        project = self._projects.get(project_id)
        workspaces = dict(project.workspaces)
        workspaces[self._adapter.workspace_key] = Path(self._config.remote_workspace)
        return replace(project, root=Path(self._config.remote_project_root), workspaces=workspaces)

    def __getattr__(self, name: str):
        return getattr(self._projects, name)


def describe_target(config: RemoteAdapterConfig | None) -> str:
    """Where an adapter runs, for a sentence a person reads. Never a credential."""
    if config is None:
        return "this machine"
    return f"{config.ssh_user}@{config.ssh_host}" + (f":{config.ssh_port}" if config.ssh_port != 22 else "")


@dataclass(frozen=True)
class RemoteComponents:
    config: RemoteAdapterConfig
    environment: RemoteEnvironment
    filesystem: RemoteFilesystem
    provisioner: RemoteWorkspaceProvisioner
    deliverables: RemoteDeliverables

    async def aclose(self) -> None:
        """Close the one connection behind all three. Idempotent; never raises —
        closing is the last thing a settled run does and must not unsettle it."""
        try:
            await self.environment.aclose()
        except Exception:  # noqa: BLE001 - closing must not raise
            pass


def remote_components_for(
    adapter: ExecutionAdapter,
    projects: ProjectRegistry,
    *,
    allowed_workspace_roots: list[Path] | None = None,
) -> RemoteComponents:
    """The remote collaborators for one use of ``adapter``. Raises ``ValueError`` for
    an adapter that is not remote — callers ask :func:`is_remote` first."""
    remote = adapter.remote
    if remote is None:
        raise ValueError(f"execution adapter {adapter.name} is not a remote adapter")
    environment = RemoteEnvironment(
        host=remote.ssh_host,
        port=remote.ssh_port,
        user=remote.ssh_user,
        secret_ref=remote.secret_ref,
        host_key_mode=remote.host_key_mode,
        known_hosts_path=remote.known_hosts_path,
        credential_source=remote.credential_source,
        environment_id=remote.environment_id,
    )
    filesystem = environment.fs
    # The provisioner sees the remote paths, not the registry's locally resolved ones,
    # and no local allowed roots: confinement on the host is under the remote root.
    del allowed_workspace_roots
    provisioner = RemoteWorkspaceProvisioner(
        _RemoteProjectView(projects, adapter, remote), environment, filesystem,  # type: ignore[arg-type]
        remote_project_root=Path(remote.remote_project_root),
        allowed_workspace_roots=None,
    )
    return RemoteComponents(
        config=remote,
        environment=environment,
        filesystem=filesystem,
        provisioner=provisioner,
        deliverables=RemoteDeliverables(environment, filesystem),
    )
