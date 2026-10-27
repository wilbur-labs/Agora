"""CR-71 slice 2: backend-authored text in the reader's display language.

Covers the catalog (parity), the setting (endpoint, persistence, startup load), one
message per group written after switching to 日本語 (C run-log notes, D attention
items, E session reasons, F refusals), the session reason rendered at read time, and
the rule that text reaching an agent never follows the reader's language.

The language is reset around every test by ``conftest.reset_display_language``; the
tests below also restore it themselves where they switch it, so neither depends on
the other.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

from agora import messages
from agora.api.app import app, _load_display_language
from agora.attention.models import AttentionKind, AttentionState
from agora.attention.store import AttentionStore
from agora.config.settings import get_config, resolve_control_plane_db_path
from agora.execution.adapters import EVENT_FORMAT_KIRO_STREAM_JSON
from agora.execution.dispatcher import _merge_overlapping
from agora.execution.review_fallback import readable_failure
from agora.execution.runlog import RunLogStore
from agora.execution.sessions import (
    SESSION_FORKED,
    SESSION_LOST,
    SESSION_UNREADABLE,
    AgentSessionStore,
)
from agora.execution.transcript import reader_for
from agora.orchestration.provider_usage import _normalize_kiro_stream_json
from agora.projects.store import ProjectStore
from agora.tasks.router import get_project_registry
from agora.terminal.remote_tasks import RemoteTaskRefused, verify_task

from tests.test_agent_sessions import KIRO_LINES
from tests.test_execution import _planned_task, _settled_worker_run, _system


@pytest.fixture
def ja():
    messages.set_display_language("ja")
    yield
    messages.set_display_language(messages.DEFAULT_LANGUAGE)


# ------------------------------------------------------------------ the catalog


def test_every_message_has_all_three_languages_with_the_same_placeholders():
    for key, entry in messages.MESSAGES.items():
        assert set(entry) == set(messages.LANGUAGES), key
        for lang in messages.LANGUAGES:
            assert isinstance(entry[lang], str) and entry[lang], (key, lang)
        expected = messages.placeholders(entry["zh"])
        for lang in ("ja", "en"):
            assert messages.placeholders(entry[lang]) == expected, (key, lang)


def test_japanese_entries_are_not_left_in_chinese():
    """A ja entry identical to zh is a translation that was never written — except
    where the text is the same in both scripts on purpose."""
    same_on_purpose = {"transcript.list_separator", "transcript.write_lines"}
    for key, entry in messages.MESSAGES.items():
        if key not in same_on_purpose:
            assert entry["ja"] != entry["zh"], key


def test_the_default_is_chinese_and_an_unknown_language_is_refused():
    assert messages.get_display_language() == "zh"
    with pytest.raises(ValueError):
        messages.set_display_language("fr")
    assert messages.get_display_language() == "zh"


def test_say_renders_in_the_current_language(ja):
    assert messages.say("transcript.plan_update") == messages.MESSAGES["transcript.plan_update"]["ja"]
    assert messages.say_in("zh", "transcript.plan_update") == "· 收到计划更新"


def test_the_module_states_the_agent_input_rule():
    assert "never" in messages.__doc__ and "say()" in messages.__doc__
    assert "agent's input" in messages.__doc__


# ------------------------------------------------------------------ the endpoint


def _registry_store(tmp_path):
    return ProjectStore(tmp_path / "display-language.db")


class _Registry:
    def __init__(self, store):
        self.store = store


@pytest.fixture
def client(tmp_path):
    store = _registry_store(tmp_path)
    app.dependency_overrides[get_project_registry] = lambda: _Registry(store)
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.pop(get_project_registry, None)
        messages.set_display_language(messages.DEFAULT_LANGUAGE)


def test_get_returns_the_default(client):
    http, _ = client
    response = http.get("/api/settings/display-language")
    assert response.status_code == 200
    assert response.json() == {"language": "zh"}


def test_put_persists_then_switches(client):
    http, store = client
    response = http.put("/api/settings/display-language", json={"language": "ja"})
    assert response.status_code == 200
    assert response.json() == {"language": "ja"}
    assert store.get_setting(messages.SETTING_DISPLAY_LANGUAGE) == "ja"
    assert messages.get_display_language() == "ja"
    assert messages.say("transcript.plan_update") == "· 計画の更新を受け取りました"
    assert http.get("/api/settings/display-language").json() == {"language": "ja"}


def test_put_refuses_an_unknown_language(client):
    http, store = client
    response = http.put("/api/settings/display-language", json={"language": "fr"})
    assert response.status_code == 422
    assert messages.get_display_language() == "zh"
    assert store.get_setting(messages.SETTING_DISPLAY_LANGUAGE) is None


def test_a_failed_write_leaves_the_language_unchanged(tmp_path):
    class _Broken:
        def set_setting(self, key, value):
            raise sqlite3.OperationalError("database is locked")

    app.dependency_overrides[get_project_registry] = lambda: _Registry(_Broken())
    try:
        response = TestClient(app).put("/api/settings/display-language", json={"language": "ja"})
    finally:
        app.dependency_overrides.pop(get_project_registry, None)
    assert response.status_code == 503
    assert "not saved" in response.json()["detail"]
    assert messages.get_display_language() == "zh"


def test_two_concurrent_puts_leave_saved_and_active_language_equal(tmp_path):
    # A commits ja and stalls before activating; B runs meanwhile. Without the
    # endpoint's lock B would persist+activate en inside that gap and A would then
    # activate ja over it: saved en, in use ja (codex review, CR-71 slice 2).
    import threading

    real = _registry_store(tmp_path)
    b_wrote = threading.Event()

    class _Stalling:
        def set_setting(self, key, value):
            real.set_setting(key, value)
            if value == "ja":
                b_wrote.wait(timeout=1.0)
            else:
                b_wrote.set()

        def get_setting(self, key):
            return real.get_setting(key)

    app.dependency_overrides[get_project_registry] = lambda: _Registry(_Stalling())
    try:
        http = TestClient(app)
        a = threading.Thread(target=lambda: http.put("/api/settings/display-language", json={"language": "ja"}))
        a.start()
        while real.get_setting(messages.SETTING_DISPLAY_LANGUAGE) != "ja":
            pass
        http.put("/api/settings/display-language", json={"language": "en"})
        a.join()
    finally:
        app.dependency_overrides.pop(get_project_registry, None)
    assert messages.get_display_language() == real.get_setting(messages.SETTING_DISPLAY_LANGUAGE)
    messages.set_display_language(messages.DEFAULT_LANGUAGE)


def test_the_put_goes_through_the_origin_guard(client):
    http, store = client
    response = http.put(
        "/api/settings/display-language", json={"language": "ja"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert messages.get_display_language() == "zh"
    assert store.get_setting(messages.SETTING_DISPLAY_LANGUAGE) is None


def test_startup_adopts_the_stored_language():
    ProjectStore(resolve_control_plane_db_path(get_config())).set_setting(
        messages.SETTING_DISPLAY_LANGUAGE, "en",
    )
    _load_display_language()
    assert messages.get_display_language() == "en"


def test_startup_keeps_the_default_when_the_setting_cannot_be_read(monkeypatch, capsys):
    class _Unreadable:
        def __init__(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr("agora.projects.store.ProjectStore", _Unreadable)
    _load_display_language()  # must not raise
    assert messages.get_display_language() == "zh"
    assert "display language could not be loaded" in capsys.readouterr().err


def test_startup_keeps_the_default_for_a_value_outside_the_catalog(capsys):
    ProjectStore(resolve_control_plane_db_path(get_config())).set_setting(
        messages.SETTING_DISPLAY_LANGUAGE, "fr",
    )
    _load_display_language()
    assert messages.get_display_language() == "zh"
    assert "display language could not be loaded" in capsys.readouterr().err


# ---------------------------------------------------- C. run-log notes, in ja


def test_run_log_notes_are_written_in_japanese(ja):
    joined = _merge_overlapping(b"head", b"tail", 100)
    assert messages.MESSAGES["run.merge_gap"]["ja"].encode() in joined
    reader = reader_for(EVENT_FORMAT_KIRO_STREAM_JSON)
    reader.feed('{"type":"inventedEvent","data":{}}')
    assert "解釈できなかったイベント種別" in reader.finish()


def test_the_run_log_cap_notice_is_written_in_japanese(ja, tmp_path, monkeypatch):
    import agora.execution.runlog as runlog

    monkeypatch.setattr(runlog, "MAX_LOG_BYTES", 64)
    logs = RunLogStore(tmp_path / "logs")
    logs.append("run_ja", "x" * 200)
    text = logs.path_for("run_ja").read_bytes().decode("utf-8")
    assert "サイズ上限" in text


def test_the_kiro_truncation_note_does_not_follow_the_reader(ja):
    """It is appended to the semantic output, which the review prompt embeds — agent
    input, so fixed-language (CR-71 P1)."""
    lines = list(KIRO_LINES)
    lines[-1] = lines[-1].replace('"finalTextTruncated":false', '"finalTextTruncated":true')
    semantic, _ = _normalize_kiro_stream_json("\n".join(lines), run_id="run_k", adapter="kiro")
    assert semantic.endswith("[kiro 声明这段最终文本被它自己截断了,显示的不是全文]")


# --------------------------------------------------- D. attention items, in ja


def test_a_review_owed_blocker_is_written_in_japanese(ja, tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    dispatcher.reconcile_reviews_owed()
    items = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)
    assert len(items) == 1
    blocker = items[0]
    assert blocker.title == messages.MESSAGES["review.owed_blocker_title"]["ja"]
    assert messages.MESSAGES["review.owed_after_restart"]["ja"] in blocker.body
    assert "見てください" in blocker.body or "確認" in blocker.body
    assert blocker.run_id == worker.run_id


def test_a_blocker_written_under_another_language_is_the_same_blocker(tmp_path):
    """The title is part of the idempotency key; a switch between two attempts must
    not pile up a second blocker for one missing review."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    dispatcher._block_review_owed(task_id=task.task_id, run_id=worker.run_id, reason="first")
    messages.set_display_language("ja")
    try:
        dispatcher._block_review_owed(task_id=task.task_id, run_id=worker.run_id, reason="second")
    finally:
        messages.set_display_language(messages.DEFAULT_LANGUAGE)
    items = [
        item for item in AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)
        if item.kind == AttentionKind.BLOCKER
    ]
    assert len(items) == 1
    assert items[0].title == dispatcher.REVIEW_FAILED_TITLE  # the first one, untouched


