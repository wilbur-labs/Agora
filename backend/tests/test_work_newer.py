"""A newer conversation of the same task, opened outside the work page (workspace CR-86).

The work page remembers the conversations it launched. One the person started with
``cw open`` in a terminal — often in the task's primary_repo, so its transcript sits under
another directory — was invisible to it, and the page brought back an older one. The
transcript names its task in claude-workspace's SessionStart hook output; that, and the
work root beside it, is what claims it. Nothing else does.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agora.terminal.manager import shell_literal
from agora.work.projects import work_key
from agora.work.reports import BUSY_SECONDS, conversation_task, newer_task_conversation

from tests.test_work import _report, _seed, _service, _transcript, run, world  # noqa: F401  (fixtures)


def _hook(task: str, root: str, cwd: str) -> str:
    content = (f"# Active Task: {task}  ← 来自 $CW_TASK（cw open 钉死本窗口；/clear 也不变）\n\n"
               f"## 路径\n- 当前工作目录（项目仓库）：{cwd}\n"
               f"- 工具目录：E:/tool —— 命令都相对它\n"
               f"- 工作根：{root} —— `MEMORY.md`、`tasks/<name>/` 都相对它\n")
    return json.dumps({"type": "attachment", "cwd": cwd, "attachment": {
        "type": "hook_success", "hookName": "SessionStart:startup", "hookEvent": "SessionStart", "content": content,
    }}, ensure_ascii=False)


def _outside(claude: Path, *, task: str, root: str, cwd: str = "E:/repo", age: float = 3600,
             spoken: bool = True, extra: list[str] | None = None, folder: str = "E--repo") -> str:
    """A transcript written by a session the work page did not open, ``age`` seconds ago."""
    session = str(uuid.uuid4())
    path = claude / folder / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.time() - age
    lines = [_hook(task, root, cwd)] + (extra or [])
    if spoken:
        lines.append(json.dumps({"type": "user", "cwd": cwd, "timestamp": _iso(stamp),
                                 "message": {"role": "user", "content": "#17 为什么失败了"}}, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (stamp, stamp))
    return session


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _touch(path: Path, age: float = 0) -> None:
    """A write with nobody speaking: what resuming a conversation does to its file."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "attachment", "attachment": {"type": "hook_success"}}\n')
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def _recorded(service, claude: Path, key: str, work: Path, *, age: float = 7200) -> str:
    """The work page's own recovery point, ``age`` seconds old."""
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    path = _transcript(claude, sid, spoken=False)
    stamp = time.time() - age
    with path.open("a", encoding="utf-8") as handle:  # spoken in, at a time claude writes down
        handle.write(json.dumps({"type": "user", "timestamp": _iso(stamp),
                                 "message": {"role": "user", "content": "hi"}}) + "\n")
    os.utime(path, (stamp, stamp))
    _report(service, key, session=sid, transcript=path)
    return sid


def _resumed(service) -> str | None:
    """The session id the last launch passes to ``--resume``, or None for a fresh start."""
    joined = " ".join(service.manager.sessions[-1].argv)
    marker = shell_literal("--resume") + " "
    return joined.split(marker, 1)[1].split(" ", 1)[0].strip("'\"") if marker in joined else None


def _project(service, key):
    return next(p for p in service.view()["projects"] if p["key"] == key)


