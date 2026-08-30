"""The RESUME progress snapshot the progress bar is driven by.

What these pin is the distinction the UI depends on: a snapshot that is absent, one
that could not be read, one written in markers this reader does not count, and one that
was read and counted are four different answers, and none of them may become 0.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agora.projects.progress_snapshot import (
    MAX_SNAPSHOT_BYTES,
    SNAPSHOT_RELATIVE_PATH,
    UNCOUNTED_MARKERS_REASON,
    count_progress_markers,
    has_uncounted_checklist_lines,
    read_progress_snapshot,
)
from tests.acl_support import deny_self_traverse, restore_traverse


def _write(root: Path, text: str) -> Path:
    path = root / SNAPSHOT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_counts_markers_as_the_first_token_of_a_line():
    counts = count_progress_markers(
        "# Progress\n"
        "- ✅ Shipped the reader\n"
        "  - ✅ nested item still counts\n"
        "* 🔵 Wiring the bar\n"
        "+ ⬜ Portfolio surface\n"
        "1. ⬜ ordered bullets too\n"
        "2) ⬜ and this form\n"
        "✅ no bullet at all\n"
    )
    assert counts == {"done": 3, "in_progress": 1, "todo": 3}


def test_prose_mentioning_a_marker_is_not_an_item():
    # The snapshot's own explanatory text says things like "mark it ✅ when done".
    # Counting that as an item would inflate the percentage from the file's prose.
    counts = count_progress_markers(
        "The convention is: mark an item ✅ when it is done, 🔵 while it is in\n"
        "progress, and leave it ⬜ until it starts.\n"
        "- ✅ the one real item\n"
    )
    assert counts == {"done": 1, "in_progress": 0, "todo": 0}


def test_one_item_per_line_even_when_a_line_carries_several_markers():
    assert count_progress_markers("- ✅ done ⬜ ⬜ ⬜\n") == {"done": 1, "in_progress": 0, "todo": 0}


def test_absent_snapshot_reports_absent_with_no_counts(tmp_path):
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == "absent"
    # The counts are None, not 0: a project with no snapshot renders empty, and a
    # zero in this payload is a number the UI could render as 0%.
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)
    assert snapshot.source == SNAPSHOT_RELATIVE_PATH.as_posix()


def test_a_directory_at_the_snapshot_path_is_unreadable_not_absent(tmp_path):
    """A directory is present-but-unusable, which is not the same claim as absent.

    This test previously asserted `absent`, and that expectation was wrong: `absent`
    renders as "this project keeps no progress snapshot", a settled fact about the
    project, while a directory (or a device, or a broken symlink) at that path means the
    reader could not read what is there. Pinning the collapse made the defect look
    intended, so the assertion is inverted here rather than only in the reader.
    """
    (tmp_path / SNAPSHOT_RELATIVE_PATH).mkdir(parents=True)
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.reason == "unreadable"
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


def test_a_snapshot_in_another_marker_convention_is_reported_as_such(tmp_path):
    # This repository's own snapshot is written as `- [x]` task-list lines and holds
    # over a thousand of them. Counting zero and reporting "tracks no items yet" would
    # be a false statement about the project, so the mismatch is its own outcome — and
    # the reader still counts nothing, because mapping a checkbox onto ✅/🔵/⬜ is the
    # maintainer's decision to make (a checkbox cannot express 🔵 at all).
    _write(tmp_path, "# Progress\n\n- [x] Shipped the reader\n- [ ] Wire the bar\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == UNCOUNTED_MARKERS_REASON
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


def test_counted_markers_win_over_another_convention_in_the_same_file(tmp_path):
    # A file mid-migration counts what it has. The other-convention outcome is only for
    # a file where nothing at all was counted.
    _write(tmp_path, "- ✅ counted\n- [x] not counted by this reader\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (1, 0, 0)


def test_a_one_character_link_is_not_mistaken_for_a_checkbox():
    assert has_uncounted_checklist_lines("- [a](https://example.com) a link, not a box\n") is False
    assert has_uncounted_checklist_lines("- [x] a box\n") is True
    assert has_uncounted_checklist_lines("1. [ ]\n") is True


def test_unreadable_snapshot_is_unknown_and_never_zero(tmp_path):
    path = _write(tmp_path, "- ✅ done\n")
    path.write_bytes(b"- \xff\xfe not utf-8 \xff")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == "unreadable"
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


def test_a_snapshot_that_cannot_be_stat_ed_is_unreadable_not_absent(tmp_path, monkeypatch):
    """The reported defect: an inaccessible snapshot read as "this project keeps none".

    ``Path.exists()`` returns False both for nothing-is-there and for a stat this
    process may not complete, and the ``is_symlink()`` fallback that followed it failed
    the same way. A `PROGRESS.md` under a directory whose ACL denies traversal therefore
    rendered as a settled fact about the project instead of as unknown. Only a definite
    ``FileNotFoundError`` is absence.
    """
    _write(tmp_path, "- ✅ real content that cannot be reached\n")

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "lstat", refuse)
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.reason == "unreadable"
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)


@pytest.mark.parametrize(
    "error", [PermissionError(13, "denied"), OSError(5, "I/O error"), NotADirectoryError(20, "nope")]
)
def test_no_stat_failure_but_absence_may_report_absent(tmp_path, monkeypatch, error):
    # The distinction stated at the level every platform can test: whatever else the
    # OS raises, it is not "there is no snapshot".
    _write(tmp_path, "- ✅ done\n")

    def refuse(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(Path, "lstat", refuse)
    assert read_progress_snapshot(tmp_path).reason == "unreadable"


def test_a_snapshot_under_a_directory_denying_traversal_is_unreadable(tmp_path):
    """The same case with the OS enforcing it, where the platform allows that.

    Skipped rather than faked when the account cannot deny itself traversal, so the
    record says whether the ACL path was actually exercised.
    """
    root = tmp_path / "project"
    _write(root, "- ✅ real content behind the deny\n")
    locked = root / ".agora"
    if not deny_self_traverse(locked):
        pytest.skip("this platform/account cannot deny itself traversal")
    try:
        snapshot = read_progress_snapshot(root)
        if snapshot.available:
            pytest.skip("the deny did not take effect for this token (elevated or bypassed)")
        assert snapshot.reason == "unreadable", "a denied read must not report 'no snapshot'"
        assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (None, None, None)
    finally:
        restore_traverse(locked)


def test_a_broken_symlink_at_the_snapshot_path_is_unreadable_not_absent(tmp_path):
    # Something is recorded at the path; "this project keeps no snapshot" would be false.
    path = tmp_path / SNAPSHOT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(tmp_path / "nowhere.md")
    except (OSError, NotImplementedError):
        pytest.skip("this platform/account cannot create symlinks")
    assert read_progress_snapshot(tmp_path).reason == "unreadable"


def test_oversized_snapshot_is_unknown_rather_than_a_floor(tmp_path):
    _write(tmp_path, "- ✅ done\n" + "x" * (MAX_SNAPSHOT_BYTES + 1))
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is False
    # Counting a prefix would report a floor; a floor rendered as a percentage is a
    # wrong number, so the reason is its own and the counts stay absent.
    assert snapshot.reason == "too_large"
    assert snapshot.done is None


def test_read_snapshot_counts_are_exact(tmp_path):
    _write(tmp_path, "- ✅ a\n- ✅ b\n- 🔵 c\n- ⬜ d\n- ⬜ e\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True and snapshot.reason is None
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (2, 1, 2)


def test_a_snapshot_with_no_items_is_available_with_zero_items(tmp_path):
    # Present, readable, and genuinely tracking nothing — no markers of any convention.
    # This is not "absent", not "unreadable", and not "another convention"; the display
    # layer turns 0 items into "no number", not into 0%.
    _write(tmp_path, "# Progress\n\nNothing tracked with markers yet.\n")
    snapshot = read_progress_snapshot(tmp_path)
    assert snapshot.available is True
    assert (snapshot.done, snapshot.in_progress, snapshot.todo) == (0, 0, 0)


def test_payload_shape_is_the_api_contract(tmp_path):
    _write(tmp_path, "- ✅ a\n")
    payload = read_progress_snapshot(tmp_path).payload()
    assert payload == {
        "available": True,
        "reason": None,
        "done": 1,
        "in_progress": 0,
        "todo": 0,
        "source": SNAPSHOT_RELATIVE_PATH.as_posix(),
    }
