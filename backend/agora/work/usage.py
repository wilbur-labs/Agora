"""How much of each agent's subscription limits is used (workspace CR-87, = CR-12 item 6).

Only official paths, and nothing read from a credential (CR-12: no OAuth token, no
unpublished endpoint):

- claude: the ``rate_limits`` Claude Code sends its status line, recorded by the work
  page's status-line relay (``statusline_relay.py``). Present only for claude.ai
  Pro/Max/Ultra subscribers, and only after a session's first answer.
- codex: ``codex app-server``'s ``account/rateLimits/read``, a short-lived process per
  read, kept for ``CODEX_TTL`` seconds.
- kiro-cli: has no non-interactive way to read ``/usage``; said so, not guessed.

Every answer is ``ok`` with what was read and when, or ``unavailable`` with why — an
agent whose usage could not be read is never shown as unused.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from agora.execution.dispatcher import ExecutionDispatcher

CODEX_TTL = 60.0
CODEX_TIMEOUT = 20.0
_WINDOW_NAMES = {"five_hour": "5h", "seven_day": "7d"}

_codex_lock = threading.Lock()
_codex_cache: tuple[float, dict[str, Any]] | None = None


def _unavailable(reason: str) -> dict[str, Any]:
    return {"state": "unavailable", "reason": reason}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# --- claude ---------------------------------------------------------------------------------
def read_claude(usage_dir: Path | None, *, now: float | None = None) -> dict[str, Any]:
    """A window whose reset time has passed since it was recorded is said as reset, with
    no figure: what was used before the reset says nothing about now."""
    moment = time.time() if now is None else now
    if usage_dir is None:
        return _unavailable("no_hook")
    try:
        data = json.loads((usage_dir / "claude.json").read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return _unavailable("not_reported")
    except (OSError, ValueError, UnicodeDecodeError):
        return _unavailable("unreadable")
    if not isinstance(data, dict):
        return _unavailable("unreadable")
    limits = data.get("limits")
    if not isinstance(limits, dict) or not limits:
        # The status line reported, but never with limits: not a subscription that has
        # them, or no answer yet in any work-page session.
        return {**_unavailable("no_limits"), "seen_at": _number(data.get("seen_at"))}
    windows = []
    for name, label in _WINDOW_NAMES.items():
        window = limits.get(name)
        if isinstance(window, dict) and _number(window.get("used_percentage")) is not None:
            resets_at = _number(window.get("resets_at"))
            passed = resets_at is not None and resets_at <= moment
            windows.append({"label": label, "used": None if passed else _number(window["used_percentage"]),
                            "resets_at": resets_at, "reset": passed})
    if not windows:
        return _unavailable("unreadable")
    return {"state": "ok", "windows": windows, "read_at": _number(data.get("limits_at"))}


# --- codex ----------------------------------------------------------------------------------
def _codex_exe() -> str | None:
    return shutil.which("codex") or (shutil.which("codex.cmd") if os.name == "nt" else None)


def _end(proc: subprocess.Popen, reader: threading.Thread | None, job: object | None = None) -> None:
    """The whole process tree, not only ``proc``: on Windows ``codex.cmd`` is a wrapper
    whose node child holds the pipes, and killing the wrapper alone left it running with
    the reader blocked on it (codex review round 1). The job object holds every
    descendant (as for runs); ``taskkill /T`` only when there is none — measured, it can
    take a minute. Then the pipes, then the reader."""
    try:
        if job is not None:
            ExecutionDispatcher._release_process_tree(job)
        elif os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            # Its own process group, started that way: the group goes with it, before
            # the pipes are closed — a child still holding them would otherwise block
            # the close and, with it, every later read (codex review round 2).
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
    except (OSError, subprocess.SubprocessError):
        pass
    for pipe in (proc.stdin, proc.stdout):
        try:
            if pipe is not None:
                pipe.close()
        except OSError:
            pass
    try:
        proc.wait(5)
    except (OSError, subprocess.SubprocessError):
        pass
    if reader is not None:
        reader.join(5)


def _ask_codex(exe: str) -> dict[str, Any]:
    """One ``account/rateLimits/read`` from a short-lived app-server, or why not."""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "agora", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"},
    ]
    try:
        proc = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                start_new_session=os.name != "nt")
    except OSError as exc:
        return _unavailable(f"could not start codex ({type(exc).__name__})")
    # Into a job object at once, so what the wrapper starts is held with it.
    job = ExecutionDispatcher._adopt_process_tree(proc)
    answer: dict[str, Any] | None = None
    reader: threading.Thread | None = None
    deadline = time.monotonic() + CODEX_TIMEOUT
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(requests[0]).encode("utf-8") + b"\n")
        proc.stdin.flush()
        sent = 1
        result: list[dict[str, Any]] = []

        def read() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                try:
                    message = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(message, dict):
                    result.append(message)
                    if message.get("id") == 2:
                        return

        def read_safely() -> None:
            try:
                read()
            except (OSError, ValueError):
                pass  # the pipe closed under it: the read is over

        reader = threading.Thread(target=read_safely, daemon=True)
        reader.start()
        while time.monotonic() < deadline and reader.is_alive():
            if sent == 1 and any(m.get("id") == 1 for m in result):
                for request in requests[1:]:
                    proc.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
                proc.stdin.flush()
                sent = 3
            reader.join(0.1)
        answer = next((m for m in result if m.get("id") == 2), None)
    except OSError as exc:
        return _unavailable(f"codex app-server failed ({type(exc).__name__})")
    finally:
        _end(proc, reader, job)
    if answer is None:
        return _unavailable("codex app-server did not answer")
    if "error" in answer:
        error = answer.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        return _unavailable(f"codex: {message or 'error'}"[:200])
    return parse_codex(answer.get("result"))


def parse_codex(result: object) -> dict[str, Any]:
    """The windows and reset credits from an ``account/rateLimits/read`` result. Only
    what the bar shows is kept: no account id, no plan details beyond its name."""
    if not isinstance(result, dict) or not isinstance(result.get("rateLimits"), dict):
        return _unavailable("codex answered without rate limits")
    limits = result["rateLimits"]
    windows = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not isinstance(window, dict) or _number(window.get("usedPercent")) is None:
            continue
        minutes = _number(window.get("windowDurationMins"))
        label = (f"{int(minutes // 60)}h" if minutes and minutes < 1440 else f"{int(minutes // 1440)}d") \
            if minutes else key
        windows.append({"label": label, "used": _number(window["usedPercent"]),
                        "resets_at": _number(window.get("resetsAt")), "reset": False})
    if not windows:
        return _unavailable("codex answered without rate limits")
    credits = result.get("rateLimitResetCredits")
    available = _number(credits.get("availableCount")) if isinstance(credits, dict) else None
    return {"state": "ok", "windows": windows, "read_at": time.time(),
            "reset_credits": int(available) if available is not None else None,
            "limit_reached": limits.get("rateLimitReachedType") is not None,
            "plan": limits.get("planType") if isinstance(limits.get("planType"), str) else None}


def read_codex(*, now: float | None = None) -> dict[str, Any]:
    """Kept for ``CODEX_TTL`` seconds; one read at a time."""
    global _codex_cache
    with _codex_lock:
        moment = time.time() if now is None else now
        if _codex_cache is not None and moment - _codex_cache[0] < CODEX_TTL:
            return _codex_cache[1]
        exe = _codex_exe()
        answer = _unavailable("codex is not installed") if exe is None else _ask_codex(exe)
        _codex_cache = (moment, answer)
        return answer


def read_kiro() -> dict[str, Any]:
    return _unavailable("kiro_no_api")


def read_all(usage_dir: Path | None) -> dict[str, Any]:
    return {"claude": read_claude(usage_dir), "codex": read_codex(), "kiro": read_kiro()}
