"""W8's first acceptance line, as something that can be re-run.

> 打开 Agora 进到一个任务，**像用本地 gpt/codex 一样连着说三轮**：说一句它接着做，
> 再说一句它记得上一句，中途看得到它在改哪些文件。

Three turns against the **real** codex CLI, in a throwaway git worktree:

1. give it a word to remember;
2. ask what the word was — the answer proves the session carried context, which no
   amount of unit testing of argv can establish; and
3. ask it to write that word to a file — proving the resumed turn can still edit
   (the reason ``codex exec resume`` needs its sandbox as a config override) and
   that "which files it changed" shows up mid-conversation.

Marked ``integration`` so the ordinary gate never spends the user's quota. Run it
deliberately::

    ./.venv/Scripts/python.exe -m pytest tests/test_session_continuity_live.py -m integration -s

**It does not read the file the agent wrote.** Measured twice on 2026-09-08, on two
volumes: when codex writes through a shell inside its Windows ``workspace-write``
sandbox, the file lands with a DACL this process cannot open at all — persistently,
and ``Get-Acl`` is denied too, so it is a permission and not a transient lock.
Recorded as CR-43, because it means Agora's *deliverable content* reads can hit the
same wall. This test asserts the file exists and that the conversation showed it
being changed; whether the remembered word survived is settled by turn two's answer,
which needs no filesystem at all.

It is evidence, not a guard: it costs real tokens and depends on a signed-in CLI,
so it is not something CI can hold. What it replaces is a screenshot — a claim
that the loop worked, with no way for the next session to check.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys

import pytest

from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.models import CreateRunRequest, RunState
from agora.execution.sessions import MODE_RESUME, MODE_START, SESSION_ACTIVE
from agora.execution.store import ExecutionStore
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore
from agora.workspaces.naming import worktree_branch

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("git") is None, reason="git provisions the worktree"),
    pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI is the subject"),
]

GIT = shutil.which("git")
_GIT_ID = ("-c", "user.email=t@t", "-c", "user.name=t")


#: A word with no reason to appear in any other context, so finding it in turn
#: two's answer cannot be a coincidence or a model's general knowledge.
WORD = "kingfisher-8213"

#: Generous: a real agent turn does model calls and tool calls.
TURN_TIMEOUT = 600


def _git(cwd, *args):
    subprocess.run([GIT, "-C", str(cwd), *args], check=True, timeout=30, capture_output=True)


def _system(tmp_path):
    """A project, a provisioned worktree, and a dispatcher wired to the real CLIs."""
    root = tmp_path / "alpha"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, *_GIT_ID, "add", "-A")
    _git(root, *_GIT_ID, "commit", "-q", "-m", "seed")
    (tmp_path / "workspaces").mkdir()
    workspace = tmp_path / "workspaces" / "codex"
    _git(root, "worktree", "add", "-q", "-b", worktree_branch("alpha", "codex"), str(workspace), "HEAD")

    config = {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "root": str(root),
                    "workspaces": {
                        "codex": str(workspace),
                        "claude": str(tmp_path / "workspaces" / "claude"),
                        "kiro": str(tmp_path / "workspaces" / "kiro"),
                    },
                }
            },
        },
        # No adapter overrides: this test is about the shipped commands. An
        # override would prove only that a stub can be resumed.
        "execution": {"allowed_workspace_roots": [str(tmp_path / "workspaces")]},
    }
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    dispatcher = ExecutionDispatcher(
        store, ProjectRegistry(config), build_adapter_registry(config),
        max_concurrent_global=1, max_concurrent_per_project=1,
        allowed_workspace_roots=[tmp_path / "workspaces"],
    )
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Session continuity"))
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
            (TaskState.PLANNED.value, task.task_id),
        )
    return dispatcher, tasks, task.task_id, workspace


async def _turn(dispatcher: ExecutionDispatcher, tasks: TaskStore, task_id: str, prompt: str):
    # Re-read the task every turn. The first run carries it planned -> running,
    # which bumps its version, and ``create`` refuses a stale one — correctly: a
    # queued run outlives the state it was checked against. The conversation
    # window reloads the task for the same reason.
    task = tasks.get(task_id)
    run = dispatcher.queue(
        CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt=prompt,
            timeout_seconds=TURN_TIMEOUT, expected_task_version=task.version,
        )
    )
    settled = await asyncio.wait_for(dispatcher.execute(run.run_id), timeout=TURN_TIMEOUT + 60)
    transcript = dispatcher.run_logs.read(run.run_id).text
    _show(f"\n--- turn: {prompt[:60]}\n{transcript}\n--- state={settled.state} exit={settled.exit_code}")
    return settled, transcript


def _show(text: str) -> None:
    """Print a transcript without letting the console's encoding fail the test.

    The transcript is UTF-8 and contains the markers this slice introduced (``›``,
    ``✎``); a Windows console at cp932 raises ``UnicodeEncodeError`` on them, and
    a *diagnostic* must never be the thing that fails a run whose assertions all
    passed. Learned by exactly that happening on the first attempt.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    sys.stdout.write("\n")


