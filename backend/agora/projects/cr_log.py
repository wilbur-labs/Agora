"""A project's CR Log, read from the project's own repository.

**Why the file and not a table in the control-plane database.** Recorded in
``tasks/agora/adr/0004`` (claude-workspace, task:agora). The short form: a CR Log
stops a project's scope from drifting only because whoever works on the project is
made to see the OPEN rows — in the working model this product is a port of, a hook
injects them at the start of every session. Move the rows into a database and no
agent reads them any more; what is left is a list on a screen. So the authoritative
record is a file in the project's repository, it travels with a clone, and this
module only reads it.

**The contract, exactly.**

- The log is ``<project root>/.agora/development/CR-LOG.md``. One path, no search, no
  fallback — the same rule and the same reason as :mod:`agora.projects.progress_snapshot`:
  with a fallback, "this project keeps no CR Log" stops being provable, and that is
  the one sentence the UI has to be able to say without inventing rows.
- Inside it, **a CR row is a table row whose first cell is a CR id** (``CR-`` and
  digits). Nothing else identifies it. In particular the header row is never read:
  its words are decoration, they are written in whatever language the project is
  written in, and making them identifiers is how a mechanism breaks the day somebody
  translates a heading. The columns are **positional**, and that is the format:

      ``| id | date | source | description | kind | status | decision | propagated to |``

- A CR row that does not have exactly ``CR_LOG_COLUMNS`` cells makes the whole read
  ``malformed``, and the payload names the offending ids. It would be easy to skip
  such a row and report the others, and that is precisely the wrong thing: the
  columns carry meaning by position, so a row of another width has no trustworthy
  status, and the row dropped may be the OPEN one. A CR Log that quietly loses a CR
  is worse than one that refuses to render.

  **What that check actually catches, measured rather than assumed.** A GFM table
  normalises every body row to the width its *header* declares, so the parsed cell
  count can never disagree with its table: a short row is padded with empty cells at
  the end, and a **long row is truncated, its extra cells discarded**. The parsed
  count therefore catches a table whose column count is not the format — under a
  six-column header every status is read out of the wrong cell — but it is blind to a
  single over-long row, which arrives already trimmed to the right width with every
  column after the extra cell shifted by one. So the row's **source line is counted
  too**, and a row with more cells written than the format has is ``malformed``. A
  short row is not: its cells keep their positions, the missing ones read as empty,
  and a row with no status cell comes back ``unrecognised``, which is the honest
  answer and costs nothing else in the file.

- **A bare status token in a cell that is not the status cell makes the log
  ``malformed`` too**, because the most likely explanation is that the columns are
  not in the format's order — and a reordered table is the one input that can turn an
  ``OPEN`` row into ``open_count == 0`` with nothing anywhere reporting a problem.
  Since the header words are deliberately never read, this value check is the only
  thing standing between a reordered log and a screen that says "nothing is open".
  **Exactly what it does not cover**, stated rather than implied: a reordering that
  moves no bare status token out of place — swapping ``date`` and ``source``, say —
  is undetectable here and will be displayed as written. The check covers the
  reorderings that can lie about status, not reordering in general.

- **A CR id written into a table cell that is not part of a recognised row makes the
  log ``malformed``.** "This project has raised nothing", or a count that is simply too
  low, said over a file holding an OPEN row, is the worst thing this module can produce,
  and five different inputs reached it: an id column written second, an id written as a
  link, a split log whose second table nothing parsed in, a CR row that a misplaced
  delimiter turned into a header row, and a single such row added to a log that
  otherwise parses. Four rules were written and withdrawn trying to say *where* an id
  may hide; there is always another where. This rule is not about location — an id in a
  cell that is not part of a row this reader can show you is a row about to be lost,
  whatever made it unreadable.

  Cells of *recognised* rows are exempt, because that is where cross-references between
  rows live — and that is the only exemption. An earlier version also subtracted ids
  that appeared as a good row somewhere, which cancelled out a duplicate row written
  some other way: the arithmetic masked exactly the rows it was meant to find.

  Prose is exempt too: a log's own header saying "CR-3 moved to the tracker" is
  ordinary, and the version of this rule that policed the whole file text had to be
  withdrawn. So are block quotes and code fences — a ``CR-LOG.md`` that shows an example
  of a CR row is the file most likely to exist, and neither is a table row.

  **How an id is spotted inside a cell**, since this is the half that must not miss:
  the boundary is spelled out as "no ASCII identifier character before it, no digit
  after it" rather than written ``\b``, because ``\b`` separates word from non-word
  characters and **CJK characters are word characters** — ``見CR-2`` with no space had
  no boundary in front of it and went unreported. The cell is also folded first: NFKC,
  every Unicode dash, the handful of Cyrillic/Greek/Roman-numeral look-alikes for ``C``
  and ``R``, and the zero-width characters deleted — so ``ＣR-2``, a ``CR‑2`` pasted out
  of a document and a Cyrillic ``СR-2`` are all *reported* even though none is a valid
  id. That fold is an enumeration and says so at its definition: the standard library
  has no confusables table, so it covers what has been thought of. The strict pattern
  decides what a row is; this one decides what may not disappear quietly.

  **What it does not cover, stated**: a change request written outside a markdown table
  — as a list item, inside a raw HTML table, in a block quote or in a code fence — is
  not seen at all. The format is a table. Nor does it see an id whose *own text* is
  broken up by markup the block parser leaves as raw source (an id split across an
  emphasis boundary); what it matches is the cell's source, not a rendering of it.

  **What it costs**: a cell naming a CR that is not a row in this file reads as
  malformed. Measured against the real corpora before adopting it — this repository's
  own ``CR-LOG.md`` and the sixteen-row table in the workspace task that drives it both
  pass.

  **And what it costs, also stated.** A correctly ordered row whose *decision* cell is
  the bare word ``DONE`` is rejected as malformed, because from a position-only view
  that row and a Decision/Status swap are the same bytes — they cannot be told apart
  without reading the header words, which this format deliberately does not do. The
  trade is accepted in one direction on purpose: a false ``malformed`` names the row,
  is visible immediately, and is fixed by writing the decision as prose; a false
  "nothing is open" is silent and defeats the only mechanism this file exists to
  carry. Loud and wrong beats quiet and wrong here.
- Block quotes are not read. A quoted table is somebody else's log — the same
  exclusion, for the same reason, as the sibling snapshot reader.

**Status and kind are matched by value, and an unknown one is its own answer.**
``OPEN`` / ``DECIDED`` / ``DONE`` for status; ``bug`` / ``clarify`` / ``refine`` /
``creep`` / ``cut`` / ``defer`` for kind. A cell holding anything else is
``unrecognised`` — **never folded into "not open"**. Folding it in is the failure
this repository keeps producing: a check narrower than the claim it makes, which then
reads as proof. "Nothing is open here" and "one of these rows has a status I do not
understand" are different sentences and the UI must be able to say the second one.

**Unlike the progress snapshot, an unrecognised row does not make the read
unavailable**, and the difference is not an inconsistency. There, the numbers are
reduced to a percentage, so a partial count is a floor rendered as a fact. Here the
rows are *listed*: an unrecognised status is visible on its own row, next to its own
description, and nothing is hidden by reporting the rest. What this module still
refuses to do is report a bare ``open_count`` as if it were the whole story — it
reports the count of rows it could not classify beside it, always, so a consumer
cannot render the first number without having been handed the second.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from agora.fsprobe import probe_path

#: The one path. See the module docstring on why there is no search and no fallback.
CR_LOG_RELATIVE_PATH = Path(".agora") / "development" / "CR-LOG.md"

#: The read bound, matching the snapshot reader's. Past it the rows would be a prefix
#: of the log, and a prefix of a CR Log is indistinguishable from a CR Log in which
#: the open items happen to be at the end.
MAX_CR_LOG_BYTES = 2 * 1024 * 1024

#: A CR row is identified by this and by nothing else — not by which table it is in,
#: not by a header word. Anchored: a cell that merely *mentions* CR-3 is prose.
#:
#: `[0-9]` rather than `\d`, which in Python matches every Unicode decimal digit: with
#: `\d` a full-width `CR-２` was a *valid but different* id from `CR-2`, two rows that
#: look identical on screen. Not an id, and therefore reported by the scan below
#: rather than quietly accepted.
CR_ID_PATTERN = re.compile(r"^CR-[0-9]+$")

#: The same id, found inside a cell rather than anchored to the whole of one. Used
#: only to answer: does this unreadable row carry a change request?
#:
#: The boundaries are spelled out rather than written ``\b``, because ``\b`` is a
#: boundary between word and non-word characters and **CJK characters are word
#: characters** in Python's Unicode-aware regex. In a corpus written in Chinese and
#: Japanese that is not an edge case: ``見CR-2``, with no space, has no word boundary
#: in front of the id, and the row carrying it would have gone unreported — silently,
#: which is the one outcome this check exists to prevent. Only an ASCII identifier
#: character before it, or a digit after it, means this is part of some longer token.
_CR_ID_ANYWHERE = re.compile(r"(?<![A-Za-z0-9_-])CR-\d+(?![0-9])")

#: Everything the *suspicion* scan folds onto ASCII before looking for an id, and why
#: each group is here rather than left out:
#:
#: * **dashes** — taken from Unicode's own ``Pd`` category rather than a hand-list, plus
#:   the minus sign and the katakana prolonged sound mark, which are not ``Pd`` but are
#:   typed for a hyphen. NFKC does not fold these: ``CR‑2`` pasted out of a document
#:   keeps U+2011 and matched nothing at all.
#: * **look-alike letters** — Cyrillic ``С``/``Р``, Greek ``Ϲ``/``Ρ``, the Roman numeral
#:   ``Ⅽ``. NFKC folds none of them, because they are different letters; on screen they
#:   are the same two letters, and a row whose id begins with one is a row nobody can
#:   see is broken.
#: * **invisible characters** — zero-width space/joiner/non-joiner, the soft hyphen and
#:   the byte-order mark, deleted outright. They render as nothing and split an id in
#:   two for a matcher.
#:
#: This is an enumeration, not a general confusables table — the standard library has
#: none — so it is a list of what has been thought of, and says so.
_DASHES = {
    ord(chr(cp)): "-"
    for cp in range(0x0, 0x11000)
    if unicodedata.category(chr(cp)) == "Pd"
}
_DASHES.update({ord(c): "-" for c in "\u2212\u30fc"})
#: Applied **before** NFKC, which is not a detail: NFKC maps the Greek lunate sigma
#: ``Ϲ`` to ``Σ``, so folding it to ``C`` afterwards never fires. A normalisation that
#: runs first can destroy the very character the next step is looking for.
_LOOKALIKES = {
    ord("\u0421"): "C", ord("\u03f9"): "C", ord("\u216d"): "C",
    ord("\u0420"): "R", ord("\u03a1"): "R",
}
_INVISIBLE = {ord(c): None for c in "\u200b\u200c\u200d\u2060\ufeff\u00ad"}
_FOLD_AFTER = {**_DASHES, **_INVISIBLE}


def _confusables_folded(text: str) -> str:
    """A cell reduced to ASCII look-alikes, for the *suspicion* scan only.

    `CR_ID_PATTERN` stays strict: ``ＣR-2``, ``CR‑2`` and a Cyrillic ``СR-2`` are not
    valid ids and the rows carrying them are not recognised, which is right. What is not
    right is losing them in silence — so the scan that asks "is a change request hiding
    in this unreadable row" folds first. The strict reader decides what a row *is*; this
    decides what may not be dropped without saying so.
    """
    return unicodedata.normalize("NFKC", text.translate(_LOOKALIKES)).translate(_FOLD_AFTER)


#: The positional format. A CR row of another width is `malformed`.
CR_LOG_COLUMNS = 8
COLUMN_NAMES = (
    "id", "date", "source", "description",
    "kind", "status", "decision", "propagated_to",
)

#: Status vocabulary, compared after normalisation. Uppercase because that is what the
#: working model writes; the comparison is case-sensitive on purpose, so that an `open`
#: written in passing prose style is reported as unrecognised rather than guessed at.
STATUS_TOKENS: dict[str, str] = {"OPEN": "open", "DECIDED": "decided", "DONE": "done"}

#: The six kinds. `defer` is the one whose handling cannot be a sentence — see adr/0015
#: in claude-workspace — but this module only reports what the row says.
KIND_TOKENS: frozenset[str] = frozenset(
    {"bug", "clarify", "refine", "creep", "cut", "defer"}
)

#: Reported for a cell this module has no meaning for. Never a synonym for "closed" or
#: "no kind": it is the third answer, and it exists so that neither of the other two can
#: be asserted about a row nobody has classified.
UNRECOGNISED = "unrecognised"

#: The parser. CommonMark plus the one block rule this file needs. `gfm-like` would
#: have brought tables too, and also linkify, which is not installed and raises at
#: parse time — enabling the single rule keeps the dependency surface where the
#: sibling module already put it.
#:
#: Inline parsing is disabled for the same reason as in the snapshot reader: only
#: `token.content` is read, which the *block* parser fills with the cell's raw source,
#: so resolving emphasis and links would build children nothing here looks at.
_MARKDOWN = MarkdownIt("commonmark").enable("table")
_MARKDOWN.disable("inline")

#: Emphasis and code fences around a cell's raw text, stripped before a token is
#: compared. `**OPEN**` is OPEN. Applied only for matching; the text kept for display
#: is the cell as written.
_DECORATION = re.compile(r"^[\s*_`]+|[\s*_`]+$")


def _token_of(cell: str) -> str:
    """A cell's raw source reduced to the bare word it decorates, if it decorates one."""
    return _DECORATION.sub("", cell)


