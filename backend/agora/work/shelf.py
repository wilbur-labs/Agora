"""Pausing, archiving and restoring a work project (workspace CR-80).

The project's own record decides whether the overview lists it: ``status`` in its
``task.yaml``. So pausing writes ``status: paused`` there, and claude-workspace
(``cw list``, the SessionStart hook) sees the same thing the overview does — Agora keeps
no list of its own that could disagree with the record.

Archiving a claude-workspace task does what ``cw archive`` does (``scripts/cw.sh``):
``status: archived`` plus ``completed_at``, then the directory moves to
``tasks/_archive/``; restoring undoes both. It is done here, not by running ``cw.sh``:
that would need Git Bash on the server's PATH and would inherit whatever ``CW_TASK`` the
server was started with. If ``cw archive`` changes, this has to follow — the test
``test_archive_matches_cw_archive_layout`` pins the layout both agree on.
kiro-workspace has no archive directory, so there only the status changes.

Every write is checked by parsing the result: the status must read back as intended and
nothing else in the document may differ. A file this edit cannot handle is refused, never
rewritten. Nothing is committed: the work data repository has several sessions writing
to it at once, and a commit made here could take their changes with it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ARCHIVE_DIR = "_archive"


class ShelfRefused(Exception):
    """The change was not made; ``str(self)`` says why, for the person."""


@dataclass(frozen=True)
class Edit:
    text: str
    status: str


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    """The ``task:`` mapping's body: [first line, end) — or None when there is none."""
    for i, line in enumerate(lines):
        if re.match(r"^task:\s*(#.*)?$", line):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                body = lines[j]
                if body.strip() and not body.startswith((" ", "\t", "#")):
                    end = j
                    break
            return i + 1, end
    return None


def _set_key(lines: list[str], key: str, value: str | None, *, after: str | None = None) -> list[str]:
    """Set (or with ``value=None`` remove) ``key`` in the ``task:`` mapping, or at the top
    level when the document has no ``task:`` mapping. Only the first matching line is
    touched; a new line goes after ``after`` when that key is present, else first."""
    out = list(lines)
    bounds = _block_bounds(out)
    if bounds is None:
        indent, start, end = "", 0, len(out)
    else:
        indent, (start, end) = "  ", bounds
    pattern = re.compile(rf"^{indent}{key}:")
    for i in range(start, end):
        if pattern.match(out[i]):
            if value is None:
                del out[i]
            else:
                out[i] = f"{indent}{key}: {value}"
            return out
    if value is None:
        return out
    insert_at = start if bounds is not None else end
    if after is not None:
        after_pattern = re.compile(rf"^{indent}{after}:")
        for i in range(start, end):
            if after_pattern.match(out[i]):
                insert_at = i + 1
                break
    out.insert(insert_at, f"{indent}{key}: {value}")
    return out


def _status_of(document: Any) -> str | None:
    task = document.get("task") if isinstance(document, dict) else None
    for holder in (task if isinstance(task, dict) else None, document):
        if isinstance(holder, dict) and "status" in holder:
            value = holder["status"]
            return value.strip() if isinstance(value, str) else str(value)
    return None


def _without(document: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(document, dict):
        return document
    copy = {k: v for k, v in document.items() if k not in keys}
    task = document.get("task")
    if isinstance(task, dict):
        copy["task"] = {k: v for k, v in task.items() if k not in keys}
    return copy


def edit_status(text: str, status: str, *, completed_at: str | None = None,
                drop_completed_at: bool = False) -> Edit:
    """The document with its status set — verified by parsing, or ShelfRefused."""
    try:
        before = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ShelfRefused(f"task.yaml could not be parsed ({type(exc).__name__})") from None
    if not isinstance(before, dict):
        raise ShelfRefused("task.yaml is not a mapping")
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r\n"))
    lines = text.splitlines()
    lines = _set_key(lines, "status", status, after="goal")
    if completed_at is not None:
        lines = _set_key(lines, "completed_at", completed_at, after="status")
    elif drop_completed_at:
        lines = _set_key(lines, "completed_at", None)
    result = newline.join(lines) + (newline if trailing else "")
    try:
        after = yaml.safe_load(result)
    except yaml.YAMLError:
        raise ShelfRefused("this task.yaml's layout is not one the edit can change safely") from None
    touched = ("status", "completed_at")
    if _status_of(after) != status or _without(after, touched) != _without(before, touched):
        raise ShelfRefused("this task.yaml's layout is not one the edit can change safely")
    return Edit(text=result, status=status)


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise ShelfRefused("task.yaml is not there") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ShelfRefused(f"task.yaml could not be read ({type(exc).__name__})") from None


def _write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".agora-tmp")
    try:
        temp.write_bytes(text.encode("utf-8"))
        os.replace(temp, path)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ShelfRefused(f"task.yaml could not be written ({type(exc).__name__})") from None


def _current_task(source: Path) -> str | None:
    """The workspace's global pointer. Unknown is not "none": a pointer that cannot be read
    refuses, since the task may be the current one."""
    try:
        return (source / ".current-task").read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise ShelfRefused("could not read this workspace's .current-task, so cannot tell whether "
                           "this is the current task") from None


def pause(source: Path, task: str) -> None:
    path = Path(source) / "tasks" / task / "task.yaml"
    _write(path, edit_status(_read(path), "paused").text)


def archive(source: Path, task: str, *, move: bool, today: date | None = None) -> None:
    """``move``: a claude-workspace source, where archived tasks live in ``_archive/``."""
    source = Path(source)
    task_dir = source / "tasks" / task
    path = task_dir / "task.yaml"
    if move:
        if _current_task(source) == task:
            raise ShelfRefused("this is the workspace's current task (.current-task); switch to another "
                               "task first, as cw archive requires")
        target = source / "tasks" / ARCHIVE_DIR / task
        if os.path.lexists(target):
            raise ShelfRefused(f"tasks/{ARCHIVE_DIR}/{task} already exists")
    original = _read(path)
    stamp = (today or date.today()).isoformat()
    edited = edit_status(original, "archived", completed_at=stamp if move else None)
    _write(path, edited.text)
    if not move:
        return
    try:
        target.parent.mkdir(exist_ok=True)
        os.rename(task_dir, target)
    except OSError as exc:
        # The directory did not move: put the record back as it was, so it does not say
        # archived while still sitting among the active tasks.
        try:
            _write(path, original)
        except ShelfRefused:
            raise ShelfRefused(f"the directory could not be moved ({type(exc).__name__}), and task.yaml "
                               "could not be put back: it now says archived") from None
        raise ShelfRefused(f"the directory could not be moved ({type(exc).__name__}); nothing changed") from None


def restore(source: Path, task: str, *, archived_dir: bool) -> None:
    """Back to ``status: active``; a task in ``_archive/`` moves back first."""
    source = Path(source)
    if archived_dir:
        held = source / "tasks" / ARCHIVE_DIR / task
        task_dir = source / "tasks" / task
        if os.path.lexists(task_dir):
            raise ShelfRefused(f"tasks/{task} already exists")
        path = held / "task.yaml"
        edited = edit_status(_read(path), "active", drop_completed_at=True)
        try:
            os.rename(held, task_dir)
        except OSError as exc:
            raise ShelfRefused(f"the directory could not be moved back ({type(exc).__name__}); "
                               "nothing changed") from None
        try:
            _write(task_dir / "task.yaml", edited.text)
        except ShelfRefused as exc:
            raise ShelfRefused(f"moved back to tasks/{task}, but {exc}: it still says archived") from None
        return
    path = source / "tasks" / task / "task.yaml"
    _write(path, edit_status(_read(path), "active").text)
