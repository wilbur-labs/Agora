"""Project kickoff (W10): the agent's two passes are parsed fail-closed, the record
lives in the repository and reads as one of four things, every mutation is pinned to the
revision the caller saw, and only `adopt` touches the control plane."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.projects import kickoff
from agora.projects.kickoff import (
    KICKOFF_RELATIVE_PATH,
    KickoffError,
    gather_context,
    parse_generate,
    parse_refine,
    read_kickoff,
    write_kickoff,
)
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskStore


# --- fixtures ---------------------------------------------------------------------------------

QUESTIONS = [
    {"id": "audience", "question": "给谁用？", "why": "决定语料难度", "suggestions": ["自己", "初学者"]},
    {"id": "platform", "question": "先做哪个端？", "why": "决定技术栈", "suggestions": ["Web", "iOS"]},
    {"id": "scope", "question": "第一版只做什么？", "why": "决定首批任务", "suggestions": ["对话", "复习"]},
]

GENERATE_OK = json.dumps({
    "charter_draft": {"goal": "做一个每日日语搭子", "non_goals": ["不做考试题库"], "success_criteria": ["每天能对话十分钟"]},
    "questions": QUESTIONS,
    "needs_mockup": True,
    "mockup_note": "对话页和复习页的布局",
    "read_notes": ["仓库只有初始提交，没有 README"],
})

REFINE_OK = json.dumps({
    "charter": {"goal": "每天十分钟的日语对话搭子", "non_goals": ["不做题库"], "success_criteria": ["连续七天用它"]},
    "tasks": [
        {"title": "画 mockup", "description": "对话页与复习页", "kind": "mockup"},
        {"title": "搭骨架", "description": "Next.js + FastAPI", "kind": "scaffold"},
    ],
    "needs_mockup": True,
})

PROPOSAL = {
    "charter": {"goal": "g", "non_goals": [], "success_criteria": []},
    "tasks": [{"title": "one", "description": "d", "kind": "mockup"}, {"title": "two", "description": "d", "kind": "feature"}],
    "needs_mockup": True, "mockup_task_missing": False,
}


#: The project id the flow tests' records belong to; `_client` sets it to the registry's.
_PID = ["p"]


def _record(**overrides):
    base = {
        "project_id": _PID[0],
        "status": "questions", "brief": "b", "round": "r1",
        "questions": [{"id": "a", "question": "A?", "why": "", "suggestions": []}],
        "answers": {}, "charter_draft": {"goal": "g", "non_goals": [], "success_criteria": []},
        "needs_mockup": True, "mockup_note": "", "read_notes": [],
        "context": {"readme": False, "tree_entries": 0, "git_log_entries": 0, "unread": [], "text": "(none)"},
        "proposal": None,
    }
    base.update(overrides)
    return base


def _agent(prompt: str, cwd) -> str:
    return GENERATE_OK if "charter_draft" in prompt else REFINE_OK


# --- parsing ------------------------------------------------------------------------------

def test_parse_generate_takes_the_object_out_of_prose():
    out = parse_generate("Sure:\n```json\n" + GENERATE_OK + "\n```")
    assert out["charter_draft"]["goal"] == "做一个每日日语搭子"
    assert [q["id"] for q in out["questions"]] == ["audience", "platform", "scope"]
    assert out["questions"][0]["suggestions"] == ["自己", "初学者"]
    assert out["needs_mockup"] is True
    assert out["read_notes"] == ["仓库只有初始提交，没有 README"]


@pytest.mark.parametrize("text", [
    "the agent refused",
    "[]",
    '{"questions": []}',
    json.dumps({"charter_draft": {"goal": ""}, "questions": QUESTIONS, "needs_mockup": False}),
    # one bad item fails the whole answer — not "the two good ones"
    json.dumps({"charter_draft": {"goal": "g"}, "questions": QUESTIONS[:2] + [{"no_question": 1}], "needs_mockup": False}),
    # fewer than the contract's minimum
    json.dumps({"charter_draft": {"goal": "g"}, "questions": QUESTIONS[:2], "needs_mockup": False}),
])
def test_parse_generate_fails_closed(text):
    with pytest.raises(KickoffError):
        parse_generate(text)


@pytest.mark.parametrize("bad", [
    {"charter_draft": {"goal": "g", "non_goals": ["a", 3]}, "questions": QUESTIONS, "needs_mockup": False},
    {"charter_draft": {"goal": "g"}, "questions": QUESTIONS, "needs_mockup": False, "read_notes": "one string"},
    {"charter_draft": {"goal": "g"}, "questions": QUESTIONS[:2] + [{**QUESTIONS[2], "suggestions": ["a", ""]}], "needs_mockup": False},
    {"charter_draft": {"goal": "g", "success_criteria": [str(i) for i in range(9)]}, "questions": QUESTIONS, "needs_mockup": False},
])
def test_parse_refuses_a_malformed_string_list_rather_than_trimming_it(bad):
    with pytest.raises(KickoffError):
        parse_generate(json.dumps(bad))


def test_parse_finds_the_object_behind_prose_that_has_braces_of_its_own():
    out = parse_generate("先说明：形如 {\"key\": value} 的对象。示例 {not json}。答案：\n" + GENERATE_OK + "\n完。")
    assert out["charter_draft"]["goal"] == "做一个每日日语搭子"
    # A decodable *example* object before the answer is not the answer.
    out = parse_generate('示例：{"type": "example", "note": "not it"}\n实际答案：' + GENERATE_OK)
    assert out["charter_draft"]["goal"] == "做一个每日日语搭子"
    out2 = parse_refine('{"example": true} then ' + REFINE_OK)
    assert out2["charter"]["goal"] == "每天十分钟的日语对话搭子"
    # An example shaped like the answer — with the key, empty — before the real one.
    out3 = parse_generate('示例：{"questions": []}\n真正的答案：' + GENERATE_OK)
    assert out3["charter_draft"]["goal"] == "做一个每日日语搭子"
    # And when none of them parse, the refusal is about the first.
    with pytest.raises(KickoffError, match="charter|questions"):
        parse_generate('{"questions": []} and {"questions": [1]}')
    with pytest.raises(KickoffError, match="no JSON object"):
        parse_generate("no braces here")


def test_parse_generate_refuses_a_question_id_the_answers_route_would_refuse():
    with pytest.raises(KickoffError, match="id is longer"):
        parse_generate(json.dumps({
            "charter_draft": {"goal": "g"},
            "questions": QUESTIONS[:2] + [{**QUESTIONS[2], "id": "q" * 129}],
            "needs_mockup": False,
        }))


def test_parse_generate_refuses_a_duplicate_id_that_grows_past_the_cap_when_made_unique():
    long_id = "q" * 128
    with pytest.raises(KickoffError, match="once made unique"):
        parse_generate(json.dumps({
            "charter_draft": {"goal": "g"},
            "questions": [{"id": long_id, "question": "A?"}, {"id": long_id, "question": "B?"}, {"question": "C?"}],
            "needs_mockup": False,
        }))


def test_parse_generate_renames_duplicate_question_ids_rather_than_merging_answers():
    out = parse_generate(json.dumps({
        "charter_draft": {"goal": "g"},
        "questions": [
            {"id": "platform", "question": "A?"}, {"id": "platform", "question": "B?"}, {"question": "C?"},
        ],
        "needs_mockup": False,
    }))
    assert [q["id"] for q in out["questions"]] == ["platform", "platform-2", "q3"]


@pytest.mark.parametrize("value", ["false", 1, None])
def test_parse_refuses_a_non_boolean_needs_mockup(value):
    with pytest.raises(KickoffError, match="needs_mockup"):
        parse_generate(json.dumps({"charter_draft": {"goal": "g"}, "questions": QUESTIONS, "needs_mockup": value}))
    with pytest.raises(KickoffError, match="needs_mockup"):
        parse_refine(json.dumps({"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "t", "description": "d", "kind": "feature"}], "needs_mockup": value}))


def test_parse_refine_names_a_missing_or_misplaced_mockup_task_instead_of_repairing():
    proposal = parse_refine(json.dumps({
        "charter": {"goal": "g", "non_goals": [], "success_criteria": ["a"]},
        "tasks": [{"title": "搭骨架", "description": "…", "kind": "scaffold"}],
        "needs_mockup": True,
    }))
    assert proposal["mockup_task_missing"] is True
    later = parse_refine(json.dumps({
        "charter": {"goal": "g", "success_criteria": ["done"]},
        "tasks": [{"title": "code first", "description": "d", "kind": "feature"}, {"title": "mockup", "description": "d", "kind": "mockup"}],
        "needs_mockup": True,
    }))
    assert later["mockup_task_missing"] is True
    with_mockup = parse_refine(json.dumps({
        "charter": {"goal": "g", "success_criteria": ["done"]},
        "tasks": [{"title": "画 mockup", "description": "d", "kind": "mockup"}, {"title": "x", "description": "d", "kind": "custom"}],
        "needs_mockup": True,
    }))
    assert with_mockup["mockup_task_missing"] is False
    assert [t["kind"] for t in with_mockup["tasks"]] == ["mockup", "custom"]
    # A kind outside the contract is refused by name, not filed as "custom" — "mock-up"
    # rounded to custom would be a mockup task quietly turned into something else.
    with pytest.raises(KickoffError, match="mock-up"):
        parse_refine(json.dumps({
            "charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "画 mockup", "description": "d", "kind": "mock-up"}], "needs_mockup": True,
        }))
    # A bad task fails the whole answer.
    with pytest.raises(KickoffError):
        parse_refine(json.dumps({"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "ok", "description": "d", "kind": "feature"}, {"nope": 1}], "needs_mockup": False}))
    with pytest.raises(KickoffError):
        parse_refine(json.dumps({"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [], "needs_mockup": False}))


# --- the context --------------------------------------------------------------------------

def test_gather_context_reads_what_is_there_and_names_what_is_not(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("# Proj\n\nhello\n", encoding="utf-8")
    ctx = gather_context(root)
    assert ctx.readme is not None and ctx.readme.startswith("# Proj")
    assert ctx.tree == ["src/", "README.md"]
    # Not a git repository: said, not shown as "no history".
    assert ctx.git_log == []
    assert any("not a git repository" in u for u in ctx.unread)
    text = ctx.as_prompt_text()
    assert "Could NOT be read" in text and "not a git repository" in text
    assert ctx.summary()["text"] == text


def test_gather_context_tries_the_next_readme_when_the_first_is_not_a_file(tmp_path):
    root = tmp_path / "proj"
    (root / "README.md").mkdir(parents=True)  # a directory under the README name
    (root / "README.txt").write_text("plain text readme\n", encoding="utf-8")
    ctx = gather_context(root)
    assert ctx.readme == "plain text readme"
    assert any(u.startswith("README.md (not a regular file") for u in ctx.unread)


def test_gather_context_names_a_readme_that_is_not_utf8_as_unread_and_tries_the_next(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_bytes("日本語の説明\n".encode("cp932"))
    (root / "README.txt").write_text("fallback\n", encoding="utf-8")
    ctx = gather_context(root)
    assert ctx.readme == "fallback"
    assert any(u.startswith("README.md (") and "not UTF-8" in u for u in ctx.unread)


def test_read_plain_text_does_not_call_a_cut_multibyte_sequence_an_error(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("日本語" * 100, encoding="utf-8")  # 3 bytes per character
    # A limit that lands inside a character: the partial one is held back, not an error.
    out, cut = kickoff._read_plain_text(f, 10)
    assert out == "日本語" and cut is True
    assert kickoff._read_plain_text(f, 10_000) == ("日本語" * 100, False)
    with pytest.raises(OSError, match="not UTF-8"):
        f.write_bytes(b"\xff\xfe not utf8")
        kickoff._read_plain_text(f, 10_000)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_read_kickoff_calls_a_file_past_the_bound_malformed_rather_than_reading_a_prefix(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".agora").mkdir(parents=True)
    write_kickoff(root, _record())
    good = (root / KICKOFF_RELATIVE_PATH).read_bytes()
    # A valid record followed by whitespace and a tail the flow never wrote: past the bound.
    monkeypatch.setattr(kickoff, "RECORD_CHARS", len(good) + 10)
    (root / KICKOFF_RELATIVE_PATH).write_bytes(good + b" " * 20 + b"<<<<<<< conflict tail")
    assert read_kickoff(root).reason == "malformed"
    # Under the bound, whitespace after the object is still a record.
    (root / KICKOFF_RELATIVE_PATH).write_bytes(good + b"  ")
    assert read_kickoff(root).available is True
    # And the README head is a head by design: cut, not refused.
    (root / "README.md").write_text("x" * 50, encoding="utf-8")
    assert kickoff._read_plain_text(root / "README.md", 10) == ("x" * 10, True)


def test_write_kickoff_refuses_a_record_the_read_could_not_take_back(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write_kickoff(root, _record())
    before = read_kickoff(root).record
    with pytest.raises(KickoffError, match="bytes"):
        write_kickoff(root, _record(answers={"a": "x" * (kickoff.RECORD_CHARS + 1)}))
    # The good record is still there, untouched.
    assert read_kickoff(root).record == before


def test_kickoff_answers_are_bounded_so_the_record_stays_readable(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record())
    rev = _disk_rev(root)
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"a": "x" * (kickoff.ANSWER_CHARS + 1)}, "expected_revision": rev})
        assert resp.status_code == 422
        assert _disk_rev(root) == rev
        ok = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"a": "x" * kickoff.ANSWER_CHARS}, "expected_revision": rev})
        assert ok.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_read_kickoff_does_not_read_through_a_link(tmp_path):
    elsewhere = tmp_path / "elsewhere" / ".agora"
    elsewhere.mkdir(parents=True)
    root = tmp_path / "proj"
    root.mkdir()
    write_kickoff(tmp_path / "elsewhere", _record(project_id="other"))
    try:
        (root / ".agora").symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    read = read_kickoff(root, "other")
    assert read.available is False and read.reason == "linked"


def test_gather_context_names_a_git_log_that_is_not_utf8_as_unread(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="abc 日本語\n".encode("cp932"), stderr=b""))
    ctx = gather_context(root)
    assert ctx.git_log == []
    assert any("git log (not UTF-8" in u for u in ctx.unread)


def test_gather_context_without_readme_says_none_not_empty(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    ctx = gather_context(root)
    assert ctx.readme is None
    assert ctx.summary()["readme"] is False
    assert ctx.is_empty is True
    (root / "src").mkdir()
    assert gather_context(root).is_empty is False


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_gather_context_does_not_follow_a_readme_symlink(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOKEN=abc\n", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    try:
        (root / "README.md").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    ctx = gather_context(root)
    assert ctx.readme is None
    assert "TOKEN" not in ctx.as_prompt_text()
    assert any("README.md" in u and "not followed" in u for u in ctx.unread)


# --- the record -----------------------------------------------------------------------------

def test_read_kickoff_tells_absent_from_malformed(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    absent = read_kickoff(root)
    assert (absent.available, absent.reason) == (False, "absent")
    (root / KICKOFF_RELATIVE_PATH).parent.mkdir(parents=True)
    (root / KICKOFF_RELATIVE_PATH).write_text("{not json", encoding="utf-8")
    assert read_kickoff(root).reason == "malformed"
    (root / KICKOFF_RELATIVE_PATH).write_text('{"status": "elsewhere"}', encoding="utf-8")
    assert read_kickoff(root).reason == "malformed"
    # A status alone is not a record: the flow reads questions, answers, the draft and
    # the context off it, and a file without them is malformed, not available.
    write_kickoff(root, {"status": "questions"})
    assert read_kickoff(root).reason == "malformed"
    write_kickoff(root, _record())
    read = read_kickoff(root)
    assert read.available and read.record is not None and read.record["status"] == "questions"
    # Per status: a "proposed" record without a proposal, an "adopted" one without the
    # sealed payload, are malformed.
    write_kickoff(root, _record(status="proposed", proposal=None))
    assert read_kickoff(root).reason == "malformed"
    write_kickoff(root, _record(status="adopted"))
    assert read_kickoff(root).reason == "malformed"


def test_read_kickoff_rejects_non_string_nested_values_and_a_missing_revision(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    write_kickoff(root, _record(answers={"a": 1}))
    assert read_kickoff(root).reason == "malformed"
    write_kickoff(root, _record(read_notes=["ok", 3]))
    assert read_kickoff(root).reason == "malformed"
    # A revision that does not match the content — a hand edit, a merge — is not a
    # record the flow may mutate.
    write_kickoff(root, _record())
    path = root / KICKOFF_RELATIVE_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["answers"] = {"a": "edited by hand"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert read_kickoff(root).reason == "malformed"


def test_fingerprint_tells_absent_from_malformed_and_changes_with_the_record(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    absent = read_kickoff(root).fingerprint()
    (root / KICKOFF_RELATIVE_PATH).parent.mkdir(parents=True)
    (root / KICKOFF_RELATIVE_PATH).write_text("{not json", encoding="utf-8")
    assert read_kickoff(root).fingerprint() != absent
    write_kickoff(root, _record())
    one = read_kickoff(root).fingerprint()
    write_kickoff(root, _record(answers={"a": "x"}))
    assert read_kickoff(root).fingerprint() != one


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_write_kickoff_refuses_to_write_through_links(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    try:
        (root / ".agora").symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    with pytest.raises(KickoffError, match="link"):
        write_kickoff(root, _record())
    assert not (elsewhere / "kickoff.json").exists()
    # A link planted where the temporary file used to go is not written through: the
    # temporary file has a fresh random name and is created exclusively.
    root2 = tmp_path / "proj2"
    (root2 / ".agora").mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text("keep me", encoding="utf-8")
    (root2 / ".agora" / "kickoff.json.tmp").symlink_to(victim)
    write_kickoff(root2, _record())
    assert victim.read_text(encoding="utf-8") == "keep me"
    assert read_kickoff(root2).available is True
    assert [p.name for p in (root2 / ".agora").iterdir() if p.name.endswith(".tmp")] == ["kickoff.json.tmp"]


# --- the routes -----------------------------------------------------------------------------

def _registry(tmp_path: Path) -> ProjectRegistry:
    pid = f"kick{uuid4().hex[:8]}"
    root = tmp_path / pid
    root.mkdir()
    config = {
        "projects": {
            "registry_path": str(tmp_path / ".agora" / "projects.yaml"),
            "default": pid,
            "projects": {
                pid: {
                    "name": "Kick", "root": str(root),
                    "workspaces": {a: str(root / ".agora" / "workspaces" / a) for a in ("codex", "claude", "kiro")},
                }
            },
        }
    }
    registry = ProjectRegistry(config)
    registry.set_projects_base(tmp_path / "base")
    return registry


def _client(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    tasks = TaskStore(tmp_path / "kickoff.db")
    app.dependency_overrides[get_project_registry] = lambda: registry
    app.dependency_overrides[get_task_store] = lambda: tasks
    pid = registry.current_project_id()
    _PID[0] = pid
    monkeypatch.setattr(kickoff, "_run_agent", _agent)
    return TestClient(app), registry, tasks, pid


def _rev(payload) -> str:
    return payload["record"]["revision"]


def _disk_rev(root) -> str:
    rec = read_kickoff(root).record
    assert rec is not None
    return rec["revision"]


def test_kickoff_flow_start_answer_refine_adopt(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    try:
        # Nothing yet: absent, not malformed, not an empty record.
        got = client.get(f"/api/projects/{pid}/kickoff").json()
        assert (got["available"], got["reason"]) == (False, "absent")
        # Steps that need a record say why they cannot run.
        assert client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": None}).status_code == 409

        started = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "一个日语学习搭子", "expected_revision": None}).json()
        assert started["available"] and started["record"]["status"] == "questions"
        assert [q["id"] for q in started["record"]["questions"]] == ["audience", "platform", "scope"]
        assert started["record"]["context"]["readme"] is False
        # The record is in the repository, not the control plane.
        assert (registry.get(pid).root / KICKOFF_RELATIVE_PATH).exists()
        rev = _rev(started)
        # Refining before answering is refused.
        assert client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": rev}).status_code == 409

        answered = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"audience": "自己", "bogus": "x"}, "expected_revision": rev}).json()
        assert answered["record"]["status"] == "answered"
        # An answer to a question that was not asked is dropped, not stored.
        assert answered["record"]["answers"] == {"audience": "自己"}
        assert _rev(answered) != rev
        rev = _rev(answered)

        proposed = client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": rev}).json()
        assert proposed["record"]["status"] == "proposed"
        assert proposed["record"]["proposal"]["tasks"][0]["kind"] == "mockup"
        assert proposed["record"]["proposal"]["mockup_task_missing"] is False
        rev = _rev(proposed)
        # Nothing committed yet.
        assert registry.get(pid).charter.get("goal", "") == ""
        assert tasks.list(project_id=pid, limit=10) == []

        # Changing an answer withdraws the proposal built on the old one.
        again = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"audience": "初学者"}, "expected_revision": rev}).json()
        assert again["record"]["status"] == "answered" and again["record"]["proposal"] is None
        rev = _rev(again)
        assert client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [], "expected_revision": rev}).status_code == 409
        proposed = client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": rev}).json()
        rev = _rev(proposed)

        adopted = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "改过的目标", "non_goals": ["不做题库"], "success_criteria": ["连续七天用它"]},
            "tasks": proposed["record"]["proposal"]["tasks"][:2],
            "expected_revision": rev,
        }).json()
        assert adopted["record"]["status"] == "adopted"
        # The charter went through the human path, edited text and all …
        assert registry.get(pid).charter["goal"] == "改过的目标"
        # … and the tasks are in the store, in the proposal's order, the mockup first.
        created = tasks.list(project_id=pid, limit=10)
        assert [t.title for t in sorted(created, key=lambda t: -t.priority)] == ["画 mockup", "搭骨架"]
        assert all(t.metadata.get("kickoff") is True for t in created)
        # Planned, not backlog: the kickoff *is* their requirements-and-design step, and
        # a backlog task cannot be dispatched — the defect the owner hit on 2026-09-13.
        assert {t.state.value for t in created} == {"planned"}
        assert adopted["record"]["adopted"]["tasks"][0]["kind"] == "mockup"
        # Once is once: a retry (a lost response, a double click) creates nothing again.
        again = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "x", "success_criteria": ["y"]}, "tasks": proposed["record"]["proposal"]["tasks"], "expected_revision": _rev(adopted)})
        assert again.status_code == 409 and "already" in again.text
        # The very same request that adopted — old revision and all — hears "already", not "stale".
        same = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "改过的目标", "non_goals": ["不做题库"], "success_criteria": ["连续七天用它"]},
            "tasks": proposed["record"]["proposal"]["tasks"][:2],
            "expected_revision": rev,
        })
        assert same.status_code == 409 and "already" in same.text
        assert len(tasks.list(project_id=pid, limit=10)) == 2
        # And a terminal record is not reopened by an old tab's answers.
        assert client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"audience": "late"}, "expected_revision": _rev(adopted)}).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_a_remark_about_a_proposal_reaches_the_pass_that_draws_the_next_one(tmp_path, monkeypatch):
    # A box whose text goes nowhere is worse than no box: the conversation invites the
    # person to say what is wrong with the draft, so the instruction has to be in the
    # prompt, with the draft it is about.
    seen: list[str] = []

    def agent(prompt: str, cwd) -> str:
        seen.append(prompt)
        return GENERATE_OK if "charter_draft" in prompt else REFINE_OK

    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(kickoff, "_run_agent", agent)
    try:
        started = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "b", "expected_revision": None}).json()
        answered = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"audience": "自己"}, "expected_revision": _rev(started)}).json()
        first = client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": _rev(answered)}).json()
        # No remark: nothing extra is put in the agent's mouth.
        assert "the owner was not happy" not in seen[-1]
        again = client.post(f"/api/projects/{pid}/kickoff/refine", json={
            "note": "第二条任务不该在这一版里", "expected_revision": _rev(first),
        }).json()
        assert "第二条任务不该在这一版里" in seen[-1]
        # With the draft it points at — "the second task" means nothing without the list.
        assert "画 mockup" in seen[-1]
        # And on the record, so the next reader can see why the proposal changed.
        assert again["record"]["refine_note"] == "第二条任务不该在这一版里"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_mutations_are_pinned_to_the_revision_the_caller_saw(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="answered", answers={"a": "x"}))
    current = _disk_rev(root)
    try:
        # A stale tab: it saw some other content.
        for path, body in [
            ("answers", {"answers": {"a": "y"}, "expected_revision": "stale-token"}),
            ("refine", {"expected_revision": "stale-token"}),
            ("adopt", {"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [], "expected_revision": "stale-token"}),
            ("", {"brief": "again", "expected_revision": "stale-token"}),
        ]:
            resp = client.post(f"/api/projects/{pid}/kickoff/{path}".rstrip("/"), json=body)
            assert resp.status_code == 409 and "kickoff_stale" in resp.text, path
        # Nothing moved, and the agent was never asked for the stale start.
        assert _disk_rev(root) == current
        # The current revision goes through, and the token moves with the content.
        saved = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"a": "y"}, "expected_revision": current}).json()
        assert _rev(saved) != current and saved["record"]["answers"] == {"a": "y"}
    finally:
        app.dependency_overrides.clear()


def test_kickoff_adopt_resumes_after_a_failure_halfway_without_duplicating(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    body = {"charter": {"goal": "edited", "success_criteria": ["done"]}, "tasks": [{"title": "one", "description": "d", "kind": "mockup"}, {"title": "two", "description": "d", "kind": "feature"}], "expected_revision": _disk_rev(root)}
    # The second task's creation blows up the first time.
    real_create = tasks.create
    calls = {"n": 0}

    def flaky(req, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real_create(req, **kwargs)

    monkeypatch.setattr(tasks, "create", flaky)
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        # Halfway: one task exists, the record says so and is still adopting.
        assert [t.title for t in tasks.list(project_id=pid, limit=10)] == ["one"]
        halfway = read_kickoff(root).record
        assert halfway is not None and halfway["status"] == "adopting"
        assert [t["title"] for t in halfway["adopted"]["tasks"]] == ["one"]
        # The retry finishes the job from what was sealed: "one" is not created again, the
        # retry's own body is ignored — a reload that re-sends the original proposal cannot
        # bring back an unticked task or overwrite the edited charter.
        monkeypatch.setattr(tasks, "create", real_create)
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "original", "success_criteria": ["ignored too"]}, "tasks": body["tasks"] + [{"title": "three", "description": "d", "kind": "feature"}],
            "expected_revision": halfway["revision"],
        }).json()
        assert done["record"]["status"] == "adopted"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
        assert registry.get(pid).charter["goal"] == "edited"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_resume_keeps_a_charter_edited_while_the_adoption_sat_half_done(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    body = {"charter": {"goal": "sealed", "success_criteria": ["done"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}
    real_create = tasks.create
    calls = {"n": 0}

    def flaky(req, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real_create(req, **kwargs)

    monkeypatch.setattr(tasks, "create", flaky)
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        assert registry.get(pid).charter["goal"] == "sealed"
        # The person edits the charter through its own editor while the adoption is half done.
        registry.set_charter(pid, goal="edited by hand", non_goals=[], success_criteria=[])
        monkeypatch.setattr(tasks, "create", real_create)
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["record"]["status"] == "adopted"
        # The resume finished the tasks and left the edit alone.
        assert registry.get(pid).charter["goal"] == "edited by hand"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_resume_writes_the_charter_the_first_pass_did_not_reach_even_over_an_old_one(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    # The project had a charter before this kickoff: "goal is non-empty" proves nothing.
    registry.set_charter(pid, goal="old goal", non_goals=[], success_criteria=[])
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    body = {"charter": {"goal": "sealed", "success_criteria": ["done"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}
    real_set = registry.set_charter
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db locked")
        return real_set(*args, **kwargs)

    monkeypatch.setattr(registry, "set_charter", flaky)
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        halfway = read_kickoff(root).record
        assert halfway is not None and halfway["status"] == "adopting"
        assert not halfway["adopted"].get("charter_written")
        assert registry.get(pid).charter["goal"] == "old goal"
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["record"]["status"] == "adopted"
        assert registry.get(pid).charter["goal"] == "sealed"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_resume_after_a_crash_between_charter_and_flag_keeps_a_meanwhile_edit(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    registry.set_charter(pid, goal="old goal", non_goals=[], success_criteria=[])
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    body = {"charter": {"goal": "sealed", "success_criteria": ["done"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}
    # The crash lands after `set_charter` and before the record write that would have
    # recorded it: 1 = seal, 2 = the flag.
    real_write = kickoff.write_kickoff
    writes = {"n": 0}

    def flaky_write(r, record):
        writes["n"] += 1
        if writes["n"] == 2:
            raise OSError("disk full")
        return real_write(r, record)

    monkeypatch.setattr(kickoff, "write_kickoff", flaky_write)
    try:
        with pytest.raises(OSError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        assert registry.get(pid).charter["goal"] == "sealed"
        halfway = read_kickoff(root).record
        assert halfway is not None and halfway["status"] == "adopting" and halfway["adopted"]["charter_written"] is False
        monkeypatch.setattr(kickoff, "write_kickoff", real_write)
        # Case one: nobody touched it — the resume sees the sealed charter in place and
        # does not write it again (no edit is lost, the flag is set).
        registry.set_charter(pid, goal="edited meanwhile", non_goals=[], success_criteria=[])
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["record"]["status"] == "adopted"
        assert registry.get(pid).charter["goal"] == "edited meanwhile"
        # And the record says the sealed charter was not applied over the edit.
        assert done["record"]["adopted"]["charter_kept_edit"] is True
        assert done["record"]["adopted"]["charter_written"] is True
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_start_over_a_halfway_adoption_is_refused(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    body = {"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}
    real_create = tasks.create
    calls = {"n": 0}

    def flaky(req, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real_create(req, **kwargs)

    monkeypatch.setattr(tasks, "create", flaky)
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        halfway = read_kickoff(root).record
        assert halfway is not None and halfway["status"] == "adopting"
        # Starting over would erase the only record of the approved task not yet created.
        resp = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "again", "expected_revision": halfway["revision"]})
        assert resp.status_code == 409 and "kickoff_adopting" in resp.text
        assert read_kickoff(root).record["status"] == "adopting"
        # Resuming is the way out, and it finishes.
        monkeypatch.setattr(tasks, "create", real_create)
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["record"]["status"] == "adopted"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("text", [
    json.dumps({"charter_draft": {"goal": 123}, "questions": QUESTIONS, "needs_mockup": False}),
    json.dumps({"charter_draft": {"goal": "g"}, "questions": QUESTIONS[:2] + [{"id": "x", "question": {"zh": "问"}}], "needs_mockup": False}),
    json.dumps({"charter_draft": {"goal": "g"}, "questions": QUESTIONS[:2] + [{"id": 7, "question": "q"}], "needs_mockup": False}),
    json.dumps({"charter_draft": {"goal": "g"}, "questions": QUESTIONS, "needs_mockup": False, "mockup_note": ["a"]}),
])
def test_parse_generate_refuses_non_text_in_text_fields(text):
    with pytest.raises(KickoffError, match="not text"):
        parse_generate(text)


@pytest.mark.parametrize("task", [
    {"title": {"zh": "画原型"}, "description": "d", "kind": "mockup"},
    {"title": "t", "description": 5, "kind": "feature"},
    {"title": "t", "description": "d", "kind": ["feature"]},
])
def test_parse_refine_refuses_non_text_in_text_fields(task):
    with pytest.raises(KickoffError, match="not text"):
        parse_refine(json.dumps({"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [task], "needs_mockup": False}))


def test_kickoff_adopt_on_another_branch_with_a_different_first_task_creates_it(tmp_path, monkeypatch):
    """Same round id, different approved content (the record came from another branch):
    the task created under the first key must not stand in for the different one."""
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    try:
        first = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "task A", "description": "d", "kind": "feature"}], "expected_revision": _disk_rev(root),
        }).json()
        assert first["record"]["status"] == "adopted"
        # Branch switch: the same round, proposed again, with a different first task.
        write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
        second = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "task B", "description": "d", "kind": "feature"}], "expected_revision": _disk_rev(root),
        }).json()
        assert second["record"]["status"] == "adopted"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["task A", "task B"]
    finally:
        app.dependency_overrides.clear()


def test_parse_refine_requires_kind_description_and_a_success_criterion():
    with pytest.raises(KickoffError, match="kind"):
        parse_refine(json.dumps({"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": [{"title": "t", "description": "d"}], "needs_mockup": False}))
    with pytest.raises(KickoffError, match="description"):
        parse_refine(json.dumps({"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": [{"title": "t", "kind": "feature"}], "needs_mockup": False}))
    with pytest.raises(KickoffError, match="success_criteria"):
        parse_refine(json.dumps({"charter": {"goal": "g"}, "tasks": [{"title": "t", "description": "d", "kind": "feature"}], "needs_mockup": False}))
    # The draft may come without criteria; the questions settle them.
    parse_generate(json.dumps({"charter_draft": {"goal": "g"}, "questions": QUESTIONS, "needs_mockup": False}))


def test_fill_substitutes_in_one_pass_so_a_brief_may_contain_a_token():
    out = kickoff._fill("N={name} B={brief} C={context}", name="{brief}", brief="say {context} literally", context="REPO")
    assert out == "N={brief} B=say {context} literally C=REPO"


def test_parse_refuses_a_charter_field_the_adopt_route_would_refuse():
    with pytest.raises(KickoffError, match="charter goal is longer"):
        parse_refine(json.dumps({"charter": {"goal": "g" * 2001, "success_criteria": ["c"]}, "tasks": [{"title": "t", "description": "d", "kind": "feature"}], "needs_mockup": False}))
    # List items follow the charter editor's own cap (500), so the result can be saved there again.
    with pytest.raises(KickoffError, match="success_criteria is longer than 500"):
        parse_generate(json.dumps({"charter_draft": {"goal": "g", "success_criteria": ["c" * 501]}, "questions": QUESTIONS, "needs_mockup": False}))


def test_run_agent_removes_its_scratch_directory_when_the_call_fails(monkeypatch, tmp_path):
    made: list[str] = []
    real_mkdtemp = kickoff.tempfile.mkdtemp
    monkeypatch.setattr(kickoff.tempfile, "mkdtemp", lambda **k: (made.append(real_mkdtemp(**k)), made[-1])[1])
    monkeypatch.setattr(kickoff, "resolve_runtime_command", lambda cmd: cmd)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(KickoffError, match="TimeoutExpired"):
        kickoff._run_agent("p", tmp_path)
    assert made and not Path(made[0]).exists()


def test_parse_refine_refuses_an_over_long_field_rather_than_cutting_it():
    with pytest.raises(KickoffError, match="title"):
        parse_refine(json.dumps({"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "t" * 301, "description": "d", "kind": "feature"}], "needs_mockup": False}))
    with pytest.raises(KickoffError, match="description"):
        parse_refine(json.dumps({
            "charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "t", "description": "d" * 20_001, "kind": "feature"}], "needs_mockup": False,
        }))


@pytest.mark.parametrize("body", [
    {"charter": {"goal": "   ", "success_criteria": ["c"]}, "tasks": []},
    {"charter": {"goal": "g", "success_criteria": []}, "tasks": []},
    {"charter": {"goal": "g", "success_criteria": ["c", "  "]}, "tasks": []},
    {"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": [{"title": "t", "description": "d", "kind": "weird"}]},
    {"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": [{"title": "t", "description": " ", "kind": "feature"}]},
    {"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": [{"title": "t", "kind": "feature"}]},
])
def test_kickoff_adopt_route_holds_the_person_to_the_same_contract(tmp_path, monkeypatch, body):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)})
        assert resp.status_code == 422, resp.text
        assert read_kickoff(root).record["status"] == "proposed"
        assert tasks.list(project_id=pid, limit=10) == []
    finally:
        app.dependency_overrides.clear()


def test_kickoff_adopted_record_on_a_fresh_control_plane_says_so_and_can_be_reapplied(tmp_path, monkeypatch):
    """The record travels with the repository; the charter and tasks do not."""
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "sealed goal", "non_goals": [], "success_criteria": ["c"]},
        "approved": [
            {"key": "r1-1-abc", "title": "one", "description": "d", "kind": "mockup"},
            {"key": "r1-2-def", "title": "two", "description": "d", "kind": "feature"},
        ],
        "tasks": [
            {"key": "r1-1-abc", "task_id": "gone-1", "title": "one", "kind": "mockup"},
            {"key": "r1-2-def", "task_id": "gone-2", "title": "two", "kind": "feature"},
        ],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed, adopted_at="2026-09-12T00:00:00+00:00"))
    try:
        read = client.get(f"/api/projects/{pid}/kickoff").json()
        assert read["record"]["status"] == "adopted"
        # Said, not assumed: nothing of it is on this control plane.
        # The receipt names ids this store does not hold: not complete here either.
        assert read["here"] == {"applied_here": False, "charter": "absent", "tasks_recorded": 2, "tasks_present": 0, "receipt_complete": False, "complete": False, "key_conflict": False}
        body = {"charter": sealed["charter"], "tasks": [{k: t[k] for k in ("title", "description", "kind")} for t in sealed["approved"]], "expected_revision": read["record"]["revision"]}
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).json()
        assert done["record"]["status"] == "adopted"
        assert done["here"]["complete"] is True and done["here"]["tasks_present"] == 2
        assert registry.get(pid).charter["goal"] == "sealed goal"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
        # The first adoption's time stands; the recorded ids are this machine's now.
        assert done["record"]["adopted_at"] == "2026-09-12T00:00:00+00:00"
        assert all(t["task_id"] not in {"gone-1", "gone-2"} for t in done["record"]["adopted"]["tasks"])
        # Complete here: adopting again is "already adopted".
        again = client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        assert again.status_code == 409 and "already" in again.text
        # A charter edited *after* the adoption is ordinary: the adoption stays complete.
        registry.set_charter(pid, goal="edited later", non_goals=[], success_criteria=["c"])
        later = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert later["charter"] == "differs" and later["complete"] is True
    finally:
        app.dependency_overrides.clear()


def test_kickoff_charter_only_adoption_from_elsewhere_is_not_complete_here(tmp_path, monkeypatch):
    """No task to prove the adoption by: a charter A here and a sealed B with zero tasks
    must not read as complete. The marker decides, not the count."""
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    registry.set_charter(pid, goal="charter A", non_goals=[], success_criteria=["a"])
    sealed = {
        "charter": {"goal": "charter B", "non_goals": [], "success_criteria": ["b"]},
        "approved": [], "tasks": [],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    try:
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here == {"applied_here": False, "charter": "differs", "tasks_recorded": 0, "tasks_present": 0, "receipt_complete": True, "complete": False, "key_conflict": False}
        # And starting over is refused: the seal is the only copy of B.
        assert client.post(f"/api/projects/{pid}/kickoff", json={"brief": "x", "expected_revision": _disk_rev(root)}).status_code == 409
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}).json()
        assert done["here"]["applied_here"] is True and done["here"]["complete"] is True
        assert registry.get(pid).charter["goal"] == "charter B"
        # Applied here: a later edit of the charter is an edit, and the adoption stays complete.
        registry.set_charter(pid, goal="charter B, edited", non_goals=[], success_criteria=["b"])
        later = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert later["charter"] == "differs" and later["complete"] is True
    finally:
        app.dependency_overrides.clear()


def test_kickoff_reapply_clears_a_kept_edit_note_from_the_machine_the_record_came_from(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "B", "non_goals": [], "success_criteria": ["b"]},
        "approved": [], "tasks": [],
        "charter_before": None, "charter_written": True, "charter_kept_edit": True,
    }
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    try:
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}).json()
        assert registry.get(pid).charter["goal"] == "B"
        # Written here: the note that it was kept, not written, no longer holds.
        assert done["record"]["adopted"]["charter_kept_edit"] is False
    finally:
        app.dependency_overrides.clear()


def test_kickoff_key_scan_is_one_query_not_pages(tmp_path, monkeypatch):
    """The scan the resume relies on is a single statement — one snapshot — and refuses
    past its ceiling rather than resuming on a partial list."""
    from agora.api import kickoff_router
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    calls: list[dict] = []
    real_list = tasks.list

    def counting(**kwargs):
        calls.append(kwargs)
        return real_list(**kwargs)

    monkeypatch.setattr(tasks, "list", counting)
    kickoff_router._tasks_by_kickoff_key(tasks, pid)
    assert len(calls) == 1 and calls[0]["offset"] == 0 and calls[0]["limit"] == kickoff_router._TASK_SCAN_CEILING + 1
    monkeypatch.setattr(kickoff_router, "_TASK_SCAN_CEILING", 1)
    # The public route takes tasks only against a settled goal (CR-61).
    registry.set_charter(pid, goal="g", non_goals=[], success_criteria=[])
    for title in ("a", "b"):
        made = client.post("/api/tasks", json={"project_id": pid, "title": title, "description": "d"})
        assert made.status_code == 201, made.text
    if len(real_list(project_id=pid, limit=10)) >= 2:
        with pytest.raises(Exception, match="kickoff_task_scan_too_large"):
            kickoff_router._tasks_by_kickoff_key(tasks, pid)


def test_kickoff_resume_is_pinned_to_the_seals_own_revisions(tmp_path, monkeypatch):
    """The same request again (pre-seal revision) and the panel (current revision) may
    resume; a page holding some other record's revision may not."""
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    pre = _disk_rev(root)
    body = {"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": PROPOSAL["tasks"], "expected_revision": pre}
    real_create = tasks.create
    calls = {"n": 0}

    def flaky(req, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real_create(req, **kwargs)

    monkeypatch.setattr(tasks, "create", flaky)
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        monkeypatch.setattr(tasks, "create", real_create)
        # Some other record's revision: refused, nothing created.
        stale = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": "another-branch"})
        assert stale.status_code == 409 and "kickoff_stale" in stale.text
        assert len(tasks.list(project_id=pid, limit=10)) == 1
        # The pre-seal revision — the same request again — resumes.
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).json()
        assert done["record"]["status"] == "adopted"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
    finally:
        app.dependency_overrides.clear()


