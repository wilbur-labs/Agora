"""Tests for current control-plane configuration loading."""
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from agora.config.settings import get_config, reset_config


class TestConfig:
    # Opted out of the conftest redirect on purpose. That fixture points
    # AGORA_CONFIG_PATH at a copy, which means a bare get_config() stops performing
    # the repository-root/backend/cwd search — and this test is *about* that search.
    # Under the redirect it read the copy and asserted the copy's contents, so the
    # search could have been removed entirely and it would still have passed.
    @pytest.mark.real_config_discovery
    def test_loads_yaml(self):
        reset_config()
        cfg = get_config()
        assert {"projects", "control_plane", "orchestration", "execution"} <= cfg.keys()
        assert "council" not in cfg
        assert "models" not in cfg
        assert cfg["execution"]["adapters"]["kiro"]["enabled"] is True

    def test_env_var_substitution(self):
        reset_config()
        os.environ["_AGORA_TEST_VAR"] = "test_value"
        try:
            # Write a temp config with env var
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                f.write("test_key: ${_AGORA_TEST_VAR}")
                path = f.name
            reset_config()
            cfg = get_config(path)
            assert cfg["test_key"] == "test_value"
        finally:
            os.environ.pop("_AGORA_TEST_VAR", None)
            os.unlink(path)
            reset_config()

    def test_environment_can_select_an_isolated_config(self, tmp_path, monkeypatch):
        path = tmp_path / "isolated-config.yaml"
        path.write_text("control_plane:\n  db_path: isolated.db\n", encoding="utf-8")
        monkeypatch.setenv("AGORA_CONFIG_PATH", str(path))
        reset_config()
        try:
            assert get_config()["control_plane"]["db_path"] == "isolated.db"
        finally:
            reset_config()

    def test_loads_utf8_yaml(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text('display_name: "交付控制面 🚦"\n', encoding="utf-8")
        reset_config()
        try:
            assert get_config(path)["display_name"] == "交付控制面 🚦"
        finally:
            reset_config()

    # Same reason, and it also exercises the default path: reset-then-reload has to
    # resolve the file the same way twice, which is only the real search when nothing
    # has pinned AGORA_CONFIG_PATH.
    @pytest.mark.real_config_discovery
    def test_reset_config(self):
        reset_config()
        cfg1 = get_config()
        reset_config()
        cfg2 = get_config()
        # Should reload (not same object)
        assert cfg1 is not cfg2

    def test_docker_release_layout(self):
        root = Path(__file__).resolve().parents[2]
        compose = yaml.safe_load((root / "docker-compose.yaml").read_text(encoding="utf-8"))
        api = compose["services"]["agora-api"]
        cli = compose["services"]["agora-cli"]

        expected = {
            "./data:/app/backend/data",
            "./.agora:/app/.agora",
        }
        assert expected <= set(api["volumes"])
        assert expected <= set(cli["volumes"])
        assert not any("skills" in item for item in api["volumes"] + cli["volumes"])
        assert not any("agora-workspace" in item for item in api["volumes"] + cli["volumes"])
        assert api["healthcheck"]["test"][-1] == "http://127.0.0.1:8000/health"

        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        assert "/app/backend/skills" not in dockerfile
        assert dockerfile.index("COPY backend/ backend/") < dockerfile.index("RUN pip install")
