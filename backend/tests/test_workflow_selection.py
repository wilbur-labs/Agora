"""A project's workflow selection — the `workflow:` block of its task.yaml (workspace CR-75
slice 4). claude-workspace's SessionStart hook reads the same block; the reading rules
pinned here are the ones its tests pin (scripts/tests/test_workflow_section.sh)."""
from __future__ import annotations

import os

import pytest
import yaml

from agora.projects.importer import StrictLoader
from agora.projects.workflow_selection import (
    DEFAULTS, FEATURES, KIRO_DEFAULTS, WorkflowRefused, edit_workflow, read_workflow, text_sha,
)
from agora.projects.workspace_tasks import _read_manifest
from agora.terminal.auth import load_or_create_token
from agora.work.projects import work_key

from tests.test_work import FakeSession, _service, _task, _tool, api, run, world  # noqa: F401  (fixtures)

BASE = "task:\n  id: t\n  goal: g   # the goal\n  status: active\nprimary_repo: E:/x\n"
ALL_ON = {k: True for k in FEATURES}


def read(text: str):
    return read_workflow(yaml.load(text, Loader=StrictLoader), text)


# --- reading ------------------------------------------------------------------------------
def test_no_block_is_the_default_and_aidlc_is_off_by_default():
    r = read(BASE)
    assert r.state == "default" and r.features == DEFAULTS and r.features["aidlc"] is False
    assert r.base == text_sha(BASE)


def test_a_block_sets_what_it_names_and_the_rest_keep_their_defaults():
    r = read(BASE + "workflow:   # chosen on the card\n  deviation: off\n  records: False\n  review: yes\n")
    assert r.state == "set"
    assert r.features == {**DEFAULTS, "deviation": False, "records": False}


def test_an_empty_block_is_set_with_every_default():
    r = read(BASE + "workflow:\n")
    assert r.state == "set" and r.features == DEFAULTS


@pytest.mark.parametrize("block", [
    "workflow:\n  review: of\n",          # typo
    "workflow:\n  reveiw: off\n",         # unknown switch
    'workflow:\n  review: "off"\n',       # a string, not a switch
    "workflow: {review: off}\n",          # not the block form the hook reads
    "workflow:\n  review:off\n",          # YAML reads this as a string
    "workflow:\n  review:\n    x: 1\n",
    "workflow: off\n",
])
def test_anything_else_is_unreadable_never_the_default(block):
    r = read(BASE + block)
    assert r.state == "unreadable" and r.features is None and r.reason


def test_the_manifest_carries_it(tmp_path):
    (tmp_path / "task.yaml").write_text(BASE + "workflow:\n  review: off\n", encoding="utf-8")
    payload = _read_manifest(tmp_path / "task.yaml").payload()["workflow"]
    assert payload["state"] == "set" and payload["features"]["review"] is False
    (tmp_path / "task.yaml").write_text("task: [\n", encoding="utf-8")
    assert _read_manifest(tmp_path / "task.yaml").payload()["workflow"] is None


# --- the edit -----------------------------------------------------------------------------
def edit(text: str, **off: bool) -> str:
    return edit_workflow(text, {**ALL_ON, "aidlc": False, **off}, loader=StrictLoader)


def test_a_missing_block_is_appended_and_nothing_else_changes():
    out = edit(BASE, review=False)
    assert out.startswith(BASE)
    assert out.endswith("workflow:\n  requirements: on\n  breakdown: on\n  aidlc: off\n"
                        "  deviation: on\n  review: off\n  records: on\n")
    assert read(out).features["review"] is False


def test_an_existing_block_is_replaced_in_place_keeping_what_follows():
    text = BASE + "workflow:\n  review: off\n  # note\n\nmilestones:\n  - id: M1\n    status: done\n"
    out = edit(text, records=False)
    assert out.count("workflow:") == 1
    assert "  # note\n\nmilestones:\n  - id: M1\n    status: done\n" in out
    assert read(out).features == {**DEFAULTS, "records": False}
    assert yaml.safe_load(out)["milestones"] == [{"id": "M1", "status": "done"}]