def test_gather_context_names_an_uninspectable_git_dir_as_unread_not_as_no_history(tmp_path, monkeypatch):
    from agora.fsprobe import PathProbe
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    real_probe = kickoff.probe_path

    def probe(path, **kwargs):
        if Path(path).name == ".git":
            return PathProbe(kind="unknown", error=PermissionError("acl"))
        return real_probe(path, **kwargs)

    monkeypatch.setattr(kickoff, "probe_path", probe)
    ctx = gather_context(root)
    assert ctx.git_log == []
    assert any("could not be inspected" in u for u in ctx.unread)
    assert not any("not a git repository" in u for u in ctx.unread)


def test_gather_context_counts_the_cut_against_the_shown_list_not_the_raw_directory(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    for i in range(kickoff.TREE_ENTRIES):
        (root / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    ctx = gather_context(root)
    # Exactly the entries fit; the two filtered names are not "more".
    assert len(ctx.tree) == kickoff.TREE_ENTRIES and not any(e.startswith("…") for e in ctx.tree)
    (root / "one-more.txt").write_text("x", encoding="utf-8")
    assert gather_context(root).tree[-1] == "… (1 more)"


def test_previous_adoptions_are_trimmed_to_a_byte_budget_and_the_drop_is_counted(monkeypatch):
    monkeypatch.setattr(kickoff, "RECEIPTS_CHARS", 400)
    big = lambda n: {"round": f"r{n}", "adopted_at": None, "charter": {"goal": "g" * 150, "non_goals": [], "success_criteria": []}, "approved": [], "tasks": [], "charter_kept_edit": False}
    old = _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, previous_adoptions=[big(1), big(2)], previous_adoptions_dropped=3,
                  adopted={"charter": {"goal": "new", "non_goals": [], "success_criteria": ["c"]}, "approved": [], "tasks": [], "charter_before": None, "charter_written": True, "charter_kept_edit": False})
    receipts, dropped = kickoff.previous_adoptions_after(old)
    # Newest kept, oldest dropped, and the count carries the old record's own count.
    assert [r["round"] for r in receipts][-1] == "r1" or receipts[-1]["charter"]["goal"] == "new"
    assert receipts[-1]["charter"]["goal"] == "new"
    assert dropped == 3 + (3 - len(receipts))
    assert len(json.dumps(receipts, ensure_ascii=False).encode("utf-8")) <= 400


@pytest.mark.parametrize("bad_seal", [
    {"charter": {"goal": "g", "non_goals": [], "success_criteria": []}, "approved": [], "tasks": []},
    {"charter": {"goal": "  ", "non_goals": [], "success_criteria": ["c"]}, "approved": [], "tasks": []},
    {"charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]}, "approved": [{"key": "k", "title": "t", "description": "", "kind": "feature"}], "tasks": []},
    {"charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]}, "approved": [{"key": "k", "title": "t", "description": "d", "kind": "weird"}], "tasks": []},
])
def test_a_seal_the_adopt_route_would_refuse_reads_as_malformed_not_as_resumable(tmp_path, bad_seal):
    root = tmp_path / "proj"
    root.mkdir()
    write_kickoff(root, _record(status="adopting", answers={"a": "x"}, proposal=PROPOSAL,
                                adopted={**bad_seal, "charter_before": None, "charter_written": False, "charter_kept_edit": False}))
    assert read_kickoff(root).reason == "malformed"
    # A seal that meets the contract still reads.
    good = {"charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]}, "approved": [{"key": "k", "title": "t", "description": "d", "kind": "feature"}], "tasks": []}
    write_kickoff(root, _record(status="adopting", answers={"a": "x"}, proposal=PROPOSAL,
                                adopted={**good, "charter_before": None, "charter_written": False, "charter_kept_edit": False}))
    assert read_kickoff(root).available is True


def test_kickoff_here_measures_against_the_approved_list_not_the_written_back_one(tmp_path, monkeypatch):
    """A re-apply that failed halfway writes the record's task list back shorter than
    the approved list; the next look must still count against what was approved."""
    from agora.api import kickoff_router
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]},
        "approved": [
            {"key": "r1-1-aaa", "title": "one", "description": "d", "kind": "feature"},
            {"key": "r1-2-bbb", "title": "two", "description": "d", "kind": "feature"},
        ],
        "tasks": [],  # written back short
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    registry.set_charter(pid, goal="g", non_goals=[], success_criteria=["c"])
    registry.store.set_setting(kickoff_router._adopted_marker_key(pid), kickoff.seal_fingerprint(sealed) + ":wrote")
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    try:
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["applied_here"] is True and here["tasks_recorded"] == 2 and here["tasks_present"] == 0 and here["complete"] is False
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}).json()
        assert done["here"]["tasks_present"] == 2 and done["here"]["complete"] is True
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
    finally:
        app.dependency_overrides.clear()


