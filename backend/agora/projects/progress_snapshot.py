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
- Two checklist conventions are counted, and a file is read in exactly one of them:

  ``markers``      ``✅`` done, ``🔵`` in progress, ``⬜`` not started. A marker
                   counts only when it is the first thing on its source line, so the
                   same character used mid-sentence in the prose around the checklist
                   is not counted as an item. One item per line — the first wins. No
                   list bullet is required: this convention is the working model's
                   own and has never asked for one.
  ``checkboxes``   Markdown task-list boxes, ``[x]``/``[X]`` done and ``[ ]`` not
                   done, first thing in a **list item** — because that is what a GFM
                   task-list marker is. A bare ``[x] release`` paragraph is not an
                   item; paired with an ``[x]: https://…`` definition further down it
                   is a link. See "coarser, not incomplete" below for why this is
                   countable at all and what it costs.

  **Blocks are decided by a CommonMark parser, not by scanning lines.** Whether a
  line is an item is a *block* question — is this fence open, are these four spaces
  code or a nested item, does this paragraph continue the list item above it — and
  hand-rolling those rules produced a steady run of miscounts, each fix uncovering
  the next. The worst was self-inflicted: fence tracking added to stop a documented
  example from being counted let a stray indented ``~~~`` open a fence that never
  closed, and a file full of items read as tracking nothing at all — this module's
  own worst failure, manufactured by the code meant to prevent it. So the block
  structure comes from ``markdown-it-py`` and this module only counts.

  What that settles, at no further cost: fenced and indented code are never counted
  (a snapshot documenting its own convention is not counted as having done the
  example), ``-[x]`` with no separating space is the paragraph it really is, a tab
  indent is measured in columns, headings are not items (``## ✅ Phase one`` is a
  section title), and neither are block quotes (a quoted checklist is somebody
  else's list — true before by accident, since ``>`` is not a bullet, and on purpose
  now).

- Every outcome this can have is distinguishable in the payload, because the UI has
  to distinguish them:

  =====================  ========================================================
  ``absent``             there is nothing at the snapshot path. The bar renders
                         empty. It does **not** render 0%.
  ``too_large``          the file is past ``MAX_SNAPSHOT_BYTES``. Counting a prefix
                         would yield a floor, and a floor rendered as a percentage
                         is a wrong number, so this is unknown.
  ``unreadable``         something is at the path and it could not be read, **or**
                         the path could not be observed at all: permissions on the
                         file or on a directory above it, undecodable bytes, or an
                         entry that is not a regular file (a directory, a broken
                         symlink, a device). Unknown, and never zero — "present but
                         unusable" is not "there is no snapshot", and neither is
                         "could not tell". Only a definite ``FileNotFoundError``
                         is absence; see :mod:`agora.fsprobe`.
  ``uncounted_markers``  the file was read and it carries checklist lines whose
                         marks this reader does not count — a box holding anything
                         but ``x``, ``X`` or a space (``[/]``, ``[-]``, ``[~]``:
                         real conventions elsewhere, with no agreed meaning here).
                         Counting the rest would report a floor, so this is
                         unknown. The honest statement is "this file uses marks
                         this reader does not count", not "this snapshot tracks
                         nothing".
  ``mixed_conventions``  the file was read and carries **both** markers and
                         checkboxes. Unknown; see "a file speaks one convention".
  available              counts are exact for the convention named by
                         ``convention`` — ``markers``, ``checkboxes``, or ``none``
                         for a file that lists no item of either, whose counts are
                         then three zeros, which is "no number to report", not 0%.
  =====================  ========================================================

  When it is not available all three counts are ``None`` rather than 0. A zero
  cannot be rendered as a percentage by accident if it is not in the payload.

  ``convention`` is always set on an available payload, so a consumer seeing it
  missing knows it is talking to a server older than the field — and that such a
  server's counts came from a rule that silently preferred markers in a mixed file,
  i.e. may be a floor. That is why the field is never left empty to mean "nothing
  special": the display has to be able to tell "no items" from "cannot vouch for
  these counts", and it can only do that if exactly one of them arrives as nothing.

