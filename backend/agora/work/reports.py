"""Reading what the session reporter wrote (CR-75 §3, §5).

A report is an assertion from the local runtime, not tamper-proof evidence: the page
says "recorded", never "verified". Every line is checked on its own; nothing in a line
reaches a command line or a path unless it passes these checks.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
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


def has_conversation(transcript: Path) -> bool:
    """Whether the transcript holds a message. Claude writes the file at startup, so one
    nobody spoke in exists and holds only its own bookkeeping — nothing to resume. Raises
    ``OSError`` when it cannot be read; a line that does not parse is skipped."""
    with transcript.open("rb") as handle:
        for line in handle:
            if b'"user"' not in line and b'"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("type") in ("user", "assistant"):
                return True
    return False


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


# --- a newer conversation of the same task, opened outside the work page (CR-86) -------------
#: How claude-workspace's SessionStart hook names the task and the work root (its first
#: line, and the line in its 路径 block). Without both, a transcript is not claimed.
_ACTIVE_TASK_RE = re.compile(r"^# Active Task: (\S+)")
_WORK_ROOT_RE = re.compile(r"^- 工作根：(.+?)(?: —— .*)?$", re.M)
#: A newer conversation written to this recently may still be open in another window:
#: resuming it here as well would fork it, so it is named, not taken.
BUSY_SECONDS = 600


@dataclass(frozen=True)
class TaskConversation:
    session_id: str
    transcript: Path
    #: The file's last write — any write, so a window that may still be open.
    modified: float
    #: When someone last spoke in it: what "newer" is measured by.
    spoken: float
    #: The directory the conversation ran in, as its last entries say.
    cwd: str | None


def _same_dir(a: str, b: str) -> bool:
    def norm(path: str) -> str:
        return os.path.normcase(path.replace("\\", "/").rstrip("/"))
    return norm(a) == norm(b)


def conversation_task(transcript: Path) -> tuple[str, str, str | None] | None:
    """(task, work root, cwd) named by the transcript's last SessionStart hook output —
    the last, since an unpinned window can change task at /clear — or None when it names
    none. Every SessionStart output with content counts: one that names no task and work
    root (claude-workspace's ``(无)``, or any other) clears what came before (codex review
    round 2). Empty outputs — hooks that print nothing, Agora's reporter among them — say
    nothing. Raises ``OSError`` when the file cannot be read."""
    found: tuple[str, str] | None = None
    cwd: str | None = None
    with transcript.open("rb") as handle:
        for line in handle:
            if b'"cwd"' in line and b'"hook_success"' not in line:
                try:
                    entry = json.loads(line)
                except ValueError:
                    entry = None
                if isinstance(entry, dict) and isinstance(entry.get("cwd"), str):
                    cwd = entry["cwd"]
            if b'"hook_success"' not in line or b"SessionStart" not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            attachment = entry.get("attachment") if isinstance(entry, dict) else None
            if not isinstance(attachment, dict) or attachment.get("type") != "hook_success" \
                    or attachment.get("hookEvent") != "SessionStart":
                continue
            content = attachment.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            task, root = _ACTIVE_TASK_RE.match(content), _WORK_ROOT_RE.search(content)
            found = (task.group(1), root.group(1).strip()) if task and root else None
            if isinstance(entry.get("cwd"), str):
                cwd = entry["cwd"]
    return (*found, cwd) if found else None


def last_spoken(transcript: Path) -> float | None:
    """When the last user or assistant message was written, or None when there is none
    with a readable time. Not the file's mtime: resuming a conversation writes startup
    bookkeeping into it without anyone speaking (codex review round 1). Raises ``OSError``
    when the file cannot be read."""
    last: float | None = None
    with transcript.open("rb") as handle:
        for line in handle:
            if b'"user"' not in line and b'"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
                continue
            stamp = entry.get("timestamp")
            try:
                at = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if isinstance(stamp, str) else None
            except ValueError:
                at = None
            if at is not None and (last is None or at > last):
                last = at
    return last


def newer_task_conversation(base: Path, task: str, work_root: str, *, after: float,
                            exclude: str | None = None) -> TaskConversation | None:
    """The conversation under ``base`` (Claude's projects directory) in which someone
    spoke last, after ``after``, whose last SessionStart hook named ``task`` under
    ``work_root``. Transcripts that cannot be read, or name no task, are passed over: this
    only ever adds a candidate, it never says there is none."""
    candidates: list[tuple[float, Path]] = []
    try:
        folders = [f for f in base.iterdir() if f.is_dir()]
    except OSError:
        return None
    for folder in folders:
        try:
            for path in folder.glob("*.jsonl"):
                if not SESSION_ID_RE.fullmatch(path.stem) or path.stem == exclude:
                    continue
                modified = path.stat().st_mtime
                if modified > after:
                    candidates.append((modified, path))
        except OSError:
            continue
    best: TaskConversation | None = None
    # A file is written to at least as late as anyone spoke in it, so the mtime filter
    # above loses nothing; the order is decided by when someone spoke.
    for modified, path in sorted(candidates, reverse=True):
        if best is not None and modified <= best.spoken:
            break  # nothing older-written can have spoken later
        try:
            named = conversation_task(path)
            if named is None or named[0] != task or not _same_dir(named[1], work_root):
                continue
            spoken = last_spoken(path)
        except OSError:
            continue
        if spoken is None or spoken <= after:
            continue
        if best is None or spoken > best.spoken:
            best = TaskConversation(session_id=path.stem, transcript=path, modified=modified,
                                    spoken=spoken, cwd=named[2])
    return best
