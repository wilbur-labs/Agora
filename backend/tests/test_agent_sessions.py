"""Session continuity, live transcripts, and the honesty of both.

The event lines asserted below are **captured output**, not invented shapes: each
one was produced by running the installed CLI on 2026-09-08 (codex 0.149.1 /
claude 2.1.246 / kiro-cli 2.19.1). That matters more here than in most tests,
because the whole slice rests on parsing three vendors' event streams, and a
suite that agrees with a guessed schema proves only that the guess is
self-consistent.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from agora.execution.adapters import (
    EVENT_FORMAT_CLAUDE_STREAM_JSON,
    EVENT_FORMAT_CODEX_JSONL,
    EVENT_FORMAT_KIRO_STREAM_JSON,
    SESSION_ID_CALLER,
    SESSION_ID_OBSERVED,
    ExecutionAdapter,
    build_adapter_registry,
)
from agora.execution.models import ROLE_REVIEW, ROLE_WORKER
from agora.execution.runlog import (
    MAX_LOG_BYTES,
    TRUNCATION_NOTICE,
    RunLogError,
    RunLogStore,
)
from agora.execution.sessions import (
    MODE_RESUME,
    ROLE_UNKNOWN,
    MODE_START,
    SESSION_ACTIVE,
    SESSION_FORKED,
    SESSION_LOST,
    SESSION_NONE,
    SESSION_PENDING,
    SESSION_UNIDENTIFIED,
    SESSION_UNREADABLE,
    SESSION_UNSUPPORTED,
    AgentSessionStore,
)
from agora.execution.security import redact_text
from agora.execution.transcript import reader_for
from agora.orchestration.provider_usage import (
    RuntimeResultFormat,
    normalize_native_output,
)


# -- captured CLI output -------------------------------------------------------

CODEX_LINES = [
    '{"type":"thread.started","thread_id":"01a07c85-065e-7492-a66b-c1b2ac4c556c"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"ok"}}',
    '{"type":"item.completed","item":{"id":"item_1","type":"file_change",'
    '"changes":[{"path":"C:\\\\tmp\\\\touched.txt","kind":"add"}],"status":"completed"}}',
    '{"type":"item.completed","item":{"id":"item_2","type":"command_execution",'
    '"command":"powershell -Command \\"Get-Content .\\\\touched.txt\\"",'
    '"aggregated_output":"ok\\n","exit_code":0,"status":"completed"}}',
    '{"type":"turn.completed","usage":{"input_tokens":19592,"cached_input_tokens":0,'
    '"cache_write_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}',
]

CLAUDE_LINES = [
    '{"type":"system","subtype":"init","session_id":"95a4668f-fd36-422b-96c6-1677e494fa8b",'
    '"cwd":"C:\\\\tmp","tools":["Bash"]}',
    '{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"o"}},'
    '"session_id":"95a4668f-fd36-422b-96c6-1677e494fa8b"}',
    '{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"k"}},'
    '"session_id":"95a4668f-fd36-422b-96c6-1677e494fa8b"}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ok"}]},'
    '"session_id":"95a4668f-fd36-422b-96c6-1677e494fa8b"}',
    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}',
    '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
    '"session_id":"95a4668f-fd36-422b-96c6-1677e494fa8b","total_cost_usd":0.092598,'
    '"duration_ms":6000,"modelUsage":{"claude-opus-5":{"inputTokens":2}},'
    '"usage":{"input_tokens":2,"output_tokens":4,"cache_creation_input_tokens":8043,'
    '"cache_read_input_tokens":15790}}',
]

KIRO_LINES = [
    '{"type":"runStarted","data":{"payloadSchema":"acp","acpProtocolVersion":1,"engine":"v2"}}',
    '{"type":"metadata","data":{"sessionId":"c02cfd89-027b-473b-b363-ca1fc6eed24e",'
    '"contextUsagePercentage":3.519}}',
    '{"type":"sessionUpdate","data":{"sessionId":"c02cfd89-027b-473b-b363-ca1fc6eed24e",'
    '"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"ok"}}}}',
    '{"type":"metadata","data":{"sessionId":"c02cfd89-027b-473b-b363-ca1fc6eed24e",'
    '"contextUsagePercentage":1.676,'
    '"meteringUsage":[{"value":0.03644523847429519,"unit":"credit","unitPlural":"credits"}],'
    '"turnDurationMs":2669}}',
    '{"type":"runFinished","data":{"sessionId":"c02cfd89-027b-473b-b363-ca1fc6eed24e",'
    '"status":"success","stopReason":"end_turn","finalText":"ok","finalTextTruncated":false}}',
]

#: The v1 engine's refusal of stream-json, captured verbatim. It arrives as an
#: event rather than a crash, which is why a misconfigured engine has to surface
#: as a readable error line instead of an empty transcript.
KIRO_V1_REFUSAL = (
    '{"type":"runError","data":{"sessionId":null,"stage":"engine",'
    '"message":"--output-format stream-json is not supported on the v1 engine. '
    'Pass --agent-engine v2 (or v3)."}}'
)


def _render(event_format: str, lines: list[str]):
    reader = reader_for(event_format)
    text = "".join(reader.feed(line) for line in lines)
    return reader, text + reader.finish()


# -- adapters ------------------------------------------------------------------


def test_default_adapters_no_longer_discard_their_sessions():
    """The flags that made resume impossible are gone from every adapter."""
    registry = build_adapter_registry({})
    joined = {name: " ".join(adapter.command_template) for name, adapter in registry.items()}
    assert "--ephemeral" not in joined["codex"]
    assert "--no-session-persistence" not in joined["claude"]
    for adapter in registry.values():
        assert adapter.can_resume(), f"{adapter.name} cannot continue a conversation"


def test_resume_shape_is_not_the_start_shape():
    """codex resume rejects --sandbox, so the two templates must differ."""
    codex = build_adapter_registry({})["codex"]
    start = codex.build_command("hello")
    resumed = codex.build_command("again", session_id="sid-1", resume=True)
    assert "--sandbox" in start
    assert "--sandbox" not in resumed
    assert resumed[:4] == ["codex", "exec", "resume", "sid-1"]
    assert resumed[-1] == "again"
    # The sandbox travels as a config override instead; without it every
    # follow-up turn would run under whatever the user's config says.
    assert 'sandbox_mode="workspace-write"' in resumed


def test_claude_start_uses_a_caller_chosen_id_and_resume_swaps_the_flag():
    claude = build_adapter_registry({})["claude"]
    assert claude.session_id_source == SESSION_ID_CALLER
    start = claude.build_command("hello", session_id="uuid-1")
    assert "--session-id" in start and start[start.index("--session-id") + 1] == "uuid-1"
    assert "--verbose" in start, "stream-json exits 1 without --verbose"
    resumed = claude.build_command("again", session_id="uuid-1", resume=True)
    assert "--session-id" not in resumed
    assert resumed[resumed.index("--resume") + 1] == "uuid-1"


def test_configured_model_is_pinned_on_both_turns():
    """A pinned model reaches the first turn AND the resume.

    Absent on the resume, turn two would follow the user's own CLI default — the
    conversation would change model halfway through, which is the surprise pinning
    exists to prevent.
    """
    claude = build_adapter_registry(
        {"execution": {"adapters": {"claude": {"model": "opus"}}}}
    )["claude"]
    start = claude.build_command("hello", session_id="uuid-1")
    resumed = claude.build_command("again", session_id="uuid-1", resume=True)
    for command in (start, resumed):
        assert command[command.index("--model") + 1] == "opus"
        # Before the prompt flag, which is where the CLI takes it, and with the
        # executable still first.
        assert command[0] == "claude"
        assert command.index("--model") < command.index("-p")
    # The prompt is still exactly one element and still the one that was passed.
    assert start[start.index("-p") + 1] == "hello"
    assert resumed[resumed.index("-p") + 1] == "again"


def test_unpinned_model_leaves_the_command_untouched():
    """No `model` key means no flag — not an empty one, and not a default guess."""
    claude = build_adapter_registry({})["claude"]
    assert "--model" not in claude.build_command("hello", session_id="uuid-1")
    assert "--model" not in claude.build_command("x", session_id="uuid-1", resume=True)


def test_model_is_refused_where_no_flag_position_is_verified():
    """kiro has no declared --model position, so a configured one fails at load.

    Inserting it anyway would either fail at launch or be swallowed as kiro's
    positional prompt — and the second is silent.
    """
    with pytest.raises(ValueError, match="not supported"):
        build_adapter_registry({"execution": {"adapters": {"kiro": {"model": "opus"}}}})


def test_model_is_refused_alongside_a_command_override():
    """An override describes a different executable; its flag position is unknown."""
    with pytest.raises(ValueError, match="resume_command override"):
        build_adapter_registry({
            "execution": {"adapters": {"claude": {
                "model": "opus",
                "command": ["stub", "{prompt}"],
            }}}
        })


def test_model_is_refused_alongside_a_resume_only_override():
    """`resume_command` can be overridden on its own, and then turn two is a
    different program from turn one. Checking only `command` inserted the default
    CLI's flag position into that wrapper's argv, where it is either rejected or —
    worse — swallowed as a positional argument. (kiro review, 2026-09-21.)"""
    with pytest.raises(ValueError, match="resume_command override"):
        build_adapter_registry({
            "execution": {"adapters": {"claude": {
                "model": "opus",
                "resume_command": ["wrapper", "{session_id}", "{prompt}"],
            }}}
        })


def test_blank_model_is_refused():
    with pytest.raises(ValueError, match="non-empty string"):
        build_adapter_registry({"execution": {"adapters": {"claude": {"model": "  "}}}})


def test_kiro_needs_the_v2_engine_for_stream_json():
    kiro = build_adapter_registry({})["kiro"]
    for command in (kiro.build_command("hi"), kiro.build_command("hi", session_id="s", resume=True)):
        assert command[command.index("--agent-engine") + 1] == "v2"


def test_caller_chosen_id_is_refused_when_missing():
    claude = build_adapter_registry({})["claude"]
    with pytest.raises(ValueError):
        claude.build_command("hello")


def test_resume_is_refused_without_an_id_or_a_template():
    codex = build_adapter_registry({})["codex"]
    with pytest.raises(ValueError):
        codex.build_command("hello", resume=True)
    bare = ExecutionAdapter(name="x", command_template=("x", "{prompt}"), workspace_key="x")
    assert not bare.can_resume()
    with pytest.raises(ValueError):
        bare.build_command("hello", session_id="s", resume=True)


def test_a_stubbed_command_does_not_inherit_the_real_resume_command():
    """The second turn of a stubbed adapter must not run the real CLI.

    A config that replaces ``claude`` with ``echo`` used to inherit claude's
    resume template, so turn two would have launched the actual claude binary
    against the user's account.
    """
    registry = build_adapter_registry(
        {"execution": {"adapters": {"claude": {"command": ["echo", "{prompt}"]}}}}
    )
    stub = registry["claude"]
    assert stub.command_template == ("echo", "{prompt}")
    assert not stub.can_resume()
    assert stub.session_id_source == SESSION_ID_OBSERVED
    assert stub.resume_template == ()


def test_an_explicit_contradiction_is_refused():
    with pytest.raises(ValueError, match="session_id"):
        build_adapter_registry({
            "execution": {"adapters": {"claude": {
                "command": ["echo", "{prompt}"], "session_id_source": SESSION_ID_CALLER,
            }}}
        })


def test_a_stub_may_still_declare_a_resume_command():
    registry = build_adapter_registry({
        "execution": {"adapters": {"codex": {
            "command": ["echo", "{prompt}"],
            "resume_command": ["echo", "{session_id}", "{prompt}"],
        }}}
    })
    assert registry["codex"].build_command("p", session_id="s", resume=True) == ["echo", "s", "p"]


# -- transcript ----------------------------------------------------------------


def test_codex_transcript_shows_the_message_the_files_and_the_command():
    reader, text = _render(EVENT_FORMAT_CODEX_JSONL, CODEX_LINES)
    assert reader.session_id == "01a07c85-065e-7492-a66b-c1b2ac4c556c"
    assert "ok" in text
    assert "✎ add C:\\tmp\\touched.txt" in text
    assert "$ powershell" in text
    assert reader.unread_kinds == []


def test_a_codex_command_is_echoed_once_across_started_and_completed():
    """Codex reports a command twice; the transcript shows it once.

    Both halves have to work alone: ``item.started`` may be the only event a
    late-attached reader sees, and a fast command can be reported only as
    ``item.completed`` — which is how the fixture above caught a version of this
    reader that echoed the command on ``started`` only, and rendered output with
    no command above it.
    """
    started = (
        '{"type":"item.started","item":{"id":"item_2","type":"command_execution",'
        '"command":"pytest -q","status":"in_progress"}}'
    )
    completed = (
        '{"type":"item.started","item":{"id":"item_2","type":"command_execution",'
        '"command":"pytest -q","aggregated_output":"1 passed","exit_code":0,'
        '"status":"completed"}}'
    ).replace("item.started", "item.completed")
    _, both = _render(EVENT_FORMAT_CODEX_JSONL, [started, completed])
    assert both.count("$ pytest -q") == 1
    assert "1 passed" in both

    _, only_completed = _render(EVENT_FORMAT_CODEX_JSONL, [completed])
    assert only_completed.count("$ pytest -q") == 1


def test_a_failed_codex_command_reports_its_exit_code():
    completed = (
        '{"type":"item.completed","item":{"id":"i","type":"command_execution",'
        '"command":"false","aggregated_output":"","exit_code":1,"status":"completed"}}'
    )
    _, text = _render(EVENT_FORMAT_CODEX_JSONL, [completed])
    assert "(exit 1)" in text


def test_claude_transcript_streams_deltas_and_does_not_repeat_them():
    reader, text = _render(EVENT_FORMAT_CLAUDE_STREAM_JSON, CLAUDE_LINES)
    assert reader.session_id == "95a4668f-fd36-422b-96c6-1677e494fa8b"
    # "ok" arrived as two deltas and once more inside the assistant message. It
    # is shown once: a window that printed every answer twice would be unusable.
    assert text.count("ok") == 1
    assert reader.unread_kinds == []


def test_kiro_transcript_reads_chunks_and_the_session_id():
    reader, text = _render(EVENT_FORMAT_KIRO_STREAM_JSON, KIRO_LINES)
    assert reader.session_id == "c02cfd89-027b-473b-b363-ca1fc6eed24e"
    assert "ok" in text
    assert reader.final_text_truncated is False
    assert reader.unread_kinds == []


def test_kiro_v1_refusal_is_shown_not_swallowed():
    reader, text = _render(EVENT_FORMAT_KIRO_STREAM_JSON, [KIRO_V1_REFUSAL])
    assert "v1 engine" in text
    assert "✖" in text
    assert reader.session_id is None


def test_a_truncated_final_text_is_reported_as_truncated():
    lines = list(KIRO_LINES)
    lines[-1] = lines[-1].replace('"finalTextTruncated":false', '"finalTextTruncated":true')
    reader, text = _render(EVENT_FORMAT_KIRO_STREAM_JSON, lines)
    assert reader.final_text_truncated is True
    assert "截断" in text


@pytest.mark.parametrize(
    "event_format, line",
    [
        (EVENT_FORMAT_CODEX_JSONL, '{"type":"turn.invented","data":1}'),
        (EVENT_FORMAT_CLAUDE_STREAM_JSON, '{"type":"invented_event"}'),
        (EVENT_FORMAT_KIRO_STREAM_JSON, '{"type":"inventedEvent","data":{}}'),
    ],
)
def test_an_unknown_event_kind_is_recorded_not_dropped(event_format, line):
    """A transcript may not quietly omit what it could not read.

    This is the guard that keeps the window honest as the vendors' CLIs move: a
    new event kind shows up as an unread event, so the reader of the transcript
    knows something happened that Agora did not understand.
    """
    reader, text = _render(event_format, [line])
    assert reader.unread_kinds, "unknown event vanished"
    assert "未解读" in text


def test_a_repeated_unknown_kind_is_reported_once():
    line = '{"type":"invented_event"}'
    reader, text = _render(EVENT_FORMAT_CLAUDE_STREAM_JSON, [line] * 50)
    assert reader.unread_kinds == ["invented_event"]
    assert text.count("未解读的事件：invented_event") == 1


def test_a_non_json_line_is_shown_and_counted():
    reader, text = _render(EVENT_FORMAT_CODEX_JSONL, ["error: something went wrong"])
    assert "error: something went wrong" in text
    assert reader.malformed_lines == 1


def test_a_second_session_id_in_one_turn_is_a_conflict_not_an_overwrite():
    lines = [
        '{"type":"thread.started","thread_id":"first"}',
        '{"type":"thread.started","thread_id":"second"}',
    ]
    reader, _ = _render(EVENT_FORMAT_CODEX_JSONL, lines)
    assert reader.session_id == "first"
    assert any("conflict" in kind for kind in reader.unread_kinds)


# -- run log -------------------------------------------------------------------


def test_run_log_reads_only_what_is_new(tmp_path):
    logs = RunLogStore(tmp_path / "run-logs")
    logs.append("run-1", "› hello\n")
    first = logs.read("run-1")
    assert first.text == "› hello\n"
    assert first.reachable

    logs.append("run-1", "world\n")
    second = logs.read("run-1", first.next_offset)
    assert second.text == "world\n"
    assert second.offset == first.next_offset


def test_a_transcript_that_does_not_exist_yet_is_empty_and_reachable(tmp_path):
    logs = RunLogStore(tmp_path / "run-logs")
    slice_ = logs.read("never-written")
    assert slice_.text == ""
    assert slice_.reachable is True
    assert slice_.next_offset == 0


def test_a_transcript_that_cannot_be_read_is_reported_unreachable(tmp_path):
    """A directory where a file should be: present, and not readable as one."""
    logs = RunLogStore(tmp_path / "run-logs")
    (tmp_path / "run-logs").mkdir(parents=True)
    (tmp_path / "run-logs" / "run-2.log").mkdir()
    slice_ = logs.read("run-2")
    assert slice_.reachable is False
    assert slice_.unreachable_reason
    assert slice_.text == ""


def test_a_multibyte_character_is_never_split_across_two_reads(tmp_path):
    logs = RunLogStore(tmp_path / "run-logs")
    logs.append("run-3", "改动文件")
    # An offset inside a three-byte character, which is what a slice boundary or
    # a partly flushed write looks like.
    slice_ = logs.read("run-3", 0)
    assert slice_.text == "改动文件"
    path = logs.path_for("run-3")
    with path.open("rb") as handle:
        raw = handle.read()
    assert len(raw) == 12
    # Truncate the file mid-character and confirm the reader holds the fragment
    # back rather than emitting a replacement character in its place.
    with path.open("wb") as handle:
        handle.write(raw[:7])
    partial = logs.read("run-3", 0)
    # Seven bytes hold 改 (3) + 动 (3) + the first byte of 文. The fragment is
    # held back, so the next read starts on it and completes the character
    # instead of leaving a replacement mark behind for good.
    assert partial.text == "改动"
    assert partial.next_offset == 6


def test_a_run_id_that_could_choose_the_file_is_refused(tmp_path):
    logs = RunLogStore(tmp_path / "run-logs")
    for bad in ("../escape", "a/b", "", "."):
        with pytest.raises(RunLogError):
            logs.read(bad)


# -- sessions ------------------------------------------------------------------


@pytest.fixture()
def sessions(tmp_path):
    return AgentSessionStore(tmp_path / "control-plane.db")


def test_the_first_turn_starts_a_session(sessions, tmp_path):
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.mode == MODE_START
    assert decision.session_id is None
    assert not decision.refused
    assert sessions.get("t1", "codex").state == SESSION_NONE


def test_a_caller_chosen_id_is_minted_up_front(sessions, tmp_path):
    decision = sessions.decide(
        task_id="t1", adapter="claude", workspace=tmp_path,
        can_resume=True, caller_chooses_id=True,
    )
    assert decision.mode == MODE_START
    assert decision.session_id


def test_the_second_turn_resumes_the_recorded_session(sessions, tmp_path):
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.mode == MODE_RESUME
    assert decision.session_id == "sid-1"
    assert not decision.refused


def test_a_session_recorded_in_another_workspace_is_refused_not_restarted(sessions, tmp_path):
    """The rule adr/0007 names: never silently open a new conversation.

    All three CLIs file sessions per working directory, so a session recorded
    against one worktree is invisible from another. The turn is refused with that
    reason; the alternative — starting fresh — answers the user in an empty
    context while the window still shows the conversation they were having.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex",
        workspace=tmp_path / "worktree-a", session_id="sid-1",
    )
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path / "worktree-b",
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.refused
    assert decision.mode == MODE_RESUME
    assert "工作目录" in (decision.refusal or "")