def test_gather_context_names_a_failed_readme_listing_as_unread(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# here\n", encoding="utf-8")
    real_listdir = os.listdir
    monkeypatch.setattr(kickoff.os, "listdir", lambda p: (_ for _ in ()).throw(PermissionError("acl")) if Path(p) == root else real_listdir(p))
    ctx = gather_context(root)
    assert ctx.readme is None
    assert any(u.startswith("README lookup (") for u in ctx.unread)


def test_gather_context_does_not_count_lines_of_a_cut_readme(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("line\n" * 5000, encoding="utf-8")
    monkeypatch.setattr(kickoff, "README_CHARS", 200)  # the read limit is README_CHARS * 4 = 800 bytes
    ctx = gather_context(root)
    assert ctx.readme is not None
    assert "goes on past the read limit" in ctx.readme
    assert "more lines" not in ctx.readme
    # And a whole short file still gets its honest line count, after the cap.
    (root / "README.md").write_text("l\n" * (kickoff.README_LINES + 3), encoding="utf-8")
    assert gather_context(root).readme.endswith("… (3 more lines)")


def test_kickoff_start_over_an_unreadable_record_is_refused_before_the_agent_runs(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    (root / ".agora").mkdir(parents=True, exist_ok=True)
    (root / KICKOFF_RELATIVE_PATH).write_text("{not json", encoding="utf-8")
    ran = {"n": 0}
    monkeypatch.setattr(kickoff, "_run_agent", lambda prompt, cwd: (ran.__setitem__("n", ran["n"] + 1), GENERATE_OK)[1])
    try:
        # The page saw no record (expected_revision null); the file is malformed now.
        resp = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "b", "expected_revision": None})
        assert resp.status_code == 409 and "malformed" in resp.text
        assert ran["n"] == 0
    finally:
        app.dependency_overrides.clear()


def test_kickoff_adopt_is_refused_before_the_seal_when_the_adopted_record_would_not_fit(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    before = (root / KICKOFF_RELATIVE_PATH).read_bytes()
    # The seal itself would fit; the adopted record after it (ids, time, flags) would not.
    sealed_size = len(before) + 600
    monkeypatch.setattr(kickoff, "RECORD_CHARS", sealed_size)
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root),
        })
        assert resp.status_code == 409 and "kickoff_record_too_large" in resp.text
        # Nothing sealed, nothing created, nothing written.
        assert (root / KICKOFF_RELATIVE_PATH).read_bytes() == before
        assert tasks.list(project_id=pid, limit=10) == []
        assert not (registry.get(pid).charter or {}).get("goal")
    finally:
        app.dependency_overrides.clear()


