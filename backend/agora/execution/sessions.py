"""The provider session a task's conversation continues in.

Agora's primitive is "one task = one continuing session" (``tasks/agora/adr/0007``),
and continuity is provided by the vendors' own resume, not by replaying history
into a fresh process. So the control plane has to hold one durable fact per task,
adapter **and role**: which provider session this conversation is.

The role is in that key, not beside it. A worker turn and the independent review
of that turn can run on the same adapter — an ordinary configuration — and keying
by ``(task_id, adapter)`` alone made the review *resume the implementation
session*, inheriting the implementer's entire context. That is the one thing a
second opinion exists not to do (adr/0002).

Four rules shape everything below.

1. **A session is scoped to a working directory.** All three CLIs file sessions
   under the cwd they ran in (claude by cwd slug, codex and kiro by a cwd filter),
   so a session recorded against one worktree is not visible from another. The
   workspace is therefore part of the record, and a mismatch is a reason to stop,
   not a detail to overlook.

2. **"Cannot be read" is never rendered as "does not exist".** If a session is on
   record but cannot be continued here, the turn is refused with that reason. The
   tempting alternative — quietly start a new session — is the one behaviour
   adr/0007 rules out by name, because it silently drops the context the user is
   relying on and looks identical to success.

3. **A new session is always an explicit act.** ``forget`` exists for that, and it
   is the only thing that clears a session id.

4. **A review never continues the work it reviews.** Its role gives it its own
   session, so "resume" for a review turn means resuming *earlier reviews*, never
   the implementation.

Stored in its own tables rather than in ``execution_runs.result_metadata``: that
column is written wholesale when a run settles (see ``models.IDENTITY_METADATA_KEYS``
for the incident), so a session fact written at launch would be erased by the
run's own result.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from pydantic import BaseModel

from .models import ROLE_WORKER

#: A session Agora believes it can continue: an id is on record, and it was last
#: seen in the workspace this task's runs use.
SESSION_ACTIVE = "active"
#: An id is on record and a **live condition** stops this configuration from
#: continuing it: the workspace moved, or the adapter has no resume command. Not
#: "no session" — the context is probably still there, somewhere this
#: configuration cannot reach.
#:
#: ``decide`` re-derives both conditions on every turn rather than trusting this
#: flag, so the state heals on its own once the condition goes away (put the
#: worktree back and the next message continues the conversation). That is the
#: whole reason it is a different state from ``SESSION_LOST`` below.
SESSION_UNREADABLE = "unreadable"
#: A resumed turn ran and the provider named a **different** session than the
#: one it was asked to continue — the CLI forked instead of resuming, a wrapper
#: substituted one, a version changed behaviour. The turn's own work is fine; what
#: is not fine is calling the result "the same conversation", because the context
#: the person was relying on is not in it.
#:
#: Recorded with the new id **beside** the old one rather than over it, and the
#: window is expected to say a fork happened. Overwriting was the silent version:
#: the badge kept reading "session live" while the conversation had restarted.
SESSION_FORKED = "forked"
#: A resumed turn failed **and the provider never named a session in it**. All
#: three CLIs announce the session in their first event, so silence means the
#: process died before it had one — the id Agora holds was not picked up.
#:
#: This one is **not** re-derivable from anything ``decide`` can look at, so it
#: blocks: the next turn is refused until the person explicitly starts a new
#: session. What it deliberately does not claim is that the session is gone. It
#: says what was observed once, which is why the id is kept.
SESSION_LOST = "lost"
#: An id was chosen by Agora and handed to a CLI, and **no provider event has
#: confirmed it yet**. Only claude reaches this state, because only claude takes
#: a caller-chosen id.
#:
#: It is not ``active``: if that first process died before announcing anything —
#: bad arguments, no credentials, a cancel — the session may never have been
#: created, and the next message would ``--resume`` an id the provider has never
#: heard of. It is not ``lost`` either: nothing was ever established, so there is
#: no context to lose, and the honest next step is to start cleanly rather than
#: to block. ``decide`` therefore starts a **new** session from here, which is the
#: one place a start replaces a recorded id — and it is safe precisely because
#: the record was never confirmed.
#:
#: The cost, stated: if the process did create the session and died silently, the
#: first prompt's context is dropped. That prompt's turn failed anyway, and the
#: alternative is refusing every later message over a session nobody can show.
SESSION_PENDING = "pending"
#: No session on record. The next turn starts one.
SESSION_NONE = "none"
#: The role a legacy row belonged to could not be established. Sessions written
#: before the role existed were keyed ``(task_id, adapter)`` and **both** the
#: worker and the review path wrote through that key, so a migrated row may be
#: either — and there is nothing in it to tell them apart.
#:
#: Such a row is filed under this role rather than under ``worker``. It is
#: therefore invisible to ``get(task, adapter, "worker")``, which answers
#: ``none`` — the next worker turn starts cleanly, and the badge says so, which is
#: true. Nothing is deleted: the id stays listed, so it can still be seen. The
#: alternative, asserting ``worker``, would have resumed a *review* conversation
#: as the user's own on any task where an adapter had only ever reviewed — the
#: wrong context, and across the boundary adr/0002 exists to hold.
ROLE_UNKNOWN = "unknown"
#: A turn ran and **no session id could be read from its output**. Not the same
#: as "no session": one may exist on the provider's side and Agora simply cannot
#: name it — a CLI whose event shape changed, or a settlement line lost to a
#: truncated read.
#:
#: Recorded so the *next* turn does not read as a clean first turn. It cannot be
#: resumed (there is no id), so the next message does start a new conversation —
#: but the window says the previous turn's context is not being carried, rather
#: than implying there was never any to carry.
SESSION_UNIDENTIFIED = "unidentified"
#: This execution mode does not carry a provider session at all — today, the
#: codex app-server bridge, whose continuity story is unverified. Recorded rather
#: than left blank: a bridge that starts a fresh context every turn while the
#: window shows a running conversation is the failure adr/0007 exists to prevent,
#: and the only defence a half-built product has is saying so on screen.
SESSION_UNSUPPORTED = "unsupported"

MODE_START = "start"
MODE_RESUME = "resume"


class AgentSession(BaseModel):
    """What the control plane knows about one task's conversation."""

    task_id: str
    adapter: str
    #: Part of a session's identity, not a label beside it: a review turn must
    #: not resume the implementation session even when both use one adapter.
    role: str = ROLE_WORKER
    state: str
    session_id: str | None
    workspace: str | None
    #: Why the state is ``unreadable``. Empty for every other state, and shown to
    #: the user verbatim — it is the difference between "start a new one" and
    #: "fix the worktree".
    reason: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    last_run_id: str | None = None
    #: Turns Agora has actually run in this session, so a window can say "third
    #: turn" rather than implying a conversation whose length it never counted.
    turns: int = 0
    #: The id this conversation had before a resume landed somewhere else. Set
    #: only with state ``forked``, and kept so the earlier context is still
    #: nameable rather than merely gone.
    previous_session_id: str | None = None