def test_an_adapter_that_cannot_resume_is_refused_once_a_session_exists(sessions, tmp_path):
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="kiro", workspace=tmp_path, session_id="sid-1",
    )
    decision = sessions.decide(
        task_id="t1", adapter="kiro", workspace=tmp_path,
        can_resume=False, caller_chooses_id=False,
    )
    assert decision.refused


def test_a_lost_session_blocks_the_next_turn(sessions, tmp_path):
    """The refusal the module and the window both promise, now kept.

    ``decide`` used to consult only the live conditions, so a session already
    observed not to resume was dispatched again: the frontend blocked sending and
    the control plane did not, which is the claim exceeding the check.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    sessions.mark_lost(task_id="t1", adapter="codex", reason="供应商没有报出会话 id")
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.refused
    assert "供应商没有报出会话 id" in (decision.refusal or "")
    assert sessions.get("t1", "codex").session_id == "sid-1"


def test_a_condition_based_refusal_heals_itself(sessions, tmp_path):
    """A moved worktree must not become a permanent block.

    ``unreadable`` is a report, not a gate: ``decide`` re-derives the workspace
    and resume conditions every turn. Put the worktree back and the conversation
    continues — no flag to clear. This is why the provider-rejected case needed a
    state of its own rather than reusing this one.
    """
    home = tmp_path / "worktree-a"
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=home, session_id="sid-1",
    )
    elsewhere = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path / "worktree-b",
        can_resume=True, caller_chooses_id=False,
    )
    assert elsewhere.refused
    sessions.mark_unreadable(task_id="t1", adapter="codex", reason=elsewhere.refusal or "")
    assert sessions.get("t1", "codex").state == SESSION_UNREADABLE

    back_home = sessions.decide(
        task_id="t1", adapter="codex", workspace=home,
        can_resume=True, caller_chooses_id=False,
    )
    assert not back_home.refused
    assert back_home.session_id == "sid-1"


def test_a_workspace_differing_only_in_case_is_the_same_workspace(sessions, tmp_path):
    """A refusal naming two paths that differ by a drive letter's case is a dead end."""
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex",
        workspace=str(tmp_path).upper(), session_id="sid-1",
    )
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=str(tmp_path).lower(),
        can_resume=True, caller_chooses_id=False,
    )
    assert not decision.refused, decision.refusal


def test_a_successful_turn_clears_a_lost_or_unreadable_state(sessions, tmp_path):
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    sessions.mark_lost(task_id="t1", adapter="codex", reason="whatever")
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    session = sessions.get("t1", "codex")
    assert session.state == SESSION_ACTIVE
    assert session.reason is None


def test_an_unreadable_session_keeps_its_id(sessions, tmp_path):
    """Losing reachability must not become losing the conversation."""
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    sessions.mark_unreadable(task_id="t1", adapter="codex", reason="resume failed")
    session = sessions.get("t1", "codex")
    assert session.state == SESSION_UNREADABLE
    assert session.session_id == "sid-1"
    assert session.reason == "resume failed"


def test_forgetting_is_the_only_thing_that_clears_a_session(sessions, tmp_path):
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    assert sessions.forget(task_id="t1", adapter="codex") is True
    assert sessions.get("t1", "codex").state == SESSION_NONE
    assert sessions.get("t1", "codex").session_id is None
    # And it says when there was nothing to clear, rather than reporting success.
    assert sessions.forget(task_id="t1", adapter="codex") is False


def test_turns_are_counted_once_per_turn(sessions, tmp_path):
    """A resumed turn re-announces the same id; that is one turn, not two."""
    sessions.record_launch(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="sid-1", mode=MODE_START,
    )
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path, session_id="sid-1",
    )
    assert sessions.get("t1", "claude").turns == 1
    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="sid-1", mode=MODE_RESUME,
    )
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="claude", workspace=tmp_path, session_id="sid-1",
    )
    assert sessions.get("t1", "claude").turns == 2


def test_a_run_records_which_session_it_used(sessions, tmp_path):
    sessions.record_launch(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id=None, mode=MODE_START,
    )
    bound = sessions.for_run("r1")
    assert bound is not None
    assert bound.mode == MODE_START
    assert bound.confirmed is False
    # An observed-id adapter has no session to name before the provider mints
    # one, and no row is invented for it.
    assert sessions.get("t1", "codex").state == SESSION_NONE

    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-9",
    )
    assert sessions.for_run("r1").confirmed is True
    assert sessions.get("t1", "codex").session_id == "sid-9"


def test_an_unsupported_bridge_says_so_rather_than_staying_blank(sessions, tmp_path):
    """CR-39's trigger: the codex app-server bridge carries no session.

    If this assertion ever fails because the bridge grew a verified resume path,
    that is the moment to close CR-39 — not to delete the test.
    """
    sessions.mark_unsupported(
        task_id="t1", adapter="codex", workspace=tmp_path, reason="app-server 不参与会话续接",
    )
    session = sessions.get("t1", "codex")
    assert session.state == SESSION_UNSUPPORTED
    assert session.session_id is None
    assert session.reason


def test_an_unsupported_link_keeps_the_id_and_still_says_it_cannot_continue(
    sessions, tmp_path,
):
    """The id is the conversation; the state is *this link's* ability to continue.

    Keeping the state as ``active`` because an id existed — which this test used
    to require — is how switching a task's codex adapter to the app-server bridge
    went on reading "session live" while every turn ran in a fresh context. The
    id is still kept: another configuration may yet reach that conversation.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    sessions.mark_unsupported(
        task_id="t1", adapter="codex", workspace=tmp_path, reason="app-server 不续接",
    )
    session = sessions.get("t1", "codex")
    assert session.session_id == "sid-1", "the handle on the conversation must survive"
    assert session.state == SESSION_UNSUPPORTED
    assert "app-server" in (session.reason or "")


def test_an_unconfirmed_first_session_starts_over_rather_than_resuming(sessions, tmp_path):
    """A caller-chosen id nothing has acknowledged is not a session yet.

    Recorded as ``active``, a first claude process that died before announcing
    anything left the next message running ``--resume`` against an id the
    provider may never have created.
    """
    sessions.record_launch(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-uuid", mode=MODE_START, confirmed=False,
    )
    pending = sessions.get("t1", "claude")
    assert pending.state == SESSION_PENDING
    assert pending.session_id == "chosen-uuid"

    decision = sessions.decide(
        task_id="t1", adapter="claude", workspace=tmp_path,
        can_resume=True, caller_chooses_id=True,
    )
    # Starting is right *here* and nowhere else: nothing was ever established, so
    # no context is dropped — unlike a confirmed session, which is never replaced
    # without the person saying so.
    assert decision.mode == MODE_START
    assert decision.session_id and decision.session_id != "chosen-uuid"
    assert not decision.refused

    # And once the provider names it, it is a real session again.
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-uuid",
    )
    assert sessions.get("t1", "claude").state == SESSION_ACTIVE


# -- usage, on the new output formats ------------------------------------------


def test_codex_jsonl_usage_is_now_reachable():
    """The reader existed all along; the argv never passed ``--json``."""
    semantic, observation = normalize_native_output(
        adapter="codex", result_format=RuntimeResultFormat.CODEX_JSONL_V1,
        stdout="\n".join(CODEX_LINES), run_id="r1",
    )
    assert semantic == "ok"
    assert observation is not None
    assert observation.token_measurement == "exact"
    assert observation.input_tokens == 19592
    assert observation.output_tokens == 5


def test_claude_stream_json_usage_comes_from_the_closing_result_event():
    semantic, observation = normalize_native_output(
        adapter="claude", result_format=RuntimeResultFormat.CLAUDE_STREAM_JSON_V1,
        stdout="\n".join(CLAUDE_LINES), run_id="r1",
    )
    assert semantic == "ok"
    assert observation is not None
    assert observation.source == "claude_stream_json"
    assert observation.cost_usd == pytest.approx(0.092598)
    assert observation.token_measurement == "exact"


def test_a_stream_that_never_reached_its_result_event_reports_no_usage():
    """Not zero usage: the run died before saying what it spent."""
    semantic, observation = normalize_native_output(
        adapter="claude", result_format=RuntimeResultFormat.CLAUDE_STREAM_JSON_V1,
        stdout="\n".join(CLAUDE_LINES[:3]), run_id="r1",
    )
    assert observation is None
    assert semantic  # the raw stream is still the best available record


def test_kiro_acp_usage_reports_measured_credits_and_no_invented_tokens():
    semantic, observation = normalize_native_output(
        adapter="kiro", result_format=RuntimeResultFormat.KIRO_STREAM_JSON_V1,
        stdout="\n".join(KIRO_LINES), run_id="r1",
    )
    assert semantic == "ok"
    assert observation is not None
    assert observation.native_credits == pytest.approx(0.03644523847429519)
    assert observation.native_credit_measurement == "exact"
    # The text scrape it replaces estimated tokens from byte length. A real
    # credit figure is no reason to keep publishing a fake token one.
    assert observation.token_measurement == "unavailable"
    assert observation.total_tokens is None
    assert observation.duration_ms == 2669


def test_kiro_credits_are_taken_from_the_last_report_not_summed():
    """kiro repeats a running metering block; summing would multiply the cost."""
    doubled = list(KIRO_LINES)
    doubled.insert(4, doubled[3])
    _, observation = normalize_native_output(
        adapter="kiro", result_format=RuntimeResultFormat.KIRO_STREAM_JSON_V1,
        stdout="\n".join(doubled), run_id="r1",
    )
    assert observation.native_credits == pytest.approx(0.03644523847429519)


def test_a_truncated_kiro_answer_is_recorded_as_truncated():
    lines = list(KIRO_LINES)
    lines[-1] = lines[-1].replace('"finalTextTruncated":false', '"finalTextTruncated":true')
    semantic, _ = normalize_native_output(
        adapter="kiro", result_format=RuntimeResultFormat.KIRO_STREAM_JSON_V1,
        stdout="\n".join(lines), run_id="r1",
    )
    assert "截断" in semantic


def test_a_jsonl_stream_with_one_unreadable_line_yields_no_usage():
    """All-or-nothing: half-reading a stream attributes figures nobody verified."""
    lines = list(KIRO_LINES)
    lines.insert(2, "not json at all")
    _, observation = normalize_native_output(
        adapter="kiro", result_format=RuntimeResultFormat.KIRO_STREAM_JSON_V1,
        stdout="\n".join(lines), run_id="r1",
    )
    assert observation is None


def test_a_stubbed_command_does_not_inherit_the_cli_event_format():
    """A replaced executable does not emit the CLI's events.

    Inheriting the format pointed a JSON Lines reader at whatever the stub prints:
    the transcript understood none of it, and `token_capture` still reported
    "supported" for a stream nothing could parse.
    """
    registry = build_adapter_registry(
        {"execution": {"adapters": {"kiro": {"command": ["echo", "{prompt}"]}}}}
    )
    assert registry["kiro"].event_format == "plain"

    declared = build_adapter_registry(
        {"execution": {"adapters": {"kiro": {
            "command": ["wrapper", "{prompt}"], "event_format": EVENT_FORMAT_KIRO_STREAM_JSON,
        }}}}
    )
    assert declared["kiro"].event_format == EVENT_FORMAT_KIRO_STREAM_JSON


@pytest.mark.parametrize(
    "adapter, result_format, lines",
    [
        ("claude", RuntimeResultFormat.CLAUDE_STREAM_JSON_V1, CLAUDE_LINES),
        ("kiro", RuntimeResultFormat.KIRO_STREAM_JSON_V1, KIRO_LINES),
    ],
)
def test_a_truncated_tail_still_yields_usage(adapter, result_format, lines):
    """A long run keeps its figures despite starting mid-event.

    The retained output is a byte *tail*, so once a run passes the tail limit its
    first line is the back half of an event. Without being told, the reader
    refused the whole stream over that one fragment — losing the semantic text and
    the usage of exactly the runs long enough to be worth measuring.
    """
    # The back half of an event: what a byte tail's first line looks like.
    truncated = "\n".join([':15790}}', *lines])
    _, refused = normalize_native_output(
        adapter=adapter, result_format=result_format, stdout=truncated, run_id="r1",
    )
    assert refused is None, "a partial first line must not be read as a whole event"

    semantic, observation = normalize_native_output(
        adapter=adapter, result_format=result_format, stdout=truncated, run_id="r1",
        stdout_prefix_truncated=True,
    )
    assert observation is not None
    assert semantic == "ok"


def test_truncation_recovery_forgives_exactly_one_line():
    """Format-only recovery, not a licence to skip anything unparseable."""
    broken = "\n".join([KIRO_LINES[0], "not json", *KIRO_LINES[1:]])
    _, observation = normalize_native_output(
        adapter="kiro", result_format=RuntimeResultFormat.KIRO_STREAM_JSON_V1,
        stdout=broken, run_id="r1", stdout_prefix_truncated=True,
    )
    assert observation is None


def test_every_default_adapter_has_a_usage_reader():
    """No adapter silently loses usage capture to a format nothing parses."""
    from agora.execution.dispatcher import ExecutionDispatcher

    registry = build_adapter_registry({})
    for adapter in registry.values():
        assert adapter.event_format in ExecutionDispatcher._USAGE_RESULT_FORMATS


def test_token_capture_is_claimed_only_where_tokens_are_reported():
    """Having a reader and yielding tokens are two different facts.

    kiro's ACP stream reports credits and no token counts — its reader says so —
    so listing it as token-capturable made the Environments page promise a figure
    that path can never produce.
    """
    from agora.execution.dispatcher import token_capturable_adapters

    registry = build_adapter_registry({})
    assert token_capturable_adapters(registry) == {"codex", "claude"}


# -- what round two of the review found ----------------------------------------


def test_a_review_turn_does_not_resume_the_work_it_reviews(sessions, tmp_path):
    """The independent second opinion must not inherit the implementer's context.

    Keying a session by ``(task_id, adapter)`` alone did exactly that whenever the
    worker and the reviewer are the same adapter, which is an ordinary
    configuration: the automatic review resumed the implementation session. That
    is the one thing a review exists not to do (adr/0002).
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="worker-session", role=ROLE_WORKER,
    )
    review = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False, role=ROLE_REVIEW,
    )
    assert review.mode == MODE_START, "the review resumed the implementation session"
    assert review.session_id is None

    # And the two conversations stay apart afterwards.
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="review-session", role=ROLE_REVIEW,
    )
    assert sessions.get("t1", "codex", ROLE_WORKER).session_id == "worker-session"
    assert sessions.get("t1", "codex", ROLE_REVIEW).session_id == "review-session"


