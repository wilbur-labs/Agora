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


# ── the day is the one on the wall clock, not UTC's ────────────────────

def test_late_night_work_lands_on_the_local_day_not_the_utc_one():
    """The case that made this necessary: in UTC+9, 02:30 local is 17:30 UTC the
    previous date. Bucketing by UTC would file a late night under yesterday and
    make "where did I leave off" answer with the wrong day."""
    def tokyo_day(ts):
        # A fixed +09:00 view, so the test does not inherit the machine's zone.
        from datetime import datetime, timedelta
        if not ts:
            return None
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            return None
        return (parsed + timedelta(hours=9)).date().isoformat()

    # Worked 01:00–02:30 local on the 27th; both timestamps are UTC the 26th.
    runs = [_run("succeeded", "2026-08-26T16:00:00+00:00", delivered_count=2)]
    events = [_event("state_changed", "2026-08-26T17:30:00+00:00", to="review")]

    local = last_active_day(events, runs, today="2026-08-27", day_of=tokyo_day)
    assert local["day"] == "2026-08-27"
    assert local["days_ago"] == 0
    assert _kinds(local) == ["delivered", "stage_reached"]

    # The same history read in UTC files it a day earlier — the bug this avoids.
    utc = last_active_day(events, runs, today="2026-08-27",
                          day_of=lambda ts: ts[:10] if ts else None)
    assert utc["day"] == "2026-08-26"
    assert utc["days_ago"] == 1


def test_localday_helpers_agree_with_each_other():
    """A timestamp inside a local day's UTC bounds must map back to that day."""
    from agora.tasks.localday import local_day_bounds, local_day_of

    start, end = local_day_bounds("2026-08-27")
    assert local_day_of(start) == "2026-08-27"
    # The end bound is exclusive: it is already the next local day.
    assert local_day_of(end) == "2026-08-28"


def test_localday_of_rejects_junk_and_assumes_utc_when_naive():
    from datetime import datetime, timezone
    from agora.tasks.localday import local_day_of

    assert local_day_of(None) is None
    assert local_day_of("") is None
    assert local_day_of("not-a-timestamp") is None
    # A bare timestamp is read as UTC, not as local: reading it as local would
    # shift it by the offset and could move it to the wrong day.
    naive = "2026-08-26T16:00:00"
    aware = "2026-08-26T16:00:00+00:00"
    assert local_day_of(naive) == local_day_of(aware)
    expected = datetime(2026, 8, 26, 16, tzinfo=timezone.utc).astimezone().date().isoformat()
    assert local_day_of(aware) == expected


# ── which wall clock (codex: the process zone is UTC in Docker) ─────────

def test_configured_offset_decides_the_day_not_the_process_zone():
    """The process zone is the wrong thing to rely on: the supported Docker image
    is UTC with no TZ, and a backend on a remote server is in that server's zone.
    An explicit offset makes the day the viewer's regardless of where it runs."""
    from agora.tasks.localday import local_day_bounds, local_day_of, parse_offset

    tokyo = parse_offset("+09:00")
    utc = parse_offset("+00:00")
    new_york = parse_offset("-05:00")

    # 02:30 on the 27th in Tokyo; 17:30 on the 26th in UTC.
    late_night = "2026-08-26T17:30:00+00:00"
    assert local_day_of(late_night, zone=tokyo) == "2026-08-27"
    assert local_day_of(late_night, zone=utc) == "2026-08-26"
    assert local_day_of(late_night, zone=new_york) == "2026-08-26"

    # And the query bounds follow the same zone.
    assert local_day_bounds("2026-08-27", zone=tokyo) == (
        "2026-08-26T15:00:00+00:00", "2026-08-27T15:00:00+00:00",
    )
    assert local_day_bounds("2026-08-27", zone=utc) == (
        "2026-08-27T00:00:00+00:00", "2026-08-28T00:00:00+00:00",
    )


def test_a_bad_offset_is_rejected_rather_than_silently_utc():
    """A typo must not quietly move everyone's days back to UTC."""
    import pytest as _pytest
    from agora.tasks.localday import InvalidTimezoneOffset, parse_offset

    assert parse_offset("+0900").utcoffset(None).total_seconds() == 9 * 3600
    assert parse_offset("-05:30").utcoffset(None).total_seconds() == -5.5 * 3600
    bad_inputs = [
        "Asia/Tokyo",   # a zone name, the intuitive thing to write
        "+9", "09:00", "",  # malformed
        "+99:00", "+15:00",  # out of range
        # timedelta silently normalizes these into a *different* zone (+06:00 and
        # -01:39), so accepting them would be the very failure this guards.
        "+05:60", "-00:99", "+00:60",
    ]
    for bad in bad_inputs:
        with _pytest.raises(InvalidTimezoneOffset):
            parse_offset(bad)
    # And the largest legal minute value still works.
    assert parse_offset("+05:59").utcoffset(None).total_seconds() == 5 * 3600 + 59 * 60