class RunSession(BaseModel):
    """The session one run used, and whether the provider ever confirmed it."""

    run_id: str
    session_id: str | None
    mode: str
    #: True once this run's event stream carried a session id. For codex and kiro
    #: that is the only way the id is learned at all; for claude, where Agora
    #: chooses the id, it is the provider agreeing that the id it was handed is
    #: the session it is in.
    confirmed: bool
    workspace: str


@dataclass(frozen=True)
class SessionDecision:
    """How the next turn should be launched."""

    mode: str
    session_id: str | None
    #: The clear generation this decision was made under. ``record_launch``
    #: writes the session row only if it is still current, so a clear that lands
    #: between deciding and launching is not undone by the launch.
    clear_seq: int = 0
    #: Set when the turn must not be launched at all. The dispatcher settles the
    #: run with this text; nothing falls back to starting a new session.
    refusal: str | None = None
    #: True when the refusal came from a **live condition** — the workspace, or
    #: the adapter's lack of a resume command — rather than from the recorded
    #: state. Only these may be written back as ``unreadable``: doing it for a
    #: recorded ``lost`` refusal overwrote the state that blocks with the one that
    #: heals, and the very next turn was let through.
    condition_based: bool = False

    @property
    def refused(self) -> bool:
        return self.refusal is not None


def initialize_session_schema(db: sqlite3.Connection) -> None:
    _migrate_legacy_sessions(db)
    statements = (
        """
        CREATE TABLE IF NOT EXISTS agent_sessions (
            task_id TEXT NOT NULL,
            adapter TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'worker',
            session_id TEXT,
            workspace TEXT NOT NULL,
            state TEXT NOT NULL,
            reason TEXT,
            turns INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_run_id TEXT,
            previous_session_id TEXT,
            cleared_at TEXT,
            clear_seq INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (task_id, adapter, role)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS execution_run_sessions (
            run_id TEXT PRIMARY KEY,
            session_id TEXT,
            mode TEXT NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            workspace TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            clear_seq INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_task
            ON agent_sessions(task_id)
        """,
    )
    for statement in statements:
        db.execute(statement)
    # Additive columns for a table this build may already have created. The
    # check and the change are not one transaction — two control planes starting
    # together both read "column missing", and the second `ALTER TABLE` then
    # failed with a duplicate-column error that stopped the process from
    # starting. The race is left in place and its *outcome* handled: the second
    # one finds the column already added, which is exactly the state it wanted.
    _add_column(db, "agent_sessions", "previous_session_id", "TEXT")
    _add_column(db, "agent_sessions", "cleared_at", "TEXT")
    _add_column(db, "agent_sessions", "clear_seq", "INTEGER NOT NULL DEFAULT 0")
    run_columns = {row[1] for row in db.execute("PRAGMA table_info(execution_run_sessions)")}
    if run_columns:
        _add_column(db, "execution_run_sessions", "clear_seq", "INTEGER NOT NULL DEFAULT 0")


def _add_column(
    db: sqlite3.Connection, table: str, column: str, declaration: str
) -> None:
    """Add a column if it is not there, tolerating another process adding it first.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, so the alternative to catching
    the duplicate is holding a write lock around a read — and the read is on
    every start-up path. The error text is checked rather than swallowed
    wholesale: anything that is not "this column already exists" is still a
    reason to stop.
    """
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if column in columns:
        return
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    except sqlite3.OperationalError as error:
        if "duplicate column" not in str(error).lower():
            raise


