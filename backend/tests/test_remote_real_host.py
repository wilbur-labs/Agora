"""W3-a: a run dispatched to a remote adapter really executes on that host.

This is the round trip the U5 wiring commit could not do (`1787054`: "Not done
here: a real SSH round trip from this machine"). Everything else about the remote
path is covered by fakes in tests/test_remote_wiring.py — deliberately, since the
dispatcher's routing is what those verify. What fakes structurally cannot verify
is whether the *other side* agrees: a server that refuses an SSH env request, a
worktree that git will not create, a snapshot whose digests come from a real file.

Marked `integration`, so it is deselected by default (`-m "not integration"`) and
skipped outright unless the host is configured. Nothing here names a host, an
account or a path: the target is private, and this repository's remote is not.

    AGORA_SSH_TEST_HOST      host/IP of the test box
    AGORA_SSH_TEST_USER      ssh user
    AGORA_SSH_TEST_PORT      optional, default 22
    AGORA_SSH_TEST_ROOT      pre-created remote project root (D2 form-2: Agora
                             never creates the root)
    AGORA_SSH_PASSWORD_test  credential, named by secret_ref
      | AGORA_SSH_KEY_test
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath

import pytest

from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.models import CreateRunRequest, REMOTE_TARGET_KEY, RunState
from agora.execution.remote_wiring import remote_components_for
from agora.execution.store import ExecutionStore
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore

pytestmark = pytest.mark.integration


def _target() -> dict[str, str]:
    host = os.environ.get("AGORA_SSH_TEST_HOST")
    user = os.environ.get("AGORA_SSH_TEST_USER")
    root = os.environ.get("AGORA_SSH_TEST_ROOT")
    secret_ref = "AGORA_SSH_KEY_test" if os.environ.get("AGORA_SSH_KEY_test") else "AGORA_SSH_PASSWORD_test"
    if not (host and user and root and os.environ.get(secret_ref)):
        pytest.skip(
            "integration: set AGORA_SSH_TEST_HOST / AGORA_SSH_TEST_USER / "
            "AGORA_SSH_TEST_ROOT / AGORA_SSH_PASSWORD_test (or AGORA_SSH_KEY_test) "
            "to run against a real host"
        )
    return {"host": host, "user": user, "root": root, "secret_ref": secret_ref}


# Form A: a deterministic remote command, not a real agent CLI. What is under test is
# Agora's chain to the host; whether an agent on that host happens to be signed in is
# a property of the host, and letting it decide this test's outcome would test that
# instead. It echoes its identity env so a server that drops those variables fails
# here (CR-22) rather than silently leaving remote agents without a run id.
REMOTE_SCRIPT = (
    'printf "run=%s\\n" "$AGORA_RUN_ID"; '
    'printf "task=%s\\n" "$AGORA_TASK_ID"; '
    'printf "project=%s\\n" "$AGORA_PROJECT_ID"; '
    # Fail closed if the carrier ever regresses: when AGORA_RUN_ID is empty (the CR-22
    # symptom) the run must exit non-zero, NOT write a ".probe" (empty-prefix) file the
    # finally-cleanup — keyed on run_id — could never delete. So the guard fails loudly
    # instead of silently leaving litter in the worktree.
    'test -n "$AGORA_RUN_ID" || { echo "AGORA_RUN_ID empty" >&2; exit 3; }; '
    'printf "made by %s\\n" "$AGORA_RUN_ID" > "$AGORA_RUN_ID.probe"'
)


def _config(tmp_path: Path, target: dict[str, str]) -> dict:
    local = tmp_path / "alpha"
    workspace = f"{target['root']}/.agora/workspaces/claude"
    return {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha", "worker_agent": "claude", "reviewer_agent": "claude",
                    # The registry resolves every path on THIS machine (CR-89); the remote
                    # paths the dispatcher uses come from the adapter's ssh section.
                    "root": str(local),
                    "workspaces": {"claude": str(local / "workspaces" / "claude")},
                }
            },
        },
        "execution": {
            "max_concurrent_global": 1,
            "max_concurrent_per_project": 1,
            "allowed_workspace_roots": [str(local / "workspaces")],
            "adapters": {
                "claude": {
                    "command": ["sh", "-c", REMOTE_SCRIPT, "sh", "{prompt}"],
                    "workspace_key": "claude",
                    "bridge_mode": "ssh",
                    "ssh": {
                        "ssh_host": target["host"],
                        "ssh_port": int(os.environ.get("AGORA_SSH_TEST_PORT", "22")),
                        "ssh_user": target["user"],
                        "secret_ref": target["secret_ref"],
                        "remote_workspace": workspace,
                        "remote_project_root": target["root"],
                        # Pin on first sight into a known_hosts of this test's own, so a
                        # run here neither depends on nor edits the user's file.
                        "host_key_mode": "tofu",
                        "known_hosts_path": str(tmp_path / "known_hosts"),
                    },
                }
            },
        },
    }


def test_a_run_on_a_remote_adapter_executes_on_the_host_and_brings_back_its_changes(tmp_path):
    target = _target()
    config = _config(tmp_path, target)
    workspace = config["execution"]["adapters"]["claude"]["ssh"]["remote_workspace"]

    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    adapters = build_adapter_registry(config)
    dispatcher = ExecutionDispatcher(
        store, projects, adapters,
        max_concurrent_global=1, max_concurrent_per_project=1,
        allowed_workspace_roots=[Path(config["execution"]["allowed_workspace_roots"][0])],
        auto_review=False,
    )

    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Real remote round trip"))
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
            (TaskState.PLANNED.value, task.task_id),
        )

    run = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="claude", prompt="write a probe file",
        timeout_seconds=120, expected_task_version=2,
    ))
    # Queued against the worktree on the host, not a locally resolved path.
    assert run.workspace == workspace
    assert run.result_metadata[REMOTE_TARGET_KEY]["host"] == target["host"]

    try:
        finished = asyncio.run(dispatcher.execute(run.run_id))

        assert finished.state == RunState.SUCCEEDED, f"{finished.state}: {finished.error_message}"
        assert finished.exit_code == 0
        # The identity env crossed the SSH boundary. An SSH env request alone does not
        # achieve this: sshd forwards only what AcceptEnv allows (default LANG LC_*), so
        # before CR-22 these came back empty from a real host while every fake passed.
        assert f"run={run.run_id}" in finished.stdout_tail
        assert f"task={task.task_id}" in finished.stdout_tail
        assert "project=alpha" in finished.stdout_tail

        # The snapshot is of the remote worktree, and carries the content digest that
        # later deliverable reads verify against.
        snapshot = store.deliverable_snapshot(run.run_id)
        assert snapshot is not None, "a successful remote run must leave a snapshot"
        probe = next(f for f in snapshot["files"] if f["path"] == f"{run.run_id}.probe")
        assert probe["sha256"]
    finally:
        # Leave the worktree as it was found: the file this run wrote is the only
        # thing it added, and a second run must not inherit it.
        components = remote_components_for(adapters["claude"], projects)
        try:
            asyncio.run(_remove(components, PurePosixPath(workspace), f"{run.run_id}.probe"))
        finally:
            asyncio.run(components.aclose())


async def _remove(components, workspace: PurePosixPath, name: str) -> None:
    await components.environment.run(["rm", "-f", str(workspace / name)], timeout=30)


@pytest.mark.integration
def test_default_ssh_keys_reach_the_real_host():
    """CR-24 on a real host: no password, no key path — this machine's ~/.ssh keys, as
    `ssh user@host` uses them. Set AGORA_SSH_TEST_DEFAULT_KEYS=1 where that login works."""
    import asyncio as _asyncio
    import os as _os

    from agora.environments_remote import RemoteEnvironment

    host, user = _os.environ.get("AGORA_SSH_TEST_HOST"), _os.environ.get("AGORA_SSH_TEST_USER")
    if not (host and user and _os.environ.get("AGORA_SSH_TEST_DEFAULT_KEYS")):
        pytest.skip("set AGORA_SSH_TEST_HOST / _USER / AGORA_SSH_TEST_DEFAULT_KEYS=1")
    env = RemoteEnvironment(host=host, user=user, secret_ref="ssh-default", credential_source="ssh_default")

    async def go():
        try:
            return await env.run(["echo", "default-keys-ok"], timeout=30)
        finally:
            await env.aclose()

    result = _asyncio.run(go())
    assert result.outcome == "completed", result
    assert "default-keys-ok" in result.stdout
