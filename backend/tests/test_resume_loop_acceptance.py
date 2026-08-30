"""W0(b): can I pick up yesterday's work on a real task, in Agora, and continue it?

This is the falsification slice's acceptance, written as one demonstration rather
than as unit coverage of its parts. Every step below is the production path — the
task is created and moved through the real lifecycle API, the worktree is
provisioned by the real provisioner through the real endpoint, the run is
dispatched with the body ``continue-action.tsx`` posts, and it settles through
``ExecutionDispatcher.execute`` with the deliverable snapshot the dispatcher
freezes. Nothing is hand-inserted: no row is written by SQL, no run is settled by
calling ``store.finish`` directly, and the recap is read over HTTP the way the Task
Workspace reads it.

Only two things are stand-ins, and both are named:

- The agent is a trivial local process instead of Codex, so the test is fast and
  deterministic. It is reached through the ordinary CLI adapter, so everything
  around it — the launch, the readiness gate, the baseline, the snapshot, the
  settle — is what a real Codex run gets.
- The clock is pinned past the display zone's midnight rather than waited out.

What it proves, in the slice's own words: the run's outcome is written back, and
the next day, opening the task shows yesterday's step. Three endings are checked,
because "delivered" is not the only way a day can end and the three must read
differently: a run that delivered files, a run that changed nothing, and a run
that failed.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from agora.api.app import app
from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ROLE_KEY, ROLE_WORKER
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.models import TERMINAL_RUN_STATES
from agora.execution.router import get_execution_dispatcher, get_execution_store
from agora.execution.store import ExecutionStore
from agora.projects import ProjectRegistry
from agora.tasks import localday
from agora.tasks.localday import local_day_bounds, local_day_of, parse_offset
from agora.tasks.recap import ITEM_KINDS
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskStore
from agora.workspaces.provisioner import WorkspaceProvisioner
from agora.workspaces.router import get_workspace_provisioner

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required to provision run worktrees")

_GIT_ID = ("-c", "user.email=t@t", "-c", "user.name=t")

# The adapter the Continue action dispatches. Mirrors CONTINUE_ADAPTER in
# frontend/src/lib/continue-run.ts; the timeout mirrors CONTINUE_TIMEOUT_SECONDS.
_ADAPTER = "codex"
_TIMEOUT_SECONDS = 600

# The stand-in agent. One command for all three runs, branching on the prompt it
# was given, so the *adapter* is configured once and each run's ending comes from
# what that run was actually asked to do — as it would with a real runtime.
_AGENT = r"""
import pathlib, sys
prompt = sys.argv[-1]
if "OUTCOME:deliver" in prompt:
    pathlib.Path("notes.md").write_text("continued from yesterday\n", encoding="utf-8")
    pathlib.Path("plan.txt").write_text("next: finish the loop\n", encoding="utf-8")
    print("wrote notes.md and plan.txt")
elif "OUTCOME:no-op" in prompt:
    print("read the code; nothing needed changing")
else:
    print("could not continue: the repository is not what the task assumed", file=sys.stderr)
    sys.exit(3)