def test_gather_context_does_not_follow_a_gitfile_pointing_outside_the_project(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(["git", "init", "-q", str(elsewhere)], check=False, capture_output=True)
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {elsewhere / '.git'}\n", encoding="utf-8")
    ctx = gather_context(root)
    assert ctx.git_log == []
    assert any("gitfile pointing outside" in u for u in ctx.unread)
    assert not any("not a git repository" in u for u in ctx.unread)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_gather_context_does_not_follow_a_linked_git_dir(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(["git", "init", "-q", str(elsewhere)], check=False, capture_output=True)
    root = tmp_path / "proj"
    root.mkdir()
    try:
        (root / ".git").symlink_to(elsewhere / ".git", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    ctx = gather_context(root)
    assert ctx.git_log == [] and any(".git is a link" in u for u in ctx.unread)


def test_write_kickoff_does_not_recreate_a_missing_project_root(tmp_path):
    root = tmp_path / "gone"
    with pytest.raises(KickoffError, match="does not exist"):
        write_kickoff(root, _record())
    assert not root.exists()


def test_a_seal_past_the_adopt_routes_counts_reads_as_malformed(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    too_many = {"charter": {"goal": "g", "non_goals": [], "success_criteria": [f"c{i}" for i in range(21)]}, "approved": [], "tasks": []}
    write_kickoff(root, _record(status="adopting", answers={"a": "x"}, proposal=PROPOSAL,
                                adopted={**too_many, "charter_before": None, "charter_written": False, "charter_kept_edit": False}))
    assert read_kickoff(root).reason == "malformed"


def test_kickoff_reapply_recreates_a_recorded_task_whose_id_lost_its_key(tmp_path, monkeypatch):
    """A partially restored database: the id is there under an ordinary task, the key is
    not. Present by id and absent by key would loop forever; identity is the key."""
    from agora.api import kickoff_router
    from agora.tasks.models import CreateTaskRequest
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    plain = tasks.create(CreateTaskRequest(project_id=pid, title="unrelated", description="d"))
    sealed = {
        "charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]},
        "approved": [{"key": "r1-1-aaa", "title": "one", "description": "d", "kind": "feature"}],
        "tasks": [{"key": "r1-1-aaa", "task_id": plain.task_id, "title": "one", "kind": "feature"}],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    registry.set_charter(pid, goal="g", non_goals=[], success_criteria=["c"])
    registry.store.set_setting(kickoff_router._adopted_marker_key(pid), kickoff.seal_fingerprint(sealed) + ":wrote")
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    try:
        assert client.get(f"/api/projects/{pid}/kickoff").json()["here"]["complete"] is False
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}).json()
        assert done["here"]["complete"] is True
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "unrelated"]
        assert done["record"]["adopted"]["tasks"][0]["task_id"] != plain.task_id
    finally:
        app.dependency_overrides.clear()


def test_kickoff_reapply_retry_after_a_task_failure_keeps_a_charter_edited_meanwhile(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "B", "non_goals": [], "success_criteria": ["b"]},
        "approved": [{"key": "r1-1-aaa", "title": "one", "description": "d", "kind": "feature"}],
        "tasks": [{"key": "r1-1-aaa", "task_id": "gone", "title": "one", "kind": "feature"}],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    body = {"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}
    real_create = tasks.create
    monkeypatch.setattr(tasks, "create", lambda req, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")))
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        assert registry.get(pid).charter["goal"] == "B"
        # Meanwhile the person edits the charter; the retry must not write B back.
        registry.set_charter(pid, goal="C, edited", non_goals=[], success_criteria=["c"])
        monkeypatch.setattr(tasks, "create", real_create)
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["here"]["complete"] is True and done["here"]["charter"] == "differs"
        assert registry.get(pid).charter["goal"] == "C, edited"
        assert [t.title for t in tasks.list(project_id=pid, limit=10)] == ["one"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_adopted_tasks_keep_their_approved_order_to_the_twentieth(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    approved = [{"title": f"task {i:02d}", "description": "d", "kind": "feature"} for i in range(20)]
    try:
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": approved, "expected_revision": _disk_rev(root)}).json()
        assert done["record"]["status"] == "adopted"
        listed = tasks.list(project_id=pid, limit=50)
        priorities = [t.priority for t in sorted(listed, key=lambda t: t.title)]
        assert priorities == sorted(priorities, reverse=True) and len(set(priorities)) == 20
        assert [t.title for t in sorted(listed, key=lambda t: -t.priority)] == [a["title"] for a in approved]
    finally:
        app.dependency_overrides.clear()


def test_store_writes_charter_and_marker_in_one_transaction(tmp_path):
    from agora.projects.store import ProjectStore
    store = ProjectStore(tmp_path / "p.db")
    with pytest.raises(KeyError):
        store.set_charter_with_setting("no-such-project", {"goal": "g"}, "k", "v")
    # The setting insert ran first inside the transaction; the failed update rolled it back.
    assert store.get_setting("k") is None


def test_kickoff_receipt_short_of_an_approved_task_is_not_complete_and_is_repaired(tmp_path, monkeypatch):
    """The last task reached the store; the record write after it failed. The next look
    must not say complete, and adopting again must record — not recreate — that task."""
    from agora.api import kickoff_router
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}).json()
    try:
        assert done["here"]["complete"] is True
        # Simulate the failed record write: the receipt lists one of the two.
        rec = read_kickoff(root).record
        rec["adopted"]["tasks"] = rec["adopted"]["tasks"][:1]
        write_kickoff(root, rec)
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["tasks_present"] == 2 and here["receipt_complete"] is False and here["complete"] is False
        again = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": [], "expected_revision": _disk_rev(root)}).json()
        assert again["here"]["complete"] is True and len(again["record"]["adopted"]["tasks"]) == 2
        assert len(tasks.list(project_id=pid, limit=10)) == 2  # nothing created twice
    finally:
        app.dependency_overrides.clear()


def test_gather_context_git_log_ignores_an_inherited_git_dir(tmp_path, monkeypatch):
    def repo(path: Path, message: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        for cmd in (["git", "init", "-q"], ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", message]):
            subprocess.run(cmd, cwd=str(path), check=True, capture_output=True)
    ours, other = tmp_path / "ours", tmp_path / "other"
    repo(ours, "ours-commit")
    repo(other, "other-commit")
    for var in ("GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    ctx = gather_context(ours)
    assert ctx.git_log and "ours-commit" in ctx.git_log[0]
    assert not any("other-commit" in line for line in ctx.git_log)


def test_project_lock_is_keyed_by_directory_identity(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert kickoff.project_lock(root) is kickoff.project_lock(root / "." / ".")
    assert kickoff.project_lock(root) is kickoff.project_lock(Path(str(root).upper()) if os.name == "nt" else root)
    assert kickoff._directory_identity(root).startswith("id:")
    assert kickoff._directory_identity(tmp_path / "not-yet").startswith("path:")


def test_read_kickoff_calls_every_decode_failure_malformed(tmp_path):
    root = tmp_path / "proj"
    (root / ".agora").mkdir(parents=True)
    # An integer literal past the interpreter's digit limit: json.loads raises ValueError, not JSONDecodeError.
    (root / KICKOFF_RELATIVE_PATH).write_text('{"n": ' + "9" * 5000 + "}", encoding="utf-8")
    assert read_kickoff(root).reason == "malformed"
    (root / KICKOFF_RELATIVE_PATH).write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert read_kickoff(root).reason == "malformed"


def test_kickoff_refusals_use_the_shared_error_shape(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="answered", answers={"a": "x"}))
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"a": "y"}, "expected_revision": "stale"})
        detail = resp.json()["detail"]
        assert resp.status_code == 409 and detail["code"] == "kickoff_stale" and detail["message"] and detail["revision"] == _disk_rev(root)
    finally:
        app.dependency_overrides.clear()


def test_kickoff_receipt_with_another_machines_ids_is_not_complete_and_is_repaired(tmp_path, monkeypatch):
    from agora.api import kickoff_router
    from agora.tasks.models import CreateTaskRequest
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    # The task exists here under its key (created by a re-apply that crashed before the record write)…
    here_task = tasks.create(CreateTaskRequest(project_id=pid, title="one", description="d", kind="feature", metadata={"kickoff": True, "kickoff_key": "r1-1-aaa", "kickoff_kind": "feature"}))
    sealed = {
        "charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]},
        "approved": [{"key": "r1-1-aaa", "title": "one", "description": "d", "kind": "feature"}],
        # … while the receipt still carries the other machine's id.
        "tasks": [{"key": "r1-1-aaa", "task_id": "other-machine-id", "title": "one", "kind": "feature"}],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    registry.set_charter(pid, goal="g", non_goals=[], success_criteria=["c"])
    registry.store.set_setting(kickoff_router._adopted_marker_key(pid), kickoff.seal_fingerprint(sealed) + ":wrote")
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    try:
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["tasks_present"] == 1 and here["receipt_complete"] is False and here["complete"] is False
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}).json()
        assert done["here"]["complete"] is True
        assert done["record"]["adopted"]["tasks"][0]["task_id"] == here_task.task_id
        assert len(tasks.list(project_id=pid, limit=10)) == 1
    finally:
        app.dependency_overrides.clear()


def test_gather_context_names_an_unresolvable_gitfile_target_as_unread(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").write_text("gitdir: somewhere\n", encoding="utf-8")
    real_resolve = Path.resolve

    def boom(self, *a, **k):
        if self.name == "somewhere":
            raise OSError("share gone")
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", boom)
    ctx = gather_context(root)
    assert ctx.git_log == [] and any("could not be resolved" in u for u in ctx.unread)


def test_is_empty_does_not_mistake_a_file_named_with_an_ellipsis_for_the_cut_marker(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "…仕様書.txt").write_text("spec", encoding="utf-8")
    ctx = gather_context(root)
    assert ctx.is_empty is False


def test_kickoff_resume_on_the_pre_seal_revision_must_carry_the_seals_content(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    pre = _disk_rev(root)
    a_body = {"charter": {"goal": "A's goal", "success_criteria": ["a"]}, "tasks": PROPOSAL["tasks"][:1], "expected_revision": pre}
    b_body = {"charter": {"goal": "B's goal", "success_criteria": ["b"]}, "tasks": PROPOSAL["tasks"], "expected_revision": pre}
    monkeypatch.setattr(tasks, "create", lambda req, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")))
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=a_body)  # A sealed, failed halfway
        # B, same pre-seal revision, different choices: not a resume of A's seal.
        resp = client.post(f"/api/projects/{pid}/kickoff/adopt", json=b_body)
        assert resp.status_code == 409 and "kickoff_seal_mismatch" in resp.text
        assert registry.get(pid).charter["goal"] == "A's goal"
        # A's own retry — the same body — resumes.
        monkeypatch.setattr(tasks, "create", tasks.__class__.create.__get__(tasks))
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json=a_body).json()
        assert done["record"]["status"] == "adopted"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_reapply_marks_applied_even_when_the_charter_already_matches(tmp_path, monkeypatch):
    from agora.api import kickoff_router
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "B", "non_goals": [], "success_criteria": ["b"]},
        "approved": [{"key": "r1-1-aaa", "title": "one", "description": "d", "kind": "feature"}],
        "tasks": [{"key": "r1-1-aaa", "task_id": "gone", "title": "one", "kind": "feature"}],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    registry.set_charter(pid, goal="B", non_goals=[], success_criteria=["b"])  # already the approved one
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    body = {"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}
    real_create = tasks.create
    monkeypatch.setattr(tasks, "create", lambda req, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")))
    try:
        with pytest.raises(RuntimeError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        assert registry.store.get_setting(kickoff_router._adopted_marker_key(pid)) == kickoff.seal_fingerprint(sealed) + ":wrote"
        registry.set_charter(pid, goal="C, edited", non_goals=[], success_criteria=["c"])
        monkeypatch.setattr(tasks, "create", real_create)
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["here"]["complete"] is True and registry.get(pid).charter["goal"] == "C, edited"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_record_note_follows_the_marker_when_the_record_write_failed(tmp_path, monkeypatch):
    """Charter and marker committed (`wrote`), the record write after them failed: the
    file still says the edit was kept. Not complete; the next adopt rewrites the note."""
    from agora.api import kickoff_router
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "B", "non_goals": [], "success_criteria": ["b"]},
        "approved": [], "tasks": [],
        "charter_before": None, "charter_written": True, "charter_kept_edit": True,
    }
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    body = {"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}
    real_write = kickoff.write_kickoff
    monkeypatch.setattr(kickoff, "write_kickoff", lambda r, rec: (_ for _ in ()).throw(OSError("disk full")))
    try:
        with pytest.raises(OSError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        assert registry.get(pid).charter["goal"] == "B"
        monkeypatch.setattr(kickoff, "write_kickoff", real_write)
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["applied_here"] is True and here["complete"] is False
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["here"]["complete"] is True
        assert done["record"]["adopted"]["charter_kept_edit"] is False
    finally:
        app.dependency_overrides.clear()


def test_parse_turns_every_decode_failure_into_a_kickoff_error():
    with pytest.raises(KickoffError, match="nests deeper"):
        parse_generate('{"a": ' * 100000 + "1" + "}" * 100000)
    with pytest.raises(KickoffError):
        parse_generate('{"n": ' + "9" * 5000 + "}")


def test_kickoff_task_scan_ceiling_refuses_before_the_seal_and_reads_still_answer(tmp_path, monkeypatch):
    from agora.api import kickoff_router
    from agora.tasks.models import CreateTaskRequest
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    tasks.create(CreateTaskRequest(project_id=pid, title="t", description="d"))
    monkeypatch.setattr(kickoff_router, "_TASK_SCAN_CEILING", 0)
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    before = (root / KICKOFF_RELATIVE_PATH).read_bytes()
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)})
        assert resp.status_code == 409 and "kickoff_task_scan_too_large" in resp.text
        assert (root / KICKOFF_RELATIVE_PATH).read_bytes() == before  # nothing sealed
        assert not (registry.get(pid).charter or {}).get("goal")
        # A read of an adopted record still answers, and says the scan failed.
        write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted={
            "charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]}, "approved": [], "tasks": [],
            "charter_before": None, "charter_written": True, "charter_kept_edit": False}))
        read = client.get(f"/api/projects/{pid}/kickoff")
        assert read.status_code == 200 and read.json()["here"]["scan_failed"] is True and read.json()["here"]["complete"] is False
    finally:
        app.dependency_overrides.clear()


def test_kickoff_resume_of_a_cloned_halfway_record_does_not_trust_its_task_ids(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]},
        "approved": [{"key": "r1-1-aaa", "title": "one", "description": "d", "kind": "feature"}, {"key": "r1-2-bbb", "title": "two", "description": "d", "kind": "feature"}],
        "tasks": [{"key": "r1-1-aaa", "task_id": "other-machine", "title": "one", "kind": "feature"}],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
        "sealed_from_revision": None,
    }
    write_kickoff(root, _record(status="adopting", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    registry.set_charter(pid, goal="g", non_goals=[], success_criteria=["c"])
    try:
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": sealed["charter"], "tasks": [], "expected_revision": _disk_rev(root)}).json()
        assert done["record"]["status"] == "adopted" and done["here"]["complete"] is True
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
        assert all(t["task_id"] != "other-machine" for t in done["record"]["adopted"]["tasks"])
    finally:
        app.dependency_overrides.clear()


def test_kickoff_write_refusals_keep_their_reason(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record())
    rev = _disk_rev(root)
    # Just above the record as it is: the read still takes it; the answer pushes the write over.
    monkeypatch.setattr(kickoff, "RECORD_CHARS", len((root / KICKOFF_RELATIVE_PATH).read_bytes()) + 60)
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"a": "y" * 300}, "expected_revision": rev})
        assert resp.status_code == 409 and resp.json()["detail"]["code"] == "kickoff_record_too_large"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_a_stranger_under_an_approved_key_is_a_conflict_not_the_task(tmp_path, monkeypatch):
    from agora.tasks.models import CreateTaskRequest
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    approved = [{"title": "one", "description": "d", "kind": "mockup"}]
    key = kickoff.task_key("r1", 0, approved[0])
    # An ordinary task that happens to carry the key — not created by any adoption.
    tasks.create(CreateTaskRequest(project_id=pid, title="something else", description="d", metadata={"kickoff_key": key}))
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": approved, "expected_revision": _disk_rev(root)})
        assert resp.status_code == 409 and "kickoff_key_conflict" in resp.text
        # Not taken for the approved task, not doubled: still one task, the stranger.
        assert [t.title for t in tasks.list(project_id=pid, limit=10)] == ["something else"]
        assert read_kickoff(root).record["status"] == "adopting"  # sealed, and said so
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["key_conflict"] is True and here["tasks_present"] == 0
    finally:
        app.dependency_overrides.clear()


