"""Test connection for a registered remote environment (CR-4 slice 2, second half).

One check, in the order a run depends on it, each step saying what it established and
stopping where the next step would mean trusting something not yet trusted:

1. **Host key** — the key the server presents, compared with the files this
   environment trusts, *without* authenticating or pinning. Unknown under TOFU is shown
   with its fingerprint for the person to confirm (``pin_confirmed_host_key``); the test
   itself never records it. Unknown under strict, a mismatch, or no key: stop.
2. **Sign-in** — only once the key is trusted.
3. **Project root** — is a directory, is absent, or cannot be observed.
4. **Agent CLIs** — on the same PATH a run gets (login shell + user bin dirs).
5. **claude-workspace tool directory** — when the environment names one.

Every "could not tell" is said as such; nothing here turns a failed read into "no".
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agora.messages import say
from agora.environments_remote import RemoteHostKeyError, _agent_path_word

CHECK_TIMEOUT = 30.0
_CLIS = ("claude", "codex", "kiro-cli")


async def _check(environment: Any, record: Any) -> dict[str, Any]:
    # Verify only, on every connect and reconnect: a test never adds trust (codex review).
    environment.pin_on_connect = False
    result: dict[str, Any] = {"host_key": None, "auth": "skipped", "auth_detail": None,
                              "root": "skipped", "clis": None, "tool": None}
    try:
        key, trust = await environment.presented_host_key()
    except Exception as exc:  # noqa: BLE001 - unreachable, refused, timed out: said, not guessed
        result["host_key"] = {"status": "unreachable", "detail": f"{type(exc).__name__}: {exc}"[:300]}
        return result
    result["host_key"] = {"status": trust, "fingerprint": key.get_fingerprint("sha256"),
                          "algorithm": key.get_algorithm(), "mode": record.host_key_mode}
    if trust not in ("trusted", "ca"):
        return result
    try:
        conn = await environment._ensure_conn()
    except RemoteHostKeyError as exc:
        result["auth"], result["auth_detail"] = "refused", str(exc)[:300]
        return result
    except Exception as exc:  # noqa: BLE001 - sign-in failed: the reason, never the credential
        result["auth"], result["auth_detail"] = "failed", type(exc).__name__
        return result
    result["auth"] = "ok"
    probe = await environment.probe(Path(record.remote_project_root))
    result["root"] = {"dir": "dir", "absent": "absent", "unknown": "unknown"}.get(probe.kind, "not_dir")
    path_word = _agent_path_word(await environment._login_path_for(conn))
    script = f"export {path_word}; for c in {' '.join(_CLIS)}; do printf '%s=' \"$c\"; command -v \"$c\" || echo; done"
    found = await environment.run(["sh", "-c", script], timeout=15, read_only=True)
    if found.outcome == "completed" and found.returncode == 0:
        clis: dict[str, str | None] = {c: None for c in _CLIS}
        for line in found.stdout.splitlines():
            name, _, where = line.partition("=")
            if name in clis:
                clis[name] = where.strip() or None
        result["clis"] = clis
    if record.workspace_tool_dir:
        from agora.terminal.remote_tasks import RemoteTaskRefused, resolve_tool

        try:
            tool, work = await resolve_tool(environment, record.workspace_tool_dir)
            result["tool"] = {"ok": True, "tool_dir": str(tool), "work_root": str(work)}
        except RemoteTaskRefused as exc:
            result["tool"] = {"ok": False, "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001
            result["tool"] = {"ok": False, "reason": say("connection.tool_unreadable", error_type=type(exc).__name__)}
    return result


async def check_environment(environment: Any, record: Any) -> dict[str, Any]:
    """Run the check, bounded as a whole. ``timed_out`` says the answer stopped early."""
    try:
        return {**await asyncio.wait_for(_check(environment, record), timeout=CHECK_TIMEOUT), "timed_out": False}
    except (asyncio.TimeoutError, TimeoutError):
        return {"host_key": None, "auth": "skipped", "auth_detail": None, "root": "skipped",
                "clis": None, "tool": None, "timed_out": True}
