"""OS keyring credential store — CR-4 slice 2 (adr/0001).

These tests drive the store against an in-memory keyring backend (keyring ships one
for exactly this) so they never touch the machine's real secret store, and they pin the
one rule that makes this module safe: when there is NO usable backend, every operation
fails closed rather than pretending a secret was stored or absent.
"""
from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend

from agora.execution import credential_store
from agora.execution.credential_store import KeyringUnavailableError


class _MemoryKeyring(KeyringBackend):
    """A real, usable in-memory backend (priority >= 1) so the store's _guard passes."""

    priority = 5  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self._store[(service, username)]
        except KeyError as exc:
            raise keyring.errors.PasswordDeleteError("not found") from exc


class _NoKeyring(KeyringBackend):
    """The null backend: priority < 1, which _guard treats as 'no usable backend'."""

    priority = -1  # type: ignore[assignment]

    def get_password(self, service, username):  # pragma: no cover - never reached
        raise keyring.errors.NoKeyringError

    def set_password(self, service, username, password):  # pragma: no cover
        raise keyring.errors.NoKeyringError

    def delete_password(self, service, username):  # pragma: no cover
        raise keyring.errors.NoKeyringError


@pytest.fixture
def memory_backend(monkeypatch):
    backend = _MemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    # credential_store calls keyring.set/get/delete_password, which dispatch to the
    # active backend; point those at ours too.
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


@pytest.fixture
def no_backend(monkeypatch):
    monkeypatch.setattr(keyring, "get_keyring", lambda: _NoKeyring())


# --- happy path ---------------------------------------------------------------------


def test_set_get_delete_roundtrip(memory_backend):
    assert credential_store.get_password("env-a") is None
    credential_store.set_password("env-a", "s3cret")
    assert credential_store.get_password("env-a") == "s3cret"
    assert credential_store.has_password("env-a") is True
    credential_store.delete_password("env-a")
    assert credential_store.get_password("env-a") is None
    assert credential_store.has_password("env-a") is False


def test_set_replaces_existing(memory_backend):
    credential_store.set_password("env-a", "one")
    credential_store.set_password("env-a", "two")
    assert credential_store.get_password("env-a") == "two"


def test_delete_absent_is_idempotent(memory_backend):
    # No entry — deleting is a no-op, matching the registry delete's idempotence.
    credential_store.delete_password("never-set")


def test_entries_are_keyed_per_environment(memory_backend):
    credential_store.set_password("env-a", "a")
    credential_store.set_password("env-b", "b")
    assert credential_store.get_password("env-a") == "a"
    assert credential_store.get_password("env-b") == "b"
    credential_store.delete_password("env-a")
    assert credential_store.get_password("env-a") is None
    assert credential_store.get_password("env-b") == "b"  # untouched


# --- fail closed when there is no backend -------------------------------------------


def test_no_backend_fails_closed_on_every_op(no_backend):
    """The load-bearing rule: no usable backend => raise, never store or report the
    secret anywhere unprotected, and never answer has_password with a plain False."""
    with pytest.raises(KeyringUnavailableError):
        credential_store.set_password("env-a", "s3cret")
    with pytest.raises(KeyringUnavailableError):
        credential_store.get_password("env-a")
    with pytest.raises(KeyringUnavailableError):
        credential_store.has_password("env-a")
    with pytest.raises(KeyringUnavailableError):
        credential_store.delete_password("env-a")


def test_librarys_own_fail_backend_trips_the_guard(monkeypatch):
    """Fail-closed rests on _guard treating a priority<1 backend as unusable. Exercise
    the LIBRARY's actual null backend (keyring.backends.fail.Keyring), not just our
    stub, so a future `keyring` release that changes that sentinel priority breaks this
    test rather than silently opening the gate."""
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    with pytest.raises(KeyringUnavailableError):
        credential_store.set_password("env-a", "s3cret")
    with pytest.raises(KeyringUnavailableError):
        credential_store.get_password("env-a")



class _LockedKeyring(KeyringBackend):
    """A backend that IS present (priority >= 1, so _guard passes) but is unusable at
    runtime — a locked Keychain / Secret Service. Its operations raise a generic
    ``KeyringError`` (not ``NoKeyringError``), which _guard's priority check cannot
    pre-empt."""

    priority = 5  # type: ignore[assignment]

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("backend is locked")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringError("backend is locked")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringError("backend is locked")