def test_a_legacy_session_is_kept_but_attributed_to_no_role(tmp_path):
    """A row from before the role existed may be a worker's or a review's.

    Both paths wrote through the old ``(task_id, adapter)`` key, and nothing in
    the row says which — so calling it the worker's would have resumed a review
    conversation as the user's own wherever an adapter had only ever reviewed.
    It is kept, under a role that claims nothing, and the worker slot answers
    honestly that it has none.
    """
    import sqlite3

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE agent_sessions (
                task_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT,
                workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
                turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, last_run_id TEXT,
                PRIMARY KEY (task_id, adapter)
            )
            """
        )
        db.execute(
            "INSERT INTO agent_sessions VALUES"
            " ('t1','codex','sid-1','C:/wt','active',NULL,4,'now','now','r1')"
        )
        db.commit()

    store = AgentSessionStore(db_path)
    # Nothing is lost: the id is still there, under a role that asserts nothing.
    kept = [row for row in store.list_for_task("t1") if row.session_id == "sid-1"]
    assert len(kept) == 1
    assert kept[0].role == "unknown"
    assert kept[0].turns == 4
    assert "无法判定" in (kept[0].reason or "")
    # And the worker conversation is honestly reported as not existing.
    assert store.get("t1", "codex", ROLE_WORKER).state == SESSION_NONE


def test_a_transcript_stops_at_its_size_cap_and_says_so(tmp_path):
    """Unbounded append put the control-plane disk at the mercy of a loop."""
    logs = RunLogStore(tmp_path / "run-logs")
    chunk = "x" * 100_000
    written = 0
    while written < MAX_LOG_BYTES + 200_000:
        logs.append("run-big", chunk)
        written += len(chunk)
    slice_ = logs.read("run-big")
    size = logs.path_for("run-big").stat().st_size
    assert size <= MAX_LOG_BYTES + len(TRUNCATION_NOTICE)
    # And the cut is visible rather than looking like an agent that went quiet.
    tail = logs.read("run-big", max(0, size - 4096)).text
    assert "上限" in tail
    assert slice_.reachable


def test_appending_after_the_cap_is_a_no_op(tmp_path):
    logs = RunLogStore(tmp_path / "run-logs")
    logs.append("run-x", "y" * (MAX_LOG_BYTES + 10))
    first = logs.path_for("run-x").stat().st_size
    logs.append("run-x", "more")
    assert logs.path_for("run-x").stat().st_size == first


def test_a_source_cannot_claim_another_adapters_provenance():
    """`source` is a provenance claim, so it is bound to the adapter it names."""
    from agora.protocol.models import ProviderUsageObservation

    with pytest.raises(Exception):
        ProviderUsageObservation.model_validate({
            "schema_version": "1.0", "run_id": "r1", "adapter": "kiro",
            "provider": "kiro", "source": "claude_stream_json",
            "source_payload_sha256": "0" * 64,
            "token_measurement": "unavailable", "token_method": "unavailable",
            "cost_measurement": "unavailable", "cost_method": "unavailable",
        })


# -- what round three of the review found ---------------------------------------


def test_a_secret_split_across_events_is_still_redacted(tmp_path):
    """The credential leak, and the fix that failed to fix it.

    claude and kiro stream assistant text token by token, so a key arrives as
    several rendered chunks. The first version redacted each chunk alone and said
    a secret could not be split across two writes — false for exactly those two
    adapters. The second held bytes back "so a pattern straddling the cut is
    whole on the next pass", but the cut had already been written by then.

    So: feed a secret one character at a time and read the file.
    """
    from agora.execution.dispatcher import _RedactingWriter

    logs = RunLogStore(tmp_path / "run-logs")
    writer = _RedactingWriter(logs, "run-secret")
    secret = "sk-livekey0123456789abcdef"
    for char in f"here it is: {secret} done\n":
        writer.write(char)
    writer.close()
    written = logs.read("run-secret").text
    assert secret not in written, written
    assert "[REDACTED]" in written


def test_a_secret_split_across_a_forced_flush_is_still_redacted(tmp_path):
    """The same, on a line long enough to force a flush mid-line."""
    from agora.execution.dispatcher import _RedactingWriter

    logs = RunLogStore(tmp_path / "run-logs")
    writer = _RedactingWriter(logs, "run-long")
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123"
    # Fill to just under the flush limit, then emit the secret one character at a
    # time so it is in flight across whatever boundary the writer chooses. The
    # space matters: the patterns match tokens, and a key glued to the end of a
    # word is not one — the first version of this test asserted a redaction that
    # a single write would not have made either.
    writer.write("x" * (_RedactingWriter._FLUSH_LIMIT - 10) + " ")
    for char in secret:
        writer.write(char)
    writer.write(" trailing\n")
    writer.close()
    written = logs.read("run-long").text
    assert secret not in written, written[-200:]
    assert "[REDACTED]" in written


def test_a_resume_that_lands_in_another_session_is_not_called_continuous(sessions, tmp_path):
    """A provider that forks instead of resuming must not read as continuity.

    `record_observed` overwrote the id with whatever came back, so the badge kept
    saying "session live" while the context the person relied on was gone.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-old",
    )
    sessions.mark_forked(
        task_id="t1", adapter="codex", observed="sid-new", previous="sid-old",
    )
    session = sessions.get("t1", "codex")
    assert session.state == SESSION_FORKED
    assert session.session_id == "sid-new"
    assert session.previous_session_id == "sid-old"
    assert "sid-old" in (session.reason or "")


def test_a_lost_session_is_not_downgraded_by_the_refusal_it_causes(sessions, tmp_path):
    """`lost` blocks; `unreadable` heals. Writing the second over the first let
    the very next turn through — the block this promised to hold."""
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    sessions.mark_lost(task_id="t1", adapter="codex", reason="no session announced")
    refusal = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert refusal.refused
    # The dispatcher only writes `unreadable` for a condition-based refusal, and
    # this one is not: it came from the record.
    assert refusal.condition_based is False
    assert sessions.get("t1", "codex").state == SESSION_LOST


def test_a_condition_refusal_is_marked_as_one(sessions, tmp_path):
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex",
        workspace=tmp_path / "a", session_id="sid-1",
    )
    moved = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path / "b",
        can_resume=True, caller_chooses_id=False,
    )
    assert moved.refused
    assert moved.condition_based is True


def test_a_failed_append_is_reported_as_lost_output(tmp_path):
    """A readable transcript with output missing from it says so.

    `reachable` could not carry this — the file reads fine — so the loss had no
    way to reach a reader, while the docstring claimed it did.
    """
    logs = RunLogStore(tmp_path / "run-logs")
    logs.append("run-a", "first\n")
    assert logs.read("run-a").lossy is False
    # A directory where the log file is: the append fails, the earlier text still
    # reads.
    (tmp_path / "run-logs" / "run-b.log").mkdir(parents=True)
    logs.append("run-b", "lost\n")
    slice_b = logs.read("run-b")
    assert slice_b.lossy is True


# -- what round five of the review found ----------------------------------------


def test_a_settlement_larger_than_the_tail_survives():
    """A final answer can be one JSON line bigger than the whole retained tail.

    Cut in the middle, the semantic text degrades to a JSON fragment and the
    token figures vanish — on exactly the runs whose cost is worth knowing. The
    reader keeps those lines whole while they stream past.
    """
    from agora.execution.dispatcher import _settlement_stdout
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for line in CLAUDE_LINES:
        reader.feed(line)
    assert "result" in reader.settlement

    # A tail that begins inside the closing event: no complete copy of it left.
    tail = CLAUDE_LINES[-1][5000:]
    rebuilt, prefix_truncated = _settlement_stdout(tail, True, reader)
    assert prefix_truncated is False, "retained lines are whole by construction"
    semantic, observation = normalize_native_output(
        adapter="claude", result_format=RuntimeResultFormat.CLAUDE_STREAM_JSON_V1,
        stdout=rebuilt, run_id="r1", stdout_prefix_truncated=prefix_truncated,
    )
    assert semantic == "ok"
    assert observation is not None and observation.token_measurement == "exact"


def test_a_thinking_block_reports_its_size_because_its_words_are_not_served():
    """The shape below is **measured**, not invented (claude CLI, 2026-09-22).

    `content_block_start` carries `{"type":"thinking","thinking":"","signature":""}`,
    every `thinking_delta` carries `thinking: ""` with an `estimated_tokens` count,
    and the closing `assistant` copy is empty too. The words are not on offer in
    this mode, so the transcript says how much thinking happened instead of
    printing a bare `·` and standing still (CR-104).

    The first version of this test fabricated thinking text and passed while the
    product stayed broken — which is why the fixture is now the real one.
    """
    from agora.execution.transcript import reader_for

    def event(inner):
        return json.dumps({"type": "stream_event", "event": inner})

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join([
        reader.feed(event({"type": "content_block_start", "index": 0,
                           "content_block": {"type": "thinking", "thinking": "", "signature": ""}})),
        reader.feed(event({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 64}})),
        reader.feed(event({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 391}})),
        reader.feed(event({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "signature_delta", "signature": "abc"}})),
        reader.feed(event({"type": "content_block_stop", "index": 0})),
        reader.feed(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                "content": [{"type": "thinking", "thinking": ""}]}})),
    ])
    # The cumulative estimate, not the sum of the deltas.
    assert rendered == "· 想了约 391 tokens\n", rendered
    assert "· \n" not in rendered, "the empty marker is the defect being fixed"


def test_a_thinking_block_with_no_estimate_renders_nothing_at_all():
    """Saying nothing beats saying `·`. A marker with nothing after it is what the
    window showed for an entire thinking block before CR-104."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join([
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 0}})),
    ])
    assert rendered == "", rendered


def test_thinking_words_render_if_a_future_cli_ever_streams_them():
    """The count stands in for words that are not served; it does not replace words
    that are. Every line carries the marker for `_prompt_echo`'s reason: the
    transcript is line-based, and an unmarked second line reads as the agent's own
    prose rather than as its thinking."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join([
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "一行\n二行",
                      "estimated_tokens": 12}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 0}})),
    ])
    assert rendered == "· 一行\n· 二行\n", rendered
    assert "tokens" not in rendered, "the count does not repeat what was shown"


def test_thinking_that_ends_on_a_newline_leaves_no_dangling_marker():
    """`text.replace("\\n", "\\n· ")` on "第一行\\n" would mark a line that never
    starts — the empty `·` this whole change removes (kiro review, 2026-09-22)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join([
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "第一行\n"}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 0}})),
    ])
    assert rendered == "· 第一行\n", rendered
    assert "· \n" not in rendered


def test_a_thinking_estimate_survives_a_stream_that_never_closed_the_block():
    """A killed process, a cancelled turn or a truncated pipe ends the stream
    without `content_block_stop`. What was already known is written by `finish`
    rather than discarded with the reader (kiro review, 2026-09-22)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "thinking", "thinking": ""}}}))
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 391}}}))
    assert "· 想了约 391 tokens\n" in reader.finish()


def test_thinking_served_both_ways_is_shown_once():
    """A CLI that streams the words AND repeats them in the closing copy must not
    produce two copies in the transcript (kiro review, 2026-09-22)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join([
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "检查文件"}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 0}})),
        reader.feed(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                "content": [{"type": "thinking", "thinking": "检查文件"}]}})),
    ])
    assert rendered.count("检查文件") == 1, rendered
    # Not silently, though: a copy served in both places is a shape no observed CLI
    # produces, and whether it repeats the deltas or extends them cannot be told
    # from here — so it is reported rather than passed over (kiro review).
    assert "claude thinking:streamed and repeated" in reader.unread_kinds
    # And a later message decides for itself.
    again = reader.feed(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                    "content": [{"type": "thinking", "thinking": "第二轮"}]}}))
    assert again == "· 第二轮\n", again


def test_a_closing_copy_is_matched_to_its_block_and_not_to_its_position():
    """Measured ordering: one `assistant` copy per block, each before that
    block's `content_block_stop`, each carrying its block alone —

        content_block_start 0 (thinking) → deltas → assistant [thinking]
        → content_block_stop 0 → content_block_start 1 (text) → deltas
        → assistant [text] → content_block_stop 1

    (claude CLI, 2026-09-22). So a copy always sits at position 0 while the
    stream index climbs, and reading the position as the index asked about
    block 0 for every block. Here the thinking block is index **1**: its words
    streamed, so its copy is a repeat and must not be rendered a second time.
    Keyed on position, the lookup asks about block 0, finds nothing, and prints
    the thinking twice.
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join([
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "先说一句"}}})),
        reader.feed(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "先说一句"}]}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 0}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "thinking", "thinking": ""}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 1,
            "delta": {"type": "thinking_delta", "thinking": "流过的"}}})),
        reader.feed(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                "content": [{"type": "thinking", "thinking": "流过的"}]}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 1}})),
    ])
    assert rendered.count("流过的") == 1, rendered
    assert "claude thinking:streamed and repeated" in reader.unread_kinds


def test_an_unclosed_blocks_estimate_survives_the_next_message_starting():
    """A block whose `content_block_stop` never came is written out when index 0
    starts the next message, not overwritten in silence (kiro review,
    2026-09-22). `finish()` cannot save it: by then `_thinking_blocks[0]` holds
    the *new* message's block.
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "thinking", "thinking": ""}}}))
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 391}}}))
    # No `content_block_stop` — the message was cut short. The next one begins.
    carried = reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "thinking", "thinking": ""}}}))
    assert carried == "· 想了约 391 tokens\n", carried


def test_a_copy_that_never_arrived_does_not_silence_the_next_block():
    """Matching copies to blocks *in order* breaks as soon as one copy is missing:
    the head of the queue stays behind and every later copy is matched to the
    wrong block (kiro review, 2026-09-22). Identity does not have that failure —
    here block 0's copy never arrives, and block 1's copy must still render.
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "流过的"}},
        # block 0's closing copy is lost here
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": ""}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "没流过的"}]}}))
    # Leading newline: block 0's line is still open and this is block 1's text,
    # so the line is ended rather than continued (kiro review, round 6).
    assert closing == "\n· 没流过的\n", closing
    assert "claude thinking:streamed and repeated" not in reader.unread_kinds


def test_a_copy_that_extends_the_deltas_appends_only_the_suffix():
    """The block's signature is its identity: measured on a real stream, the
    `signature_delta` accumulates to exactly the `signature` the closing copy
    carries (500 characters, byte for byte, 2026-09-22). With the block
    identified and its streamed words in hand, a copy that extends them yields
    exactly its suffix — the old code dropped the whole copy here and its own
    comment called that suffix unrecoverable (kiro review, 2026-09-22).
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        # Block 0 is text, so the thinking block is index 1 while its copy still
        # sits at position 0 — the shape that the position lookup got wrong.
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "thinking_delta", "thinking": "前半"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "signature_delta", "signature": "CAIS8AIK"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "signature_delta", "signature": "pgEIEhgC"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{
            "type": "thinking", "thinking": "前半和后半",
            "signature": "CAIS8AIKpgEIEhgC"}]}}))
    # The suffix, on the line the deltas left open — not the whole copy again.
    assert closing == "和后半\n", closing
    assert "claude thinking:streamed and repeated" not in reader.unread_kinds


def test_two_blocks_with_the_same_words_are_not_guessed_between():
    """Words are not a unique identity. Block 0 streams 检查文件 and closes; block
    1 streams nothing and its copy carries 检查文件 too. Matching on text alone
    picked the closed block, saw it had been shown, and silenced block 1's only
    copy (kiro review, 2026-09-22). Unresolvable without a signature — so it is
    rendered and the tie is reported, not guessed.
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "检查文件"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": ""}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "检查文件"}]}}))
    assert "· 检查文件\n" in closing, closing
    assert "claude thinking:copy matches two blocks" in reader.unread_kinds


def test_words_shown_by_the_copy_also_suppress_the_count():
    """A block whose words arrive only in the closing copy has been shown, and
    its `content_block_stop` must not then add a token count on top of them —
    the same "never both" rule the deltas already obey (kiro review,
    2026-09-22).
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        # The shape claude actually serves: an estimate, no words.
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 391}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "CAIS8AIK"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{
            "type": "thinking", "thinking": "这次副本里有字", "signature": "CAIS8AIK"}]}}))
    assert closing == "· 这次副本里有字\n", closing
    stop = reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_stop", "index": 0}}))
    assert "391" not in stop, stop
    assert "391" not in reader.finish()


def test_the_same_words_in_two_open_blocks_are_not_guessed_between_either():
    """The tie does not depend on the word-for-word block having been closed: a
    block whose `content_block_stop` went missing is still open, and the first
    fix only tested the closed ones (kiro review, round 3).
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "检查文件"}},
        # no content_block_stop for block 0 — it is still open
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": ""}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "检查文件"}]}}))
    assert "检查文件" in closing, closing
    assert "claude thinking:copy matches two blocks" in reader.unread_kinds


def test_every_line_a_copy_adds_carries_the_thinking_marker():
    """The transcript is read line by line, so a continuation line without `·`
    reads as the agent's own prose (kiro review, round 3)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "前半"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "CAIS8AIK"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{
            "type": "thinking", "thinking": "前半\n第二行\n第三行",
            "signature": "CAIS8AIK"}]}}))
    assert closing == "\n· 第二行\n· 第三行\n", closing


def test_a_repeat_diagnostic_does_not_continue_an_open_thinking_line():
    """`_unread` writes a line of its own, and gluing it to the end of a thinking
    line the deltas left unfinished breaks the line boundaries the transcript is
    parsed on (kiro review, round 3)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "流过的"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "流过的"}]}}))
    assert closing.startswith("\n"), closing
    assert "流过的· " not in closing, closing


def test_a_silent_block_that_already_closed_still_counts_in_the_tie():
    """The tie is about a block that streamed nothing, not about whether it is
    still open: block 1 can close before its copy arrives, and testing only the
    open blocks matched the copy to block 0 and dropped block 1's only words
    (kiro review, round 4).
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "检查文件"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_stop", "index": 1},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "检查文件"}]}}))
    assert "检查文件" in closing, closing
    assert "claude thinking:copy matches two blocks" in reader.unread_kinds


def test_a_copy_that_arrives_after_the_count_says_so():
    """Measured, the copy comes before `content_block_stop`. If it ever came
    after, the count is already on the line and the words cannot replace it — so
    the pair is reported rather than left looking intended (kiro review, round
    4)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 391}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "CAIS8AIK"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    stop = reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_stop", "index": 0}}))
    assert stop == "· 想了约 391 tokens\n", stop
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{
            "type": "thinking", "thinking": "迟到的字", "signature": "CAIS8AIK"}]}}))
    assert "· 迟到的字\n" in closing, closing
    assert "claude thinking:copy arrived after the count" in reader.unread_kinds


def test_a_copy_replayed_after_it_was_rendered_is_not_rendered_twice():
    """Rendering a copy makes its words that block's text. Leaving the text empty
    made a replay of the same event look like a suffix, and the whole thing was
    printed again (kiro review, round 4)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "thinking", "thinking": "", "signature": "CAIS8AIK"}}}))
    copy = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "这次副本里有字", "signature": "CAIS8AIK"}]}})
    assert reader.feed(copy) == "· 这次副本里有字\n"
    again = reader.feed(copy)
    assert "这次副本里有字" not in again, again
    assert "claude thinking:streamed and repeated" in reader.unread_kinds


def test_an_unsigned_copy_that_extends_the_deltas_still_appends_only_the_suffix():
    """Without a signature the words are the identity, and a copy that extends
    them is still the same block's. Matching on equality alone sent it down the
    "no block" path: the prefix was printed a second time and the open line was
    broken (kiro review, round 5)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "前半"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "thinking", "thinking": "前半和后半"}]}}))
    assert closing == "和后半\n", closing


