"""CR-120 / CR-121: a codex review that runs out of quota is handed to kiro, and a
review failure is reported in words a person reads.

Found by the owner on 2026-09-25: the codex review hit its usage limit, settled as
"process exited with a non-zero status", the task stopped, and the card showed the
raw event stream. The rule the owner set: when codex is out of quota, kiro reviews
instead — one substitute, never a second opinion beside it.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys

from agora.attention.models import AttentionKind, AttentionState
from agora.attention.store import AttentionStore
from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher, _nonzero_reason
from agora.execution.store import ExecutionStore
from agora.execution.models import CreateRunRequest, ROLE_REVIEW, RunState
from agora.execution.review_fallback import is_usage_limit, readable_failure, retry_at
from agora.execution.transcript import CodexJsonlReader
from agora.projects import ProjectRegistry
from agora.tasks.store import TaskStore

from tests.test_execution import _config, _planned_task, _settled_worker_run

QUOTA = (
    "agent reported: You’ve hit your usage limit. Upgrade to Pro "
    "(https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage "
    "to purchase more credits or try again at 8:44 PM."
)


def _system(tmp_path, *, kiro: bool = True):
    ok = [sys.executable, "-c", "print('ok')"]
    config = _config(tmp_path, ok)
    config["execution"]["adapters"]["kiro"] = (
        {"command": [*ok, "{prompt}"], "workspace_key": "kiro"} if kiro else {"enabled": False}
    )
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    dispatcher = ExecutionDispatcher(
        store, ProjectRegistry(config), build_adapter_registry(config),
        max_concurrent_global=2, max_concurrent_per_project=1,
        allowed_workspace_roots=[tmp_path / "workspaces"], auto_review=True,
    )
    return tasks, store, dispatcher


def _failed_review(dispatcher, store, task_id, worker_run_id, *, adapter="codex", error=QUOTA):
    review = dispatcher.queue(
        CreateRunRequest(task_id=task_id, adapter=adapter, prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker_run_id,
    )
    running = store.start(review.run_id, expected_version=review.version)
    return store.finish(review.run_id, RunState.FAILED, expected_version=running.version,
                        exit_code=1, stdout_tail='{"type":"turn.failed"}', stderr_tail="",
                        error_message=error)


def _reviews_of(store, task_id, worker_run_id):
    return [r for r in store.list(task_id=task_id)
            if r.role == ROLE_REVIEW and r.reviews_run_id == worker_run_id]


def _after(dispatcher, finished, worker_run_id):
    # The substitute is queued, not launched: cancelling a launched review would
    # itself raise the review-owed blocker, which is not what is under test.
    dispatcher.schedule = lambda run_id: None
    asyncio.run(dispatcher._after_run(finished, ROLE_REVIEW, worker_run_id))


def test_a_codex_review_out_of_quota_is_handed_to_kiro(tmp_path):
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id)

    _after(dispatcher, failed, worker.run_id)

    reviews = _reviews_of(store, task.task_id, worker.run_id)
    assert sorted(r.adapter for r in reviews) == ["codex", "kiro"]
    # A substitute, not a blocker: the task is not stopped while kiro reviews.
    assert AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN) == []


def test_the_substitute_approval_says_whose_place_it_took(tmp_path):
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id)
    _after(dispatcher, failed, worker.run_id)
    kiro = next(r for r in _reviews_of(store, task.task_id, worker.run_id) if r.adapter == "kiro")
    current = store.require(kiro.run_id)
    assert current.state == RunState.QUEUED
    running = store.start(kiro.run_id, expected_version=current.version)
    done = store.finish(kiro.run_id, RunState.SUCCEEDED, expected_version=running.version,
                        exit_code=0, stdout_tail="looks right", stderr_tail="")
    dispatcher._raise_review_attention(done, worker.run_id, AttentionKind.APPROVAL)
    _assert_substitute_title(tasks, task.task_id)


def _assert_substitute_title(tasks, task_id):
    approvals = [i for i in AttentionStore(tasks).list(task_id=task_id)
                 if i.kind == AttentionKind.APPROVAL]
    assert len(approvals) == 1
    title = approvals[0].title
    assert "评审:kiro" in title and "顶替 codex" in title and "额度用完" in title


def test_a_review_that_failed_for_another_reason_is_not_substituted(tmp_path):
    """A crash may be telling us something; asking another vendor would bury it."""
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id,
                            error="process exited with a non-zero status")
    _after(dispatcher, failed, worker.run_id)

    assert [r.adapter for r in _reviews_of(store, task.task_id, worker.run_id)] == ["codex"]
    blocker = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert blocker.kind == AttentionKind.BLOCKER and blocker.run_id == worker.run_id


def test_without_kiro_the_blocker_says_quota_and_when_it_resets(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, kiro=False)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id)
    _after(dispatcher, failed, worker.run_id)

    blocker = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert "codex 额度用完" in blocker.body and "8:44 PM" in blocker.body
    # Not the raw stream, and not the bare exit-status sentence.
    assert "turn.failed" not in blocker.body
    assert "non-zero status" not in blocker.body
    # What answering it means is stated: the work is accepted unreviewed.
    assert "没有经过复核" in blocker.body


def test_a_substitute_is_dispatched_once_per_worker_run(tmp_path):
    """One hop: a second quota failure on the same worker run ends in the blocker."""
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    first = _failed_review(dispatcher, store, task.task_id, worker.run_id)
    _after(dispatcher, first, worker.run_id)
    kiro = next(r for r in _reviews_of(store, task.task_id, worker.run_id) if r.adapter == "kiro")
    current = store.require(kiro.run_id)
    running = store.start(kiro.run_id, expected_version=current.version)
    store.finish(kiro.run_id, RunState.FAILED, expected_version=running.version,
                 exit_code=1, stdout_tail="", stderr_tail="", error_message="quota")
    again = _failed_review(dispatcher, store, task.task_id, worker.run_id)
    _after(dispatcher, again, worker.run_id)

    assert sorted(r.adapter for r in _reviews_of(store, task.task_id, worker.run_id)) == [
        "codex", "codex", "kiro",
    ]
    open_items = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)
    assert [i.kind for i in open_items] == [AttentionKind.BLOCKER]


def test_codex_turn_failed_becomes_the_run_reason():
    reader = CodexJsonlReader()
    message = QUOTA.removeprefix("agent reported: ")
    shown = reader.feed(json.dumps({"type": "turn.failed", "error": {"message": message}}))
    assert shown.startswith("✖ You’ve hit your usage limit")
    assert reader.vendor_error == message
    assert _nonzero_reason(reader) == f"agent reported: {message}"
    assert _nonzero_reason(CodexJsonlReader()) == "process exited with a non-zero status"


def test_the_quota_sentence_is_read_the_way_a_person_reads_it():
    assert is_usage_limit(QUOTA) and retry_at(QUOTA) == "8:44 PM"
    assert readable_failure("codex", QUOTA) == "codex 额度用完（它说 8:44 PM 之后可以再用）"
    assert not is_usage_limit("process exited with a non-zero status")
    assert readable_failure("codex", "timeout after 600s") == "timeout after 600s"


def test_a_missing_worktree_is_said_in_words_and_keeps_the_original():
    raw = "workspace is not a provisioned worktree for this project/adapter; provision it before running"
    said = readable_failure("codex", raw)
    assert said.startswith("codex 的工作目录没有建好，运行没能开始")
    assert raw in said


def test_a_substitute_that_cannot_launch_still_leaves_a_gate(tmp_path):
    """The codex review of CR-121: kiro configured but its worktree not usable at launch.

    The substitute is queued (so the codex blocker is not raised), then fails before
    launch. Pre-launch refusals settle without ``_after_run``; without the gate for
    them the task refused every worker run with nothing in the inbox until restart.

    Queueing a review now provisions the reviewer's worktree (CR-61), so the worktree
    is removed after queueing to reach the pre-launch refusal this test is about.
    """
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id)
    _after(dispatcher, failed, worker.run_id)
    kiro = next(r for r in _reviews_of(store, task.task_id, worker.run_id) if r.adapter == "kiro")
    shutil.rmtree(tmp_path / "workspaces" / "kiro")

    settled = asyncio.run(dispatcher.execute(kiro.run_id))

    assert settled.state == RunState.FAILED  # tmp_path/workspaces/kiro is gone
    open_items = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)
    assert [i.kind for i in open_items] == [AttentionKind.BLOCKER]
    assert open_items[0].run_id == worker.run_id
    assert "kiro 的复核没有完成" in open_items[0].body
    assert store.reviews_owed(task_id=task.task_id) == []


def test_the_substitute_never_reviews_its_own_work(tmp_path):
    """A kiro worker reviewed by codex: when codex is out of quota, kiro may not stand in."""
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="kiro", prompt="implement it",
        timeout_seconds=10, expected_task_version=2,
    ))
    running = store.start(worker.run_id, expected_version=worker.version)
    store.finish(worker.run_id, RunState.SUCCEEDED, expected_version=running.version,
                 exit_code=0, stdout_tail="implemented it", stderr_tail="")
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id)
    _after(dispatcher, failed, worker.run_id)

    assert [r.adapter for r in _reviews_of(store, task.task_id, worker.run_id)] == ["codex"]
    blocker = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert blocker.kind == AttentionKind.BLOCKER and "codex 额度用完" in blocker.body


def test_the_substitute_reviews_what_the_failed_review_was_shown(tmp_path):
    """kiro round 3: the worktree is shared, so rebuilding the prompt could show kiro a
    later version than the one the approval is bound to. It gets the same prompt."""
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id)
    _after(dispatcher, failed, worker.run_id)
    kiro = next(r for r in _reviews_of(store, task.task_id, worker.run_id) if r.adapter == "kiro")
    assert store.require(kiro.run_id).prompt == store.require(failed.run_id).prompt == "review it"


def test_a_transient_rate_limit_or_a_full_disk_is_not_an_allowance(tmp_path):
    for said in ("agent reported: rate limit exceeded; retry in 1 second",
                 "agent reported: Disk quota exceeded"):
        assert not is_usage_limit(said)
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    failed = _failed_review(dispatcher, store, task.task_id, worker.run_id,
                            error="agent reported: Disk quota exceeded")
    _after(dispatcher, failed, worker.run_id)
    assert [r.adapter for r in _reviews_of(store, task.task_id, worker.run_id)] == ["codex"]


def test_a_generic_failure_reports_what_stderr_said():
    from agora.execution.review_fallback import failure_reason
    assert failure_reason("process exited with a non-zero status", "noise\nauthentication expired\n") == "authentication expired"
    assert failure_reason("agent reported: boom", "whatever") == "agent reported: boom"
    assert failure_reason(None, "") is None