def test_kickoff_missing_project_root_reads_as_root_missing_and_refuses_a_start_before_the_agent(tmp_path, monkeypatch):
    import shutil
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    shutil.rmtree(root)
    ran = {"n": 0}
    monkeypatch.setattr(kickoff, "_run_agent", lambda prompt, cwd: (ran.__setitem__("n", ran["n"] + 1), GENERATE_OK)[1])
    try:
        read = client.get(f"/api/projects/{pid}/kickoff").json()
        assert read["available"] is False and read["reason"] == "root_missing"
        resp = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "b", "expected_revision": None})
        assert resp.status_code == 409 and "root_missing" in resp.text and ran["n"] == 0
    finally:
        app.dependency_overrides.clear()


def test_kickoff_a_task_with_the_approved_title_but_another_description_is_not_the_approved_task(tmp_path, monkeypatch):
    from agora.tasks.models import CreateTaskRequest
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    approved = [{"title": "one", "description": "the real acceptance", "kind": "feature"}]
    key = kickoff.task_key("r1", 0, approved[0])
    tasks.create(CreateTaskRequest(project_id=pid, title="one", description="something else", kind="feature",
                                   metadata={"kickoff": True, "kickoff_key": key, "kickoff_kind": "feature"}))
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": approved, "expected_revision": _disk_rev(root)})
        assert resp.status_code == 409 and "kickoff_key_conflict" in resp.text
        assert [t.description for t in tasks.list(project_id=pid, limit=10)] == ["something else"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_a_cancelled_stranger_under_the_key_no_longer_blocks(tmp_path, monkeypatch):
    from agora.tasks.models import CreateTaskRequest
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    approved = [{"title": "one", "description": "d", "kind": "feature"}]
    key = kickoff.task_key("r1", 0, approved[0])
    stranger = tasks.create(CreateTaskRequest(project_id=pid, title="something else", description="d", metadata={"kickoff_key": key}))
    body = {"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": approved, "expected_revision": _disk_rev(root)}
    try:
        assert client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).status_code == 409
        # The workbench's cancel (DELETE) clears the way.
        assert client.delete(f"/api/tasks/{stranger.task_id}").status_code == 200
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)}).json()
        assert done["record"]["status"] == "adopted" and done["here"]["complete"] is True
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "something else"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_read_kickoff_accepts_a_project_root_reached_through_a_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    assert read_kickoff(link).reason == "absent"
    write_kickoff(link, _record())
    assert read_kickoff(link).available is True