def test_a_copy_does_not_continue_another_blocks_open_line():
    """Each block owns the line it left open. A copy belonging to a different
    block used to continue it, so two blocks ended up on one line under one
    marker and a line-by-line reader filed the text under the wrong one (kiro
    review, round 6)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": "sig-0"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "检查文件"}},
        # block 0's line stays open — no `content_block_stop`
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": "", "signature": "sig-1"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{
            "type": "thinking", "thinking": "继续处理", "signature": "sig-1"}]}}))
    assert closing == "\n· 继续处理\n", closing


def test_a_copy_naming_a_block_this_reader_never_saw_is_not_matched_by_words():
    """A signature is an answer. When it matches nothing, the copy belongs to a
    block this reader never saw start — falling through to the words matched it
    to a different block that happened to say the same thing and suppressed it
    as that block's repeat (kiro review, round 6)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": "sig-a"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "检查文件"}},
        {"type": "content_block_stop", "index": 0},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    # Block B's `content_block_start` was lost; only its copy arrives.
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{
            "type": "thinking", "thinking": "检查文件", "signature": "sig-b"}]}}))
    assert "· 检查文件\n" in closing, closing
    assert "claude thinking:streamed and repeated" not in reader.unread_kinds


def test_two_blocks_that_are_both_a_prefix_are_not_cut_at_a_guessed_place():
    """"前半" and "前半和" are both prefixes of "前半和后半", and without a
    signature nothing says which one the copy continues. Taking the first cut
    the suffix at the wrong place and said nothing about it (kiro review, round
    6)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "前半和"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "thinking_delta", "thinking": "前半"}},
        {"type": "content_block_stop", "index": 1},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "前半和后半"}]}}))
    assert "前半和后半" in closing, closing
    assert "claude thinking:copy matches two blocks" in reader.unread_kinds


def test_a_second_blocks_thinking_starts_on_its_own_line():
    """Measured path, truncated stream: block 0 loses its `content_block_stop`
    and block 1 streams. Without ending the open line the two blocks came out as
    one `· 检查文件· 继续处理`, which a line-by-line reader files as a single
    thinking record (kiro review, round 7)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join(
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
        for event in [
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "检查文件"}},
            # no `content_block_stop` for block 0
            {"type": "content_block_start", "index": 1,
             "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "thinking_delta", "thinking": "继续处理"}},
        ]
    )
    assert rendered == "· 检查文件\n· 继续处理", rendered


def test_the_reply_does_not_continue_an_unfinished_thinking_line():
    """Same rule for the agent's own text: it must not land on a `·` line left
    open by a block that never closed (kiro review, round 7)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "检查文件"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    reply = reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 1,
        "delta": {"type": "text_delta", "text": "我的回答"}}}))
    assert reply == "\n我的回答", reply


def test_elimination_counts_the_blocks_that_have_shown_nothing():
    """One block still silent, others already filled in by their own copies: the
    copy can only be that one's. Counting candidates instead of silent ones lost
    the match, and the block's `content_block_stop` then printed a token count on
    top of the words the copy had just shown (kiro review, round 7)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 12}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    # Block 0's words arrive only in its copy, and it closes.
    reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "第一段"}]}}))
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_stop", "index": 0}}))
    for event in [
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 391}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "第二段"}]}}))
    assert "· 第二段\n" in closing, closing
    stop = reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_stop", "index": 1}}))
    assert "391" not in stop, stop


def test_more_than_one_silent_block_is_reported_rather_than_picked_between():
    """Elimination names a block only when exactly one has shown nothing. With
    two, the copy is still rendered but the tie is reported — otherwise every
    candidate stays unshown and each `content_block_stop` adds a token count
    beside words already on screen (kiro review, round 8)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for index in (0, 1):
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": index,
            "content_block": {"type": "thinking", "thinking": ""}}}))
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": index,
            "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 30}}}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "谁的字"}]}}))
    assert "· 谁的字\n" in closing, closing
    assert "claude thinking:copy matches two blocks" in reader.unread_kinds


def test_a_disagreeing_copy_replayed_is_not_rendered_twice():
    """The words a copy showed become the block's text even when they disagree
    with the deltas — `_unread` deduplicates, so a replay came back with the
    content repeated and no warning at all (kiro review, round 8)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": "S"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "旧内容"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    copy = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "新内容", "signature": "S"}]}})
    assert "新内容" in reader.feed(copy)
    again = reader.feed(copy)
    assert "新内容" not in again, again


def test_the_only_block_there_is_gets_the_copy_even_with_no_signature():
    """One block, one copy: nothing else it could belong to. Left unmatched, the
    block stayed unshown and its `content_block_stop` printed a token count on
    top of the words the copy had just shown (kiro review, round 5)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 391}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "副本里的字"}]}}))
    assert closing == "· 副本里的字\n", closing
    stop = reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_stop", "index": 0}}))
    assert "391" not in stop, stop


def test_a_suffix_arriving_after_the_block_closed_keeps_its_marker():
    """`content_block_stop` ends the open line; a state that still claims the
    line is open then wrote the late suffix without the `·`, and a line-by-line
    reader takes that for the agent's own prose (kiro review, round 5)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "前半"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "CAIS8AIK"}},
        {"type": "content_block_stop", "index": 0},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{
            "type": "thinking", "thinking": "前半后半", "signature": "CAIS8AIK"}]}}))
    assert closing == "· 后半\n", closing


def test_a_new_messages_blocks_do_not_inherit_the_previous_messages_indices():
    """Indices are per message and restart at 0, and — measured — the copies
    arrive interleaved with the blocks, so there is no point in the stream that
    is reliably "the message ended". The reset therefore happens when index 0
    starts. Without it, a second message's thinking would be matched against the
    first message's record and silenced as a repeat of something else.
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    for event in [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "第一条的思考"}},
    ]:
        reader.feed(json.dumps({"type": "stream_event", "event": event}))
    # The first message's copy never arrives — the turn moved on.
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "thinking", "thinking": ""}}}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "第二条的思考"}]}}))
    assert closing == "· 第二条的思考\n", closing
    assert "claude thinking:streamed and repeated" not in reader.unread_kinds


def test_a_sibling_that_streamed_does_not_silence_a_block_that_did_not():
    """Asked once per message instead of per block, one streaming block dropped
    the closing copy of every other one (kiro review, 2026-09-22)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "thinking", "thinking": ""}}}))
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "流过的"}}}))
    reader.feed(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_stop", "index": 0}}))
    closing = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [
            {"type": "thinking", "thinking": "流过的"},
            {"type": "thinking", "thinking": "没流过的"},
        ]}}))
    # The sibling that never streamed is rendered — that is the fix. Block 0 is
    # served in both places, which is reported rather than repeated.
    assert "· 没流过的\n" in closing, closing
    # Block 0 is not rendered a second time...
    assert "· 流过的\n" not in closing, closing
    # ...and not passed over in silence either.
    assert "claude thinking:streamed and repeated" in reader.unread_kinds


def test_words_and_a_count_are_never_both_shown_for_one_block():
    """`shown` was briefly cleared to re-open the marker after a trailing newline,
    which also told the close that nothing had been rendered — so the block printed
    its words *and* a token count (kiro review, 2026-09-22)."""
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = "".join([
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "第一行\n",
                      "estimated_tokens": 12}}})),
        reader.feed(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 0}})),
    ])
    assert rendered == "· 第一行\n", rendered
    assert "tokens" not in rendered


def test_a_persons_prompt_is_echoed_whole_however_long_it_is():
    """CR-99's rule: what the person typed goes into the window as they typed it.

    Length is explicitly not the test. Truncating a long instruction someone
    actually wrote is the "the system decided what you may see" behaviour CR-99
    removed, and it would come straight back if this were a size limit.
    """
    from agora.execution.dispatcher import _prompt_echo

    long_prompt = "\n".join(f"第 {n} 行的要求" for n in range(200))
    echoed = _prompt_echo(long_prompt, False)
    assert echoed.count("\n") == 200
    for line in echoed.splitlines():
        assert line.startswith("› "), line
    assert "第 199 行的要求" in echoed


def test_a_machine_written_review_prompt_is_summarised_not_reprinted():
    """CR-106: the review prompt embeds whole changed files, and echoing it line
    by line put alembic.ini, env.py and the new tests into the chat window — code
    the person had just watched the worker write."""
    from agora.execution.dispatcher import _prompt_echo

    prompt = (
        "You are an independent code reviewer.\n"
        "## FILE: backend/alembic.ini (added)\n"
        "[alembic]\nscript_location = %(here)s/alembic\n"
    )
    echoed = _prompt_echo(prompt, True, machine_written=True)
    assert echoed.count("\n") == 1, "one line, not the diff"
    assert echoed.startswith("›› ")
    assert "alembic" not in echoed and "script_location" not in echoed
    assert str(len(prompt.strip())) in echoed, "says how much it stood in for"


def test_thinking_that_never_streamed_is_still_rendered():
    """The closing copy is only skipped when it is empty.

    A non-empty one means the deltas did not carry it — dropping it on the
    assumption that they always do would lose the text outright.
    """
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = reader.feed(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "没走 delta"}]}}))
    assert rendered == "· 没走 delta\n", rendered


def test_an_untruncated_tail_is_still_preferred():
    """Rebuilding when nothing was dropped would throw away context."""
    from agora.execution.dispatcher import _settlement_stdout
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_KIRO_STREAM_JSON)
    for line in KIRO_LINES:
        reader.feed(line)
    whole = "\n".join(KIRO_LINES)
    assert _settlement_stdout(whole, False, reader) == (whole, False)


def test_kiro_keeps_its_metering_line_for_a_truncated_tail():
    from agora.execution.dispatcher import _settlement_stdout
    from agora.execution.transcript import reader_for

    reader = reader_for(EVENT_FORMAT_KIRO_STREAM_JSON)
    for line in KIRO_LINES:
        reader.feed(line)
    rebuilt, _ = _settlement_stdout("garbage", True, reader)
    _, observation = normalize_native_output(
        adapter="kiro", result_format=RuntimeResultFormat.KIRO_STREAM_JSON_V1,
        stdout=rebuilt, run_id="r1",
    )
    assert observation is not None
    assert observation.native_credits is not None


def test_a_run_summary_carries_the_identity_its_placement_needs():
    """A summary without the role reads as a worker run, so every review became
    a top-level turn of its own."""
    from agora.execution.models import RunSummary

    assert RunSummary.model_fields["role"].default == ROLE_WORKER
    assert "reviews_run_id" in RunSummary.model_fields


# -- what round six of the review found -----------------------------------------


def test_an_interrupted_session_migration_is_finished_not_abandoned(tmp_path):
    """The rebuild is two commits in sqlite; stopping between them lost the lot.

    A process that died after the rename left the real session ids in
    ``agent_sessions_pre_role`` and no ``agent_sessions`` — and the next startup,
    seeing none, created an empty one and never looked at the other again. Every
    conversation on that installation would have silently started over.
    """
    import sqlite3

    db_path = tmp_path / "interrupted.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE agent_sessions_pre_role ("
            " task_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT,"
            " workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,"
            " turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,"
            " last_seen_at TEXT NOT NULL, last_run_id TEXT,"
            " PRIMARY KEY (task_id, adapter))"
        )
        db.execute(
            "INSERT INTO agent_sessions_pre_role VALUES"
            " ('t1','codex','sid-survivor','C:/wt','active',NULL,7,'now','now','r1')"
        )
        db.commit()

    store = AgentSessionStore(db_path)
    survived = [row for row in store.list_for_task("t1") if row.session_id == "sid-survivor"]
    assert survived, "the interrupted migration dropped a session"
    assert survived[0].turns == 7
    assert survived[0].role == "unknown"
    with sqlite3.connect(db_path) as db:
        leftover = db.execute(
            "SELECT name FROM sqlite_master WHERE name = 'agent_sessions_pre_role'"
        ).fetchall()
    assert leftover == [], "the half-migrated table must not be left behind"


def test_a_half_finished_migration_does_not_overwrite_migrated_rows(tmp_path):
    """The copy ran and the drop did not: already-migrated rows win."""
    import sqlite3

    db_path = tmp_path / "half.db"
    store = AgentSessionStore(db_path)
    store.record_observed(
        run_id="r2", task_id="t1", adapter="codex", workspace="C:/new", session_id="sid-new",
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE agent_sessions_pre_role ("
            " task_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT,"
            " workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,"
            " turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,"
            " last_seen_at TEXT NOT NULL, last_run_id TEXT,"
            " PRIMARY KEY (task_id, adapter))"
        )
        db.execute(
            "INSERT INTO agent_sessions_pre_role VALUES"
            " ('t1','codex','sid-stale','C:/old','active',NULL,1,'then','then','r1')"
        )
        db.commit()

    AgentSessionStore(db_path)
    # The already-migrated worker row wins; the legacy row lands beside it under
    # the role that claims nothing, rather than over it.
    assert store.get("t1", "codex").session_id == "sid-new"
    stale = [row for row in store.list_for_task("t1") if row.session_id == "sid-stale"]
    assert stale and stale[0].role == "unknown"


def test_a_fork_reaches_the_run_record_too(sessions, tmp_path):
    """Two records of one fact must not disagree about which session ran."""
    sessions.record_launch(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-old", mode=MODE_RESUME, confirmed=True,
    )
    sessions.mark_forked(
        task_id="t1", adapter="codex", observed="sid-new", previous="sid-old", run_id="r1",
    )
    bound = sessions.for_run("r1")
    assert bound is not None
    assert bound.session_id == "sid-new", "the audit row named a session the turn did not use"
    assert bound.confirmed is True


def test_lost_output_survives_a_restart(tmp_path):
    """The gap outlives the process that saw it.

    Held only in memory, a restart re-reported a mutilated log as complete and
    readable — the very confusion the flag was added to remove.
    """
    root = tmp_path / "run-logs"
    logs = RunLogStore(root)
    (root / "run-b.log").mkdir(parents=True)
    logs.append("run-b", "lost\n")
    assert logs.read("run-b").lossy is True

    # A fresh store is a fresh process: the in-memory set is empty.
    assert RunLogStore(root).read("run-b").lossy is True


def test_a_failed_turn_says_why_in_the_conversation():
    """`_failure_note` carries both halves; neither is enough alone."""
    from agora.execution.dispatcher import _failure_note

    note = _failure_note("process exited with a non-zero status", "error: unknown flag --nope")
    assert "process exited" in note
    assert "--nope" in note
    assert _failure_note(None, "") == ""


# -- what round seven of the review found ---------------------------------------


def test_stopping_a_slow_turn_does_not_condemn_its_session(sessions, tmp_path):
    """Clicking Stop is not evidence that a session could not be resumed.

    Marked `lost` there, every later message was refused and the only way out was
    "start a new session" — which throws away the context the person was trying
    to keep. On the exact path they use: a CLI slow to print its first event.
    """
    from agora.execution.dispatcher import ExecutionDispatcher
    from agora.execution.transcript import reader_for

    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    reader = reader_for(EVENT_FORMAT_CODEX_JSONL)  # nothing fed: no session announced

    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.sessions = sessions
    ExecutionDispatcher._settle_session(
        dispatcher, run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        transcript=reader, resuming=True, exit_code=1, role=ROLE_WORKER,
        requested="sid-1", killed=True,
    )
    assert sessions.get("t1", "codex").state == SESSION_ACTIVE, "a stop condemned the session"

    # A process that ended on its own, though, is evidence — and still says so.
    ExecutionDispatcher._settle_session(
        dispatcher, run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        transcript=reader, resuming=True, exit_code=1, role=ROLE_WORKER,
        requested="sid-1", killed=False,
    )
    assert sessions.get("t1", "codex").state == SESSION_LOST


def test_the_line_buffer_has_a_ceiling():
    """A CLI that never emits a newline must not grow the buffer without bound."""
    from agora.execution.dispatcher import _MAX_PENDING_LINE

    assert _MAX_PENDING_LINE <= 8 * 1024 * 1024


def test_a_full_record_and_its_summary_agree_about_identity():
    """Two readings of one row must not contradict each other."""
    from agora.execution.models import ExecutionRun, RunSummary

    for model in (ExecutionRun, RunSummary):
        assert model.model_fields["role"].default == ROLE_WORKER
        assert "reviews_run_id" in model.model_fields


# -- what round eight of the review found ---------------------------------------


def test_clearing_a_session_is_not_undone_by_a_run_already_reading(sessions, tmp_path):
    """The clear has to outlast a turn that reports its session moments later.

    Deleting the row left nothing to say a clear had happened, so the draining
    process's next session event re-created the conversation the person had just
    asked to leave — the API said "none" and the next message went straight back
    into the old context.
    """
    sessions.record_launch(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id=None, mode=MODE_START,
    )
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-old",
    )
    assert sessions.get("t1", "codex").session_id == "sid-old"

    assert sessions.forget(task_id="t1", adapter="codex") is True
    assert sessions.get("t1", "codex").session_id is None

    # r1 was bound before the clear, so its late observation is ignored.
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-old",
    )
    assert sessions.get("t1", "codex").session_id is None, "the clear was undone"


def test_a_turn_started_after_a_clear_still_records_its_session(sessions, tmp_path):
    """The tombstone must not swallow the *next* conversation."""
    sessions.forget(task_id="t1", adapter="codex")
    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id=None, mode=MODE_START,
    )
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-new",
    )
    assert sessions.get("t1", "codex").session_id == "sid-new"


def test_a_turn_whose_session_could_not_be_named_says_so(sessions, tmp_path):
    """Recording nothing made the next message read as a clean first turn.

    Which is the silence adr/0007 forbids, reachable through nothing more exotic
    than a CLI changing the shape of its session event.
    """
    sessions.mark_unidentified(
        task_id="t1", adapter="codex", workspace=tmp_path,
        reason="输出里读不到会话 id",
    )
    session = sessions.get("t1", "codex")
    assert session.state == SESSION_UNIDENTIFIED
    assert session.session_id is None
    assert "读不到" in (session.reason or "")
    # There is no id, so the next turn can only start — but it is not pretending
    # this is the first one.
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.mode == MODE_START
    assert not decision.refused