def test_crlf_files_stay_crlf():
    text = BASE.replace("\n", "\r\n")
    out = edit(text, deviation=False)
    assert "\n" not in out.replace("\r\n", "")
    assert read(out).features["deviation"] is False


def test_an_unreadable_block_is_never_rewritten():
    with pytest.raises(WorkflowRefused):
        edit(BASE + "workflow:\n  review: of\n")


@pytest.mark.parametrize("features", [{"review": False}, {**ALL_ON, "extra": True}, {**ALL_ON, "review": "off"}])
def test_the_request_must_name_all_six_switches_as_booleans(features):
    with pytest.raises(WorkflowRefused):
        edit_workflow(BASE, features, loader=StrictLoader)


# --- through the API ----------------------------------------------------------------------
def test_the_card_writes_the_selection_into_the_task_yaml(api, tmp_path):
    client, service, work = api
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    key = work_key(work, "alpha")
    path = work / "tasks" / "alpha" / "task.yaml"
    before = path.read_bytes().decode("utf-8")
    body = {"features": {**ALL_ON, "aidlc": False, "records": False}, "base": text_sha(before)}
    reply = client.post(f"/api/work/projects/{key}/workflow", json=body)
    assert reply.status_code == 200, reply.text
    after = path.read_bytes().decode("utf-8")
    assert reply.json()["base"] == text_sha(after)
    assert read(after).features["records"] is False

    # the page's copy is now stale: refused, not overwritten
    stale = client.post(f"/api/work/projects/{key}/workflow", json=body)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "changed"
    assert path.read_bytes().decode("utf-8") == after

    assert client.post("/api/work/projects/nope/workflow", json=body).status_code == 404
    assert client.post(f"/api/work/projects/{key}/workflow", json={"features": {}, "base": text_sha(after)}
                       ).json()["detail"]["code"] == "refused"


def test_the_workflow_route_needs_the_terminal_token(api):
    client, service, work = api
    key = work_key(work, "alpha")
    reply = client.post(f"/api/work/projects/{key}/workflow", json={"features": ALL_ON, "base": "x"})
    assert reply.status_code == 401


def test_the_read_base_is_the_hash_of_the_bytes_even_for_a_crlf_file(tmp_path):
    """The edit hashes the bytes; a reader that translated newlines would hand the page a
    base that never matches, and every save would come back "changed"."""
    raw = (BASE + "workflow:\n  review: off\n").replace("\n", "\r\n").encode("utf-8")
    (tmp_path / "task.yaml").write_bytes(raw)
    payload = _read_manifest(tmp_path / "task.yaml").payload()["workflow"]
    assert payload["base"] == text_sha(raw.decode("utf-8"))


# --- codex review round 1 -----------------------------------------------------------------
@pytest.mark.parametrize("block", [
    "workflow:\n  {review: off}\n",        # YAML takes it; the hook's line grammar does not
    'workflow:\n  "review": off\n',        # a quoted key
    "workflow:\n  review: !!bool off\n",   # a tagged value
])
def test_what_yaml_accepts_but_the_session_hook_cannot_read_is_unreadable(block):
    """The card must not show a setting the sessions ignore, and the edit must not rewrite
    a block the hook could not read."""
    assert read(BASE + block).state == "unreadable"
    with pytest.raises(WorkflowRefused):
        edit(BASE + block)


