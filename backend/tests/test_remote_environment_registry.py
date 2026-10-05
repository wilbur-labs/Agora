"""Registering a remote (SSH) environment through the API — CR-4 slice 1.

Registering a remote (SSH) environment through the API — CR-4 slices 1 and 2.

The CRUD contract plus the rules that make this more than a plain table:

* **the credential value is never accepted (except a keyring password, written to the
  OS keyring and dropped) or returned.** The response reports ``credential_source`` and
  a three-state ``credential_present`` (env var set / key file exists / keyring entry
  exists), never the value (NFR-2 / SECURITY-12, public repo; adr/0001).
* **an invalid SSH field set is refused at register time.** ``host_key_mode`` is a
  closed set; a bad value is 422, validated through the same frozen ``RemoteAdapterConfig``
  contract the runtime uses, not a second copy.
* **the credential source is explicit** (env_var / key_path / keyring), each with its
  own rules — the env_var name is constrained to the AGORA_ namespace, a keyring source
  needs a password to store (or an existing one on update).
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
    """Only the source and whether a credential is present — never the secret itself.
    The response is scanned for any field that could carry a credential value."""
    client.post("/api/remote-environments", json=_payload())
    entry = client.get("/api/remote-environments").json()["environments"][0]

    assert entry["secret_ref"] == "AGORA_SSH_KEY_kimono"
    assert entry["credential_source"] == "env_var"
    assert "credential_present" in entry
    # No field carries the secret itself. (credential_source / credential_present are
    # metadata, not the value; password must never appear.)
    assert "password" not in entry
    assert "private_key" not in entry


def test_credential_present_reflects_the_env_var(client, monkeypatch):
    """For an env_var source, credential_present tracks whether the named env var is set."""
    client.post("/api/remote-environments", json=_payload())

    monkeypatch.delenv("AGORA_SSH_KEY_kimono", raising=False)
    absent = client.get("/api/remote-environments").json()["environments"][0]
    assert absent["credential_present"] is False

    monkeypatch.setenv("AGORA_SSH_KEY_kimono", "/home/ubuntu/.ssh/id_ed25519")
    present = client.get("/api/remote-environments").json()["environments"][0]
    assert present["credential_present"] is True


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


# --- slice 2: credential source (adr/0001) ------------------------------------------


@pytest.fixture
def memory_keyring(monkeypatch):
    """An in-memory keyring backend so keyring-source tests never touch the machine's
    real secret store. Patches the module functions credential_store calls."""
    import keyring

    store: dict[str, str] = {}

    class _Mem:
        priority = 5

    def _id(u: str) -> str:
        # credential_store hex-encodes the id into the keyring key; decode it so the
        # assertions below can stay keyed by the readable environment id.
        return bytes.fromhex(u.removeprefix("env-")).decode("utf-8")

    monkeypatch.setattr(keyring, "get_keyring", lambda: _Mem())
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__(_id(u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get(_id(u)))

    def _delete(s, u):
        import keyring.errors
        if _id(u) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[_id(u)]

    monkeypatch.setattr(keyring, "delete_password", _delete)
    return store


def test_keyring_source_stores_password_and_never_returns_it(client, memory_keyring):
    resp = client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="kimono-login", password="hunter2"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["credential_source"] == "keyring"
    assert body["credential_present"] is True
    assert "password" not in body
    # The password reached the keyring, not the DB.
    assert memory_keyring["kimono-vps"] == "hunter2"
    # And it is not on the stored row.
    row = client.store.require("kimono-vps")  # type: ignore[attr-defined]
    assert not hasattr(row, "password")


def test_keyring_source_without_password_is_rejected(client, memory_keyring):
    resp = client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="label", password=None),
    )
    assert resp.status_code == 422, resp.text


def test_key_path_source_does_not_require_agora_prefix(client):
    """A key_path secret_ref is a file path, not an env var name — the AGORA_ rule that
    guards the env_var presence probe must not apply to it."""
    resp = client.post(
        "/api/remote-environments",
        json=_payload(credential_source="key_path", secret_ref="/home/ubuntu/.ssh/id_ed25519"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["credential_source"] == "key_path"


def test_env_var_source_still_requires_agora_prefix(client):
    resp = client.post(
        "/api/remote-environments",
        json=_payload(credential_source="env_var", secret_ref="AWS_SECRET_ACCESS_KEY"),
    )
    assert resp.status_code == 422, resp.text


def test_password_rejected_for_non_keyring_source(client):
    resp = client.post(
        "/api/remote-environments",
        json=_payload(credential_source="key_path", secret_ref="/k", password="nope"),
    )
    assert resp.status_code == 422, resp.text


def test_update_keeps_keyring_password_when_none_sent(client, memory_keyring):
    """Editing a keyring environment without re-entering the password keeps the stored
    one (A semantics): change a label, leave the secret alone."""
    client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="login", password="keepme"),
    )
    resp = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="keyring", secret_ref="login", label="kimono prod", password=None),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "kimono prod"
    assert memory_keyring["kimono-vps"] == "keepme"  # untouched


def test_update_replaces_keyring_password_when_sent(client, memory_keyring):
    client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="login", password="old"),
    )
    resp = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="keyring", secret_ref="login", password="new"),
    )
    assert resp.status_code == 200, resp.text
    assert memory_keyring["kimono-vps"] == "new"


def test_update_row_failure_restores_previous_keyring_password(
    client, memory_keyring, monkeypatch
):
    """CR-8: the password is written before the row; if the row write then fails, the
    keyring must go back to what it was, not keep a password the row never accepted."""
    client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="login", password="old"),
    )

    def _boom(*args, **kwargs):
        raise ValueError("row write failed")

    monkeypatch.setattr(client.store, "update", _boom)  # type: ignore[attr-defined]
    resp = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="keyring", secret_ref="login", password="new"),
    )
    assert resp.status_code == 422, resp.text
    assert memory_keyring["kimono-vps"] == "old"


def test_update_row_failure_removes_password_that_was_not_there_before(
    client, memory_keyring, monkeypatch
):
    """CR-8, the other half: switching env_var -> keyring with a new password, then the
    row write fails — the new keyring entry must not survive."""
    client.post("/api/remote-environments", json=_payload())  # env_var source

    def _boom(*args, **kwargs):
        raise ValueError("row write failed")

    monkeypatch.setattr(client.store, "update", _boom)  # type: ignore[attr-defined]
    resp = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="keyring", secret_ref="login", password="new"),
    )
    assert resp.status_code == 422, resp.text
    assert "kimono-vps" not in memory_keyring


def test_update_to_keyring_without_existing_password_is_rejected(client, memory_keyring):
    """Switching an env_var environment to keyring with neither a new password nor an
    existing keyring entry would persist an environment that can never connect."""
    client.post("/api/remote-environments", json=_payload())  # env_var source
    resp = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="keyring", secret_ref="login", password=None),
    )
    assert resp.status_code == 422, resp.text


def test_delete_keyring_environment_removes_the_secret(client, memory_keyring):
    client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="login", password="bye"),
    )
    assert memory_keyring.get("kimono-vps") == "bye"
    client.delete("/api/remote-environments/kimono-vps")
    assert "kimono-vps" not in memory_keyring  # no orphaned secret


def test_update_away_from_keyring_forgets_the_secret(client, memory_keyring):
    """Editing a keyring environment to an env_var source must not orphan the password
    in the OS keyring."""
    client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="login", password="bye"),
    )
    assert memory_keyring.get("kimono-vps") == "bye"
    resp = client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="env_var", secret_ref="AGORA_SSH_KEY_kimono"),
    )
    assert resp.status_code == 200, resp.text
    assert "kimono-vps" not in memory_keyring  # secret forgotten on source change


def test_overlong_password_422_never_echoes_the_value(client, memory_keyring):
    """An over-long password is refused (422) without the value appearing in the
    response body — length is checked in the model validator, not via a Field
    max_length whose parse-time 422 would echo `input` (the password)."""
    secret = "P" * 5000
    resp = client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="login", password=secret),
    )
    assert resp.status_code == 422, resp.text
    assert secret not in resp.text  # the password value is never reflected back


def test_slice1_row_migrates_to_env_var_source(tmp_path):
    """A DB created before credential_source existed gets the column via ADD COLUMN and
    every existing row backfills to 'env_var' — slice-1 environments keep working."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    # Build a slice-1 shaped table (no credential_source column) with one row.
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE remote_environments (
            environment_id TEXT PRIMARY KEY, label TEXT NOT NULL,
            ssh_host TEXT NOT NULL, ssh_port INTEGER NOT NULL DEFAULT 22,
            ssh_user TEXT NOT NULL, remote_workspace TEXT NOT NULL,
            remote_project_root TEXT NOT NULL, secret_ref TEXT NOT NULL,
            host_key_mode TEXT NOT NULL DEFAULT 'strict', known_hosts_path TEXT,
            cleanup_worktree INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO remote_environments VALUES
            ('legacy','L','h',22,'u','/w','/p','AGORA_SSH_KEY_legacy','strict',NULL,0,'t','t');
        """
    )
    conn.commit()
    conn.close()

    # Opening the store runs _initialize, which must migrate in place.
    store = RemoteEnvironmentStore(db_path)
    record = store.require("legacy")
    assert record.credential_source == "env_var"
    # Idempotent: opening again does not fail on the already-present column.
    RemoteEnvironmentStore(db_path).require("legacy")



def test_scrub_redacts_password_from_both_input_and_ctx():
    """The global 422 redactor must close BOTH channels FastAPI echoes: the error's
    `input` AND its `ctx`. pydantic v2 puts the exception raised inside a
    model_validator into ctx["error"], which jsonable_encoder stringifies — so a
    validator that ever interpolated the password would leak it through ctx past an
    input-only redaction. This pins that ctx is dropped, not just input (adr/0001)."""
    from agora.api.app import _scrub_validation_errors

    secret = "hunter2-in-ctx"
    # A body-level (model_validator) error: loc ends at the model, input is the whole
    # parsed body, and ctx carries the raised exception (here interpolating the value).
    errors = [
        {
            "type": "value_error",
            "loc": ["body"],
            "msg": f"a password is only accepted for ... {secret}",
            "input": {"credential_source": "key_path", "password": secret},
            "ctx": {"error": f"ValueError: password rejected: {secret}"},
        }
    ]

    scrubbed = _scrub_validation_errors(errors)

    # Neither the echoed body, the ctx, nor the msg may carry the value.
    assert scrubbed[0]["input"]["password"] == "[redacted]"
    assert "ctx" not in scrubbed[0]
    import json

    assert secret not in json.dumps(scrubbed)
    # Original is untouched (the scrubber copies).
    assert errors[0]["input"]["password"] == secret


def test_scrub_drops_ctx_on_a_field_level_password_error():
    """A field-level error pointing at `password` also has its ctx dropped, so a
    per-field validator that interpolated the value cannot leak it either."""
    from agora.api.app import _scrub_validation_errors

    secret = "field-level-secret"
    errors = [
        {
            "type": "value_error",
            "loc": ["body", "password"],
            "msg": "invalid",
            "input": secret,
            "ctx": {"error": f"bad: {secret}"},
        }
    ]

    scrubbed = _scrub_validation_errors(errors)

    assert scrubbed[0]["input"] == "[redacted]"
    assert "ctx" not in scrubbed[0]
    import json

    assert secret not in json.dumps(scrubbed)


def test_scrub_leaves_non_sensitive_errors_intact():
    """Redaction must not touch ordinary validation errors — non-sensitive field
    failures keep their input and ctx so every other endpoint's 422 is unchanged."""
    from agora.api.app import _scrub_validation_errors

    errors = [
        {
            "type": "string_too_short",
            "loc": ["body", "label"],
            "msg": "String should have at least 1 character",
            "input": "",
            "ctx": {"min_length": 1},
        }
    ]

    scrubbed = _scrub_validation_errors(errors)

    assert scrubbed[0]["input"] == ""
    assert scrubbed[0]["ctx"] == {"min_length": 1}


def test_array_body_with_a_password_is_never_echoed(client):
    """A malformed body (an array where an object is expected) is echoed whole in the
    422; the redactor must find a password at any depth, not only in a top-level dict."""
    secret = "array-body-secret"
    resp = client.post("/api/remote-environments", json=[{"password": secret}])
    assert resp.status_code == 422, resp.text
    assert secret not in resp.text


def test_credential_writes_happen_under_the_mutation_lock(client, memory_keyring, monkeypatch):
    """Row and keyring writes of one request must not interleave with another's: every
    keyring write and delete runs while the shared mutation lock is held."""
    from agora.api import settings_router
    from agora.execution import credential_store

    seen: list[bool] = []
    real_set, real_delete = credential_store.set_password, credential_store.delete_password

    def _set(environment_id, password):
        seen.append(settings_router._REMOTE_ENV_MUTATION_LOCK.locked())
        real_set(environment_id, password)

    def _delete(environment_id):
        seen.append(settings_router._REMOTE_ENV_MUTATION_LOCK.locked())
        real_delete(environment_id)

    monkeypatch.setattr(credential_store, "set_password", _set)
    monkeypatch.setattr(credential_store, "delete_password", _delete)
    client.post(
        "/api/remote-environments",
        json=_payload(credential_source="keyring", secret_ref="login", password="a"),
    )
    client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="env_var", secret_ref="AGORA_SSH_KEY_kimono"),
    )
    client.put(
        "/api/remote-environments/kimono-vps",
        json=_payload(credential_source="keyring", secret_ref="login", password="b"),
    )
    client.delete("/api/remote-environments/kimono-vps")
    assert seen and all(seen), seen