def test_an_unidentified_marker_never_overwrites_a_named_session(sessions, tmp_path):
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-1",
    )
    sessions.mark_unidentified(
        task_id="t1", adapter="codex", workspace=tmp_path, reason="whatever",
    )
    session = sessions.get("t1", "codex")
    assert session.session_id == "sid-1"
    assert session.state == SESSION_ACTIVE


# -- what round nine of the review found ----------------------------------------


def test_two_sessions_in_one_turn_fail_closed(sessions, tmp_path):
    """A note in the transcript was not enough.

    If a CLI names session A and then switches to B, the work is in B while the
    record points at A — so resuming either one later may carry on without the
    turn that just happened, and redo or overwrite the files it wrote. The reader
    already noticed; now the session record stops the next resume.
    """
    from agora.execution.dispatcher import ExecutionDispatcher

    reader = reader_for(EVENT_FORMAT_CODEX_JSONL)
    reader.feed('{"type":"thread.started","thread_id":"first"}')
    reader.feed('{"type":"thread.started","thread_id":"second"}')
    assert reader.session_conflict == ("first", "second")

    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="first",
    )
    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.sessions = sessions
    ExecutionDispatcher._settle_session(
        dispatcher, run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        transcript=reader, resuming=False, exit_code=0, role=ROLE_WORKER,
        requested=None, killed=False,
    )
    session = sessions.get("t1", "codex")
    assert session.state == SESSION_LOST
    assert "两个不同的会话 id" in (session.reason or "")

    refusal = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert refusal.refused, "a conflicting turn must not be resumed"


def test_a_transcript_already_at_the_cap_reports_the_loss(tmp_path):
    """Returning silently made a truncated transcript look like a finished one."""
    logs = RunLogStore(tmp_path / "run-logs")
    # The append that fills the file also discards its own tail, so it is lossy
    # from that moment — the notice in the text is not a substitute for the flag,
    # which is what a caller reading `lossy` alone relies on.
    logs.append("run-full", "z" * (MAX_LOG_BYTES + 10))
    assert logs.read("run-full").lossy is True
    logs.append("run-full", "dropped\n")
    assert logs.read("run-full").lossy is True
    # And it survives the process that saw it.
    assert RunLogStore(tmp_path / "run-logs").read("run-full").lossy is True


# -- what round ten of the review found -----------------------------------------


def test_an_unattributable_legacy_session_blocks_instead_of_vanishing(tmp_path):
    """Both of my earlier answers were wrong, in opposite directions.

    Calling a legacy row the worker's resumed a review conversation as the user's
    own (round 8). Filing it under an unknown role made the worker slot answer
    "none" — so the next message started a new conversation with nobody deciding
    to, and the agent could redo or overwrite work the old session had finished
    (round 10). The third answer asserts nothing and starts nothing: it refuses,
    and hands the choice to the person, which is what rule 3 says.
    """
    import sqlite3

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE agent_sessions ("
            " task_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT,"
            " workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,"
            " turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,"
            " last_seen_at TEXT NOT NULL, last_run_id TEXT,"
            " PRIMARY KEY (task_id, adapter))"
        )
        db.execute(
            "INSERT INTO agent_sessions VALUES"
            " ('t1','codex','sid-legacy','C:/wt','active',NULL,3,'now','now','r1')"
        )
        db.commit()

    store = AgentSessionStore(db_path)
    decision = store.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.refused, "an unattributable session must not be stepped over"
    assert "sid-legacy" in (decision.refusal or "")
    assert "无法判定" in (decision.refusal or "")

    # Clearing it explicitly is the way out, and then work proceeds.
    assert store.forget(task_id="t1", adapter="codex", role="unknown") is True
    assert not store.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    ).refused


def test_a_clear_between_deciding_and_launching_is_not_undone(sessions, tmp_path):
    """The race moved rather than closed, the first time.

    Guarding `record_observed` alone left the window between `decide` and
    `record_launch`: a turn that had already read the old id wrote it straight
    back as active, after the API had told the person the session was gone.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path, session_id="sid-old",
    )
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.session_id == "sid-old"

    # The person clears it while that turn is starting.
    sessions.forget(task_id="t1", adapter="codex")

    # The launch, decided under the old generation, must not resurrect it.
    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id=decision.session_id, mode=decision.mode, confirmed=True,
        expected_clear_seq=decision.clear_seq,
    )
    assert sessions.get("t1", "codex").session_id is None, "the clear was undone by the launch"


def test_captured_lines_are_valid_json():
    """Guards the fixtures themselves: a typo here would weaken every test above."""
    for line in CODEX_LINES + CLAUDE_LINES + KIRO_LINES + [KIRO_V1_REFUSAL]:
        assert isinstance(json.loads(line), dict)


def _fake_run(*, task_id: str, adapter: str, run_id: str):
    """The three fields ``_render_line`` reads off a run, and nothing else."""
    from types import SimpleNamespace

    return SimpleNamespace(run_id=run_id, task_id=task_id, adapter=adapter)


class _CollectingWriter:
    """Stands in for ``_RedactingWriter``: keeps what was rendered."""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> None:
        self.chunks.append(text)


# -- round eleven --------------------------------------------------------------


def test_a_conflict_is_marked_while_streaming_not_only_at_settlement(sessions, tmp_path):
    """Two session ids in one turn must fail closed the moment they are seen.

    The settlement-only version persisted the first id as ``active`` while the
    turn was still streaming, so a crash before settlement left the record
    pointing at a session that does not hold this turn's work — and the next
    resume could redo it, or overwrite the files the second session wrote.
    """
    from agora.execution.dispatcher import ExecutionDispatcher, _ReaderGate
    from agora.execution.transcript import CodexJsonlReader

    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.sessions = sessions
    dispatcher._session_noted = {}
    run = _fake_run(task_id="t1", adapter="codex", run_id="r1")
    reader = CodexJsonlReader()
    writer = _CollectingWriter()

    # The real event, twice, with different ids — the captured shape, so the
    # test cannot pass against a spelling codex does not emit.
    for line in (
        b'{"type":"thread.started","thread_id":"first"}',
        b'{"type":"thread.started","thread_id":"second"}',
    ):
        dispatcher._render_line(
            run, tmp_path, reader, ROLE_WORKER, None, line, writer, _ReaderGate(),
        )

    recorded = sessions.get("t1", "codex", ROLE_WORKER)
    assert recorded.state == SESSION_LOST
    assert "两个不同的会话 id" in (recorded.reason or "")


def test_the_unattributable_guard_covers_the_review_role_too(sessions, tmp_path):
    """A legacy row blocks every role, because it may belong to any of them.

    Firing only for the worker let a review turn step over it and start a fresh
    review — dropping the earlier review context silently, which is the same
    rule broken from the other side.
    """
    sessions.record_observed(
        run_id="r0", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="legacy-1", role=ROLE_UNKNOWN,
    )
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False, role=ROLE_REVIEW,
    )
    assert decision.refused
    assert "legacy-1" in (decision.refusal or "")


def test_a_new_id_starts_its_turn_count_at_one(sessions, tmp_path):
    """A different session is a different conversation, counted from one.

    Adding to the previous row's total made the new session's first successful
    turn read as its second — and every early exit before it inflated the number
    further.
    """
    sessions.record_launch(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-1", mode=MODE_START, confirmed=False, expected_clear_seq=0,
    )
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-1", role=ROLE_WORKER,
    )
    assert sessions.get("t1", "claude", ROLE_WORKER).turns == 1

    # A second, different id under the same key: a new conversation.
    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-2", mode=MODE_START, confirmed=False, expected_clear_seq=0,
    )
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-2", role=ROLE_WORKER,
    )
    row = sessions.get("t1", "claude", ROLE_WORKER)
    assert row.session_id == "chosen-2"
    assert row.turns == 1

    # And continuing the same id still counts up — from the launch, which is
    # where a turn against a known session is counted.
    sessions.record_launch(
        run_id="r3", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-2", mode=MODE_RESUME, confirmed=True, expected_clear_seq=0,
    )
    sessions.record_observed(
        run_id="r3", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-2", role=ROLE_WORKER,
    )
    assert sessions.get("t1", "claude", ROLE_WORKER).turns == 2


# -- round twelve --------------------------------------------------------------


def test_a_clear_between_decide_and_launch_is_not_undone_by_the_drain(sessions, tmp_path):
    """A run is bound to the generation ``decide`` saw, not to the current one.

    The launch recorded the run's clear generation by reading the table at that
    moment. On the commonest path that made the compare-and-set pointless: an
    observed-id adapter (codex, kiro) passes ``session_id=None`` and returns
    before the guarded upsert, so a clear landing between ``decide`` and the
    launch was stamped onto the run as though the run had been decided after it.
    The drain then wrote the cleared session back as ``active`` — after the API
    had already told the person it was gone.
    """
    # A live session, as a first turn would leave it.
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    # Turn two is decided while the session is still there.
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.mode == MODE_RESUME

    # The person clears it before the process reports anything.
    assert sessions.forget(task_id="t1", adapter="codex")

    # The launch is written with the generation the decision was taken under.
    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id=None, mode=decision.mode, expected_clear_seq=decision.clear_seq,
    )
    # And the drain reports the old session id, as a live process would.
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )

    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.session_id is None, "a cleared session must not be resurrected by a drain"
    assert row.state == SESSION_NONE


def test_a_run_launched_after_a_clear_still_records_its_session(sessions, tmp_path):
    """The guard above must not swallow the *next* turn's own session.

    A generation check that rejected everything would be safe and useless: the
    conversation could never start again after a clear.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    sessions.forget(task_id="t1", adapter="codex")

    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.mode == MODE_START
    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id=None, mode=decision.mode, expected_clear_seq=decision.clear_seq,
    )
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-2", role=ROLE_WORKER,
    )
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.session_id == "sid-2"
    assert row.state == SESSION_ACTIVE


def test_a_drain_finishing_mid_request_does_not_complete_the_turn(tmp_path):
    """``is_draining`` is asked before the log is read, and it has to stay that way.

    A cancel marks the row terminal and drains stdout afterwards. Asking after
    the read let a drain that finished in between append its tail and leave
    ``_active``, so a read taken *before* that tail existed came back saying the
    turn was complete — the window stopped polling with the last of the output
    still on disk.
    """
    from agora.execution.models import RunState
    from agora.execution.router import run_output

    logs = RunLogStore(tmp_path / "logs")
    logs.append("r1", "hello\n")

    class _Store:
        def require(self, run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                run_id=run_id, state=RunState.CANCELLED, task_id="t1", adapter="codex",
                prompt="", error=None, stdout_tail="", stderr_tail="",
            )

    class _Dispatcher:
        """A drain that finishes between the two calls the endpoint makes."""

        def __init__(self) -> None:
            self.run_logs = logs
            self.asked = 0

        def is_draining(self, run_id: str) -> bool:
            self.asked += 1
            return self.asked == 1

    dispatcher = _Dispatcher()
    sliced = run_output("r1", 0, _Store(), dispatcher)
    assert sliced.text == "hello\n"
    assert sliced.complete is False, (
        "a read taken while the drain was still running must not end the turn"
    )
    assert dispatcher.asked == 1, "the draining question is asked once, before the read"


# -- round thirteen ------------------------------------------------------------


def test_a_fork_from_a_draining_run_cannot_resurrect_a_cleared_session(sessions, tmp_path):
    """`mark_forked` writes a session id, so it honours the clear generation too.

    `record_observed` has honoured it since the counter existed; this path did
    not. A fork event arriving from a process that was still draining therefore
    put the abandoned conversation back on record, and the next turn resumed
    exactly what the person had asked to drop.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id=None, mode=decision.mode, expected_clear_seq=decision.clear_seq,
    )
    assert sessions.forget(task_id="t1", adapter="codex")

    sessions.mark_forked(
        task_id="t1", adapter="codex", role=ROLE_WORKER,
        observed="sid-2", previous="sid-1", run_id="r2",
    )
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.session_id is None, "a cleared session must not come back through a fork"
    assert row.state == SESSION_NONE


def test_a_fork_starts_the_new_session_turn_count_at_one(sessions, tmp_path):
    """The forked-to session is a different conversation, counted from one.

    Carrying the old total over displayed a two-turn session as its seventh.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    for run_id in ("r2", "r3", "r4"):
        sessions.record_launch(
            run_id=run_id, task_id="t1", adapter="codex", workspace=tmp_path,
            session_id="sid-1", mode=MODE_RESUME, confirmed=True, expected_clear_seq=0,
        )
        sessions.record_observed(
            run_id=run_id, task_id="t1", adapter="codex", workspace=tmp_path,
            session_id="sid-1", role=ROLE_WORKER,
        )
    assert sessions.get("t1", "codex", ROLE_WORKER).turns == 4

    sessions.record_launch(
        run_id="r5", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", mode=MODE_RESUME, confirmed=True, expected_clear_seq=0,
    )
    sessions.mark_forked(
        task_id="t1", adapter="codex", role=ROLE_WORKER,
        observed="sid-9", previous="sid-1", run_id="r5",
    )
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.session_id == "sid-9"
    assert row.state == SESSION_FORKED
    assert row.turns == 1


def test_a_long_unbroken_line_does_not_swallow_the_events_behind_it(sessions, tmp_path):
    """The ceiling flush runs *after* whole lines are drained, not before.

    A tool that writes megabytes without a newline, followed in the same read by
    a newline and the events that carry the session id, used to be flushed whole
    as one unparseable fragment — losing those events silently, which made the
    next turn look like a new conversation.
    """
    import asyncio

    from agora.execution.dispatcher import (
        ExecutionDispatcher,
        _MAX_PENDING_LINE,
        _ReaderGate,
    )
    from agora.execution.transcript import CodexJsonlReader

    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.sessions = sessions
    dispatcher._session_noted = {}
    dispatcher.run_logs = RunLogStore(tmp_path / "logs")
    run = _fake_run(task_id="t1", adapter="codex", run_id="r1")

    blob = b"x" * (_MAX_PENDING_LINE + 4096)
    payload = blob + b'\n{"type":"thread.started","thread_id":"sid-behind-the-blob"}\n'

    class _OneShotStream:
        def __init__(self, data: bytes) -> None:
            self._data = data

        async def read(self, size: int) -> bytes:
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk

    transcript = CodexJsonlReader()
    asyncio.run(
        dispatcher._read_streaming(
            _OneShotStream(payload), run, tmp_path, transcript, ROLE_WORKER, None,
            _ReaderGate(),
        )
    )
    assert transcript.session_id == "sid-behind-the-blob", (
        "an event behind an over-long line must still be parsed"
    )
    assert sessions.get("t1", "codex", ROLE_WORKER).session_id == "sid-behind-the-blob"


def test_draining_is_answered_from_the_live_set_not_from_a_page_of_history(tmp_path):
    """The clear guard asks which processes are being read, and never pages runs.

    It used to list the task's newest 200 runs and test each one. That cap fails
    toward *allowing* a clear: an older cancelled-but-still-draining run is
    pushed off the page by newer runs, the clear is accepted, and that process's
    next session event writes the abandoned id straight back. Answering from the
    dispatcher's own active set has nothing to cap — and this test fails if the
    paging ever comes back, because listing raises.
    """
    from types import SimpleNamespace

    from agora.execution.dispatcher import ExecutionDispatcher

    class _Store:
        def require(self, run_id):
            return {
                "old-draining": SimpleNamespace(run_id=run_id, task_id="t1", adapter="codex"),
                "other-adapter": SimpleNamespace(run_id=run_id, task_id="t1", adapter="claude"),
                "other-task": SimpleNamespace(run_id=run_id, task_id="t2", adapter="codex"),
            }[run_id]

        def list(self, *args, **kwargs):  # pragma: no cover - must never be called
            raise AssertionError("draining must not be answered by paging run history")

    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.store = _Store()
    dispatcher._active = {"other-task": object(), "other-adapter": object()}
    assert dispatcher.draining_for("t1", "codex") is False

    # The one that matters is old enough that a page of recent runs would miss it.
    dispatcher._active["old-draining"] = object()
    assert dispatcher.draining_for("t1", "codex") is True


# -- round fourteen ------------------------------------------------------------


def test_a_credential_split_by_a_forced_flush_is_not_written_in_clear(tmp_path):
    """The continuation of a value cut by a forced flush is dropped, not emitted.

    A long line is flushed before any newline arrives, and only the tail is
    retained. When the cut lands inside a ``key=value`` value, redaction has
    already collapsed the visible half to a marker — and the characters that
    arrive next carry no trigger, so they used to be written in clear right after
    that marker.
    """
    from agora.execution.dispatcher import _RedactingWriter

    logs = RunLogStore(tmp_path / "logs")
    writer = _RedactingWriter(logs, "r1")

    secret = "S3cr3t" * 400  # 2400 characters, far longer than what is retained
    # No newline anywhere: this is the forced-flush path.
    writer.write("noise " * 2000 + "api_key=" + secret[:1200])
    writer.write(secret[1200:] + " trailing\n")
    writer.close()

    written = logs.read("r1", 0).text
    assert "[REDACTED]" in written
    for probe in (secret[:64], secret[1200:1264], secret[-64:]):
        assert probe not in written, "no part of the credential may reach the log"
    # And the output after the value's terminator is kept: the guard covers the
    # value, not everything that follows it.
    assert "trailing" in written


def test_clearing_a_session_that_has_no_id_still_resets_its_state(sessions, tmp_path):
    """`unsupported` and `unidentified` rows carry a state with no session id.

    The clear's fallback path only bumped the generation counter, so the badge
    kept saying "this link does not continue sessions" and the button it offered
    did nothing however many times it was pressed.
    """
    sessions.mark_unsupported(
        task_id="t1", adapter="codex", workspace=tmp_path,
        reason="app-server 桥不带会话",
    )
    assert sessions.get("t1", "codex", ROLE_WORKER).state == SESSION_UNSUPPORTED

    # False: there was no id to drop. The state still has to be reset.
    assert sessions.forget(task_id="t1", adapter="codex") is False
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.state == SESSION_NONE
    assert row.reason is None
    assert row.session_id is None


