"""A project's known defects, read from the project's own repository.

Why the files and not a control-plane table: ``.agora/decisions/0004`` and the sibling
:mod:`agora.projects.decisions` / :mod:`agora.projects.cr_log` docstrings. A defect's
authority is the maintainer's own file that travels with ``git clone`` and that an agent
in the worktree reads directly — the same reasoning ADR 0004 records for the CR Log and
the decision records, and known defects are a project-local narrative in exactly the
same way.

**The contract.**

- The source is ``<project root>/.agora/defects.yaml``. One fixed path, no search, no
  fallback — the reason is ``progress_snapshot``'s: with a fallback there is no way to
  prove "there is none", and "there is none" is the sentence this layer has to be able
  to say without inventing a number.
- The file is ``defects: [ ... ]``, each entry a mapping. ``id`` / ``title`` / ``status``
  are required; ``summary`` / ``milestone`` are optional.
- ``status`` is one of ``open`` / ``fixed``, matched as the first word at a word
  boundary (the ``decisions`` module's ``status_token`` rule). A value whose first word
  is neither is ``unrecognised`` — never silently ``open`` or ``fixed``, because a defect
  nobody has marked is not one anybody has resolved, and it must not vanish from the open
  count either way.
- An entry missing a required field is still **listed**, with ``readable=False`` — the
  same rule the decisions reader holds: dropping it would turn "there is a defect here I
  could not read" into "there is no such defect", the distinction this whole layer exists
  to preserve.

**The count is the open count.** The nav signal is "how many do you still owe", the same
as the CR Log's open count. ``fixed`` defects stay in the list (struck through, in the
UI) but are not in ``open_count``; ``unrecognised`` ones are not counted as open either
and travel as their own number, so a count is never shown as complete when some entries
could not be classified.

**reach vocabulary** is the siblings': ``absent`` / ``unreadable`` / ``too_large`` /
``malformed`` / ``duplicate_key``. When the source could not be read the fields are
``None``, never ``0`` and never an empty list — "could not read" and "there are none"
are two different things on screen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agora.fsprobe import probe_path
from agora.projects.importer import DuplicateKeyError, StrictLoader

#: The one file.
DEFECTS_RELATIVE_PATH = Path(".agora") / "defects.yaml"

#: A defects file is a handful of entries; anything larger is not one, and reading it to
#: find that out is work a UI request should not do. Matches the manifest bound in
#: workspace_tasks.
MAX_DEFECTS_BYTES = 256 * 1024

#: Status vocabulary, matched as the first word of the value at a word boundary — the
#: same shape decisions uses, so ``fixed (2026-08-27)`` reads as ``fixed`` and the prose
#: after it is kept for display.
STATUS_WORDS = ("open", "fixed")
_STATUS_HEAD = re.compile(r"^(" + "|".join(STATUS_WORDS) + r")\b", re.IGNORECASE)

#: Same as the sibling readers: never a synonym for a specific answer, always the third
#: one.
UNRECOGNISED = "unrecognised"


@dataclass(frozen=True)
class DefectRecord:
    """One defect entry. ``readable`` False means a required field was missing or the
    entry was not a mapping; only what could be read is populated."""

    #: The defect id as written (``D-1``). None only when the entry had none — then
    #: ``readable`` is False.
    id: str | None
    #: True when id / title / status were all present and usable. False keeps the entry
    #: visible without claiming fields it did not have.
    readable: bool
    title: str | None
    #: The status value as written, prose and all. None when unreadable or unstated.
    status: str | None
    #: One of STATUS_WORDS lowercased, or "unrecognised".
    status_token: str
    summary: str | None
    milestone: str | None

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "readable": self.readable,
            "title": self.title,
            "status": self.status,
            "status_token": self.status_token,
            "summary": self.summary,
            "milestone": self.milestone,
        }


@dataclass(frozen=True)
class DefectLog:
    """The defects, or the reason there are none to show."""

    available: bool
    #: "absent" | "unreadable" | "too_large" | "malformed" | "duplicate_key" when not
    #: available; None when it is.
    reason: str | None
    #: None when not available — not an empty tuple. A project with a defects file that
    #: lists nothing is a different statement from a project without one.
    records: tuple[DefectRecord, ...] | None
    #: How many entries read positively as ``open``. None when unavailable. The nav
    #: signal; ``fixed`` and ``unrecognised`` are not in it.
    open_count: int | None
    #: Entries whose status word was outside the vocabulary. Travels with the count so a
    #: consumer cannot show "N open" as complete while some entries went unclassified.
    unrecognised_count: int | None
    #: Entries that could not be read (missing a required field, or not a mapping). Same
    #: role as the decisions reader's unreadable_count.
    unreadable_count: int | None
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "records": None if self.records is None else [r.payload() for r in self.records],
            "open_count": self.open_count,
            "unrecognised_count": self.unrecognised_count,
            "unreadable_count": self.unreadable_count,
            "source": self.source,
        }


def _text_of(value: Any) -> str | None:
    """A usable string, or None. Empty and whitespace-only are None — a blank title is
    not a title, the same way workspace_tasks reads an empty ``goal:``."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_entry(entry: Any) -> DefectRecord:
    """One list item → a record. A non-mapping, or one missing id / title / status, is
    ``readable=False`` rather than dropped — and it keeps whatever fields *were* read, so
    a row missing only its id is still shown with its title, not blanked."""
    if not isinstance(entry, dict):
        return DefectRecord(
            id=None, readable=False, title=None, status=None,
            status_token=UNRECOGNISED, summary=None, milestone=None,
        )
    ident = _text_of(entry.get("id"))
    title = _text_of(entry.get("title"))
    status = _text_of(entry.get("status"))
    head = _STATUS_HEAD.match(status) if status is not None else None
    token = head.group(1).lower() if head else UNRECOGNISED
    summary = _text_of(entry.get("summary"))
    milestone = _text_of(entry.get("milestone"))
    if ident is None or title is None or status is None:
        # A required field is missing: not a fully-read record, but keep every field that
        # *was* present so the row stays as informative as it can be. Its status_token
        # stays UNRECOGNISED so it never lands in the open count while unreadable.
        return DefectRecord(
            id=ident, readable=False, title=title, status=status,
            status_token=UNRECOGNISED, summary=summary, milestone=milestone,
        )
    return DefectRecord(
        id=ident, readable=True, title=title, status=status,
        status_token=token, summary=summary, milestone=milestone,
    )