def test_the_committed_config_does_not_pin_one_persons_timezone():
    """The repo's own config.yaml must leave timezone_offset unset: it ships to
    everyone and to the Docker image, so a value here would override every
    deployment's zone with whoever committed it — reintroducing the wrong-day bug
    this module exists to fix, for every other user."""
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert not (config.get("control_plane", {}) or {}).get("timezone_offset")


def test_day_bounds_round_trip_in_the_same_zone():
    from agora.tasks.localday import local_day_bounds, local_day_of, parse_offset

    for offset in ["+09:00", "+00:00", "-05:00", "+05:45"]:
        zone = parse_offset(offset)
        start, end = local_day_bounds("2026-08-27", zone=zone)
        assert local_day_of(start, zone=zone) == "2026-08-27", offset
        # End is exclusive: it is already the next day.
        assert local_day_of(end, zone=zone) == "2026-08-28", offset


def test_day_bounds_are_two_local_midnights_not_start_plus_24h():
    """On a daylight-saving change a local day is 23 or 25 hours, so the end
    bound must be the *next local midnight*, not start + 24h. With a fixed offset
    the two agree, and this pins the shape that keeps them agreeing when the zone
    is the machine's own and does observe DST."""
    from datetime import datetime, timedelta
    from agora.tasks.localday import local_day_bounds, parse_offset

    for offset in ["+09:00", "+00:00", "-05:00", "+05:45", "+14:00"]:
        zone = parse_offset(offset)
        for day in ["2026-01-01", "2026-03-29", "2026-10-25", "2026-12-31"]:
            start, end = local_day_bounds(day, zone=zone)
            span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
            # A fixed offset never shifts, so every day is exactly 24 hours.
            assert span == timedelta(hours=24), (offset, day, span)
            # And the end bound is the following day's start.
            next_start, _ = local_day_bounds(
                (datetime.fromisoformat(day + "T00:00:00").date() + timedelta(days=1)).isoformat(),
                zone=zone,
            )
            assert end == next_start, (offset, day)


def test_zone_offset_is_reported_so_a_client_can_time_the_rollover():
    """The client needs the *display* zone's offset to know when this zone's day
    rolls over. The browser's own zone is not a substitute: the display zone can
    be configured and the backend may run somewhere else entirely."""
    from agora.tasks.localday import parse_offset, zone_offset_minutes

    assert zone_offset_minutes(zone=parse_offset("+09:00")) == 540
    assert zone_offset_minutes(zone=parse_offset("+00:00")) == 0
    assert zone_offset_minutes(zone=parse_offset("-05:00")) == -300
    assert zone_offset_minutes(zone=parse_offset("+05:45")) == 345
    # Unset falls back to the process zone, which must still be a real offset.
    assert isinstance(zone_offset_minutes(), int)


def test_next_rollover_is_the_zone_s_next_midnight_not_now_plus_24h():
    """The client schedules its refetch from this, because the current offset
    alone cannot locate the next midnight across a DST change. Must always land
    strictly ahead and no more than a day out, in every zone."""
    from datetime import datetime, timedelta, timezone as tz
    from agora.tasks.localday import local_day_bounds, local_day_of, next_rollover, parse_offset

    # One captured instant drives every assertion, including the call itself: let
    # next_rollover read the clock again and a run that crosses a zone's midnight
    # gets the midnight *after* the one this "now" belongs to, so the test would
    # re-enact the very cross-midnight race it exists to catch.
    now = datetime.now(tz.utc)
    for offset in ["+09:00", "+00:00", "-05:00", "+05:45", "+14:00", "-11:00"]:
        zone = parse_offset(offset)
        at = datetime.fromisoformat(next_rollover(zone=zone, now=now))
        assert now < at <= now + timedelta(days=1), offset
        # It is exactly a midnight in that zone: the instant belongs to the day
        # after the one that contains "now".
        today = local_day_of(now.isoformat(), zone=zone)
        assert local_day_of(at.isoformat(), zone=zone) != today, offset
        assert at.isoformat() == local_day_bounds(today, zone=zone)[1], offset


# ── one response, one instant (codex P2 / kiro P3) ─────────────────────
#
# The endpoint derives several things from the wall clock: the day it reads, the
# `today` that becomes days_ago, the reported offset, and the next rollover. Read
# the clock once per derivation and a request that crosses this zone's midnight
# answers from two days at once — days_ago from before the boundary, next_rollover
# from the midnight *after* it — and the client then waits a whole extra day while
# calling yesterday "today". These pin the single-snapshot behavior by handing the
# endpoint a clock that jumps forward on every read.