def test_text_plain_body_with_a_password_is_never_echoed(client):
    """A body sent as text/plain is echoed as a raw string; it cannot be inspected field
    by field, so one that names a sensitive field is redacted whole."""
    secret = "TEXT_PLAIN_SECRET"
    resp = client.post(
        "/api/remote-environments",
        content='{"password":"' + secret + '"}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 422, resp.text
    assert secret not in resp.text


def test_real_store_dependency_resolves_without_override(tmp_path, monkeypatch):
    """Every other test here overrides get_remote_environment_store, which once hid a
    missing import: the real dependency raised NameError, so the endpoint was a 500 in
    the running app. Call the real one, pointed at a temp database."""
    from agora.api import settings_router

    monkeypatch.setattr(
        settings_router,
        "get_config",
        lambda: {"control_plane": {"db_path": str(tmp_path / "agora.db")}},
    )
    store = settings_router.get_remote_environment_store()
    assert store.list_environments() == []


def test_uninspectable_key_path_reads_as_unknown_not_absent(client, monkeypatch):
    """A key path that cannot be inspected (permission denied) is "cannot tell" (None),
    not "absent" (False) — and it must not turn the whole listing into a 500."""
    client.post(
        "/api/remote-environments",
        json=_payload(credential_source="key_path", secret_ref="/locked/id_ed25519"),
    )

    def _denied(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "is_file", _denied)
    resp = client.get("/api/remote-environments")
    assert resp.status_code == 200, resp.text
    assert resp.json()["environments"][0]["credential_present"] is None