**Checkboxes are coarser, not incomplete.** The objection to counting them is that a
checkbox has no form for 🔵 in progress, so the three buckets cannot be filled. That
is true and it is not a reason to report nothing, because the missing bucket is not a
missing *item*: an item a human would call in progress is written as an unchecked
box, so ``done + not-done`` still partitions the whole checklist. The total is exact,
and ``done / total`` — the only percentage this module's decision reports — is exact
with it. What a checkbox file cannot support is the *split* of not-done into "in
progress" and "not started".

So a checkbox file is available, with ``in_progress`` reported as ``None``: unknown,
not zero. Zero would assert that nothing is in flight, which is precisely the claim
the file cannot make. This is the one place ``available`` is true and a count is
``None``, and ``convention`` is what tells a reader to expect it — which also means
a display built against the older payload (three integers whenever available) fails
closed on this one rather than rendering a wrong number: it sees a count it cannot
use and says so.

``todo`` then means *not done*, and may include work in flight. A UI reading a
checkbox snapshot must word it that way and must not draw an in-progress segment —
not even a zero-width one.

**A file speaks one convention.** A file carrying both is ``mixed_conventions``:
unknown. This reverses the earlier rule that markers won in a mixed file, and the
reversal is only because that rule's premise expired. When checkboxes were not
counted at all, preferring markers cost nothing. Now that they are countable data,
preferring markers silently drops real items and reports a floor as a percentage —
the same wrong number ``too_large`` exists to prevent. And the two readings cannot
be merged either: whether a block of boxes is another section of the same checklist
or an unrelated list is not decidable from the text, and guessing is inventing.
Agora's own snapshot is pure checkboxes (no line-leading marker at all), so the
strict rule costs nothing on the file that motivated counting them.

Pure apart from the one bounded file read, so the counting rules are testable
without a project on disk.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.common.utils import normalizeReference

from agora.fsprobe import probe_path

#: Where a project keeps its resume progress snapshot, relative to the project root.
SNAPSHOT_RELATIVE_PATH = Path(".agora") / "development" / "PROGRESS.md"

#: Marker -> bucket. Exactly the three the recorded decision names; anything else is
#: not an item, because guessing a fourth bucket is inventing data.
MARKERS: dict[str, str] = {"✅": "done", "🔵": "in_progress", "⬜": "todo"}

#: Checkbox mark -> bucket. ``not_done`` is deliberately not called ``todo``: under
#: this convention it also holds whatever is in flight, and naming it "todo" here is
#: how that would quietly become "not started" upstream.
CHECKBOX_MARKS: dict[str, str] = {"x": "done", "X": "done", " ": "not_done"}

#: The read bound. Past it the counts would be a floor rather than a count, which is
#: reported as unknown instead of as a number.
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024

#: The two conventions a counted file can be written in, as reported in the payload.
CONVENTION_MARKERS = "markers"
CONVENTION_CHECKBOXES = "checkboxes"

#: Reported for a file that was read and carries no item of either convention. Its own
#: value rather than a null, because a *missing* ``convention`` has to keep meaning
#: exactly one thing — a server older than the field — and a display cannot tell the
#: two apart if both arrive as nothing.
CONVENTION_NONE = "none"

#: Reported when the file carries checklist lines whose marks this reader does not
#: count, so any count of the rest would be a floor.
UNCOUNTED_MARKERS_REASON = "uncounted_markers"

#: Reported when the file carries both conventions, so neither reading is the file's.
MIXED_CONVENTIONS_REASON = "mixed_conventions"