def _migrate_legacy_sessions(db: sqlite3.Connection) -> None:
    """Add ``role`` to the primary key of a table created before it existed.

    The role has to be part of a session's identity, not a column beside it: the
    key decides which conversation a turn resumes, and ``(task_id, adapter)``
    made an automatic **review** resume the implementation session whenever the
    reviewer and the worker are the same adapter — which is an ordinary
    configuration. The independent second opinion would then have inherited the
    implementer's whole context, which is the one thing it exists not to do
    (adr/0002).

    SQLite cannot widen a primary key in place, so the table is rebuilt. Existing
    rows are filed under :data:`ROLE_UNKNOWN`, **not** ``worker``: the old key was
    ``(task_id, adapter)`` and both the worker and the review path wrote through
    it, so a migrated row may be either and carries nothing that says which. An
    earlier version of this docstring asserted only the worker turn ever recorded
    one, which was false — and acting on it would have resumed a review
    conversation as the user's own wherever an adapter had only ever reviewed.

    **Atomic, and recoverable if it was not.** DDL in sqlite commits as it runs,
    so the rename and the copy were two separate commits: a process that stopped
    between them left the real session ids in ``agent_sessions_pre_role`` and no
    ``agent_sessions`` at all — and the next startup, seeing none, created an
    empty one and never looked at the other again. Every conversation on that
    installation would have silently started over. So the rebuild runs inside one
    explicit transaction, *and* a leftover ``_pre_role`` table is treated as the
    interrupted case and drained on the next start.
    """
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "agent_sessions_pre_role" in tables:
        # An interrupted rebuild. Finish it rather than leaving the rows behind:
        # they are somebody's live conversations. The list is re-read **inside**
        # the lock by `_drain_pre_role`, because two control planes starting
        # together both see the old table here and the second one would otherwise
        # act on a world the first has already changed — creating a table that
        # now exists, or reading one that has just been dropped.
        _drain_pre_role(db, tables)
        return
    if "agent_sessions" not in tables:
        return
    columns = {row[1] for row in db.execute("PRAGMA table_info(agent_sessions)")}
    if "role" in columns:
        return
    db.execute("BEGIN IMMEDIATE")
    # Asked **again**, now that this connection holds the write lock. The check
    # above is a read: two control planes opening the same old database both saw
    # "no role", and the second one — arriving after the first had finished —
    # renamed the *migrated* table back to `agent_sessions_pre_role` and refiled
    # every row as `unknown`. From then on both the worker and the review path
    # were refused on tasks that had been fine a second earlier, and where a task
    # had rows for two roles the rebuild hit a primary-key conflict and the
    # process failed to start at all.
    columns = {row[1] for row in db.execute("PRAGMA table_info(agent_sessions)")}
    if "role" in columns:
        db.execute("ROLLBACK")
        return
    db.execute("ALTER TABLE agent_sessions RENAME TO agent_sessions_pre_role")
    db.execute(
        """
        CREATE TABLE agent_sessions (
            task_id TEXT NOT NULL,
            adapter TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'worker',
            session_id TEXT,
            workspace TEXT NOT NULL,
            state TEXT NOT NULL,
            reason TEXT,
            turns INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_run_id TEXT,
            PRIMARY KEY (task_id, adapter, role)
        )
        """
    )
    db.execute(
        """
        INSERT INTO agent_sessions
            (task_id, adapter, role, session_id, workspace, state, reason, turns,
             first_seen_at, last_seen_at, last_run_id)
        SELECT task_id, adapter, 'unknown', session_id, workspace, state,
               COALESCE(reason, '') ||
               ' （这条会话记录于 role 进入主键之前，无法判定它属于 worker 还是 review，' ||
               '因此没有归入任何一方。在它被显式清除之前，这个适配器上的每一轮都会被拒绝——' ||
               '既不能当作你的会话续接，也不能当作不存在。对话窗口会显示它并给出清除按钮' ||
               '（清除的是 role=unknown 这一行，清 worker 清不掉它）。）',
               turns, first_seen_at, last_seen_at, last_run_id
          FROM agent_sessions_pre_role
        """
    )
    db.execute("DROP TABLE agent_sessions_pre_role")
    db.commit()


def _drain_pre_role(db: sqlite3.Connection, tables: set[str]) -> None:
    """Finish a rebuild that stopped partway, without losing a row.

    Reached when ``agent_sessions_pre_role`` is still on disk. Either the copy
    never ran (no ``agent_sessions``) or it ran and the drop did not; both are
    resolved by copying what is not already there and then dropping. ``INSERT OR
    IGNORE`` is what makes the second case safe — a row already migrated is left
    exactly as it is, including any state change it has seen since.
    """
    db.execute("BEGIN IMMEDIATE")
    # Re-read now that this connection holds the write lock. The caller's list
    # was taken before it, and with two control planes starting together the
    # other one may have finished the whole rebuild in between — leaving this one
    # to create a table that now exists, or to copy from one that has just been
    # dropped. Both end the process before it can serve anything.
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "agent_sessions_pre_role" not in tables:
        # Someone else finished it. Nothing to drain.
        db.execute("ROLLBACK")
        return
    if "agent_sessions" not in tables:
        db.execute(
            """
            CREATE TABLE agent_sessions (
                task_id TEXT NOT NULL,
                adapter TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'worker',
                session_id TEXT,
                workspace TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT,
                turns INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_run_id TEXT,
                previous_session_id TEXT,
                PRIMARY KEY (task_id, adapter, role)
            )
            """
        )
    # A pre-role table normally has no `role` column — that is what makes it
    # pre-role. But a table *renamed* by an interrupted rebuild can already carry
    # one, and forcing every row to `unknown` then collided two roles on one
    # primary key: `INSERT OR IGNORE` dropped the second silently and the
    # `DROP TABLE` below made it unrecoverable. Where the column is there, it is
    # the answer.
    columns = {row[1] for row in db.execute("PRAGMA table_info(agent_sessions_pre_role)")}
    if "role" in columns:
        db.execute(
            """
            INSERT OR IGNORE INTO agent_sessions
                (task_id, adapter, role, session_id, workspace, state, reason, turns,
                 first_seen_at, last_seen_at, last_run_id)
            SELECT task_id, adapter, role, session_id, workspace, state, reason,
                   turns, first_seen_at, last_seen_at, last_run_id
              FROM agent_sessions_pre_role
            """
        )
    else:
        db.execute(
            """
            INSERT OR IGNORE INTO agent_sessions
                (task_id, adapter, role, session_id, workspace, state, reason, turns,
                 first_seen_at, last_seen_at, last_run_id)
            SELECT task_id, adapter, 'unknown', session_id, workspace, state,
                   COALESCE(reason, '') ||
                   ' （这条会话记录于 role 进入主键之前，无法判定它属于 worker 还是 review，' ||
                   '因此没有归入任何一方。在它被显式清除之前，这个适配器上的每一轮都会被拒绝——' ||
                   '既不能当作你的会话续接，也不能当作不存在。对话窗口会显示它并给出清除按钮' ||
                   '（清除的是 role=unknown 这一行，清 worker 清不掉它）。）',
                   turns, first_seen_at, last_seen_at, last_run_id
              FROM agent_sessions_pre_role
            """
        )
    db.execute("DROP TABLE agent_sessions_pre_role")
    db.commit()


