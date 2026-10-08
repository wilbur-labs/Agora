"""Agora's own version has one value, and /health says which installation this is (CR-31)."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from agora import __version__
from agora.api.app import app, instance_of

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent


def test_the_four_version_records_agree():
    # A regex, not tomllib: Python 3.10 is still supported.
    pyproject = re.search(r'^\[project\][^\[]*?^version = "([^"]+)"',
                          (BACKEND / "pyproject.toml").read_text(encoding="utf-8"), re.M | re.S).group(1)
    package = json.loads((REPO / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    lock = re.search(r'\[\[package\]\]\s*name = "agora"\s*version = "([^"]+)"',
                     (BACKEND / "uv.lock").read_text(encoding="utf-8"))
    assert lock, "agora is missing from uv.lock"
    assert {__version__, pyproject, package, lock.group(1)} == {__version__}


def test_instance_is_only_what_the_launcher_said():
    assert instance_of({"AGORA_INSTANCE": "stable"}) == "stable"
    assert instance_of({"AGORA_INSTANCE": "dev"}) == "dev"
    assert instance_of({}) == "unknown"
    assert instance_of({"AGORA_INSTANCE": "production"}) == "unknown"


def test_health_reports_version_and_instance(monkeypatch):
    monkeypatch.setenv("AGORA_INSTANCE", "stable")
    monkeypatch.setenv("AGORA_CONFIG_PATH", str(REPO / "config.dev.yaml"))
    body = TestClient(app).get("/health").json()
    assert body == {"status": "ok", "version": __version__, "instance": "stable", "config": "config.dev.yaml"}