@dataclass(frozen=True)
class ProgressSnapshot:
    """Counted items, or the reason there is no count. Never both."""

    available: bool
    #: "absent" | "too_large" | "unreadable" | "uncounted_markers" |
    #: "mixed_conventions" when not available; None when it is.
    reason: str | None
    done: int | None
    #: ``None`` on a checkbox snapshot even though it is available — the convention
    #: cannot express work in flight, and 0 would claim there is none.
    in_progress: int | None
    #: Under ``checkboxes`` this is *not done*, which may include work in flight.
    todo: int | None
    #: The path that was consulted, as the UI names what it read (or did not find).
    source: str
    #: Which convention the counts were read in: "markers", "checkboxes", or "none"
    #: for a readable file that lists no item of either. None only when there are no
    #: counts at all — so for a consumer, a missing/None ``convention`` on an
    #: available payload can only mean a server that predates this field.
    convention: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "done": self.done,
            "in_progress": self.in_progress,
            "todo": self.todo,
            "source": self.source,
            "convention": self.convention,
        }


#: The block parser. CommonMark, no plugins: the task-list extension is deliberately
#: not used, because it resolves a checkbox against the document's link reference
#: definitions — one stray ``[x]: https://…`` line makes every ``- [x]`` in the file a
#: link, and counting would then silently drop the whole checklist.
#:
#: Inline parsing is disabled because nothing here reads it. Only ``token.content``
#: is used — the block parser's own raw source for the block — so emphasis, links and
#: code spans would be resolved into children this module never looks at. Turning the
#: rule off is a third of the parse on a real snapshot, and it removes work rather
#: than caching its result: a cache keyed on the file's timestamp would be one more
#: thing that can be wrong about a number, in a module whose whole subject is numbers
#: that must not be wrong.
_MARKDOWN = MarkdownIt("commonmark")
_MARKDOWN.disable("inline")


def _text_blocks(text: str) -> tuple[list[tuple[str, bool]], set[str]]:
    """``(block source, is the start of a list item)`` for each text block.

    Why a parser and not a line scan. The rules that decide whether a line is an item
    are block rules — a fence is open or closed, four spaces are code or a nested
    item, a paragraph continues a list item or ends it — and hand-rolling them
    produced a steady stream of miscounts, each fix uncovering the next: an indented
    ``~~~`` opening a fence that never closed read a file full of items as tracking
    nothing at all. That is the failure this module exists to prevent, manufactured by
    the code meant to prevent it. The parser knows these rules; this function only
    asks it which blocks hold text.

    What falls out for free, rather than as another special case:

    * fenced and indented code never reach here — the parser hands them back as their
      own token types, and their content is not inline text. A snapshot documenting
      its own convention is no longer counted as having done the example.
    * a list item is one *because the parser says so*, so ``-[x]`` with no space is
      the paragraph it really is, and a tab indent is measured in columns.
    * headings are excluded: ``## ✅ Phase one`` is a section title, not an item.
    * block quotes are excluded: a quoted checklist is somebody else's list. This was
      already true by accident (``>`` is not a bullet) and is now on purpose.
    """
    blocks: list[tuple[str, bool]] = []
    env: dict[str, Any] = {}
    depth_quoted = 0
    in_heading = False
    starts_item = False
    for token in _MARKDOWN.parse(text, env):
        kind = token.type
        # Container depth first, and as its own chain: these token types also have to
        # fall through to the `starts_item` rules below. Folding them into one
        # if/elif meant an *empty* block quote — ``- >``, which opens and closes with
        # no inline token in between — left `starts_item` set, and the next paragraph
        # in the file inherited it and was counted as a finished item.
        if kind == "blockquote_open":
            depth_quoted += 1
        elif kind == "blockquote_close":
            depth_quoted -= 1
        elif kind == "heading_open":
            in_heading = True
        elif kind == "heading_close":
            in_heading = False

        if kind == "list_item_open":
            starts_item = True
        elif kind == "inline":
            if not depth_quoted and not in_heading:
                blocks.append((token.content, starts_item))
            starts_item = False
        elif kind != "paragraph_open":
            # Anything else between the item and its text means the item does not
            # begin with a paragraph — a nested list, a fence, a block quote, a rule.
            starts_item = False
    # The document's link reference definitions, normalised the way markdown-it
    # normalises them, so a label can be looked up by the same key it was stored
    # under. Collected by a *block* rule, so they survive inline parsing being off.
    return blocks, set(env.get("references") or ())


