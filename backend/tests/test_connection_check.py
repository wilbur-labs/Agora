"""Test connection and host-key confirmation on the environment page (CR-4 slice 2, second half)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import asyncssh
import pytest

from agora.environments_remote import RemoteEnvironment, RemoteHostKeyError
from agora.execution.connection_check import check_environment


def _key():
    return asyncssh.generate_private_key("ssh-ed25519").convert_to_public()


def _env(tmp_path, mode="tofu"):
    return RemoteEnvironment(host="h.example", user="u", secret_ref="", credential_source="ssh_default",
                             host_key_mode=mode, known_hosts_path=str(tmp_path / "known_hosts"))


@pytest.fixture
def presents(monkeypatch, tmp_path):
    """The server's presented key; the operator's own known_hosts is empty."""
    monkeypatch.setattr("agora.environments_remote._user_known_hosts", lambda: tmp_path / "operator_known_hosts")
    state = {"key": _key(), "asked": []}

    async def fake(host, port, config=None, **kwargs):
        state["asked"].append(kwargs.get("server_host_key_algs"))
        return state["key"]

    monkeypatch.setattr(asyncssh, "get_server_host_key", fake)
    return state


def test_an_unknown_key_is_recorded_only_after_the_seen_fingerprint_is_confirmed(tmp_path, presents):
    env = _env(tmp_path)
    key, trust = asyncio.run(env.presented_host_key())
    assert trust == "unknown" and not (tmp_path / "known_hosts").exists()  # looking pins nothing
    with pytest.raises(RemoteHostKeyError, match="nothing recorded"):
        asyncio.run(env.pin_confirmed_host_key("SHA256:not-the-one"))
    assert not (tmp_path / "known_hosts").exists()
    fingerprint = key.get_fingerprint("sha256")
    assert asyncio.run(env.pin_confirmed_host_key(fingerprint)) == fingerprint
    assert asyncio.run(env.presented_host_key())[1] == "trusted"


def test_a_different_key_for_a_host_on_file_is_a_mismatch_and_is_never_recorded(tmp_path, presents):
    env = _env(tmp_path)
    first = presents["key"]
    asyncio.run(env.pin_confirmed_host_key(first.get_fingerprint("sha256")))
    presents["key"] = _key()  # the host now presents another key
    _, trust = asyncio.run(env.presented_host_key())
    assert trust == "mismatch"
    assert presents["asked"][-1] == ["ssh-ed25519"]  # asked for the algorithm on file
    with pytest.raises(RemoteHostKeyError):
        asyncio.run(env.pin_confirmed_host_key(presents["key"].get_fingerprint("sha256")))


def test_a_strict_environment_is_never_edited_by_agora(tmp_path, presents):
    env = _env(tmp_path, mode="strict")
    with pytest.raises(RemoteHostKeyError, match="TOFU"):
        asyncio.run(env.pin_confirmed_host_key(presents["key"].get_fingerprint("sha256")))


def test_the_check_stops_before_signing_in_when_the_key_is_not_trusted(tmp_path, presents):
    env = _env(tmp_path)
    record = SimpleNamespace(host_key_mode="tofu", remote_project_root="/srv/p", workspace_tool_dir=None)
    result = asyncio.run(check_environment(env, record))
    assert result["host_key"]["status"] == "unknown" and result["host_key"]["fingerprint"].startswith("SHA256:")
    assert result["auth"] == "skipped" and result["root"] == "skipped"
    assert not (tmp_path / "known_hosts").exists()


def test_an_unreachable_host_is_said_as_unreachable(tmp_path, monkeypatch):
    async def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(asyncssh, "get_server_host_key", refuse)
    record = SimpleNamespace(host_key_mode="strict", remote_project_root="/srv/p", workspace_tool_dir=None)
    result = asyncio.run(check_environment(_env(tmp_path, "strict"), record))
    assert result["host_key"]["status"] == "unreachable" and "refused" in result["host_key"]["detail"]


def test_a_key_recorded_under_the_hosts_address_is_trusted(tmp_path, presents, monkeypatch):
    env = _env(tmp_path, mode="strict")
    key = presents["key"]
    (tmp_path / "known_hosts").write_text(
        f"10.0.0.7 {key.export_public_key('openssh').decode().split()[0]} {key.export_public_key('openssh').decode().split()[1]}\n",
        encoding="utf-8")

    async def addresses():
        return ["10.0.0.7"]

    monkeypatch.setattr(env, "_addresses", addresses)
    assert asyncio.run(env.presented_host_key())[1] == "trusted"


def test_a_trusted_ca_leaves_a_replaced_key_to_the_connection(tmp_path, presents):
    env = _env(tmp_path, mode="strict")
    old = _key()
    ca = asyncssh.generate_private_key("ssh-ed25519").convert_to_public()
    line = lambda prefix, k: f"{prefix}h.example {' '.join(k.export_public_key('openssh').decode().split()[:2])}\n"
    (tmp_path / "known_hosts").write_text(line("", old) + line("@cert-authority ", ca), encoding="utf-8")
    assert asyncio.run(env.presented_host_key())[1] == "ca"


def test_a_test_connection_never_records_a_key_even_when_it_signs_in(tmp_path, presents, monkeypatch):
    env = _env(tmp_path)  # TOFU
    key = presents["key"]
    head = " ".join(key.export_public_key("openssh").decode().split()[:2])
    (tmp_path / "known_hosts").write_text(f"10.0.0.7 {head}\n", encoding="utf-8")  # trusted by address only

    async def addresses():
        return ["10.0.0.7"]

    async def no_pin():
        raise AssertionError("a test connection pinned a key")

    async def refuse(**kwargs):
        raise OSError("auth stopped here")

    monkeypatch.setattr(env, "_addresses", addresses)
    monkeypatch.setattr(env, "_tofu_pin_if_needed", no_pin)
    monkeypatch.setattr(asyncssh, "connect", refuse)
    record = SimpleNamespace(host_key_mode="tofu", remote_project_root="/srv/p", workspace_tool_dir=None)
    result = asyncio.run(check_environment(env, record))
    assert result["host_key"]["status"] == "trusted" and result["auth"] == "failed"
    assert (tmp_path / "known_hosts").read_text(encoding="utf-8") == f"10.0.0.7 {head}\n"


def test_a_revoked_key_is_said_as_revoked_and_never_recorded(tmp_path, presents):
    env = _env(tmp_path)  # TOFU
    key = presents["key"]
    head = " ".join(key.export_public_key("openssh").decode().split()[:2])
    (tmp_path / "known_hosts").write_text(f"@revoked h.example {head}\n", encoding="utf-8")
    assert asyncio.run(env.presented_host_key())[1] == "revoked"
    with pytest.raises(RemoteHostKeyError, match="revoked"):
        asyncio.run(env.pin_confirmed_host_key(key.get_fingerprint("sha256")))
    assert (tmp_path / "known_hosts").read_text(encoding="utf-8") == f"@revoked h.example {head}\n"
