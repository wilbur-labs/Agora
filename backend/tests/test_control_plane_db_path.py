"""Where the control-plane database is, and refusing to guess when there are two.

The rule these pin: a relative path in config.yaml is anchored to the directory
holding config.yaml, never to the directory the process happened to start in. It
used to be the latter, so an installation can already have data at the old
location — hence the second half of this file, which is about refusing rather than
switching.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agora.config.settings import (
    ControlPlaneDatabaseAmbiguity,
    assert_no_database_ambiguity,
    resolve_control_plane_db_path,
    resolve_legacy_cwd_db_path,
)


@pytest.fixture
def anchor(tmp_path) -> Path:
    """A directory standing in for the one holding config.yaml."""
    install = tmp_path / "install"
    install.mkdir()
    return install


@pytest.fixture
def elsewhere(tmp_path, monkeypatch) -> Path:
    """A working directory that is *not* the installation. Every test runs from here,
    so an accidental cwd-relative resolution shows up as a wrong path rather than as
    a coincidence."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    return other


# --- anchoring -----------------------------------------------------------------


def test_a_relative_data_dir_follows_the_config_file_not_the_shell(anchor, elsewhere):
    config = {"memory": {"data_dir": "./data"}}
    assert resolve_control_plane_db_path(config, anchor=anchor) == anchor / "data" / "agora.db"
    # And the old rule is what it is being distinguished from.
    assert resolve_legacy_cwd_db_path(config) == elsewhere / "data" / "agora.db"


def test_a_relative_explicit_db_path_is_anchored_too(anchor, elsewhere):
    # Rule 1 was the one place a relative path stayed cwd-relative even after the
    # data_dir rule was fixed -- the same bug, one rule higher up.
    config = {"control_plane": {"db_path": "state/control.db"}}
    assert resolve_control_plane_db_path(config, anchor=anchor) == anchor / "state" / "control.db"


def test_an_absolute_db_path_is_left_exactly_alone(anchor, elsewhere, tmp_path):
    explicit = tmp_path / "somewhere" / "chosen.db"
    config = {"control_plane": {"db_path": str(explicit)}}
    assert resolve_control_plane_db_path(config, anchor=anchor) == explicit
    # Absolute is the escape hatch the ambiguity error recommends, so it has to
    # resolve identically under both rules.
    assert resolve_legacy_cwd_db_path(config) == explicit


def test_the_last_resort_default_is_anchored_as_well(anchor, elsewhere):
    assert resolve_control_plane_db_path({}, anchor=anchor) == anchor / "data" / "agora.db"


# --- refusing to guess ----------------------------------------------------------


