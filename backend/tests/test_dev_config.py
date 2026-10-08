"""config.dev.yaml is config.yaml with exactly three keys changed (CR-31 slice 2)."""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent


def _load(name):
    return yaml.safe_load((REPO / name).read_text(encoding="utf-8"))


def test_dev_config_differs_only_where_it_must():
    stable, dev = _load("config.yaml"), _load("config.dev.yaml")
    assert dev["projects"]["registry_path"] == ".agora/dev/projects.yaml"
    assert dev["projects"]["projects"]["agora"]["root"] == ".agora/dev/sandbox"
    assert dev["memory"]["data_dir"] == "./data-dev"
    for doc in (stable, dev):
        doc["projects"]["registry_path"] = None
        doc["projects"]["projects"]["agora"]["root"] = None
        doc["projects"]["projects"]["agora"]["name"] = None
        doc["memory"]["data_dir"] = None
    assert dev == stable, "config.dev.yaml drifted from config.yaml; copy the change across"


def test_keyring_namespace_follows_the_environment(monkeypatch):
    from agora.execution import credential_store

    monkeypatch.delenv("AGORA_KEYRING_SERVICE", raising=False)
    assert credential_store._service() == "agora-remote-env"
    monkeypatch.setenv("AGORA_KEYRING_SERVICE", "agora-remote-env-dev")
    assert credential_store._service() == "agora-remote-env-dev"