@pytest.fixture
def locked_backend(monkeypatch):
    backend = _LockedKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


def test_present_but_locked_backend_fails_closed_not_500(locked_backend):
    """A present-but-locked backend passes _guard's priority check, then raises a
    generic KeyringError mid-operation. set/get must re-raise it as
    KeyringUnavailableError so the API returns a clean 422 (fail-closed), never an
    unhandled 500 — and still without storing or reporting the secret."""
    with pytest.raises(KeyringUnavailableError):
        credential_store.set_password("env-a", "s3cret")
    with pytest.raises(KeyringUnavailableError):
        credential_store.get_password("env-a")
    # A locked store cannot confirm the entry is gone, so delete fails closed too — it
    # must not report a delete that did not happen.
    with pytest.raises(KeyringUnavailableError):
        credential_store.delete_password("env-a")


class _GenericNotFoundKeyring(_MemoryKeyring):
    """A backend that reports "not found" on delete with a generic KeyringError rather
    than PasswordDeleteError."""

    def delete_password(self, service, username):
        if (service, username) not in self._store:
            raise keyring.errors.KeyringError("no such item")
        del self._store[(service, username)]


def test_delete_absent_with_generic_error_is_idempotent(monkeypatch):
    """A generic KeyringError on delete counts as "already gone" only after a read
    confirms nothing is there."""
    backend = _GenericNotFoundKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    credential_store.delete_password("never-stored")  # no raise


class _NativeErrorKeyring(KeyringBackend):
    """The Windows backend lets its native ``pywintypes.error`` through unwrapped (e.g.
    1312, no logon session) — not a KeyringError at all."""

    priority = 5  # type: ignore[assignment]

    def get_password(self, service, username):
        raise OSError(1312, "A specified logon session does not exist")

    def set_password(self, service, username, password):
        raise OSError(1312, "A specified logon session does not exist")

    def delete_password(self, service, username):
        raise OSError(1312, "A specified logon session does not exist")


def test_native_backend_error_fails_closed_on_every_op(monkeypatch):
    backend = _NativeErrorKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    with pytest.raises(KeyringUnavailableError):
        credential_store.set_password("env-a", "s3cret")
    with pytest.raises(KeyringUnavailableError):
        credential_store.get_password("env-a")
    with pytest.raises(KeyringUnavailableError):
        credential_store.delete_password("env-a")


class _DeniedDeleteKeyring(_MemoryKeyring):
    """macOS-style: PasswordDeleteError for ANY delete failure, entry still present."""

    def delete_password(self, service, username):
        raise keyring.errors.PasswordDeleteError("access denied")


def test_password_delete_error_with_entry_still_present_fails_closed(monkeypatch):
    backend = _DeniedDeleteKeyring()
    backend.set_password("agora-remote-env", credential_store.credential_key("env-a"), "s3cret")
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    with pytest.raises(KeyringUnavailableError):
        credential_store.delete_password("env-a")


def test_backend_that_fails_to_load_is_unavailable(monkeypatch):
    def _broken():
        raise ImportError("No module named 'missing_backend'")

    monkeypatch.setattr(keyring, "get_keyring", _broken)
    with pytest.raises(KeyringUnavailableError):
        credential_store.get_password("env-a")



class _CaseInsensitiveKeyring(_MemoryKeyring):
    """Windows-like: credential target names compare case-insensitively."""

    def get_password(self, service, username):
        return self._store.get((service.lower(), username.lower()))

    def set_password(self, service, username, password):
        self._store[(service.lower(), username.lower())] = password


def test_ids_differing_only_in_case_never_share_a_password(monkeypatch):
    """`Prod` and `prod` are distinct environments; on a case-insensitive store they
    must still not read each other's password (it would be sent to the wrong host)."""
    backend = _CaseInsensitiveKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    credential_store.set_password("Prod", "prod-host-password")
    credential_store.set_password("prod", "other-host-password")
    assert credential_store.get_password("Prod") == "prod-host-password"
    assert credential_store.get_password("prod") == "other-host-password"