def test_a_failed_reviewers_reason_reads_in_japanese(ja):
    reason = readable_failure("codex", "agent reported: You've hit your usage limit. Try again at 8:44 PM.")
    assert reason == "codex の利用枠を使い切りました（8:44 PM 以降にまた使えるとのことです）"
    assert readable_failure("codex", None) == "（エラーの詳細はありません）"


# ---------------------------------------------- E. session reasons, read time


def _sessions(tmp_path) -> AgentSessionStore:
    return AgentSessionStore(tmp_path / "sessions.db")


def test_a_coded_reason_written_in_chinese_reads_in_japanese(tmp_path):
    sessions = _sessions(tmp_path)
    sessions.mark_lost(
        task_id="t1", adapter="codex",
        reason=messages.say("session.resume_lost", exit_code=1),
        reason_code="session.resume_lost", reason_args={"exit_code": 1},
    )
    assert sessions.get("t1", "codex").reason.startswith("上一次续接会话失败（退出码 1）")
    messages.set_display_language("ja")
    try:
        session = sessions.get("t1", "codex")
        assert session.state == SESSION_LOST
        assert session.reason == messages.say_in("ja", "session.resume_lost", exit_code=1)
        # The refusal built from it is one language, not "stored zh + fresh ja".
        decision = sessions.decide(
            task_id="t1", adapter="codex", workspace=tmp_path, can_resume=True, caller_chooses_id=False,
        )
        assert decision.refusal == (
            messages.say_in("ja", "session.resume_lost", exit_code=1)
            + messages.say_in("ja", "session.lost_refusal_suffix")
        )
    finally:
        messages.set_display_language(messages.DEFAULT_LANGUAGE)


