"""The project's known defects, read from its own repository.

What these pin, beyond the reach vocabulary the sibling readers established: a status
nobody wrote is not "open" and not "fixed" but "unrecognised"; an entry missing a
required field is still listed rather than dropped; an empty file is a settled "no
defects", not a malformed one; and "could not read" is never a count of zero.
"""
from __future__ import annotations

from pathlib import Path

from agora.projects.defects import (
    DEFECTS_RELATIVE_PATH,
    MAX_DEFECTS_BYTES,
    UNRECOGNISED,
    read_defects,
)


def _write(root: Path, text: str) -> Path:
    path = root / DEFECTS_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- reach --------------------------------------------------------------------


def test_no_defects_file_reports_absent(tmp_path):
    log = read_defects(tmp_path)
    assert (log.available, log.reason) == (False, "absent")
    assert log.records is None
    assert log.open_count is None
    assert log.unrecognised_count is None
    assert log.unreadable_count is None


def test_a_directory_where_the_file_should_be_is_unreadable(tmp_path):
    path = tmp_path / DEFECTS_RELATIVE_PATH
    path.mkdir(parents=True, exist_ok=True)
    log = read_defects(tmp_path)
    assert (log.available, log.reason) == (False, "unreadable")
    assert log.records is None


def test_a_file_over_the_bound_is_too_large(tmp_path):
    _write(tmp_path, "defects:\n" + ("  - id: D-1\n    title: x\n    status: open\n" * 20000))
    path = tmp_path / DEFECTS_RELATIVE_PATH
    # The fixture must actually exceed the bound, or the test asserts nothing.
    assert path.stat().st_size > MAX_DEFECTS_BYTES
    log = read_defects(tmp_path)
    assert (log.available, log.reason) == (False, "too_large")
    assert log.records is None


def test_malformed_yaml_is_malformed_not_empty(tmp_path):
    _write(tmp_path, "defects: [ this is : not valid : yaml")
    log = read_defects(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")
    assert log.records is None


def test_duplicate_key_is_its_own_reason(tmp_path):
    _write(tmp_path, "defects:\n  - id: D-1\n    id: D-2\n    title: x\n    status: open\n")
    log = read_defects(tmp_path)
    assert (log.available, log.reason) == (False, "duplicate_key")


def test_top_level_not_a_mapping_is_malformed(tmp_path):
    _write(tmp_path, "- just\n- a\n- list\n")
    log = read_defects(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")


def test_defects_not_a_list_is_malformed(tmp_path):
    _write(tmp_path, "defects: not-a-list\n")
    log = read_defects(tmp_path)
    assert (log.available, log.reason) == (False, "malformed")


# --- settled nothing (available, but empty) -----------------------------------


def test_empty_file_is_no_defects_not_malformed(tmp_path):
    _write(tmp_path, "")
    log = read_defects(tmp_path)
    assert log.available is True
    assert log.reason is None
    assert log.records == ()
    assert log.open_count == 0


def test_mapping_without_defects_key_is_no_defects(tmp_path):
    _write(tmp_path, "something_else: 1\n")
    log = read_defects(tmp_path)
    assert log.available is True
    assert log.records == ()
    assert log.open_count == 0


# --- records ------------------------------------------------------------------


def test_open_and_fixed_are_counted_and_kept(tmp_path):
    _write(
        tmp_path,
        "defects:\n"
        "  - id: D-1\n    title: files were fake\n    status: fixed\n    summary: computed live\n    milestone: W0\n"
        "  - id: D-2\n    title: trust bound to spec\n    status: open\n"
        "  - id: D-3\n    title: role default\n    status: open\n",
    )
    log = read_defects(tmp_path)
    assert log.available is True
    assert log.records is not None and len(log.records) == 3
    # open count is the owed count; fixed stays in the list but out of the count.
    assert log.open_count == 2
    assert log.unrecognised_count == 0
    assert log.unreadable_count == 0
    fixed = log.records[0]
    assert (fixed.id, fixed.status_token, fixed.milestone, fixed.summary) == (
        "D-1", "fixed", "W0", "computed live",
    )


def test_status_first_word_matched_with_trailing_prose(tmp_path):
    _write(tmp_path, "defects:\n  - id: D-1\n    title: x\n    status: fixed (2026-08-27, commit abc)\n")
    log = read_defects(tmp_path)
    assert log.records is not None
    assert log.records[0].status_token == "fixed"
    # Prose after the token is kept for display.
    assert log.records[0].status == "fixed (2026-08-27, commit abc)"


def test_status_outside_vocabulary_is_unrecognised_not_open(tmp_path):
    _write(tmp_path, "defects:\n  - id: D-1\n    title: x\n    status: wontfix\n")
    log = read_defects(tmp_path)
    assert log.records is not None
    assert log.records[0].status_token == UNRECOGNISED
    # An unmarked defect is neither open nor fixed: not in the open count.
    assert log.open_count == 0
    assert log.unrecognised_count == 1


def test_entry_missing_required_field_is_listed_not_dropped(tmp_path):
    _write(
        tmp_path,
        "defects:\n"
        "  - id: D-1\n    title: complete\n    status: open\n"
        "  - id: D-2\n    title: no status here\n"          # missing status
        "  - title: no id here\n    status: open\n",         # missing id
    )
    log = read_defects(tmp_path)
    assert log.records is not None and len(log.records) == 3
    assert log.unreadable_count == 2
    # The one complete open defect is the only thing counted.
    assert log.open_count == 1
    # The missing-status entry keeps its id so it is nameable.
    assert log.records[1].id == "D-2"
    assert log.records[1].readable is False
    # The missing-id entry is listed with id None.
    assert log.records[2].id is None
    assert log.records[2].readable is False


def test_non_mapping_entry_is_unreadable(tmp_path):
    _write(tmp_path, "defects:\n  - just a string\n  - id: D-1\n    title: x\n    status: open\n")
    log = read_defects(tmp_path)
    assert log.records is not None and len(log.records) == 2
    assert log.records[0].readable is False
    assert log.records[0].id is None
    assert log.open_count == 1


def test_empty_defects_list_is_available_with_zero(tmp_path):
    _write(tmp_path, "defects: []\n")
    log = read_defects(tmp_path)
    assert log.available is True
    assert log.records == ()
    assert log.open_count == 0