@pytest.mark.asyncio
async def test_three_turns_in_one_session(tmp_path):
    dispatcher, tasks, task_id, workspace = _system(tmp_path)

    first, first_text = await _turn(
        dispatcher, tasks, task_id,
        f"Remember this word for later: {WORD}. Reply with only: noted",
    )
    assert first.state == RunState.SUCCEEDED, first.error_message
    # The transcript opens with the user's own line, marked as a session start.
    assert first_text.startswith("› "), first_text[:80]

    session = dispatcher.sessions.get(task_id, "codex")
    assert session.state == SESSION_ACTIVE
    assert session.session_id, "codex announced no session id"
    assert dispatcher.sessions.for_run(first.run_id).mode == MODE_START
    opened = session.session_id

    second, second_text = await _turn(
        dispatcher, tasks, task_id,
        "What word did I ask you to remember? Reply with only that word.",
    )
    assert second.state == RunState.SUCCEEDED, second.error_message
    # THE acceptance line: the second turn knew the first one.
    assert WORD in second_text, f"context did not carry over:\n{second_text}"
    # …and it carried over by resuming, not by anything this test replayed.
    assert second_text.startswith("›› "), second_text[:80]
    assert dispatcher.sessions.for_run(second.run_id).mode == MODE_RESUME
    assert dispatcher.sessions.get(task_id, "codex").session_id == opened

    third, third_text = await _turn(
        dispatcher, tasks, task_id,
        "Write that word, and nothing else, into a file named remembered.txt in the current directory.",
    )
    assert third.state == RunState.SUCCEEDED, third.error_message
    written = workspace / "remembered.txt"
    # A resumed turn can still edit. Without `-c sandbox_mode=...` on the resume
    # command this is the assertion that fails, and it fails *quietly* in the
    # product: the agent explains it cannot write and the conversation looks fine.
    assert written.is_file(), f"resumed turn could not write:\n{third_text}"
    # The file's **content** is deliberately not asserted. Measured on 2026-09-08:
    # when codex writes through a shell inside its Windows sandbox, the file lands
    # with a DACL this process cannot open at all — persistently, and `Get-Acl` is
    # denied too. That is CR-43, and asserting bytes here would make this test fail
    # for a reason that has nothing to do with session continuity. What the word
    # actually got remembered is already proven by turn two's answer.
    #
    # What *is* asserted is what Agora shows the user mid-conversation:
    assert "remembered.txt" in third_text
    assert "✎" in third_text, f"no file-change line in the transcript:\n{third_text}"

    session = dispatcher.sessions.get(task_id, "codex")
    assert session.turns == 3, session
    assert session.session_id == opened

    # The run's own record still holds both, so the conversation did not replace
    # the audit trail — it contains it. Asserted separately, because they are two
    # different claims: the comment used to say "deliverable" and the assertion
    # read `usage`, so a total failure of `snapshot_changes` would have passed
    # this test as long as codex still reported its tokens.
    assert third.result_metadata.get("usage") is not None, (
        "usage was not captured from codex --json"
    )
    delivered = dispatcher.store.deliverable_snapshot(third.run_id)
    assert delivered is not None, "the run recorded no delivery at all"
    # What it *says* is left to CR-43: a file codex wrote through its sandbox can
    # be unreadable to this process, so the snapshot may honestly record it as
    # unknown. That a snapshot was taken and stored is the part this test can
    # prove, and the part that regressed when nobody was asserting it.
    assert "files" in delivered, delivered