class AgentSessionStore:
    """Durable session state, on the one control-plane database."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        with closing(self._connect()) as db:
            initialize_session_schema(db)
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=60)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            # Another connection holds the write lock — two control planes
            # starting against one database, which this project supports. The
            # journal mode is a property of the *file*, so whoever wins sets it
            # and this connection inherits it; failing to start over a pragma
            # that is already someone else's job would be the wrong answer.
            pass
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # -- reading -------------------------------------------------------------

    def get(self, task_id: str, adapter: str, role: str = ROLE_WORKER) -> AgentSession:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM agent_sessions WHERE task_id = ? AND adapter = ? AND role = ?",
                (task_id, adapter, role),
            ).fetchone()
        if row is None:
            return AgentSession(
                task_id=task_id, adapter=adapter, role=role, state=SESSION_NONE,
                session_id=None, workspace=None,
            )
        return _session_of(row)

    def list_for_task(self, task_id: str) -> list[AgentSession]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM agent_sessions WHERE task_id = ? ORDER BY adapter, role",
                (task_id,),
            ).fetchall()
        return [_session_of(row) for row in rows]

    def for_run(self, run_id: str) -> RunSession | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM execution_run_sessions WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return RunSession(
            run_id=row["run_id"],
            session_id=row["session_id"],
            mode=row["mode"],
            confirmed=bool(row["confirmed"]),
            workspace=row["workspace"],
        )

    # -- deciding ------------------------------------------------------------

    def decide(
        self,
        *,
        task_id: str,
        adapter: str,
        workspace: Path | str,
        can_resume: bool,
        caller_chooses_id: bool,
        role: str = ROLE_WORKER,
    ) -> SessionDecision:
        """Whether the next turn starts a session or continues one.

        Never returns "start" for a task that already has a session id. A turn
        that cannot reach its session is refused, because the alternative is to
        answer the user in a brand-new context while the window still shows the
        conversation they think they are in.
        """
        current = self.get(task_id, adapter, role)
        seq = self._clear_seq(task_id, adapter, role)
        target = str(Path(workspace))
        if current.session_id is None and role != ROLE_UNKNOWN:
            # Any role, not just the worker's. The legacy row could belong to
            # either — that is the whole reason it is unattributable — so a review
            # turn stepping over it starts a fresh review and drops the earlier
            # review context, silently, which is the same rule broken from the
            # other side.
            orphan = self.get(task_id, adapter, ROLE_UNKNOWN)
            if orphan.session_id is not None:
                # A session recorded before roles existed, whose owner cannot be
                # established. Answering "none" here — which is what filing it
                # under ROLE_UNKNOWN did on its own — started a new conversation
                # without anyone deciding to, and the agent could then redo or
                # overwrite work the old session had already finished. Refused
                # instead: rule 3 says a new session is an explicit act, and this
                # is exactly a case where the person has to make it.
                return SessionDecision(
                    mode=MODE_RESUME, session_id=orphan.session_id, clear_seq=seq,
                    refusal=(
                        f"这个任务在 {adapter} 上有一段旧会话（{orphan.session_id}），"
                        "它记录于 role 进入主键之前，**无法判定它属于你的工作还是某次评审**——"
                        "所以既不能当作你的会话续接（可能给你评审的上下文），"
                        "也不能当作不存在（那会静默新开一段，agent 可能重做或覆盖它已完成的工作）。"
                        "请你决定：**确认丢弃它，就显式新开一个会话**（对话窗口上的按钮）。"
                        "把它认领成某一方的会话目前还做不到——那需要一个把 role 定下来的动作，"
                        "Agora 还没有提供（CR-47）。在那之前，继续这段旧上下文的唯一办法是手工改库，"
                        "所以这里说的是实话，而不是一个你按不到的选项。"
                    ),
                )
        if current.state == SESSION_PENDING:
            # An id we chose and the provider never acknowledged. Nothing was
            # established, so starting cleanly loses nothing — see SESSION_PENDING.
            return SessionDecision(
                mode=MODE_START,
                session_id=str(uuid.uuid4()) if caller_chooses_id else None,
                clear_seq=seq,
            )
        if current.session_id is None and current.state != SESSION_LOST:
            return SessionDecision(
                mode=MODE_START,
                session_id=str(uuid.uuid4()) if caller_chooses_id else None,
                clear_seq=seq,
            )
        if current.state == SESSION_LOST:
            # The one refusal that comes from the *record* rather than from a
            # condition re-derived here: a previous resume of this id ran and the
            # provider never named a session in it. Nothing this function can
            # look at would notice that again, so without this the next message
            # would be dispatched at a session already observed not to resume —
            # and the frontend, which does block on this state, would be making a
            # promise the control plane does not keep.
            # Reached with **or without** an id. A turn that named two
            # sessions can leave this state with none — the first write failed —
            # and returning "start" a few lines above meant the fail-closed was
            # bypassed by exactly the path it exists to stop: the next turn
            # silently opened a new context over work the second session wrote.
            return SessionDecision(
                mode=MODE_RESUME if current.session_id else MODE_START,
                session_id=current.session_id, clear_seq=seq,
                refusal=(
                    (current.reason or "上一次续接这个会话失败，且供应商没有报出会话 id。")
                    + " 这个判断是上一次真实观测的结果，不会自己变回来。"
                    "解决了那次失败的原因（比如重新登录）之后，点「再试一次续接」，"
                    "下一条消息就会按原来的会话 id 再试；确实要另起一段，请显式新开会话。"
                ),
            )
        if current.workspace is not None and not _same_workspace(current.workspace, target):
            return SessionDecision(
                mode=MODE_RESUME, session_id=current.session_id, clear_seq=seq,
                refusal=(
                    "这个任务的会话记录在另一个工作目录里"
                    f"（会话在 {current.workspace}，本次要用 {target}）。"
                    "三家 CLI 的会话都按工作目录分域，换了目录就读不到原会话。"
                    "要继续这段上下文，请用原来的工作目录；确实要另起一段，请显式新开会话。"
                ),
                condition_based=True,
            )
        if not can_resume:
            return SessionDecision(
                mode=MODE_RESUME, session_id=current.session_id, clear_seq=seq,
                refusal=(
                    f"适配器 {adapter} 没有配置续接命令，无法继续已记录的会话 "
                    f"{current.session_id}。这不是「没有会话」——上下文还在，"
                    "只是这个配置读不到它。"
                ),
                condition_based=True,
            )
        return SessionDecision(
            mode=MODE_RESUME, session_id=current.session_id, clear_seq=seq,
        )

    def _clear_seq(self, task_id: str, adapter: str, role: str) -> int:
        """The clear generation now, so a decision can be checked against it later."""
        with closing(self._connect()) as db:
            return _clear_seq_of(db, task_id, adapter, role)

    # -- writing -------------------------------------------------------------

    def record_launch(
        self,
        *,
        run_id: str,
        task_id: str,
        adapter: str,
        workspace: Path | str,
        session_id: str | None,
        mode: str,
        role: str = ROLE_WORKER,
        confirmed: bool = False,
        expected_clear_seq: int | None = None,
    ) -> None:
        """Bind a run to the session it is about to use.

        Written before the process starts, so a run that dies immediately still
        says which session it was for. For a caller-chosen id this also creates
        the session record itself: the id exists and was handed to the CLI, and a
        record written only on success would lose the session of exactly the run
        that needs resuming.
        """
        now = _now()
        target = str(Path(workspace))
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO execution_run_sessions
                    (run_id, session_id, mode, confirmed, workspace, recorded_at, clear_seq)
                VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, (
                    SELECT clear_seq FROM agent_sessions
                     WHERE task_id = ? AND adapter = ? AND role = ?), 0))
                ON CONFLICT(run_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    mode = excluded.mode,
                    -- Never downgraded: a run confirmed by a provider event does
                    -- not become unconfirmed because a later launch write said
                    -- so. It was hardcoded to 0 and ignored the caller's own
                    -- argument, so a resume of a known id — which *is* confirmed
                    -- at launch — read as unconfirmed for ever if the process
                    -- died before its first event.
                    confirmed = MAX(execution_run_sessions.confirmed, excluded.confirmed),
                    workspace = excluded.workspace,
                    recorded_at = excluded.recorded_at,
                    clear_seq = excluded.clear_seq
                """,
                (
                    run_id, session_id, mode, 1 if confirmed else 0, target, now,
                    # **The generation this turn was decided under**, not the one
                    # in the table right now. Reading the current value here made
                    # the compare-and-set below pointless on the commonest path:
                    # an observed-id adapter passes `session_id=None`, returns
                    # before that upsert, and a clear landing between `decide` and
                    # here was stamped onto the run as though the run had been
                    # decided after it. `_cleared_after` then compared equal
                    # generations, called the clear older than the run, and let
                    # the drain write the cleared session back as active — with
                    # the API having already told the person it was gone.
                    expected_clear_seq,
                    task_id, adapter, role,
                ),
            )
            if session_id is None:
                # An observed-id adapter starting a session: there is nothing to
                # record yet, and inventing a row here would claim a session id
                # the provider has not minted.
                return
            db.execute(
                """
                INSERT INTO agent_sessions
                    (task_id, adapter, role, session_id, workspace, state, reason, turns,
                     first_seen_at, last_seen_at, last_run_id)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, ?)
                ON CONFLICT(task_id, adapter, role) DO UPDATE SET
                    session_id = excluded.session_id,
                    workspace = excluded.workspace,
                    -- A launch is not evidence. `unreadable` and `forked` are
                    -- things a previous turn *observed*, and overwriting them
                    -- with `active` here made the badge read "session live"
                    -- again the moment a turn started — before any event of this
                    -- turn had confirmed anything. A run cancelled or timed out
                    -- before its first event then left that claim standing,
                    -- because `_settle_session` says nothing about a killed run.
                    -- `record_observed` clears these states when the provider
                    -- actually names the session, which is the evidence they
                    -- were waiting for.
                    state = CASE WHEN agent_sessions.state IN ('unreadable', 'forked')
                                 THEN agent_sessions.state ELSE excluded.state END,
                    reason = CASE WHEN agent_sessions.state IN ('unreadable', 'forked')
                                  THEN agent_sessions.reason ELSE NULL END,
                    -- A different id is a different conversation: its count
                    -- starts at one. Adding to the old row's total made a first
                    -- successful turn read as the session's second, and every
                    -- early exit before it inflated the number further.
                    turns = CASE WHEN agent_sessions.session_id = excluded.session_id
                                 THEN agent_sessions.turns + 1 ELSE 1 END,
                    last_seen_at = excluded.last_seen_at,
                    last_run_id = excluded.last_run_id
                WHERE ? IS NULL OR agent_sessions.clear_seq = ?
                """,
                (
                    task_id, adapter, role, session_id, target,
                    # A caller-chosen id on a *start* is `pending` until the
                    # provider names it: recorded as `active`, a first process
                    # that died before announcing anything left the next message
                    # resuming a session that may never have been created.
                    SESSION_ACTIVE if confirmed else SESSION_PENDING,
                    now, now, run_id,
                    # The generation this launch was decided under, twice: the
                    # upsert's WHERE is a compare-and-set. A clear that lands
                    # between `decide` and here used to be undone by this write —
                    # the API had already told the person the session was gone,
                    # and their next message resumed it anyway. Passing None
                    # keeps the unconditional behaviour for callers with no
                    # decision to check against (tests, direct recording).
                    expected_clear_seq, expected_clear_seq,
                ),
            )

    def record_observed(
        self,
        *,
        run_id: str,
        task_id: str,
        adapter: str,
        workspace: Path | str,
        session_id: str,
        role: str = ROLE_WORKER,
    ) -> None:
        """Record the session id a provider announced in this run's stream."""
        now = _now()
        target = str(Path(workspace))
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO execution_run_sessions
                    (run_id, session_id, mode, confirmed, workspace, recorded_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    confirmed = 1,
                    workspace = excluded.workspace,
                    recorded_at = excluded.recorded_at
                """,
                (run_id, session_id, MODE_START, target, now),
            )
            existing = db.execute(
                "SELECT session_id, turns, cleared_at, clear_seq FROM agent_sessions"
                " WHERE task_id = ? AND adapter = ? AND role = ?",
                (task_id, adapter, role),
            ).fetchone()
            if existing is not None and _cleared_after(db, existing, run_id):
                # The person cleared this session after the run that is reporting
                # now had already started. Writing it back is how "start a new
                # session" reported success and then continued the old
                # conversation anyway.
                return
            if existing is None:
                db.execute(
                    """
                    INSERT INTO agent_sessions
                        (task_id, adapter, role, session_id, workspace, state, reason, turns,
                         first_seen_at, last_seen_at, last_run_id)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, 1, ?, ?, ?)
                    """,
                    (task_id, adapter, role, session_id, target, SESSION_ACTIVE, now, now, run_id),
                )
                return
            # A resumed turn re-announces the same id; that is confirmation, not a
            # new session, and must not bump the turn count twice (record_launch
            # already counted this turn for a known id).
            already_counted = existing["session_id"] == session_id
            db.execute(
                """
                UPDATE agent_sessions
                   SET session_id = ?, workspace = ?, state = ?, reason = NULL,
                       turns = turns + ?, last_seen_at = ?, last_run_id = ?
                 WHERE task_id = ? AND adapter = ? AND role = ?
                """,
                (
                    session_id, target, SESSION_ACTIVE, 0 if already_counted else 1,
                    now, run_id, task_id, adapter, role,
                ),
            )

    def mark_unreadable(
        self, *, task_id: str, adapter: str, reason: str, role: str = ROLE_WORKER,
    ) -> None:
        """Flag a session a live condition stops this configuration from continuing.

        For the window's benefit only: ``decide`` re-derives the conditions
        (workspace, resume support) on every turn, so this state never decides
        anything and never traps — put the worktree back and the next message
        continues the conversation, without anyone having to clear a flag.

        The id is **kept**. It is the only handle on a conversation that probably
        still exists on disk, and deleting it here would turn a reachability
        problem into permanent data loss — and make the next turn look like a
        clean first turn.
        """
        self._mark(task_id, adapter, role, SESSION_UNREADABLE, reason)

    def mark_forked(
        self,
        *,
        task_id: str,
        adapter: str,
        observed: str,
        previous: str,
        role: str = ROLE_WORKER,
        run_id: str | None = None,
    ) -> None:
        """Record that a resume landed in a different session than it asked for.

        The observed id becomes the current one — it is where the work actually
        happened, and a later turn resuming the old id would be resuming the
        conversation the provider already declined to continue. The old id is
        kept in ``previous_session_id`` so it stays nameable, and the state says a
        fork happened so the window stops calling this the same conversation.
        """
        now = _now()
        with self._transaction() as db:
            existing = db.execute(
                "SELECT session_id, turns, cleared_at, clear_seq FROM agent_sessions"
                " WHERE task_id = ? AND adapter = ? AND role = ?",
                (task_id, adapter, role),
            ).fetchone()
            if (
                existing is not None
                and run_id is not None
                and _cleared_after(db, existing, run_id)
            ):
                # The person cleared this session after this run was bound to it.
                # `record_observed` has honoured that since the generation counter
                # existed; this path did not, and it writes a session id — so a
                # fork event arriving from a draining process put the abandoned
                # conversation back, and the next turn resumed what the person had
                # explicitly dropped.
                return
            db.execute(
                """
                UPDATE agent_sessions
                   SET session_id = ?, previous_session_id = ?, state = ?,
                       reason = ?, last_seen_at = ?,
                       -- A fork **is** a different conversation, so its count
                       -- starts at one. Carrying the old session's total over
                       -- showed a two-turn session as its seventh turn.
                       turns = 1
                 WHERE task_id = ? AND adapter = ? AND role = ?
                """,
                (
                    observed, previous, SESSION_FORKED,
                    (
                        "续接时供应商报出的会话 id 与请求的不是同一个"
                        f"（请求 {previous}，实际 {observed}）。"
                        "这一轮的工作在新会话里，之前那段上下文没有被带过来。"
                    ),
                    now, task_id, adapter, role,
                ),
            )
            if run_id is not None:
                # The run's own row, too. Left at the requested id with
                # `confirmed = 0`, the audit record said this turn used a session
                # it did not use — while the task-level record said otherwise.
                db.execute(
                    """
                    UPDATE execution_run_sessions
                       SET session_id = ?, confirmed = 1, recorded_at = ?
                     WHERE run_id = ?
                    """,
                    (observed, now, run_id),
                )

    def mark_unidentified(
        self,
        *,
        task_id: str,
        adapter: str,
        workspace: Path | str,
        reason: str,
        role: str = ROLE_WORKER,
    ) -> None:
        """Record that a turn ran and its session could not be named.

        Written when no id was read from a turn's output at all. Without it the
        table stayed empty and the **next** message read as a clean first turn —
        the window showing a fresh conversation while a session it could not see
        may well have existed. That is the silence adr/0007 forbids, reachable
        through nothing more exotic than a CLI changing its event shape.

        Never overwrites a session that *is* on record: an id already known is
        the more specific truth, and this state carries none.

        **An unconfirmed ``pending`` id is not a session on record.** A
        caller-chosen id is written before the process starts, so a claude turn
        that ran, wrote files and never named its session left a ``pending`` row
        holding a non-null id — which this method then declined to touch. The
        next turn read ``pending``, was told "nothing was established, so nothing
        is lost", and minted a fresh UUID: a new conversation over a turn whose
        work was real, with the agent free to redo or overwrite it. ``pending``
        is therefore replaceable here, and only ``pending``.
        """
        now = _now()
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO agent_sessions
                    (task_id, adapter, role, session_id, workspace, state, reason, turns,
                     first_seen_at, last_seen_at, last_run_id)
                VALUES (?, ?, ?, NULL, ?, ?, ?, 0, ?, ?, NULL)
                ON CONFLICT(task_id, adapter, role) DO UPDATE SET
                    state = CASE WHEN agent_sessions.session_id IS NULL
                                   OR agent_sessions.state = 'pending'
                                 THEN excluded.state ELSE agent_sessions.state END,
                    reason = CASE WHEN agent_sessions.session_id IS NULL
                                    OR agent_sessions.state = 'pending'
                                  THEN excluded.reason ELSE agent_sessions.reason END,
                    -- The unconfirmed id goes with the state it belonged to.
                    -- Leaving it behind under `unidentified` would name a session
                    -- as this task's while saying none could be named.
                    session_id = CASE WHEN agent_sessions.state = 'pending'
                                      THEN NULL ELSE agent_sessions.session_id END,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    task_id, adapter, role, str(Path(workspace)),
                    SESSION_UNIDENTIFIED, reason, now, now,
                ),
            )

    def mark_lost(
        self, *, task_id: str, adapter: str, reason: str, role: str = ROLE_WORKER,
    ) -> None:
        """Record that a resumed turn ran and the provider never named a session.

        Unlike :meth:`mark_unreadable` this one **is** load-bearing: ``decide``
        refuses on it, because no condition it can re-derive would notice this
        again. Only :meth:`forget` clears it, which is the person deciding to
        start over rather than the dispatcher deciding for them.
        """
        self._mark(task_id, adapter, role, SESSION_LOST, reason)

    def _mark(
        self, task_id: str, adapter: str, role: str, state: str, reason: str
    ) -> None:
        """Record a state for this conversation, **creating the row if needed**.

        Update-only, this silently did nothing when no row existed yet — and the
        one caller that must never silently do nothing is `mark_lost`, the
        fail-closed for a turn that named two sessions. A transient write error
        on the first session event was enough to leave the table empty, and then
        the refusal that should have stopped the next turn was not recorded at
        all: the conversation read as "no session", a new one was opened, and the
        agent was free to redo or overwrite what the second session had written.
        """
        now = _now()
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO agent_sessions
                    (task_id, adapter, role, session_id, workspace, state, reason,
                     turns, first_seen_at, last_seen_at, last_run_id)
                VALUES (?, ?, ?, NULL, '', ?, ?, 0, ?, ?, NULL)
                ON CONFLICT(task_id, adapter, role) DO UPDATE SET
                    state = excluded.state,
                    reason = excluded.reason,
                    last_seen_at = excluded.last_seen_at
                """,
                (task_id, adapter, role, state, reason, now, now),
            )

    def retry(self, *, task_id: str, adapter: str, role: str = ROLE_WORKER) -> bool:
        """Let a session recorded as ``lost`` be attempted once more.

        The refusal it lifts is a real observation — a resume that ran and never
        named a session — and nothing this control plane can re-derive would ever
        notice it had stopped being true. So the person says so: they fixed the
        cause (logged in again, restored the CLI) and want the recorded id tried
        rather than thrown away.

        Without this the message on that state ("solve the cause and continue")
        described an action nobody could take: the only button was the one that
        drops the context. It moves to ``unreadable``, which ``decide`` re-derives
        every turn — so the next message resumes, and if it fails the same way the
        turn's own settlement puts it back to ``lost``.

        Returns whether anything changed, so a caller can tell "retrying" from
        "there was nothing in that state".
        """
        with self._transaction() as db:
            cursor = db.execute(
                """
                UPDATE agent_sessions
                   SET state = ?, reason = ?, last_seen_at = ?
                 WHERE task_id = ? AND adapter = ? AND role = ?
                   AND state = ? AND session_id IS NOT NULL
                """,
                (
                    SESSION_UNREADABLE,
                    "你要求再试一次续接：下一条消息会按原来的会话 id 再试。"
                    "如果它又没能接上，这里会重新变回「续接失败」。",
                    _now(), task_id, adapter, role, SESSION_LOST,
                ),
            )
            return cursor.rowcount > 0

    def mark_unsupported(
        self, *, task_id: str, adapter: str, workspace: Path | str, reason: str,
        role: str = ROLE_WORKER,
    ) -> None:
        """Record that this task's runs are not carrying a session at all.

        Written for an execution mode with no verified resume path, so the window
        can say "这条链路不续接会话" instead of showing a conversation whose turns
        each start from nothing.

        The **id** is never overwritten — it is the handle on a conversation that
        another configuration may still reach. The **state** is, because it
        describes this link rather than the conversation: leaving it `active`
        because an id existed is exactly how switching to the app-server bridge
        went on claiming continuity it no longer had.
        """
        now = _now()
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO agent_sessions
                    (task_id, adapter, role, session_id, workspace, state, reason, turns,
                     first_seen_at, last_seen_at, last_run_id)
                VALUES (?, ?, ?, NULL, ?, ?, ?, 0, ?, ?, NULL)
                ON CONFLICT(task_id, adapter, role) DO UPDATE SET
                    -- The state is about **this configuration's** ability to
                    -- continue, so it is written whether or not an id is on
                    -- record. Keeping the old `active` state when an id existed
                    -- was wrong in the case that matters: switch a task's codex
                    -- adapter to the app-server bridge and the window went on
                    -- reading "session live" while every turn ran in a fresh
                    -- context. The id itself is untouched — it is the handle on
                    -- a conversation that may still be reachable from another
                    -- configuration, which is why it is kept.
                    state = excluded.state,
                    reason = excluded.reason,
                    last_seen_at = excluded.last_seen_at
                """,
                (task_id, adapter, role, str(Path(workspace)), SESSION_UNSUPPORTED, reason, now, now),
            )

    def forget(
        self,
        *,
        task_id: str,
        adapter: str,
        role: str = ROLE_WORKER,
        blocking_run_states: "Sequence[str] | None" = None,
    ) -> bool:
        """Drop the session record so the next turn starts a new conversation.

        ``blocking_run_states`` makes the clear **atomic against a turn starting**.
        The caller checked for live runs before calling; that check and this write
        were two statements, so a run created in between was already holding the
        old session id by the time the tombstone landed — the response said
        "none" and that turn went on to continue the abandoned conversation and
        write files in it. A run row exists (queued) before its process starts, so
        testing for one inside this transaction closes that window rather than
        narrowing it. Raises :class:`SessionClearRefused` when one is found.

        Where the run table is not in this database — a store constructed with a
        separate path — there is nothing to test here and the caller's own check
        stands alone. That is stated rather than silently assumed.

        The explicit act rule 3 requires. Returns whether anything was dropped,
        so a caller can tell "cleared" from "there was nothing to clear" instead
        of reporting success either way.

        **A tombstone, not a delete.** A run that was already reading its process
        can report its session id moments later, and with the row simply gone
        ``record_observed`` re-created the very conversation the person had just
        asked to leave — the clear reported success and silently did not happen.
        The row stays with a null id and a ``cleared_at``, and any observation
        from a run that started before that instant is ignored.
        """
        now = _now()
        with self._transaction() as db:
            if blocking_run_states:
                _refuse_if_run_active(db, task_id, adapter, blocking_run_states)
            cursor = db.execute(
                """
                UPDATE agent_sessions
                   SET session_id = NULL, previous_session_id = session_id,
                       state = ?, reason = NULL, turns = 0, last_run_id = NULL,
                       last_seen_at = ?, cleared_at = ?, clear_seq = clear_seq + 1
                 WHERE task_id = ? AND adapter = ? AND role = ?
                   AND session_id IS NOT NULL
                """,
                (SESSION_NONE, now, now, task_id, adapter, role),
            )
            if cursor.rowcount == 0:
                # Nothing to clear, but the instant still has to be on record:
                # a run already draining could otherwise write a session into a
                # task that has none, moments after the person asked for a fresh
                # start.
                db.execute(
                    """
                    INSERT INTO agent_sessions
                        (task_id, adapter, role, session_id, workspace, state, reason,
                         turns, first_seen_at, last_seen_at, last_run_id, cleared_at,
                         clear_seq)
                    VALUES (?, ?, ?, NULL, '', ?, NULL, 0, ?, ?, NULL, ?, 1)
                    ON CONFLICT(task_id, adapter, role) DO UPDATE SET
                        cleared_at = excluded.cleared_at,
                        clear_seq = agent_sessions.clear_seq + 1,
                        last_seen_at = excluded.last_seen_at,
                        -- The row's *state* is reset here too. A row can carry a
                        -- state with no id — `unsupported` and `unidentified`
                        -- both do — and bumping only the counter left the badge
                        -- saying "this link does not continue sessions" while the
                        -- button it offered did nothing, however many times it
                        -- was pressed.
                        state = excluded.state,
                        reason = NULL,
                        turns = 0,
                        last_run_id = NULL
                    """,
                    (task_id, adapter, role, SESSION_NONE, now, now, now),
                )
            return cursor.rowcount > 0


def _same_workspace(recorded: str, target: str) -> bool:
    """Whether two workspace paths name the same directory.

    Compared with ``normcase`` rather than as plain strings, because this
    comparison **refuses the user's turn** when it fails. On Windows ``D:\\wt`` and
    ``d:\\wt`` are one directory, and a refusal saying "your session is in another
    working directory" while naming two paths that differ only in a drive letter's
    case would be an unfixable dead end.
    """
    return os.path.normcase(recorded) == os.path.normcase(target)


def _clear_seq_of(db: sqlite3.Connection, task_id: str, adapter: str, role: str) -> int:
    row = db.execute(
        "SELECT clear_seq FROM agent_sessions WHERE task_id = ? AND adapter = ? AND role = ?",
        (task_id, adapter, role),
    ).fetchone()
    return int(_column(row, "clear_seq") or 0) if row is not None else 0


class SessionClearRefused(Exception):
    """A session clear was refused because a run of that adapter is live.

    Raised from inside the clearing transaction, so the refusal and the state it
    was decided on are the same instant.
    """


def _refuse_if_run_active(
    db: sqlite3.Connection, task_id: str, adapter: str, states: "Sequence[str]"
) -> None:
    """Refuse the clear if a run of this adapter is queued or running.

    Reads ``execution_runs`` directly, in the caller's transaction. The state
    vocabulary is passed in rather than known here: which states count as "still
    going" belongs to the execution models, and duplicating the list is how the
    two answers drift apart.
    """
    table = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'execution_runs'"
    ).fetchone()
    if table is None:
        # A store on its own database. Nothing to test, and refusing every clear
        # because of it would be worse than relying on the caller's own check.
        return
    placeholders = ", ".join("?" for _ in states)
    row = db.execute(
        f"""
        SELECT run_id FROM execution_runs
         WHERE task_id = ? AND adapter = ? AND state IN ({placeholders})
         LIMIT 1
        """,
        (task_id, adapter, *states),
    ).fetchone()
    if row is not None:
        raise SessionClearRefused(str(row["run_id"]))


def _cleared_after(db: sqlite3.Connection, existing: sqlite3.Row, run_id: str) -> bool:
    """Whether this session was cleared after ``run_id`` was bound to it.

    Compared by **generation**, not by timestamp. The first version of this read
    two ISO strings at second granularity — and a clear racing a still-draining
    process is precisely an event that happens inside one second, so the
    comparison said "not cleared" and the drain wrote the old session back. A
    counter that only ``forget`` advances orders the two exactly, whatever the
    clock does.

    A run with no launch record cannot be placed at all, and the clear was an
    explicit act, so the clear stands.
    """
    current = _column(existing, "clear_seq") or 0
    row = db.execute(
        "SELECT clear_seq FROM execution_run_sessions WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return current > 0
    return int(_column(row, "clear_seq") or 0) < int(current)


def _session_of(row: sqlite3.Row) -> AgentSession:
    return AgentSession(
        task_id=row["task_id"],
        adapter=row["adapter"],
        role=row["role"],
        state=row["state"],
        session_id=row["session_id"],
        workspace=row["workspace"],
        reason=row["reason"],
        previous_session_id=_column(row, "previous_session_id"),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        last_run_id=row["last_run_id"],
        turns=int(row["turns"] or 0),
    )


def _column(row: sqlite3.Row, name: str):
    """One column, or None when this database predates it."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
