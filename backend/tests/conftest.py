"""Test isolation for the one SQLite file the whole control plane lives in.

**The defect this closes.** Every store resolves its database through
``resolve_control_plane_db_path``, and the shipped ``config.yaml`` names a *relative*
``memory.data_dir: ./data``. So the file a process uses depends on the directory it
was started from — and ``make test`` (``cd backend && pytest``) and ``make dev``
(``cd backend && uvicorn``) both start from ``backend/``, which means **the test
suite and the development server resolve to the same file**,
``backend/data/agora.db``.

That is not a hypothetical. A single test was observed opening it, because
``agora.api.app.lifespan`` calls ``initialize_control_plane_store()`` and
``get_execution_dispatcher()`` *directly* rather than through ``Depends`` — so
``app.dependency_overrides``, however carefully a test sets it, cannot reach them.
Every ``with TestClient(app)`` in the suite runs that lifespan.

**Why it mattered more than a dirty file.** ``start_recovery`` reconciles owed
reviews and then **resumes the persisted queue**, dispatching queued runs. Against a
real database that means launching real agent processes from a test run. It had not
happened yet only because that database's runs were all terminal — the hazard was
latent, not absent, and one queued run would have been enough.

**Two kinds of real state, not one.** The database was the obvious one; the second
was found by review after the first fix was already called complete. Constructing a
``ProjectRegistry`` — which the startup path does — creates ``.agora/research``,
``.agora/decisions``, ``.agora/specs`` and every registered workspace *under each
project's root*. One ``with TestClient(app)`` was measured issuing twelve such
directory creations into this developer's own repository and into a second real
project beside it, no-ops only for as long as those directories happen to exist.
Delete one and the test suite puts it back.

So both are redirected: ``control_plane.db_path`` (the first rule
``resolve_control_plane_db_path`` consults) for the database, and the project roots,
workspaces and ``projects_base`` for the directories. Ids, agent names, adapters and
limits are left exactly as the suite has always seen them, because tests assert
against the real ones and none of them can reach a disk.

The import source is **copied and rewritten**, not removed. Pointing
``registry_path`` at a nonexistent file was the first attempt and it was wrong twice:
the registry took its "no import source" branch, so nothing exercised the real YAML
import any more, and the real file carries entries the inline configuration block
does not — a second project, and ``projects_base`` — so the fallback landed on
``project_root / "projects"``, which is derived from the module's own location and is
therefore the *real* projects directory however much else has been redirected.

The working-directory dependence itself is *not* fixed here. It is a product defect
as much as a test one — starting the server from another directory silently presents
a different project list — and it is deferred to the environment work, where "where
does this project live" is the subject rather than an accident. Fixing it means
moving the resolved path, which on a developer's machine switches the server to a
different existing database; that needs a startup check that refuses rather than
switches, and it does not belong in a conftest.

**The guard is the part that stays true**, and its own history is the argument for
its shape. Redirecting paths fixes today's suite; the guard fails the *accident* —
the next direct call, new startup path or forgotten factory — instead of letting it
reach a real file in silence. Its first version watched ``sqlite3.connect`` alone,
which is exactly why it could not see those twelve directories, and why it read as
proof that nothing was left. **A guard narrower than the claim it supports is worse
than no guard**, so what it covers is stated exactly rather than as "any test":

* Intercepted: calls routed through the patched module attributes ``sqlite3.connect``
  and ``Path.mkdir`` / ``os.mkdir`` / ``os.makedirs``. That reaches every store in
  ``agora/`` — the ones that call ``sqlite3.connect(...)`` themselves after ``import
  sqlite3``, and the ones that go through another store's ``_connect`` and so arrive
  at the same attribute. Paths are resolved through links first, because a junction
  inside the temp tree pointing at the repository defeated the lexical check, and on
  Windows a junction needs no elevation.
* Not intercepted: file *writes* and anything a subprocess does. Both **exist in**
  ``agora/`` — this is a boundary, not an absence. Writes were measured rather than
  assumed: the startup path was instrumented and writes nothing outside the temp
  tree, and no test asserts anything about what a subprocess touches.
* Intercepted but resolved wrongly — the wrapper runs and then decides about the
  wrong path, so the call is *allowed*, which is the same outcome as not seeing it and
  a different cause: ``sqlite3.connect(..., uri=True)``, whose ``file:`` URI is read as
  a filename; ``os.mkdir(..., dir_fd=fd)``, which resolves against a descriptor the
  wrapper never looks at; and an already-cached ancestor directory swapped for a
  junction after the fact. No caller in ``agora/`` reaches any of them today.

Those three are not prose to be trusted: they are ``tests/test_guard_boundaries.py``,
as tests that state the behaviour we want and are skipped with the reason, so
``pytest -rs`` reads them out every run. Deleting a skip is how the next person picks
one up (CR-8).

One more way past is worth naming and is *not* in that file, because it is not a debt
anyone can pick up: a ``from sqlite3 import connect`` bound before this fixture runs
holds the real function, and patching the module attribute afterwards cannot reach a
reference that already exists. That is a limit of the technique rather than a gap in
this guard, so it is stated here and nowhere pretended to be reclaimable. Nothing in
``agora/`` binds SQLite that way today; every store calls through the attribute.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
import yaml

from agora.config import settings as settings_module

#: Every ``lru_cache``'d store factory. They memoise a path resolved from the
#: configuration, so a test that changes the configuration has to clear all of them
#: or it gets the previous test's database. Listed as strings and resolved lazily so
#: importing this file does not drag the whole application in.
_CACHED_FACTORIES = (
    ("agora.tasks.router", "get_task_store"),
    ("agora.attention.router", "get_attention_store"),
    ("agora.control_plane.router", "initialize_control_plane_store"),
    ("agora.execution.router", "get_execution_store"),
    ("agora.execution.router", "get_execution_dispatcher"),
    ("agora.workflows.router", "get_workflow_store"),
    ("agora.workflows.router", "get_workflow_orchestrator"),
    ("agora.workflows.router", "get_workflow_supervisor"),
    ("agora.workspaces.router", "get_workspace_provisioner"),
)


#: Memo for the resolved form of directories that exist. Only *existing* paths are
#: cached, so a path created later cannot be served a stale answer.
_PHYSICAL_CACHE: dict[str, Path] = {}


def _normalised(path) -> Path:
    """An absolute, case-normalised path with links resolved — where it matters.

    A lexical ``abspath`` is not enough. A junction or symlink inside the temp tree
    that points at the repository makes ``…/tmp/repo-link/data/agora.db`` look like a
    temporary path while the operating system opens the real development database;
    on Windows a junction needs no elevation, so that is not a theoretical bypass.
    The same resolution is what makes this correct on systems whose temp directory is
    *itself* a link (macOS ``/var`` → ``/private/var``).

    Resolution is limited to the deepest **existing** ancestor and memoised, because
    this runs on every connection and every directory creation the suite makes and
    ``resolve`` stats each component. The tail below that ancestor cannot contain a
    link, because it does not exist yet.
    """
    lexical = Path(os.path.abspath(str(path)))
    tail: list[str] = []
    probe = lexical
    while True:
        key = str(probe)
        cached = _PHYSICAL_CACHE.get(key)
        if cached is not None:
            base = cached
            break
        try:
            if probe.exists():
                base = probe.resolve()
                _PHYSICAL_CACHE[key] = base
                break
        except OSError:
            pass
        if probe.parent == probe:
            base = probe
            break
        tail.append(probe.name)
        probe = probe.parent
    for name in reversed(tail):
        base = base / name
    return Path(os.path.normcase(str(base)))


def _within_any(path: Path, roots: tuple[Path, ...]) -> bool:
    """Whether ``path`` is inside one of ``roots``, by path component.

    Not ``startswith``. A string prefix test says yes to a *sibling* whose name
    merely begins with an allowed directory's — with the temp directory at
    ``…/Local/Temp``, ``…/Local/Temp-persistent/agora.db`` passed it, so a database
    outside every allowed root could be read and written without the guard noticing.
    """
    for root in roots:
        try:
            if path.is_relative_to(root):
                return True
        except (OSError, ValueError):
            continue
    return False


def _clear_factory_caches() -> None:
    import importlib

    for module_name, attribute in _CACHED_FACTORIES:
        factory = getattr(importlib.import_module(module_name), attribute, None)
        cache_clear = getattr(factory, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()


def _is_path(value: object) -> bool:
    """Whether a configuration value is already a usable path, and so safe to move."""
    return isinstance(value, str) and value.strip() != ""


def _rewritten_projects(projects: dict, scratch: Path) -> dict:
    """The same project ids, with every path moved under ``scratch``.

    Ids and agent names are left alone: the suite asserts against the real ones, and
    they are not what can reach a developer's disk.
    """
    rewritten = {}
    for project_id, project in projects.items():
        if not isinstance(project, dict):
            rewritten[project_id] = project
            continue
        moved = dict(project)
        root = scratch / "projects" / str(project_id)
        # Only values that are *already usable paths* are rewritten. Supplying a
        # ``root`` the real file does not have, or replacing a malformed workspace
        # value with a working temporary path, repairs a broken configuration on the
        # way in — the importer rejects both shapes, and a test asserting that it
        # rejects them would start passing for the wrong reason. Invalid stays
        # invalid and missing stays missing: this fixture moves paths, it does not
        # fix files.
        #
        # ``_is_path`` and not ``isinstance(..., str)``: an empty string is a str, and
        # checking only the type let ``root: ""`` through to be replaced by a real
        # temporary path. The first version of this guard had exactly that hole.
        if _is_path(project.get("root")):
            moved["root"] = root.as_posix()
        workspaces = project.get("workspaces")
        if isinstance(workspaces, dict):
            moved["workspaces"] = {
                key: (
                    (root / ".agora" / "workspaces" / str(key)).as_posix()
                    if _is_path(value)
                    else value
                )
                for key, value in workspaces.items()
            }
        rewritten[project_id] = moved
    return rewritten


def _rewritten_project_source(source: dict | None, scratch: Path) -> dict:
    """The registry's YAML import source, path-rewritten.

    ``projects_base`` matters as much as the roots: without it the registry falls
    back to ``project_root / "projects"``, and ``project_root`` comes from the
    module's own file rather than from the configuration — so it is the real
    directory no matter what else has been redirected.
    """
    document = dict(source or {})
    document["projects"] = _rewritten_projects(document.get("projects") or {}, scratch)
    # Absent, or already a usable path → move it. Present but malformed → leave it, so
    # the importer still rejects the file. Overwriting unconditionally was the first
    # version, and it turned "your projects_base is not a path" into a silent pass.
    if "projects_base" not in document or _is_path(document.get("projects_base")):
        document["projects_base"] = (scratch / "projects").as_posix()
    return document


@pytest.fixture(scope="session")
def real_project_source() -> dict | None:
    """The repository's own ``.agora/projects.yaml``, parsed once, or None if absent.

    Absent is a real state — a fresh clone has no import source — and it must stay
    distinguishable from "could not be read", so only a definite absence yields None.
    """
    path = Path(__file__).resolve().parents[2] / ".agora" / "projects.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    # Read with the *importer's own* loader, not ``yaml.safe_load``. The production
    # reader installs a strict loader precisely because ``safe_load`` applies
    # last-one-wins to duplicate keys, and its comment says why: for an import source
    # that is data loss reported as success. Loading the real file with ``safe_load``
    # and dumping the result back out therefore *erased* a duplicate key — the
    # importer rejects the original and accepted the copy, so a malformed
    # ``projects.yaml`` on someone's disk would have been silently normalised by the
    # test fixture. This module had already been bitten by the same PyYAML behaviour
    # an hour earlier, on the config file, and reintroduced it here.
    from agora.projects.importer import _StrictLoader

    return yaml.load(text, Loader=_StrictLoader) or {}


@pytest.fixture(scope="session")
def real_config_text() -> str:
    """The repository's own ``config.yaml``, read once.

    Copied rather than replaced: the suite asserts against the real project ids and
    adapter names, so the only thing a test may change about the configuration is
    where the database goes.
    """
    for candidate in (
        Path(__file__).resolve().parents[2] / "config.yaml",   # repository root
        Path(__file__).resolve().parents[1] / "config.yaml",   # backend/
    ):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.fail("config.yaml not found; the isolation fixture cannot copy it")


@pytest.fixture(autouse=True)
def isolate_control_plane_database(
    request, tmp_path_factory, monkeypatch, real_config_text, real_project_source
):
    """Point every store at a database in a temp directory of this fixture's own.

    ``control_plane.db_path`` is the first rule ``resolve_control_plane_db_path``
    consults, so one added key is enough and nothing else has to be understood or
    kept in step with the real file.

    Deliberately **not** in the test's own ``tmp_path``. An autouse fixture that
    writes into it is inventing files the test did not create, and at least one test
    asserts its ``tmp_path`` is empty afterwards because the thing it exercises is
    supposed to clean up after itself — an isolation fixture must not be able to fail
    a test about tidiness.

    A test marked ``real_config_discovery`` is left alone. Redirecting through
    ``AGORA_CONFIG_PATH`` means a bare ``get_config()`` no longer performs the
    repository-root → backend → cwd search at all, so a test *about that search*
    would keep passing with the search broken: it was reading this fixture's copy and
    asserting the copy's contents. The marker is the opt-out, and it is deliberately
    not automatic — a test that wants the real configuration should have to say so.
    """
    if request.node.get_closest_marker("real_config_discovery"):
        yield None
        return
    scratch = tmp_path_factory.mktemp("agora-control-plane")
    config_path = scratch / "agora-test-config.yaml"
    db_path = scratch / "control-plane.db"
    # Parsed and re-dumped rather than appended to. The real file already has a
    # ``control_plane:`` section, carrying the auth credentials the control-plane
    # tests read, and a second top-level key of the same name does not merge — PyYAML
    # takes the last one and drops the first, without a word.
    config = yaml.safe_load(real_config_text) or {}
    control_plane = config.setdefault("control_plane", {})
    control_plane["db_path"] = db_path.as_posix()

    # The database was not the only thing pointing at real state. Constructing a
    # ``ProjectRegistry`` — which the startup path does, via the execution dispatcher
    # — imports the project list and then *creates directories under each project's
    # root*: ``.agora/research``, ``.agora/decisions``, ``.agora/specs`` and every
    # registered workspace. One `with TestClient(app)` was measured issuing twelve
    # such mkdirs into this developer's own repository and into a second real project
    # beside it. They are no-ops only while those directories happen to exist; delete
    # one and the test suite puts it back. So the project paths are redirected too.
    #
    # The import source is **copied and rewritten**, not made to disappear. Pointing
    # ``registry_path`` at a nonexistent file was the first attempt and it was wrong
    # twice over: the registry then took the "no import source" branch, so no test
    # exercised the real YAML import any more; and the real file carries entries the
    # inline block does not — a second project, and ``projects_base`` — so seeding
    # fell back to ``project_root / "projects"``, which is derived from the module's
    # own location and is therefore **the real projects directory**, whatever the
    # configuration says. `POST /api/projects` then resolves a real path.
    projects = config.setdefault("projects", {})
    source_path = scratch / "projects.yaml"
    if real_project_source is None:
        # No import source here, and that is a real state that must survive the copy:
        # ``.agora/projects.yaml`` is gitignored, so a clean clone and CI have no such
        # file. Writing a placeholder turned that into a *present but empty* source,
        # which the importer rejects outright (``'projects' mapping is empty``) — so
        # every test that runs the startup lifespan would have failed on a fresh
        # checkout while passing on this machine. This repository already carries
        # three "cannot run it after cloning" issues; that would have been a fourth,
        # introduced by the isolation fixture itself.
        projects["registry_path"] = source_path.as_posix()  # deliberately absent
    else:
        source_path.write_text(
            yaml.safe_dump(
                _rewritten_project_source(real_project_source, scratch),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        projects["registry_path"] = source_path.as_posix()
    projects["projects"] = _rewritten_projects(projects.get("projects") or {}, scratch)
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("AGORA_CONFIG_PATH", str(config_path))
    settings_module.reset_config()
    _clear_factory_caches()
    try:
        yield db_path
    finally:
        # Both, and in this order: a cached store outlives the environment variable,
        # so leaving one behind hands the next test a handle on this test's temp
        # directory — which pytest is about to delete.
        _clear_factory_caches()
        settings_module.reset_config()


@pytest.fixture(autouse=True)
def refuse_databases_outside_the_temp_tree(tmp_path_factory, monkeypatch, request):
    """Fail a test that opens a SQLite file outside the allowed temp roots.

    "Allowed temp roots", not "the pytest temp tree": pytest's own base *and* the
    platform temp directory are both allowed, so ``tempfile.gettempdir()/x.db``
    passes. That is deliberate — tests that make their own directory with
    ``tempfile.mkdtemp`` are doing the right thing — but it is a wider allowance than
    "this test's ``tmp_path``", and the message a failing test reads says so.

    Scope, exactly: calls routed through the ``sqlite3.connect`` attribute, which is
    how every store in ``agora/`` reaches SQLite — directly, or through another
    store's ``_connect``. Not ``uri=True`` URIs, not a connection bound before this
    fixture runs, not a subprocess — see the module docstring's lists and
    ``tests/test_guard_boundaries.py``, which carries the reclaimable ones as skipped
    tests rather than as this sentence.

    This is the part that keeps holding after today's fix. The redirection above
    depends on every store resolving its path through the configuration; the moment
    something resolves one another way — a new startup path, a hard-coded default, a
    factory this file's list has fallen behind — the guard names the test and the
    path instead of letting it reach the developer's own database in silence.

    ``:memory:`` is allowed, and so is any temporary directory: pytest's own base,
    which covers every test's ``tmp_path``, and the platform temp directory, which
    covers tests that make their own with ``tempfile.mkdtemp``. Allowing the whole
    temp directory does not weaken this. What it exists to catch lives under the
    repository — ``backend/data/agora.db`` and ``data/agora.db`` — and nothing there
    is ever a temporary path.
    """
    allowed = tuple(
        _normalised(path) for path in (tmp_path_factory.getbasetemp(), tempfile.gettempdir())
    )
    original = sqlite3.connect

    def guarded(database, *args, **kwargs):
        target = str(database)
        if target != ":memory:" and not target.startswith("file::memory:"):
            resolved = _normalised(target)
            if not _within_any(resolved, allowed):
                raise AssertionError(
                    "test opened a database outside the allowed temp roots: "
                    f"{resolved}\n"
                    f"  test: {request.node.nodeid}\n"
                    "  This is the hazard tests/conftest.py exists to prevent: the "
                    "suite and `make dev` resolve to the same file, and startup "
                    "recovery dispatches queued runs. Resolve the path through the "
                    "configuration (see isolate_control_plane_database), or build "
                    "the store under tmp_path."
                )
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded)

    # Directories, not only databases. The first version of this guard watched
    # ``sqlite3.connect`` alone — and so it could not see that the startup path was
    # creating twelve directories inside the developer's real projects, which is how
    # that hole survived a review round. A guard narrower than the claim it is making
    # is worse than no guard: it reads as proof.
    real_mkdir = Path.mkdir

    def guarded_mkdir(self, *args, **kwargs):
        resolved = _normalised(self)
        if not _within_any(resolved, allowed):
            raise AssertionError(
                f"test created a directory outside the allowed temp roots: {resolved}\n"
                f"  test: {request.node.nodeid}\n"
                "  Something resolved a real path — a project root, a workspace, a "
                "data directory. Redirect it through the configuration (see "
                "isolate_control_plane_database) or build it under tmp_path."
            )
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)

    # ``os.makedirs`` and ``os.mkdir`` are not used anywhere in ``agora/`` today —
    # all fourteen directory-creation sites go through ``Path.mkdir``. They are
    # guarded anyway, because the alternative is a guard that silently stops
    # applying the first time somebody writes ``os.makedirs``, which is precisely the
    # shape of the hole this guard was just widened to close.
    for module, name in ((os, "makedirs"), (os, "mkdir")):
        real = getattr(module, name)

        def guarded_os_mkdir(path, *args, _real=real, **kwargs):
            resolved = _normalised(path)
            if not _within_any(resolved, allowed):
                raise AssertionError(
                    f"test created a directory outside the allowed temp roots: {resolved}\n"
                    f"  test: {request.node.nodeid}"
                )
            return _real(path, *args, **kwargs)

        monkeypatch.setattr(module, name, guarded_os_mkdir)
    yield