def test_kickoff_a_cancelled_approved_task_is_still_the_approved_task_beside_an_archived_stranger(tmp_path, monkeypatch):
    """Two tasks under one key: an archived stranger and the approved task the person
    later cancelled. The approved one wins the key; nothing is recreated."""
    from agora.tasks.models import CreateTaskRequest
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    approved = [{"title": "one", "description": "d", "kind": "feature"}]
    key = kickoff.task_key("r1", 0, approved[0])
    stranger = tasks.create(CreateTaskRequest(project_id=pid, title="stranger", description="d", metadata={"kickoff_key": key}))
    assert client.post(f"/api/tasks/{stranger.task_id}/archive").status_code == 200
    body = {"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": approved, "expected_revision": _disk_rev(root)}
    try:
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).json()
        assert done["record"]["status"] == "adopted" and done["here"]["complete"] is True
        approved_id = done["record"]["adopted"]["tasks"][0]["task_id"]
        # The person cancels the approved task later.
        assert client.delete(f"/api/tasks/{approved_id}").status_code == 200
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["tasks_present"] == 1 and here["complete"] is True
        again = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)})
        assert again.status_code == 409 and "already" in again.text
        assert len(tasks.list(project_id=pid, limit=10)) == 2  # nothing revived, nothing doubled
    finally:
        app.dependency_overrides.clear()