def read_defects(root: Path) -> DefectLog:
    """Read one project's known defects from its fixed path. Never raises."""
    return read_defects_file(
        root / DEFECTS_RELATIVE_PATH, DEFECTS_RELATIVE_PATH.as_posix()
    )


def read_defects_file(path: Path, source: str) -> DefectLog:
    """The same read, of a defects file that may live elsewhere (kept parallel to
    ``decisions.read_decisions_dir`` for the workspace-task reader's sake)."""
    unavailable = lambda reason: DefectLog(  # noqa: E731 - one shape, several exits
        available=False, reason=reason, records=None, open_count=None,
        unrecognised_count=None, unreadable_count=None, source=source,
    )
    probe = probe_path(path)
    if probe.absent:
        return unavailable("absent")
    if not probe.is_file():
        return unavailable("unreadable")
    if probe.size is not None and probe.size > MAX_DEFECTS_BYTES:
        return unavailable("too_large")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return unavailable("unreadable")
    try:
        # StrictLoader, shared with the project importer and workspace_tasks: a
        # duplicated key inside an entry would otherwise parse as the last one and drop
        # the earlier value silently.
        document = yaml.load(text, Loader=StrictLoader)
    except DuplicateKeyError:
        return unavailable("duplicate_key")
    except yaml.YAMLError:
        return unavailable("malformed")

    # An empty file (``None``) is a settled "no defects listed", not a malformed one —
    # the file exists and says nothing. A non-mapping top level, or a ``defects`` that is
    # not a list, is malformed: it is shaped like neither an empty file nor the contract.
    if document is None:
        return DefectLog(
            available=True, reason=None, records=(), open_count=0,
            unrecognised_count=0, unreadable_count=0, source=source,
        )
    if not isinstance(document, dict):
        return unavailable("malformed")
    raw = document.get("defects")
    if raw is None:
        # The mapping exists but names no defects list: the same settled nothing as an
        # empty file, not a malformed one.
        return DefectLog(
            available=True, reason=None, records=(), open_count=0,
            unrecognised_count=0, unreadable_count=0, source=source,
        )
    if not isinstance(raw, list):
        return unavailable("malformed")

    records = tuple(_read_entry(entry) for entry in raw)
    return DefectLog(
        available=True,
        reason=None,
        records=records,
        open_count=sum(1 for r in records if r.readable and r.status_token == "open"),
        unrecognised_count=sum(1 for r in records if r.readable and r.status_token == UNRECOGNISED),
        unreadable_count=sum(1 for r in records if not r.readable),
        source=source,
    )