"""

# The seeds the Continue action would send. Shaped like seedContinuePrompt's output
# (goal, where you left off, next step) so what reaches the adapter is a prompt of
# the kind the product actually dispatches, not a bare token.
_SEEDS = {
    "deliver": (
        "Goal\nKeep the resume notes current\n\n"
        "Where you left off\nYesterday\n- A run delivered work.\n\n"
        "Next step\nContinue the work.\n\nOUTCOME:deliver"
    ),
    "no-op": (
        "Goal\nCheck whether the fix is still needed\n\n"
        "Where you left off\nYesterday\n- A run delivered work.\n\n"
        "Next step\nContinue the work.\n\nOUTCOME:no-op"
    ),
    "fail": (
        "Goal\nApply the migration\n\n"
        "Where you left off\nYesterday\n- A run delivered work.\n\n"
        "Next step\nContinue the work.\n\nOUTCOME:fail"
    ),
}


# The display zone is chosen, not fixed, and this is not incidental. Pinning one
# offset puts the zone's midnight at some fixed instant of the UTC day, and once a
# day the suite itself would straddle it: the task would be created before the
# boundary and its run settle after, so "yesterday's work" would be split across
# two days and the demonstration would fail for a reason that has nothing to do
# with what it tests. Choosing the offset that makes *now* midday leaves roughly
# twelve hours of headroom on either side, so the day arithmetic below is
# deterministic at every hour the suite may run.
def _display_zone() -> tuple[timezone, int]:
    """``(zone, offset_minutes)`` placing the current instant near local midday."""
    hours = 12 - datetime.now(timezone.utc).hour  # 12 .. -11, all legal offsets
    sign = "+" if hours >= 0 else "-"
    return parse_offset(f"{sign}{abs(hours):02d}:00"), hours * 60


def _git(cwd: Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(cwd), *args], check=True, timeout=30, capture_output=True)


def _real_git_project(tmp_path: Path) -> Path:
    """A real project folder: its own repository, with a commit, as a user's would
    be. The agent worktree is deliberately NOT created here — provisioning it is
    part of what the demonstration exercises."""
    root = tmp_path / "alpha"
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "README.md").write_text("alpha\n", encoding="utf-8")
    _git(root, *_GIT_ID, "add", "-A")
    _git(root, *_GIT_ID, "commit", "-q", "-m", "seed")
    return root


def _config(tmp_path: Path) -> dict:
    workspaces = tmp_path / "workspaces"
    return {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "root": str(tmp_path / "alpha"),
                    "workspaces": {
                        "codex": str(workspaces / "codex"),
                        "claude": str(workspaces / "claude"),
                        "kiro": str(workspaces / "kiro"),
                    },
                }
            },
        },
        "execution": {
            "max_concurrent_global": 2,
            "max_concurrent_per_project": 1,
            "allowed_workspace_roots": [str(workspaces)],
            "adapters": {
                # The trivial agent, reached through the ordinary CLI adapter.
                "codex": {"command": [sys.executable, "-c", _AGENT, "{prompt}"],
                          "workspace_key": "codex"},
                "claude": {"enabled": False},
                "kiro": {"enabled": False},
            },
        },
    }


def _system(tmp_path: Path):
    """The real stores, registry, dispatcher and provisioner over a temporary
    project — the same objects the app builds from config, wired to a scratch
    database instead of the configured one."""
    _real_git_project(tmp_path)
    config = _config(tmp_path)
    roots = [Path(value).resolve() for value in config["execution"]["allowed_workspace_roots"]]
    tasks = TaskStore(tmp_path / "agora.db")
    runs = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    dispatcher = ExecutionDispatcher(
        runs, projects, build_adapter_registry(config),
        max_concurrent_global=2, max_concurrent_per_project=1,
        allowed_workspace_roots=roots,
    )
    provisioner = WorkspaceProvisioner(projects, allowed_workspace_roots=roots)
    return tasks, runs, dispatcher, projects, provisioner


def _install(tasks, runs, dispatcher, projects, provisioner) -> None:
    app.dependency_overrides[get_task_store] = lambda: tasks
    app.dependency_overrides[get_execution_store] = lambda: runs
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_project_registry] = lambda: projects
    app.dependency_overrides[get_workspace_provisioner] = lambda: provisioner


async def _planned_task(client: httpx.AsyncClient, title: str) -> dict:
    """A task on the board, walked to `planned` the way the product walks it.

    `planned` is one of the two states the Continue action is offered in
    (CONTINUABLE_TASK_STATES), and there is no shortcut to it: the control plane
    refuses `requirements -> design` without an approved spec, so the walk goes
    through the requirements interview and a human approval. Deliberately not
    forced by SQL — a task that arrived at `planned` by a direct write would not
    carry the events the recap reads, and the demonstration would be reading a
    history nothing produced.
    """
    created = await client.post("/api/tasks", json={"project_id": "alpha", "title": title})
    assert created.status_code == 201, created.text
    task = created.json()

    moved = await client.patch(
        f"/api/tasks/{task['task_id']}/state",
        json={"target_state": "requirements", "actor": "user", "expected_version": task["version"]},
    )
    assert moved.status_code == 200, moved.text
    task = moved.json()

    spec = await client.post(f"/api/tasks/{task['task_id']}/specs", json={
        "title": title,
        "summary": "Carried over from yesterday.",
        "functional": [{"requirement_id": "R1", "statement": f"{title} is done."}],
        "acceptance_scenarios": [{
            "scenario_id": "A1", "requirement_ids": ["R1"],
            "given": "the task was left part-way",
            "when": "the work is continued",
            "then": "the task moves forward",
        }],
        "created_by": "user",
    })
    assert spec.status_code == 201, spec.text
    draft = spec.json()
    approved = await client.post(
        f"/api/specs/{draft['spec_id']}/approve",
        json={"expected_revision": draft["revision"], "actor": "user"},
    )
    assert approved.status_code == 200, approved.text

    for target in ("design", "planned"):
        current = await client.get(f"/api/tasks/{task['task_id']}")
        assert current.status_code == 200, current.text
        moved = await client.patch(
            f"/api/tasks/{task['task_id']}/state",
            json={"target_state": target, "actor": "user",
                  "expected_version": current.json()["version"]},
        )
        assert moved.status_code == 200, moved.text
        task = moved.json()
    assert task["state"] == "planned"
    return task


async def _provision_the_worktree(client: httpx.AsyncClient) -> None:
    """What the Continue action does when the worktree does not exist yet: read the
    status, see `missing` on a git project (workspaceReadiness -> provisionable),
    provision, and refuse to go on unless the result is exactly `ready`
    (provisionResultGate)."""
    before = await client.get(f"/api/workspaces/alpha/{_ADAPTER}")
    assert before.status_code == 200, before.text
    assert before.json()["state"] == "missing"
    assert before.json()["source_is_git"] is True

    result = await client.post(
        "/api/workspaces/provision", json={"project_id": "alpha", "adapter": _ADAPTER}
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["created"] is True
    assert body["status"]["state"] == "ready", body


async def _continue_run(client: httpx.AsyncClient, task: dict, prompt: str) -> dict:
    """Dispatch exactly as the Continue action does: POST /api/runs with the
    adapter it pins, the seeded prompt, its timeout, the task version in hand, and
    the human as actor. Goes through ExecutionStore.create, so every create-time
    gate applies."""
    created = await client.post("/api/runs", json={
        "task_id": task["task_id"],
        "adapter": _ADAPTER,
        "prompt": prompt,
        "timeout_seconds": _TIMEOUT_SECONDS,
        "expected_task_version": task["version"],
        "actor": "user",
    })
    assert created.status_code == 201, created.text
    return created.json()


async def _settled(client: httpx.AsyncClient, run_id: str, *, timeout: float = 90.0) -> dict:
    """Let the run settle the way it settles in production — the dispatcher task
    the create endpoint scheduled, running on this loop — and read the record back
    over HTTP."""
    terminal = {state.value for state in TERMINAL_RUN_STATES}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["state"] in terminal:
            return run
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} did not settle within {timeout}s")


def _pin_the_next_morning(monkeypatch, zone, settled_day: str) -> datetime:
    """Advance the clock past the display zone's midnight, without sleeping: the
    morning after the day the run settled."""
    _, next_midnight = local_day_bounds(settled_day, zone=zone)
    pinned = datetime.fromisoformat(next_midnight) + timedelta(hours=9)
    monkeypatch.setattr(localday, "now_utc", lambda: pinned)
    return pinned


def _run_items(recap: dict) -> list[dict]:
    """The recap items that are a run's outcome, as opposed to the day's other
    facts (the task being created, the stage it reached)."""
    kinds = {"delivered", "ran_no_changes", "run_failed", "reviewed"}
    return [item for item in recap["items"] if item["kind"] in kinds]


def test_yesterdays_step_is_what_the_task_shows_the_next_day(tmp_path, monkeypatch):
    """The W0(b) loop, end to end, three times over.

    Dispatch a real run through the live Continue path into the task's provisioned
    worktree, let it settle with its deliverable snapshot, advance the clock past
    the display zone's midnight, and read the recap the way the workspace view
    does. The recap must name *yesterday's* step: one day ago, on the day the run
    settled, reporting that run's real outcome.
    """
    zone, offset_minutes = _display_zone()
    # The display zone is configuration, so pin it rather than inherit the
    # machine's: "yesterday" has to be a claim about a stated wall clock.
    monkeypatch.setattr(localday, "configured_zone", lambda: zone)

    tasks, runs, dispatcher, projects, provisioner = _system(tmp_path)
    _install(tasks, runs, dispatcher, projects, provisioner)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agora.test") as client:
            await _provision_the_worktree(client)

            # ── Yesterday: three tasks are worked, and end three different ways ──
            worked: dict[str, dict] = {}
            for outcome, title in (
                ("deliver", "Write the resume notes"),
                ("no-op", "Check whether the fix is still needed"),
                ("fail", "Apply the migration"),
            ):
                task = await _planned_task(client, title)
                queued = await _continue_run(client, task, _SEEDS[outcome])
                settled = await _settled(client, queued["run_id"])
                delivered = await client.get(f"/api/runs/{queued['run_id']}/deliverables")
                assert delivered.status_code == 200, delivered.text
                worked[outcome] = {
                    "task": task, "run": settled, "deliverables": delivered.json(),
                }

            # The run really ran, in the provisioned worktree, and the outcomes are
            # the three the recap has to tell apart.
            deliver = worked["deliver"]
            assert deliver["run"]["state"] == "succeeded"
            assert deliver["run"]["exit_code"] == 0
            assert Path(deliver["run"]["workspace"]) == (tmp_path / "workspaces" / "codex")
            # The snapshot is the run's own frozen delivery record, taken by the
            # dispatcher as the run ended — not a live listing of the worktree.
            assert deliver["deliverables"]["source"] == "snapshot"
            assert sorted(f["path"] for f in deliver["deliverables"]["files"]) == [
                "notes.md", "plan.txt"
            ]
            assert (tmp_path / "workspaces" / "codex" / "notes.md").read_text(
                encoding="utf-8"
            ) == "continued from yesterday\n"

            no_op = worked["no-op"]
            assert no_op["run"]["state"] == "succeeded"
            # "Changed nothing" is knowledge here, not an absence: the worktree is
            # NOT clean — it still holds the first run's two files — and the
            # baseline is what keeps them out of this run's record.
            assert no_op["deliverables"]["source"] == "snapshot"
            assert no_op["deliverables"]["files"] == []

            failed = worked["fail"]
            assert failed["run"]["state"] == "failed"
            assert failed["run"]["exit_code"] == 3

            # Every run is a worker run, recorded as such by create. Role is what
            # separates a delivery from a review in the recap, so it is asserted as
            # a fact of the record rather than assumed.
            for entry in worked.values():
                assert entry["run"]["result_metadata"][ROLE_KEY] == ROLE_WORKER

            # All three settled on one day (they ran within the same test), which is
            # the day the recap has to name.
            settled_days = {
                local_day_of(entry["run"]["finished_at"], zone=zone) for entry in worked.values()
            }
            assert len(settled_days) == 1, settled_days
            yesterday = settled_days.pop()

            # ── The next morning ────────────────────────────────────────────────
            pinned = _pin_the_next_morning(monkeypatch, zone, yesterday)
            assert local_day_of(pinned.isoformat(), zone=zone) != yesterday

            recaps: dict[str, dict] = {}
            for outcome, entry in worked.items():
                response = await client.get(f"/api/tasks/{entry['task']['task_id']}/recap")
                assert response.status_code == 200, response.text
                recap = response.json()
                recaps[outcome] = recap

                # Yesterday, named as yesterday: the day the run settled, one day
                # ago. `days_ago == 1` is what the Resume Brief renders as its
                # "yesterday" heading.
                assert recap["day"] == yesterday, (outcome, recap)
                assert recap["days_ago"] == 1, (outcome, recap)
                assert recap["zone_offset_minutes"] == offset_minutes
                # The whole day was read, so what follows is the day, not a page of it.
                assert recap["partial"] is False
                assert recap["more"] == 0
                # And the response agrees with itself: the rollover it tells the
                # client to wait for is the end of the day it is calling today.
                today = (
                    datetime.fromisoformat(f"{recap['day']}T00:00:00").date()
                    + timedelta(days=recap["days_ago"])
                )
                assert recap["next_rollover"] == local_day_bounds(
                    today.isoformat(), zone=zone
                )[1]

            # Exactly one run outcome per day per task, and it is that run's own.
            delivered_item = _run_items(recaps["deliver"])
            assert delivered_item == [{
                "kind": "delivered",
                "summary": ITEM_KINDS["delivered"],
                "adapter": _ADAPTER,
                # Two files, because that run wrote two — cross-checked against its
                # snapshot below, so this is the run's real count and not a constant.
                "files": 2,
                "files_at_least": False,
            }], recaps["deliver"]
            assert delivered_item[0]["files"] == len(deliver["deliverables"]["files"])
            assert delivered_item[0]["adapter"] == deliver["run"]["adapter"]

            # The three read differently, which is the point of asserting all three:
            # a day that ended on a verified no-op does not say "delivered work",
            # and a day that ended on a failure does not either.
            assert _run_items(recaps["no-op"]) == [{
                "kind": "ran_no_changes",
                "summary": ITEM_KINDS["ran_no_changes"],
                "adapter": _ADAPTER,
            }], recaps["no-op"]
            assert _run_items(recaps["fail"]) == [{
                "kind": "run_failed",
                "summary": ITEM_KINDS["run_failed"],
                "adapter": _ADAPTER,
            }], recaps["fail"]

            # Not merely "something happened": the day reads as the day it was —
            # the task was created, its spec was approved, the run ended the way it
            # ended, and the task is where the run left it.
            for outcome, recap in recaps.items():
                kinds = [item["kind"] for item in recap["items"]]
                assert kinds[0] == "created", (outcome, kinds)
                assert "spec_approved" in kinds, (outcome, kinds)
                assert kinds[-1] == "stage_reached", (outcome, kinds)
                assert recap["items"][-1]["state"] == "running", (outcome, recap)

    try:
        asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()
