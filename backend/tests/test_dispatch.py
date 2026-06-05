from __future__ import annotations

import asyncio
import json
import sys

from agora.projects import ProjectRegistry
from agora.research import ResearchOrchestrator


def _fake_cli(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        "import sys\n"
        "print('fake worker received prompt chars:', len(sys.argv[-1]))\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def _config(tmp_path, command):
    return {
        "projects": {
            "registry_path": str(tmp_path / "registry" / "projects.yaml"),
            "default": "demo",
            "projects": {
                "demo": {
                    "name": "Demo",
                    "root": str(tmp_path / "demo"),
                    "workspaces": {
                        "claude": str(tmp_path / "workspaces" / "claude"),
                        "codex": str(tmp_path / "workspaces" / "codex"),
                        "kiro": str(tmp_path / "workspaces" / "kiro"),
                    },
                }
            },
        },
        "research": {
            "dispatch": {"timeout_seconds": 30},
            "workers": {
                "codex-engineering": {
                    "enabled": True,
                    "command": command,
                    "workspace_key": "codex",
                }
            },
        },
    }


def test_dispatch_runs_configured_worker_and_records_results(tmp_path):
    command = _fake_cli(tmp_path)
    cfg = _config(tmp_path, command)
    ProjectRegistry(cfg)

    task = asyncio.run(
        ResearchOrchestrator(cfg).run(
            "实现一个小功能",
            dispatch=True,
            only_worker="codex-engineering",
        )
    )

    worker_results = json.loads((task.task_dir / "worker-results.json").read_text(encoding="utf-8"))
    assert len(worker_results) == 1
    result = worker_results[0]
    assert result["worker"] == "codex-engineering"
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["notes_file"] == "notes/codex-engineering.md"
    assert result["stdout_chars"] > 0
    assert (task.task_dir / "notes" / "codex-engineering.md").exists()

    audit_log = tmp_path / "demo" / ".agora" / "audit.log"
    events = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert "research_task_created" in {event["event"] for event in events}
    assert "worker_dispatch_started" in {event["event"] for event in events}
    assert "worker_dispatch_finished" in {event["event"] for event in events}


def test_non_dispatch_does_not_run_configured_cli(tmp_path):
    cfg = _config(tmp_path, ["definitely-missing-agora-test-cli"])
    ProjectRegistry(cfg)

    task = asyncio.run(
        ResearchOrchestrator(cfg).run(
            "实现一个小功能",
            dispatch=False,
            only_worker="codex-engineering",
        )
    )

    worker_results = json.loads((task.task_dir / "worker-results.json").read_text(encoding="utf-8"))
    assert worker_results[0]["status"] == "stubbed"
    assert worker_results[0]["reason"] == "dispatch disabled"
