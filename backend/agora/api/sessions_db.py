"""Session persistence — SQLite storage for chat sessions."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

_DB_PATH: Path | None = None
_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _conn, _DB_PATH
    if _conn is not None:
        return _conn
    from agora.config.settings import get_config
    data_dir = Path(get_config().get("memory", {}).get("data_dir", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    _DB_PATH = data_dir / "agora.db"
    _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            messages TEXT NOT NULL DEFAULT '[]'
        )
    """)
    _conn.commit()
    return _conn


def create_session(title: str = "") -> str:
    db = _get_db()
    sid = uuid.uuid4().hex[:12]
    db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (sid, title))
    db.commit()
    return sid


def list_sessions() -> list[dict[str, Any]]:
    db = _get_db()
    rows = db.execute("SELECT id, title, created_at FROM sessions ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_session(sid: str) -> dict[str, Any] | None:
    db = _get_db()
    row = db.execute("SELECT id, title, created_at, messages FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["messages"] = json.loads(d["messages"])
    return d


def update_session_messages(sid: str, messages: list[dict]) -> None:
    db = _get_db()
    db.execute("UPDATE sessions SET messages = ? WHERE id = ?", (json.dumps(messages, ensure_ascii=False), sid))
    db.commit()


def update_session_title(sid: str, title: str) -> None:
    db = _get_db()
    db.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, sid))
    db.commit()


def delete_session(sid: str) -> None:
    db = _get_db()
    db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    db.commit()


# --- Shares ---

def _ensure_shares_table():
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS shares (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            messages TEXT NOT NULL DEFAULT '[]'
        )
    """)
    db.commit()


def create_share(messages: list[dict], session_id: str = "") -> str:
    _ensure_shares_table()
    db = _get_db()
    sid = uuid.uuid4().hex[:8]
    db.execute(
        "INSERT INTO shares (id, session_id, messages) VALUES (?, ?, ?)",
        (sid, session_id, json.dumps(messages, ensure_ascii=False)),
    )
    db.commit()
    return sid


def get_share(sid: str) -> dict[str, Any] | None:
    _ensure_shares_table()
    db = _get_db()
    row = db.execute("SELECT id, session_id, created_at, messages FROM shares WHERE id = ?", (sid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["messages"] = json.loads(d["messages"])
    return d
