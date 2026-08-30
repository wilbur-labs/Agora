"""The one rule this repository kept getting wrong: only ``FileNotFoundError`` is absence.

``Path.exists()`` answers False both for "nothing is there" and for "this process could
not find out", and that collapse was shipped twice in one night — an unreadable
``.agora/projects.yaml`` reported as an absent one, and an unreadable ``PROGRESS.md``
reported as "this project keeps no snapshot". These pin the distinction at the level the
platform allows: a real denied directory where the OS will enforce it, and a raised
``OSError`` everywhere else.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agora.fsprobe import PathProbe, probe_path

from tests.acl_support import deny_self_traverse, restore_traverse


# ------------------------------------------------------------------- absence vs unknown


def test_a_missing_path_is_absent(tmp_path):
    probe = probe_path(tmp_path / "nothing-here")
    assert probe.kind == "absent"
    assert probe.absent is True and probe.unknown is False
    assert (probe.exists(), probe.is_file(), probe.is_dir()) == (False, False, False)


def test_a_missing_parent_is_still_absence(tmp_path):
    # Nothing along the path exists, which is a definite negative, not a failure to look.
    assert probe_path(tmp_path / "no" / "such" / "dir" / "file.md").absent is True


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "Permission denied"),
        OSError(5, "I/O error"),
        NotADirectoryError(20, "Not a directory"),
        BlockingIOError(11, "Resource temporarily unavailable"),
    ],
)
def test_any_stat_failure_that_is_not_absence_is_unknown(tmp_path, monkeypatch, error):
    """The regression these fixes are about: a raised OSError must never read as absent.

    This is the platform-independent half of the ACL case below — the same code path,
    reached by making the stat fail directly instead of by making the OS refuse it.
    """
    def refuse(self, *args, **kwargs):
        raise error

    monkeypatch.setattr(Path, "lstat", refuse)
    probe = probe_path(tmp_path / "PROGRESS.md")
    assert probe.kind == "unknown"
    assert probe.unknown is True and probe.absent is False
    # Tri-state: None, so no payload can render this as a definite False.
    assert (probe.exists(), probe.is_file(), probe.is_dir()) == (None, None, None)
    assert probe.reason() == type(error).__name__


def test_a_file_under_a_directory_that_denies_traversal_is_unknown_not_absent(tmp_path):
    """The real ACL case, enforced by the OS rather than by a patched stat.

    Skipped where the platform cannot build it (no account name, or a root/elevated
    token that ignores the denial), because a test that quietly passes without
    exercising the case is worse than one that says it did not run.
    """
    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked / "PROGRESS.md"
    target.write_text("- ✅ real content behind the deny\n", encoding="utf-8")
    if not deny_self_traverse(locked):
        pytest.skip("this platform/account cannot deny itself traversal")
    try:
        probe = probe_path(target)
        if probe.kind == "file":
            pytest.skip("the deny did not take effect for this token (elevated or bypassed)")
        assert probe.kind == "unknown", "a denied stat must not be classified as absence"
        assert probe.absent is False
        assert probe.exists() is None
        assert probe.reason() == "PermissionError"
    finally:
        restore_traverse(locked)

# ------------------------------------------------------------------------ present kinds


def test_a_regular_file_carries_its_size(tmp_path):
    path = tmp_path / "file.md"
    path.write_bytes(b"12345")
    probe = probe_path(path)
    assert probe.kind == "file" and probe.size == 5
    assert (probe.exists(), probe.is_file(), probe.is_dir()) == (True, True, False)


def test_a_directory_is_present_and_is_not_a_file(tmp_path):
    probe = probe_path(tmp_path)
    assert probe.kind == "dir"
    assert (probe.exists(), probe.is_file(), probe.is_dir()) == (True, False, True)
    # No size on a non-file: a size read off a directory probe would be meaningless.
    assert probe.size is None


def test_a_symlink_to_a_file_reads_as_the_file(tmp_path):
    target = tmp_path / "target.md"
    target.write_text("hi", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/account cannot create symlinks")
    assert probe_path(link).kind == "file"


def test_a_broken_symlink_is_present_but_unusable_not_absent(tmp_path):
    link = tmp_path / "dangling.md"
    try:
        link.symlink_to(tmp_path / "nothing-here")
    except (OSError, NotImplementedError):
        pytest.skip("this platform/account cannot create symlinks")
    probe = probe_path(link)
    # Something *is* recorded at this path. "Nothing is here" would be false.
    assert probe.kind == "other"
    assert probe.absent is False and probe.exists() is True
    assert probe.is_file() is False


def test_not_following_a_symlink_reports_the_link_itself(tmp_path):
    target = tmp_path / "target.md"
    target.write_text("hi", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/account cannot create symlinks")
    assert probe_path(link, follow_symlinks=False).kind == "other"


def test_reason_is_empty_when_nothing_failed():
    assert PathProbe("absent").reason() == ""
    assert PathProbe("file", size=0).reason() == ""
