"""How much of each agent's subscription limits is used (workspace CR-87, = CR-12 item 6).

Only official paths, and nothing read from a credential (CR-12: no OAuth token, no
unpublished endpoint):

- claude: the ``rate_limits`` Claude Code sends its status line, recorded by the work
  page's status-line relay (``statusline_relay.py``). Present only for claude.ai
  Pro/Max/Ultra subscribers, and only after a session's first answer.
- codex: ``codex app-server``'s ``account/rateLimits/read``, a short-lived process per
  read, kept for ``CODEX_TTL`` seconds.
- kiro-cli: ``kiro-cli acp --agent-engine=v3``'s ``_kiro/account/getUsage`` — the call
  kiro's own ``/usage`` makes, through the installed CLI; a short-lived process per read,
  kept for ``KIRO_TTL`` seconds. A kiro without it (before 3.x) is said to be unreadable.

Every answer is ``ok`` with what was read and when, or ``unavailable`` with why — an
agent whose usage could not be read is never shown as unused.
"""
from __future__ import annotations

import json
import math
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
KIRO_TTL = 300.0  # credits move slowly; a month-long budget
KIRO_TIMEOUT = 20.0
_WINDOW_NAMES = {"five_hour": "5h", "seven_day": "7d"}

_codex_lock = threading.Lock()
_codex_cache: tuple[float, dict[str, Any]] | None = None
_kiro_lock = threading.Lock()
_kiro_cache: tuple[float, dict[str, Any]] | None = None


def _unavailable(reason: str) -> dict[str, Any]:
    return {"state": "unavailable", "reason": reason}


def _number(value: object) -> float | None:
    """A finite number, or None: NaN or infinity would not serialise, and would take the
    other agents' readings down with it (kiro review)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


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


def _ask(argv: list[str], requests: list[dict[str, Any]], name: str,
         timeout: float) -> dict[str, Any]:
    """Answer ``id`` 2 of a short-lived JSON-RPC server on stdio, or why not: the first
    request (``id`` 1, ``initialize``) is sent at once, the rest once it is answered.
    Returns the message itself, or ``unavailable``."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                start_new_session=os.name != "nt")
    except OSError as exc:
        return _unavailable(f"could not start {name} ({type(exc).__name__})")
    # Into a job object at once, so what the wrapper starts is held with it.
    job = ExecutionDispatcher._adopt_process_tree(proc)
    answer: dict[str, Any] | None = None
    reader: threading.Thread | None = None
    deadline = time.monotonic() + timeout
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
            first = next((m for m in result if m.get("id") == 1), None)
            if first is not None and "error" in first:
                answer = first  # a failed handshake is the answer: nothing more is asked (kiro review)
                break
            if sent == 1 and first is not None:
                for request in requests[1:]:
                    proc.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
                proc.stdin.flush()
                sent = len(requests)
            reader.join(0.1)
        if answer is None:
            answer = next((m for m in result if m.get("id") == 2), None)
    except OSError as exc:
        return _unavailable(f"{name} failed ({type(exc).__name__})")
    finally:
        _end(proc, reader, job)
    if answer is None:
        return _unavailable(f"{name} did not answer")
    return answer


def _error_of(answer: dict[str, Any], name: str) -> dict[str, Any] | None:
    if "error" not in answer:
        return None
    error = answer.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    step = "initialize failed: " if answer.get("id") == 1 else ""
    return _unavailable(f"{name}: {step}{message or 'error'}"[:200])


def _ask_codex(exe: str) -> dict[str, Any]:
    """One ``account/rateLimits/read`` from a short-lived app-server, or why not."""
    answer = _ask([exe, "app-server"], [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "agora", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"},
    ], "codex app-server", CODEX_TIMEOUT)
    if answer.get("state") == "unavailable":
        return answer
    return _error_of(answer, "codex") or parse_codex(answer.get("result"))


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


