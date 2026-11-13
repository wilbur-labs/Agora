"""The project's CR Log, read from its own repository.

What these pin is the set of answers the UI has to keep apart. A log that is absent,
one that could not be read, one too large to read whole, one whose shape cannot be
trusted, one that is present and empty, and one that was read and classified are six
different statements — and none of them may arrive as "0 open".

The load-bearing one is the last distinction: a row whose status this reader does not
understand is neither open nor closed. Folding it into "not open" is the failure this
repository keeps manufacturing — a check narrower than the claim it makes, which then
reads as proof.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agora.projects.cr_log import (
    CR_ID_PATTERN,
    CR_LOG_COLUMNS,
    CR_LOG_RELATIVE_PATH,
    MAX_CR_LOG_BYTES,
    UNRECOGNISED,
    read_cr_log,
)
from tests.acl_support import deny_self_traverse, restore_traverse

HEADER = (
    "| ID | 日期 | 来源 | 描述 | 类型 | 状态 | 决定 | 已传播到 |\n"
    "|---|---|---|---|---|---|---|---|\n"
)


def _write(root: Path, text: str) -> Path:
    path = root / CR_LOG_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _row(cr_id: str, kind: str = "refine", status: str = "OPEN", propagated: str = "x") -> str:
    return f"| {cr_id} | 2026-09-05 | agent | 描述 | {kind} | {status} | 决定 | {propagated} |\n"


# --- reach: the answers that are not rows ------------------------------------


def test_absent_log_reports_absent_and_no_rows(tmp_path):
    log = read_cr_log(tmp_path)
    assert log.available is False
    assert log.reason == "absent"
    # None, not an empty list: "this project keeps no CR Log" must not be able to
    # render as "this project's CR Log is empty".
    assert log.rows is None
    assert log.open_count is None
    assert log.unrecognised_status_count is None
    assert log.source == CR_LOG_RELATIVE_PATH.as_posix()


def test_a_directory_at_the_path_is_unreadable_not_absent(tmp_path):
    path = tmp_path / CR_LOG_RELATIVE_PATH
    path.mkdir(parents=True)
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "unreadable")
    assert log.open_count is None


def test_a_log_past_the_size_bound_is_unknown_not_partially_read(tmp_path):
    # A prefix of a CR Log is indistinguishable from a CR Log whose open rows happen
    # to sit at the end, so a prefix is never counted.
    _write(tmp_path, HEADER + _row("CR-1") + "\n" + "x" * (MAX_CR_LOG_BYTES + 1))
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "too_large")
    assert log.rows is None


@pytest.mark.parametrize(
    "error", [PermissionError(13, "denied"), OSError(5, "I/O error"), NotADirectoryError(20, "nope")]
)
def test_a_stat_failure_that_is_not_absence_never_reports_absent(tmp_path, monkeypatch, error):
    # The distinction stated at the level every platform can test: whatever else the
    # OS raises, it is not "this project keeps no CR Log".
    _write(tmp_path, HEADER + _row("CR-1"))

    def refuse(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(Path, "lstat", refuse)
    log = read_cr_log(tmp_path)
    assert log.reason == "unreadable"
    assert (log.rows, log.open_count, log.unrecognised_status_count) == (None, None, None)


def test_a_log_under_a_directory_denying_traversal_is_unreadable(tmp_path):
    """The same case with the OS enforcing it, where the platform allows that.

    Skipped rather than faked when the account cannot deny itself traversal, so the
    record says whether the ACL path was actually exercised — and the assertion below
    is a real one whenever it does run, not a disjunction that holds either way.
    """
    root = tmp_path / "project"
    _write(root, HEADER + _row("CR-1"))
    locked = root / ".agora"
    if not deny_self_traverse(locked):
        pytest.skip("this platform/account cannot deny itself traversal")
    try:
        log = read_cr_log(root)
        if log.available:
            pytest.skip("the deny did not take effect for this token (elevated or bypassed)")
        assert log.reason == "unreadable", "a denied read must not report 'no CR Log'"
        assert log.open_count is None
    finally:
        restore_traverse(locked)


def test_undecodable_bytes_are_unreadable(tmp_path):
    path = tmp_path / CR_LOG_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"| CR-1 | \xff\xfe not utf-8 |\n")
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "unreadable")


def test_a_present_log_with_no_rows_is_available_and_empty(tmp_path):
    # Distinct from every unavailable answer: this project keeps a CR Log and has
    # raised nothing in it.
    _write(tmp_path, "# CR Log\n\nNothing raised yet.\n")
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert log.reason is None
    assert log.rows == ()
    assert (log.open_count, log.unrecognised_status_count) == (0, 0)


# --- what counts as a CR row --------------------------------------------------


def test_rows_are_found_by_their_id_cell_not_by_the_header_words(tmp_path):
    # The header is written in whatever language the project is written in. Reading
    # it would make those words identifiers, which is how the mechanism breaks the
    # day somebody translates them.
    _write(
        tmp_path,
        "| whatever | these | words | are | they | are | not | read |\n"
        "|---|---|---|---|---|---|---|---|\n"
        + _row("CR-1")
        + _row("CR-2", status="DONE"),
    )
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["CR-1", "CR-2"]
    assert log.open_count == 1


def test_an_unrelated_table_in_the_same_file_is_not_read_as_rows(tmp_path):
    _write(
        tmp_path,
        "| milestone | status |\n|---|---|\n| W4 | in progress |\n\n"
        + HEADER
        + _row("CR-1"),
    )
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["CR-1"]


def test_a_log_split_across_two_tables_still_reads_as_one_log(tmp_path):
    _write(tmp_path, HEADER + _row("CR-1") + "\nprose in between\n\n" + HEADER + _row("CR-2"))
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["CR-1", "CR-2"]
    assert log.open_count == 2


def test_a_quoted_table_is_somebody_elses_log(tmp_path):
    quoted = "".join("> " + line + "\n" for line in (HEADER + _row("CR-9")).splitlines())
    _write(tmp_path, quoted + "\n" + HEADER + _row("CR-1"))
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["CR-1"]


def test_prose_mentioning_a_cr_id_is_not_a_row(tmp_path):
    _write(tmp_path, "See CR-3 for the decision.\n\n" + HEADER + _row("CR-1"))
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["CR-1"]


def test_an_id_cell_that_merely_contains_a_cr_id_is_not_a_row():
    # The anchoring itself, asserted where it lives: "superseded by CR-4" is prose,
    # not an id. Pinned at this level because at file level such a row now makes the
    # log malformed either way (round 9), which would hide what this is testing.
    assert CR_ID_PATTERN.match("CR-4")
    assert not CR_ID_PATTERN.match("superseded by CR-4")
    assert not CR_ID_PATTERN.match("CR-4 and CR-5")


def test_a_revision_suffix_is_part_of_the_id(tmp_path):
    """`CR-113v6` is how a real log writes a follow-up, and the hook that injects open
    CRs counts it. Refused, it read as an unreadable row naming CR-113 and the whole
    log was malformed, blaming the row that was fine."""
    _write(tmp_path, HEADER + _row("CR-113", status="DONE") + _row("CR-113v2"))
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["CR-113", "CR-113v2"]
    assert log.open_count == 1
    for not_an_id in ("CR-113v", "CR-113a", "CR-113-2", "CR-113 v2", "CR-v2"):
        assert not CR_ID_PATTERN.match(not_an_id), not_an_id


def test_an_over_long_row_is_named_even_after_a_vertical_tab(tmp_path):
    """A vertical tab is a line break to `str.splitlines()` and not to the parser. With
    one in an earlier cell the source count came from the next row: the log named
    CR-3, which was fine, and the over-long CR-2 would have passed had it come last."""
    long_row = "| CR-2 | 2026-09-05 | agent | 描述 | refine | OPEN | 决定 | x | extra |\n"
    _write(tmp_path, HEADER + _row("CR-1").replace("描述", "描\x0b述") + long_row + _row("CR-3"))
    log = read_cr_log(tmp_path)
    assert (log.reason, log.malformed_ids) == ("malformed", ("CR-2",))


def test_rows_cut_off_from_their_table_are_still_read(tmp_path):
    """The template's example comment sits between the delimiter and the rows, and a
    blank line inside a log is easy to leave. Either ends the table for the parser, and
    a real log of five change requests then read as "0 rows, 0 open"."""
    comment = '<!-- 示例：| CR-1 | 2026-05-30 | user | "x" | creep | OPEN | | | -->\n'
    _write(tmp_path, HEADER + comment + _row("CR-1", status="DONE") + _row("CR-2")
           + "\n" + _row("CR-3") + "| CR-4 | 2026-09-05 | agent | 短 | bug |\n")
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["CR-1", "CR-2", "CR-3", "CR-4"]
    assert (log.open_count, log.unrecognised_status_count) == (2, 1)
    assert log.rows[1].propagated_to == "x"


def test_a_cut_off_row_that_is_too_long_is_malformed_like_any_other(tmp_path):
    _write(tmp_path, HEADER + _row("CR-1") + "\n"
           + "| CR-2 | 2026-09-05 | agent | a | bug | OPEN | d | x | extra |\n")
    log = read_cr_log(tmp_path)
    assert (log.reason, log.malformed_ids) == ("malformed", ("CR-2",))


def test_a_row_a_cr_log_s_own_table_does_not_recognise_makes_it_malformed(tmp_path):
    # …and the file-level consequence of the same input, stated separately.
    _write(
        tmp_path,
        HEADER + _row("CR-1") + "| superseded by CR-4 | a | b | c | d | e | f | g |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-4",)


def test_a_bold_id_still_identifies_the_row(tmp_path):
    _write(tmp_path, HEADER + "| **CR-1** | d | s | desc | refine | **OPEN** | dec | prop |\n")
    log = read_cr_log(tmp_path)
    assert [row.id for row in log.rows] == ["**CR-1**"]
    assert log.open_count == 1


# --- the shape rule -----------------------------------------------------------


def test_a_table_of_the_wrong_width_makes_the_whole_read_malformed(tmp_path):
    # Six columns, so `status` would be read out of the `decision` cell. Skipping the
    # rows and reporting the rest is the wrong thing: the columns mean what they mean
    # by position, and the row dropped may be the open one.
    _write(
        tmp_path,
        "| a | b | c | d | e | f |\n|---|---|---|---|---|---|\n"
        "| CR-1 | 2026-09-05 | agent | 描述 | refine | OPEN |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.rows is None
    assert log.open_count is None
    # Named, not counted: the fix is in one row and the person needs to know which.
    assert log.malformed_ids == ("CR-1",)


def test_a_narrow_table_is_caught_before_its_truncation_can_be_believed(tmp_path):
    # Measured: a GFM row longer than its header is truncated and the extra cells are
    # discarded, so the status would never reach this module at all. The width check
    # is what stands between that and a log rendering as "0 open".
    _write(tmp_path, "| a | b |\n|---|---|\n| CR-1 | 2026-09-05 | agent | d | refine | OPEN |\n")
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.open_count is None


def test_a_short_row_under_a_correct_header_keeps_its_columns_and_reads_unrecognised(tmp_path):
    # Measured: the parser pads a short row with empty cells at the *end*, so the
    # cells that are present stay in their own columns. A row with no status cell is
    # then unclassified — which is the honest answer, and specifically not "closed".
    _write(tmp_path, HEADER + "| CR-1 | 2026-09-05 | agent |\n")
    log = read_cr_log(tmp_path)
    assert log.available is True
    row = log.rows[0]
    assert (row.id, row.date, row.source) == ("CR-1", "2026-09-05", "agent")
    assert row.status_token == UNRECOGNISED
    assert (log.open_count, log.unrecognised_status_count) == (0, 1)


def test_a_well_formed_log_names_no_malformed_rows(tmp_path):
    _write(tmp_path, HEADER + _row("CR-1"))
    assert read_cr_log(tmp_path).malformed_ids == ()


def test_the_column_count_is_the_format(tmp_path):
    row = "| " + " | ".join(["CR-1"] + ["c"] * (CR_LOG_COLUMNS - 1)) + " |\n"
    _write(tmp_path, HEADER + row)
    assert read_cr_log(tmp_path).available is True


# --- status and kind ----------------------------------------------------------


def test_open_decided_and_done_are_read_and_counted(tmp_path):
    _write(
        tmp_path,
        HEADER + _row("CR-1") + _row("CR-2", status="DECIDED") + _row("CR-3", status="DONE"),
    )
    log = read_cr_log(tmp_path)
    assert [row.status_token for row in log.rows] == ["open", "decided", "done"]
    assert log.open_count == 1
    assert log.unrecognised_status_count == 0


def test_an_unknown_status_is_neither_open_nor_closed(tmp_path):
    # The real workspace log has rows whose status cell is a sentence
    # ("A 已完成并提交 / B 转入 W2"). Calling that "not open" is a claim the file
    # never made.
    _write(tmp_path, HEADER + _row("CR-1", status="**A 已完成 / B 转入 W2**"))
    log = read_cr_log(tmp_path)
    row = log.rows[0]
    assert row.status_token == UNRECOGNISED
    assert row.is_open is False
    assert log.open_count == 0
    # …and the count that stops "0 open" from being the whole story.
    assert log.unrecognised_status_count == 1


def test_an_unrecognised_row_does_not_make_the_log_unavailable(tmp_path):
    # Unlike the progress snapshot, which reduces to a percentage: here the rows are
    # listed, so the unclassified one is visible on its own row and nothing is hidden
    # by reporting the rest.
    _write(tmp_path, HEADER + _row("CR-1") + _row("CR-2", status="tbd"))
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert (log.open_count, log.unrecognised_status_count) == (1, 1)


def test_status_matching_is_case_sensitive(tmp_path):
    # A lowercase "open" is prose style, not the token. Guessing at it is how a
    # vocabulary stops being one.
    _write(tmp_path, HEADER + _row("CR-1", status="open"))
    log = read_cr_log(tmp_path)
    assert log.rows[0].status_token == UNRECOGNISED
    assert log.open_count == 0


def test_a_status_word_inside_a_longer_cell_is_not_the_status(tmp_path):
    # The workspace's own log has a "propagated to" cell reading
    # "本行即触发物：OPEN 状态 → hook…". Matching on containment would read that as
    # a status.
    _write(tmp_path, HEADER + _row("CR-1", status="DONE", propagated="本行即触发物：OPEN 状态"))
    log = read_cr_log(tmp_path)
    assert log.rows[0].status_token == "done"
    assert log.open_count == 0


def test_the_six_kinds_are_read_and_anything_else_is_unrecognised(tmp_path):
    kinds = ["bug", "clarify", "refine", "creep", "cut", "defer", "housekeeping"]
    _write(tmp_path, HEADER + "".join(_row(f"CR-{i}", kind=k) for i, k in enumerate(kinds)))
    log = read_cr_log(tmp_path)
    assert [row.kind_token for row in log.rows] == kinds[:-1] + [UNRECOGNISED]


def test_an_empty_cell_does_not_shift_the_columns(tmp_path):
    # A `defer` whose "propagated to" is empty is the row whose emptiness is the
    # whole point. If an empty cell vanished, every column after it would shift and
    # this row would come back reading as another one.
    _write(tmp_path, HEADER + _row("CR-1", kind="defer", propagated=""))
    log = read_cr_log(tmp_path)
    row = log.rows[0]
    assert (row.kind_token, row.status_token) == ("defer", "open")
    assert row.propagated_to == ""


def test_the_row_keeps_its_cells_as_written(tmp_path):
    _write(tmp_path, HEADER + _row("CR-1"))
    row = read_cr_log(tmp_path).rows[0]
    assert (row.date, row.source, row.description, row.decision) == (
        "2026-09-05", "agent", "描述", "决定",
    )


# --- payload ------------------------------------------------------------------


def test_payload_carries_both_numbers_together(tmp_path):
    _write(tmp_path, HEADER + _row("CR-1") + _row("CR-2", status="tbd"))
    payload = read_cr_log(tmp_path).payload()
    assert payload["open_count"] == 1
    # Never one without the other: a consumer cannot render "1 open" without having
    # been handed the number that says how much of the log that covers.
    assert payload["unrecognised_status_count"] == 1
    assert len(payload["rows"]) == 2


def test_an_unavailable_payload_has_no_zeroes_in_it(tmp_path):
    payload = read_cr_log(tmp_path).payload()
    assert payload["rows"] is None
    assert payload["open_count"] is None
    assert payload["unrecognised_status_count"] is None


# --- regressions from review round 1 -----------------------------------------


def test_columns_in_another_order_are_caught_rather_than_read_as_all_clear(tmp_path):
    """The catastrophic input: a log whose columns are ordered differently, in which
    an OPEN row read as `done` and the payload said `open_count == 0` with nothing
    reporting a problem. The header words are never read, so a bare status token
    sitting outside the status column is the only available signal."""
    _write(
        tmp_path,
        "| ID | Date | Source | Description | Kind | Decision | Status | Propagated |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| CR-1 | 2026-09-05 | agent | d | refine | DONE | OPEN | x |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-1",)
    assert log.open_count is None


def test_a_row_written_with_too_many_cells_is_caught_though_the_parser_trims_it(tmp_path):
    """Measured: markdown-it normalises a long row to the header's width before this
    module sees it, so the parsed count agrees and every column after the extra cell
    has silently shifted. Only the source line still shows what was written."""
    _write(
        tmp_path,
        HEADER + "| CR-1 | 2026-09-05 | agent | d | extra | refine | OPEN | accept | x |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-1",)


def test_an_escaped_pipe_in_a_cell_is_content_not_a_column(tmp_path):
    # The source-line count must not read a description's escaped pipe as a separator,
    # or an ordinary row would be rejected as malformed.
    _write(
        tmp_path,
        HEADER + r"| CR-1 | 2026-09-05 | agent | a \| b | refine | OPEN | accept | x |" + "\n",
    )
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert log.open_count == 1


def test_a_status_word_as_a_whole_cell_elsewhere_is_treated_as_a_reordering(tmp_path):
    # Deliberate, and its limits are stated in the module docstring: this catches the
    # reorderings that can lie about status, not reordering in general.
    _write(tmp_path, HEADER + _row("CR-1", status="DONE", propagated="OPEN"))
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")


def test_a_status_word_inside_a_longer_cell_is_still_not_a_reordering(tmp_path):
    # The workspace's own log writes "本行即触发物：OPEN 状态 → hook…" in that column.
    # Matching on containment here would reject every real log.
    _write(tmp_path, HEADER + _row("CR-1", status="DONE", propagated="本行即触发物：OPEN 状态"))
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert log.rows[0].status_token == "done"


# --- regressions from review round 2 -----------------------------------------


def test_an_unreadable_log_reports_unknown_malformed_ids_not_none_to_fix(tmp_path):
    # Same collapse as the sibling reader's `unnumbered_files`, caught a round later:
    # [] here would say "no row needs fixing" about a file nobody could open.
    payload = read_cr_log(tmp_path).payload()
    assert payload["reason"] == "absent"
    assert payload["malformed_ids"] is None


def test_a_malformed_log_still_names_the_rows(tmp_path):
    # A six-column *table*, which is malformed. A six-cell row under the eight-column
    # header is not — the parser pads it, its columns keep their places, and it reads
    # as unrecognised. Getting that distinction wrong is what this test first did.
    _write(tmp_path, "| a | b | c | d | e | f |\n|---|---|---|---|---|---|\n"
                     "| CR-1 | 2026-09-05 | agent | d | refine | OPEN |\n")
    assert read_cr_log(tmp_path).payload()["malformed_ids"] == ["CR-1"]


def test_a_short_row_is_not_malformed_and_carries_no_ids(tmp_path):
    _write(tmp_path, HEADER + "| CR-1 | a | b | c | d | e |\n")
    payload = read_cr_log(tmp_path).payload()
    assert payload["available"] is True
    assert payload["malformed_ids"] == []
    assert payload["unrecognised_status_count"] == 1


def test_a_decision_cell_that_is_a_bare_status_word_is_rejected_on_purpose(tmp_path):
    """The accepted cost of the reordering check, pinned so it cannot drift silently.

    From a position-only view this row and a Decision/Status swap are the same bytes.
    The trade is one-directional by design: a false `malformed` names the row and is
    fixed by writing the decision as prose; a false "nothing is open" is silent.
    """
    _write(tmp_path, HEADER + _row("CR-1", status="OPEN").replace("| 决定 |", "| DONE |"))
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")


# --- regressions from review round 7 -----------------------------------------


def test_an_id_column_that_is_not_first_makes_the_log_malformed_not_empty(tmp_path):
    """The worst thing this feature can say, and it was reachable from a plausible
    table: with the date column written first, no row is recognised as a CR row at
    all, the payload carries no rows, and the strip announces that the project has
    raised nothing — with an OPEN row sitting in the file."""
    _write(
        tmp_path,
        "| Date | ID | Source | Description | Kind | Status | Decision | Propagated |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 2026-09-05 | CR-1 | user | Important change | refine | OPEN | pending | |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-1",)
    assert log.rows is None


def test_a_cross_reference_to_another_cr_is_not_a_misplaced_id(tmp_path):
    # In a log that parses, a cell naming another CR is an ordinary cross-reference.
    # Rejecting those would make the check useless on every real log.
    _write(tmp_path, HEADER + _row("CR-1", status="DONE", propagated="CR-3"))
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert [r.id for r in log.rows] == ["CR-1"]


def test_a_file_with_no_cr_ids_anywhere_is_simply_empty(tmp_path):
    _write(tmp_path, "# CR Log\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert log.rows == ()
    assert log.malformed_ids == ()


# --- regressions from review round 8 -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # An id written as a link. Inline parsing is off, so the cell arrives as raw
        # source and the anchored cell match cannot see the id inside it.
        "| ID | Date | S | D | K | St | Dec | P |\n|---|---|---|---|---|---|---|---|\n"
        "| [**CR-2**](changes/CR-2.md) | 2026-09-05 | user | urgent | refine | OPEN | p | |\n",
        # The id column written second.
        "| Date | ID | S | D | K | St | Dec | P |\n|---|---|---|---|---|---|---|---|\n"
        "| 2026-09-05 | CR-1 | user | x | refine | OPEN | p | |\n",
        # …and written last.
        "| Date | S | D | K | St | Dec | P | ID |\n|---|---|---|---|---|---|---|---|\n"
        "| 2026-09-05 | u | x | refine | OPEN | p | q | CR-1 |\n",
    ],
)
def test_a_file_that_mentions_a_cr_but_yields_no_rows_is_malformed_not_empty(tmp_path, text):
    """The worst sentence this module can produce is "this project has raised nothing"
    said over a file holding an OPEN row. Three different spellings reached it, so the
    rule is not about spellings: ids mentioned, nothing recognised, therefore not an
    empty change log."""
    _write(tmp_path, text)
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.rows is None
    assert log.malformed_ids  # names what to look at


def test_a_log_with_only_a_header_is_genuinely_empty(tmp_path):
    # The other side of the rule above, and the one it must not break: a project that
    # keeps a change log and has raised nothing mentions no id, so it stays empty.
    _write(tmp_path, HEADER)
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert log.rows == ()
    assert (log.open_count, log.malformed_ids) == (0, ())


def test_one_id_repeated_is_named_once(tmp_path):
    _write(
        tmp_path,
        "| Date | ID |\n|---|---|\n| 2026-09-05 | CR-1 |\n| 2026-09-06 | CR-1 |\n",
    )
    assert read_cr_log(tmp_path).malformed_ids == ("CR-1",)


# --- regressions from review round 9 -----------------------------------------


def test_an_unrecognised_row_inside_a_parsing_log_is_not_lost(tmp_path):
    """The limit I had written down instead of closing. One recognised row disabled
    the file-level rule for every unrecognised row in the file, so adding a row
    written some other way — a linked id, a moved id column — to an existing log made
    that row vanish while the log went on reporting its old count."""
    _write(
        tmp_path,
        HEADER
        + _row("CR-1", status="DONE")
        + "| [**CR-2**](changes/CR-2.md) | 2026-09-05 | u | urgent | refine | OPEN | p | q |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-2",)
    # And specifically not: one row, zero open.
    assert log.rows is None
    assert log.open_count is None


def test_a_cross_reference_inside_a_recognised_row_is_still_fine(tmp_path):
    # The other side: cross-references live in rows that ARE recognised, which is why
    # the rule is about rows rather than about cells.
    _write(tmp_path, HEADER + _row("CR-1", status="DONE", propagated="见 CR-3"))
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert [r.id for r in log.rows] == ["CR-1"]


def test_an_unrelated_table_elsewhere_is_not_held_to_the_log_s_rule(tmp_path):
    # Only tables that actually hold CR rows are CR logs.
    _write(
        tmp_path,
        "| milestone | status |\n|---|---|\n| W4 | in progress |\n\n" + HEADER + _row("CR-1"),
    )
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert [r.id for r in log.rows] == ["CR-1"]


# --- regressions from review round 10 ----------------------------------------


@pytest.mark.parametrize(
    "second_table",
    [
        # A split log is a supported shape. Both of these put an OPEN row in the second
        # table where none of its rows parse, and the previous rule — "a table that
        # holds a recognised row" — ignored that table entirely, so the log reported
        # its first table's count and called it the answer.
        "| ID | Date | S | D | K | St | Dec | P |\n|---|---|---|---|---|---|---|---|\n"
        "| [**CR-2**](changes/CR-2.md) | 2026-09-05 | u | urgent | refine | OPEN | p | q |\n",
        "| Date | ID | S | D | K | St | Dec | P |\n|---|---|---|---|---|---|---|---|\n"
        "| 2026-09-05 | CR-2 | u | urgent | refine | OPEN | p | q |\n",
    ],
)
def test_a_second_table_whose_rows_do_not_parse_is_not_silently_dropped(tmp_path, second_table):
    _write(tmp_path, HEADER + _row("CR-1", status="DONE") + "\nprose\n\n" + second_table)
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-2",)
    # Specifically not: one row, zero open.
    assert log.open_count is None


def test_a_split_log_whose_tables_both_parse_still_reads_as_one(tmp_path):
    _write(
        tmp_path,
        HEADER + _row("CR-1", status="DONE") + "\nprose\n\n" + HEADER + _row("CR-2"),
    )
    log = read_cr_log(tmp_path)
    assert [r.id for r in log.rows] == ["CR-1", "CR-2"]
    assert log.open_count == 1


def test_a_table_mentioning_no_cr_id_is_not_held_to_the_rule(tmp_path):
    # What keeps the widened rule from rejecting every file with a table in it.
    _write(
        tmp_path,
        "| milestone | status |\n|---|---|\n| W4 | in progress |\n\n" + HEADER + _row("CR-1"),
    )
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert [r.id for r in log.rows] == ["CR-1"]


# --- regressions from review round 11 ----------------------------------------


def test_a_cr_row_turned_into_a_header_row_is_not_lost(tmp_path):
    """A misplaced delimiter line makes the first row of a table its header, and header
    rows are never read. The whole table then disappeared, count unchanged."""
    _write(
        tmp_path,
        HEADER + _row("CR-1", status="DONE") + "\nprose\n\n"
        + "| CR-2 | 2026-09-05 | u | urgent | refine | OPEN | p | q |\n"
        + "|---|---|---|---|---|---|---|---|\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-2",)


def test_a_normal_header_row_is_not_itself_suspicious(tmp_path):
    # The other side: a header carries column names, not ids, so it never trips this.
    _write(tmp_path, HEADER + _row("CR-1"))
    assert read_cr_log(tmp_path).available is True


def test_prose_may_name_a_cr_that_is_not_a_row(tmp_path):
    # A log's own header saying where a CR went is ordinary. The version of this rule
    # that policed prose broke three existing tests and was withdrawn.
    _write(tmp_path, "# CR Log\n\nCR-9 was carried to the tracker.\n\n" + HEADER + _row("CR-1"))
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert [r.id for r in log.rows] == ["CR-1"]


def test_a_documented_example_in_a_fence_does_not_poison_the_log(tmp_path):
    # A CR-LOG.md showing an example of a CR row is exactly the file most likely to
    # exist. Fenced code is not a table, so it is not seen.
    _write(
        tmp_path,
        HEADER + _row("CR-1") + "\n```\n| CR-9 | … |\n```\n",
    )
    assert read_cr_log(tmp_path).available is True


def test_a_totals_row_carrying_no_id_loses_nothing_and_is_allowed(tmp_path):
    # An earlier rule rejected any unrecognised row in a log's table, which made a
    # totals or separator row fatal. A row with no id in it loses no CR.
    _write(tmp_path, HEADER + _row("CR-1") + "| total | | | | | | | |\n")
    log = read_cr_log(tmp_path)
    assert log.available is True
    assert [r.id for r in log.rows] == ["CR-1"]


# --- regressions from review round 12 ----------------------------------------


def test_a_duplicate_row_for_an_id_that_already_parsed_is_not_cancelled_out(tmp_path):
    """The rule briefly subtracted ids that appeared as a good row somewhere, which
    masked exactly the rows it was meant to find: a second row for CR-1 written another
    way was cancelled by the first, and its OPEN status disappeared."""
    _write(
        tmp_path,
        HEADER
        + _row("CR-1", status="DONE")
        + "| [**CR-1**](changes/CR-1.md) | 2026-09-05 | u | reopened | refine | OPEN | p | q |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-1",)
    assert log.open_count is None


# --- regressions from review round 13 ----------------------------------------


def test_an_id_written_against_cjk_text_is_still_found(tmp_path):
    r"""`\b` is a boundary between word and non-word characters, and CJK characters are
    word characters in Python's Unicode-aware regex — so `見CR-2`, with no space, had no
    boundary in front of the id and the row carrying it went unreported. In a corpus
    written in Chinese and Japanese that is not an edge case."""
    _write(
        tmp_path,
        HEADER + _row("CR-1", status="DONE")
        + "| 見CR-2 | 2026-09-05 | u | urgent | refine | OPEN | p | q |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-2",)


@pytest.mark.parametrize(
    "cell, found",
    [
        ("CR-2", ["CR-2"]),
        ("見CR-2", ["CR-2"]),          # no boundary in front, still an id
        ("CR-2番", ["CR-2"]),          # nor behind
        ("（CR-2）", ["CR-2"]),
        ("CR-2、CR-3", ["CR-2", "CR-3"]),
        ("CR-23", ["CR-23"]),          # not CR-2 inside a longer number
        ("XCR-2", []),                 # part of a longer ASCII token
        ("MY_CR-2", []),
        ("a-CR-2", []),
    ],
)
def test_what_counts_as_an_id_inside_a_cell(cell, found):
    from agora.projects.cr_log import _CR_ID_ANYWHERE

    assert _CR_ID_ANYWHERE.findall(cell) == found


# --- regressions from review round 14 ----------------------------------------


@pytest.mark.parametrize(
    "written_id",
    [
        "CR‑2",   # non-breaking hyphen, as pasted out of a document or a PDF
        "ＣR-2",   # full-width C
        "CR-２",   # full-width digit
        "ＣＲ‑2",
    ],
)
def test_a_confusable_id_is_reported_rather_than_dropped(tmp_path, written_id):
    """None of these is a valid id and none is recognised as a row — which is right.
    What would not be right is losing them in silence, so the suspicion scan folds
    full-width forms and non-ASCII dashes before looking."""
    _write(
        tmp_path,
        HEADER + _row("CR-1", status="DONE")
        + f"| {written_id} | 2026-09-05 | u | urgent | refine | OPEN | p | q |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.malformed_ids == ("CR-2",)


def test_folding_is_only_for_the_scan_not_for_recognising_rows(tmp_path):
    # The strict pattern decides what a row *is*; the folded scan decides what may not
    # disappear quietly. A confusable never becomes a row.
    from agora.projects.cr_log import _confusables_folded

    assert _confusables_folded("CR‑2") == "CR-2"
    assert not CR_ID_PATTERN.match("CR‑2")
    assert not CR_ID_PATTERN.match("ＣR-2")


# --- regressions from review round 15 ----------------------------------------


@pytest.mark.parametrize(
    "written_id, why",
    [
        ("СR-2", "Cyrillic С (U+0421)"),
        ("ϹΡ-2", "Greek lunate sigma and rho"),
        ("ⅭR-2", "Roman numeral Ⅽ"),
        ("C\u200bR-2", "zero-width space inside the letters"),
        ("CR\u00ad-2", "soft hyphen"),
        ("CR\u058a2", "Armenian hyphen"),
        ("CR\u2e1a2", "a dash from the supplemental punctuation block"),
    ],
)
def test_a_look_alike_id_is_reported_rather_than_dropped(tmp_path, written_id, why):
    """None of these is a valid id, and none becomes a row. Losing them in silence is
    the thing that is not allowed, so the suspicion scan folds look-alike letters, every
    Unicode dash, and the zero-width characters before it looks."""
    _write(
        tmp_path,
        HEADER + _row("CR-1", status="DONE")
        + f"| {written_id} | 2026-09-05 | u | urgent | refine | OPEN | p | q |\n",
    )
    log = read_cr_log(tmp_path)
    assert (log.available, log.reason) == (False, "malformed"), why
    assert log.malformed_ids == ("CR-2",), why


def test_look_alikes_are_folded_before_nfkc_not_after():
    """Order is not a detail here: NFKC maps the Greek lunate sigma Ϲ to Σ, so folding
    it to C afterwards never fires. A normalisation that runs first can destroy the very
    character the next step is looking for."""
    from agora.projects.cr_log import _confusables_folded

    assert _confusables_folded("ϹΡ-2") == "CR-2"
    assert _confusables_folded("СR-2") == "CR-2"
    # …and none of them is ever a row.
    for spelling in ("ϹΡ-2", "СR-2", "CR\u20112", "ＣR-2"):
        assert not CR_ID_PATTERN.match(spelling)