def count_progress_markers(text: str) -> dict[str, int]:
    """Count ✅ / 🔵 / ⬜ items in a snapshot's text.

    A marker counts only as the first thing on its source line, and at most once per
    line. Prose that mentions a marker mid-sentence — which the snapshot's own
    explanatory text does — is not an item.

    Every line of a text block is offered, not only its first: two marker lines with
    no blank between them are one Markdown paragraph and two items, which is how these
    checklists are actually written. No list bullet is required, unlike the boxes
    below: this convention is the working model's own and has never demanded one, and
    a bare ``✅ shipped`` line is unambiguous in a way a bare ``[x] shipped`` is not.
    """
    return _count_markers(_text_blocks(text)[0])


def _count_markers(blocks: list[tuple[str, bool]]) -> dict[str, int]:
    counts = {"done": 0, "in_progress": 0, "todo": 0}
    for content, _starts_item in blocks:
        for line in content.splitlines():
            head = line.lstrip()
            for marker, bucket in MARKERS.items():
                if head.startswith(marker):
                    counts[bucket] += 1
                    break
    return counts


#: Zero-width joiner. The codepoint after one continues the cluster it joins.
_ZWJ = "‍"

#: Unicode general categories that continue a character rather than adding one, by
#: *category* rather than by combining class. ``unicodedata.combining()`` was used
#: here and it returns 0 for several real continuations — the keycap enclosure U+20E3
#: (``Me``) in ``#️⃣``, spacing marks (``Mc``), and the supplementary variation
#: selectors (``Mn``) — so each of those made a one-character mark look like a
#: multi-character label and be skipped without a word. The category test covers all
#: three, and it subsumes the ordinary variation selectors, which are ``Mn`` too.
_COMBINING_CATEGORIES = frozenset({"Mn", "Me", "Mc"})


def _is_one_mark(mark: str) -> bool:
    """Whether ``mark`` is a single visual character (one grapheme cluster).

    Combining marks, variation selectors (``✔️`` is U+2714 U+FE0F), skin-tone
    modifiers, ZWJ sequences and regional-indicator pairs (a flag) are all one
    character written as several codepoints. Assuming a mark was a single *codepoint*
    meant ``[✔️]`` did not even look like a box: it was skipped in silence, and the
    file's remaining boxes were then reported as a complete count. A partial count
    presented as a whole one is this module's worst outcome and the hardest of them
    to notice, so the recognised set is kept wide enough that nothing plausible falls
    through it into silence.

    **This is an approximation of UAX#29, and the residual error is deliberately
    one-directional.** It is *wider* than the real segmentation in two known ways,
    both raised in review and both accepted rather than fixed:

    * every ``Mc`` counts as a continuation, while UAX#29's SpacingMark set is not
      quite all of ``Mc`` — so ``~`` followed by U+102B reads here as one mark.
    * a ZWJ continues unconditionally, while GB11 continues only between
      Extended_Pictographic characters — so ``~`` ZWJ ``a`` reads here as one mark.

    In both, a two-character *label* is taken for a one-character mark, and the file
    is reported **unknown** — the bar is hidden and says why. Narrowing them needs a
    real segmenter (``unicodedata`` exposes neither SpacingMark nor
    Extended_Pictographic), and getting a hand-rolled version of either slightly
    wrong turns them into the *other* error: a mark read as a label, skipped in
    silence, and the file's remaining items reported as its whole. That direction
    produces a wrong percentage, and this module trades a hidden bar for a wrong
    number every time the choice comes up.

    A cluster built some way this does not recognise likewise reads as a
    multi-character label and is ignored — the right answer for the shape this
    actually guards, ``- [some label] text``, which must not cost a file its bar.
    """
    if not mark:
        return False
    # A flag is two regional indicators. Handled inside the loop rather than as a
    # length-2 special case, because ``🇺🇸️`` — the pair plus a variation selector — is
    # still one character, and a rule that insisted on exactly two codepoints fell
    # through to the loop and skipped the mark in silence.
    base_is_regional = "\U0001f1e6" <= mark[0] <= "\U0001f1ff"
    regional_taken = False
    after_zwj = False
    for char in mark[1:]:
        if after_zwj:
            # Whatever a ZWJ joined is part of this character, and it may join again.
            after_zwj = char == _ZWJ
            continue
        if char == _ZWJ:
            after_zwj = True
            continue
        if unicodedata.category(char) in _COMBINING_CATEGORIES:
            continue
        if "\U0001f3fb" <= char <= "\U0001f3ff":
            continue
        if "\U000e0020" <= char <= "\U000e007f":
            continue
        if base_is_regional and not regional_taken and "\U0001f1e6" <= char <= "\U0001f1ff":
            regional_taken = True  # the flag's second half, and only one of them
            continue
        return False
    # A mark that ends on a ZWJ joins nothing, so it is not a finished character.
    return not after_zwj