def test_a_clear_is_refused_inside_its_own_transaction_when_a_run_is_live(tmp_path):
    """The check and the clear are one statement, not two.

    A run created between the caller's check and this write was already carrying
    the old session id, so the response said "none" while that turn went on to
    continue the abandoned conversation. A run row exists before its process
    starts, so the test happens inside the clearing transaction.
    """
    import sqlite3

    from agora.execution.sessions import SessionClearRefused

    db_path = tmp_path / "control-plane.db"
    store = AgentSessionStore(db_path)
    store.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    # The shape the execution store keeps, with one run of this adapter queued.
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS execution_runs"
            " (run_id TEXT PRIMARY KEY, task_id TEXT, adapter TEXT, state TEXT)"
        )
        db.execute(
            "INSERT INTO execution_runs VALUES ('r2', 't1', 'codex', 'queued')"
        )
        db.commit()

    with pytest.raises(SessionClearRefused):
        store.forget(task_id="t1", adapter="codex", blocking_run_states=["queued", "running"])
    # Refused means nothing changed: the id is still there for that turn to use.
    assert store.get("t1", "codex", ROLE_WORKER).session_id == "sid-1"

    # And once the run has settled, the same call clears.
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE execution_runs SET state = 'succeeded' WHERE run_id = 'r2'")
        db.commit()
    assert store.forget(
        task_id="t1", adapter="codex", blocking_run_states=["queued", "running"]
    ) is True
    assert store.get("t1", "codex", ROLE_WORKER).session_id is None


@pytest.mark.skip(
    reason=(
        "CR-46: a stop takes the whole process tree on Windows — a job object "
        "assigned at spawn, measured against a background process whose parent "
        "had already exited. **POSIX is what remains.** There the signal reaches "
        "the CLI and not the shell or copy it started, so an orphan keeps "
        "writing the worktree after Agora has snapshotted the deliverables and "
        "released the lock, and the next run enters a directory a live process "
        "is still changing. Closing it needs the children in their own process "
        "group, which changes signal delivery for every run and cannot be "
        "measured from this machine. This skip is the artifact that keeps saying "
        "so."
    )
)
def test_a_stop_takes_the_whole_process_tree_in_every_case():  # pragma: no cover - CR-46
    raise AssertionError("unimplemented: see CR-46")


@pytest.mark.skipif(os.name != "nt", reason="the tree kill is the Windows path; POSIX is CR-46")
def test_a_stop_reaches_the_grandchild_on_windows(tmp_path):
    """Measured, not asserted from the argv: the grandchild is actually gone.

    An agent's own children hold the worktree — a shell, a copy, a build.
    Signalling only the CLI left them running while Agora had already snapshotted
    the deliverables and released the workspace lock, so the next run entered a
    worktree a live process was still writing to.
    """
    import asyncio
    import subprocess
    import sys

    from agora.execution.dispatcher import ExecutionDispatcher

    # A parent that starts a long-lived grandchild, prints its pid, and waits.
    program = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        "print(child.pid, flush=True); "
        "time.sleep(120)"
    )

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", program,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
        grandchild = int(line.decode().strip())
        await ExecutionDispatcher._stop(proc)
        return grandchild

    grandchild = asyncio.run(scenario())

    for _ in range(100):
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {grandchild}", "/NH"],
            capture_output=True, text=True, check=False,
        ).stdout
        if str(grandchild) not in listed:
            break
        time.sleep(0.1)
    else:  # pragma: no cover - the failure this test exists to catch
        subprocess.run(["taskkill", "/PID", str(grandchild), "/F"], check=False,
                       capture_output=True)
        raise AssertionError("the grandchild outlived the stop")


# -- round fifteen -------------------------------------------------------------


def test_an_unconfirmed_pending_id_gives_way_to_unidentified(sessions, tmp_path):
    """A caller-chosen id nothing confirmed is not a session on record.

    A claude turn that ran, wrote files and never named its session left a
    `pending` row holding a non-null id, and `mark_unidentified` declined to
    touch it because an id was present. The next turn was then told "nothing was
    established, so nothing is lost" and minted a fresh UUID — a new conversation
    over a turn whose work was real, with the agent free to redo or overwrite it.
    """
    sessions.record_launch(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="chosen-1", mode=MODE_START, confirmed=False, expected_clear_seq=0,
    )
    assert sessions.get("t1", "claude", ROLE_WORKER).state == SESSION_PENDING

    sessions.mark_unidentified(
        task_id="t1", adapter="claude", workspace=tmp_path,
        reason="这一轮跑完了，但输出里读不到会话 id",
    )
    row = sessions.get("t1", "claude", ROLE_WORKER)
    assert row.state == SESSION_UNIDENTIFIED
    assert row.session_id is None, "the unconfirmed id goes with the state it belonged to"
    assert "读不到会话 id" in (row.reason or "")


def test_a_confirmed_session_is_still_never_overwritten_by_unidentified(sessions, tmp_path):
    """The relaxation above is `pending` only — a live session outranks it."""
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    sessions.mark_unidentified(
        task_id="t1", adapter="codex", workspace=tmp_path, reason="读不到",
    )
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.state == SESSION_ACTIVE
    assert row.session_id == "sid-1"


# -- round sixteen -------------------------------------------------------------


def test_a_line_that_merely_ends_with_a_secret_does_not_eat_what_follows(tmp_path, monkeypatch):
    """The swallow covers a value that was **cut**, not one that finished.

    Asking whether the redacted buffer ended with the marker was broader than
    the question: a line that merely ends with a secret matches it too, and the
    ordinary output after it — Japanese text, a path, punctuation with no leading
    separator — was then dropped without a word.
    """
    from agora.execution.dispatcher import _RedactingWriter

    secret = "Z" * 40
    monkeypatch.setenv("SOME_API_TOKEN", secret)

    logs = RunLogStore(tmp_path / "logs")
    writer = _RedactingWriter(logs, "r1")
    # A forced flush whose buffer ends exactly at the end of a whole secret.
    writer.write("noise " * 2000 + secret)
    writer.write("日本語のテキストが続く\n")
    writer.close()

    written = logs.read("r1", 0).text
    assert secret not in written
    assert "日本語のテキストが続く" in written, "output after a finished secret must survive"


def test_the_cut_never_falls_inside_a_redaction(tmp_path):
    """One function answers "where may I cut this text?", and this is what it means.

    Answering it in pieces produced four wrong versions in a row; the fourth
    handled patterns and ignored environment secrets, on the reasoning that a
    retained tail longer than any secret made a cut inside one impossible. It
    does not: a secret merely has to *straddle* the point.
    """
    from agora.execution.security import safe_cut

    # A match that reaches the end is kept whole — it may still be growing.
    assert safe_cut("noise api_key=abcdef", 20) == len("noise ")
    # A finished one may be cut after, but never through.
    text = "noise api_key=abcdef more"
    assert safe_cut(text, len(text)) == len(text)
    assert safe_cut(text, len("noise api_key=ab")) == len("noise ")
    # Nothing to avoid.
    assert safe_cut("nothing to see here", 5) == 5


def test_a_secret_straddling_the_flush_point_never_reaches_the_log(tmp_path, monkeypatch):
    """The sweep that caught it: the secret is walked across the cut, one at a time.

    Measured before the fix: it reached the log in clear at 53 of the 161 offsets
    below. A single well-chosen case would have passed — which is why this test
    is a sweep and not a case.
    """
    from agora.execution.dispatcher import _RedactingWriter

    secret = "ENVSECRET" * 6
    monkeypatch.setenv("PROBE_API_TOKEN", secret)
    logs = RunLogStore(tmp_path / "logs")

    for offset in range(-80, 81):
        run_id = f"sweep{offset}"
        writer = _RedactingWriter(logs, run_id)
        filler = "n" * (writer._FLUSH_LIMIT + 1)
        position = max(0, len(filler) - writer._keep + offset)
        chunks = [filler[:position] + secret + filler[position:], " ordinary text after\n"]
        for chunk in chunks:
            writer.write(chunk)
        writer.close()
        written = logs.read(run_id, 0).text
        assert secret not in written, f"the secret straddled the cut at offset {offset}"
        assert written == redact_text("".join(chunks)), f"offset {offset}"


def test_the_migrated_row_says_that_turns_are_refused(tmp_path):
    """The persisted reason has to describe what actually happens next.

    It said "the next worker turn opens a new session cleanly" — which stopped
    being true when the guard was widened to refuse *every* role. A person
    retrying then got refusal after refusal, and clearing under the default role
    did not touch the row doing the refusing.
    """
    import sqlite3

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE agent_sessions (
                task_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT,
                workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
                turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, last_run_id TEXT,
                PRIMARY KEY (task_id, adapter)
            )
            """
        )
        db.execute(
            "INSERT INTO agent_sessions VALUES"
            " ('t1','codex','sid-1','C:/wt','active',NULL,4,'now','now','r1')"
        )
        db.commit()

    store = AgentSessionStore(db_path)
    reason = next(
        row.reason for row in store.list_for_task("t1") if row.session_id == "sid-1"
    ) or ""
    assert "会被拒绝" in reason
    assert "role=unknown" in reason
    assert "干净地新开" not in reason, "the old claim must not survive anywhere"
    # And the refusal it describes is the behaviour.
    decision = store.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.refused


# -- round seventeen -----------------------------------------------------------


def test_output_after_a_complete_token_survives_and_the_secret_still_does_not(tmp_path):
    """The hold keeps the continuation instead of guessing about it.

    A complete ``sk-``/``ghp_`` token at a flush boundary is indistinguishable
    from a cut one — the pattern ends at a word boundary, and the end of a buffer
    is one. The first version of this guard leaked the tail of a long credential;
    the second dropped ordinary output that followed a complete one. Holding it
    until the value's own terminator answers neither way and loses nothing.
    """
    from agora.execution.dispatcher import _RedactingWriter

    logs = RunLogStore(tmp_path / "logs")

    # A complete token exactly at the boundary, then ordinary text. The
    # invariant, stated as the test: **what the writer emits is what redaction
    # would have produced had the whole text arrived at once**. Anything weaker
    # is a guess about which side of a cut a value fell on, and each of the three
    # previous versions guessed differently and wrongly.
    whole = "noise " * 2000 + "sk-abcdefghijklmnop" + "日本語のテキストが続く\n"
    writer = _RedactingWriter(logs, "r1")
    writer.write("noise " * 2000 + "sk-abcdefghijklmnop")
    writer.write("日本語のテキストが続く\n")
    writer.close()
    written = logs.read("r1", 0).text
    assert written == redact_text(whole)
    assert "sk-abcdefghijklmnop" not in written
    assert "日本語のテキストが続く" in written, "output after a finished token must survive"

    # A credential cut by the boundary: neither half may appear.
    secret = "ABCDEFGH" * 300
    writer = _RedactingWriter(logs, "r2")
    writer.write("noise " * 2000 + "api_key=" + secret[:1000])
    writer.write(secret[1000:] + " after\n")
    writer.close()
    written = logs.read("r2", 0).text
    assert "[REDACTED]" in written
    for probe in (secret[:64], secret[1000:1064], secret[-64:]):
        assert probe not in written
    assert "after" in written


def test_an_unbroken_run_past_the_hold_limit_becomes_the_marker(tmp_path):
    """The hold is bounded, and what it gives up on is shown as a redaction."""
    from agora.execution.dispatcher import _RedactingWriter

    logs = RunLogStore(tmp_path / "logs")
    writer = _RedactingWriter(logs, "r1")
    writer.write("noise " * 2000 + "api_key=" + "Q" * 100)
    # No separator anywhere, past the ceiling.
    writer.write("Q" * (_RedactingWriter._HOLD_LIMIT + 10))
    writer.write(" done\n")
    writer.close()
    written = logs.read("r1", 0).text
    assert "QQQQQQQQ" not in written, "an unshowable run must not be written"
    assert "[REDACTED]" in written
    assert "done" in written


def test_a_resume_that_never_named_a_session_stops_claiming_continuity(sessions, tmp_path):
    """Exit code 0 is not evidence that the conversation was continued.

    The row was left `active` and the badge kept reading "session live · N
    turns". All three CLIs announce the session in their first event, so silence
    means the resume was ignored — the work went somewhere Agora cannot see — or
    the reader no longer recognises the event. Either way continuity is unproven.
    """
    from agora.execution.dispatcher import ExecutionDispatcher
    from agora.execution.transcript import CodexJsonlReader

    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.sessions = sessions

    dispatcher._settle_session(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        transcript=CodexJsonlReader(), resuming=True, exit_code=0,
        role=ROLE_WORKER, requested="sid-1", killed=False,
    )
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.state == SESSION_UNREADABLE
    assert row.session_id == "sid-1", "the id is kept: the conversation is probably still there"
    assert "没法确认" in (row.reason or "")


def test_the_app_server_bridge_is_not_reported_as_token_capable(tmp_path):
    """Token capability follows the execution path, not the format alone.

    Configured on the app-server bridge, codex produces no usage result at all —
    and the settings page promised numbers that never arrive.
    """
    from agora.execution.dispatcher import token_capturable_adapters

    config = {
        "execution": {
            "adapters": {
                "codex": {"bridge_mode": "codex_app_server"},
                "claude": {},
                "kiro": {},
            }
        }
    }
    registry = build_adapter_registry(config)
    assert registry["codex"].event_format == EVENT_FORMAT_CODEX_JSONL
    capable = token_capturable_adapters(registry)
    assert "codex" not in capable, "the bridge yields no usage, whatever the format says"
    assert "claude" in capable

    # And on the CLI path it is capable again — the answer follows the config.
    config["execution"]["adapters"]["codex"] = {"bridge_mode": "cli"}
    assert "codex" in token_capturable_adapters(build_adapter_registry(config))


# -- round eighteen ------------------------------------------------------------


def test_a_launch_does_not_clear_a_warning_state(sessions, tmp_path):
    """`unreadable` and `forked` survive the start of the next turn.

    They record what a previous turn *observed*. Overwriting them with `active`
    at launch made the badge read "session live" again the moment a turn started,
    before any event of that turn had confirmed anything — and a run cancelled
    before its first event left the claim standing, because `_settle_session`
    says nothing about a killed run.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    sessions.mark_unreadable(
        task_id="t1", adapter="claude", role=ROLE_WORKER,
        reason="上一轮跑完了但没报出会话 id",
    )

    sessions.record_launch(
        run_id="r2", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="sid-1", mode=MODE_RESUME, confirmed=True, expected_clear_seq=0,
    )
    row = sessions.get("t1", "claude", ROLE_WORKER)
    assert row.state == SESSION_UNREADABLE, "a launch is not evidence"
    assert "没报出会话 id" in (row.reason or "")

    # An event from the provider *is* evidence, and clears it.
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="claude", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    healed = sessions.get("t1", "claude", ROLE_WORKER)
    assert healed.state == SESSION_ACTIVE
    assert healed.reason is None


def test_the_writer_emits_exactly_what_redaction_would_have(tmp_path):
    """The invariant, over the shapes that broke each earlier version.

    Every one of those versions was a guess about which side of a forced flush a
    value fell on. Stating the guarantee as "what comes out equals redacting the
    whole input" leaves nothing to guess: it is either true of a case or not.
    """
    from agora.execution.dispatcher import _RedactingWriter

    logs = RunLogStore(tmp_path / "logs")
    long_run = "noise " * 2000
    cases = {
        "cut-assignment": [long_run + "api_key=" + "A" * 1200, "B" * 1200 + " tail\n"],
        "finished-token": [long_run + "sk-abcdefghijklmnop", "日本語が続く\n"],
        "no-secret": [long_run + "x" * 3000, "y" * 100 + "\n"],
        "secret-then-newline": [long_run + "api_key=hunter2\n", "after\n"],
        "split-across-three": [long_run + "api_", "key=secret", "value tail\n"],
    }
    for index, (name, chunks) in enumerate(cases.items()):
        run_id = f"r{index}"
        writer = _RedactingWriter(logs, run_id)
        for chunk in chunks:
            writer.write(chunk)
        writer.close()
        assert logs.read(run_id, 0).text == redact_text("".join(chunks)), name


# -- round nineteen ------------------------------------------------------------


def test_a_token_between_word_characters_is_still_redacted(tmp_path):
    """Both ends of the token pattern follow its own alphabet.

    `\\b` between "鍵" and "s" is not a boundary — Japanese characters are word
    characters — so `鍵sk-abcdefghijklmnop日本語` matched nothing at all and the
    credential was written in clear by every redaction pass. In this project's
    own output, an identifier pressed against Japanese text is ordinary.
    """
    assert "sk-abcdefghijklmnop" not in redact_text("鍵sk-abcdefghijklmnop日本語")
    assert "ghp_abcdefghijklmnop" not in redact_text("キーghp_abcdefghijklmnop、次")
    # And an ordinary word ending in "sk" is not turned into a false positive.
    assert redact_text("risk-management") == "risk-management"


def test_the_session_named_in_a_dropped_head_is_still_read(tmp_path):
    """The head of the output is kept, because that is where the session is.

    All three CLIs announce it in their first event. A chatty run pushes that
    line out of a bounded tail, so a turn that had named its session was recorded
    as having named none — and the next message opened a new conversation over
    the files it had already written.
    """
    import asyncio

    from agora.execution.dispatcher import ExecutionDispatcher, OUTPUT_TAIL_LIMIT

    class _Stream:
        def __init__(self, data: bytes) -> None:
            self._data = data

        async def read(self, size: int) -> bytes:
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk

    opening = b'{"type":"thread.started","thread_id":"sid-in-the-head"}\n'
    noisy = opening + (b"x" * (OUTPUT_TAIL_LIMIT * 3)) + b"\ntail line\n"

    head, tail, total = asyncio.run(
        ExecutionDispatcher._read_head_and_tail(_Stream(noisy), keep_head=OUTPUT_TAIL_LIMIT)
    )
    assert total == len(noisy), "the reader counts every byte that went past it"

    assert opening in head, "the opening event must survive a long run"
    assert b"tail line" in tail
    assert opening not in tail, "and the tail alone is exactly what used to be read"