def test_every_kickoff_route_answers_with_the_control_plane_view(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    try:
        started = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "b"}).json()
        assert "here" in started and started["here"] is None  # not adopted: nothing to say
        saved = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"audience": "me"}, "expected_revision": _rev(started)}).json()
        assert "here" in saved
        proposed = client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": _rev(saved)}).json()
        assert "here" in proposed
        # A refine whose final re-read finds an adopted record (a checkout meanwhile) says
        # what is here, not nothing.
        real_read = kickoff.read_kickoff
        adopted = _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted={
            "charter": {"goal": "g", "non_goals": [], "success_criteria": ["c"]}, "approved": [], "tasks": [],
            "charter_before": None, "charter_written": True, "charter_kept_edit": False})
        calls = {"n": 0}

        def read_then_swap(r, project_id=None):
            calls["n"] += 1
            result = real_read(r, project_id)
            if calls["n"] == 3:  # the route's final re-read
                write_kickoff(root, adopted)
                return real_read(r, project_id)
            return result

        monkeypatch.setattr(kickoff, "read_kickoff", read_then_swap)
        answered = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"audience": "you"}, "expected_revision": _rev(proposed)}).json()
        if answered.get("record", {}).get("status") == "adopted":
            assert answered["here"] is not None and answered["here"]["complete"] is False
    finally:
        app.dependency_overrides.clear()


def test_gather_context_finds_a_readme_in_any_case(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Readme.md").write_text("# mixed case\n", encoding="utf-8")
    assert gather_context(root).readme == "# mixed case"


def test_kickoff_a_new_round_keeps_the_receipt_of_the_adoption_before_it(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    try:
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "first goal", "success_criteria": ["c"]}, "tasks": PROPOSAL["tasks"][:1], "expected_revision": _disk_rev(root),
        }).json()
        assert done["record"]["status"] == "adopted"
        started = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "round two", "expected_revision": _disk_rev(root)}).json()
        assert started["record"]["status"] == "questions"
        receipts = started["record"]["previous_adoptions"]
        assert len(receipts) == 1
        assert receipts[0]["round"] == "r1" and receipts[0]["charter"]["goal"] == "first goal"
        assert [t["title"] for t in receipts[0]["approved"]] == ["one"]
        assert receipts[0]["approved"][0]["description"] == "d"  # the acceptance travels with the receipt
        assert receipts[0]["tasks"][0]["task_id"] == done["record"]["adopted"]["tasks"][0]["task_id"]
        # The record with a receipt in it is still a record.
        assert read_kickoff(root, pid).available is True
    finally:
        app.dependency_overrides.clear()


def test_kickoff_marker_binds_the_seal_not_the_round(tmp_path, monkeypatch):
    """Two branches fork one questions record and adopt different things under the same
    round; only the one adopted here is applied here."""
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    try:
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "A", "success_criteria": ["a"]}, "tasks": [{"title": "one", "description": "d", "kind": "feature"}],
            "expected_revision": _disk_rev(root),
        }).json()
        assert done["here"]["applied_here"] is True
        # The other branch: same round "r1", a different charter, no tasks.
        other = {
            "charter": {"goal": "B", "non_goals": [], "success_criteria": ["b"]}, "approved": [], "tasks": [],
            "charter_before": None, "charter_written": True, "charter_kept_edit": False,
        }
        write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=other))
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["applied_here"] is False and here["complete"] is False and here["charter"] == "differs"
        assert client.post(f"/api/projects/{pid}/kickoff", json={"brief": "x", "expected_revision": _disk_rev(root)}).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_kickoff_a_charter_cleared_after_an_adoption_applied_here_is_the_persons_and_stays_cleared(tmp_path, monkeypatch):
    """On the control plane that applied the seal the charter is the person's from then
    on: clearing it is a decision, not a gap — the adoption stays complete and nothing
    writes the approved charter back (the panel promises the rewrite only where the seal
    was never applied)."""
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    try:
        body = {"charter": {"goal": "A", "success_criteria": ["a"]}, "tasks": [], "expected_revision": _disk_rev(root)}
        assert client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).json()["here"]["complete"] is True
        registry.set_charter(pid, goal="", non_goals=[], success_criteria=[])
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here["applied_here"] is True and here["charter"] == "absent" and here["complete"] is True
        again = client.post(f"/api/projects/{pid}/kickoff/adopt", json={**body, "expected_revision": _disk_rev(root)})
        assert again.status_code == 409 and "already" in again.text
        assert not (registry.get(pid).charter or {}).get("goal")
    finally:
        app.dependency_overrides.clear()


def test_kickoff_halfway_view_counts_what_the_store_holds_not_what_the_record_wrote(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    body = {"charter": {"goal": "g", "success_criteria": ["c"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}
    real_write = kickoff.write_kickoff
    writes = {"n": 0}

    def flaky_write(r, record):
        writes["n"] += 1
        if writes["n"] == 3:  # 1 = seal, 2 = charter written, 3 = after the first task
            raise OSError("disk full")
        return real_write(r, record)

    monkeypatch.setattr(kickoff, "write_kickoff", flaky_write)
    try:
        with pytest.raises(OSError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        monkeypatch.setattr(kickoff, "write_kickoff", real_write)
        read = client.get(f"/api/projects/{pid}/kickoff").json()
        assert read["record"]["status"] == "adopting" and read["record"]["adopted"]["tasks"] == []
        # The record wrote none down; the store holds one, and the view says one.
        assert read["here"] == {"applied_here": False, "charter": "absent", "tasks_recorded": 2, "tasks_present": 1, "receipt_complete": False, "complete": False, "key_conflict": False}
    finally:
        app.dependency_overrides.clear()


def test_kickoff_start_over_an_adopted_record_not_applied_here_is_refused(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    sealed = {
        "charter": {"goal": "B", "non_goals": [], "success_criteria": ["b"]},
        "approved": [{"key": "r9-1-abc", "title": "one", "description": "d", "kind": "feature"}],
        "tasks": [{"key": "r9-1-abc", "task_id": "gone", "title": "one", "kind": "feature"}],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    rev = _disk_rev(root)
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "again", "expected_revision": rev})
        assert resp.status_code == 409 and "kickoff_not_applied_here" in resp.text
        assert _disk_rev(root) == rev  # the seal is still there
        # Applied here, a new round may start.
        body = {"charter": sealed["charter"], "tasks": [{"title": "one", "description": "d", "kind": "feature"}], "expected_revision": rev}
        assert client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).json()["here"]["complete"] is True
        started = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "again", "expected_revision": _disk_rev(root)})
        assert started.status_code == 200 and started.json()["record"]["status"] == "questions"
    finally:
        app.dependency_overrides.clear()


def test_router_decides_every_mutation_on_one_snapshot_of_the_record():
    """`git checkout` can replace the record between two reads inside one request; the
    revision check and the record it guards must come from the same read. Pinned at the
    source: no helper reads the file on its own for a decision, and the two-read helpers
    this replaced do not come back under their old names."""
    source = (Path(__file__).resolve().parents[1] / "agora" / "api" / "kickoff_router.py").read_text(encoding="utf-8")
    for gone in ("_require_revision(", "_record_or_409(", "_current_revision("):
        assert gone not in source, gone
    # Call sites pass the snapshot they hold; the def line (`_check_revision(read: ...`) is the one left over.
    assert source.count("_check_revision(read,") + source.count("_check_revision(early,") == source.count("_check_revision(") - 1
    assert source.count("_record_of(read)") + source.count("_record_of(early)") == source.count("_record_of(") - 1


def test_kickoff_reapply_sets_the_approved_charter_over_another_branchs_leftover(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    # This control plane holds an unrelated charter and none of the recorded tasks.
    registry.set_charter(pid, goal="charter A from elsewhere", non_goals=[], success_criteria=["a"])
    sealed = {
        "charter": {"goal": "charter B", "non_goals": [], "success_criteria": ["b"]},
        "approved": [{"key": "r9-1-abc", "title": "one", "description": "d", "kind": "feature"}],
        "tasks": [{"key": "r9-1-abc", "task_id": "gone", "title": "one", "kind": "feature"}],
        "charter_before": None, "charter_written": True, "charter_kept_edit": False,
    }
    write_kickoff(root, _record(status="adopted", answers={"a": "x"}, proposal=PROPOSAL, adopted=sealed))
    try:
        here = client.get(f"/api/projects/{pid}/kickoff").json()["here"]
        assert here == {"applied_here": False, "charter": "differs", "tasks_recorded": 1, "tasks_present": 0, "receipt_complete": False, "complete": False, "key_conflict": False}
        body = {"charter": sealed["charter"], "tasks": [{"title": "one", "description": "d", "kind": "feature"}], "expected_revision": _disk_rev(root)}
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).json()
        assert done["here"]["complete"] is True and done["here"]["charter"] == "matches"
        assert registry.get(pid).charter["goal"] == "charter B"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_another_projects_record_on_the_same_root_is_foreign_not_ours(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    # The registry allows two ids on one directory; the other one got here first.
    write_kickoff(root, _record(project_id="the-other", status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    rev = _disk_rev(root)
    try:
        read = client.get(f"/api/projects/{pid}/kickoff").json()
        assert read["available"] is False and read["reason"] == "foreign"
        # Nothing from this project may touch it: not a start over it, not an adoption of it.
        for path, body in [
            ("", {"brief": "mine"}),
            ("answers", {"answers": {"a": "y"}, "expected_revision": rev}),
            ("refine", {"expected_revision": rev}),
            ("adopt", {"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [], "expected_revision": rev}),
        ]:
            resp = client.post(f"/api/projects/{pid}/kickoff/{path}".rstrip("/"), json=body)
            assert resp.status_code == 409 and "kickoff_foreign" in resp.text, path
        assert _disk_rev(root) == rev
        assert not (registry.get(pid).charter or {}).get("goal")
        assert tasks.list(project_id=pid, limit=10) == []
    finally:
        app.dependency_overrides.clear()


def test_kickoff_adopt_recovers_a_task_created_but_not_yet_recorded(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    body = {"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}
    # The crash lands *between* creating the first task and recording it: the record
    # write after the first create fails.
    real_write = kickoff.write_kickoff
    writes = {"n": 0}

    def flaky_write(r, record):
        writes["n"] += 1
        if writes["n"] == 3:  # 1 = seal, 2 = charter written, 3 = after the first task
            raise OSError("disk full")
        return real_write(r, record)

    monkeypatch.setattr(kickoff, "write_kickoff", flaky_write)
    try:
        with pytest.raises(OSError):
            client.post(f"/api/projects/{pid}/kickoff/adopt", json=body)
        assert [t.title for t in tasks.list(project_id=pid, limit=10)] == ["one"]
        halfway = read_kickoff(root).record
        assert halfway is not None and halfway["status"] == "adopting" and halfway["adopted"]["tasks"] == []
        monkeypatch.setattr(kickoff, "write_kickoff", real_write)
        # The same request again — old revision and all; a resume ignores the body.
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json=body).json()
        assert done["record"]["status"] == "adopted"
        # Found by its key, not created again.
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "two"]
        keys = [t["key"] for t in done["record"]["adopted"]["tasks"]]
        assert [k.rsplit("-", 1)[0] for k in keys] == ["r1-1", "r1-2"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_a_new_round_after_adoption_creates_its_own_tasks(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    monkeypatch.setattr(kickoff, "_run_agent", lambda prompt, cwd: GENERATE_OK if "charter_draft" in prompt else json.dumps({
        "charter": {"goal": "g2", "success_criteria": ["done"]}, "tasks": [{"title": "round two", "description": "d", "kind": "mockup"}], "needs_mockup": True,
    }))
    try:
        adopted = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": PROPOSAL["tasks"], "expected_revision": _disk_rev(root)}).json()
        assert adopted["record"]["status"] == "adopted"
        # Asking again starts a new round …
        started = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "again", "expected_revision": _rev(adopted)}).json()
        assert started["record"]["status"] == "questions" and started["record"]["round"] != "r1"
        rev = _rev(started)
        rev = _rev(client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"audience": "me"}, "expected_revision": rev}).json())
        rev = _rev(client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": rev}).json())
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={"charter": {"goal": "g2", "success_criteria": ["done"]}, "tasks": [{"title": "round two", "description": "d", "kind": "mockup"}], "expected_revision": rev}).json()
        # … whose first task is created, not mistaken for round one's first task.
        assert done["record"]["status"] == "adopted"
        assert sorted(t.title for t in tasks.list(project_id=pid, limit=10)) == ["one", "round two", "two"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_two_approved_tasks_may_share_a_title(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="proposed", answers={"a": "x"}, proposal=PROPOSAL))
    try:
        done = client.post(f"/api/projects/{pid}/kickoff/adopt", json={
            "charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "same", "description": "d", "kind": "feature"}, {"title": "same", "description": "d", "kind": "research"}],
            "expected_revision": _disk_rev(root),
        }).json()
        assert done["record"]["status"] == "adopted"
        assert [t.title for t in tasks.list(project_id=pid, limit=10)] == ["same", "same"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_start_refuses_an_empty_brief_on_an_empty_repository(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(kickoff, "_run_agent", lambda prompt, cwd: (_ for _ in ()).throw(AssertionError("agent must not be called")))
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "   ", "expected_revision": None})
        assert resp.status_code == 422 and "brief_required" in resp.text
    finally:
        app.dependency_overrides.clear()


def test_kickoff_blank_answers_do_not_count_as_answered(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record())
    try:
        saved = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"a": "   "}, "expected_revision": _disk_rev(root)}).json()
        assert saved["record"]["status"] == "questions" and saved["record"]["answers"] == {}
        saved = client.post(f"/api/projects/{pid}/kickoff/answers", json={"answers": {"a": "  yes "}, "expected_revision": _rev(saved)}).json()
        assert saved["record"]["status"] == "answered" and saved["record"]["answers"] == {"a": "yes"}
    finally:
        app.dependency_overrides.clear()


