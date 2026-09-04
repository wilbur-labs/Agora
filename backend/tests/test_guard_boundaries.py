"""The isolation guard's known boundaries, carried by the suite rather than by prose.

``tests/conftest.py`` fails a test that opens a database or creates a directory
outside the allowed temp roots *through the module attributes it patches* — which is how
every store in ``agora/`` gets there, and is not the same as "any test". Three ways
past it are known and **not closed** (a fourth, a ``connect`` bound before the fixture
runs, is a limit of patching itself and is stated in ``conftest.py`` rather than here,
because there is nothing to pick up). They
are written here as the behaviour we want, skipped with the reason, so the debt shows
up in every run's summary (``-rs``) instead of living in a docstring that reads like
reassurance. Deleting a skip is how the next person picks one up: it then fails
honestly until the guard covers that case.

Why not closed: the guard's job is to catch **accidents** — a factory that resolves a
real path, a new startup path, a store this conftest's list has fallen behind. It is
not a sandbox against a test that is *trying* to escape, and it cannot become one:
file writes are outside its scope by design (measured, stated in conftest.py), so a
test that wants to reach the repository can simply ``open(..., "w")``. All three
cases below need a test to go out of its way; none can happen by accident in the
current code, where ``uri=True`` and ``dir_fd=`` appear nowhere.

Tracked as CR-8 in the workspace RESUME for this task. See adr/0015 there for why a
deferred hazard has to produce something like this file rather than a paragraph.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

#: A harmless name outside the temp tree. Nothing reads it; it exists so these tests
#: do not have to aim at real data to prove the boundary.
_OUTSIDE = Path(__file__).resolve().parents[1] / "zz-guard-boundary-probe.db"


@pytest.mark.skip(
    reason="CR-8: the guard does not parse sqlite file: URIs, so uri=True gets past it. "
    "Unused in agora/ today. Delete this skip to pick it up."
)
def test_the_guard_should_see_through_a_sqlite_file_uri(tmp_path, monkeypatch):
    """``sqlite3.connect("file:…", uri=True)`` hands SQLite a URI, not a filename.

    The guard normalises the whole string as a path, so a URI naming an absolute
    location outside the temp tree looks like a relative filename inside it.

    The ``chdir`` is load-bearing, and without it this test would have been the thing
    it exists to prevent: run from ``backend/``, the guard's *lexical* reading of
    ``file:D:/…`` resolves under the repository — outside the temp tree — so the guard
    raises for the wrong reason and the test passes while the bypass is wide open.
    Deleting the skip would then prove nothing. From inside ``tmp_path`` that same
    misreading lands in the allowed tree, so only real URI parsing can make this pass.
    """
    monkeypatch.chdir(tmp_path)
    try:
        with pytest.raises(AssertionError, match="outside the allowed temp roots"):
            sqlite3.connect(f"file:{_OUTSIDE.as_posix()}?mode=rwc", uri=True).close()
    finally:
        _OUTSIDE.unlink(missing_ok=True)


@pytest.mark.skip(
    reason="CR-8: the resolution memo caches an existing ancestor and does not revalidate "
    "it, so replacing that directory with a junction serves a stale answer. "
    "Requires a test to deliberately swap it. Delete this skip to pick it up."
)
def test_the_guard_should_revalidate_a_cached_ancestor_that_became_a_link(tmp_path):
    """A cached directory, deleted and replaced by a junction to somewhere real."""
    switch = tmp_path / "switch"
    switch.mkdir()
    sqlite3.connect(str(switch / "warm.db")).close()  # caches `switch` as itself
    switch.rmdir()
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(switch), str(_OUTSIDE.parent)],
        capture_output=True, text=True,
    )
    if made.returncode != 0:
        pytest.skip("this platform cannot create a junction")
    with pytest.raises(AssertionError, match="outside the allowed temp roots"):
        (switch / "zz-guard-boundary-dir").mkdir()


# Two reasons, and on Windows only the first is printed: `skipif` wins, so `pytest -rs`
# there says "dir_fd is POSIX-only" and never mentions CR-8. It therefore names CR-8
# itself — a boundary that goes unreported on the machine this suite mostly runs on is
# a debt with no artifact, which is the whole thing this file exists to avoid.
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="CR-8 (dir_fd bypass): dir_fd is POSIX-only, so this boundary cannot be "
    "exercised on Windows at all — it is unreported here, not resolved.",
)
@pytest.mark.skip(
    reason="CR-8: os.mkdir(dir_fd=…) resolves relative to a file descriptor, which the "
    "guard does not read. Unused in agora/ today. Delete this skip to pick it up."
)
def test_the_guard_should_reject_a_directory_created_against_a_foreign_fd(tmp_path):
    """``dir_fd`` makes the path relative to a descriptor, not to the cwd."""
    outside_fd = os.open(str(_OUTSIDE.parent), os.O_RDONLY)
    try:
        os.chdir(tmp_path)
        with pytest.raises(AssertionError, match="outside the allowed temp roots"):
            os.mkdir("zz-guard-boundary-dir", dir_fd=outside_fd)
    finally:
        os.close(outside_fd)