def test_a_cancelled_request_keeps_the_lock_until_the_workflow_write_has_ended(world, monkeypatch):
    import asyncio
    import threading

    from agora.work import service as service_module
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    path = work / "tasks" / "alpha" / "task.yaml"
    started, release = threading.Event(), threading.Event()
    real = service_module._write_workflow

    def slow(*args):
        started.set()
        release.wait(5)
        return real(*args)
    monkeypatch.setattr(service_module, "_write_workflow", slow)
    base = text_sha(path.read_bytes().decode("utf-8"))

    async def scenario():
        request = asyncio.create_task(service.set_workflow(key, {**ALL_ON, "aidlc": False, "review": False}, base))
        await asyncio.to_thread(started.wait, 5)
        request.cancel()
        await asyncio.sleep(0)
        assert service._lock.locked()  # the write is still running: the lock is held
        release.set()
        await asyncio.gather(*service._shelf_tasks)
        assert not service._lock.locked()

    run(scenario())
    assert read(path.read_bytes().decode("utf-8")).features["review"] is False


# --- kiro-workspace (CR-75 slice 4, kiro) ---------------------------------------------------
KIRO_BASE = "name: k1\nproject_path: /e/x\nbranch_prefix: feat/     # convention\n"


def _kiro(tmp_path, text: str, *, steering: bool = True):
    kiro = tmp_path / "kiro"
    (kiro / ".kiro" / "agents").mkdir(parents=True)
    if steering:
        (kiro / ".kiro" / "steering").mkdir()
        (kiro / ".kiro" / "steering" / "workflow-selection.md").write_text("# rules\n", encoding="utf-8")
    (kiro / ".kiro" / "agents" / "k1.json").write_text('{"name": "k1"}', encoding="utf-8")
    _task(kiro, "k1", None)
    # CRLF, as kiro-workspace's files are on Windows (git autocrlf).
    (kiro / "tasks" / "k1" / "task.yaml").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    return kiro


def test_a_kiro_task_reads_aidlc_as_on_when_nothing_says_otherwise(tmp_path):
    from agora.projects.workspace_tasks import read_workspace_source
    kiro = _kiro(tmp_path, KIRO_BASE)
    manifest = read_workspace_source(kiro).tasks[0].manifest
    # kiro's task.yaml is not claude-workspace's shape, and the workflow is read all the same
    assert manifest.reason == "unknown_schema"
    assert manifest.workflow.state == "default" and manifest.workflow.features == ALL_ON

    (kiro / "tasks" / "k1" / "task.yaml").write_text(KIRO_BASE + "workflow:\n  review: off\n", encoding="utf-8")
    wf = read_workspace_source(kiro).tasks[0].manifest.workflow
    assert wf.state == "set" and wf.features == {**ALL_ON, "review": False}

    (kiro / "tasks" / "k1" / "task.yaml").write_text(KIRO_BASE + "workflow:\n  aidlc: of\n", encoding="utf-8")
    assert read_workspace_source(kiro).tasks[0].manifest.workflow.state == "unreadable"


def test_a_claude_workspace_task_keeps_aidlc_off_by_default(world):
    from agora.projects.workspace_tasks import read_workspace_source
    tmp_path, work, claude = world
    tasks = {t.name: t for t in read_workspace_source(work).tasks}
    assert tasks["alpha"].manifest.workflow.features["aidlc"] is False


def test_a_kiro_unknown_shape_outside_a_kiro_workspace_carries_no_workflow(tmp_path):
    (tmp_path / "task.yaml").write_text(KIRO_BASE + "workflow:\n  review: off\n", encoding="utf-8")
    assert _read_manifest(tmp_path / "task.yaml").workflow is None


def test_the_card_writes_a_kiro_tasks_selection(tmp_path):
    kiro = _kiro(tmp_path, KIRO_BASE + "status: active\n")
    service = _service(tmp_path, kiro, tmp_path)
    path = kiro / "tasks" / "k1" / "task.yaml"
    before = path.read_bytes().decode("utf-8")
    features = {**ALL_ON, "aidlc": False, "deviation": False}
    base = run(service.set_workflow(work_key(kiro, "k1"), features, text_sha(before)))
    after = path.read_bytes().decode("utf-8")
    assert base == text_sha(after)
    assert after.startswith(before) and "workflow:\r\n  requirements: on\r\n  breakdown: on\r\n  aidlc: off\r\n" in after
    wf = read_workflow(yaml.load(after, Loader=StrictLoader), after, KIRO_DEFAULTS)
    assert wf.state == "set" and wf.features == features


