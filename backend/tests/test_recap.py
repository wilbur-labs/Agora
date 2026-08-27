"""The "where did I leave off" recap: the last day a task moved, and what moved.

`recap.last_active_day` is pure, so these pin the derivation exactly. The point
of the feature is that opening a task answers the question from recorded facts —
so the tests care most about what it refuses to claim: no day when nothing
happened, no file count when the count is unknown, no future when clocks skew.
"""
from __future__ import annotations

from types import SimpleNamespace

from agora.tasks.recap import ITEM_KINDS, MAX_ITEMS, last_active_day


def _event(event_type: str, created_at: str, **payload):
    return SimpleNamespace(event_type=event_type, created_at=created_at, payload=payload)


def _run(state: str, finished_at: str | None, *, adapter="codex", role="worker",
         delivered_count=None, delivered_truncated=False):
    return {
        "adapter": adapter, "state": state, "finished_at": finished_at,
        "role": role, "delivered_count": delivered_count,
        "delivered_truncated": delivered_truncated,
    }


def _kinds(recap):
    return [item["kind"] for item in recap["items"]]


def test_nothing_recorded_yields_no_day():
    recap = last_active_day([], [])
    assert recap == {"day": None, "items": [], "more": 0, "days_ago": None}


def test_picks_the_most_recent_day_and_ignores_earlier_ones():
    events = [
        _event("spec.approved", "2026-08-20T09:00:00+00:00"),
        _event("state_changed", "2026-08-25T11:00:00+00:00", to="review"),
    ]
    runs = [
        _run("succeeded", "2026-08-20T10:00:00+00:00", delivered_count=9),
        _run("succeeded", "2026-08-25T10:30:00+00:00", delivered_count=3),
    ]
    recap = last_active_day(events, runs, today="2026-08-27")
    assert recap["day"] == "2026-08-25"
    assert recap["days_ago"] == 2
    # Only that day's items — the 2026-08-20 spec approval and run are gone.
    assert _kinds(recap) == ["delivered", "stage_reached"]
    assert recap["items"][0]["files"] == 3
    assert recap["items"][1]["state"] == "review"


def test_unknown_file_count_stays_unknown():
    """A run with no deliverable snapshot has an unknown count. The recap says
    it delivered work without inventing a number (see D-1)."""
    recap = last_active_day([], [_run("succeeded", "2026-08-27T10:00:00+00:00")],
                            today="2026-08-27")
    assert _kinds(recap) == ["delivered"]
    assert recap["items"][0]["files"] is None
    assert recap["days_ago"] == 0


def test_review_runs_are_reported_as_review_not_delivery():
    runs = [
        _run("succeeded", "2026-08-27T10:00:00+00:00", delivered_count=2),
        _run("succeeded", "2026-08-27T10:40:00+00:00", adapter="claude",
             role="review", delivered_count=0),
    ]
    recap = last_active_day([], runs, today="2026-08-27")
    assert _kinds(recap) == ["delivered", "reviewed"]
    assert recap["items"][1]["adapter"] == "claude"


def test_failed_runs_are_reported_and_in_flight_ones_are_not():
    runs = [
        _run("timed_out", "2026-08-27T09:00:00+00:00"),
        _run("running", None),
        _run("queued", None),
        _run("cancelled", "2026-08-27T09:30:00+00:00"),
    ]
    recap = last_active_day([], runs, today="2026-08-27")
    # A timeout is an outcome; queued/running/cancelled are not.
    assert _kinds(recap) == ["run_failed"]


def test_many_stage_hops_collapse_to_where_the_task_ended_up():
    events = [
        _event("state_changed", "2026-08-27T09:00:00+00:00", to="planned"),
        _event("state_changed", "2026-08-27T10:00:00+00:00", to="running"),
        _event("state_changed", "2026-08-27T11:00:00+00:00", to="review"),
    ]
    recap = last_active_day(events, [], today="2026-08-27")
    assert _kinds(recap) == ["stage_reached"]
    assert recap["items"][0]["state"] == "review"


def test_attention_and_notes_are_counted_not_listed():
    events = [
        _event("attention.created", "2026-08-27T09:00:00+00:00"),
        _event("attention.created", "2026-08-27T09:05:00+00:00"),
        _event("user_message", "2026-08-27T09:10:00+00:00"),
    ]
    recap = last_active_day(events, [], today="2026-08-27")
    assert _kinds(recap) == ["needs_you", "noted"]
    assert recap["items"][0]["count"] == 2
    assert recap["items"][1]["count"] == 1


def test_a_busy_day_reports_the_overflow_instead_of_growing():
    runs = [
        _run("succeeded", f"2026-08-27T{hour:02d}:00:00+00:00", delivered_count=1)
        for hour in range(MAX_ITEMS + 4)
    ]
    recap = last_active_day([], runs, today="2026-08-27")
    assert len(recap["items"]) == MAX_ITEMS
    assert recap["more"] == 4