# --- kiro -----------------------------------------------------------------------------------
def _kiro_exe() -> str | None:
    return shutil.which("kiro-cli") or (shutil.which("kiro-cli.cmd") if os.name == "nt" else None)


def _ask_kiro(exe: str) -> dict[str, Any]:
    """One ``_kiro/account/getUsage`` from a short-lived ``kiro-cli acp``, or why not. No
    session is made: nothing is left in kiro's session list (measured, 3.1.0)."""
    answer = _ask([exe, "acp", "--agent-engine=v3"], [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": 1, "clientCapabilities": {},
                    "clientInfo": {"name": "agora", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "_kiro/account/getUsage", "params": {}},
    ], "kiro-cli acp", KIRO_TIMEOUT)
    if answer.get("state") == "unavailable":
        return answer
    error = answer.get("error")
    if answer.get("id") == 2 and isinstance(error, dict) and error.get("code") == -32601:
        return _unavailable("kiro_no_api")  # a kiro that has no such call
    return _error_of(answer, "kiro") or parse_kiro(answer.get("result"))


def parse_kiro(result: object) -> dict[str, Any]:
    """The credits from a ``getUsage`` result: the breakdown of resource type ``CREDIT``
    (or named so) with a limit; another limited resource is never shown as credits. Left = limit - used, never below 0; add-on and
    bonus credits are not counted, as kiro's own status line does not count them."""
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(result, dict) and result.get("success") is False:
        message = result.get("message")
        return _unavailable(f"kiro: {message}"[:200] if isinstance(message, str) and message
                            else "kiro answered without usage")
    breakdowns = data.get("usageBreakdowns") if isinstance(data, dict) else None
    if not isinstance(breakdowns, list):
        return _unavailable("kiro answered without usage")
    limited = [b for b in breakdowns if isinstance(b, dict) and b.get("hasLimit") is True
               and (_number(b.get("limit")) or 0) > 0 and (_number(b.get("used")) or 0) >= 0
               and _number(b.get("used")) is not None]
    if not limited:
        return _unavailable("kiro answered without a credit limit")
    # By the stable resource type first; the display name is localisable (kiro review).
    chosen = next((b for b in limited if b.get("resourceType") == "CREDIT"), None) \
        or next((b for b in limited if str(b.get("displayName", "")).strip().lower() in ("credit", "credits")), None)
    if chosen is None:
        return _unavailable("kiro answered without a credit limit")
    limit, used = float(chosen["limit"]), float(chosen["used"])
    return {"state": "ok",
            "windows": [{"label": "month", "used": min(100.0, used / limit * 100),
                         "resets_at": _date_at(data.get("billingCycleReset")), "reset": False}],
            "read_at": time.time(),
            "credits_left": max(0.0, limit - used), "credits_limit": limit,
            "limit_reached": used >= limit and data.get("overagesEnabled") is not True,
            "plan": data.get("planName") if isinstance(data.get("planName"), str) else None}


def _date_at(value: object) -> float | None:
    """``2026-12-01`` as Unix seconds at that day's start, UTC (kiro does not say the
    zone); None when it is not a date."""
    if not isinstance(value, str):
        return None
    try:
        from datetime import datetime, timezone
        return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def read_kiro(*, now: float | None = None) -> dict[str, Any]:
    """Kept for ``KIRO_TTL`` seconds; one read at a time."""
    global _kiro_cache
    with _kiro_lock:
        moment = time.time() if now is None else now
        if _kiro_cache is not None and moment - _kiro_cache[0] < KIRO_TTL:
            return _kiro_cache[1]
        exe = _kiro_exe()
        answer = _unavailable("kiro-cli is not installed") if exe is None else _ask_kiro(exe)
        _kiro_cache = (moment, answer)
        return answer


def read_all(usage_dir: Path | None) -> dict[str, Any]:
    return {"claude": read_claude(usage_dir), "codex": read_codex(), "kiro": read_kiro()}
