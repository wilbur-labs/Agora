"""Value objects for the remote environment registry (CR-4 slice 1).

A registered remote environment is the persisted form of the SSH section of an
execution adapter (:class:`agora.execution.adapters.RemoteAdapterConfig`). The
registry lets a person create/edit/delete these through the product instead of
hand-editing ``config.yaml``.

Field validation is deliberately delegated to ``RemoteAdapterConfig`` itself
(``to_adapter_config``) rather than re-stated here — one frozen contract, one set
of rules (``extra='forbid'``, host_key_mode literal, port int). This module only
adds the registry-level fields the adapter config has no concept of: a stable
identifier, a human label, and timestamps.

**No credential ever lives on this record** — ``secret_ref`` names an env var, per
NFR-2 / SECURITY-12 (public repo). See ``registry_store`` for persistence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .adapters import HostKeyMode, RemoteAdapterConfig

#: The registry key rule, stated once so the Pydantic request field, this
#: validator and the frontend form all enforce the *same* thing: must start with
#: an alphanumeric, then letters/numbers/dashes/underscores. Same rule as a
#: project id, and for the same reason — it may appear in a URL path and must not
#: need escaping. The earlier ``.replace().isalnum()`` check disagreed at the
#: edges (it accepted a leading dash and rejected an all-dashes id), which is two
#: rules for one field; this collapses to one.
ENVIRONMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def validate_environment_id(environment_id: str) -> str:
    if not ENVIRONMENT_ID_PATTERN.match(environment_id):
        raise ValueError(
            "Environment id must start with a letter or number and contain only "
            "letters, numbers, dashes, or underscores"
        )
    return environment_id


@dataclass(frozen=True)
class RemoteEnvironmentRecord:
    """One registered remote environment, exactly as stored.

    Carries the ``RemoteAdapterConfig`` field set plus the registry's own
    identity/label/timestamps. ``to_adapter_config`` is the single place the record
    is turned back into the frozen adapter contract, which validates the port type,
    the host_key_mode literal and the required strings via ``extra='forbid'``. The
    cross-field rule (tofu needs a known_hosts file) is not on that contract — it is
    enforced at the API boundary (``RemoteEnvironmentRequest``) at register time, and
    again by ``RemoteEnvironment._known_hosts_arg`` at connect time.
    """

    environment_id: str
    label: str
    ssh_host: str
    ssh_user: str
    remote_workspace: str
    remote_project_root: str
    secret_ref: str
    ssh_port: int = 22
    host_key_mode: HostKeyMode = "strict"
    known_hosts_path: str | None = None
    cleanup_worktree: bool = False
    created_at: str | None = None
    updated_at: str | None = None

    def to_adapter_config(self) -> RemoteAdapterConfig:
        """The frozen adapter contract this record represents.

        Constructing it validates the SSH fields (``extra='forbid'`` cannot fire —
        we pass exactly its fields — but the port type, host_key_mode literal and
        required-string checks all run here). Slice 1 does not feed this into the
        runtime; it exists so validation has one owner and slice 2 has the object
        it needs.
        """
        return RemoteAdapterConfig(
            ssh_host=self.ssh_host,
            ssh_port=self.ssh_port,
            ssh_user=self.ssh_user,
            remote_workspace=self.remote_workspace,
            remote_project_root=self.remote_project_root,
            secret_ref=self.secret_ref,
            host_key_mode=self.host_key_mode,
            known_hosts_path=self.known_hosts_path,
            cleanup_worktree=self.cleanup_worktree,
        )