def test_clock_skew_never_reports_a_future_day():
    recap = last_active_day([], [_run("succeeded", "2026-08-27T10:00:00+00:00")],
                            today="2026-08-20")
    assert recap["day"] == "2026-08-27"
    assert recap["days_ago"] == 0  # clamped, not negative


def test_malformed_timestamps_do_not_break_the_recap():
    events = [
        _event("spec.approved", "not-a-date"),
        _event("spec.approved", ""),
        _event("spec.approved", "2026-08-27T09:00:00+00:00"),
    ]
    runs = [_run("succeeded", "garbage"), _run("succeeded", None)]
    recap = last_active_day(events, runs, today="2026-08-27")
    assert recap["day"] == "2026-08-27"
    assert _kinds(recap) == ["spec_approved"]


def test_days_ago_is_none_without_a_today():
    recap = last_active_day([], [_run("succeeded", "2026-08-27T10:00:00+00:00")])
    assert recap["day"] == "2026-08-27"
    assert recap["days_ago"] is None


def test_every_item_kind_carries_a_summary():
    events = [
        _event("spec.rejected", "2026-08-27T09:00:00+00:00"),
        _event("state_changed", "2026-08-27T09:30:00+00:00", to="blocked"),
        _event("attention.created", "2026-08-27T09:40:00+00:00"),
        _event("user_message", "2026-08-27T09:50:00+00:00"),
    ]
    runs = [
        _run("succeeded", "2026-08-27T08:00:00+00:00", delivered_count=1),
        _run("failed", "2026-08-27T08:30:00+00:00"),
    ]
    recap = last_active_day(events, runs, today="2026-08-27")
    for item in recap["items"]:
        assert item["kind"] in ITEM_KINDS
        assert item["summary"] == ITEM_KINDS[item["kind"]]


# ── what the recap refuses to claim (codex review round 1) ─────────────

def test_bookkeeping_events_do_not_steal_the_last_active_day():
    """`archived` on a later day used to pick that day and then produce nothing,
    hiding the last day that actually had something to show."""
    events = [
        _event("spec.approved", "2026-08-25T09:00:00+00:00"),
        _event("archived", "2026-08-26T09:00:00+00:00"),
        _event("unarchived", "2026-08-27T09:00:00+00:00"),
        _event("spec.updated", "2026-08-27T10:00:00+00:00"),
        _event("attention.expired", "2026-08-27T11:00:00+00:00"),
    ]
    recap = last_active_day(events, [], today="2026-08-27")
    assert recap["day"] == "2026-08-25"
    assert _kinds(recap) == ["spec_approved"]


def test_a_verified_zero_change_run_is_not_called_a_delivery():
    """An empty snapshot means "this run changed nothing" — knowledge, not a
    missing count. Reporting it as delivered would describe a known no-op as
    delivery."""
    recap = last_active_day(
        [], [_run("succeeded", "2026-08-27T10:00:00+00:00", delivered_count=0)],
        today="2026-08-27",
    )
    assert _kinds(recap) == ["ran_no_changes"]


def test_a_truncated_snapshot_count_is_reported_as_a_floor():
    recap = last_active_day(
        [], [_run("succeeded", "2026-08-27T10:00:00+00:00",
                  delivered_count=200, delivered_truncated=True)],
        today="2026-08-27",
    )
    item = recap["items"][0]
    assert item["kind"] == "delivered"
    assert item["files"] == 200 and item["files_at_least"] is True


def test_an_exact_count_is_not_marked_as_a_floor():
    recap = last_active_day(
        [], [_run("succeeded", "2026-08-27T10:00:00+00:00", delivered_count=3)],
        today="2026-08-27",
    )
    assert recap["items"][0]["files_at_least"] is False


def test_creation_answering_and_handoff_are_reported():
    events = [
        _event("task_created", "2026-08-27T08:00:00+00:00"),
        _event("attention.responded", "2026-08-27T09:00:00+00:00"),
        _event("attention.responded", "2026-08-27T09:10:00+00:00"),
        _event("handoff.completed", "2026-08-27T10:00:00+00:00"),
    ]
    recap = last_active_day(events, [], today="2026-08-27")
    assert _kinds(recap) == ["created", "handed_off", "answered"]
    assert recap["items"][2]["count"] == 2


def test_run_activity_events_mark_the_day_without_duplicating_the_outcome():
    """run.* events say the day was active; the outcome comes from the run record,
    which is the only thing that knows whether anything was delivered."""
    events = [
        _event("run.queued", "2026-08-27T09:00:00+00:00"),
        _event("run.process_started", "2026-08-27T09:01:00+00:00"),
        _event("run.settled", "2026-08-27T09:05:00+00:00"),
    ]
    recap = last_active_day(events, [], today="2026-08-27")
    assert recap["day"] == "2026-08-27"
    assert recap["items"] == []  # the day was active, but nothing is claimed