def test_a_kiro_workspace_without_the_steering_file_is_offered_no_switch(tmp_path):
    # its agents would ignore every switch, so neither the read nor the write claims one
    from agora.projects.workspace_tasks import read_workspace_source
    kiro = _kiro(tmp_path, KIRO_BASE + "status: active\nworkflow:\n  review: off\n", steering=False)
    assert read_workspace_source(kiro).tasks[0].manifest.workflow is None
    service = _service(tmp_path, kiro, tmp_path)
    path = kiro / "tasks" / "k1" / "task.yaml"
    before = path.read_bytes()
    with pytest.raises(WorkflowRefused, match="workflow-selection.md"):
        run(service.set_workflow(work_key(kiro, "k1"), ALL_ON, text_sha(before.decode("utf-8"))))
    assert path.read_bytes() == before


def test_a_workspace_whose_kind_cannot_be_established_carries_no_workflow(tmp_path, monkeypatch):
    # `.claude/` could not be probed (an ACL, a share offline): not read as absent, so the
    # workspace is not taken for kiro and its defaults are not applied (kiro review).
    from types import SimpleNamespace

    from agora.projects import workspace_tasks
    kiro = _kiro(tmp_path, KIRO_BASE)
    real = workspace_tasks.probe_path

    def probe(path, **kw):
        if path.name == ".claude":
            return SimpleNamespace(is_dir=lambda: None, is_file=lambda: None)
        return real(path, **kw)

    monkeypatch.setattr(workspace_tasks, "probe_path", probe)
    assert workspace_tasks.workflow_workspace(kiro) is None


def test_only_the_block_changes_in_a_file_that_mixes_line_endings():
    text = "task:\r\n  id: t\n  goal: g\r\nworkflow:\r\n  review: off\r\nprimary_repo: E:/x\n# tail\n"
    out = edit_workflow(text, {**ALL_ON, "records": False}, loader=StrictLoader)
    assert out.startswith("task:\r\n  id: t\n  goal: g\r\nworkflow:\r\n  requirements: on\r\n")
    assert out.endswith("  records: off\r\nprimary_repo: E:/x\n# tail\n")
    appended = edit_workflow("task:\r\n  id: t\n  goal: g", ALL_ON, loader=StrictLoader)
    assert appended.startswith("task:\r\n  id: t\n  goal: g\r\n\r\nworkflow:\r\n") and appended.endswith("records: on")


def test_the_write_leaves_no_temp_file_behind(tmp_path):
    from agora.work import shelf
    path = tmp_path / "task.yaml"
    path.write_bytes(b"a: 1\n")
    os.chmod(path, 0o644)
    mode = os.stat(path).st_mode
    shelf._write(path, "a: 2\n")
    assert path.read_bytes() == b"a: 2\n"
    assert os.stat(path).st_mode == mode   # not mkstemp's 0600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["task.yaml"]


def test_a_failed_write_of_a_read_only_task_yaml_leaves_no_temp_file(tmp_path):
    import stat

    from agora.work import shelf
    path = tmp_path / "task.yaml"
    path.write_bytes(b"a: 1\n")
    os.chmod(path, stat.S_IREAD)
    try:
        try:
            shelf._write(path, "a: 2\n")
        except shelf.ShelfRefused:
            pass   # Windows refuses to replace a read-only file; POSIX replaces it
        assert sorted(p.name for p in tmp_path.iterdir()) == ["task.yaml"]
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_new_block_lines_use_only_breaks_the_session_hook_splits_on():
    out = edit_workflow("task:\n  id: t\n  goal: g ", ALL_ON, loader=StrictLoader)
    assert "\nworkflow:\n  requirements: on\n" in out and " workflow" not in out