def _checkbox_bucket(mark: str, references: set[str]) -> str | None:
    """Which bucket a box's mark counts into, or None when it is not a box at all.

    ``x`` / ``X`` / space are the counted marks, and they are decided **before**
    anything else: a document containing one ``[x]: https://…`` definition makes
    markdown-it render every ``- [x]`` in it as a link, so consulting the references
    for those would silently drop the entire checklist. GFM takes the task marker
    first too.

    For any other mark there is no such catastrophe, and the references settle a real
    ambiguity: ``- [~] details`` beside a ``[~]: https://…`` definition is a shortcut
    reference link, not a checklist mark, and counting it as an unknown box hid a
    perfectly countable marker file behind ``mixed_conventions``. Defined label →
    not a box.

    Otherwise: a mark that is not a word character is a checklist mark with no agreed
    meaning here (``[/]``, ``[-]``, ``[✓]``) — a real item, uncounted, so the file is
    reported unknown rather than counted short. A word character is not a checklist
    mark at all but an undefined reference label (``[a]``, ``[1]``; ``isalnum()`` is
    true for CJK too, which is the wanted answer, since no convention uses a word
    character as its mark). Judged on the mark's base character, so a variation
    selector cannot change the answer.
    """
    if mark in CHECKBOX_MARKS:
        return CHECKBOX_MARKS[mark]
    if not _is_one_mark(mark):
        # More than one visual character is a link label (``[some label] text``), and
        # no checklist convention has a multi-character mark.
        return None
    if normalizeReference(mark) in references:
        return None
    # A *single codepoint* word character is a label character. The length test is
    # load-bearing: ``1️⃣`` is a digit followed by two continuations, and judging it by
    # its base character alone called it a label and skipped it without a word — an
    # item dropped in silence, with the file's remaining boxes then reported as the
    # whole of it. A mark carrying continuations is a symbol, whatever it starts with.
    if len(mark) == 1 and mark.isalnum():
        return None
    return "uncounted"


def count_checkbox_items(text: str) -> dict[str, int]:
    """Count Markdown task-list boxes in a snapshot's text.

    A box counts only as the first thing in a **list item**, because that is what a
    GFM task-list marker is. The list item comes from the parser, so ``-[x]`` with no
    separating space is the paragraph it really is, and a tab-indented line is placed
    by column rather than by character count.

    Requiring the list item is also what settles the shortcut-reference ambiguity
    without guessing: a bare ``[x] release`` line paired with an ``[x]: https://…``
    definition is a link, and it used to count as a finished item. The inverse fix —
    asking the parser whether the text resolved to a link — was **rejected after
    testing it**: one ``[x]: url`` definition anywhere makes markdown-it render every
    ``- [x]`` in the document as a link, so that rule would silently drop the entire
    checklist. GFM itself takes the task marker before link resolution, and so does
    this.

    Returns ``done`` / ``not_done`` and, separately, ``uncounted`` — a box holding a
    mark this reader has no meaning for. Those are kept apart rather than folded into
    either bucket, because a caller has to be able to tell "counted the whole file"
    from "counted part of it": a partial count is a floor, and a floor is not a
    percentage.
    """
    return _count_boxes(*_text_blocks(text))


