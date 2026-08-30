"""SQLite persistence for projects — the authoritative project store.

Projects used to live only in ``.agora/projects.yaml``. That made a project's
existence, root and agent assignment knowable only by reading a file the database
could not see, so a run could be gated, dispatched and settled against a project
no SQL query would ever return.

This store makes the database authoritative. It deliberately uses the *same*
SQLite file as tasks, runs and gates (see
``agora.config.settings.resolve_control_plane_db_path``) so that a project and
the rows referencing it can be written in one transaction. A separate file would
have kept the two-store split this change exists to remove.

The YAML is now an import source only: see ``agora.projects.importer``. This
module never reads or writes it.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from agora.tasks.models import utc_now

from .models import (
    DEFAULT_REVIEWER_AGENT,
    DEFAULT_WORKER_AGENT,
    Project,
    validate_agents,
    validate_project_id,
)

# Rows created by importing the maintainer's YAML versus rows created through
# Agora itself. The distinction is what lets a re-import tell "the maintainer
# edited the file" apart from "somebody changed this project in the product".
ORIGIN_YAML_IMPORT = "yaml_import"
ORIGIN_AGORA = "agora"

# project_settings keys. Scalar control-plane state that is not per-project.
SETTING_CURRENT_PROJECT = "current_project_id"
SETTING_PROJECTS_BASE = "projects_base"
SETTING_LAST_IMPORT_ERROR = "last_import_error"


class ProjectExistsError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectProvenance:
    """Where a project row came from, and what has happened to it since.

    ``imported_snapshot`` is the exact field set written at import time.
    ``agora_revision`` counts writes made through Agora and ``imported_revision``
    is what that counter read at the last import, so the two together answer the
    question a second import has to ask — has anyone changed this project through
    Agora since? — for a change that was later changed back, which comparing
    values cannot.
    """

    origin: str
    imported_from: str | None
    imported_at: str | None
    imported_snapshot: dict[str, Any] | None
    agora_revision: int
    imported_revision: int | None

    @property
    def changed_in_agora_since_import(self) -> bool:
        """True unless it can be proved that no Agora-side write has happened.

        A row with no recorded import revision (created through Agora, or written
        by a database older than the counter) cannot prove it, so it reads as
        changed and the import reports a conflict rather than overwriting it.
        """
        if self.imported_revision is None:
            return True
        return self.agora_revision != self.imported_revision


@dataclass(frozen=True)
class ImportWriteOutcome:
    """What one atomic import decision did to one project row.

    ``status`` is ``inserted``, ``updated``, ``unchanged`` or ``conflict``.
    ``stored_fields`` is the row as it stands after the decision, which for a
    conflict is what the store kept and is what the conflict is reported against.
    """

    status: str
    project: Project
    stored_fields: dict[str, Any]


class ProjectStore:
    """Synchronous store for projects, matching the other control-plane stores."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self, *, isolation_level: str | None = "") -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=isolation_level)
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

    @contextmanager
    def _immediate_transaction(self) -> Iterator[sqlite3.Connection]:
        """A transaction that takes the write lock before its first read.

        Any decision of the form "read the row, then write it if it still looks
        like X" must run in here. SQLite's default transaction is deferred, so a
        plain SELECT takes no write lock and another connection can change the row
        between the comparison and the UPDATE — the comparison would then be about
        a row that no longer exists in that shape, and the concurrent write would
        be overwritten without anyone noticing. ``BEGIN IMMEDIATE`` makes the read
        and the write one serialised unit.
        """
        with closing(self._connect(isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def _initialize(self) -> None:
        with closing(self._connect()) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    -- Local-environment location of the project. Not identity:
                    -- see the environment note in agora/projects/models.py
                    -- before adding anything that keys or joins on this column.
                    root TEXT NOT NULL,
                    workspaces TEXT NOT NULL DEFAULT '{}',
                    worker_agent TEXT NOT NULL,
                    reviewer_agent TEXT NOT NULL,
                    charter TEXT NOT NULL DEFAULT '{}',
                    origin TEXT NOT NULL,
                    imported_from TEXT,
                    imported_at TEXT,
                    imported_snapshot TEXT,
                    -- Bumped by every Agora-side write to this row (see _update).
                    -- imported_revision is its value at the last import. Equal
                    -- counters mean no Agora write has touched the row since,
                    -- which a value comparison cannot tell: a change made through
                    -- Agora and changed back looks identical to never changed.
                    agora_revision INTEGER NOT NULL DEFAULT 0,
                    imported_revision INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Databases created before the revision counters existed keep serving.
            # Their rows get imported_revision NULL, which reads as "cannot prove
            # nobody changed this through Agora", so an import reports a conflict
            # instead of overwriting them. Fail closed, once, per old row.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
            for column, ddl in (
                ("agora_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("imported_revision", "INTEGER"),
            ):
                if column not in columns:
                    db.execute(f"ALTER TABLE projects ADD COLUMN {column} {ddl}")
            db.commit()

    # ------------------------------------------------------------------ reads

    def list_projects(self) -> dict[str, Project]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM projects ORDER BY project_id").fetchall()
        return {row["project_id"]: _project_from_row(row) for row in rows}

    def get(self, project_id: str) -> Project | None:
        row = self._row(project_id)
        return None if row is None else _project_from_row(row)

    def require(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project is None:
            raise KeyError(f"Unknown project: {project_id}")
        return project

    def exists(self, project_id: str) -> bool:
        return self._row(project_id) is not None

    def count(self) -> int:
        with closing(self._connect()) as db:
            return int(db.execute("SELECT COUNT(*) FROM projects").fetchone()[0])

    def provenance(self, project_id: str) -> ProjectProvenance | None:
        row = self._row(project_id)
        if row is None:
            return None
        raw = row["imported_snapshot"]
        imported_revision = row["imported_revision"]
        return ProjectProvenance(
            origin=row["origin"],
            imported_from=row["imported_from"],
            imported_at=row["imported_at"],
            imported_snapshot=json.loads(raw) if raw else None,
            agora_revision=int(row["agora_revision"] or 0),
            imported_revision=None if imported_revision is None else int(imported_revision),
        )

    def _row(self, project_id: str) -> sqlite3.Row | None:
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()

    # ----------------------------------------------------------------- writes

    def insert(
        self,
        project_id: str,
        *,
        name: str,
        root: Path,
        workspaces: dict[str, Path],
        worker_agent: str = DEFAULT_WORKER_AGENT,
        reviewer_agent: str = DEFAULT_REVIEWER_AGENT,
        charter: dict[str, Any] | None = None,
        origin: str = ORIGIN_AGORA,
        imported_from: str | None = None,
        imported_snapshot: dict[str, Any] | None = None,
    ) -> Project:
        validate_project_id(project_id)
        validate_agents(worker_agent, reviewer_agent)
        try:
            with self._transaction() as db:
                _insert_row(
                    db,
                    project_id,
                    name=name,
                    root=root,
                    workspaces=workspaces,
                    worker_agent=worker_agent,
                    reviewer_agent=reviewer_agent,
                    charter=charter,
                    origin=origin,
                    imported_from=imported_from,
                    imported_snapshot=imported_snapshot,
                    now=utc_now(),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectExistsError(f"Project already exists: {project_id}") from exc
        return self.require(project_id)

    def apply_import(
        self,
        project_id: str,
        *,
        fields: dict[str, Any],
        root: Path,
        workspaces: dict[str, Path],
        imported_from: str,
    ) -> ImportWriteOutcome:
        """Decide and carry out one project's import in a single transaction.

        The comparison and the write it authorises are the same serialised unit,
        because they are one decision: "this row is still exactly what we imported,
        so the file may replace it". Reading the row, deciding outside a
        transaction and then issuing an unconditional UPDATE would let an API
        write land in between and be overwritten — and since constructing a
        registry runs an import, that race is against ordinary request traffic,
        not a rare batch job.

        ``fields`` must be the comparable field set produced the same way as
        :func:`project_fields`, which is what makes the equality check meaningful.
        """
        validate_project_id(project_id)
        validate_agents(fields["worker_agent"], fields["reviewer_agent"])
        now = utc_now()
        with self._immediate_transaction() as db:
            row = db.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                _insert_row(
                    db,
                    project_id,
                    name=fields["name"],
                    root=root,
                    workspaces=workspaces,
                    worker_agent=fields["worker_agent"],
                    reviewer_agent=fields["reviewer_agent"],
                    charter=fields["charter"],
                    origin=ORIGIN_YAML_IMPORT,
                    imported_from=imported_from,
                    imported_snapshot=fields,
                    now=now,
                )
                status = "inserted"
            elif project_fields(_project_from_row(row)) == fields:
                status = "unchanged"
            elif _untouched_since_import(row):
                db.execute(
                    """
                    UPDATE projects
                       SET name = ?, root = ?, workspaces = ?, worker_agent = ?,
                           reviewer_agent = ?, charter = ?, origin = ?,
                           imported_from = ?, imported_at = ?,
                           imported_snapshot = ?, imported_revision = agora_revision,
                           updated_at = ?
                     WHERE project_id = ?
                    """,
                    (
                        fields["name"],
                        str(root),
                        json.dumps({k: str(v) for k, v in sorted(workspaces.items())}),
                        fields["worker_agent"],
                        fields["reviewer_agent"],
                        json.dumps(fields["charter"] or {}),
                        ORIGIN_YAML_IMPORT,
                        imported_from,
                        now,
                        json.dumps(fields),
                        now,
                        project_id,
                    ),
                )
                status = "updated"
            else:
                status = "conflict"
            final = db.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        project = _project_from_row(final)
        return ImportWriteOutcome(
            status=status, project=project, stored_fields=project_fields(project)
        )

    def set_agents(self, project_id: str, *, worker_agent: str, reviewer_agent: str) -> Project:
        validate_agents(worker_agent, reviewer_agent)
        self._update(
            project_id,
            "worker_agent = ?, reviewer_agent = ?",
            (worker_agent, reviewer_agent),
        )
        return self.require(project_id)

    def set_charter(self, project_id: str, charter: dict[str, Any]) -> Project:
        self._update(project_id, "charter = ?", (json.dumps(charter),))
        return self.require(project_id)

    def _update(self, project_id: str, assignment: str, params: tuple[Any, ...]) -> None:
        """The one Agora-side write path for a project row.

        Every such write bumps ``agora_revision``. That counter is the only thing
        that can answer "has Agora changed this row since it was imported" for a
        change that was later changed back, so a new per-project write must go
        through here rather than issuing its own UPDATE.
        """
        with self._transaction() as db:
            cursor = db.execute(
                f"UPDATE projects SET {assignment}, agora_revision = agora_revision + 1, "
                "updated_at = ? WHERE project_id = ?",
                (*params, utc_now(), project_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown project: {project_id}")

    # --------------------------------------------------------------- settings

    def get_setting(self, key: str) -> str | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT value FROM project_settings WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else row["value"]

    def set_setting(self, key: str, value: str) -> None:
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO project_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def clear_setting(self, key: str) -> None:
        with self._transaction() as db:
            db.execute("DELETE FROM project_settings WHERE key = ?", (key,))

    def current_project_id(self) -> str | None:
        return self.get_setting(SETTING_CURRENT_PROJECT)

    def set_current_project_id(self, project_id: str) -> None:
        """Move the current-project pointer. Fails closed on an unknown project:
        the pointer may never name a project the store cannot resolve. The check
        and the write share one immediate transaction so the check cannot be about
        a row that changed before the write landed."""
        with self._immediate_transaction() as db:
            row = db.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown project: {project_id}")
            db.execute(
                """
                INSERT INTO project_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
                """,
                (SETTING_CURRENT_PROJECT, project_id, utc_now()),
            )


def _insert_row(
    db: sqlite3.Connection,
    project_id: str,
    *,
    name: str,
    root: Path,
    workspaces: dict[str, Path],
    worker_agent: str,
    reviewer_agent: str,
    charter: dict[str, Any] | None,
    origin: str,
    imported_from: str | None,
    imported_snapshot: dict[str, Any] | None,
    now: str,
) -> None:
    """Insert one project row on an open connection.

    Shared by :meth:`ProjectStore.insert` and :meth:`ProjectStore.apply_import` so
    that the two cannot disagree about what a new row records — in particular that
    an imported row starts with ``imported_revision`` equal to its revision
    counter, and a row created through Agora has no import revision at all.
    """
    db.execute(
        """
        INSERT INTO projects (
            project_id, name, root, workspaces, worker_agent,
            reviewer_agent, charter, origin, imported_from,
            imported_at, imported_snapshot, agora_revision, imported_revision,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            name,
            str(root),
            json.dumps({k: str(v) for k, v in sorted(workspaces.items())}),
            worker_agent,
            reviewer_agent,
            json.dumps(charter or {}),
            origin,
            imported_from,
            now if imported_from else None,
            json.dumps(imported_snapshot) if imported_snapshot else None,
            0,
            0 if imported_from else None,
            now,
            now,
        ),
    )


def _untouched_since_import(row: sqlite3.Row) -> bool:
    """Has nothing changed this row through Agora since its last import?

    Both conditions are required, and both are about evidence rather than a guess:

    * the revision counter still reads what it read at import time — this is what
      catches a change made through Agora and then changed back, which leaves the
      values identical to the import;
    * the row still equals the snapshot recorded at import time — a row altered by
      something that does not go through this store (a hand-edited database, a
      write path that bypassed ``_update``) counts as touched and is kept, not
      overwritten.

    Anything unprovable answers False, so the import reports a conflict and the
    stored row survives.
    """
    imported_revision = row["imported_revision"]
    if imported_revision is None:
        return False
    if int(row["agora_revision"] or 0) != int(imported_revision):
        return False
    raw = row["imported_snapshot"]
    if not raw:
        return False
    return json.loads(raw) == project_fields(_project_from_row(row))


def _project_from_row(row: sqlite3.Row) -> Project:
    root = Path(row["root"])
    workspaces = {
        key: Path(value) for key, value in json.loads(row["workspaces"] or "{}").items()
    }
    return Project(
        project_id=row["project_id"],
        name=row["name"],
        root=root,
        workspaces=workspaces,
        worker_agent=row["worker_agent"] or DEFAULT_WORKER_AGENT,
        reviewer_agent=row["reviewer_agent"] or DEFAULT_REVIEWER_AGENT,
        charter=json.loads(row["charter"] or "{}"),
    )


def project_fields(project: Project) -> dict[str, Any]:
    """The comparable field set of a project, as plain JSON-safe values.

    Used to decide whether a row still matches what was imported. Paths are
    compared as strings because that is how they are stored.
    """
    return {
        "name": project.name,
        "root": str(project.root),
        "workspaces": {k: str(v) for k, v in sorted(project.workspaces.items())},
        "worker_agent": project.worker_agent,
        "reviewer_agent": project.reviewer_agent,
        "charter": project.charter,
    }
