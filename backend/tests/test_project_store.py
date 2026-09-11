"""Projects are authoritative in the database; the YAML is an import source.

These tests pin the properties the migration exists to guarantee: a project a
task references is visible to SQL, importing the maintainer's file is idempotent,
changes made through Agora survive re-import, and a file that cannot be read is
an error rather than an empty project list.
"""
from __future__ import annotations

import sqlite3

import pytest
import yaml

from agora.config.settings import resolve_control_plane_db_path
from agora.execution.models import CreateRunRequest
from agora.execution.store import ExecutionStore
from agora.projects import ProjectRegistry
from agora.projects.registry import TodayFocusFullError
from agora.projects import store as store_module
from agora.projects.importer import (
    ProjectImportError,
    ProjectImportSourceMissing,
    read_project_yaml,
)
from agora.projects.store import (
    SETTING_TODAY_FOCUS,
    ORIGIN_AGORA,
    ORIGIN_YAML_IMPORT,
    ProjectStore,
    project_fields,
)
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore

_YAML = """\
current: alpha
projects_base: {base}
projects:
  alpha:
    name: Alpha
    root: {alpha}
    workspaces:
      codex: {alpha}/.agora/workspaces/codex
      claude: {alpha}/.agora/workspaces/claude
      kiro: {alpha}/.agora/workspaces/kiro
    worker_agent: codex
    reviewer_agent: claude
  beta:
    name: Beta
    root: {beta}
    workspaces:
      codex: {beta}/.agora/workspaces/codex
"""


def _posix(path) -> str:
    return str(path).replace("\\", "/")


def _write_yaml(tmp_path, *, worker: str = "codex", name: str = "Alpha") -> "object":
    source = tmp_path / ".agora" / "projects.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    text = _YAML.format(
        base=_posix(tmp_path / "base"),
        alpha=_posix(tmp_path / "alpha"),
        beta=_posix(tmp_path / "beta"),
    )
    text = text.replace("worker_agent: codex", f"worker_agent: {worker}")
    text = text.replace("name: Alpha", f"name: {name}")
    source.write_text(text, encoding="utf-8")
    return source


def _config(tmp_path) -> dict:
    return {"projects": {"registry_path": str(tmp_path / ".agora" / "projects.yaml"),
                         "default": "alpha"}}


def _registry(tmp_path) -> ProjectRegistry:
    return ProjectRegistry(_config(tmp_path), project_root=tmp_path)


def test_projects_live_in_the_same_database_file_as_tasks(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)

    db_path = resolve_control_plane_db_path(_config(tmp_path))
    assert registry.store.db_path == db_path
    assert TaskStore(db_path).db_path == registry.store.db_path

    # One file, so a project and the rows referencing it are joinable — the
    # property the YAML store could not offer at any price.
    db = sqlite3.connect(str(db_path))
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    assert {"projects", "project_settings", "tasks"} <= tables


