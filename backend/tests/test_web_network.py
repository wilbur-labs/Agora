from __future__ import annotations

import pytest

import agora.tools.web as web


def test_direct_network_mode_ignores_proxy_environment(monkeypatch):
    monkeypatch.setattr(web, "get_config", lambda: {"web": {"network_mode": "direct"}})

    assert web._client_options() == {"trust_env": False}


def test_system_network_mode_inherits_proxy_environment(monkeypatch):
    monkeypatch.setattr(web, "get_config", lambda: {"web": {"network_mode": "system"}})

    assert web._client_options() == {"trust_env": True}


def test_system_network_mode_is_backward_compatible_default(monkeypatch):
    monkeypatch.setattr(web, "get_config", lambda: {})

    assert web._client_options() == {"trust_env": True}


def test_invalid_network_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(web, "get_config", lambda: {"web": {"network_mode": "magic"}})

    with pytest.raises(ValueError, match="direct.*system"):
        web._client_options()
