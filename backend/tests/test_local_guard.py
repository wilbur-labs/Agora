"""The local-origin guard (CR-34): web pages may not drive this Agora."""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agora.api.local_guard import LocalOriginGuard, host_allowed, origin_allowed


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/r")
    def read():
        return {"ok": True}

    @app.post("/w")
    def write():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("hi")
        await socket.close()

    app.add_middleware(LocalOriginGuard)
    return app


@pytest.fixture
def local(monkeypatch):
    monkeypatch.delenv("AGORA_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("AGORA_DEV_ORIGINS", raising=False)
    return TestClient(_app(), base_url="http://127.0.0.1:8000")


def test_same_origin_and_originless_writes_pass(local):
    assert local.post("/w", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    assert local.post("/w").status_code == 200  # curl, the CLI


def test_a_foreign_page_cannot_write(local):
    for origin in ("https://evil.example", "http://localhost:3000", "null", "http://127.0.0.1:8001"):
        resp = local.post("/w", headers={"Origin": origin})
        assert resp.status_code == 403, origin
    # Reads stay open: a cross-origin GET cannot read the answer without CORS anyway.
    assert local.get("/r", headers={"Origin": "https://evil.example"}).status_code == 200


def test_dns_rebinding_is_refused(local):
    assert local.get("/r", headers={"Host": "evil.example:8000"}).status_code == 403
    assert local.get("/r", headers={"Host": "localhost:8000"}).status_code == 200
    assert local.get("/r", headers={"Host": "[::1]:8000"}).status_code == 200


def test_websocket_origin_is_checked(local):
    with local.websocket_connect("ws://127.0.0.1:8000/ws", headers={"Origin": "http://127.0.0.1:8000"}) as ws:
        assert ws.receive_text() == "hi"
    with pytest.raises(WebSocketDisconnect):
        with local.websocket_connect("ws://127.0.0.1:8000/ws", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()


def test_configured_hosts_and_dev_origins(monkeypatch):
    monkeypatch.setenv("AGORA_ALLOWED_HOSTS", "10.36.94.35")
    monkeypatch.setenv("AGORA_DEV_ORIGINS", "http://127.0.0.1:3000")
    assert host_allowed("10.36.94.35:8000")
    assert not host_allowed("evil.example")
    assert origin_allowed("http://127.0.0.1:3000", "127.0.0.1:8000")
    monkeypatch.setenv("AGORA_ALLOWED_HOSTS", "*")
    assert host_allowed("anything:1")


def test_same_origin_means_scheme_host_and_port():
    """review: http://localhost is not the origin of https://localhost."""
    assert origin_allowed("http://localhost", "localhost", "http")
    assert not origin_allowed("http://localhost", "localhost", "https")
    assert origin_allowed("https://localhost", "localhost:443", "https")
    assert origin_allowed("http://127.0.0.1:8000", "127.0.0.1:8000", "ws")
    assert not origin_allowed("http://127.0.0.1:8000", "127.0.0.1:8000", "wss")
    assert not origin_allowed("ftp://127.0.0.1:8000", "127.0.0.1:8000", "http")


def test_a_malformed_origin_is_refused_not_an_error(local):
    """review: `http://[` used to raise inside the guard."""
    assert not origin_allowed("http://[", "127.0.0.1:8000", "http")
    assert local.post("/w", headers={"Origin": "http://["}).status_code == 403
