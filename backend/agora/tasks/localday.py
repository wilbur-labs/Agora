"""Calendar days as the person using Agora experiences them.

Everything is stored in UTC, which is right for storage and wrong for the word
"yesterday". Someone working at 02:30 local is having a late night, not an early
yesterday — and in UTC+9 that timestamp falls on the previous UTC date. A recap
that answers "where did I leave off" has to agree with the calendar on the wall,
or the answer is off by a day exactly when they worked late.

**Which wall clock** is a configuration question, not a detail. The process zone
is the wrong default to rely on: the supported Docker image is
``python:3.12-slim`` with no ``TZ``, so a container reads as UTC and the shift
this module exists to make never happens. Remote projects make it worse — the
backend can sit on a server in another zone entirely. So the zone comes from
``control_plane.timezone_offset`` in config, and the process zone is only the
fallback for a plain local run, where it is correct.

The offset is written as ``+09:00`` / ``-05:00`` rather than a zone name, because
``zoneinfo`` needs a tz database that Windows does not ship: ``ZoneInfo`` raises
``ZoneInfoNotFoundError`` there unless ``tzdata`` is installed. Known limitation:
a fixed offset does not follow daylight saving. Zones that observe DST should
leave it unset on a machine whose own clock is right, where ``astimezone()``
applies the offset in force at each timestamp.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

_OFFSET_PATTERN = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


class InvalidTimezoneOffset(ValueError):
    pass


def parse_offset(value: str) -> timezone:
    """``"+09:00"`` (or ``"+0900"``) as a fixed timezone. Raises
    ``InvalidTimezoneOffset`` on anything else, so a typo in config surfaces
    instead of silently reverting to UTC and moving everyone's days."""
    match = _OFFSET_PATTERN.match(value.strip())
    if not match:
        raise InvalidTimezoneOffset(
            f"timezone_offset must look like +09:00 or -05:00, got {value!r}"
        )
    sign, hours, minutes = match.groups()
    # Check the minute field before building a timedelta. timedelta happily
    # normalizes "+05:60" into +06:00 and "-00:99" into -01:39, so without this
    # a typo would silently select a *different* zone — the exact failure this
    # function refuses `Asia/Tokyo` to prevent, arriving through the other door.
    if int(minutes) >= 60:
        raise InvalidTimezoneOffset(f"timezone_offset minutes must be < 60, got {value!r}")
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    if delta > timedelta(hours=14):  # the widest real-world offset is +14:00
        raise InvalidTimezoneOffset(f"timezone_offset out of range: {value!r}")
    return timezone(-delta if sign == "-" else delta)


def configured_zone() -> timezone | None:
    """The configured display zone, or None to use the process zone.

    Read per call rather than cached: config is reloadable, and a recap is cheap
    enough that one dict lookup does not matter.
    """
    from agora.config.settings import get_config

    raw = (get_config().get("control_plane", {}) or {}).get("timezone_offset")
    if raw in (None, ""):
        return None
    return parse_offset(str(raw))


def now_utc() -> datetime:
    """The current instant, in UTC.

    Exists so a response can read the clock *once* and pass that instant to every
    derivation below. Each of these functions defaults to reading the clock again,
    which is fine in isolation and wrong for a response that has to agree with
    itself: a request that straddles local midnight would take its day from before
    the boundary and its next rollover from after it, and the page would then call
    yesterday "today" until the following midnight.
    """
    return datetime.now(timezone.utc)


def local_day_of(timestamp: str | None, *, zone: timezone | None = None) -> str | None:
    """The display calendar day (YYYY-MM-DD) of a stored UTC ISO timestamp, or
    None if it is missing or unparseable — a recap must not fail because one row
    is malformed.

    ``zone`` overrides the configured one; None means "resolve it now".
    """
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Stored timestamps always carry an offset; read a bare one as UTC rather
        # than as local, which would shift it and could move it to another day.
        parsed = parsed.replace(tzinfo=timezone.utc)
    target = zone if zone is not None else configured_zone()
    shifted = parsed.astimezone(target) if target else parsed.astimezone()
    return shifted.date().isoformat()


def local_today(*, zone: timezone | None = None, now: datetime | None = None) -> str:
    """Today, on the display zone's wall clock.

    ``now`` pins the instant, so a caller that also derives a rollover reads the
    clock once and gets one day out of it; None means "read it now".
    """
    target = zone if zone is not None else configured_zone()
    instant = now if now is not None else now_utc()
    return (instant.astimezone(target) if target else instant.astimezone()).date().isoformat()


def _local_midnight(day: date, zone: timezone | None) -> datetime:
    """The instant a display-zone day begins, as an aware datetime."""
    naive = datetime.combine(day, time())
    # A fixed offset attaches exactly; the process zone resolves the offset that
    # applies on *that* date, which is what makes the DST case come out right.
    return naive.replace(tzinfo=zone) if zone else naive.astimezone()


def zone_offset_minutes(*, zone: timezone | None = None, now: datetime | None = None) -> int:
    """The display zone's current offset from UTC, in minutes. Informational —
    for scheduling a day rollover use ``next_rollover``, which is DST-correct.

    ``now`` pins the instant, which matters under DST: the offset either side of a
    transition differs, so a response must report the one for the instant it
    answered from.
    """
    target = zone if zone is not None else configured_zone()
    instant = now if now is not None else now_utc()
    offset = (instant.astimezone(target) if target else instant.astimezone()).utcoffset()
    return int(offset.total_seconds() // 60) if offset else 0


def next_rollover(*, zone: timezone | None = None, now: datetime | None = None) -> str:
    """The next instant the display zone's calendar day changes, as a UTC ISO
    timestamp.

    Computed here rather than left to the client, because the current offset is
    not enough to locate the next midnight: on a spring-forward date the offset
    in force *now* is the old one, so "now plus 24 hours minus time-since-
    midnight" lands an hour late and a page keeps saying "today" into the next
    day. The bounds helper resolves each midnight in the zone independently, so
    this is right across a DST change.

    The browser's own zone is not a substitute either — the display zone can be
    configured and the backend may run elsewhere, which is why the configured
    zone exists at all.

    ``now`` pins the instant. Pass the same one used for ``local_today`` or the
    two can disagree across midnight, which puts the client's refetch a whole day
    out — the boundary it waits for is the one *after* the day it is showing.
    """
    target = zone if zone is not None else configured_zone()
    instant = now if now is not None else now_utc()
    today = (instant.astimezone(target) if target else instant.astimezone()).date()
    _, end = local_day_bounds(today.isoformat(), zone=target)
    return end


def local_day_bounds(day: str, *, zone: timezone | None = None) -> tuple[str, str]:
    """``(start, end)`` as UTC ISO timestamps spanning one display-zone day, end
    exclusive.

    Stored timestamps are UTC, so a display day is a *range* over them, never a
    string prefix: in UTC+9, local 2026-08-27 runs from 2026-08-26T15:00Z to
    2026-08-27T15:00Z. Any query bucketing by day has to compare against these
    bounds instead of matching the first ten characters.

    Both ends are the *next* local midnight, computed independently, rather than
    start plus 24 hours. On a daylight-saving change a local day is 23 or 25 hours
    long, so adding a fixed day would put the boundary an hour off and file that
    hour's work under the wrong day.
    """
    parsed = date.fromisoformat(day)
    target = zone if zone is not None else configured_zone()
    start = _local_midnight(parsed, target).astimezone(timezone.utc)
    end = _local_midnight(parsed + timedelta(days=1), target).astimezone(timezone.utc)
    return start.isoformat(), end.isoformat()
