"""Registering a remote (SSH) environment through the API — CR-4 slice 1.

Slice 1 is CRUD + list only: registering an environment records it; it does NOT feed
the runtime. What is pinned here is the CRUD contract plus the two rules that make this
more than a plain table:

* **the credential value is never accepted or returned.** A request carries
  ``secret_ref`` — the *name* of an env var — and the response reports only whether that
  variable is currently set (``secret_present``), never its value (NFR-2 / SECURITY-12,
  public repo).
* **an invalid SSH field set is refused at register time.** ``host_key_mode`` is a
  closed set; a bad value is 422, validated through the same frozen ``RemoteAdapterConfig``
  contract the runtime uses, not a second copy.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.api.settings_router import get_remote_environment_store
from agora.execution.environment_registry_store import RemoteEnvironmentStore


def _payload(environment_id: str = "kimono-vps", **overrides) -> dict:
    base = {
        "environment_id": environment_id,
        "label": "kimono VPS",
        "ssh_host": "vps.example.test",
        "ssh_port": 22,
        "ssh_user": "ubuntu",
        "remote_workspace": "/home/ubuntu/kimono/.agora/ws/codex",
        "remote_project_root": "/home/ubuntu/kimono",
        "secret_ref": "AGORA_SSH_KEY_kimono",
        "host_key_mode": "strict",
        "known_hosts_path": "/home/ubuntu/.agora/known_hosts",
        "cleanup_worktree": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(tmp_path: Path):
    store = RemoteEnvironmentStore(tmp_path / "agora.db")
    app.dependency_overrides[get_remote_environment_store] = lambda: store
    with TestClient(app) as test_client:
        test_client.store = store  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


# --- the listing --------------------------------------------------------------------


def test_nothing_registered_lists_no_environments(client):
    response = client.get("/api/remote-environments")

    assert response.status_code == 200, response.text
    assert response.json() == {"environments": []}


def test_register_then_list(client):
    created = client.post("/api/remote-environments", json=_payload())
    assert created.status_code == 201, created.text

    listing = client.get("/api/remote-environments").json()["environments"]
    assert len(listing) == 1
    entry = listing[0]
    assert entry["environment_id"] == "kimono-vps"
    assert entry["ssh_host"] == "vps.example.test"
    assert entry["host_key_mode"] == "strict"


# --- the credential is never handled ------------------------------------------------


def test_credential_value_is_never_returned(client):
    """Only the env-var name and whether it is set — never the secret itself. The
    response is scanned for any key that could carry a credential value."""
    client.post("/api/remote-environments", json=_payload())
    entry = client.get("/api/remote-environments").json()["environments"][0]

    assert entry["secret_ref"] == "AGORA_SSH_KEY_kimono"
    assert "secret_present" in entry
    # No field carries the secret itself.
    assert "password" not in entry
    assert "private_key" not in entry
    assert "credential" not in entry


def test_secret_present_reflects_the_env_var(client, monkeypatch):
    client.post("/api/remote-environments", json=_payload())

    monkeypatch.delenv("AGORA_SSH_KEY_kimono", raising=False)
    absent = client.get("/api/remote-environments").json()["environments"][0]
    assert absent["secret_present"] is False

    monkeypatch.setenv("AGORA_SSH_KEY_kimono", "/home/ubuntu/.ssh/id_ed25519")
    present = client.get("/api/remote-environments").json()["environments"][0]
    assert present["secret_present"] is True


# --- validation ---------------------------------------------------------------------


def test_invalid_host_key_mode_is_rejected(client):
    response = client.post(
        "/api/remote-environments", json=_payload(host_key_mode="trust-me")
    )
    assert response.status_code == 422, response.text


def test_secret_ref_must_be_in_the_agora_namespace(client):
    """secret_present probes os.environ for this name, so an unconstrained secret_ref
    would make the list endpoint an oracle for any server env var. The AGORA_ prefix
    keeps the presence probe inside the credentials this feature owns."""
    response = client.post(
        "/api/remote-environments", json=_payload(secret_ref="AWS_SECRET_ACCESS_KEY")
    )
    assert response.status_code == 422, response.text
    # A bare PATH probe is refused too.
    assert client.post(
        "/api/remote-environments", json=_payload(secret_ref="PATH")
    ).status_code == 422


def test_environment_id_with_leading_dash_is_rejected(client):
    """The id rule is one regex now (must start alphanumeric), shared by the request
    field, validate_environment_id and the frontend — no edge where a leading dash slips
    through one check but not another."""
    response = client.post(
        "/api/remote-environments", json=_payload(environment_id="-kimono")
    )
    assert response.status_code == 422, response.text


def test_tofu_without_known_hosts_is_rejected_at_register_time(client):
    """The server is the authority, not the form: tofu with no known_hosts file to pin
    into is refused at register time (422), not persisted as a row that only fails when
    slice 2 tries to connect. The frontend blocks the same input as a friendly echo."""
    response = client.post(
        "/api/remote-environments",
        json=_payload(host_key_mode="tofu", known_hosts_path=None),
    )
    assert response.status_code == 422, response.text


def test_tofu_with_known_hosts_is_accepted(client):
    response = client.post(
        "/api/remote-environments",
        json=_payload(
            host_key_mode="tofu", known_hosts_path="/home/ubuntu/.agora/known_hosts"
        ),
    )
    assert response.status_code == 201, response.text


def test_duplicate_id_is_conflict(client):
    assert client.post("/api/remote-environments", json=_payload()).status_code == 201
    again = client.post("/api/remote-environments", json=_payload())
    assert again.status_code == 409, again.text


# --- update -------------------------------------------------------------------------


def test_update_replaces_fields(client):
    client.post("/api/remote-environments", json=_payload())
    updated = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(label="kimono production", ssh_user="deploy"),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["label"] == "kimono production"
    assert body["ssh_user"] == "deploy"


def test_update_unknown_id_is_404(client):
    response = client.put(
        "/api/remote-environments/ghost", json=_payload(environment_id="ghost")
    )
    assert response.status_code == 404, response.text


def test_update_body_id_must_match_path(client):
    client.post("/api/remote-environments", json=_payload())
    response = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(environment_id="something-else"),
    )
    assert response.status_code == 422, response.text


def test_update_to_tofu_without_known_hosts_is_rejected(client):
    """The tofu guard is on the shared request model, so PUT is held to the same rule
    as POST: an existing strict environment cannot be edited into tofu with no
    known_hosts file."""
    client.post("/api/remote-environments", json=_payload())
    response = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(host_key_mode="tofu", known_hosts_path=None),
    )
    assert response.status_code == 422, response.text


# --- delete -------------------------------------------------------------------------


def test_delete_forgets_the_environment(client):
    client.post("/api/remote-environments", json=_payload())
    deleted = client.delete("/api/remote-environments/kimono-vps")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"was_registered": True}
    assert client.get("/api/remote-environments").json() == {"environments": []}


def test_delete_absent_id_is_idempotent(client):
    response = client.delete("/api/remote-environments/never-existed")
    assert response.status_code == 200, response.text
    assert response.json() == {"was_registered": False}
