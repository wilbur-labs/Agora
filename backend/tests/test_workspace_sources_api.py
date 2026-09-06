"""Registering a workspace directory, and reading it back through the API (W7).

Three things are pinned here, and the first two are the reason this is not a plain
CRUD test:

* **probe before register.** Accepting a path and then rendering an empty list is the
  defect this product keeps correcting: the person cannot tell "this directory holds no
  tasks" from "I could not read it" from "wrong directory". `POST /workspace-sources/
  probe` answers that first, without storing anything.
* **forgetting a source touches no file.** `DELETE` removes a row from this machine's
  settings; the work stays exactly where it is. Measured against the tree.
* **the registration list survives concurrent edits.** It is stored as one
  newline-separated setting, so a read-then-write across two calls would let two
  registrations each start from the same list and the later one drop the other with no
  error anywhere. `edit_setting_lines` does the whole thing in one transaction.
"""
from __future__ import annotations

import hashlib
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.projects.store import SETTING_WORKSPACE_SOURCES
from agora.tasks.router import get_project_registry

TASK_YAML = "task:\n  id: alpha\n  title: Alpha\n  goal: 把工作模型产品化\n"
RESUME_MD = (
    "# RESUME — alpha\n\n"
    "## 当前状态\n\n停在 W2。\n\n"
    "## 下一步\n\n先打通。\n\n"
    "## 变更记录 (CR Log)\n\n"
    "| ID | 日期 | 来源 | 描述 | 类型 | 状态 | 决定 | 已传播到 |\n"
    "|---|---|---|---|---|---|---|---|\n"
    "| CR-1 | 2026-09-06 | user | 描述 | defer | OPEN | 决定 | 触发物 |\n"
)


def _registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry({
        "projects": {
            "registry_path": str(tmp_path / "control" / "projects.yaml"),
            "default": "demo",
            "projects": {
                "demo": {
                    "name": "Demo",
                    "root": str(tmp_path / "control" / "demo"),
                    "workspaces": {
                        agent: str(tmp_path / "control" / "demo" / ".agora" / "ws" / agent)
                        for agent in ("claude", "codex", "kiro")
                    },
                }
            },
        }
    })


def _workspace(tmp_path: Path, name: str = "ws") -> Path:
    """A workspace directory holding one readable task."""
    root = tmp_path / name
    task = root / "tasks" / "alpha"
    task.mkdir(parents=True)
    (task / "task.yaml").write_text(TASK_YAML, encoding="utf-8")
    (task / "RESUME.md").write_text(RESUME_MD, encoding="utf-8")
    return root


