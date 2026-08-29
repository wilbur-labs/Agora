"""Settle the run-scoped Attention that a finished run can no longer answer.

A vendor bridge raises Attention *from inside a live run*: Codex's app-server
asks for permission to execute a command or write a file and blocks its turn
until the answer is delivered back over the same connection (see
``bridges/codex_protocol.py``), and the Claude/Kiro hook ingress captures the
equivalent notification. Those items are answerable only while that process
exists. Once the run is over the prompt is gone with it, and an item left ``open``
is a gate nobody can clear — which, because
``ExecutionStore._reject_open_gate`` refuses a worker run while an ``approval``
or ``blocker`` is open, locks every future worker run on that task until a human
finds the orphan and cancels it by hand. The Codex bridge raises ``approval``,
so that is the *default* runtime path, not an edge case.

The cut is by **origin, not by run state.** The auto cross-review's approval is
raised by ``ExecutionDispatcher._raise_review_attention`` *after* its review run
is already terminal, and it is scoped (``run_id``) to the worker run it reviewed,
which is terminal too. A rule of the form "the run ended, so cancel its open
items" would therefore destroy the review gate — the decision this whole slice
exists to create. So only items carrying the explicit bridge marker are settled
here:

- a row in ``attention_bridge_events`` for this ``run_id``. That row exists if
  and only if the item came from ``AttentionStore.create_bridge_event``, i.e. a
  vendor bridge raised it during a run.
- ``requester != REVIEW_REQUESTER``, checked independently. The review gate is
  created through ``AttentionStore.create`` and has no bridge row, so this is a
  second, redundant guard rather than the load-bearing one — redundant on
  purpose, because the cost of getting this wrong is losing a human decision.

A review gate outlives its run; a permission prompt does not. That asymmetry is
the whole content of this module.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from .models import AttentionState, REVIEW_REQUESTER


#: Recorded as the item's ``cancellation_reason`` and as the bridge delivery
#: error. It says *why* it can no longer be answered rather than only that it was
#: cancelled: a human reading the timeline needs to know the prompt died with its
#: process, not that somebody dismissed it.
ORPHANED_PROMPT_REASON = (
    "the run that raised this prompt ended before it was answered"
)


def _has_tables(db: sqlite3.Connection) -> bool:
    """Whether this database holds the Attention bridge tables at all.

    ``ExecutionStore`` initializes only the execution schema, so a deployment or
    test that never constructs an ``AttentionStore`` has no Attention items and
    nothing to settle. That is a fact about the schema, not an unreadable state.
    """
    rows = db.execute(
        """SELECT name FROM sqlite_master WHERE type = 'table'
           AND name IN ('attention_items', 'attention_bridge_events')"""
    ).fetchall()
    return len({row["name"] for row in rows}) == 2


def settle_bridge_attention_for_run(
    db: sqlite3.Connection,
    *,
    run_id: str,
    now: str,
    record_event: Callable[[str, dict], None],
) -> list[str]:
    """Cancel this run's still-open bridge prompts. Returns the item ids settled.

    Runs inside the caller's transaction — the one that settles the run — so a
    crash cannot leave the run terminal and its prompt open. ``record_event``
    appends to the task timeline; the caller owns it because the event writer is
    ``TaskStore``'s, and passing it keeps this module free of any execution or
    task-store import.
    """
    if not _has_tables(db):
        return []
    rows = db.execute(
        """SELECT a.item_id, a.version
           FROM attention_items a
           JOIN attention_bridge_events b ON b.item_id = a.item_id
           WHERE b.run_id = ? AND a.state = ? AND a.requester <> ?
           ORDER BY a.created_at, a.rowid""",
        (run_id, AttentionState.OPEN.value, REVIEW_REQUESTER),
    ).fetchall()
    settled: list[str] = []
    for row in rows:
        cursor = db.execute(
            """UPDATE attention_items
               SET state = ?, cancellation_reason = ?, updated_at = ?, version = version + 1
               WHERE item_id = ? AND state = ? AND version = ?""",
            (
                AttentionState.CANCELLED.value,
                ORPHANED_PROMPT_REASON,
                now,
                row["item_id"],
                AttentionState.OPEN.value,
                row["version"],
            ),
        )
        if not cursor.rowcount:
            continue  # settled underneath us; the other writer owns its own event
        settled.append(row["item_id"])
        record_event(
            "attention.cancelled",
            {"item_id": row["item_id"], "reason": ORPHANED_PROMPT_REASON, "run_id": run_id},
        )
        # A bidirectional answer can no longer be delivered to a process that is
        # gone, so the delivery record must say failed rather than sit pending
        # forever. Mirrors AttentionStore._fail_undelivered_bridge.
        delivery = db.execute(
            """UPDATE attention_bridge_events
               SET delivery_state = 'failed', delivery_error = ?
               WHERE item_id = ? AND delivery_mode = 'bidirectional'
                 AND delivery_state IN ('pending', 'ready')""",
            (ORPHANED_PROMPT_REASON, row["item_id"]),
        )
        if delivery.rowcount:
            record_event(
                "attention.delivery_failed",
                {"item_id": row["item_id"], "error": ORPHANED_PROMPT_REASON},
            )
    return settled
