"""OS keyring for the remote-environment password credential — CR-4 slice 2 (adr/0001).

A password credential for a registered remote environment lives in the platform's
own secret store (Windows Credential Locker / macOS Keychain / Linux Secret Service),
**never** in Agora's control-plane database (NFR-2 / SECURITY-12 — the mirror is
public). A private-key *path* is not a secret and stays in the DB; only a password
uses this module.

Two rules this module upholds, both from adr/0001:

* **fail closed, never in the clear.** When the ``keyring`` library has no usable
  backend (e.g. a headless Linux box with no Secret Service), every operation raises
  :class:`KeyringUnavailableError` — it does not fall back to storing or reporting the
  password anywhere unprotected. ``has_password`` raises too rather than answering
  ``False``, so a caller cannot mistake "no backend" for "no credential set" (the
  second constitution rule: 读不到 / 没有 are different).
* **one entry per environment.** Keyed by ``service="agora-remote-env",
  username=<encoded environment_id>`` (see :func:`credential_key`). The id is immutable (slice-1 design), so the key is
  stable; ``delete_password`` is what the registry's ``delete`` calls so a removed
  environment does not orphan a secret in the OS store.
"""
from __future__ import annotations

import os

import keyring
from keyring.errors import NoKeyringError

#: One keyring service namespace for every remote-environment password. The per-row
#: key is the environment_id (immutable, so the keyring key never has to move).
#: Known gap (CR-11, deferred): the namespace is per OS account, not per control-plane
#: database — two Agora configurations under one account that register the same id
#: share one entry (one overwrites or deletes the other's password).
_SERVICE = "agora-remote-env"


def _service() -> str:
    """The namespace: ``AGORA_KEYRING_SERVICE`` when set (a development install uses its
    own, so it never reads, overwrites or deletes the stable install's passwords — CR-31),
    the shared default otherwise."""
    return os.environ.get("AGORA_KEYRING_SERVICE", "").strip() or _SERVICE


def credential_key(environment_id: str) -> str:
    """The keyring username for ``environment_id``: the id's UTF-8 bytes as lowercase
    hex. Windows credential target names are case-insensitive, so the raw ids ``Prod``
    and ``prod`` would share one entry — and one environment would connect with the
    other's password. Hex is case-free and one-to-one, so distinct ids never collide."""
    return "env-" + environment_id.encode("utf-8").hex()


class KeyringUnavailableError(RuntimeError):
    """The OS keyring backend is not usable on this machine, so a password credential
    can neither be stored nor read. Distinct from "no credential set": this is
    "this machine cannot keep a secret", which the product must report as itself
    rather than as an absent credential (adr/0001, fail-closed)."""


def _guard():
    """Raise :class:`KeyringUnavailableError` if there is no usable keyring backend.

    ``keyring.get_keyring()`` returns a ``fail.Keyring`` (chosen when no real backend
    is present) whose operations raise ``NoKeyringError``. Detecting it up front turns
    every operation's failure into one explicit, testable error instead of a mid-write
    surprise. A backend that fails to even load (e.g. ``PYTHON_KEYRING_BACKEND`` names
    a module that is not installed) raises from ``get_keyring`` — that is the same
    "cannot keep a secret here", not a crash."""
    try:
        backend = keyring.get_keyring()
    except Exception as exc:
        raise KeyringUnavailableError(f"OS keyring backend failed to load: {exc}") from exc
    # keyring's null backend has priority < 1; a real backend is >= 1. This is the
    # documented signal for "no usable backend", steadier than matching a class name.
    if getattr(backend, "priority", 0) < 1:
        raise KeyringUnavailableError(
            "this machine has no usable OS keyring backend, so a password credential "
            "cannot be stored or read; use a key-path credential or set the env var"
        )


def set_password(environment_id: str, password: str) -> None:
    """Store the password for ``environment_id`` in the OS keyring, replacing any
    existing one. Raises :class:`KeyringUnavailableError` if there is no backend —
    never stores it anywhere else."""
    _guard()
    try:
        keyring.set_password(_service(), credential_key(environment_id), password)
    except NoKeyringError as exc:
        raise KeyringUnavailableError(str(exc)) from exc
    except Exception as exc:
        # A backend that is present but unusable at runtime (a locked Keychain / Secret
        # Service) raises a generic KeyringError that _guard's priority check does not
        # pre-empt — and the Windows backend lets its native ``pywintypes.error`` through
        # unwrapped (e.g. error 1312, no logon session). Treat any of them as unavailable
        # so the caller gets a clean fail-closed error (a 422), not an unhandled 500 —
        # still no secret stored or leaked.
        raise KeyringUnavailableError(f"OS keyring is unusable: {exc}") from exc


def get_password(environment_id: str) -> str | None:
    """The stored password for ``environment_id``, or ``None`` if none is set. Raises
    :class:`KeyringUnavailableError` if there is no backend (which is NOT the same as
    ``None`` — one is "cannot look", the other is "looked, nothing there")."""
    _guard()
    try:
        return keyring.get_password(_service(), credential_key(environment_id))
    except NoKeyringError as exc:
        raise KeyringUnavailableError(str(exc)) from exc
    except Exception as exc:
        # Present-but-unusable backend (locked, or a native Windows error): fail closed
        # as unavailable rather than letting it become a 500.
        raise KeyringUnavailableError(f"OS keyring is unusable: {exc}") from exc


def has_password(environment_id: str) -> bool:
    """Whether a password is set for ``environment_id``. Raises
    :class:`KeyringUnavailableError` if there is no backend rather than answering
    ``False`` — the product must tell "no keyring here" apart from "no password set"."""
    return get_password(environment_id) is not None


def delete_password(environment_id: str) -> None:
    """Forget the password for ``environment_id``. Idempotent: deleting an absent entry
    is not an error (a delete that races another delete, or a key-path environment that
    never had one, must not raise). Raises :class:`KeyringUnavailableError` when there
    is no backend, or when the backend cannot confirm the entry is gone (a locked
    store) — a delete that did not happen is never reported as done."""
    _guard()
    try:
        keyring.delete_password(_service(), credential_key(environment_id))
    except NoKeyringError as exc:
        raise KeyringUnavailableError(str(exc)) from exc
    except Exception as exc:
        # Every delete error is ambiguous. PasswordDeleteError means "not found" on
        # some backends but any Keychain failure on macOS (access denied, secret still
        # there); a generic KeyringError or a native Windows error may be either too.
        # Look before calling it done — only a read that finds nothing makes it
        # absent, which keeps deleting an absent entry idempotent.
        try:
            still_there = keyring.get_password(_service(), credential_key(environment_id)) is not None
        except Exception:
            still_there = True  # cannot look, so cannot claim it is gone
        if still_there:
            raise KeyringUnavailableError(
                f"OS keyring could not delete the entry: {exc}"
            ) from exc
