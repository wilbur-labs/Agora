"""SQLite persistence for registered remote (SSH) environments — CR-4 slice 1.

A remote environment is, today, an execution adapter configured with
``bridge_mode: ssh`` carrying a :class:`agora.execution.adapters.RemoteAdapterConfig`
(host / port / user / secret_ref / host_key_mode / known_hosts_path /
remote_workspace / remote_project_root). Until now the only way to have one was to
hand-edit ``config.yaml``. This store is the registry the mockup's "environment"
screen sits on: it lets a person create, edit and delete those records through the
product. The choice to key it off the adapter config (not a per-project environment)
follows the note in ``remote_wiring.py``: the config already binds an adapter to a
host, and the environment screen is a layer over exactly that fact.

**Scope of slice 1 (see task.yaml CR-4 anchor).** This persists the *record* and
serves CRUD. It deliberately does NOT wire the registered environment into the
runtime — a run still resolves its remote target from ``config.yaml``. Connection
testing, TOFU fingerprint confirmation and a new credential home (OS keyring / key
path) are slice 2, which needs a real host to verify against.

**Credentials are never stored here.** Exactly as ``RemoteAdapterConfig`` documents
(NFR-2, SECURITY-12, public repo): the row holds ``secret_ref`` — the *name* of the
environment variable that holds the key path or password — and never the secret
itself. The list endpoint reports whether that env var is currently set, never its
value, the same way ``settings_router._control_plane_auth`` does.

Uses the same SQLite file as projects/tasks/runs (see
``agora.config.settings.resolve_control_plane_db_path``) so the registry shares the
one authoritative control-plane database rather than opening a second file.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from agora.tasks.models import utc_now

from . import credential_store
from .credential_store import KeyringUnavailableError
from .environment_registry_models import (
    RemoteEnvironmentRecord,
    validate_environment_id,
)


class RemoteEnvironmentExistsError(ValueError):
    """A row with this environment_id already exists."""


class RemoteEnvironmentNotFoundError(KeyError):
    """No row with this environment_id."""


class RemoteEnvironmentStore:
    """Synchronous store for registered remote environments, matching the other
    control-plane stores (see ``agora.projects.store.ProjectStore``)."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _initialize(self) -> None:
        with closing(self._connect()) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_environments (
                    environment_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    -- The RemoteAdapterConfig field set. No credential is ever
                    -- stored: secret_ref names an env var, never the secret.
                    ssh_host TEXT NOT NULL,
                    ssh_port INTEGER NOT NULL DEFAULT 22,
                    ssh_user TEXT NOT NULL,
                    remote_workspace TEXT NOT NULL,
                    remote_project_root TEXT NOT NULL,
                    secret_ref TEXT NOT NULL,
                    host_key_mode TEXT NOT NULL DEFAULT 'strict',
                    known_hosts_path TEXT,
                    cleanup_worktree INTEGER NOT NULL DEFAULT 0,
                    -- Where the SSH credential comes from (CR-4 slice 2, adr/0001).
                    -- A password (source 'keyring') lives in the OS keyring, never
                    -- here; a key PATH ('key_path') is not a secret and is carried in
                    -- secret_ref; 'env_var' is the slice-1 default.
                    credential_source TEXT NOT NULL DEFAULT 'env_var',
                    -- The project whose root this is (one environment = one project).
                    project_id TEXT,
                    -- The claude-workspace tool directory on the host (CR-30 slice 3).
                    workspace_tool_dir TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Migrate a slice-1 table in place: add credential_source if an older row
            # set was created before it existed. ADD COLUMN with a DEFAULT backfills
            # every existing row to 'env_var' (the slice-1 behaviour), touches no data,
            # and is the safe migration on the shared control-plane DB (adr/0002). Guard
            # on the current columns so re-running _initialize is idempotent.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(remote_environments)")}
            if "credential_source" not in columns:
                db.execute(
                    "ALTER TABLE remote_environments "
                    "ADD COLUMN credential_source TEXT NOT NULL DEFAULT 'env_var'"
                )
            if "project_id" not in columns:
                # Nullable on purpose: an existing row's project is not something this
                # migration can know, and guessing it is the cross-project write this
                # column exists to stop. Such a row is refused at dispatch until edited.
                db.execute("ALTER TABLE remote_environments ADD COLUMN project_id TEXT")
            if "workspace_tool_dir" not in columns:
                # Nullable: "not registered" is the honest value for every existing row.
                db.execute("ALTER TABLE remote_environments ADD COLUMN workspace_tool_dir TEXT")
            db.commit()

    # ------------------------------------------------------------------ reads

    def list_environments(self) -> list[RemoteEnvironmentRecord]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM remote_environments ORDER BY environment_id"
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def get(self, environment_id: str) -> RemoteEnvironmentRecord | None:
        row = self._row(environment_id)
        return None if row is None else _record_from_row(row)

    def require(self, environment_id: str) -> RemoteEnvironmentRecord:
        record = self.get(environment_id)
        if record is None:
            raise RemoteEnvironmentNotFoundError(
                f"Unknown remote environment: {environment_id}"
            )
        return record

    def _row(self, environment_id: str) -> sqlite3.Row | None:
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT * FROM remote_environments WHERE environment_id = ?",
                (environment_id,),
            ).fetchone()

    # ----------------------------------------------------------------- writes

    def insert(self, record: RemoteEnvironmentRecord) -> RemoteEnvironmentRecord:
        validate_environment_id(record.environment_id)
        # Validate the SSH field set through the frozen adapter contract before we
        # write, so a bad host_key_mode never reaches the table.
        record.to_adapter_config()
        now = utc_now()
        try:
            with self._transaction() as db:
                db.execute(
                    """
                    INSERT INTO remote_environments (
                        environment_id, label, ssh_host, ssh_port, ssh_user,
                        remote_workspace, remote_project_root, secret_ref,
                        host_key_mode, known_hosts_path, cleanup_worktree,
                        credential_source, project_id, workspace_tool_dir, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*_columns(record), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise RemoteEnvironmentExistsError(
                f"Remote environment already exists: {record.environment_id}"
            ) from exc
        return self.require(record.environment_id)

    def update(
        self, environment_id: str, record: RemoteEnvironmentRecord
    ) -> RemoteEnvironmentRecord:
        """Replace every editable field of an existing row. The identifier itself
        is not editable — a rename is a delete plus a create, which keeps the
        primary key stable for anything that may later reference it."""
        record.to_adapter_config()  # validate before write, same as insert
        with self._transaction() as db:
            cursor = db.execute(
                """
                UPDATE remote_environments
                   SET label = ?, ssh_host = ?, ssh_port = ?, ssh_user = ?,
                       remote_workspace = ?, remote_project_root = ?, secret_ref = ?,
                       host_key_mode = ?, known_hosts_path = ?, cleanup_worktree = ?,
                       credential_source = ?, project_id = ?, workspace_tool_dir = ?, updated_at = ?
                 WHERE environment_id = ?
                """,
                (
                    record.label,
                    record.ssh_host,
                    record.ssh_port,
                    record.ssh_user,
                    record.remote_workspace,
                    record.remote_project_root,
                    record.secret_ref,
                    record.host_key_mode,
                    record.known_hosts_path,
                    1 if record.cleanup_worktree else 0,
                    record.credential_source,
                    record.project_id,
                    record.workspace_tool_dir,
                    utc_now(),
                    environment_id,
                ),
            )
            if cursor.rowcount == 0:
                raise RemoteEnvironmentNotFoundError(
                    f"Unknown remote environment: {environment_id}"
                )
        return self.require(environment_id)

    def delete(self, environment_id: str) -> bool:
        """Forget a registered environment. Returns whether a row was removed
        (idempotent: deleting an already-absent id is not an error).

        If the environment kept its password in the OS keyring (adr/0001), that entry
        is removed too, so a deleted environment does not orphan a secret in the OS
        store. The keyring delete is best-effort AFTER the row is gone: a keyring that
        is unavailable must not block forgetting the environment (the row is the
        authoritative record), and deleting an absent entry is already a no-op in
        ``credential_store``.

        Known gap (CR-10, deferred): if the keyring is unavailable at this moment (e.g.
        locked) the password stays in the OS store with no row left to retry from. It
        is overwritten if the same id is registered again; otherwise only removable by
        hand from the OS credential manager.
        """
        record = self.get(environment_id)
        with self._transaction() as db:
            cursor = db.execute(
                "DELETE FROM remote_environments WHERE environment_id = ?",
                (environment_id,),
            )
            removed = cursor.rowcount > 0
        if removed and record is not None and record.credential_source == "keyring":
            try:
                credential_store.delete_password(environment_id)
            except KeyringUnavailableError:
                # The secret may be left behind (CR-10, see docstring); the row is
                # already gone and forgetting the environment must not fail on this.
                pass
        return removed


def _columns(record: RemoteEnvironmentRecord) -> tuple[Any, ...]:
    return (
        record.environment_id,
        record.label,
        record.ssh_host,
        record.ssh_port,
        record.ssh_user,
        record.remote_workspace,
        record.remote_project_root,
        record.secret_ref,
        record.host_key_mode,
        record.known_hosts_path,
        1 if record.cleanup_worktree else 0,
        record.credential_source,
        record.project_id,
        record.workspace_tool_dir,
    )


def _record_from_row(row: sqlite3.Row) -> RemoteEnvironmentRecord:
    return RemoteEnvironmentRecord(
        environment_id=row["environment_id"],
        label=row["label"],
        ssh_host=row["ssh_host"],
        ssh_port=int(row["ssh_port"]),
        ssh_user=row["ssh_user"],
        remote_workspace=row["remote_workspace"],
        remote_project_root=row["remote_project_root"],
        secret_ref=row["secret_ref"],
        host_key_mode=row["host_key_mode"],
        known_hosts_path=row["known_hosts_path"],
        cleanup_worktree=bool(row["cleanup_worktree"]),
        credential_source=row["credential_source"],
        project_id=row["project_id"],
        workspace_tool_dir=row["workspace_tool_dir"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
