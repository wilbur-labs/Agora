"""A project's RESUME progress snapshot, counted — the display layer's progress source.

**Why this exists rather than the dashboard's weights.** `tasks/progress.py`
derives a criterion's status from *task-level* signals: `has_worker_success` is
true when the task has any historical successful worker run, and every criterion of
that task then shares that status. So after a requirements change adds new
acceptance criteria, a run that finished before those criteria existed still makes
them read as already produced — and, on a task carried to verified/done, as already
verified. `verified_pct` is that same weight rolled up. It is a real defect of the
metric, not of one caller, so no display can be made honest by rewording it: the
number itself is wrong in an ordinary situation.

The recorded UI decision is therefore that the progress bar is driven by the RESUME
progress snapshot the working model already keeps — the human-maintained checklist
of where the project is — and that a percentage is reported from its **done** count
only. This module is the reader for that file, and it counts; it never scores.

**The contract, exactly.**

- The snapshot is ``<project root>/.agora/development/PROGRESS.md`` — the same path
  `AGENTS.md` names as the resume record. One path, no search, no fallback: a
  fallback would make "no snapshot" un-provable, which is the one thing the UI has
  to be able to say without inventing a number.
- Items are counted from line markers: ``✅`` done, ``🔵`` in progress, ``⬜`` not
  started. A marker counts only when it is the first thing on its line, optionally
  after a list bullet (``-``, ``*``, ``+``, ``1.``, ``1)``), so the same character
  used mid-sentence in the prose around the checklist is not counted as an item.
  One item per line — the first marker wins.
- Every outcome this can have is distinguishable in the payload, because the UI has
  to distinguish them:

  ===================  ==========================================================
  ``absent``           there is nothing at the snapshot path. The bar renders
                       empty. It does **not** render 0%.
  ``too_large``        the file is past ``MAX_SNAPSHOT_BYTES``. Counting a prefix
                       would yield a floor, and a floor rendered as a percentage
                       is a wrong number, so this is unknown.
  ``unreadable``       something is at the path and it could not be read, **or**
                       the path could not be observed at all: permissions on the
                       file or on a directory above it, undecodable bytes, or an
                       entry that is not a regular file (a directory, a broken
                       symlink, a device). Unknown, and never zero — "present but
                       unusable" is not "there is no snapshot", and neither is
                       "could not tell". Only a definite ``FileNotFoundError``
                       is absence; see :mod:`agora.fsprobe`.
  ``uncounted_markers``  the file was read, no ``✅``/``🔵``/``⬜`` item was found,
                       and it does carry checklist lines in another convention
                       (Markdown task-list checkboxes). Unknown: the honest
                       statement is "this file uses markers this reader does not
                       count", not "this snapshot tracks nothing". See below.
  available            counts are exact. ``done + in_progress + todo`` may still
                       be 0 — a snapshot with no items of any convention — which
                       is "no number to report", not 0%.
  ===================  ==========================================================

  When it is not available the three counts are ``None`` rather than 0. A zero
  cannot be rendered as a percentage by accident if it is not in the payload.

**Why ``uncounted_markers`` exists rather than a checkbox rule.** Agora's own
``.agora/development/PROGRESS.md`` is written as ``- [x]`` task-list lines and holds
over a thousand of them, so the marker rule counts zero items in a file a human would
describe as recording a great deal of finished work. Reporting that as "tracks no
items yet" is a false statement about the project, so it gets its own outcome. What
this module does **not** do is map checkboxes into the three buckets: the recorded
decision names ``✅``/``🔵``/``⬜``, a checkbox has no form for 🔵 in progress, and
picking one would extend the maintainer's decision instead of implementing it. So this
reader reports the mismatch and counts nothing.

Pure apart from the one bounded file read, so the counting rules are testable
without a project on disk.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agora.fsprobe import probe_path

#: Where a project keeps its resume progress snapshot, relative to the project root.
SNAPSHOT_RELATIVE_PATH = Path(".agora") / "development" / "PROGRESS.md"

#: Marker -> bucket. Exactly the three the recorded decision names; anything else is
#: not an item, because guessing a fourth bucket is inventing data.
MARKERS: dict[str, str] = {"✅": "done", "🔵": "in_progress", "⬜": "todo"}

#: List bullets a marker may sit behind. `1.` / `1)` are handled separately.
_BULLETS = ("-", "*", "+")

#: The read bound. Past it the counts would be a floor rather than a count, which is
#: reported as unknown instead of as a number.
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024

#: Reported when the file was read, no counted marker was found, and it does carry
#: checklist lines this reader does not count.
UNCOUNTED_MARKERS_REASON = "uncounted_markers"


@dataclass(frozen=True)
class ProgressSnapshot:
    """Counted items, or the reason there is no count. Never both."""

    available: bool
    #: "absent" | "too_large" | "unreadable" | "uncounted_markers" when not available;
    #: None when it is.
    reason: str | None
    done: int | None
    in_progress: int | None
    todo: int | None
    #: The path that was consulted, as the UI names what it read (or did not find).
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "done": self.done,
            "in_progress": self.in_progress,
            "todo": self.todo,
            "source": self.source,
        }


def _strip_bullet(line: str) -> str:
    """The line with leading whitespace and one optional list bullet removed."""
    text = line.lstrip()
    for bullet in _BULLETS:
        if text.startswith(bullet):
            return text[len(bullet):].lstrip()
    # An ordered-list bullet: digits followed by "." or ")".
    digits = 0
    while digits < len(text) and text[digits].isdigit():
        digits += 1
    if digits > 0 and digits < len(text) and text[digits] in ".)":
        return text[digits + 1:].lstrip()
    return text


def count_progress_markers(text: str) -> dict[str, int]:
    """Count ✅ / 🔵 / ⬜ items in a snapshot's text.

    A marker counts only as the first token of its line (after an optional list
    bullet), and at most once per line. Prose that mentions a marker mid-sentence —
    which the snapshot's own explanatory text does — is not an item.
    """
    counts = {"done": 0, "in_progress": 0, "todo": 0}
    for raw in text.splitlines():
        head = _strip_bullet(raw)
        for marker, bucket in MARKERS.items():
            if head.startswith(marker):
                counts[bucket] += 1
                break
    return counts


def has_uncounted_checklist_lines(text: str) -> bool:
    """Whether the text carries checklist lines in a convention this reader does not count.

    Only Markdown task-list checkboxes (``- [ ]``, ``- [x]``, ``1. [X]``): the form this
    repository's own snapshot uses. It is deliberately a yes/no and not a count —
    deciding which of ✅ / 🔵 / ⬜ a checkbox means is the maintainer's call, not this
    reader's, and an unchecked box cannot even express "in progress". This exists so
    "no items counted" can be reported as *this file speaks another convention* instead
    of as *this project tracks nothing*.
    """
    for raw in text.splitlines():
        head = _strip_bullet(raw)
        # "[", one mark character, "]", then end of line or a space. The trailing rule
        # keeps a one-character Markdown link (``- [a](url)``) from reading as a box.
        if len(head) >= 3 and head[0] == "[" and head[2] == "]":
            if len(head) == 3 or head[3].isspace():
                return True
    return False


def read_progress_snapshot(root: Path) -> ProgressSnapshot:
    """Read and count one project's snapshot. Never raises: every failure is a
    reason the UI can render as unknown, because a 500 on the dashboard would take
    the whole cockpit down for a file that is optional by design."""
    source = SNAPSHOT_RELATIVE_PATH.as_posix()
    path = root / SNAPSHOT_RELATIVE_PATH
    unavailable = lambda reason: ProgressSnapshot(  # noqa: E731 - one shape, several call sites
        available=False, reason=reason, done=None, in_progress=None, todo=None, source=source,
    )
    # One stat, and only ``FileNotFoundError`` is absence. ``Path.exists()`` was used
    # here and it cannot make that distinction: it answers False both for nothing and
    # for a stat this process was not permitted to complete, so a snapshot under a
    # directory whose ACL denies traversal read as "this project keeps no snapshot" —
    # a settled fact about the project — instead of "it could not be read". A chained
    # ``is_symlink()`` did not recover it either, because that call fails the same way.
    probe = probe_path(path)
    if probe.absent:
        return unavailable("absent")
    if not probe.is_file():
        # Everything else present-or-unobservable: a directory, a device, a broken
        # symlink (recorded at the path, so not absence), or a path that could not be
        # stat'd at all. Unknown, and the UI says so rather than "no snapshot".
        return unavailable("unreadable")
    if probe.size is not None and probe.size > MAX_SNAPSHOT_BYTES:
        return unavailable("too_large")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return unavailable("unreadable")
    counts = count_progress_markers(text)
    if not any(counts.values()) and has_uncounted_checklist_lines(text):
        # Read fine, and it plainly tracks items — in markers this reader does not
        # count. Zeroes here would report the file as tracking nothing, which is a
        # false statement about the project rather than a missing number.
        return unavailable(UNCOUNTED_MARKERS_REASON)
    return ProgressSnapshot(
        available=True,
        reason=None,
        done=counts["done"],
        in_progress=counts["in_progress"],
        todo=counts["todo"],
        source=source,
    )