def _fingerprint(root: Path) -> set[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        seen.add((relative, "<dir>" if path.is_dir()
                  else hashlib.sha256(path.read_bytes()).hexdigest()))
    return seen


@pytest.fixture
def client(tmp_path):
    registry = _registry(tmp_path)
    app.dependency_overrides[get_project_registry] = lambda: registry
    with TestClient(app) as test_client:
        test_client.registry = registry  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


# --- the listing --------------------------------------------------------------------


def test_nothing_registered_lists_no_sources(client):
    response = client.get("/api/workspace-sources")

    assert response.status_code == 200, response.text
    assert response.json() == {"sources": []}


def test_a_registered_workspace_is_read_on_every_request(client, tmp_path):
    """Read now, not cached: these are the user's live working notes, and the point of
    the panel is that it shows what they say today."""
    root = _workspace(tmp_path)
    assert client.post("/api/workspace-sources", json={"path": str(root)}).status_code == 201

    first = client.get("/api/workspace-sources").json()["sources"]
    assert [t["name"] for t in first[0]["tasks"]] == ["alpha"]
    assert first[0]["tasks"][0]["manifest"]["goal"] == "把工作模型产品化"

    (root / "tasks" / "beta").mkdir()
    second = client.get("/api/workspace-sources").json()["sources"]

    assert [t["name"] for t in second[0]["tasks"]] == ["alpha", "beta"]


def test_the_open_cr_rows_come_through_the_payload(client, tmp_path):
    """The one number the panel exists to make impossible to miss."""
    root = _workspace(tmp_path)
    client.post("/api/workspace-sources", json={"path": str(root)})

    task = client.get("/api/workspace-sources").json()["sources"][0]["tasks"][0]

    assert task["cr_log"]["available"] is True
    assert task["cr_log"]["open_count"] == 1


def test_a_registered_directory_that_is_gone_is_reported_absent_not_dropped(client, tmp_path):
    """A registration that no longer resolves stays listed and says why. Removing it
    silently would turn "you moved it" into "you never had one"."""
    root = _workspace(tmp_path, "moved")
    client.post("/api/workspace-sources", json={"path": str(root)})
    for path in sorted(root.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    root.rmdir()

    sources = client.get("/api/workspace-sources").json()["sources"]

    assert len(sources) == 1
    assert (sources[0]["available"], sources[0]["reason"]) == (False, "absent")
    assert sources[0]["tasks"] is None


# --- probe, which is why this is not two endpoints ------------------------------------


def test_probe_reports_what_a_path_would_yield_without_registering_it(client, tmp_path):
    root = _workspace(tmp_path)

    probed = client.post("/api/workspace-sources/probe", json={"path": str(root)})

    assert probed.status_code == 200, probed.text
    assert [t["name"] for t in probed.json()["tasks"]] == ["alpha"]
    assert client.get("/api/workspace-sources").json() == {"sources": []}


def test_probing_an_unreadable_path_is_an_answer_to_show_not_an_error(client, tmp_path):
    """A 422 would make the UI interpret a failure it cannot classify. The reason is
    the whole product of this endpoint."""
    (tmp_path / "not-a-workspace").mkdir()

    probed = client.post("/api/workspace-sources/probe",
                         json={"path": str(tmp_path / "not-a-workspace")})

    assert probed.status_code == 200, probed.text
    assert (probed.json()["available"], probed.json()["reason"]) == (False, "no_tasks_dir")


@pytest.mark.parametrize("endpoint", ["/api/workspace-sources", "/api/workspace-sources/probe"])
def test_a_relative_path_is_refused_by_both_write_paths(client, endpoint):
    """Relative to what? The API process's working directory is not a place the person
    can see, so a relative path would register a directory nobody named."""
    response = client.post(endpoint, json={"path": "tasks"})

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("endpoint", ["/api/workspace-sources", "/api/workspace-sources/probe"])
def test_an_empty_path_is_refused(client, endpoint):
    assert client.post(endpoint, json={"path": ""}).status_code == 422


# --- registering and forgetting -------------------------------------------------------


def test_registering_twice_keeps_one_entry(client, tmp_path):
    root = _workspace(tmp_path)

    first = client.post("/api/workspace-sources", json={"path": str(root)})
    second = client.post("/api/workspace-sources", json={"path": str(root)})

    assert (first.status_code, second.status_code) == (201, 201)
    assert len(client.get("/api/workspace-sources").json()["sources"]) == 1


def test_the_same_directory_spelled_differently_is_the_same_entry(client, tmp_path):
    root = _workspace(tmp_path)
    detour = root.parent / "ws" / ".." / "ws"

    client.post("/api/workspace-sources", json={"path": str(root)})
    client.post("/api/workspace-sources", json={"path": str(detour)})

    assert len(client.get("/api/workspace-sources").json()["sources"]) == 1


@pytest.mark.skipif(sys.platform != "win32",
                    reason="path case-insensitivity is a Windows property")
def test_one_windows_directory_spelled_in_two_cases_is_one_source(client, tmp_path):
    """`resolve()` normalises the case of a path that exists — and this endpoint
    deliberately accepts one that does not, so an offline drive can stay registered.
    There the typed spelling survives, and comparing the stored lines as strings made
    `D:/Work/Offline` and `D:/Work/offline` two sources that cannot forget each other.
    """
    offline = tmp_path / "Offline-Share"

    client.post("/api/workspace-sources", json={"path": str(offline)})
    client.post("/api/workspace-sources", json={"path": str(offline).lower()})

    assert len(client.get("/api/workspace-sources").json()["sources"]) == 1


@pytest.mark.skipif(sys.platform != "win32",
                    reason="path case-insensitivity is a Windows property")
def test_a_windows_source_can_be_forgotten_under_a_different_case(client, tmp_path):
    """Forgetting has to recognise every spelling registering accepts, or a source can
    be registered and then impossible to remove."""
    offline = tmp_path / "Offline-Share"
    client.post("/api/workspace-sources", json={"path": str(offline)})

    removed = client.delete("/api/workspace-sources", params={"path": str(offline).lower()})

    assert removed.json() == {"was_registered": True}
    assert client.get("/api/workspace-sources").json() == {"sources": []}


def test_sources_keep_the_order_they_were_added_in(client, tmp_path):
    roots = [_workspace(tmp_path, name) for name in ("second", "first", "third")]
    for root in roots:
        client.post("/api/workspace-sources", json={"path": str(root)})

    listed = [Path(s["root"]).name for s in client.get("/api/workspace-sources").json()["sources"]]

    assert listed == ["second", "first", "third"]


def test_a_directory_that_cannot_be_read_can_still_be_registered(client, tmp_path):
    """Deliberate: the UI has already shown the person what the probe found, and a
    source on a disconnected drive must survive a reboot rather than refuse to be
    stored."""
    response = client.post("/api/workspace-sources",
                           json={"path": str(tmp_path / "offline-share")})

    assert response.status_code == 201, response.text
    assert response.json()["reason"] == "absent"
    assert len(client.get("/api/workspace-sources").json()["sources"]) == 1


def test_forgetting_a_source_removes_the_row_and_touches_no_file(client, tmp_path):
    root = _workspace(tmp_path)
    client.post("/api/workspace-sources", json={"path": str(root)})
    before = _fingerprint(root)

    removed = client.delete("/api/workspace-sources", params={"path": str(root)})

    assert removed.status_code == 200, removed.text
    assert removed.json() == {"was_registered": True}
    assert client.get("/api/workspace-sources").json() == {"sources": []}
    assert _fingerprint(root) == before


def test_forgetting_something_that_was_never_registered_says_so(client, tmp_path):
    response = client.delete("/api/workspace-sources", params={"path": str(tmp_path / "nope")})

    assert response.json() == {"was_registered": False}


def test_forgetting_one_source_leaves_the_others(client, tmp_path):
    keep, drop = _workspace(tmp_path, "keep"), _workspace(tmp_path, "drop")
    for root in (keep, drop):
        client.post("/api/workspace-sources", json={"path": str(root)})

    client.delete("/api/workspace-sources", params={"path": str(drop)})

    listed = [Path(s["root"]).name for s in client.get("/api/workspace-sources").json()["sources"]]
    assert listed == ["keep"]


# --- the storage underneath ------------------------------------------------------------


def test_the_registration_list_is_machine_local_settings_state(tmp_path):
    registry = _registry(tmp_path)
    root = _workspace(tmp_path)

    registry.register_workspace_source(root)

    stored = registry.store.get_setting(SETTING_WORKSPACE_SOURCES)
    assert stored.splitlines() == [str(root.resolve())]


def test_blank_lines_in_the_stored_setting_are_not_sources(tmp_path):
    registry = _registry(tmp_path)
    registry.store.set_setting(SETTING_WORKSPACE_SOURCES, f"\n{tmp_path / 'a'}\n   \n")

    assert registry.workspace_sources() == [tmp_path / "a"]


def test_no_setting_at_all_means_no_sources(tmp_path):
    assert _registry(tmp_path).workspace_sources() == []


def test_a_relative_path_is_refused_below_the_api_too(tmp_path):
    """The API layer rejects it, and so does the registry: a guard that only exists in
    the router is one call site away from not existing."""
    with pytest.raises(ValueError):
        _registry(tmp_path).register_workspace_source("tasks")


def test_editing_the_list_runs_the_read_and_the_write_as_one_serialised_unit(tmp_path,
                                                                              monkeypatch):
    """The lost-update race: two registrations that both start from `[A]` write `[A,B]`
    and `[A,C]`, and whichever commits second drops the other with no error anywhere.

    What this pins is the primitive and the ordering: the edit runs *inside* an
    immediate transaction — the helper whose own docstring reserves it for "read the
    row, then write it if it still looks like X" — and the deferred one is not used,
    because a deferred SELECT takes no write lock and one transaction is then not one
    serialised unit. What it does not pin is SQLite's enforcement of that lock; that is
    the database's job, and faking two connections here would test the fake.
    """
    from agora.projects.store import ProjectStore

    registry = _registry(tmp_path)
    registry.register_workspace_source(tmp_path / "first")

    events: list[str] = []

    def recording(name, real):
        @contextmanager
        def wrapper(self):
            events.append(f"{name}:enter")
            with real(self) as db:
                yield db
            events.append(f"{name}:exit")
        return wrapper

    monkeypatch.setattr(ProjectStore, "_immediate_transaction",
                        recording("immediate", ProjectStore._immediate_transaction))
    monkeypatch.setattr(ProjectStore, "_transaction",
                        recording("deferred", ProjectStore._transaction))

    seen: list[list[str]] = []

    def append_second(current: list[str]) -> list[str]:
        events.append("edit")
        seen.append(list(current))
        return [*current, str(tmp_path / "second")]

    updated = registry.store.edit_setting_lines(SETTING_WORKSPACE_SOURCES, append_second)

    assert events == ["immediate:enter", "edit", "immediate:exit"]
    assert seen == [[str((tmp_path / "first").resolve())]]
    assert updated == [str((tmp_path / "first").resolve()), str(tmp_path / "second")]
    assert [p.as_posix() for p in registry.workspace_sources()] == [
        (tmp_path / "first").resolve().as_posix(), (tmp_path / "second").as_posix(),
    ]


def test_editing_a_setting_that_does_not_exist_yet_starts_from_an_empty_list(tmp_path):
    registry = _registry(tmp_path)
    seen: list[list[str]] = []

    def record(current: list[str]) -> list[str]:
        seen.append(current)
        return ["x"]

    registry.store.edit_setting_lines("a_key_nobody_wrote", record)

    assert seen == [[]]
    assert registry.store.get_setting("a_key_nobody_wrote") == "x"
