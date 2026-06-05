from __future__ import annotations

from agora.doctor import run_doctor
from agora.projects import ProjectRegistry


def test_doctor_reports_project_and_workers(tmp_path):
    cfg = {
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
        }
    }
    ProjectRegistry(cfg)

    report = run_doctor(cfg)

    assert report["project"]["id"] == "demo"
    assert {w["worker"] for w in report["workers"]} == {
        "claude-research",
        "codex-engineering",
        "kiro-spec",
    }
    assert all(w["workspace_ok"] for w in report["workers"])
