"""A project's decision records (ADRs), read from the project's own repository.

Why the files and not a control-plane table: ``tasks/agora/adr/0004`` in
claude-workspace, and the sibling :mod:`agora.projects.cr_log` docstring. The reach
vocabulary here is the one those two established — ``absent`` / ``unreadable``, and a
count is never 0 when the answer is "could not tell".

**The contract.**

- The records are ``<project root>/.agora/decisions/NNNN-*.md``. One directory, no
  search, no fallback.
- A file is a record because **its name begins with digits**. Anything else in the
  directory is not a record and is reported by name in ``unnumbered_files`` rather
  than dropped in silence — otherwise a decision saved under the wrong name would
  make the log quietly shorter, and "you have four decisions" would be a false
  statement about a directory holding five files.
- Each record's number comes from its filename, its title from the first ``#``
  heading, and its status and date from the **metadata block**: everything before the
  first ``##`` heading.

**The metadata labels are identifiers, and that is a real difference from the CR
Log.** There the columns are positional and the header words are never read, so
translating them costs nothing. Here there is no position to fall back on, and the
alternative — recognising a *value* rather than a label, the way statuses are matched
in the CR Log — was tried against the sixteen real records in claude-workspace's own
``adr/`` and does not survive contact with them:

    - **Status**: accepted
    - **Status**: accepted；其中 source 分流一条已被 [[0006-…]] 取代（…一整段中文…）
    - **Status**: superseded by [[0009-english-machinery-shareable-tool]]（…）
    **Status**: Accepted (2026-08-31) — supersedes [[0011-data-repo-without-remote]]

Four shapes in one directory: with and without a list bullet, lower and upper case,
a bare token and a token trailed by a paragraph of prose. A value-first rule has to
guess where the token ends, and guessing is what this module is written to avoid. So
``Status`` and ``Date`` are magic words, exactly as the working model's own
"Never translate — identifiers" rule already treats a handful of section headings.

**The status token is the first word of the value, matched at a word boundary**, so
all four shapes above read as ``accepted`` / ``superseded`` and the prose after them
is kept as written for display. A value whose first word is not in the vocabulary is
``unrecognised`` — never silently ``accepted``, because a record nobody has marked is
not a decision anybody has taken.

**A record that cannot be read still appears**, with its number and filename and
``readable=False``. Dropping it would turn "there is a decision here I could not open"
into "there is no such decision", which is the distinction this whole layer exists to
preserve.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from agora.fsprobe import probe_path

#: The one directory.
DECISIONS_RELATIVE_PATH = Path(".agora") / "decisions"

#: Per-record read bound. A prefix of an ADR could hold its Context and not its
#: Status, which is the one field a reader is here for.
MAX_RECORD_BYTES = 512 * 1024

#: A file is a record because its name starts with digits. The number is what the
#: working model numbers decisions by, and it is what "superseded by NNNN" points at.
RECORD_NAME_PATTERN = re.compile(r"^(\d+)[-_. ]")

#: Metadata labels. Identifiers — see the module docstring for why these, unlike the
#: CR Log's column headers, cannot be made positional.
LABEL_STATUS = "status"
LABEL_DATE = "date"

#: Status vocabulary, matched as the first word of the value at a word boundary.
STATUS_WORDS = ("proposed", "accepted", "superseded", "deprecated", "rejected")
_STATUS_HEAD = re.compile(r"^(" + "|".join(STATUS_WORDS) + r")\b", re.IGNORECASE)

#: The successor a superseded record points at. **Four attempts** got this wrong,
#: each one fixing its own example and leaving the class open — which is the pattern
#: `lessons/measured-not-reasoned` exists for, so the fifth changes the judgement
#: rather than widening the pattern:
#:
#:   first number in the value    `Superseded (2026-09-05) by [[0012-new]]`      -> 2026
#:   first number after "by"      `Superseded by version 2 of [[0012-new]]`      -> None
#:   first link in the value      `Superseded (see [[0003-history]]) by …`       -> 0003
#:   regex-masked link spans      `Superseded (see [[0003-history-by-2026)…`     -> 2026
#:
#: Every one of those failed on **where the word "by" is**, and a regex cannot answer
#: that: masking `\[\[[^\]]*\]\]` is defeated by an unclosed link and by a nested
#: bracket. Bracket *depth* answers it exactly, so `_successor_of` counts brackets and
#: only a "by" at depth zero opens the clause. An unclosed bracket then yields no
#: clause at all — None, "this record says it was superseded and does not say by
#: what", which is the honest answer and not an invented pointer.
#: Full-width brackets count toward the depth as well. They are not wiki-link
#: syntax, but this corpus is written in Chinese and Japanese, where
#: `［［0003-history-by-2026］］` is an ordinary thing to type — and with only ASCII
#: counted, the "by" inside such a slug read as depth zero and the slug's own year
#: came back as the successor.
#: The bracket characters, and the scanner that finds them, **from one definition**.
#: Widening the pattern alone is how the sixth instance of this repository's
#: recurring defect was produced, inside the fix for the fifth: the regex learned
#: about `［` while the loop below still compared against `"["`, so a full-width
#: bracket was neither an opener nor a closer and fell through to the branch that
#: treats a match as the "by" clause. Deriving the pattern from the same strings
#: the loop tests against removes the possibility rather than the instance.
_BRACKET_OPEN = "[［"
_BRACKET_CLOSE = "]］"
_BRACKET_OR_BY = re.compile(
    "[" + re.escape(_BRACKET_OPEN) + "]|[" + re.escape(_BRACKET_CLOSE) + "]|\\bby\\b",
    re.IGNORECASE,
)

#: In the clause's tail: a wiki link anywhere (that is how the corpus writes it), or a
#: bare number **immediately** after "by". The second is deliberately tight — a loose
#: scan turned `by the 2026 review` into the successor 2026.
_SUPERSEDED_LINK = re.compile(r"\[\[\s*(\d{3,4})")
_SUPERSEDED_NEAR = re.compile(r"^[\s:,;：，、—–-]*(\d{3,4})\b")

#: An ISO date anywhere in the Date value. Not "the whole value must be a date": the
#: real records write `2026-08-31` plain, and one writes it inside parentheses.
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

#: Same as the sibling readers: never a synonym for a specific answer, always the
#: third one.
UNRECOGNISED = "unrecognised"

#: The record number repeated at the head of its own title, which the UI would
#: otherwise render twice ("0013 · 0013 — Work archive"). The separator set is what
#: the sixteen records in claude-workspace's `adr/` actually use — `0001.` and
#: `0013 —` both occur — rather than the one form the template shows. A number with no
#: separator after it is left alone: that is a title that begins with a number.
_TITLE_NUMBER = re.compile(r"^\s*\d+\s*[.．、:：\-—–]\s+")

#: Label text stripped of its decoration before comparison: `- **Status**:` is
#: `status`. Leading list punctuation is included because a record may or may not use
#: a bullet — both shapes exist in the corpus this was measured against.
_LABEL_DECORATION = re.compile(r"^[\s*_`\-+>]+|[\s*_`:]+$")

_MARKDOWN = MarkdownIt("commonmark")
_MARKDOWN.disable("inline")


@dataclass(frozen=True)
class DecisionRecord:
    """One ADR file. ``readable`` False means only ``number`` and ``filename`` are known."""

    number: int
    filename: str
    readable: bool
    #: The first `#` heading with any leading `NNNN.` stripped. None when unreadable.
    title: str | None
    #: The Status value as written, prose and all. None when unreadable or unstated.
    status: str | None
    #: One of STATUS_WORDS lowercased, or "unrecognised".
    status_token: str
    #: The number this record says it was superseded by, when it says so.
    superseded_by: str | None
    #: An ISO date from the Date field. None when the record does not state one —
    #: unknown, and never today's date.
    date: str | None

    def payload(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "filename": self.filename,
            "readable": self.readable,
            "title": self.title,
            "status": self.status,
            "status_token": self.status_token,
            "superseded_by": self.superseded_by,
            "date": self.date,
        }


@dataclass(frozen=True)
class DecisionLog:
    """The records, or the reason there are none to show."""

    available: bool
    #: "absent" | "unreadable" when not available; None when it is.
    reason: str | None
    #: None when not available — not an empty tuple. A project with a decisions
    #: directory and nothing in it is a different statement from a project without one.
    records: tuple[DecisionRecord, ...] | None
    #: Records present but unopenable. Travels with the list so a consumer cannot show
    #: a count of decisions without the number saying how many it could not read.
    unreadable_count: int | None
    #: Files in the directory that are not named as records, by name. ``None`` — not
    #: an empty tuple — when the directory could not be listed. Whether anything is
    #: misplaced in there is then unknown, and an empty list would be this module
    #: answering a question nobody was able to look at.
    unnumbered_files: tuple[str, ...] | None
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "records": None if self.records is None else [r.payload() for r in self.records],
            "unreadable_count": self.unreadable_count,
            "unnumbered_files": None if self.unnumbered_files is None else list(self.unnumbered_files),
            "source": self.source,
        }


def _successor_of(value: str) -> str | None:
    """The record this one says replaced it, or None if it does not say.

    The word "by" only opens the clause when it is **outside every bracket**, counted
    rather than pattern-matched: a link slug can contain it (`[[0003-history-by-2026]]`),
    brackets nest, and a link can be left unclosed. Each of those defeated a regex that
    tried to mask link spans, and each produced a *wrong* successor rather than none —
    a pointer between two records that reads as a fact and leads somewhere else.
    """
    depth = 0
    for match in _BRACKET_OR_BY.finditer(value):
        token = match.group(0)
        if token in _BRACKET_OPEN:
            depth += 1
        elif token in _BRACKET_CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0:
            # Only a "by" can reach here — the scanner matches nothing else — and it
            # is the same two strings above that make that true.
            tail = value[match.end():]
            found = _SUPERSEDED_LINK.search(tail) or _SUPERSEDED_NEAR.match(tail)
            return found.group(1) if found else None
    return None


def _unquoted_headings(tokens: list[Any]) -> set[str]:
    """The heading tags a record uses **as its own structure**, quotes excluded.

    Quoted headings are somebody else's document. Letting one set this record's
    metadata boundary is the same mistake, one level up, as reading a quoted
    ``Status:`` as this record's status.
    """
    tags: set[str] = set()
    depth_quoted = 0
    for token in tokens:
        if token.type == "blockquote_open":
            depth_quoted += 1
        elif token.type == "blockquote_close":
            depth_quoted -= 1
        elif token.type == "heading_open" and not depth_quoted:
            tags.add(token.tag)
    return tags


def _metadata_and_title(text: str) -> tuple[str | None, dict[str, str]]:
    """``(title, {label: value})`` from the record's metadata block.

    The block is bounded structurally rather than by a named section, so a record may
    write its metadata as a bullet list or as bare paragraphs and both are read. Which
    boundary applies depends on what the record has, and the cases are the record of
    four separate defects found in review:

    * **it has its own ``##``** — the block ends at the first *unquoted* one. Ending it
      at any heading below ``h1`` was narrower: metadata grouped under a
      ``### Metadata`` subheading is still before the first ``##`` and was dropped.
    * **no unquoted ``##`` but some heading** — *any* heading ends it, quoted included.
      Ignoring quoted headings here left a record whose only heading was
      ``> ## Example`` with no boundary at all, and its body prose became its status.
    * **no heading at all to stop at** — the metadata is at most **one top-level
      block**: the block after the title, or the first when there is no title. This
      applies only in that case; making it unconditional was stricter than the
      contract and cut off metadata sitting after a quoted block or a `###`
      subheading in a record that did have its own `##`. Counting only
      paragraph and list *closes* was narrower than "one block": a thematic break, an
      HTML block or a fence in between stopped nothing, and a record with no title
      never started counting, so in each case a later line beginning ``Status:``
      became the record's status.
    * headings **before** the title do not end anything — a stray ``## Preamble`` above
      the ``#`` used to discard the title and the metadata both.

    What is deliberately not attempted: separating metadata from prose *inside* one
    block. A record whose first block is a paragraph beginning ``Status:`` is read as
    stating a status, because from the text alone it does.
    """
    tokens = list(_MARKDOWN.parse(text, {}))
    headings = _unquoted_headings(tokens)
    boundary = "h2" if "h2" in headings else None
    has_title = "h1" in headings

    title: str | None = None
    fields: dict[str, str] = {}
    in_title = False
    title_done = not has_title
    stop = False
    depth_quoted = 0
    blocks = 0
    for token in tokens:
        if token.type == "blockquote_open":
            depth_quoted += 1
        elif token.type == "blockquote_close":
            depth_quoted -= 1

        if (
            boundary is None
            and title_done
            and token.level == 0
            and token.nesting >= 0
            and token.type != "inline"
        ):
            # Only when there is no `##` to stop at. Applying it unconditionally was
            # *stricter* than the contract — the mirror of the usual defect — and it
            # cut off a record whose metadata sat after a quoted block or a `###`
            # subheading but before its real `## Context`.
            #
            # Counted from the title rather than from the top of the file: counting
            # from the top let a stray `### Preamble` above the `#` eat the allowance,
            # so the record's real metadata was stopped as the third block.
            blocks += 1
            if blocks > 1:
                stop = True

        if token.type == "heading_open":
            quoted = depth_quoted > 0
            if token.tag == "h1" and not quoted:
                in_title = True
            elif title_done and (boundary is None or (token.tag == boundary and not quoted)):
                stop = True
        elif token.type == "heading_close":
            if in_title:
                title_done = True
            in_title = False
        elif token.type == "inline":
            if depth_quoted:
                # A quoted line is somebody else's record. Without this a
                # `> Status: accepted` in the preamble marks as taken a decision
                # nobody has taken. Both sibling readers exclude block quotes.
                continue
            if in_title:
                if title is None:
                    title = _TITLE_NUMBER.sub("", token.content).strip()
                continue
            if stop or not title_done:
                continue
            for line in token.content.splitlines():
                label, sep, value = line.partition(":")
                if not sep:
                    continue
                key = _LABEL_DECORATION.sub("", label).strip().lower()
                if key in (LABEL_STATUS, LABEL_DATE) and key not in fields:
                    fields[key] = value.strip()
    return title, fields


def _read_record(path: Path, number: int) -> DecisionRecord:
    unreadable = DecisionRecord(
        number=number, filename=path.name, readable=False, title=None, status=None,
        status_token=UNRECOGNISED, superseded_by=None, date=None,
    )
    probe = probe_path(path)
    if not probe.is_file():
        # Absent, a directory, or unobservable. Either way this record's fields are
        # unknown; the record itself is known to exist because the listing named it.
        return unreadable
    if probe.size is not None and probe.size > MAX_RECORD_BYTES:
        return unreadable
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return unreadable

    title, fields = _metadata_and_title(text)
    status = fields.get(LABEL_STATUS)
    head = _STATUS_HEAD.match(status or "")
    token = head.group(1).lower() if head else UNRECOGNISED
    superseded_by = None
    if token == "superseded":
        superseded_by = _successor_of(status or "")
    date_value = _ISO_DATE.search(fields.get(LABEL_DATE, ""))
    return DecisionRecord(
        number=number,
        filename=path.name,
        readable=True,
        title=title,
        status=status,
        status_token=token,
        superseded_by=superseded_by,
        date=date_value.group(1) if date_value else None,
    )


def read_decisions(root: Path) -> DecisionLog:
    """Read one project's decision records. Never raises."""
    source = DECISIONS_RELATIVE_PATH.as_posix()
    directory = root / DECISIONS_RELATIVE_PATH
    unavailable = lambda reason: DecisionLog(  # noqa: E731 - one shape, two call sites
        available=False, reason=reason, records=None, unreadable_count=None,
        unnumbered_files=None, source=source,
    )
    probe = probe_path(directory)
    if probe.absent:
        return unavailable("absent")
    if not probe.is_dir():
        return unavailable("unreadable")
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        # Present but not listable — permissions, most often. Not "no decisions".
        return unavailable("unreadable")

    records: list[DecisionRecord] = []
    unnumbered: list[str] = []
    for entry in entries:
        matched = RECORD_NAME_PATTERN.match(entry.name)
        if not matched:
            unnumbered.append(entry.name)
            continue
        records.append(_read_record(entry, int(matched.group(1))))
    records.sort(key=lambda r: (r.number, r.filename))
    return DecisionLog(
        available=True,
        reason=None,
        records=tuple(records),
        unreadable_count=sum(1 for r in records if not r.readable),
        unnumbered_files=tuple(unnumbered),
        source=source,
    )
