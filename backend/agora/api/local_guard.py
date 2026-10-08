"""Who may drive this Agora: the person at this machine, not any web page they open (CR-34).

Agora's API has no authentication on most routes and runs actions — registering
projects, dispatching agents that change code. Two browser-borne ways in are closed here:

* **Cross-site requests.** ``CORSMiddleware(allow_origins=["*"])`` used to let any page
  the person visited call the API from their browser. Now a request that changes state
  (anything but GET / HEAD / OPTIONS) and carries an ``Origin`` must come from this
  server's own origin, or from an origin named in ``AGORA_DEV_ORIGINS`` (the Next dev
  server on :3000, off unless a development install turns it on). WebSocket handshakes
  are held to the same rule whatever their method. A request with no ``Origin`` — curl,
  the CLI, the test client — is the person's own process and is let through.
* **DNS rebinding.** A page on ``evil.example`` whose name later resolves to 127.0.0.1
  is "same origin" with itself. The ``Host`` header still says ``evil.example``, so only
  the loopback names are accepted, plus whatever ``AGORA_ALLOWED_HOSTS`` adds (a server
  reached by its address, e.g. the Docker deployment; ``*`` turns the check off).

What this does NOT do: authenticate. Another account on the same machine can still
reach 127.0.0.1 directly; the terminal (CR-30) is held to a per-launch token for that
reason, and the rest of the API is not (yet). This guard is about web pages.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def dev_origins() -> list[str]:
    """Extra browser origins allowed to call this server (``AGORA_DEV_ORIGINS``)."""
    return [origin.rstrip("/").lower() for origin in _env_list("AGORA_DEV_ORIGINS")]


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8000
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header: str | None) -> bool:
    extra = _env_list("AGORA_ALLOWED_HOSTS")
    if "*" in extra:
        return True
    if not host_header:
        return False
    return _hostname(host_header) in _LOOPBACK_HOSTS | {h.lower() for h in extra}


_DEFAULT_PORT = {"http": 80, "https": 443}


def _origin_tuple(scheme: str, netloc: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) with the default port filled in, or None if unparseable."""
    try:
        parts = urlsplit(f"{scheme}://{netloc}")
        port = parts.port or _DEFAULT_PORT.get(scheme)
    except ValueError:
        return None
    if parts.hostname is None or port is None:
        return None
    return scheme, parts.hostname.lower(), port


def origin_allowed(origin: str, host_header: str | None, scheme: str = "http") -> bool:
    """The exact origin of the server the request reached — scheme, host and port — or a
    configured dev origin. ``scheme`` is the request's own (``ws``/``wss`` count as
    ``http``/``https``): an ``http://`` page is not the same origin as an ``https://``
    server on the same name (review)."""
    origin = origin.strip().rstrip("/").lower()
    if not origin or origin == "null":
        return False
    if origin in dev_origins():
        return True
    if not host_header:
        return False
    served = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False  # a malformed Origin is refused, not a server error (review)
    if parts.scheme not in _DEFAULT_PORT:
        return False
    theirs = _origin_tuple(parts.scheme, parts.netloc)
    ours = _origin_tuple(served, host_header.strip().lower())
    return theirs is not None and theirs == ours


class LocalOriginGuard:
    """Pure ASGI, so it covers WebSocket handshakes as well as HTTP."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host")
        origin = headers.get("origin")
        reason = None
        if not host_allowed(host):
            reason = f"Host '{host}' is not this machine. Set AGORA_ALLOWED_HOSTS to reach Agora by another name."
        elif origin is not None and (scope["type"] == "websocket" or scope.get("method", "GET").upper() not in _SAFE_METHODS):
            if not origin_allowed(origin, host, scope.get("scheme", "http")):
                reason = f"Requests from '{origin}' may not change this Agora."
        if reason is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": reason[:120]})
            return
        body = json.dumps({"detail": reason}).encode("utf-8")
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
