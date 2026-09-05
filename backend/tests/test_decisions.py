"""The project's decision records, read from its own repository.

What these pin, beyond the reach vocabulary the sibling readers established: a record
whose status nobody wrote is not "accepted", a file that could not be opened is still
a decision that exists, and a file in the directory that is not named as a record is
reported rather than dropped — otherwise "you have four decisions" would be a false
statement about a directory holding five files.

The status cases are taken from the four shapes that actually occur in
claude-workspace's own ``adr/``, which is the corpus this reader was measured against:
with and without a list bullet, upper and lower case, a bare token and a token trailed
by a paragraph of prose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agora.projects.decisions import (
    DECISIONS_RELATIVE_PATH,
    MAX_RECORD_BYTES,
    UNRECOGNISED,
    read_decisions,
)


def _write(root: Path, name: str, text: str) -> Path:
    path = root / DECISIONS_RELATIVE_PATH / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _record(title: str = "0001. A decision", status: str = "accepted", date: str = "2026-09-05") -> str:
    return (
        f"# {title}\n\n"
        f"- **Status**: {status}\n"
        f"- **Date**: {date}\n\n"
        "## Context\n\nwhy\n\n## Decision\n\nwhat\n"
    )


# --- reach --------------------------------------------------------------------


def test_no_decisions_directory_reports_absent(tmp_path):
    log = read_decisions(tmp_path)
    assert (log.available, log.reason) == (False, "absent")
    assert log.records is None
    assert log.unreadable_count is None


def test_a_file_where_the_directory_should_be_is_unreadable(tmp_path):
    path = tmp_path / DECISIONS_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a directory", encoding="utf-8")
    log = read_decisions(tmp_path)
    assert (log.available, log.reason) == (False, "unreadable")


def test_an_empty_directory_is_available_with_no_records(tmp_path):
    # A project that keeps a decisions directory and has recorded nothing is a
    # different statement from a project without one.
    (tmp_path / DECISIONS_RELATIVE_PATH).mkdir(parents=True)
    log = read_decisions(tmp_path)
    assert log.available is True
    assert log.records == ()
    assert log.unreadable_count == 0


def test_a_directory_that_cannot_be_listed_is_unreadable_not_empty(tmp_path, monkeypatch):
    (tmp_path / DECISIONS_RELATIVE_PATH).mkdir(parents=True)

    def refuse(self):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "iterdir", refuse)
    log = read_decisions(tmp_path)
    assert (log.available, log.reason) == (False, "unreadable")
    assert log.records is None


# --- what counts as a record --------------------------------------------------


def test_records_are_found_by_a_leading_number(tmp_path):
    _write(tmp_path, "0002-second.md", _record("0002. Second"))
    _write(tmp_path, "0001-first.md", _record("0001. First"))
    log = read_decisions(tmp_path)
    assert [r.number for r in log.records] == [1, 2]
    assert [r.title for r in log.records] == ["First", "Second"]


def test_an_unnumbered_file_is_named_not_dropped(tmp_path):
    # Dropping it would make the log quietly shorter than the directory.
    _write(tmp_path, "0001-first.md", _record())
    _write(tmp_path, "README.md", "# How this directory works\n")
    log = read_decisions(tmp_path)
    assert [r.number for r in log.records] == [1]
    assert log.unnumbered_files == ("README.md",)


def test_the_record_number_repeated_in_the_title_is_not_shown_twice(tmp_path):
    # Both separators occur in the real corpus: "0001." and "0013 —".
    _write(tmp_path, "0013-archive.md", _record("0013 — Work archive"))
    _write(tmp_path, "0001-first.md", _record("0001. First"))
    log = read_decisions(tmp_path)
    assert [r.title for r in log.records] == ["First", "Work archive"]


def test_a_title_that_merely_begins_with_a_number_keeps_it(tmp_path):
    # No separator after the digits, so they are part of the sentence, not a repeated
    # record number. "45k lines stay frozen" is a real title in this project.
    _write(tmp_path, "0001-x.md", _record("45k lines stay frozen"))
    assert read_decisions(tmp_path).records[0].title == "45k lines stay frozen"


def test_only_the_leading_number_is_stripped_not_one_inside_the_title(tmp_path):
    _write(tmp_path, "0001-x.md", _record("0001. 2026 roadmap: freeze 45k lines"))
    assert read_decisions(tmp_path).records[0].title == "2026 roadmap: freeze 45k lines"


# --- status -------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, token",
    [
        ("proposed", "proposed"),
        ("accepted", "accepted"),
        ("deprecated", "deprecated"),
        ("rejected", "rejected"),
        # The messy shapes from the real corpus: a token trailed by a paragraph, and
        # a capitalised token with a date after it.
        ("accepted；其中 source 分流一条已被 [[0006-…]] 取代（…）", "accepted"),
        ("Accepted (2026-08-31) — supersedes [[0011-data-repo]]", "accepted"),
    ],
)
def test_the_status_token_is_the_first_word_of_the_value(tmp_path, status, token):
    _write(tmp_path, "0001-x.md", _record(status=status))
    record = read_decisions(tmp_path).records[0]
    assert record.status_token == token
    # …and the value is kept as written, prose and all.
    assert record.status == status


def test_a_status_nobody_wrote_is_unrecognised_not_accepted(tmp_path):
    # A record nobody has marked is not a decision anybody has taken.
    _write(tmp_path, "0001-x.md", "# 0001. Untitled decision\n\n## Context\n\nwhy\n")
    record = read_decisions(tmp_path).records[0]
    assert record.status is None
    assert record.status_token == UNRECOGNISED


def test_an_unknown_status_word_is_unrecognised(tmp_path):
    _write(tmp_path, "0001-x.md", _record(status="under discussion"))
    assert read_decisions(tmp_path).records[0].status_token == UNRECOGNISED


def test_a_superseded_record_carries_the_number_that_replaced_it(tmp_path):
    _write(tmp_path, "0008-lang.md", _record(status="superseded by [[0009-english-machinery]]（同日翻盘）"))
    record = read_decisions(tmp_path).records[0]
    assert (record.status_token, record.superseded_by) == ("superseded", "0009")


def test_a_superseded_record_that_names_no_successor_says_so(tmp_path):
    _write(tmp_path, "0008-lang.md", _record(status="superseded"))
    record = read_decisions(tmp_path).records[0]
    assert (record.status_token, record.superseded_by) == ("superseded", None)


def test_only_a_superseded_record_reports_a_superseder(tmp_path):
    # An accepted record whose prose happens to mention another number is not
    # superseded by it.
    _write(tmp_path, "0009-x.md", _record(status="accepted (supersedes [[0008-language-boundary]])"))
    record = read_decisions(tmp_path).records[0]
    assert (record.status_token, record.superseded_by) == ("accepted", None)


def test_the_word_status_in_the_prose_is_not_metadata(tmp_path):
    # The metadata block ends at the first section heading; after it, "Status:" is
    # ordinary text.
    _write(
        tmp_path,
        "0001-x.md",
        "# 0001. A decision\n\n- **Status**: accepted\n\n"
        "## Context\n\nThe old Status: rejected line is quoted here.\n",
    )
    assert read_decisions(tmp_path).records[0].status_token == "accepted"


def test_metadata_written_without_a_bullet_is_read(tmp_path):
    # Two of the real records write it as a bare paragraph.
    _write(
        tmp_path,
        "0013-x.md",
        "# 0013 — Work archive\n\n**Status**: Accepted (2026-08-31)\n\n## Context\n\nwhy\n",
    )
    record = read_decisions(tmp_path).records[0]
    assert record.status_token == "accepted"


# --- date ---------------------------------------------------------------------


def test_the_date_is_read_from_the_date_field(tmp_path):
    _write(tmp_path, "0001-x.md", _record(date="2026-09-05"))
    assert read_decisions(tmp_path).records[0].date == "2026-09-05"


def test_a_record_with_no_date_field_reports_none_rather_than_a_guess(tmp_path):
    # Two real records state their date only inside the Status value. Digging it out
    # of there would be this module inventing a field the format did not fill.
    _write(
        tmp_path,
        "0014-x.md",
        "# 0014 — Something\n\n**Status**: Accepted (2026-08-31)\n\n## Context\n\nwhy\n",
    )
    assert read_decisions(tmp_path).records[0].date is None


# --- unreadable records -------------------------------------------------------


def test_a_record_too_large_to_read_still_appears(tmp_path):
    # "There is a decision here I could not open" must not become "there is no such
    # decision".
    _write(tmp_path, "0001-huge.md", _record() + "x" * (MAX_RECORD_BYTES + 1))
    log = read_decisions(tmp_path)
    record = log.records[0]
    assert (record.number, record.filename) == (1, "0001-huge.md")
    assert record.readable is False
    assert (record.title, record.status) == (None, None)
    assert record.status_token == UNRECOGNISED
    assert log.unreadable_count == 1


def test_an_undecodable_record_still_appears(tmp_path):
    path = tmp_path / DECISIONS_RELATIVE_PATH / "0001-bytes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"# 0001. \xff\xfe\n")
    log = read_decisions(tmp_path)
    assert log.records[0].readable is False
    assert log.unreadable_count == 1


def test_a_directory_named_like_a_record_is_unreadable_not_a_decision(tmp_path):
    (tmp_path / DECISIONS_RELATIVE_PATH / "0001-a-directory.md").mkdir(parents=True)
    log = read_decisions(tmp_path)
    assert log.records[0].readable is False
    assert log.unreadable_count == 1


def test_the_unreadable_count_travels_with_the_list(tmp_path):
    _write(tmp_path, "0001-ok.md", _record())
    _write(tmp_path, "0002-huge.md", _record() + "x" * (MAX_RECORD_BYTES + 1))
    payload = read_decisions(tmp_path).payload()
    assert len(payload["records"]) == 2
    # A consumer cannot render "2 decisions" without having been handed this.
    assert payload["unreadable_count"] == 1


def test_an_unavailable_payload_has_no_zeroes_in_it(tmp_path):
    payload = read_decisions(tmp_path).payload()
    assert payload["records"] is None
    assert payload["unreadable_count"] is None
    # None, not []. This assertion used to read `== []`, which endorsed the very
    # collapse the module is written to prevent: "nothing is misplaced in there" is a
    # finding, and nobody was able to look.
    assert payload["unnumbered_files"] is None


# --- regressions from review round 1 -----------------------------------------


def test_a_date_in_the_status_is_not_mistaken_for_the_successor(tmp_path):
    # "the first three-or-four digit run in the value" read 2026 out of a date and
    # reported it as the decision that replaced this one -- a pointer that reads as a
    # fact and leads somewhere wrong.
    _write(tmp_path, "0011-x.md", _record(status="Superseded (2026-09-05) by [[0012-new]]"))
    record = read_decisions(tmp_path).records[0]
    assert (record.status_token, record.superseded_by) == ("superseded", "0012")


def test_a_quoted_status_does_not_mark_an_unmarked_decision_as_taken(tmp_path):
    _write(
        tmp_path,
        "0001-x.md",
        "# 0001. X\n\n> Status: accepted\n\n## Context\n\nquoted from somewhere else\n",
    )
    record = read_decisions(tmp_path).records[0]
    assert record.status is None
    assert record.status_token == UNRECOGNISED


def test_metadata_under_a_subheading_is_still_metadata(tmp_path):
    # It is before the first `##`, which is what the contract says the block is.
    _write(
        tmp_path,
        "0001-y.md",
        "# 0001. Y\n\n### Metadata\n\nStatus: accepted\n\n## Context\n\nwhy\n",
    )
    assert read_decisions(tmp_path).records[0].status_token == "accepted"


def test_an_unlistable_directory_reports_unknown_misplaced_files_not_none_found(tmp_path):
    # Whether anything is misplaced in there is unknown, and an empty list would be
    # this module answering a question nobody could look at.
    (tmp_path / DECISIONS_RELATIVE_PATH).mkdir(parents=True)

    def refuse(self):
        raise PermissionError(13, "denied")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "iterdir", refuse)
    try:
        payload = read_decisions(tmp_path).payload()
    finally:
        monkeypatch.undo()
    assert payload["reason"] == "unreadable"
    assert payload["unnumbered_files"] is None


# --- regressions from review round 2 -----------------------------------------


def test_the_successor_comes_from_the_link_not_the_first_number_after_by(tmp_path):
    # "digits after `by`" was still not the successor: this shape yielded 2026.
    _write(tmp_path, "0011-a.md", _record(status="Superseded by the 2026 review: [[0012-new]]"))
    assert read_decisions(tmp_path).records[0].superseded_by == "0012"


def test_a_number_between_by_and_the_link_does_not_hide_the_successor(tmp_path):
    # `\D*?` cannot cross a digit, so this shape yielded None.
    _write(tmp_path, "0011-b.md", _record(status="Superseded by version 2 of [[0012-new]]"))
    assert read_decisions(tmp_path).records[0].superseded_by == "0012"


def test_a_bare_number_after_by_is_still_read_when_there_is_no_link(tmp_path):
    _write(tmp_path, "0011-c.md", _record(status="superseded by 0012"))
    assert read_decisions(tmp_path).records[0].superseded_by == "0012"


def test_a_record_with_no_section_headings_does_not_promote_its_prose_to_metadata(tmp_path):
    # Ending the block only at `##` was right for a `### Metadata` subheading and
    # wrong here: with no `##` anywhere, the whole body became metadata and a line of
    # context prose became the record's status.
    _write(
        tmp_path,
        "0001-c.md",
        "# 0001. C\n\n### Context\n\nStatus: accepted is what the legacy endpoint returned.\n",
    )
    assert read_decisions(tmp_path).records[0].status_token == UNRECOGNISED


def test_a_quoted_section_heading_does_not_end_the_metadata_block(tmp_path):
    # Skipping quoted *inline text* was narrower than "block quotes are not read": a
    # quoted `> ## Example` ended the block and hid this record's own real status.
    _write(
        tmp_path,
        "0001-d.md",
        "# 0001. D\n\n> ## Example\n> Status: rejected\n\nStatus: accepted\n\n## Context\n\nwhy\n",
    )
    record = read_decisions(tmp_path).records[0]
    assert record.status_token == "accepted"


def test_metadata_under_a_subheading_still_works_when_a_real_section_exists(tmp_path):
    _write(
        tmp_path,
        "0001-e.md",
        "# 0001. E\n\n### Metadata\n\nStatus: accepted\n\n## Context\n\nwhy\n",
    )
    assert read_decisions(tmp_path).records[0].status_token == "accepted"


# --- regressions from review round 3 -----------------------------------------


def test_the_successor_is_named_after_by_and_never_before_it(tmp_path):
    # Trying the link first fixed the previous example and left the class open: a link
    # appearing *before* the clause won. Three attempts, three different wrong answers,
    # so the clause is located first and only its tail is searched.
    _write(tmp_path, "0011-d.md", _record(status="Superseded (see [[0003-history]]) by [[0012-new]]"))
    assert read_decisions(tmp_path).records[0].superseded_by == "0012"


@pytest.mark.parametrize(
    "status, expected",
    [
        ("superseded by [[0009-slug]]（同日翻盘）", "0009"),
        ("Superseded (2026-09-05) by [[0012-new]]", "0012"),
        ("Superseded by the 2026 review: [[0012-new]]", "0012"),
        ("Superseded by version 2 of [[0012-new]]", "0012"),
        ("Superseded (see [[0003-history]]) by [[0012-new]]", "0012"),
        ("superseded by 0012", "0012"),
        ("superseded", None),
    ],
)
def test_every_superseded_shape_seen_so_far(tmp_path, status, expected):
    """One table rather than one test each, so a future change to the extraction has
    to keep all of them — the previous versions each passed their own example."""
    _write(tmp_path, "0011-e.md", _record(status=status))
    assert read_decisions(tmp_path).records[0].superseded_by == expected


def test_a_record_whose_only_heading_is_quoted_gets_the_strict_boundary(tmp_path):
    # Ignoring quoted headings is right when the record has its own `##`. When it has
    # none, doing so left no boundary at all and the body prose became the status.
    _write(
        tmp_path,
        "0001-f.md",
        "# 0001. F\n\n> ## Example\n> Status: rejected\n\n"
        "Context says:\nStatus: accepted is what the legacy endpoint returned.\n",
    )
    record = read_decisions(tmp_path).records[0]
    assert record.status is None
    assert record.status_token == UNRECOGNISED


def test_a_quoted_heading_still_does_not_end_a_record_that_has_its_own_sections(tmp_path):
    # The other half of the same rule, kept in the same file so the two cannot drift.
    _write(
        tmp_path,
        "0001-g.md",
        "# 0001. G\n\n> ## Example\n> Status: rejected\n\nStatus: accepted\n\n## Context\n\nwhy\n",
    )
    assert read_decisions(tmp_path).records[0].status_token == "accepted"


# --- regressions from review round 5 -----------------------------------------


@pytest.mark.parametrize(
    "status, expected",
    [
        # The four earlier extractions each passed their own example. This table is
        # every shape any of them got wrong, kept together so a fifth attempt cannot
        # fix one and lose another.
        ("superseded by [[0009-slug]]（同日翻盘）", "0009"),
        ("Superseded (2026-09-05) by [[0012-new]]", "0012"),
        ("Superseded by the 2026 review: [[0012-new]]", "0012"),
        ("Superseded by version 2 of [[0012-new]]", "0012"),
        ("Superseded (see [[0003-history]]) by [[0012-new]]", "0012"),
        # "by" inside a link slug — the case regex masking got wrong.
        ("Superseded (see [[0003-history-by-2026]]) by 0012", "0012"),
        # …and with the link left unclosed, so masking cannot find its end. No clause
        # at depth zero, so no successor: unknown, never an invented one.
        ("Superseded (see [[0003-history-by-2026) by 0012", None),
        # …and nested brackets, which `[^\]]*` measures wrongly.
        ("Superseded (see [[0003-history-[by]-2026]]) by 0012", "0012"),
        # Full-width brackets, which this corpus really does contain. Widening only
        # the scanner and not the comparison put a full-width bracket in neither the
        # opener nor the closer branch, and it was read as the clause itself.
        ("Superseded (see ［［0003-history-by-2026］］) by 0012", "0012"),
        ("superseded by 0012", "0012"),
        ("superseded", None),
        # A number that is merely prose after "by" is not a successor.
        ("superseded by the 2026 review", None),
    ],
)
def test_the_successor_across_every_shape_that_has_ever_broken_it(tmp_path, status, expected):
    _write(tmp_path, "0011-f.md", _record(status=status))
    assert read_decisions(tmp_path).records[0].superseded_by == expected


@pytest.mark.parametrize(
    "body",
    [
        # A block between the title and the prose: each of these stopped nothing when
        # the boundary counted only paragraph and list closes.
        "# 0001. T\n\nContext says:\n\nStatus: accepted is what an old endpoint returned.\n",
        "# 0001. T\n\n---\n\nStatus: accepted is what an old endpoint returned.\n",
        "# 0001. T\n\n<div>x</div>\n\nStatus: accepted is what an old endpoint returned.\n",
        # No title at all, so the counting never started.
        "Preamble paragraph.\n\nStatus: accepted is what an old endpoint returned.\n",
    ],
)
def test_prose_past_the_first_block_is_not_this_record_s_status(tmp_path, body):
    _write(tmp_path, "0001-h.md", body)
    assert read_decisions(tmp_path).records[0].status_token == UNRECOGNISED


def test_a_heading_above_the_title_costs_the_record_neither_title_nor_metadata(tmp_path):
    # The block allowance is counted from the title. Counted from the top of the file,
    # a stray heading above the `#` ate it and the real metadata stopped as block 3.
    _write(
        tmp_path,
        "0001-k.md",
        "### Preamble\n# 0001. Title\n\nStatus: accepted\n",
    )
    record = read_decisions(tmp_path).records[0]
    assert (record.title, record.status_token) == ("Title", "accepted")


def test_a_heading_above_the_title_discards_neither_title_nor_metadata(tmp_path):
    # A stray `## Preamble` before the `#` used to set the boundary before the title
    # was read, and the record came back with no title and no status at all.
    _write(
        tmp_path,
        "0001-i.md",
        "## Preamble\n\n# 0001. T\n\nStatus: accepted\n\n## Context\n\nwhy\n",
    )
    record = read_decisions(tmp_path).records[0]
    assert record.title == "T"
    assert record.status_token == "accepted"


def test_the_one_block_rule_does_not_apply_to_a_record_that_has_sections(tmp_path):
    # Applying it unconditionally was stricter than the contract: metadata sitting
    # after a quoted block, in a record whose own `## Context` is the real boundary,
    # was cut off.
    _write(
        tmp_path,
        "0001-j.md",
        "# 0001. G\n\n> ## Example\n> Status: rejected\n\nStatus: accepted\n\n## Context\n\nwhy\n",
    )
    assert read_decisions(tmp_path).records[0].status_token == "accepted"