@dataclass(frozen=True)
class ChangeRequest:
    """One row of the log, as written, plus this module's reading of two of its cells."""

    id: str
    date: str
    source: str
    description: str
    kind: str
    status: str
    decision: str
    propagated_to: str
    #: "open" | "decided" | "done" | "unrecognised"
    status_token: str
    #: one of KIND_TOKENS, or "unrecognised"
    kind_token: str

    @property
    def is_open(self) -> bool:
        """True only for a row this module positively read as OPEN. An unrecognised
        status is not open and is not closed, and no caller may turn it into either by
        asking this twice."""
        return self.status_token == "open"

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "source": self.source,
            "description": self.description,
            "kind": self.kind,
            "status": self.status,
            "decision": self.decision,
            "propagated_to": self.propagated_to,
            "status_token": self.status_token,
            "kind_token": self.kind_token,
        }


@dataclass(frozen=True)
class CRLog:
    """The rows, or the reason there are none to show. Never both."""

    available: bool
    #: "absent" | "too_large" | "unreadable" | "malformed" when not available;
    #: None when it is.
    reason: str | None
    #: None when not available — not an empty tuple. An available log with no rows is a
    #: project that keeps a CR Log and has raised nothing, which is a different statement
    #: from every unavailable one and has to stay distinguishable from them.
    rows: tuple[ChangeRequest, ...] | None
    #: Rows positively read as OPEN.
    open_count: int | None
    #: Rows whose status cell this module could not read. Travels beside `open_count` at
    #: every call site so that "0 open" can never be rendered without the number that
    #: says how much of the log that claim actually covers.
    unrecognised_status_count: int | None
    #: The ids of the rows to fix, when `reason` is "malformed". Named rather than
    #: counted: the fix is in one row and the person needs to know which.
    #:
    #: ``None`` when the file could not be read at all — absent, too large, unreadable.
    #: An empty list there would say "no row needs fixing", which is a finding about a
    #: file nobody could open. Same collapse, same correction, as the sibling reader's
    #: ``unnumbered_files``; the two were caught one review round apart.
    malformed_ids: tuple[str, ...] | None
    #: The path consulted, so the UI names what it read or did not find.
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "rows": None if self.rows is None else [row.payload() for row in self.rows],
            "open_count": self.open_count,
            "unrecognised_status_count": self.unrecognised_status_count,
            "malformed_ids": None if self.malformed_ids is None else list(self.malformed_ids),
            "source": self.source,
        }