def _advancing_clock(*instants: str):
    """A ``now_utc`` replacement that returns each instant in turn and sticks on
    the last. One read sees only the first, so any extra read is visible."""
    from datetime import datetime

    values = [datetime.fromisoformat(value) for value in instants]
    reads: list = []

    def clock():
        reads.append(values[min(len(reads), len(values) - 1)])
        return reads[-1]

    clock.reads = reads  # type: ignore[attr-defined]
    return clock


def _recap_endpoint(tmp_path, monkeypatch, *, offset: str, clock, event_at: str | None):
    """The real endpoint over real stores, with the zone and the clock pinned."""
    from agora.api.settings_router import task_recap
    from agora.execution.store import ExecutionStore
    from agora.tasks import localday
    from agora.tasks.localday import parse_offset
    from agora.tasks.models import CreateTaskRequest
    from agora.tasks.store import TaskStore

    zone = parse_offset(offset)
    monkeypatch.setattr(localday, "configured_zone", lambda: zone)
    monkeypatch.setattr(localday, "now_utc", clock)

    tasks = TaskStore(tmp_path / f"recap-{offset.replace(':', '')}.db")
    runs = ExecutionStore(tasks)
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Where did I leave off"))
    if event_at is None:
        # The "no activity" path: drop the creation event so no day is eligible.
        with tasks._transaction() as db:
            db.execute("DELETE FROM task_events WHERE task_id = ?", (task.task_id,))
    else:
        with tasks._transaction() as db:
            db.execute("UPDATE task_events SET created_at = ? WHERE task_id = ?",
                       (event_at, task.task_id))
    return task_recap(task.task_id, task_store=tasks, exec_store=runs), zone


def _assert_one_instant(recap, zone):
    """The response's own fields must agree: the rollover it tells the client to
    wait for has to be the end of the day it is calling "today"."""
    from datetime import date, timedelta

    from agora.tasks.localday import local_day_bounds

    assert recap["days_ago"] is not None, "this check needs a day to anchor on"
    today = date.fromisoformat(recap["day"]) + timedelta(days=recap["days_ago"])
    assert recap["next_rollover"] == local_day_bounds(today.isoformat(), zone=zone)[1]


def test_recap_stays_consistent_when_the_clock_crosses_local_midnight(tmp_path, monkeypatch):
    """Pinned a tenth of a second before local midnight in +09:00, with every
    later read landing after it. The response must be entirely on the earlier
    side: today is the 27th, so the rollover is the 27th's, not the 28th's."""
    recap, zone = _recap_endpoint(
        tmp_path, monkeypatch, offset="+09:00",
        clock=_advancing_clock("2026-08-27T14:59:59.900000+00:00",
                               "2026-08-27T15:00:01+00:00"),
        event_at="2026-08-27T10:00:00+00:00",
    )
    assert recap["day"] == "2026-08-27"
    assert recap["days_ago"] == 0
    assert recap["next_rollover"] == "2026-08-27T15:00:00+00:00"
    assert recap["zone_offset_minutes"] == 540
    _assert_one_instant(recap, zone)


def test_the_no_activity_response_is_snapshotted_too(tmp_path, monkeypatch):
    """The early return has the same two reads to get wrong, so it takes the same
    snapshot: a task with nothing recorded still gets the boundary of the day the
    request was answered in."""
    recap, _ = _recap_endpoint(
        tmp_path, monkeypatch, offset="+09:00",
        clock=_advancing_clock("2026-08-27T14:59:59.900000+00:00",
                               "2026-08-27T15:00:01+00:00"),
        event_at=None,
    )
    assert recap["day"] is None and recap["days_ago"] is None
    assert recap["next_rollover"] == "2026-08-27T15:00:00+00:00"
    assert recap["zone_offset_minutes"] == 540


def test_next_rollover_and_days_ago_come_from_the_same_instant(tmp_path, monkeypatch):
    """Across zones, and with the clock jumping a full day on every read after the
    first: days_ago and next_rollover must still describe one instant."""
    for offset in ["+09:00", "+00:00", "-05:00", "+05:45", "+14:00", "-11:00"]:
        recap, zone = _recap_endpoint(
            tmp_path, monkeypatch, offset=offset,
            clock=_advancing_clock("2026-08-27T12:00:00+00:00",
                                   "2026-08-28T12:00:00+00:00",
                                   "2026-08-29T12:00:00+00:00"),
            event_at="2026-08-27T12:00:00+00:00",
        )
        _assert_one_instant(recap, zone)
        # And the day the response is about is the day it was answered on, so the
        # panel says "today" — the whole point of reading the clock once.
        assert recap["days_ago"] == 0, offset