def test_import_is_idempotent_and_does_not_duplicate(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    first = registry.import_status()["last_import"]
    assert sorted(first["imported"]) == ["alpha", "beta"]

    second = registry.import_from_yaml()
    assert second.imported == ()
    assert sorted(second.unchanged) == ["alpha", "beta"]
    assert second.conflicts == ()

    third = _registry(tmp_path)
    assert sorted(third.list_projects()) == ["alpha", "beta"]
    assert third.store.count() == 2


def test_yaml_file_is_never_rewritten_by_an_import(tmp_path):
    source = _write_yaml(tmp_path)
    before = source.read_bytes()

    registry = _registry(tmp_path)
    registry.use("beta")
    registry.set_project_agents("alpha", worker_agent="kiro", reviewer_agent="claude")
    registry.set_projects_base(tmp_path / "moved-base")
    registry.import_from_yaml()

    assert source.exists()
    assert source.read_bytes() == before


def test_change_made_through_agora_survives_reimport_and_is_reported(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.set_project_agents("alpha", worker_agent="kiro", reviewer_agent="claude")

    # The file still says codex, and now disagrees with the store.
    _write_yaml(tmp_path, worker="claude", name="Alpha Renamed")
    result = registry.import_from_yaml()

    assert registry.get("alpha").worker_agent == "kiro"
    assert registry.get("alpha").name == "Alpha"
    assert [c.project_id for c in result.conflicts] == ["alpha"]
    assert result.conflicts[0].fields == ("name", "worker_agent")
    assert "kept the values stored in Agora" in result.conflicts[0].describe()
    assert "alpha" not in result.updated


def test_untouched_project_follows_the_import_file(tmp_path):
    """The other half of the both-sides-differ rule.

    A row still identical to what was imported has no Agora-side change to
    protect, so the file — which the maintainer just edited — wins.
    """
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    assert registry.get("alpha").worker_agent == "codex"

    _write_yaml(tmp_path, worker="kiro", name="Alpha Renamed")
    result = registry.import_from_yaml()

    assert result.updated == ("alpha",)
    assert result.conflicts == ()
    assert registry.get("alpha").worker_agent == "kiro"
    assert registry.get("alpha").name == "Alpha Renamed"
    # Identity is the project_id; a re-import updates properties in place.
    assert registry.store.count() == 2
    assert registry.store.provenance("alpha").origin == ORIGIN_YAML_IMPORT


def test_project_created_through_agora_is_not_touched_by_import(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.add("gamma", tmp_path / "gamma", "Gamma")
    assert registry.store.provenance("gamma").origin == ORIGIN_AGORA

    registry.import_from_yaml()

    assert sorted(registry.list_projects()) == ["alpha", "beta", "gamma"]
    assert registry.get("gamma").name == "Gamma"


def test_current_project_pointer_survives_reimport(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    assert registry.current_project_id() == "alpha"

    registry.use("beta")
    registry.import_from_yaml()
    assert registry.current_project_id() == "beta"

    # And across a fresh registry, which re-imports on construction.
    assert _registry(tmp_path).current_project_id() == "beta"


def test_pointer_is_repaired_when_it_names_a_missing_project(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.store.set_setting("current_project_id", "vanished")

    repaired = _registry(tmp_path)
    assert repaired.current_project_id() in {"alpha", "beta"}
    assert repaired.store.exists(repaired.current_project_id())


@pytest.mark.parametrize(
    "text",
    [
        "projects: [not, a, mapping]",
        "current: alpha\nprojects:\n  alpha:\n    name: Alpha\n",  # no root
        "projects:\n  alpha: 3\n",
        "",
        "current: alpha\n",  # no projects key at all
        "projects:\n  alpha:\n    root: /tmp/a\n    worker_agent: gemini\n",
        "projects: {\n",  # unparseable
    ],
)
def test_malformed_import_source_is_an_error_not_an_empty_list(tmp_path, text):
    source = tmp_path / ".agora" / "projects.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")

    with pytest.raises(ProjectImportError):
        read_project_yaml(source, project_root=tmp_path)

    # And the registry refuses to come up empty rather than reporting no projects.
    with pytest.raises(ProjectImportError):
        _registry(tmp_path)
    assert source.read_text(encoding="utf-8") == text


def test_broken_import_source_does_not_erase_an_already_populated_store(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    assert sorted(registry.list_projects()) == ["alpha", "beta"]

    (tmp_path / ".agora" / "projects.yaml").write_text("projects: {\n", encoding="utf-8")
    recovered = _registry(tmp_path)

    # The database is the source of truth, so it keeps serving; the failure is
    # reported instead of being smuggled in as a shorter list.
    assert sorted(recovered.list_projects()) == ["alpha", "beta"]
    status = recovered.import_status()
    assert status["error"] is not None
    assert "Could not import projects" in status["error"]
    assert status["source_present"] is True


def test_import_error_clears_once_the_source_is_fixed(tmp_path):
    _write_yaml(tmp_path)
    _registry(tmp_path)
    (tmp_path / ".agora" / "projects.yaml").write_text("projects: {\n", encoding="utf-8")
    assert _registry(tmp_path).import_status()["error"] is not None

    _write_yaml(tmp_path)
    assert _registry(tmp_path).import_status()["error"] is None


def test_removing_the_source_clears_a_recorded_import_failure(tmp_path):
    _write_yaml(tmp_path)
    source = tmp_path / ".agora" / "projects.yaml"
    _registry(tmp_path)
    source.write_text("projects: {\n", encoding="utf-8")
    assert _registry(tmp_path).import_status()["error"] is not None

    source.unlink()
    status = _registry(tmp_path).import_status()
    assert status["source_present"] is False
    assert status["error"] is None
    assert sorted(_registry(tmp_path).list_projects()) == ["alpha", "beta"]


def test_task_and_its_runs_resolve_their_project_through_the_store(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    tasks = TaskStore(registry.store.db_path)
    runs = ExecutionStore(tasks)

    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Implement feature"))
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
            (TaskState.PLANNED.value, task.task_id),
        )
    project = registry.get(task.project_id)
    workspace = project.workspaces["codex"]
    run = runs.create(
        CreateRunRequest(
            task_id=task.task_id,
            adapter="codex",
            prompt="do the thing",
            timeout_seconds=10,
            expected_task_version=2,
        ),
        project_id=project.project_id,
        workspace=workspace,
        stored_command=["echo", "{prompt}"],
    )

    assert registry.get(run.project_id).root == project.root

    # The run's project is now reachable by query, which is the whole point: a
    # run could previously be gated against a project no SQL statement could see.
    db = sqlite3.connect(str(registry.store.db_path))
    row = db.execute(
        """
        SELECT p.project_id, p.root
          FROM execution_runs r
          JOIN tasks t ON t.task_id = r.task_id
          JOIN projects p ON p.project_id = t.project_id
         WHERE r.run_id = ?
        """,
        (run.run_id,),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "alpha"
    assert row[1] == str(project.root)


def test_root_is_a_property_of_the_project_not_its_identity(tmp_path):
    """A moved root keeps the project, its pointer and its tasks.

    W2/W3 will make "where the project is" environment-dependent; nothing here
    may key on the local path.
    """
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    tasks = TaskStore(registry.store.db_path)
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Portable"))
    registry.use("alpha")

    moved = tmp_path / "relocated-alpha"
    source = tmp_path / ".agora" / "projects.yaml"
    source.write_text(
        source.read_text(encoding="utf-8").replace(_posix(tmp_path / "alpha"), _posix(moved)),
        encoding="utf-8",
    )
    result = registry.import_from_yaml()

    assert result.updated == ("alpha",)
    reread = _registry(tmp_path)
    assert reread.get("alpha").root == moved.resolve()
    assert reread.current_project_id() == "alpha"
    assert tasks.get(task.task_id).project_id == "alpha"


def test_store_refuses_a_pointer_to_an_unknown_project(tmp_path):
    store = ProjectStore(tmp_path / "agora.db")
    with pytest.raises(KeyError):
        store.set_current_project_id("nope")
    assert store.current_project_id() is None


# --------------------------------------------------------------------------
# What an import may not do quietly: overwrite a concurrent write, shorten the
# project list, mistake an unreadable source for an absent one, mistake a
# change-and-revert for no change, or drop a pointer it cannot migrate.
# --------------------------------------------------------------------------


def test_the_import_decision_and_its_write_are_one_transaction(tmp_path, monkeypatch):
    """No other write can land between the comparison and the replacement.

    Reading the row, deciding outside any transaction and then issuing an
    unconditional UPDATE would silently overwrite a change made in between. That
    window is not theoretical: constructing a registry runs an import, so it is
    open against ordinary API traffic.
    """
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    db_path = registry.store.db_path

    observed: list[str] = []
    original = store_module._untouched_since_import

    def hooked(row):
        # Called inside the import transaction, right where the decision is taken.
        probe = sqlite3.connect(str(db_path), timeout=0)
        try:
            probe.execute(
                "UPDATE projects SET worker_agent = 'kiro' WHERE project_id = 'alpha'"
            )
            probe.commit()
            observed.append("wrote")
        except sqlite3.OperationalError:
            observed.append("locked out")
        finally:
            probe.close()
        return original(row)

    monkeypatch.setattr(store_module, "_untouched_since_import", hooked)
    _write_yaml(tmp_path, worker="claude", name="Alpha Renamed")
    result = registry.import_from_yaml()

    # The hook ran exactly once (only alpha differs), and the concurrent write
    # could not get in — so there was nothing to overwrite.
    assert observed == ["locked out"]
    assert result.updated == ("alpha",)
    assert registry.get("alpha").worker_agent == "claude"


_DUPLICATE_ALPHA = """\
projects:
  alpha:
    name: Alpha First
    root: {first}
  alpha:
    name: Alpha Second
    root: {second}
"""


def test_duplicate_keys_are_a_malformed_source_not_a_shorter_list(tmp_path):
    """The reviewer's reproduction: two blocks for one project.

    ``yaml.safe_load`` takes the last one and reports nothing wrong, so the first
    block disappears and a shorter list is presented as the maintainer's intent.
    """
    text = _DUPLICATE_ALPHA.format(
        first=_posix(tmp_path / "alpha-first"), second=_posix(tmp_path / "alpha-second")
    )
    source = tmp_path / ".agora" / "projects.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")

    # This is the data loss being rejected: a permissive load keeps one of two.
    assert list(yaml.safe_load(text)["projects"]) == ["alpha"]

    with pytest.raises(ProjectImportError) as err:
        read_project_yaml(source, project_root=tmp_path)
    assert "duplicate key 'alpha'" in str(err.value)

    # And nothing is imported: an ambiguous file is not a smaller one.
    with pytest.raises(ProjectImportError):
        _registry(tmp_path)
    assert source.read_text(encoding="utf-8") == text


def test_duplicate_keys_never_shorten_an_already_populated_project_list(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    assert sorted(registry.list_projects()) == ["alpha", "beta"]

    (tmp_path / ".agora" / "projects.yaml").write_text(
        _DUPLICATE_ALPHA.format(
            first=_posix(tmp_path / "alpha-first"), second=_posix(tmp_path / "alpha-second")
        ),
        encoding="utf-8",
    )
    recovered = _registry(tmp_path)

    assert sorted(recovered.list_projects()) == ["alpha", "beta"]
    assert recovered.get("alpha").name == "Alpha"
    status = recovered.import_status()
    assert "duplicate key 'alpha'" in status["error"]
    assert status["source_present"] is True


def test_a_duplicate_key_anywhere_in_the_source_is_rejected(tmp_path):
    source = tmp_path / ".agora" / "projects.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "projects:\n"
        "  alpha:\n"
        f"    root: {_posix(tmp_path / 'alpha')}\n"
        "    workspaces:\n"
        f"      codex: {_posix(tmp_path / 'alpha' / 'one')}\n"
        f"      codex: {_posix(tmp_path / 'alpha' / 'two')}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectImportError) as err:
        read_project_yaml(source, project_root=tmp_path)
    assert "duplicate key 'codex'" in str(err.value)


def test_an_unreadable_source_is_not_treated_as_an_absent_one(tmp_path):
    """A source that cannot be opened is unknown, and unknown fails closed.

    ``Path.exists()`` answers False for stat failures that are not absence, and
    the seeding path behind it would then hand back configured or default projects
    that look exactly like a machine which never had a source.
    """
    source = tmp_path / ".agora" / "projects.yaml"
    # Opening this fails, and not with FileNotFoundError.
    source.mkdir(parents=True)

    with pytest.raises(ProjectImportError) as err:
        _registry(tmp_path)
    assert not isinstance(err.value, ProjectImportSourceMissing)
    assert err.value.source_state == "unknown"

    store = ProjectStore(resolve_control_plane_db_path(_config(tmp_path)))
    assert store.count() == 0
    assert store.current_project_id() is None


def test_an_unreadable_source_keeps_serving_and_is_reported_as_unknown(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.use("beta")

    source = tmp_path / ".agora" / "projects.yaml"
    source.unlink()
    source.mkdir()
    recovered = _registry(tmp_path)

    assert sorted(recovered.list_projects()) == ["alpha", "beta"]
    assert recovered.current_project_id() == "beta"
    status = recovered.import_status()
    assert status["error"] is not None
    # Not False: nothing here established that the source is gone.
    assert status["source_present"] is None


def test_a_missing_source_is_still_recognised_as_absent(tmp_path):
    """The other side of the same rule: absence must stay recognisable."""
    with pytest.raises(ProjectImportSourceMissing):
        read_project_yaml(tmp_path / ".agora" / "projects.yaml", project_root=tmp_path)

    registry = _registry(tmp_path)
    status = registry.import_status()
    assert status["source_present"] is False
    assert status["error"] is None
    # Only definite absence unlocks seeding.
    assert list(registry.list_projects()) == ["alpha"]


def test_an_undecodable_source_is_unknown_not_present(tmp_path):
    """Bytes that are not text mean the read never completed.

    ``source_present`` is True only when the source was read. A UnicodeDecodeError
    is raised part-way through decoding, so nothing about the file's content was
    ever observed; calling that "present" reports an observation that was not made.
    """
    source = tmp_path / ".agora" / "projects.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    # A UTF-16 encoded document: valid YAML text, undecodable as UTF-8.
    source.write_bytes("projects:\n  alpha:\n    root: x\n".encode("utf-16"))

    with pytest.raises(ProjectImportError) as err:
        read_project_yaml(source, project_root=tmp_path)
    assert not isinstance(err.value, ProjectImportSourceMissing)
    assert err.value.source_state == "unknown"


def test_an_undecodable_source_fails_closed_and_reports_unknown(tmp_path):
    """Unknown must not unlock seeding, and must not render as absence."""
    source = tmp_path / ".agora" / "projects.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\xff\xfe\x00projects:")

    with pytest.raises(ProjectImportError) as err:
        _registry(tmp_path)
    assert err.value.source_state == "unknown"
    store = ProjectStore(resolve_control_plane_db_path(_config(tmp_path)))
    assert store.count() == 0

    # With projects already stored the registry keeps serving and reports unknown.
    _write_yaml(tmp_path)
    _registry(tmp_path)
    source.write_bytes(b"\xff\xfe\x00projects:")
    recovered = _registry(tmp_path)
    assert sorted(recovered.list_projects()) == ["alpha", "beta"]
    status = recovered.import_status()
    assert status["error"] is not None
    assert status["source_present"] is None


def test_every_import_error_that_reports_present_has_read_the_source(tmp_path):
    """The ``present`` default is only correct after the read completes.

    Guards the class of bug this test file was extended for: a raise site added
    before ``read_text`` returns would inherit ``source_state='present'`` and claim
    an observation nobody made. Every failure before that point must be explicit.
    """
    import ast
    import inspect

    from agora.projects import importer as importer_module

    tree = ast.parse(inspect.getsource(importer_module))
    read_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "read_project_yaml"
    )
    # The try/except around read_text is the only place presence is not yet known.
    read_try = next(node for node in read_fn.body if isinstance(node, ast.Try))
    for handler in read_try.handlers:
        raises = [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]
        assert raises, "an unhandled read failure would fall through as success"
        for raise_node in raises:
            call = raise_node.exc
            assert isinstance(call, ast.Call)
            name = getattr(call.func, "id", "")
            if name == "ProjectImportSourceMissing":
                continue  # absent by construction
            keywords = {kw.arg: kw.value for kw in call.keywords}
            assert "source_state" in keywords, (
                f"{name} raised before the read completed must state source_state"
            )
            assert isinstance(keywords["source_state"], ast.Constant)
            assert keywords["source_state"].value == "unknown"


def test_a_change_made_and_reverted_through_agora_still_beats_the_file(tmp_path):
    """The reviewer's second reproduction: change a value and change it back.

    The stored values are then identical to the import snapshot, so comparing
    values would call the row untouched and let the file overwrite it. The
    revision counter every Agora-side write bumps is what still knows better.
    """
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.set_project_agents("alpha", worker_agent="kiro", reviewer_agent="claude")
    registry.set_project_agents("alpha", worker_agent="codex", reviewer_agent="claude")

    provenance = registry.store.provenance("alpha")
    # The premise of the reproduction: values alone say "nobody touched this".
    assert provenance.imported_snapshot == project_fields(registry.get("alpha"))
    assert provenance.agora_revision == 2
    assert provenance.imported_revision == 0
    assert provenance.changed_in_agora_since_import is True

    _write_yaml(tmp_path, worker="claude", name="Alpha Renamed")
    result = registry.import_from_yaml()

    assert [c.project_id for c in result.conflicts] == ["alpha"]
    assert result.updated == ()
    assert registry.get("alpha").worker_agent == "codex"
    assert registry.get("alpha").name == "Alpha"


def test_an_accepted_update_rearms_the_untouched_check(tmp_path):
    """After the file wins once, a later edit must still be able to win.

    The accepted update records the counter it saw, so the next import compares
    against that instead of a stale zero — and an Agora write after it is
    protected again.
    """
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)

    _write_yaml(tmp_path, worker="kiro")
    assert registry.import_from_yaml().updated == ("alpha",)
    provenance = registry.store.provenance("alpha")
    assert provenance.imported_revision == provenance.agora_revision
    assert provenance.changed_in_agora_since_import is False

    _write_yaml(tmp_path, worker="kiro", name="Alpha Renamed")
    assert registry.import_from_yaml().updated == ("alpha",)
    assert registry.get("alpha").name == "Alpha Renamed"

    registry.set_project_agents("alpha", worker_agent="codex", reviewer_agent="claude")
    _write_yaml(tmp_path, worker="kiro", name="Alpha Again")
    result = registry.import_from_yaml()
    assert [c.project_id for c in result.conflicts] == ["alpha"]
    assert registry.get("alpha").name == "Alpha Renamed"
    assert registry.get("alpha").worker_agent == "codex"


def test_a_project_created_through_agora_is_never_overwritten_by_the_file(tmp_path):
    """It has no import revision, so "untouched since import" cannot be proved."""
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.add("gamma", tmp_path / "gamma", "Gamma")
    assert registry.store.provenance("gamma").imported_revision is None

    source = tmp_path / ".agora" / "projects.yaml"
    source.write_text(
        source.read_text(encoding="utf-8")
        + f"  gamma:\n    name: Gamma From File\n    root: {_posix(tmp_path / 'gamma-elsewhere')}\n",
        encoding="utf-8",
    )
    result = registry.import_from_yaml()

    assert [c.project_id for c in result.conflicts] == ["gamma"]
    assert registry.get("gamma").name == "Gamma"


def test_current_naming_a_project_the_file_does_not_list_is_malformed(tmp_path):
    """A pointer that cannot be migrated is a broken source, not a good import."""
    source = tmp_path / ".agora" / "projects.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        f"current: nosuch\nprojects:\n  alpha:\n    root: {_posix(tmp_path / 'alpha')}\n",
        encoding="utf-8",
    )

    with pytest.raises(ProjectImportError) as err:
        read_project_yaml(source, project_root=tmp_path)
    assert "'current' names 'nosuch'" in str(err.value)
    assert "does not list" in str(err.value)

    with pytest.raises(ProjectImportError):
        _registry(tmp_path)


def test_a_dangling_current_is_reported_and_moves_no_pointer(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.use("beta")

    source = tmp_path / ".agora" / "projects.yaml"
    source.write_text(
        source.read_text(encoding="utf-8").replace("current: alpha", "current: nosuch"),
        encoding="utf-8",
    )
    recovered = _registry(tmp_path)

    assert recovered.current_project_id() == "beta"
    assert "'current' names 'nosuch'" in recovered.import_status()["error"]
    assert sorted(recovered.list_projects()) == ["alpha", "beta"]


def test_repairing_a_stored_pointer_that_names_nothing_says_so(tmp_path, capsys):
    """The store cannot produce this pointer, so finding one is worth saying."""
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.store.set_setting("current_project_id", "vanished")

    repaired = _registry(tmp_path)
    message = capsys.readouterr().err

    assert repaired.store.exists(repaired.current_project_id())
    assert "vanished" in message
    assert repaired.current_project_id() in message

# --- Today's top tier (W9, D-1) -------------------------------------------------


def test_promoting_records_the_choice_and_keeps_its_order(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)

    assert registry.today_focus().project_ids == []
    registry.promote_to_focus("beta")
    focus = registry.promote_to_focus("alpha")

    # Promotion order, not registry order: the person chose b first, and the screen puts
    # the cards in the order they were chosen.
    assert focus.project_ids == ["beta", "alpha"]
    assert focus.limit == 3
    assert focus.is_full is False


def test_promoting_twice_is_idempotent(tmp_path):
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)

    registry.promote_to_focus("alpha")
    focus = registry.promote_to_focus("alpha")
    assert focus.project_ids == ["alpha"]


def test_a_full_tier_refuses_rather_than_evicting(tmp_path):
    # The design's wording is a rule, not a hint: "3/3 已满 —— 第 4 个要上来得先放下一个".
    # Evicting the oldest to make room would overwrite the very choice this setting
    # exists to record, and do it without telling anyone.
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.create_project("gamma")
    registry.create_project("delta")
    for pid in ("alpha", "beta", "gamma"):
        registry.promote_to_focus(pid)

    with pytest.raises(TodayFocusFullError) as excinfo:
        registry.promote_to_focus("delta")

    assert excinfo.value.limit == 3
    # And nothing moved: the refusal is total, not partial.
    assert registry.today_focus().project_ids == ["alpha", "beta", "gamma"]


def test_promoting_an_unknown_project_is_refused(tmp_path):
    # Otherwise the list would hold an id that today_focus immediately reports as
    # unresolved — a write that creates its own inconsistency.
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    with pytest.raises(KeyError):
        registry.promote_to_focus("no-such-project")
    assert registry.today_focus().project_ids == []


def test_an_id_that_no_longer_resolves_is_reported_not_silently_dropped(tmp_path):
    # How this state arises: not through the promote path, which refuses unknown ids, and
    # not through deletion, which this API does not offer. The setting holds ids with no
    # foreign key to projects, so an import that changes the project set — or any write
    # that does not go through promote — can leave one behind.
    #
    # Reporting it is what keeps "3/3 full" from becoming "2/3" with nothing to explain
    # why: the person would be shown a different number and given no way to learn that
    # the list had changed rather than the limit.
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.promote_to_focus("alpha")
    registry.store.edit_setting_lines(
        SETTING_TODAY_FOCUS, lambda current: [*current, "ghost"]
    )

    focus = registry.today_focus()
    assert focus.project_ids == ["alpha"]
    assert focus.unresolved_ids == ["ghost"]


def test_demoting_accepts_an_id_that_no_longer_resolves(tmp_path):
    # That is how a deleted project's leftover entry gets cleared. Refusing would leave
    # it unremovable.
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.store.edit_setting_lines(
        SETTING_TODAY_FOCUS, lambda current: [*current, "ghost"]
    )

    focus = registry.demote_from_focus("ghost")
    assert focus.project_ids == []
    assert focus.unresolved_ids == []


def test_the_choice_survives_a_new_registry_on_the_same_database(tmp_path):
    # It is control-plane state, so it must outlive the process that set it — and it must
    # not have been written into the projects being read (adr/0003 withholds that).
    _write_yaml(tmp_path)
    _registry(tmp_path).promote_to_focus("alpha")

    assert _registry(tmp_path).today_focus().project_ids == ["alpha"]

def test_an_unresolved_entry_still_holds_its_place(tmp_path):
    # Promotion is a human trade-off, so an entry nobody can currently resolve is still a
    # choice that was made, and it keeps its slot.
    #
    # Counting only the resolvable ones made the API contradict itself: two resolvable plus
    # one unresolved reported is_full=false and then refused the next promotion with 409, so
    # the screen would have offered a slot it did not have.
    _write_yaml(tmp_path)
    registry = _registry(tmp_path)
    registry.promote_to_focus("alpha")
    registry.promote_to_focus("beta")
    registry.store.edit_setting_lines(
        SETTING_TODAY_FOCUS, lambda current: [*current, "ghost"]
    )

    focus = registry.today_focus()
    assert focus.project_ids == ["alpha", "beta"]
    assert focus.unresolved_ids == ["ghost"]
    assert focus.is_full is True, "the unresolved entry holds the third slot"

    # And what is_full says is what promotion does -- that agreement is the point.
    registry.create_project("gamma")
    with pytest.raises(TodayFocusFullError):
        registry.promote_to_focus("gamma")

