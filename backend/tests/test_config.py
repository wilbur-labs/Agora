"""Tests for config loading and model registry."""
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from agora.config.settings import get_config, reset_config
from agora.models.registry import ModelRegistry, reset_registry


class TestConfig:
    def test_loads_yaml(self):
        reset_config()
        cfg = get_config()
        assert "models" in cfg
        assert "council" in cfg

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

    def test_loads_utf8_yaml(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text('display_name: "交付控制面 🚦"\n', encoding="utf-8")
        reset_config()
        try:
            assert get_config(path)["display_name"] == "交付控制面 🚦"
        finally:
            reset_config()

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
            "./skills:/app/backend/skills",
            "./data:/app/backend/data",
            "./.agora:/app/.agora",
            "./agora-workspace:/root/agora-workspace",
        }
        assert expected <= set(api["volumes"])
        assert expected <= set(cli["volumes"])
        assert api["healthcheck"]["test"][-1] == "http://127.0.0.1:8000/health"

        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        assert "COPY skill[s]/" not in dockerfile
        assert "/app/backend/skills/learned" in dockerfile
        assert dockerfile.index("COPY backend/ backend/") < dockerfile.index("RUN pip install")


class TestModelRegistry:
    def test_list_models(self):
        reset_config()
        reset_registry()
        reg = ModelRegistry()
        models = reg.list_models()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_get_known_model(self):
        reset_config()
        reset_registry()
        reg = ModelRegistry()
        models = reg.list_models()
        if "kiro" in models:
            provider = reg.get("kiro")
            assert provider.name == "kiro-cli"

    def test_get_unknown_model(self):
        reset_config()
        reset_registry()
        reg = ModelRegistry()
        with pytest.raises(ValueError, match="not in config"):
            reg.get("nonexistent_model_xyz")

    def test_caching(self):
        reset_config()
        reset_registry()
        reg = ModelRegistry()
        models = reg.list_models()
        if models:
            p1 = reg.get(models[0])
            p2 = reg.get(models[0])
            assert p1 is p2  # same instance