def _count_boxes(blocks: list[tuple[str, bool]], references: set[str]) -> dict[str, int]:
    counts = {"done": 0, "not_done": 0, "uncounted": 0}
    for content, starts_item in blocks:
        if not starts_item:
            continue
        head = content.splitlines()[0] if content else ""
        if not head.startswith("["):
            continue
        # The mark is whatever sits between the brackets, found by *looking* for the
        # closing one rather than assuming it is the third character. Assuming that
        # made a two-codepoint mark invisible instead of uncounted, which turns a
        # partial count into a silently complete-looking one.
        close = head.find("]")
        if close < 2:
            continue
        rest = head[close + 1:]
        # End of line, or whitespace. That is what keeps an inline link
        # (``- [a](url)``) and a reference definition (``- [a]: url``) out.
        if rest and not rest[0].isspace():
            continue
        bucket = _checkbox_bucket(head[1:close], references)
        if bucket is not None:
            counts[bucket] += 1
    return counts


def read_progress_snapshot(root: Path) -> ProgressSnapshot:
    """Read and count one project's snapshot. Never raises: every failure is a
    reason the UI can render as unknown, because a 500 on the dashboard would take
    the whole cockpit down for a file that is optional by design."""
    source = SNAPSHOT_RELATIVE_PATH.as_posix()
    path = root / SNAPSHOT_RELATIVE_PATH
    unavailable = lambda reason: ProgressSnapshot(  # noqa: E731 - one shape, several call sites
        available=False, reason=reason, done=None, in_progress=None, todo=None,
        source=source, convention=None,
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

    # One parse, both counts. The two public helpers each parse for themselves, which
    # is right for a caller holding only text and wrong here: this is the hot path, on
    # the dashboard a task switch waits for.
    blocks, references = _text_blocks(text)
    markers = _count_markers(blocks)
    boxes = _count_boxes(blocks, references)
    has_markers = any(markers.values())
    has_boxes = any(boxes.values())

    if has_markers and has_boxes:
        # Both conventions in one file. Neither reading is the file's own, and
        # picking one drops the other's items into a floor.
        return unavailable(MIXED_CONVENTIONS_REASON)
    if has_boxes:
        if boxes["uncounted"]:
            # It plainly tracks items, in marks with no agreed meaning here. Counting
            # the ones this reader does understand would report a floor.
            return unavailable(UNCOUNTED_MARKERS_REASON)
        return ProgressSnapshot(
            available=True,
            reason=None,
            done=boxes["done"],
            # Unknown, not zero: this convention has no form for work in flight, so a
            # 0 here would be this reader asserting something the file never said.
            in_progress=None,
            todo=boxes["not_done"],
            source=source,
            convention=CONVENTION_CHECKBOXES,
        )
    return ProgressSnapshot(
        available=True,
        reason=None,
        done=markers["done"],
        in_progress=markers["in_progress"],
        todo=markers["todo"],
        source=source,
        # A readable file with no item of either convention is available with three
        # zeros, and says so as its own convention value. Reporting nothing here would
        # collide with the payload of a server too old to have the field, and those
        # two have to stay distinguishable: one is "this file lists nothing", the
        # other is "this server's counts cannot be trusted to be complete".
        convention=CONVENTION_MARKERS if has_markers else CONVENTION_NONE,
    )