def test_a_newer_conversation_of_the_task_opened_outside_is_taken_and_said(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    _recorded(service, claude, key, work)
    newer = _outside(claude, task="alpha", root=str(work), cwd="E:/Work/2026/Projects/pleasanter-workflow")

    result = run(service.open(key))
    assert result["result"] == "resume"
    assert _resumed(service) == newer
    note = result["note"]
    assert note["kind"] == "newer" and note["session"] == newer
    assert note["cwd"] == "E:/Work/2026/Projects/pleasanter-workflow"
    assert _project(service, key)["launch"]["note"]["kind"] == "newer"
    # The launch's report confirms it against the conversation actually asked for.
    _report(service, key, session=newer, transcript=claude / "E--repo" / f"{newer}.jsonl", source="resume")
    assert _project(service, key)["launch"]["confirm"] == "resumed"


def test_without_a_record_the_conversation_opened_outside_is_taken(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    newer = _outside(claude, task="alpha", root=str(work).replace("\\", "/") + "/")
    result = run(service.open(key))
    assert result["result"] == "resume" and _resumed(service) == newer


def test_another_task_or_another_work_root_is_not_claimed(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    recorded = _recorded(service, claude, key, work)
    _outside(claude, task="beta", root=str(work))
    _outside(claude, task="alpha", root=str(tmp_path / "other-work"))
    _outside(claude, task="alphabet", root=str(work))
    result = run(service.open(key))
    assert result["result"] == "resume" and _resumed(service) == recorded and result["note"] is None


def test_the_last_session_start_decides_whose_conversation_it_is(world):
    """An unpinned window can change task at /clear: the conversation is the later task's."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    recorded = _recorded(service, claude, key, work)
    _outside(claude, task="alpha", root=str(work), extra=[_hook("beta", str(work), "E:/repo")])
    run(service.open(key))
    assert _resumed(service) == recorded


def test_one_still_being_written_is_named_not_taken(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    recorded = _recorded(service, claude, key, work)
    busy = _outside(claude, task="alpha", root=str(work), age=BUSY_SECONDS / 4)
    result = run(service.open(key))
    assert _resumed(service) == recorded
    assert result["note"]["kind"] == "newer_busy" and result["note"]["session"] == busy
    assert result["note"]["fallback"] == "resume"


def test_one_still_being_written_with_nothing_recorded_says_a_new_one_was_started(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    _outside(claude, task="alpha", root=str(work), age=BUSY_SECONDS / 4)
    result = run(service.open(key))
    assert _resumed(service) is None
    assert result["note"]["kind"] == "newer_busy" and result["note"]["fallback"] == "fresh"


def test_older_than_the_record_or_nobody_spoke_is_not_taken(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    recorded = _recorded(service, claude, key, work, age=600 + BUSY_SECONDS)
    _outside(claude, task="alpha", root=str(work), age=7200)
    _outside(claude, task="alpha", root=str(work), age=BUSY_SECONDS + 60, spoken=False)
    result = run(service.open(key))
    assert _resumed(service) == recorded and result["note"] is None


def test_a_transcript_that_cannot_be_read_is_passed_over(world, monkeypatch):
    tmp_path, work, claude = world
    good = _outside(claude, task="alpha", root=str(work), age=7200)
    bad = _outside(claude, task="alpha", root=str(work), age=3600)
    real = conversation_task

    def failing(path):
        if path.stem == bad:
            raise PermissionError("locked")
        return real(path)

    monkeypatch.setattr("agora.work.reports.conversation_task", failing)
    found = newer_task_conversation(claude, "alpha", str(work), after=0)
    assert found is not None and found.session_id == good


def test_the_work_root_and_cwd_are_read_from_the_hook(world):
    tmp_path, work, claude = world
    session = _outside(claude, task="alpha", root="E:/Work", cwd="E:/Work/2026/Projects/x")
    assert conversation_task(claude / "E--repo" / f"{session}.jsonl") == ("alpha", "E:/Work", "E:/Work/2026/Projects/x")


def test_a_conversation_left_while_busy_is_still_taken_once_quiet(world):
    """Resuming the recorded conversation writes into its file without anyone speaking;
    that must not make it 'newer' than the one left behind (codex review round 1)."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    recorded = _recorded(service, claude, key, work)
    left = _outside(claude, task="alpha", root=str(work), age=BUSY_SECONDS + 60)
    # The recorded conversation was resumed meanwhile: its file was written just now.
    _touch(next(claude.glob(f"*/{recorded}.jsonl")))
    result = run(service.open(key))
    assert _resumed(service) == left and result["note"]["kind"] == "newer"


def test_a_conversation_older_than_an_empty_record_is_not_called_newer(world):
    """After a /clear the recorded conversation is empty: what it was written after still
    bounds what is newer (codex review round 1)."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    run(service._hook_setup())
    _seed(service, key, work)
    sid = str(uuid.uuid4())
    path = _transcript(claude, sid, spoken=False)
    stamp = time.time() - (BUSY_SECONDS + 60)
    os.utime(path, (stamp, stamp))
    _report(service, key, session=str(uuid.uuid4()), transcript=_transcript(claude, "before"))
    _report(service, key, session=sid, transcript=path, source="clear")
    _outside(claude, task="alpha", root=str(work), age=7200)
    result = run(service.open(key))
    assert result["result"] == "fresh_empty" and result["note"] is None and _resumed(service) is None


def test_with_the_recorded_file_gone_the_time_it_was_recorded_bounds_newer(world):
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    recorded = _recorded(service, claude, key, work)
    service._read(service.store.get(key))  # the recovery point is moved, with its time
    next(claude.glob(f"*/{recorded}.jsonl")).unlink()
    _outside(claude, task="alpha", root=str(work), age=7200)  # spoke before it was recorded
    assert run(service.open(key))["note"] is None
    service.manager.close(service.manager.sessions[-1].terminal_id)
    run(service.close(key))
    later = time.time() - (BUSY_SECONDS + 60)
    with service.store._transaction() as db:
        db.execute("UPDATE work_sessions SET ck_at = ? WHERE key = ?", (later - 3600, key))
    newer = _outside(claude, task="alpha", root=str(work), age=BUSY_SECONDS + 60)
    assert run(service.open(key))["note"]["session"] == newer


def test_the_time_said_is_when_someone_last_spoke(world):
    tmp_path, work, claude = world
    session = _outside(claude, task="alpha", root=str(work), age=7200)
    path = next(claude.glob(f"*/{session}.jsonl"))
    _touch(path)
    found = newer_task_conversation(claude, "alpha", str(work), after=0)
    assert found.session_id == session and abs(found.spoken - (time.time() - 7200)) < 5
    assert found.modified > found.spoken + 3600


def _hook_text(content: str) -> str:
    return json.dumps({"type": "attachment", "attachment": {
        "type": "hook_success", "hookName": "SessionStart:clear", "hookEvent": "SessionStart", "content": content,
    }}, ensure_ascii=False)


def test_a_later_start_that_names_no_task_clears_the_task(world):
    """claude-workspace says ``(无)`` when the pointer is empty, and any other output that
    names no task and work root counts the same (codex review round 2); an empty output —
    a hook that prints nothing, like Agora's reporter — changes nothing."""
    tmp_path, work, claude = world
    none = "# Active Task: (无) ← 任务指针为空。\n- 工作根：" + str(work) + " —— x\n"
    cleared = _outside(claude, task="alpha", root=str(work), extra=[_hook_text(none)])
    other = _outside(claude, task="alpha", root=str(work), extra=[_hook_text("some other hook said this")])
    kept = _outside(claude, task="alpha", root=str(work), extra=[_hook_text("")], folder="E--kept")
    assert conversation_task(next(claude.glob(f"*/{cleared}.jsonl")))[0] == "(无)"
    assert conversation_task(next(claude.glob(f"*/{other}.jsonl"))) is None
    assert conversation_task(next(claude.glob(f"*/{kept}.jsonl")))[0] == "alpha"
    found = newer_task_conversation(claude, "alpha", str(work), after=0)
    assert found is not None and found.session_id == kept


def test_a_fresh_start_nobody_used_does_not_shut_out_the_conversation_left_busy(world):
    """No record, a busy conversation elsewhere: a new one is started and nobody speaks in
    it. Once the other is quiet, reopening takes it — the empty start bounds nothing
    (codex review round 3). An empty conversation after /clear still does (round 1)."""
    tmp_path, work, claude = world
    service = _service(tmp_path, work, claude)
    key = work_key(work, "alpha")
    left = _outside(claude, task="alpha", root=str(work), age=BUSY_SECONDS / 4)
    assert run(service.open(key))["note"]["fallback"] == "fresh"
    # That launch reports its new, empty conversation; the window is closed unused.
    empty = str(uuid.uuid4())
    _report(service, key, session=empty, transcript=_transcript(claude, empty, spoken=False), source="startup")
    service._read(service.store.get(key))
    run(service.close(key))
    # Later, the other window is quiet.
    stamp = time.time() - (BUSY_SECONDS + 60)
    os.utime(next(claude.glob(f"*/{left}.jsonl")), (stamp, stamp))
    result = run(service.open(key))
    assert _resumed(service) == left and result["note"]["kind"] == "newer"