# -- round twenty --------------------------------------------------------------


def test_a_multi_line_secret_is_not_written_one_line_at_a_time(tmp_path, monkeypatch):
    """A completed line is the unit, but not when it is half a secret.

    An environment value can be multi-line — a PEM in `PRIVATE_KEY` is the
    ordinary case — and a CLI prints it line by line. The first line matched
    nothing on its own and went out the moment its newline arrived, so the log
    held a piece of the key for ever and the polling API served it.
    """
    from agora.execution.dispatcher import _RedactingWriter

    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\nhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"
    monkeypatch.setenv("PROBE_PRIVATE_KEY", pem)

    logs = RunLogStore(tmp_path / "logs")
    writer = _RedactingWriter(logs, "r1")
    for line in pem.split("\n"):
        writer.write(line + "\n")
    writer.write("done\n")
    writer.close()

    written = logs.read("r1", 0).text
    assert "MIIEvQIBADANBgkq" not in written, "a line of the key reached the log"
    assert "[REDACTED]" in written
    assert "done" in written


def test_a_retry_puts_a_lost_session_back_within_reach(sessions, tmp_path):
    """The state's own message names an action, and now the action exists.

    `lost` refuses every turn on a real observation, and nothing `decide` can
    re-derive would notice it had stopped being true. The person says so instead
    — they logged in again — and the recorded id is tried rather than dropped.
    """
    sessions.record_observed(
        run_id="r1", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-1", role=ROLE_WORKER,
    )
    sessions.mark_lost(
        task_id="t1", adapter="codex", role=ROLE_WORKER, reason="登录过期",
    )
    refused = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert refused.refused

    assert sessions.retry(task_id="t1", adapter="codex") is True
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.session_id == "sid-1", "a retry keeps the conversation it is retrying"
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert not decision.refused
    assert decision.mode == MODE_RESUME
    assert decision.session_id == "sid-1"

    # And it says so about nothing else: a live session is not "retried".
    assert sessions.retry(task_id="t1", adapter="codex") is False


# -- round twenty-one ----------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="the job object is the Windows path; POSIX is CR-46")
def test_a_grandchild_dies_even_when_the_cli_exited_first(tmp_path):
    """The case `taskkill /T` cannot reach, measured.

    An agent that starts a background script and then returns leaves nothing to
    walk the tree from, so the script kept writing the worktree after Agora had
    frozen the deliverables and released the lock — and the next run entered a
    directory a live process was still changing. A job object holds every
    descendant regardless of the parent's lifetime.
    """
    import asyncio
    import subprocess
    import sys

    from agora.execution.dispatcher import ExecutionDispatcher

    program = (
        "import subprocess, sys; "
        # DEVNULL, or the grandchild inherits this run's stdout pipe and
        # `proc.wait()` blocks until it exits — two minutes on every gate run.
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(120)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "print(child.pid, flush=True)"
        # and then the parent exits immediately, leaving the child behind.
    )

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", program,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        job = ExecutionDispatcher._adopt_process_tree(proc)
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
        grandchild = int(line.decode().strip())
        await proc.wait()  # the CLI is gone; the background process is not
        assert proc.returncode is not None
        await ExecutionDispatcher._stop(proc, job)
        return grandchild, job

    grandchild, job = asyncio.run(scenario())
    assert job is not None, "the job object could not be created on this machine"

    for _ in range(100):
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {grandchild}", "/NH"],
            capture_output=True, text=True, check=False,
        ).stdout
        if str(grandchild) not in listed:
            break
        time.sleep(0.1)
    else:  # pragma: no cover - the failure this test exists to catch
        subprocess.run(["taskkill", "/PID", str(grandchild), "/F"],
                       check=False, capture_output=True)
        raise AssertionError("the background process outlived its run")


def test_a_japanese_filename_is_recorded_as_the_file_it_is(tmp_path):
    """git quotes and octal-escapes any non-ASCII path by default.

    Stripping the quotes and stopping there recorded a path that does not exist:
    no size, no digest, and nothing downstream could reach the real file. On
    these machines a Japanese filename is not an edge case.
    """
    from agora.execution.deliverables import _porcelain_path

    quoted = '"\\346\\227\\245\\346\\234\\254\\350\\252\\236.txt"'
    assert _porcelain_path(quoted) == "日本語.txt"
    # Bytes are decoded together, not one escape at a time — the latter turns
    # every multi-byte character into mojibake.
    assert "\ufffd" not in _porcelain_path(quoted)


def test_a_rename_arrow_inside_a_name_is_not_a_rename(tmp_path):
    """`old -> new` is a separator; a file can also be called `a -> b`."""
    from agora.execution.deliverables import _porcelain_path

    assert _porcelain_path("old.txt -> new.txt") == "new.txt"
    assert _porcelain_path('"a -> b.txt"') == "a -> b.txt"
    assert _porcelain_path(
        '"old \\346\\227\\245.txt" -> "new \\346\\234\\254.txt"'
    ) == "new 本.txt"
    assert _porcelain_path('"with\\"quote.txt"') == 'with"quote.txt'
    assert _porcelain_path("plain.txt") == "plain.txt"


def test_a_value_collapsed_at_the_hold_limit_keeps_its_tail_out_of_the_log(tmp_path):
    """The rest of a collapsed value is dropped, not appended after the marker.

    Once a value grows past the hold limit it is replaced by a marker — and its
    trigger word goes with it, so redaction can never match what follows. This is
    the one place the drop needs no guess: the collapse only happens while a
    value is still growing.
    """
    from agora.execution.dispatcher import _RedactingWriter

    logs = RunLogStore(tmp_path / "logs")
    writer = _RedactingWriter(logs, "r1")
    writer.write("noise " * 2000 + "api_key=" + "K" * 100)
    writer.write("K" * (_RedactingWriter._HOLD_LIMIT + 10))
    writer.write("KKKKKKKKKKKKKKKK tail follows\n")
    writer.close()

    written = logs.read("r1", 0).text
    assert "KKKKKKKK" not in written, "the tail of a collapsed value must not be written"
    assert "[REDACTED]" in written
    assert "tail follows" in written, "and output after the value still arrives"


def test_every_spawn_path_releases_its_job(tmp_path):
    """Both paths assign a job; both have to release it.

    The app-server path popped `_active` and left `_jobs` alone, so
    `KILL_ON_JOB_CLOSE` never fired and a background process the agent left
    behind kept writing the worktree after the snapshot was frozen and the lock
    released. Asserted by reading the source rather than by running that bridge,
    which needs a live codex app-server — what is being checked is that no
    `_jobs[...] = ` has been added without a matching release.
    """
    import pathlib
    import re

    # Located from this test file, not from an absolute path: the repository
    # can be checked out anywhere.
    source = pathlib.Path(__file__).resolve().parents[1] / "agora" / "execution" / "dispatcher.py"
    source = source.read_text(encoding="utf-8")
    assigns = len(re.findall(r"self\._jobs\[[^\]]+\] = ", source))
    releases = len(re.findall(r"self\._release_process_tree\(self\._jobs\.pop", source))
    assert assigns > 0, "the job assignment was not found at all"
    assert releases == assigns, (
        f"{assigns} spawn paths assign a job object and {releases} release one"
    )


# -- round twenty-three --------------------------------------------------------


