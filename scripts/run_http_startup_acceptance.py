"""Exercise an isolated live Agora HTTP/static startup without any AI call."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND_OUT = ROOT / "frontend" / "out"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = b"{}" if method != "GET" else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    if not (FRONTEND_OUT / "index.html").is_file():
        raise RuntimeError("frontend/out is missing; run the locked frontend build first")

    with tempfile.TemporaryDirectory(prefix="agora-http-acceptance-") as raw_temp:
        temp = Path(raw_temp)
        project_root = temp / "project"
        project_root.mkdir()
        token = f"http-acceptance-{secrets.token_hex(24)}"
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        config["memory"] = {"data_dir": str(temp / "data")}
        config["projects"] = {
            "registry_path": str(temp / "projects.yaml"),
            "default": "acceptance",
            "projects": {
                "acceptance": {
                    "name": "HTTP startup acceptance",
                    "root": str(project_root),
                    "workspaces": {
                        "codex": str(temp / "workspaces" / "codex"),
                        "claude": str(temp / "workspaces" / "claude"),
                        "kiro": str(temp / "workspaces" / "kiro"),
                    },
                }
            },
        }
        config["control_plane"] = {
            "db_path": str(temp / "data" / "agora.db"),
            "auth": {
                "credentials": [
                    {
                        "secret_ref": "AGORA_CONTROL_PLANE_TOKEN",
                        "principal": "http-acceptance",
                        "permissions": ["control_plane.read"],
                        "projects": ["acceptance"],
                    }
                ]
            },
        }
        config_path = temp / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        environment["AGORA_CONFIG_PATH"] = str(config_path)
        environment["AGORA_CONTROL_PLANE_TOKEN"] = token
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agora.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=BACKEND,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=5)
                    raise RuntimeError(
                        f"server exited before readiness\nstdout:\n{stdout}\nstderr:\n{stderr}"
                    )
                try:
                    status, _ = _request(base_url, "/health")
                    if status == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("server did not become ready within 30 seconds")

            health_status, health_body = _request(base_url, "/health")
            root_status, root_body = _request(base_url, "/")
            console_status, console_body = _request(base_url, "/control-plane")
            unauthorized_status, _ = _request(
                base_url,
                "/api/control-plane/projects/acceptance/tasks",
            )
            authorized_status, authorized_body = _request(
                base_url,
                "/api/control-plane/projects/acceptance/tasks",
                token=token,
            )
            retired_status, retired_body = _request(
                base_url,
                "/api/chat",
                method="POST",
            )

            health = json.loads(health_body)
            authorized = json.loads(authorized_body)
            retired = json.loads(retired_body)
            assert health_status == 200
            assert health == {"status": "ok", "version": "1.0.0"}
            assert root_status == 200 and "Task Delivery Control Plane" in root_body
            assert console_status == 200 and "Task Delivery Control Plane" in console_body
            assert unauthorized_status == 401
            assert authorized_status == 200
            assert authorized["tasks"] == [] and authorized["page"]["total"] == 0
            assert retired_status == 410
            assert retired["detail"]["code"] == "legacy_council_retired"

            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "acceptance_mode": "isolated_live_http",
                        "provider_or_model_called": False,
                        "health_version": health["version"],
                        "root_static": True,
                        "control_plane_static": True,
                        "unauthorized_task_index_status": unauthorized_status,
                        "authorized_task_index_status": authorized_status,
                        "retired_council_status": retired_status,
                        "temporary_state_removed": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        finally:
            _stop(process)


if __name__ == "__main__":
    raise SystemExit(main())