def test_kickoff_start_does_not_overwrite_a_record_that_moved_while_the_agent_worked(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="answered", answers={"a": "x"}))
    seen = _disk_rev(root)

    def slow_agent(prompt, cwd):
        # Meanwhile, inside the same round, another tab saved an answer.
        write_kickoff(root, _record(status="answered", answers={"a": "newer"}))
        return GENERATE_OK

    monkeypatch.setattr(kickoff, "_run_agent", slow_agent)
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "slow one", "expected_revision": seen})
        assert resp.status_code == 409 and "kickoff_stale" in resp.text
        after = read_kickoff(root).record
        assert after is not None and after["revision"] != seen and after["answers"] == {"a": "newer"}
    finally:
        app.dependency_overrides.clear()


def test_kickoff_start_over_a_malformed_file_is_refused_not_overwritten(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root

    def agent_meanwhile(prompt, cwd):
        # Absent when the ask began; something not a record appeared meanwhile.
        (root / KICKOFF_RELATIVE_PATH).parent.mkdir(parents=True, exist_ok=True)
        (root / KICKOFF_RELATIVE_PATH).write_text("{not json", encoding="utf-8")
        return GENERATE_OK

    monkeypatch.setattr(kickoff, "_run_agent", agent_meanwhile)
    try:
        assert client.post(f"/api/projects/{pid}/kickoff", json={"brief": "b", "expected_revision": None}).status_code == 409
        assert read_kickoff(root).reason == "malformed"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_refine_refuses_to_overwrite_answers_saved_while_the_agent_worked(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="answered", answers={"a": "old"}))
    seen = _disk_rev(root)

    def agent_that_races(prompt, cwd):
        # While the agent works, the person saves a new answer.
        rec = read_kickoff(root).record
        assert rec is not None
        rec["answers"] = {"a": "new"}
        write_kickoff(root, rec)
        return REFINE_OK

    monkeypatch.setattr(kickoff, "_run_agent", agent_that_races)
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": seen})
        assert resp.status_code == 409 and "kickoff_stale" in resp.text
        after = read_kickoff(root).record
        assert after is not None and after["answers"] == {"a": "new"} and after["proposal"] is None
    finally:
        app.dependency_overrides.clear()


def test_refine_keeps_the_first_pass_ui_verdict_and_records_what_it_read(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    root = registry.get(pid).root
    write_kickoff(root, _record(status="answered", answers={"a": "x"}, needs_mockup=True))
    (root / "README.md").write_text("# later\n", encoding="utf-8")
    monkeypatch.setattr(kickoff, "_run_agent", lambda prompt, cwd: json.dumps({
        "charter": {"goal": "g", "success_criteria": ["done"]}, "tasks": [{"title": "code", "description": "d", "kind": "feature"}], "needs_mockup": False,
    }))
    try:
        proposed = client.post(f"/api/projects/{pid}/kickoff/refine", json={"expected_revision": _disk_rev(root)}).json()["record"]
        # The second pass said "no UI"; the first pass saw screens, and its verdict stands.
        assert proposed["proposal"]["needs_mockup"] is True
        assert proposed["proposal"]["mockup_task_missing"] is True
        # And what this pass was shown is on record next to what the first was.
        assert "# later" in proposed["context_at_proposal"]["text"]
        assert "# later" not in proposed["context"]["text"]
    finally:
        app.dependency_overrides.clear()


def test_kickoff_record_keeps_the_text_the_agent_was_shown(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    (registry.get(pid).root / "README.md").write_text("# Kick\n\nA tool.\n", encoding="utf-8")
    try:
        started = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "b", "expected_revision": None}).json()
        assert "# Kick" in started["record"]["context"]["text"]
        assert started["record"]["context"]["readme"] is True
        assert isinstance(_rev(started), str) and len(_rev(started)) == 16
    finally:
        app.dependency_overrides.clear()


def test_kickoff_start_reports_agent_failure_as_502_and_writes_nothing(tmp_path, monkeypatch):
    client, registry, tasks, pid = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(kickoff, "_run_agent", lambda prompt, cwd: (_ for _ in ()).throw(KickoffError("agent exited 1: boom")))
    try:
        resp = client.post(f"/api/projects/{pid}/kickoff", json={"brief": "x", "expected_revision": None})
        assert resp.status_code == 502
        assert "boom" in resp.text
        assert not (registry.get(pid).root / KICKOFF_RELATIVE_PATH).exists()
        assert client.get(f"/api/projects/{pid}/kickoff").json()["reason"] == "absent"
    finally:
        app.dependency_overrides.clear()


def test_kickoff_unknown_project_is_404(tmp_path, monkeypatch):
    client, *_ = _client(tmp_path, monkeypatch)
    try:
        assert client.get("/api/projects/nope/kickoff").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_run_agent_failure_shapes_are_named(monkeypatch, tmp_path):
    # The CLI is not on the path → "unavailable", not a traceback.
    monkeypatch.setattr(kickoff, "resolve_runtime_command", lambda cmd: (_ for _ in ()).throw(kickoff.RuntimeLaunchError("no claude")))
    with pytest.raises(KickoffError, match="unavailable"):
        kickoff._run_agent("p", tmp_path)
    # A non-zero exit carries the last stderr line.
    monkeypatch.setattr(kickoff, "resolve_runtime_command", lambda cmd: cmd)
    seen = {}

    def fake_run(*a, **k):
        seen.update(k)
        return subprocess.CompletedProcess(a, 3, stdout=b"", stderr=b"x\nlast line")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(KickoffError, match="last line"):
        kickoff._run_agent("a long prompt", tmp_path)
    # The prompt travels on stdin, not on the command line (Windows caps that at ~32K).
    assert seen.get("input") == "a long prompt".encode("utf-8")
    # Output that is not UTF-8 is refused, not patched with replacement characters.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b'{"x": "\xff"}', stderr=b""))
    with pytest.raises(KickoffError, match="not UTF-8"):
        kickoff._run_agent("p", tmp_path)


def test_run_agent_is_read_only(monkeypatch, tmp_path):
    seen: dict = {}
    monkeypatch.setattr(kickoff, "resolve_runtime_command", lambda cmd: (seen.update(cmd=cmd), cmd)[1])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (seen.update(k), subprocess.CompletedProcess(a, 0, stdout=b"{}", stderr=b""))[1])
    kickoff._run_agent("p", tmp_path)
    for flag in ("--permission-mode", "plan", "--safe-mode", "--no-session-persistence", "--tools"):
        assert flag in seen["cmd"]
    # No tools at all, and not run inside the project: what the agent read is what it was handed.
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == ""
    assert Path(seen["cwd"]).resolve() != tmp_path.resolve()
    assert not Path(seen["cwd"]).exists()  # the scratch directory is gone afterwards
    # And the prompt is not an argument.
    assert "p" not in seen["cmd"]