def test_awkward_filenames_are_recorded_as_themselves(tmp_path):
    """Against real git output, with the names that broke the guessing parser.

    A file may be called ``a -> b.txt``; git quotes neither the space nor the
    arrow, so splitting on " -> " recorded a rename to a file that does not
    exist. A name with a leading or trailing space lost it to a strip(). And a
    Japanese name comes back octal-escaped unless ``-z`` is used. In each case
    the recorded path was unreachable: no size, no digest, and the real change
    never reached review.

    The parse is exercised on what git actually printed — the collection around
    it needs a provisioned worktree, which is a different question from whether
    the format is read correctly.
    """
    import subprocess

    from agora.environments import LocalEnvironment
    from agora.execution.deliverables import _porcelain_z_entries

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    # The default that produces octal escapes without `-z`; set explicitly so the
    # test states the condition it is about.
    git("config", "core.quotePath", "true")
    (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")

    # `>` is not legal in a Windows filename, so the arrow is exercised against
    # the parser below rather than against the filesystem.
    names = ["日本語.txt", " leading and trailing .txt"]
    for name in names:
        (tmp_path / name).write_text("x", encoding="utf-8")

    result = LocalEnvironment().run(
        ["git", "-C", str(tmp_path), "status", "--porcelain=v1", "-z",
         "--untracked-files=all"],
        timeout=20,
    )
    assert result.ok, result.stderr
    recorded = {path for _code, path in _porcelain_z_entries(result.stdout)}
    for name in names:
        assert name in recorded, f"{name!r} was not recorded as itself: {recorded}"
        assert (tmp_path / name).is_file(), "and the recorded path must exist on disk"

    # Two NUL-terminated records are a rename; one record whose path merely
    # contains " -> " is a file with an awkward name.
    assert _porcelain_z_entries("?? a -> b.txt\0") == [("?", "a -> b.txt")]
    # A rename is two changes: the new path, and the old one no longer
    # existing. Dropping the source said nothing about a file having been
    # removed, so review and any later restore never saw that half.
    assert _porcelain_z_entries("R  new.txt\0old.txt\0") == [
        ("R", "new.txt"), ("D", "old.txt"),
    ]
    # A copy leaves its source untouched, so only the new path changed.
    assert _porcelain_z_entries("C  copy.txt\0source.txt\0") == [("C", "copy.txt")]


def test_a_run_whose_child_holds_the_pipes_still_settles(tmp_path, monkeypatch):
    """The production shape the earlier test dodged with DEVNULL.

    A background process inherits the pipes, so `proc.wait()` returns while
    nothing ever reaches EOF. Unbounded, the drain waited for ever: the run never
    settled and its worktree lock was never released, long after
    `timeout_seconds` had passed.
    """
    import asyncio
    import sys
    import time as _time

    from agora.execution import dispatcher as dispatcher_module
    from agora.execution.dispatcher import ExecutionDispatcher

    # The production bound is twenty seconds; the behaviour under test is that
    # the wait *ends*, not how long it is. Shortened here so the gate does not
    # pay the real bound — and the child outlives it either way.
    monkeypatch.setattr(dispatcher_module, "_DRAIN_TIMEOUT_SECONDS", 2)
    _DRAIN_TIMEOUT_SECONDS = 2

    program = (
        "import subprocess, sys; "
        # Inherits this process's stdout — exactly what a backgrounded build does.
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(12)']); "
        "print('parent done', flush=True)"
    )

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", program,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        job = ExecutionDispatcher._adopt_process_tree(proc)
        await proc.wait()
        # What the dispatcher does, in the same order: stop the tree, then drain
        # under a bound.
        ExecutionDispatcher._kill_process_tree(job)
        reader = asyncio.create_task(ExecutionDispatcher._read_tail(proc.stdout))
        # Timed **around the wait**, which is what the turn is blocked on. The
        # abandoned task itself may linger until the pipe closes; measuring until
        # the event loop shuts down measures that instead, and on Windows a
        # pending pipe read does not answer a cancel at all — which is exactly
        # why the dispatcher abandons rather than cancels.
        started = _time.monotonic()
        done, pending = await asyncio.wait({reader}, timeout=_DRAIN_TIMEOUT_SECONDS)
        elapsed = _time.monotonic() - started
        out = reader.result() if reader in done else b""
        ExecutionDispatcher._release_process_tree(job)
        for task in pending:
            task.cancel()
        return out, elapsed, bool(pending)

    out, elapsed, abandoned = asyncio.run(scenario())

    assert elapsed < _DRAIN_TIMEOUT_SECONDS + 10, (
        f"the drain did not end: {elapsed:.1f}s"
    )
    # On Windows the job takes the pipe holder with it, so the read ends at EOF
    # and the output is all there. Where there is no job (CR-46) the bound is
    # what ends it, and the run still settles.
    if os.name == "nt":
        assert b"parent done" in out


def test_an_abandoned_reader_cannot_write_after_its_turn_settled(sessions, tmp_path):
    """The gate travels with the reader, so nothing can evict it.

    The first version kept a bounded set of closed run ids — a cache pretending
    to be a guard. After 256 more such runs the oldest id was evicted, and a
    reader still attached to a long-lived pipe could write again, including a
    session event that put an old id back over the one a later turn had
    established.
    """
    from agora.execution.dispatcher import ExecutionDispatcher, _ReaderGate
    from agora.execution.transcript import CodexJsonlReader

    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.sessions = sessions
    dispatcher._session_noted = {}
    dispatcher.run_logs = RunLogStore(tmp_path / "logs")
    run = _fake_run(task_id="t1", adapter="codex", run_id="r1")
    gate = _ReaderGate()
    reader = CodexJsonlReader()
    writer = _CollectingWriter()

    dispatcher._render_line(
        run, tmp_path, reader, ROLE_WORKER, None,
        b'{"type":"thread.started","thread_id":"sid-1"}', writer, gate,
    )
    assert sessions.get("t1", "codex", ROLE_WORKER).session_id == "sid-1"

    # The turn settles without this reader; a later turn establishes its own
    # session, and then the abandoned reader finally sees an old event.
    gate.close()
    sessions.record_observed(
        run_id="r2", task_id="t1", adapter="codex", workspace=tmp_path,
        session_id="sid-2", role=ROLE_WORKER,
    )
    dispatcher._render_line(
        run, tmp_path, reader, ROLE_WORKER, None,
        b'{"type":"thread.started","thread_id":"sid-1"}', writer, gate,
    )
    assert sessions.get("t1", "codex", ROLE_WORKER).session_id == "sid-2", (
        "a closed reader must not put an old session back"
    )


def test_a_head_and_tail_that_overlap_are_not_written_twice(tmp_path):
    """`startswith` only catches total overlap; the partial case duplicated text.

    When a run's output sits between one and two tail limits, the kept head and
    the bounded tail share a middle section — and concatenating them printed the
    commands, file changes and replies in it twice.
    """
    from agora.execution.dispatcher import _merge_overlapping

    from agora.execution.dispatcher import _MERGE_GAP_NOTICE

    whole = b"".join(f"line {index}\n".encode() for index in range(200))
    head, tail = whole[:600], whole[400:]
    # The overlap is arithmetic: len(head) + len(tail) - total.
    assert _merge_overlapping(head, tail, len(whole)) == whole
    # Total overlap: the whole output fit in both.
    assert _merge_overlapping(whole, whole, len(whole)) == whole
    # Repeated content is not evidence of an overlap. These two pieces share a
    # long run of identical bytes, and the byte-matching version spliced them
    # there — deleting real output and reporting no gap. The counts say they do
    # not meet, so the gap is stated.
    repeated_head = b"same line\n" * 5
    repeated_tail = b"same line\n" * 5
    joined = _merge_overlapping(repeated_head, repeated_tail, 400)
    assert joined == repeated_head + _MERGE_GAP_NOTICE + repeated_tail
    assert "没有保留" in _MERGE_GAP_NOTICE.decode("utf-8")
    # And two pieces that exactly meet are joined with nothing in between.
    assert _merge_overlapping(b"aaa", b"bbb", 6) == b"aaabbb"
    assert _merge_overlapping(b"", b"bbb", 3) == b"bbb"
    assert _merge_overlapping(b"aaa", b"", 3) == b"aaa"


def test_a_concurrent_migration_does_not_undo_the_first_one(tmp_path):
    """Two control planes opening the same old database must not both migrate.

    The schema check was a read taken before the write lock: the second process
    saw "no role", arrived after the first had finished, renamed the *migrated*
    table back and refiled every row as `unknown`. Tasks that had been fine a
    second earlier then refused both worker and review turns.
    """
    import sqlite3
    import threading

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE agent_sessions (
                task_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT,
                workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
                turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, last_run_id TEXT,
                PRIMARY KEY (task_id, adapter)
            )
            """
        )
        db.execute(
            "INSERT INTO agent_sessions VALUES"
            " ('t1','codex','sid-1','C:/wt','active',NULL,4,'now','now','r1')"
        )
        db.commit()

    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def open_store():
        try:
            barrier.wait(timeout=10)
            AgentSessionStore(db_path)
        except BaseException as error:  # pragma: no cover - the failure under test
            failures.append(error)

    threads = [threading.Thread(target=open_store) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures, failures
    store = AgentSessionStore(db_path)
    rows = [row for row in store.list_for_task("t1") if row.session_id == "sid-1"]
    assert len(rows) == 1, rows
    assert rows[0].role == "unknown"
    # And the pre-role table is gone: a second rebuild would have re-created it.
    with sqlite3.connect(db_path) as db:
        tables = {
            name for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "agent_sessions_pre_role" not in tables


def test_a_transcript_rebuilt_from_a_bounded_tail_says_it_may_be_short(tmp_path):
    """The record's stdout is a tail, and the window must not read it as the whole.

    Everything before it was dropped when the run settled, so a long answer whose
    conclusion was at the top came back as its last few kilobytes — presented as
    complete, with no notice and no way for the reader to tell.
    """
    from agora.execution.models import RunState
    from agora.execution.router import run_output
    from agora.execution.store import OUTPUT_TAIL_LIMIT

    logs = RunLogStore(tmp_path / "logs")  # deliberately empty: no transcript file

    class _Store:
        def require(self, run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                run_id=run_id, state=RunState.SUCCEEDED, task_id="t1", adapter="codex",
                prompt="do the thing", error=None, error_message=None,
                stdout_tail="x" * OUTPUT_TAIL_LIMIT, stderr_tail="",
            )

    class _Dispatcher:
        def __init__(self) -> None:
            self.run_logs = logs

        def is_draining(self, run_id: str) -> bool:
            return False

    sliced = run_output("r1", 0, _Store(), _Dispatcher())
    assert sliced.from_record is True
    assert sliced.lossy is True, "a bounded tail is not the whole of a turn"


# -- round twenty-seven --------------------------------------------------------


def test_a_renamed_source_that_was_re_created_is_not_called_deleted(tmp_path):
    """`git mv old new` and then a new `old` is both a rename and an untracked file.

    Adding the source's deletion unconditionally made the snapshot say the same
    path was deleted *and* present, and the baseline then carried that false
    deletion into every later run.
    """
    from agora.execution.deliverables import _porcelain_z_entries

    nul = chr(0)
    # The shape git prints: a rename, and the re-created source as untracked.
    stdout = f"R  new.txt{nul}old.txt{nul}?? old.txt{nul}"
    assert _porcelain_z_entries(stdout) == [("R", "new.txt"), ("?", "old.txt")]

    # And when nothing put it back, the deletion is still recorded.
    assert _porcelain_z_entries(f"R  new.txt{nul}old.txt{nul}") == [
        ("R", "new.txt"), ("D", "old.txt"),
    ]


def test_a_closed_gate_also_stops_the_writer_s_final_flush(tmp_path):
    """A chunk with no newline waits in the writer until close.

    A reader abandoned at the drain bound then wrote that text into a turn the
    window had already been told was complete — text nobody would see without
    reloading the page.
    """
    import asyncio

    from agora.execution.dispatcher import ExecutionDispatcher, _ReaderGate
    from agora.execution.transcript import PlainTextReader

    dispatcher = ExecutionDispatcher.__new__(ExecutionDispatcher)
    dispatcher.run_logs = RunLogStore(tmp_path / "logs")
    dispatcher._session_noted = {}
    run = _fake_run(task_id="t1", adapter="codex", run_id="r1")
    gate = _ReaderGate()

    class _Stream:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._served = False

        async def read(self, size: int) -> bytes:
            if self._served:
                return b""
            self._served = True
            return self._data

    async def scenario():
        # No trailing newline: the text stays in the writer's buffer until close.
        task = asyncio.create_task(
            dispatcher._read_streaming(
                _Stream(b"a late answer with no newline"), run, tmp_path,
                PlainTextReader(), ROLE_WORKER, None, gate,
            )
        )
        gate.close()
        await task

    asyncio.run(scenario())
    assert "late answer" not in dispatcher.run_logs.read("r1", 0).text, (
        "a closed reader must not append after the turn was called complete"
    )


# -- round twenty-eight --------------------------------------------------------


def test_a_file_deleted_from_the_worktree_is_not_recorded_as_modified(tmp_path):
    """`XY` is two states, and the worktree column is the one about the disk.

    An agent that stages a change and then deletes the file produces `MD`.
    Reading only the index column reported "modified", so the snapshot and the
    approval screen showed a file that is not there any more — and a later step
    could act on it as though it were.
    """
    from agora.execution.deliverables import _porcelain_z_entries

    nul = chr(0)
    assert _porcelain_z_entries(f"MD config.yaml{nul}") == [("D", "config.yaml")]
    # Staged-only changes still read from the index column.
    assert _porcelain_z_entries(f"M  staged.yaml{nul}") == [("M", "staged.yaml")]
    # And an ordinary worktree modification is unchanged.
    assert _porcelain_z_entries(f" M edited.yaml{nul}") == [("M", "edited.yaml")]
    # A rename is still recognised from either column.
    assert _porcelain_z_entries(f"R  new.txt{nul}old.txt{nul}") == [
        ("R", "new.txt"), ("D", "old.txt"),
    ]


def test_a_cancel_drains_before_it_closes_the_gate(tmp_path):
    """The gate closes after the drain, and only for a reader still pending.

    Closing it first threw away exactly what was waiting to be read — the
    agent's final reply, and the session id, which the next turn then had to
    open a new conversation without.
    """
    import inspect

    from agora.execution.dispatcher import ExecutionDispatcher

    source = inspect.getsource(ExecutionDispatcher._execute_once)
    cancel_branch = source[source.index("except asyncio.CancelledError:"):]
    cancel_branch = cancel_branch[: cancel_branch.index("raise")]
    wait_at = cancel_branch.index("asyncio.wait(")
    close_at = cancel_branch.index("gate.close()")
    assert wait_at < close_at, (
        "the cancel path must drain before it stops accepting output"
    )


# -- round twenty-nine ---------------------------------------------------------


def test_a_baseline_path_that_disappeared_is_recorded_as_gone(tmp_path):
    """Iterating only what git still reports missed everything a run removed.

    A file left dirty by an earlier run and then deleted, moved or committed away
    by this one vanished from the record entirely: the run read as having
    delivered nothing, and review, approval and any later restore never learned
    the file had gone.
    """
    from agora.execution import deliverables

    workspace = tmp_path / "wt"
    workspace.mkdir()
    baseline = ({"old.txt": {"status": "untracked", "size": 3, "sha256": "abc"}}, True)

    def fake_read(_workspace, _env):
        return ([{"path": "new.txt", "status": "untracked", "size": 3}], False)

    original = deliverables._read_changes
    deliverables._read_changes = fake_read
    try:
        snapshot = deliverables.snapshot_changes(workspace, baseline)
    finally:
        deliverables._read_changes = original

    paths = {item["path"]: item for item in snapshot["files"]}
    assert "new.txt" in paths
    assert "old.txt" in paths, "a path that left the worktree is still a change"
    assert paths["old.txt"]["status"] == "deleted"
    assert paths["old.txt"]["gone_since_baseline"] is True


def test_a_truncated_list_does_not_invent_deletions(tmp_path):
    """An absent path may simply be one the truncated list could not fit."""
    from agora.execution import deliverables

    workspace = tmp_path / "wt"
    workspace.mkdir()
    baseline = ({"old.txt": {"status": "untracked", "size": 3, "sha256": "abc"}}, True)

    def fake_read(_workspace, _env):
        return ([{"path": "new.txt", "status": "untracked", "size": 3}], True)

    original = deliverables._read_changes
    deliverables._read_changes = fake_read
    try:
        snapshot = deliverables.snapshot_changes(workspace, baseline)
    finally:
        deliverables._read_changes = original

    assert {item["path"] for item in snapshot["files"]} == {"new.txt"}
    assert snapshot["truncated"] is True


def test_marking_a_session_lost_works_when_no_row_exists_yet(sessions, tmp_path):
    """The fail-closed must not depend on a row already being there.

    Update-only, it silently did nothing — and the caller that must never
    silently do nothing is exactly this one: a turn that named two sessions. One
    transient write error on the first event was enough for the refusal to be
    lost, so the conversation read as "no session" and the next turn opened a new
    one over work the second session had already written.
    """
    sessions.mark_lost(
        task_id="t1", adapter="codex", role=ROLE_WORKER,
        reason="这一轮出现了两个会话 id",
    )
    row = sessions.get("t1", "codex", ROLE_WORKER)
    assert row.state == SESSION_LOST
    assert "两个会话 id" in (row.reason or "")
    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    # No id was ever recorded, so there is nothing to resume — but the state is
    # on record and the window shows why the last turn stopped.
    assert decision.mode == MODE_START


def test_a_read_never_starts_inside_a_character(tmp_path):
    """An offset landing mid-character used to return an unrepairable replacement.

    The continuation bytes decoded to U+FFFD and `next_offset` moved past them,
    so no later page could bring the character back — on a transcript that opens
    in Japanese, the first thing the reader saw was mojibake.
    """
    logs = RunLogStore(tmp_path / "logs")
    logs.append("r1", "日本語です\n")

    whole = logs.read("r1", 0)
    assert whole.text.startswith("日本語")

    # One byte into the first character.
    sliced = logs.read("r1", 1)
    assert "\ufffd" not in sliced.text, "a mid-character start must not be served"
    assert sliced.text.startswith("本語"), sliced.text
    assert sliced.offset == 3, "the served offset says where the text really starts"


# -- round thirty --------------------------------------------------------------


def test_a_file_put_back_the_way_head_has_it_is_not_called_deleted(tmp_path):
    """Leaving the change list has two causes, and only one is a deletion.

    A run can revert a file or commit it, and git then stops reporting it while
    the file sits right there. Recording that as "deleted" told review and
    approval to expect it gone — and a restore step built on that would have
    removed a file nobody asked to remove.
    """
    from agora.execution import deliverables

    workspace = tmp_path / "wt"
    workspace.mkdir()
    (workspace / "reverted.yaml").write_text("back to HEAD", encoding="utf-8")
    baseline = (
        {
            "reverted.yaml": {"status": "modified", "size": 1, "sha256": "a"},
            "removed.yaml": {"status": "modified", "size": 1, "sha256": "b"},
        },
        True,
    )

    original = deliverables._read_changes
    deliverables._read_changes = lambda _w, _e: ([], False)
    try:
        snapshot = deliverables.snapshot_changes(workspace, baseline)
    finally:
        deliverables._read_changes = original

    rows = {item["path"]: item for item in snapshot["files"]}
    assert rows["reverted.yaml"]["status"] == "no longer differs"
    assert rows["reverted.yaml"]["exists"] is True
    assert rows["removed.yaml"]["status"] == "deleted"
    assert rows["removed.yaml"]["exists"] is False


def test_a_lost_session_with_no_id_still_refuses(sessions, tmp_path):
    """The fail-closed is checked before "no id, so start cleanly".

    A turn that named two sessions can leave `lost` with no id at all — the
    first write failed. Returning "start" for a missing id then bypassed the
    gate by exactly the path it exists to close: the next turn opened a new
    context over the work the second session had written.
    """
    sessions.mark_lost(
        task_id="t1", adapter="codex", role=ROLE_WORKER,
        reason="这一轮出现了两个会话 id",
    )
    assert sessions.get("t1", "codex", ROLE_WORKER).session_id is None

    decision = sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    )
    assert decision.refused, "a lost session refuses whether or not it holds an id"
    assert "两个会话 id" in (decision.refusal or "")

    # And clearing it explicitly is what lets work continue.
    sessions.forget(task_id="t1", adapter="codex")
    assert not sessions.decide(
        task_id="t1", adapter="codex", workspace=tmp_path,
        can_resume=True, caller_chooses_id=False,
    ).refused


# -- round thirty-two ----------------------------------------------------------


def test_a_baseline_without_a_head_says_commits_are_unread(tmp_path):
    """The old two-field baseline cannot attribute a commit, and says so."""
    from agora.execution.deliverables import CLEAN_BASELINE, snapshot_changes
    from agora.execution import deliverables

    workspace = tmp_path / "wt"
    workspace.mkdir()
    original = deliverables._read_changes
    deliverables._read_changes = lambda _w, _e: ([], False)
    try:
        snapshot = snapshot_changes(workspace, CLEAN_BASELINE)
    finally:
        deliverables._read_changes = original
    assert snapshot["commits_unread"] is True


def test_an_interrupted_rebuild_keeps_both_roles(tmp_path):
    """A renamed table that already carries roles must not be squashed.

    Forcing every row to `unknown` collided two roles on one primary key:
    `INSERT OR IGNORE` dropped the second silently and the drop that follows made
    it unrecoverable.
    """
    import sqlite3

    db_path = tmp_path / "interrupted.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE agent_sessions_pre_role (
                task_id TEXT NOT NULL, adapter TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'worker', session_id TEXT,
                workspace TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
                turns INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL, last_run_id TEXT,
                PRIMARY KEY (task_id, adapter, role)
            )
            """
        )
        db.executemany(
            "INSERT INTO agent_sessions_pre_role VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("t1", "codex", "worker", "sid-worker", "C:/wt", "active", None, 3,
                 "now", "now", "r1"),
                ("t1", "codex", "review", "sid-review", "C:/wt", "active", None, 1,
                 "now", "now", "r2"),
            ],
        )
        db.commit()

    store = AgentSessionStore(db_path)
    assert store.get("t1", "codex", ROLE_WORKER).session_id == "sid-worker"
    assert store.get("t1", "codex", "review").session_id == "sid-review"


# --- CR-100: a tool call is named, never reprinted -----------------------------------

def test_a_tool_call_is_named_not_reprinted(tmp_path):
    """The transcript is what the person reads in the conversation window, and a tool
    call's payload is not reading matter: a ``Write``'s input *is* the file, so
    printing it put whole source files between the agent's sentences (user,
    2026-09-19: "这啥内容啊 为什么要放到窗口里"). The agent's own terminal names the
    call — ``Write(path)`` — and so does this.

    Shapes here are the ones a real ``claude -p --output-format stream-json`` emitted
    on this machine (captured 2026-09-19), including ``PowerShell`` as the shell tool,
    which the first version of the map did not know about and so fell through to the
    payload.
    """
    from agora.execution.transcript import reader_for

    cwd = r"D:\Projects\Agora\projects\demo\.agora\workspaces\claude"
    secret_body = "from app.config import Settings\n" * 40

    def block(name, payload):
        return json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1", "name": name, "input": payload}
            ]},
        })

    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    reader.feed(json.dumps({"type": "system", "subtype": "init", "cwd": cwd}))
    rendered = "".join([
        reader.feed(block("Read", {"file_path": cwd + r"\backend\app\db.py"})),
        reader.feed(block("Write", {"file_path": cwd + r"\backend\app\config.py",
                                    "content": secret_body})),
        reader.feed(block("Edit", {"file_path": cwd + r"\README.md",
                                   "old_string": secret_body, "new_string": secret_body})),
        reader.feed(block("PowerShell", {"command": "Get-ChildItem backend",
                                         "description": "list the backend"})),
        reader.feed(block("Bash", {"command": "git status --short",
                                   "description": "check the tree"})),
        reader.feed(block("Grep", {"pattern": "def create_app", "output_mode": "content"})),
        reader.feed(block("SomeNewTool", {"whatever": "x" * 5_000})),
    ])

    # The payload never reaches the window — not from Write, not from Edit.
    assert "from app.config import Settings" not in rendered
    assert len(rendered) < 1_000, rendered

    lines = [line for line in rendered.splitlines() if line.startswith("$ ")]
    assert lines[:3] == [
        # Relative to the run's own cwd, as the terminal prints it, and the write
        # says how much it wrote rather than what.
        r"$ Read(backend\app\db.py)",
        r"$ Write(backend\app\config.py) 41 行",
        r"$ Edit(README.md)",
    ]
    # A command is the one payload worth reading in full, from either shell.
    assert lines[3] == "$ PowerShell(Get-ChildItem backend)"
    assert lines[4] == "$ Bash(git status --short)"
    assert lines[5] == "$ Grep(def create_app)"
    # A tool this build has never heard of stays thin rather than invisible.
    assert lines[6].startswith("$ SomeNewTool ") and len(lines[6]) < 400


def test_a_commands_own_spacing_is_never_rewritten():
    """What the transcript shows has to be what ran, *inside* a line. Folding
    whitespace to fit turned ``A  B.txt`` into ``A B.txt`` (review, 2026-09-19).

    The one stated exception is the two-space quote on continuation lines, without
    which a multi-line command is indistinguishable from the agent's prose in a
    line-oriented record. It is asserted here so that it stays a decision rather
    than becoming an accident (workspace CR-101 carries the structured-record fix).
    """
    from agora.execution.transcript import reader_for

    command = 'Move-Item "C:\旧資料\A  B.txt" "D:\保存先\A  B.txt"\ngit status --short'
    reader = reader_for(EVENT_FORMAT_CLAUDE_STREAM_JSON)
    rendered = reader.feed(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": command}}
        ]},
    }))

    assert "A  B.txt" in rendered, "the double space is part of the filename"
    assert "A B.txt" not in rendered
    # Both lines survive, the second indented so it stays with the first on screen.
    assert "$ Bash(" in rendered and "\n  git status --short" in rendered


# -- run activity (CR-12 ①) ------------------------------------------------------


def _running(run_id, started_at):
    from types import SimpleNamespace

    from agora.execution.models import RunState

    return SimpleNamespace(
        run_id=run_id, state=RunState.RUNNING, task_id="t1", adapter="codex",
        prompt="", error=None, stdout_tail="", stderr_tail="", started_at=started_at,
    )


class _QuietDispatcher:
    def __init__(self, logs):
        self.run_logs = logs

    def is_draining(self, run_id):
        return False


def _output_of(run, logs):
    from agora.execution.router import run_output

    class _Store:
        def require(self, run_id):
            return run

    return run_output(run.run_id, 0, _Store(), _QuietDispatcher(logs))


def test_a_running_run_says_how_long_its_transcript_has_been_silent(tmp_path):
    import os
    import time

    logs = RunLogStore(tmp_path / "logs")
    logs.append("r1", "working\n")
    past = time.time() - 600
    os.utime(logs.path_for("r1"), (past, past))

    sliced = _output_of(_running("r1", "2026-10-21T00:00:00+00:00"), logs)

    assert sliced.quiet_seconds is not None and 590 <= sliced.quiet_seconds <= 700
    assert sliced.quiet_measured_at is not None and abs(sliced.quiet_measured_at - time.time()) < 60
    assert sliced.log_capped is False


def test_before_any_output_the_silence_is_counted_from_the_start(tmp_path):
    from datetime import datetime, timedelta, timezone

    logs = RunLogStore(tmp_path / "logs")
    started = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()

    sliced = _output_of(_running("r2", started), logs)

    assert sliced.quiet_seconds is not None and 170 <= sliced.quiet_seconds <= 250


def test_an_unreadable_transcript_or_a_settled_run_claims_no_silence(tmp_path):
    from types import SimpleNamespace

    from agora.execution.models import RunState

    logs = RunLogStore(tmp_path / "logs")
    logs.append("r3", "done\n")
    settled = SimpleNamespace(**{**vars(_running("r3", "2026-10-21T00:00:00+00:00")), "state": RunState.SUCCEEDED})
    assert _output_of(settled, logs).quiet_seconds is None

    from agora.execution.runlog import RunLogSlice

    class _Unreadable:
        def read(self, run_id, offset=0):
            return RunLogSlice(text="", offset=0, next_offset=0, reachable=False, unreachable_reason="PermissionError")

    sliced = _output_of(_running("r4", "2026-10-21T00:00:00+00:00"), _Unreadable())
    assert sliced.reachable is False
    assert sliced.quiet_seconds is None  # "could not read" never becomes "silent for N minutes"
    assert sliced.quiet_measured_at is None


def test_a_capped_transcript_says_so(tmp_path):
    logs = RunLogStore(tmp_path / "logs")
    chunk = "x" * 100_000
    written = 0
    while written < MAX_LOG_BYTES + 200_000:
        logs.append("r5", chunk)
        written += len(chunk)
    assert logs.read("r5").capped is True
    assert logs.read("r5", 0).written_at is not None