def _source_cell_count(line: str) -> int:
    r"""How many cells the author *wrote* on this line, before the parser normalised it.

    Needed because the parser hands back a row already trimmed or padded to its header's
    width, which hides the one case that silently destroys data: a row with more cells
    than the format, whose extra cell shifts every column after it and whose last cell
    is dropped. Counting the source line is the only place that difference is visible.

    A leading and a trailing pipe are the ordinary GFM row form and are not cells. An
    escaped ``\|`` is content, not a separator — the parser treats it that way and so
    must this, or a description containing a pipe would be reported as the wrong shape.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return len(re.split(r"(?<!\\)\|", stripped))


def _table_rows(text: str) -> list[tuple[list[str], int | None, bool]]:
    """Every row of every table, as ``(raw cells, cells written in the source, header)``.

    The second element is ``None`` when the parser gave no source location for the
    row, in which case the source count simply cannot be checked — unknown, and not
    quietly treated as agreement.

    Header rows are flagged rather than dropped: their content is still never read, but
    a CR row that a misplaced delimiter turned into a header must be visible to the
    caller's check, or its whole table vanishes with it. A CR row is still identified by its id cell alone, so a
    file whose log is preceded by some other table needs no rule to disambiguate, and
    one whose log is split across two tables still reads as one log. Which table a row
    came from is deliberately not tracked: two rounds of review were spent on rules
    about which tables count, and the rule that finally held is not about tables at all.
    """
    lines = text.splitlines()
    rows: list[tuple[list[str], int | None, bool]] = []
    depth_quoted = 0
    in_head = False
    cells: list[str] | None = None
    written: int | None = None
    for token in _MARKDOWN.parse(text, {}):
        kind = token.type
        if kind == "blockquote_open":
            depth_quoted += 1
        elif kind == "blockquote_close":
            depth_quoted -= 1
        elif kind == "thead_open":
            in_head = True
        elif kind == "thead_close":
            in_head = False
        elif kind == "tr_open":
            cells = []
            # `map` is the token's [start, end) line range. A body row is one line, so
            # its first line is the row as the author typed it. Absent map -> unknown.
            written = None
            if token.map and 0 <= token.map[0] < len(lines):
                written = _source_cell_count(lines[token.map[0]])
        elif kind == "tr_close":
            if cells is not None and not depth_quoted:
                # Header rows are carried too, flagged. They are still never *read* —
                # their words are decoration — but a CR row turned into a header by a
                # misplaced delimiter line has to be visible to the check below, or the
                # whole table disappears with it.
                rows.append((cells, written, in_head))
            cells = None
            written = None
        elif kind == "inline" and cells is not None:
            # One inline token per cell, empty ones included — measured, not assumed:
            # an empty cell emits `inline` with empty content rather than nothing at
            # all, so no padding is needed and none is done. Were that not so, the
            # cell would vanish and every column after it would shift left by one,
            # which is exactly how a `defer` row with an empty "propagated to" — the
            # row whose emptiness is the thing worth noticing — would come back
            # reading as some other row entirely.
            cells.append(token.content)
    return rows


def read_cr_log(root: Path) -> CRLog:
    """Read one project's CR Log. Never raises: every failure is a reason the UI can
    render, because a 500 here would take a project's whole view down over a file that
    is optional by design."""
    source = CR_LOG_RELATIVE_PATH.as_posix()
    path = root / CR_LOG_RELATIVE_PATH
    unavailable = lambda reason, ids=(): CRLog(  # noqa: E731 - one shape, several call sites
        available=False, reason=reason, rows=None, open_count=None,
        unrecognised_status_count=None, malformed_ids=(tuple(ids) if ids else None), source=source,
    )
    # One stat, and only a definite FileNotFoundError is absence. `Path.exists()`
    # answers False both for nothing and for a stat this process was not allowed to
    # finish, which would turn "could not look" into the settled fact "this project
    # keeps no CR Log".
    probe = probe_path(path)
    if probe.absent:
        return unavailable("absent")
    if not probe.is_file():
        return unavailable("unreadable")
    if probe.size is not None and probe.size > MAX_CR_LOG_BYTES:
        return unavailable("too_large")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return unavailable("unreadable")

    parsed = _table_rows(text)
    rows: list[ChangeRequest] = []
    malformed: list[str] = []

    def is_row(cells: list[str]) -> bool:
        return bool(cells) and bool(CR_ID_PATTERN.match(_token_of(cells[0])))

    # **A table row this reader cannot read, which carries a CR id, is a row about to
    # be lost — so the log is malformed rather than short.**
    #
    # Five narrower rules preceded this one, and each was the previous one's example
    # rather than its class:
    #
    #   "a row is a row whose first cell is an id"   an id column written second lost every
    #                                                row and the log read as empty.
    #   "…unless nothing at all was recognised"      one row written some other way — a
    #                                                linked id — vanished inside a log that
    #                                                parsed, its count unchanged.
    #   "…in a table holding a recognised row"       a split log's second table, none of
    #                                                whose rows parsed, was ignored whole.
    #                                                (That is what this scan now prevents;
    #                                                nothing is scoped to a table any more.)
    #   "…or mentioning an id anywhere"              a CR row turned into a header row by a
    #                                                misplaced delimiter still vanished.
    #   "every id in the file"                       a log's own prose saying "CR-3 moved to
    #                                                the tracker" poisoned the whole log.
    #   "…that is not also a recognised row"         a duplicate row for an id that already
    #                                                had a good row was cancelled out by it
    #                                                and vanished — the subtraction masked
    #                                                exactly the rows it was meant to find.
    #
    # The first four were rules about *where* an id may hide, and there is always
    # another where; the last two tried to decide *which* ids matter, and cancelled out
    # the rows they were looking for. What is left asks neither: a row this reader
    # cannot read, with a CR id in it, is a row about to be lost — whatever made it
    # unreadable and whichever id it carries. Cells of recognised rows are exempt, and
    # that single exemption is what lets cross-references between rows live.
    #
    # **What it does not cover, stated**: a change request written outside a markdown
    # table — as a list item, or inside a raw HTML table — is not seen. The format is a
    # table, and a rule that also policed prose is the one that had to be withdrawn.
    #
    # **What it costs**: a cell naming a CR that is not a row in this file — one carried
    # to another project's tracker — reads as malformed. Measured against the real
    # corpora before adopting it: this repository's own CR-LOG.md and the sixteen-row
    # table in the workspace task that drives it both pass.
    missing = [
        found
        for cells, _, header in parsed
        if header or not is_row(cells)
        for cell in cells
        for found in _CR_ID_ANYWHERE.findall(_confusables_folded(cell))
    ]
    if missing:
        # Deduplicated, order kept: a file repeating one id has one thing to fix.
        return unavailable("malformed", dict.fromkeys(missing))

    for cells, written, header in parsed:
        if header:
            continue
        if not cells or not CR_ID_PATTERN.match(_token_of(cells[0])):
            continue
        cr_id = _token_of(cells[0])
        if len(cells) != CR_LOG_COLUMNS:
            malformed.append(cr_id)
            continue
        if written is not None and written > CR_LOG_COLUMNS:
            # The parser already trimmed this row to the header's width, so the cells
            # in hand look right while one of them is gone and everything after the
            # extra one has shifted. Only the source line still shows that.
            malformed.append(cr_id)
            continue
        fields = {name: cell.strip() for name, cell in zip(COLUMN_NAMES, cells)}
        misplaced = [
            name for name, value in fields.items()
            if name != "status" and _token_of(value) in STATUS_TOKENS
        ]
        if misplaced:
            # A bare OPEN/DECIDED/DONE somewhere other than the status column. The
            # header words are never read, so this is the only signal that the columns
            # may not be in the format's order — and that reordering is the one input
            # able to turn an OPEN row into `open_count == 0` silently.
            malformed.append(cr_id)
            continue
        status_token = STATUS_TOKENS.get(_token_of(fields["status"]), UNRECOGNISED)
        kind_raw = _token_of(fields["kind"])
        rows.append(
            ChangeRequest(
                **fields,
                status_token=status_token,
                kind_token=kind_raw if kind_raw in KIND_TOKENS else UNRECOGNISED,
            )
        )
    if malformed:
        # Deliberately not "the rows that did parse". See the module docstring: a row
        # only reaches this width because its *table* declares it, so the table's
        # column meanings are all in question — and under a narrow header the parser
        # has already discarded the cells past its width.
        return unavailable("malformed", malformed)

    return CRLog(
        available=True,
        reason=None,
        rows=tuple(rows),
        open_count=sum(1 for row in rows if row.is_open),
        unrecognised_status_count=sum(
            1 for row in rows if row.status_token == UNRECOGNISED
        ),
        malformed_ids=(),
        source=source,
    )
