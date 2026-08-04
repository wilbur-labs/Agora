"""Executable end-to-end acceptance for the formal Task control path."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_SCRIPT = REPOSITORY_ROOT / "scripts" / "run_task_acceptance.py"


def test_deterministic_formal_task_acceptance_uses_real_processes_and_reopens_sqlite(
    tmp_path: Path,
):
    result = subprocess.run(
        [
            sys.executable,
            str(ACCEPTANCE_SCRIPT),
            "--temp-root",
            str(tmp_path),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["schema_version"] == "1.0"
    assert receipt["acceptance_mode"] == "deterministic_non_ai"
    assert receipt["provider_or_model_called"] is False
    assert receipt["task_state_before_approval"] == "needs_review"
    assert receipt["task_state_after_reopen"] == "completed"
    assert receipt["plan_state_after_approval"] == "ready_for_implementation"
    assert receipt["task_state_lifecycle"] == "control_plane_managed"
    assert receipt["stage_states"] == ["passed", "passed", "passed"]
    assert receipt["gate_states"] == ["passed", "passed", "passed"]
    assert receipt["run_runtimes"] == ["codex", "claude", "kiro"]
    assert receipt["run_exit_codes"] == [0, 0, 0]
    assert receipt["artifact_count"] == 3
    assert receipt["evidence_count"] == 3
    assert receipt["required_human_actions_before_approval"] == ["plan_approval"]
    assert receipt["required_human_actions_after_reopen"] == []
    assert receipt["persisted_reopen_verified"] is True
    assert receipt["temporary_workspace_removed"] is True
    assert list(tmp_path.iterdir()) == []
    assert "launching Stage solution_design" in result.stderr
    assert "removed isolated workspace" in result.stderr