def _make_db(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_a_database_at_the_old_location_stops_startup_and_both_paths_are_named(
    anchor, elsewhere
):
    config = {"memory": {"data_dir": "./data"}}
    legacy = _make_db(elsewhere / "data" / "agora.db", b"old")

    with pytest.raises(ControlPlaneDatabaseAmbiguity) as caught:
        assert_no_database_ambiguity(config, anchor=anchor)

    message = str(caught.value)
    # Both, absolute. A refusal that does not say which two files it is torn
    # between leaves the operator no move to make.
    assert str(anchor / "data" / "agora.db") in message
    assert str(legacy) in message


def test_it_still_refuses_when_the_anchored_path_also_has_a_database(anchor, elsewhere):
    # The case the first, narrower check would have missed -- and the case actually
    # present on the machine that check was written for: databases at *both* paths,
    # different sizes, different dates. A guard that passes exactly where the hazard
    # lives is worse than no guard, because it reads as proof.
    config = {"memory": {"data_dir": "./data"}}
    _make_db(elsewhere / "data" / "agora.db", b"old-and-larger")
    _make_db(anchor / "data" / "agora.db", b"new")

    with pytest.raises(ControlPlaneDatabaseAmbiguity):
        assert_no_database_ambiguity(config, anchor=anchor)


def test_nothing_at_the_old_location_means_there_is_nothing_to_be_torn_between(
    anchor, elsewhere
):
    config = {"memory": {"data_dir": "./data"}}
    assert assert_no_database_ambiguity(config, anchor=anchor) == anchor / "data" / "agora.db"


def test_an_absolute_db_path_answers_the_question_and_ends_the_ambiguity(
    anchor, elsewhere, tmp_path
):
    # The way out the error message recommends. A stale file at the old relative
    # location must not keep an installation that has said what it means from starting.
    _make_db(elsewhere / "data" / "agora.db", b"stale")
    chosen = tmp_path / "chosen" / "control.db"
    config = {
        "memory": {"data_dir": "./data"},
        "control_plane": {"db_path": str(chosen)},
    }
    assert assert_no_database_ambiguity(config, anchor=anchor) == chosen


def test_starting_from_the_installation_itself_is_never_ambiguous(anchor, monkeypatch):
    # Both rules land on the same file, so the presence of that file is not a
    # conflict -- it is the database, seen twice.
    monkeypatch.chdir(anchor)
    config = {"memory": {"data_dir": "./data"}}
    _make_db(anchor / "data" / "agora.db", b"the one")
    assert assert_no_database_ambiguity(config, anchor=anchor) == anchor / "data" / "agora.db"


def test_two_spellings_of_one_path_are_one_database(tmp_path):
    # Tested on the comparison itself, deliberately. The previous version of this
    # dressed it up as an end-to-end scenario that did not actually produce two
    # spellings of one file -- it passed with `_same_file` stubbed to always return
    # False, which makes it a test that could not fail.
    from agora.config.settings import _same_file

    plain = tmp_path / "data" / "agora.db"
    detoured = tmp_path / "sub" / ".." / "data" / "agora.db"
    assert _same_file(detoured, plain)          # neither exists yet
    plain.parent.mkdir(parents=True)
    plain.write_bytes(b"x")
    assert _same_file(detoured, plain)          # and once it does


def test_a_relatively_named_config_file_does_not_move_the_anchor(tmp_path, monkeypatch):
    """`config_dir()` takes `.parent` of the loaded path. A relative name left a parent
    of `.`, which resolves against whatever the working directory is when it is
    *asked* -- reintroducing one level up the very cwd-dependence this change removes.
    """
    import agora.config.settings as settings

    install = tmp_path / "install"
    install.mkdir()
    (install / "config.yaml").write_text("memory:\n  data_dir: ./data\n", encoding="utf-8")

    monkeypatch.chdir(install)
    settings.reset_config()
    config = settings.get_config("config.yaml")          # loaded by a relative name
    assert settings.config_dir() == install.resolve()

    monkeypatch.chdir(tmp_path)                          # now stand somewhere else
    assert settings.config_dir() == install.resolve()
    assert settings.resolve_control_plane_db_path(config) == install.resolve() / "data" / "agora.db"
    settings.reset_config()


def test_an_unreadable_old_location_is_not_treated_as_an_empty_one(anchor, elsewhere, monkeypatch):
    # "Could not find out" is the third answer, and it is not absence. Silently
    # starting here is the exact move this check exists to refuse.
    import agora.config.settings as settings
    from agora.fsprobe import PathProbe

    legacy = elsewhere / "data" / "agora.db"

    def probe(path, *, follow_symlinks=True):
        if Path(path) == legacy:
            return PathProbe("unknown", error=PermissionError(13, "denied"))
        return PathProbe("absent")

    monkeypatch.setattr(settings, "probe_path", probe)
    with pytest.raises(ControlPlaneDatabaseAmbiguity, match="Could not tell"):
        assert_no_database_ambiguity({"memory": {"data_dir": "./data"}}, anchor=anchor)


def test_one_installation_gets_one_database_whoever_is_asking(tmp_path, monkeypatch):
    """The invariant the resolver's own docstring opens with, pinned.

    Rule 3 used to take a base from its caller: ``ProjectRegistry`` passed its
    ``project_root`` and the task store passed none. While both were the repository
    root that was invisible; anchoring to config.yaml pulled them apart, and a
    project list and a task list in one process would have been written to two files.
    """
    import agora.config.settings as settings
    from agora.projects import ProjectRegistry
    from agora.tasks.store import TaskStore

    install = tmp_path / "install"
    (install / ".agora").mkdir(parents=True)
    (install / ".agora" / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "_config_path", install / "config.yaml")

    # A registry whose own project_root is somewhere else entirely -- the case that
    # used to decide the database.
    elsewhere_root = tmp_path / "some-other-tree"
    elsewhere_root.mkdir()
    config = {"projects": {"registry_path": ".agora/projects.yaml", "default": "none"}}

    expected = install / ".agora" / "agora.db"
    assert resolve_control_plane_db_path(config) == expected

    registry = ProjectRegistry(config, project_root=elsewhere_root)
    assert registry.store.db_path == expected
    assert TaskStore(resolve_control_plane_db_path(config)).db_path == expected


def test_something_at_the_old_path_that_is_not_a_file_is_not_a_second_database(
    anchor, elsewhere
):
    """`present` covers directories and device nodes too. None of those is a database,
    the old rule could never have opened one as such, and there is therefore no data at
    risk -- which is the only thing this check protects. Refusing to start over one
    would be a guard wider than its own reason."""
    config = {"memory": {"data_dir": "./data"}}
    (elsewhere / "data" / "agora.db").mkdir(parents=True)   # a directory by that name

    assert assert_no_database_ambiguity(config, anchor=anchor) == anchor / "data" / "agora.db"


def _probe_returning(result):
    """A stand-in probe that *insists* on being asked to follow symlinks.

    Without this assertion the two tests below could not fail on the thing they are
    about: if production switched to ``follow_symlinks=False``, every symlink would
    probe as ``other`` with no error, the guard would proceed past a link pointing at
    a real database, and a fake that ignores the flag would keep handing back the
    error-carrying probe and stay green.
    """

    def probe(path, *, follow_symlinks=True):
        assert follow_symlinks, (
            "the guard must follow symlinks; an unfollowed link probes as `other` "
            "with no error and would slip past the ambiguity check"
        )
        return result

    return probe


def test_a_symlink_whose_target_cannot_be_read_is_not_a_settled_absence(
    anchor, elsewhere, monkeypatch
):
    """`probe_path` reports a symlink with an unreadable target as ``other`` -- not
    ``unknown`` -- because something *is* recorded at the path. For "is a database
    sitting there" that settles nothing: the link may point straight at one. The
    `kind != "file"` shortcut read it as "nothing to worry about" and started on the
    other database, which is the silent switch this check exists to prevent, hiding one
    representation away from where it was being looked for.
    """
    import agora.config.settings as settings
    from agora.fsprobe import PathProbe

    monkeypatch.setattr(
        settings,
        "probe_path",
        _probe_returning(PathProbe("other", error=PermissionError(13, "denied"))),
    )
    with pytest.raises(ControlPlaneDatabaseAmbiguity, match="Could not tell"):
        assert_no_database_ambiguity({"memory": {"data_dir": "./data"}}, anchor=anchor)


def test_a_dangling_symlink_at_the_old_path_does_not_block_startup(
    anchor, elsewhere, monkeypatch
):
    """The other half, and the line between them: a target that raises
    ``FileNotFoundError`` is settled -- there is nothing behind the link, so no data is
    at risk. That is fsprobe's own rule ("only FileNotFoundError is absence") applied to
    the target instead of the name. Without this half, every dangling link would brick
    startup, which would be a guard wider than its reason.
    """
    import agora.config.settings as settings
    from agora.fsprobe import PathProbe

    monkeypatch.setattr(
        settings,
        "probe_path",
        _probe_returning(PathProbe("other", error=FileNotFoundError(2, "gone"))),
    )
    assert assert_no_database_ambiguity(
        {"memory": {"data_dir": "./data"}}, anchor=anchor
    ) == anchor / "data" / "agora.db"
