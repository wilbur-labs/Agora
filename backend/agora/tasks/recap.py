"""What happened on a task's last active day — the "you left off here" recap.

The point of opening a task should be answering "where was I", not reading the
whole event log. This derives that from what is recorded: it finds the most
recent day anything happened and reports what happened, so a task always answers
the question on first open, with no per-viewer watermark to keep.

Pure, like ``progress.py`` and ``next_action.py``: signals only, no I/O and no
store access, so the same inputs always yield the same recap. It emits stable
slugs the UI localizes rather than prose, and never calls a model — "where did I
leave off" is a question about recorded facts, and generating it would make the
answer plausible instead of true.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Iterable

from .localday import local_day_of

# Enough to say what a day amounted to without turning back into a log. A busier
# day reports the overflow rather than growing without bound.
MAX_ITEMS = 6

# Every recap item kind, with an English fallback for API consumers without the
# UI. Keep this the single source of truth (same contract as next_action.ACTIONS).
ITEM_KINDS: dict[str, str] = {
    "created": "The task was created.",
    "delivered": "A run delivered work.",
    "ran_no_changes": "A run finished without changing any file.",
    "run_failed": "A run did not finish.",
    "reviewed": "A review ran against the work.",
    "spec_approved": "The requirement spec was approved.",
    "spec_rejected": "The requirement spec was rejected.",
    "stage_reached": "The task reached a new stage.",
    "needs_you": "Something was raised for you to answer.",
    "answered": "You answered what was raised.",
    "handed_off": "The work was handed off.",
    "noted": "A note was added.",
}

_ROLE_REVIEW = "review"

# Event types that produce an item.
_ITEM_EVENTS = {
    "task_created", "spec.approved", "spec.rejected", "state_changed",
    "attention.created", "attention.responded", "handoff.completed", "user_message",
}

# Event types that mark a day as active but carry no item of their own — a run's
# outcome is reported from the run record, which knows whether it delivered.
_ACTIVITY_ONLY = {
    "run.queued", "run.process_started", "run.settled",
    "run.cancelled_output", "run.context_sealed",
}

# Only these may decide which day is "the last active day". Everything else
# (archived / unarchived / spec.updated / attention.expired …) is bookkeeping, not
# work: letting one of them pick the day would return an empty recap for that day
# and hide the last day that actually had something to show.
DAY_ELIGIBLE_EVENTS = _ITEM_EVENTS | _ACTIVITY_ONLY


# How a stored UTC timestamp becomes a calendar day. Injectable so tests can pin
# a zone instead of inheriting the machine's, and so the derivation stays a
# function of its inputs.
DayOf = Callable[[str | None], str | None]


def _item(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "summary": ITEM_KINDS[kind], **fields}


def last_active_day(
    events: Iterable[Any],
    runs: Iterable[dict[str, Any]],
    *,
    today: str | None = None,
    day_of: DayOf = local_day_of,
) -> dict[str, Any]:
    """The most recent day this task moved, and what moved on it.

    ``events`` are TaskEvent-shaped (``event_type``, ``created_at``, ``payload``).
    ``runs`` are dicts of ``adapter`` / ``state`` / ``finished_at`` / ``role`` /
    ``delivered_count``, assembled by the caller, which owns the I/O.

    ``delivered_count`` is None when a run has no deliverable snapshot: the count
    is then unknown, and the recap says "delivered work" without inventing a
    number. ``today`` (a YYYY-MM-DD string) only fills in ``days_ago``; leave it
    out and that field is None.
    """
    event_list = list(events)
    run_list = list(runs)

    days = {
        day_of(getattr(event, "created_at", None))
        for event in event_list
        if getattr(event, "event_type", "") in DAY_ELIGIBLE_EVENTS
    }
    days |= {day_of(run.get("finished_at")) for run in run_list}
    days.discard(None)
    if not days:
        return {"day": None, "items": [], "more": 0, "days_ago": None}
    day = max(days)  # ISO dates sort chronologically as strings

    items: list[dict[str, Any]] = []

    # Runs first, ordered as they finished: they are the substance of a day.
    same_day_runs = [run for run in run_list if day_of(run.get("finished_at")) == day]
    same_day_runs.sort(key=lambda run: run.get("finished_at") or "")
    for run in same_day_runs:
        adapter = run.get("adapter")
        # Which run produced this item. Carried on every run-derived kind, because
        # `adapter` alone does not identify one: a worker and its automatic review
        # finish on the same day by design, and two runs of the same adapter with the
        # same outcome were indistinguishable in the payload. A consumer wanting to
        # link an item to its run — or a test wanting to assert *which* run an item
        # describes — had to cross-check adapter + kind + file count and hope the day
        # held nothing similar. `None` when the caller did not supply one, so an
        # older assembler keeps working and the field is never invented.
        run_id = run.get("run_id")
        if run.get("state") == "succeeded":
            if str(run.get("role") or "") == _ROLE_REVIEW:
                items.append(_item("reviewed", adapter=adapter, run_id=run_id))
                continue
            count = run.get("delivered_count")
            if count == 0:
                # A verified-empty snapshot is knowledge: this run changed nothing.
                # Calling that "delivered work" would describe a known no-op as a
                # delivery. None is different — the count is unknown, not zero.
                items.append(_item("ran_no_changes", adapter=adapter, run_id=run_id))
            else:
                items.append(_item(
                    "delivered",
                    adapter=adapter,
                    run_id=run_id,
                    files=count,
                    # The snapshot stopped at MAX_FILES, so the count is a floor.
                    # Presenting a floor as exact would overstate what is known.
                    files_at_least=bool(run.get("delivered_truncated")),
                ))
        elif run.get("state") in {"failed", "timed_out", "abandoned"}:
            items.append(_item("run_failed", adapter=adapter, run_id=run_id))
        # queued/running/cancelled runs are not an outcome; they only mark activity.

    # Then the decisions recorded that day.
    same_day_events = [
        event for event in event_list if day_of(getattr(event, "created_at", None)) == day
    ]
    same_day_events.sort(key=lambda event: getattr(event, "created_at", "") or "")
    attention_raised = 0
    answered = 0
    notes = 0
    handed_off = False
    created = False
    highest_stage: str | None = None
    for event in same_day_events:
        kind = getattr(event, "event_type", "")
        payload = getattr(event, "payload", None) or {}
        if kind == "task_created":
            created = True
        elif kind == "spec.approved":
            items.append(_item("spec_approved"))
        elif kind == "spec.rejected":
            items.append(_item("spec_rejected"))
        elif kind == "state_changed":
            # One item for the day, naming where the task ended up — not each hop.
            highest_stage = str(payload.get("to") or "") or highest_stage
        elif kind == "attention.created":
            attention_raised += 1
        elif kind == "attention.responded":
            answered += 1
        elif kind == "handoff.completed":
            handed_off = True
        elif kind == "user_message":
            notes += 1

    if created:
        items.insert(0, _item("created"))
    if highest_stage:
        items.append(_item("stage_reached", state=highest_stage))
    if handed_off:
        items.append(_item("handed_off"))
    if attention_raised:
        items.append(_item("needs_you", count=attention_raised))
    if answered:
        items.append(_item("answered", count=answered))
    if notes:
        items.append(_item("noted", count=notes))

    more = max(0, len(items) - MAX_ITEMS)
    return {
        "day": day,
        "items": items[:MAX_ITEMS],
        "more": more,
        "days_ago": _days_between(day, today),
    }


def _as_date(value: str | None) -> date | None:
    """A plain YYYY-MM-DD string as a date, or None. Both sides of the day
    arithmetic are already calendar days, so this parses rather than converts."""
    if not value or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _days_between(day: str, today: str | None) -> int | None:
    """Whole days from ``day`` to ``today``, or None if today was not supplied or
    either value is not a date. Negative values are clamped to 0: a clock skew
    should not make a recap claim the future."""
    parsed_day = _as_date(day)
    parsed_today = _as_date(today)
    if parsed_day is None or parsed_today is None:
        return None
    return max(0, (parsed_today - parsed_day).days)
