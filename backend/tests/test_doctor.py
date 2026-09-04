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


def test_an_unreadable_workspace_is_not_reported_as_one_to_create(tmp_path, monkeypatch):
    """`Create workspace directory: X` for a directory that is already there sends
    the operator to do something that will fail. Not ready either way; the reason
    it gives has to be the reason."""
    import agora.doctor as doctor_module
    from agora.fsprobe import PathProbe

    monkeypatch.setattr(
        doctor_module,
        "probe_path",
        lambda path, **kw: PathProbe("unknown", error=PermissionError(13, "denied")),
    )
    # Pin the CLI answer too, so the assertion below runs whatever is installed on
    # the machine -- a test whose body depends on that is not a test.
    monkeypatch.setattr(doctor_module, "which", lambda command: f"/usr/bin/{command}")
    report = doctor_module.run_doctor()

    checked = 0
    for worker in report["workers"]:
        if worker["workspace"] is None:
            continue
        assert worker["workspace_ok"] is False
        assert "Create workspace directory" not in worker["action"]
        assert "could not be inspected" in worker["action"]
        assert "PermissionError" in worker["action"]
        checked += 1
    assert checked, "no worker had a workspace configured; the assertions never ran"
