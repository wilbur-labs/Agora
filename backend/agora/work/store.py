"""``work_sessions``: what the person wants open, the current launch, and the last
recovery point (CR-75 §2). On the one control-plane database, like the other stores.

The recovery point (``ck_*``) is written only by the server, from a journal line that
passed ``reports.read_journal``, and it is committed before a new launch replaces the
launch columns — an interrupted restore never loses the point it was restoring to.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class WorkSession:
    key: str
    source: str
    task: str
    workspace: str
    want_open: bool
    launch_id: str | None
    launch_mode: str | None
    expect_session: str | None
    shell_pid: int | None
    shell_created: float | None
    launched_at: float | None
    ck_session: str | None
    ck_transcript: str | None
    ck_launch: str | None
    ck_stale: bool


_COLUMNS = ("key, source, task, workspace, want_open, launch_id, launch_mode, expect_session, "
            "shell_pid, shell_created, launched_at, ck_session, ck_transcript, ck_launch, ck_stale")


class WorkSessionStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS work_sessions (
                    key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    task TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    -- What the person wants: 1 open, 0 closed. Cleared only by their close.
                    want_open INTEGER NOT NULL DEFAULT 0,
                    -- The current launch.
                    launch_id TEXT,
                    launch_mode TEXT,
                    expect_session TEXT,
                    shell_pid INTEGER,
                    shell_created REAL,
                    launched_at REAL,
                    -- The last recovery point, kept apart from the launch.
                    ck_session TEXT,
                    ck_transcript TEXT,
                    ck_launch TEXT,
                    ck_at REAL,
                    ck_stale INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                -- The person's board (workspace CR-81/82): todo or doing, and where the
                -- project sits in the work page's list. Agora's own, never written into
                -- a task record; kept apart from the launch bookkeeping above.
                CREATE TABLE IF NOT EXISTS work_board (
                    key TEXT PRIMARY KEY,
                    stage TEXT NOT NULL CHECK (stage IN ('todo', 'doing')),
                    position INTEGER,
                    updated_at REAL NOT NULL
                );
                """
            )
            # What was meant to be open before the board existed is "doing", written down —
            # not inferred from want_open, which a close clears (codex review CR-81 round 1).
            # Idempotent: only keys with no board row, and every later open writes its own.
            db.execute("INSERT OR IGNORE INTO work_board (key, stage, position, updated_at) "
                       "SELECT key, 'doing', NULL, ? FROM work_sessions WHERE want_open = 1", (time.time(),))
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=60)
        db.row_factory = sqlite3.Row
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

    @staticmethod
    def _row(row: sqlite3.Row) -> WorkSession:
        return WorkSession(
            key=row["key"], source=row["source"], task=row["task"], workspace=row["workspace"],
            want_open=bool(row["want_open"]), launch_id=row["launch_id"], launch_mode=row["launch_mode"],
            expect_session=row["expect_session"], shell_pid=row["shell_pid"], shell_created=row["shell_created"],
            launched_at=row["launched_at"], ck_session=row["ck_session"], ck_transcript=row["ck_transcript"],
            ck_launch=row["ck_launch"], ck_stale=bool(row["ck_stale"]),
        )

    def get(self, key: str) -> WorkSession | None:
        with closing(self._connect()) as db:
            row = db.execute(f"SELECT {_COLUMNS} FROM work_sessions WHERE key = ?", (key,)).fetchone()
        return self._row(row) if row else None

    def all(self) -> list[WorkSession]:
        with closing(self._connect()) as db:
            rows = db.execute(f"SELECT {_COLUMNS} FROM work_sessions ORDER BY key").fetchall()
        return [self._row(r) for r in rows]

    def begin_launch(self, *, key: str, source: str, task: str, workspace: str, launch_id: str,
                     launch_mode: str, expect_session: str | None) -> None:
        """The intent first (CR-75 §2): ``want_open=1`` and the new launch, before any process."""
        now = time.time()
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO work_sessions (key, source, task, workspace, want_open, launch_id, launch_mode,
                                           expect_session, shell_pid, shell_created, launched_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, NULL, NULL, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    source = excluded.source, task = excluded.task, workspace = excluded.workspace,
                    want_open = 1, launch_id = excluded.launch_id, launch_mode = excluded.launch_mode,
                    expect_session = excluded.expect_session, shell_pid = NULL, shell_created = NULL,
                    launched_at = excluded.launched_at, updated_at = excluded.updated_at
                """,
                (key, source, task, workspace, launch_id, launch_mode, expect_session, now, now),
            )

    def set_shell(self, key: str, launch_id: str, pid: int | None, created: float | None) -> None:
        with self._transaction() as db:
            db.execute("UPDATE work_sessions SET shell_pid = ?, shell_created = ?, updated_at = ? "
                       "WHERE key = ? AND launch_id = ?", (pid, created, time.time(), key, launch_id))

    def set_want_open(self, key: str, want: bool) -> None:
        with self._transaction() as db:
            db.execute("UPDATE work_sessions SET want_open = ?, updated_at = ? WHERE key = ?",
                       (1 if want else 0, time.time(), key))

    # --- the board (CR-81/82) ---------------------------------------------------------------
    def board(self) -> dict[str, tuple[str, int | None]]:
        """``{key: (stage, position)}`` for every project the person has placed."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT key, stage, position FROM work_board").fetchall()
        return {r["key"]: (r["stage"], r["position"]) for r in rows}

    def set_stage(self, key: str, stage: str) -> None:
        if stage not in ("todo", "doing"):
            raise ValueError(stage)
        with self._transaction() as db:
            db.execute(
                "INSERT INTO work_board (key, stage, position, updated_at) VALUES (?, ?, NULL, ?) "
                "ON CONFLICT(key) DO UPDATE SET stage = excluded.stage, updated_at = excluded.updated_at",
                (key, stage, time.time()),
            )

    def set_order(self, keys: list[str], stages: dict[str, str]) -> None:
        """Positions 0.. in the order given, in one transaction. A key with no board row
        yet gets one at ``stages[key]`` — the stage the page was showing it under."""
        now = time.time()
        with self._transaction() as db:
            for position, key in enumerate(keys):
                db.execute(
                    "INSERT INTO work_board (key, stage, position, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET position = excluded.position, updated_at = excluded.updated_at",
                    (key, stages[key], position, now),
                )

    def advance_checkpoint(self, key: str, *, session: str | None, transcript: str | None,
                           launch: str | None, stale: bool) -> None:
        """Move the recovery point forward. ``session=None`` only marks the current one stale."""
        with self._transaction() as db:
            if session is None:
                db.execute("UPDATE work_sessions SET ck_stale = ?, updated_at = ? WHERE key = ?",
                           (1 if stale else 0, time.time(), key))
            else:
                db.execute(
                    "UPDATE work_sessions SET ck_session = ?, ck_transcript = ?, ck_launch = ?, ck_at = ?, "
                    "ck_stale = ?, updated_at = ? WHERE key = ?",
                    (session, transcript, launch, time.time(), 1 if stale else 0, time.time(), key),
                )