def test_a_legacy_reason_without_a_code_is_shown_as_stored(tmp_path, ja):
    sessions = _sessions(tmp_path)
    sessions.mark_lost(task_id="t1", adapter="codex", reason="旧的原因，原样保留。")
    assert sessions.get("t1", "codex").reason == "旧的原因，原样保留。"
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path, can_resume=True, caller_chooses_id=False,
    )
    assert decision.refusal == "旧的原因，原样保留。" + messages.say_in("ja", "session.lost_refusal_suffix")


def test_a_row_from_before_the_columns_is_read_as_written(tmp_path, ja):
    """The migration: an ``agent_sessions`` table without ``reason_code`` gains the
    columns on open, and its rows keep their text."""
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE agent_sessions (
            task_id TEXT NOT NULL, adapter TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'worker',
            session_id TEXT, workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
            turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, last_run_id TEXT, previous_session_id TEXT,
            cleared_at TEXT, clear_seq INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (task_id, adapter, role))"""
    )
    db.execute(
        "INSERT INTO agent_sessions (task_id, adapter, role, session_id, workspace, state, reason,"
        " turns, first_seen_at, last_seen_at) VALUES ('t1', 'codex', 'worker', 's1', 'w',"
        " 'unreadable', '写于旧版本的原因', 1, 'x', 'x')"
    )
    db.commit()
    db.close()
    sessions = AgentSessionStore(path)
    with sqlite3.connect(path) as check:
        columns = {row[1] for row in check.execute("PRAGMA table_info(agent_sessions)")}
    assert {"reason_code", "reason_args"} <= columns
    assert sessions.get("t1", "codex").reason == "写于旧版本的原因"


def test_fork_retry_and_condition_refusals_follow_the_reader(tmp_path):
    sessions = _sessions(tmp_path)
    sessions.record_observed(run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="s1")
    sessions.mark_forked(task_id="t1", adapter="codex", observed="s2", previous="s1")
    messages.set_display_language("ja")
    try:
        forked = sessions.get("t1", "codex")
        assert forked.state == SESSION_FORKED
        assert forked.reason == messages.say_in("ja", "session.forked", previous="s1", observed="s2")
        # A condition-based refusal carries its code, so the dispatcher can store it.
        decision = sessions.decide(
            task_id="t1", adapter="codex", workspace=tmp_path / "elsewhere",
            can_resume=True, caller_chooses_id=False,
        )
        assert decision.condition_based and decision.refusal_code == "session.wrong_workspace"
        sessions.mark_unreadable(
            task_id="t1", adapter="codex", reason=decision.refusal,
            reason_code=decision.refusal_code, reason_args=decision.refusal_args,
        )
        messages.set_display_language("en")
        unreadable = sessions.get("t1", "codex")
        assert unreadable.state == SESSION_UNREADABLE
        assert unreadable.reason.startswith("This task's session is recorded under a different working directory")
    finally:
        messages.set_display_language(messages.DEFAULT_LANGUAGE)
    # And a confirmed session clears the code with the text.
    sessions.record_observed(run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path, session_id="s2")
    assert sessions.get("t1", "codex").reason is None


def test_retry_reads_in_the_current_language(tmp_path):
    sessions = _sessions(tmp_path)
    sessions.record_observed(run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="s1")
    sessions.mark_lost(task_id="t1", adapter="codex", reason="x")
    assert sessions.retry(task_id="t1", adapter="codex") is True
    assert sessions.get("t1", "codex").reason.startswith("你要求再试一次续接")
    messages.set_display_language("ja")
    try:
        assert sessions.get("t1", "codex").reason == messages.say_in("ja", "session.retry_requested")
    finally:
        messages.set_display_language(messages.DEFAULT_LANGUAGE)


# ----------------------------------------------- F. request-time refusals, in ja


def test_a_remote_task_refusal_reads_in_japanese(ja):
    with pytest.raises(RemoteTaskRefused) as refused:
        asyncio.run(verify_task(None, "/tool", "/root", "x;id"))
    assert str(refused.value) == "タスク名が不正です：'x;id'"
    with pytest.raises(RemoteTaskRefused) as unregistered:
        asyncio.run(verify_task(None, None, "/root", "mine"))
    assert str(unregistered.value) == messages.say_in("ja", "remote.tool_dir_unregistered")
