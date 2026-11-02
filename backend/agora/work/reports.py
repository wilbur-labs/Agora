"""Reading what the session reporter wrote (CR-75 §3, §5).

A report is an assertion from the local runtime, not tamper-proof evidence: the page
says "recorded", never "verified". Every line is checked on its own; nothing in a line
reaches a command line or a path unless it passes these checks.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Claude's session ids, canonical lowercase UUID. Nothing else reaches ``--resume``.
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: A journal larger than this is not read past it: a launch writes a handful of lines.
MAX_JOURNAL_BYTES = 1 << 20


def claude_projects_dir() -> Path:
    """Where Claude keeps transcripts: ``$CLAUDE_CONFIG_DIR/projects`` when that is set
    (the terminal inherits it from the server), ``~/.claude/projects`` otherwise."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return (Path(configured).expanduser() if configured else Path.home() / ".claude") / "projects"


def trusted_transcript(raw: object, base: Path | None = None) -> Path | None:
    """The transcript path, if it is a ``.jsonl`` under Claude's projects directory."""
    if not isinstance(raw, str) or not raw:
        return None
    base = base or claude_projects_dir()
    try:
        path = Path(raw).resolve(strict=False)
        root = base.resolve(strict=False)
    except (OSError, ValueError):
        return None
    if path.suffix != ".jsonl":
        return None
    a, b = os.path.normcase(str(path)), os.path.normcase(str(root))
    return path if a.startswith(b.rstrip("\\/") + os.sep) else None


@dataclass
class SessionEvent:
    event: str
    source: str | None
    reason: str | None
    session_id: str
    transcript: Path


@dataclass
class Journal:
    """One launch's journal, reduced."""
    #: The launch's first SessionStart line, valid or not — the confirmation is latched on it.
    first_start: SessionEvent | None = None
    #: Whether the first SessionStart line was there but failed the checks.
    first_start_invalid: bool = False
    #: The last valid SessionStart: the newest recovery point this launch offers.
    last_valid: SessionEvent | None = None
    #: A session change after ``last_valid`` (or with no valid one at all) that could not
    #: be read: a null id, a bad id, an untrusted path, a broken line.
    stale_after: bool = False
    #: The last event of any kind was a SessionEnd whose reason is not ``clear``.
    ended: bool = False
    #: The journal file exists but could not be read (locked, denied).
    read_failed: bool = False
    #: Which launch this journal is: a report is only ever weighed against its own launch.
    launch_id: str | None = None
    events: list[dict] = field(default_factory=list)


def read_journal(path: Path, *, key: str, launch_id: str, base: Path | None = None) -> Journal:
    journal = Journal(launch_id=launch_id)
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_JOURNAL_BYTES + 1)
    except FileNotFoundError:
        return journal
    except OSError:
        journal.stale_after = journal.read_failed = True
        return journal
    truncated = len(raw) > MAX_JOURNAL_BYTES
    if truncated:
        raw = raw[:MAX_JOURNAL_BYTES]
    lines = raw.split(b"\n")
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            # A broken line could have been a session change: it makes what came before stale,
            # and claude's state after it is unknown.
            _unreadable_line(journal)
            continue
        if not isinstance(record, dict) or record.get("key") != key or record.get("launch_id") != launch_id:
            _unreadable_line(journal)
            continue
        event = record.get("event")
        if event == "SessionStart":
            sid, transcript = record.get("session_id"), trusted_transcript(record.get("transcript_path"), base)
            valid = isinstance(sid, str) and SESSION_ID_RE.fullmatch(sid) is not None and transcript is not None
            parsed = SessionEvent("SessionStart", _str(record.get("source")), None, sid, transcript) if valid else None
            if journal.first_start is None and not journal.first_start_invalid:
                if parsed is None or parsed.source not in ("startup", "resume"):
                    journal.first_start_invalid = True
                else:
                    journal.first_start = parsed
            if parsed is None:
                journal.stale_after = True
            else:
                journal.last_valid, journal.stale_after = parsed, False
            journal.ended = False
        elif event == "SessionEnd":
            reason = _str(record.get("reason"))
            journal.ended = reason != "clear"
            if reason == "clear":
                # The conversation just ended; until the next SessionStart arrives the
                # newest one is not recorded yet (kiro review).
                journal.stale_after = True
        else:
            # The reporter writes a line even when it could not read what claude sent
            # (``event: null``): that is a report that failed, so what came before is
            # no longer known to be current (code review round 2).
            _unreadable_line(journal)
        journal.events.append(record)
    if truncated:
        # What came after the part read is unknown: it may hold a newer conversation, so a
        # valid line in the prefix must not clear the mark (kiro review).
        journal.stale_after = True
    return journal


def _unreadable_line(journal: Journal) -> None:
    """A line that says nothing usable: what came before is stale; if no SessionStart came
    first, the launch's first report is the unreadable one; and whether claude has quit is
    no longer known, so it is not said (kiro round 5)."""
    journal.stale_after = True
    journal.ended = False
    if journal.first_start is None:
        journal.first_start_invalid = True


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None
