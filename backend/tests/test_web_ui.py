"""Static UI availability and fail-closed retirement of the 0.5 council."""

import json
import subprocess
import sys

from fastapi.testclient import TestClient

from agora import __version__
from agora.__main__ import cli_main
from agora.api.app import app


client = TestClient(app)


class TestCurrentProductSurface:
    def test_health_and_metadata_describe_control_plane(self):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": __version__}
        assert app.version == __version__
        assert app.description == "Local-first Task delivery control plane"

    def test_index_and_control_plane_static_pages_exist(self):
        index = client.get("/")

        assert index.status_code == 200
        assert "Agora - Task Delivery Control Plane" in index.text
        assert "Multi-perspective AI council" not in index.text
        assert client.get("/control-plane").status_code == 200

    def test_unknown_api_get_is_not_frontend_success(self):
        response = client.get("/api/definitely-not-a-route")

        assert response.status_code == 404


class TestLegacyCouncilRetirement:
    RETIRED_API_PREFIXES = (
        "chat",
        "agents",
        "artifacts",
        "sessions",
        "shared",
        "skills",
        "memory",
        "profile",
    )

    def test_retired_api_roots_fail_closed_without_redirect(self):
        for prefix in self.RETIRED_API_PREFIXES:
            response = client.post(f"/api/{prefix}", json={})

            assert response.status_code == 410, prefix
            assert response.json()["detail"]["code"] == "legacy_council_retired"
            assert response.headers.get("location") is None

    def test_every_retired_api_method_is_intercepted_before_cors(self):
        for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            headers = {}
            if method == "OPTIONS":
                headers = {
                    "Origin": "https://example.test",
                    "Access-Control-Request-Method": "POST",
                }
            response = client.request(method, "/api/chat/continue", headers=headers)

            assert response.status_code == 410, method
            if method != "HEAD":
                assert response.json()["detail"]["code"] == "legacy_council_retired"

    def test_retired_nested_api_paths_share_stable_result(self):
        for path in (
            "/api/chat/continue",
            "/api/chat/share",
            "/api/agents/scout",
            "/api/artifacts/C:/private/file.txt",
            "/api/sessions/session-1",
            "/api/shared/share-1",
        ):
            response = client.get(path)

            assert response.status_code == 410, path
            assert response.json()["detail"] == {
                "code": "legacy_council_retired",
                "message": (
                    "The Agora 0.5 autonomous council is retired. "
                    "Use the authenticated Task Control Plane."
                ),
            }

    def test_retired_ui_paths_redirect_to_control_plane(self):
        for path in (
            "/chat",
            "/agents",
            "/agents/scout",
            "/skills/learned",
            "/settings/providers",
            "/shared/share-1",
        ):
            response = client.get(path, follow_redirects=False)

            assert response.status_code == 308, path
            assert response.headers["location"] == "/control-plane"

    def test_bare_cli_reports_task_control_plane(self, capsys):
        assert cli_main([]) == 0

        output = capsys.readouterr()
        assert "agora task <command>" in output.out
        assert "Task delivery control plane" in output.out
        assert output.err == ""

    def test_explicit_cli_help_uses_same_task_guidance(self, capsys):
        assert cli_main(["--help"]) == 0

        output = capsys.readouterr()
        assert "agora task <command>" in output.out
        assert output.err == ""

    def test_legacy_cli_command_is_rejected(self, capsys):
        assert cli_main(["chat"]) == 2

        output = capsys.readouterr()
        assert output.out == ""
        assert "autonomous 0.5 council is retired" in output.err

    def test_app_import_does_not_load_legacy_council_or_provider_modules(self):
        blocked_prefixes = (
            "agora.agents",
            "agora.context",
            "agora.memory",
            "agora.models",
            "agora.skills",
            "agora.tools",
            "agora.api._state",
            "agora.api.chat",
            "agora.api.agents",
            "agora.api.extras",
            "agora.api.sessions",
        )
        probe = (
            "import json, sys\n"
            "import agora.api.app\n"
            f"prefixes = {blocked_prefixes!r}\n"
            "print(json.dumps(sorted(name for name in sys.modules "
            "if name.startswith(prefixes))))\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == []

    def test_rejected_legacy_cli_does_not_load_legacy_modules(self):
        probe = (
            "import json, sys\n"
            "from agora.__main__ import cli_main\n"
            "code = cli_main(['chat'])\n"
            "prefixes = ('agora.agents', 'agora.models', 'agora.api._state')\n"
            "blocked = sorted(name for name in sys.modules if name.startswith(prefixes))\n"
            "print(json.dumps({'code': code, 'blocked': blocked}))\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"code": 2, "blocked": []}


def test_openapi_excludes_retired_council_operations():
    paths = app.openapi()["paths"]

    assert not any(
        path == "/api/chat" or path.startswith("/api/chat/")
        for path in paths
    )
    assert not any(
        path == "/api/agents" or path.startswith("/api/agents/")
        for path in paths
    )
    assert not any(
        path == "/api/artifacts" or path.startswith("/api/artifacts/")
        for path in paths
    )
