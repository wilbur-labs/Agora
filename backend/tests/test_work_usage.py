"""Agent usage (workspace CR-87 = CR-12 item 6): only official paths, and an agent whose
usage could not be read says why — never shown as unused."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agora.terminal.auth import load_or_create_token
from agora.work import hooks as hooks_module
from agora.work import usage
from agora.work.projects import work_key
from agora.work.service import workspace_statusline

from tests.test_work import _service, api, run, world  # noqa: F401  (fixtures)

RELAY = Path(hooks_module.__file__).parent / "statusline_relay.py"


def _relay(stdin: dict | str, usage_dir: Path | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "AGORA_USAGE_DIR"}
    if usage_dir is not None:
        env["AGORA_USAGE_DIR"] = str(usage_dir)
    raw = stdin if isinstance(stdin, str) else json.dumps(stdin)
    return subprocess.run([sys.executable, str(RELAY)], input=raw.encode("utf-8"), capture_output=True,
                          env=env, timeout=30)


LIMITS = {"five_hour": {"used_percentage": 23.5, "resets_at": 4_000_000_000},
          "seven_day": {"used_percentage": 41.2, "resets_at": 4_100_000_000}}


# --- the relay ------------------------------------------------------------------------------
def test_the_relay_passes_the_document_on_unchanged_and_records_the_limits(tmp_path):
    document = {"model": {"display_name": "Opus"}, "workspace": {"current_dir": "E:/日本語"}, "rate_limits": LIMITS}
    raw = json.dumps(document, ensure_ascii=False)
    done = _relay(raw, tmp_path)
    assert done.returncode == 0
    assert done.stdout == raw.encode("utf-8")
    recorded = json.loads((tmp_path / "claude.json").read_text(encoding="utf-8"))
    assert recorded["limits"] == LIMITS and recorded["limits_at"] == recorded["seen_at"]


def test_a_report_without_limits_keeps_the_last_ones_and_says_it_was_seen(tmp_path):
    _relay({"rate_limits": LIMITS}, tmp_path)
    first = json.loads((tmp_path / "claude.json").read_text(encoding="utf-8"))
    time.sleep(0.01)
    _relay({"model": {}}, tmp_path)
    second = json.loads((tmp_path / "claude.json").read_text(encoding="utf-8"))
    assert second["limits"] == LIMITS and second["limits_at"] == first["limits_at"]
    assert second["seen_at"] > first["seen_at"]


@pytest.mark.parametrize("stdin", ["not json", "", json.dumps({"rate_limits": "x"}),
                                   json.dumps({"rate_limits": {"five_hour": {"used_percentage": "1"}}})])
def test_the_relay_never_fails_the_status_line(tmp_path, stdin):
    done = _relay(stdin, tmp_path)
    assert done.returncode == 0 and done.stdout == stdin.encode("utf-8")
    if (tmp_path / "claude.json").exists():
        assert json.loads((tmp_path / "claude.json").read_text(encoding="utf-8"))["limits"] is None


def test_the_relay_without_a_usage_directory_only_passes_on(tmp_path):
    done = _relay({"rate_limits": LIMITS}, None)
    assert done.returncode == 0 and b"rate_limits" in done.stdout


# --- reading claude ---------------------------------------------------------------------------
def _write(folder: Path, data: object) -> None:
    (folder / "claude.json").write_text(json.dumps(data), encoding="utf-8")


def test_claude_reads_what_the_relay_recorded(tmp_path):
    _write(tmp_path, {"seen_at": 10.0, "limits": LIMITS, "limits_at": 9.0})
    reading = usage.read_claude(tmp_path, now=100.0)
    assert reading == {"state": "ok", "read_at": 9.0, "windows": [
        {"label": "5h", "used": 23.5, "resets_at": 4_000_000_000, "reset": False},
        {"label": "7d", "used": 41.2, "resets_at": 4_100_000_000, "reset": False}]}


def test_a_window_past_its_reset_has_no_figure(tmp_path):
    _write(tmp_path, {"seen_at": 10.0, "limits": LIMITS, "limits_at": 9.0})
    reading = usage.read_claude(tmp_path, now=4_050_000_000)
    assert reading["windows"][0] == {"label": "5h", "used": None, "resets_at": 4_000_000_000, "reset": True}
    assert reading["windows"][1]["used"] == 41.2


def test_claude_unavailable_says_why(tmp_path):
    assert usage.read_claude(None)["reason"] == "no_hook"
    assert usage.read_claude(tmp_path)["reason"] == "not_reported"
    _write(tmp_path, {"seen_at": 10.0, "limits": None, "limits_at": None})
    assert usage.read_claude(tmp_path) == {"state": "unavailable", "reason": "no_limits", "seen_at": 10.0}
    (tmp_path / "claude.json").write_text("{broken", encoding="utf-8")
    assert usage.read_claude(tmp_path)["reason"] == "unreadable"


# --- reading codex ----------------------------------------------------------------------------
CODEX_RESULT = {
    "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 54, "windowDurationMins": 300, "resetsAt": 1794207273},
                   "secondary": {"usedPercent": 8, "windowDurationMins": 10080, "resetsAt": 1794794073},
                   "planType": "team", "rateLimitReachedType": None},
    "rateLimitResetCredits": {"availableCount": 1, "credits": [{"id": "secret-ish"}]},
    "accountId": "cda29cb7-0000",
}


def test_codex_keeps_the_windows_and_the_reset_credits_and_nothing_about_the_account():
    reading = usage.parse_codex(CODEX_RESULT)
    assert reading["state"] == "ok"
    assert [(w["label"], w["used"]) for w in reading["windows"]] == [("5h", 54), ("7d", 8)]
    assert reading["reset_credits"] == 1 and reading["limit_reached"] is False and reading["plan"] == "team"
    assert "cda29cb7" not in json.dumps(reading) and "secret-ish" not in json.dumps(reading)


def test_codex_without_limits_is_unavailable_not_zero():
    assert usage.parse_codex({})["state"] == "unavailable"
    assert usage.parse_codex({"rateLimits": {"primary": {"usedPercent": "54"}}})["state"] == "unavailable"


def test_codex_is_asked_once_a_minute(monkeypatch):
    calls = []
    monkeypatch.setattr(usage, "_codex_cache", None)
    monkeypatch.setattr(usage, "_codex_exe", lambda: "codex")
    monkeypatch.setattr(usage, "_ask_codex", lambda exe: calls.append(exe) or usage.parse_codex(CODEX_RESULT))
    usage.read_codex(now=1000.0)
    usage.read_codex(now=1000.0 + usage.CODEX_TTL - 1)
    assert len(calls) == 1
    usage.read_codex(now=1000.0 + usage.CODEX_TTL + 1)
    assert len(calls) == 2


def test_codex_not_installed_is_said(monkeypatch):
    monkeypatch.setattr(usage, "_codex_cache", None)
    monkeypatch.setattr(usage, "_codex_exe", lambda: None)
    assert usage.read_codex(now=1.0) == {"state": "unavailable", "reason": "codex is not installed"}


def test_kiro_is_said_to_be_unreadable():
    assert usage.read_kiro() == {"state": "unavailable", "reason": "kiro_no_api"}


# --- wiring -------------------------------------------------------------------------------------
def test_the_hook_settings_carry_the_relay_and_still_installed_checks_it(tmp_path):
    setup = hooks_module.install(tmp_path / "data")
    assert setup.settings is not None and setup.usage_dir is not None and setup.usage_dir.is_dir()
    settings = json.loads(setup.settings.read_text(encoding="utf-8"))
    command = settings["statusLine"]["command"]
    assert "statusline_relay.py" in command and '| bash -c "$AGORA_STATUSLINE_COMMAND"' in command
    assert hooks_module.still_installed(setup)
    (setup.settings.parent / "statusline_relay.py").write_text("", encoding="utf-8")
    assert not hooks_module.still_installed(setup)


def test_the_status_line_is_the_one_claude_would_use_user_then_shared_then_local(tmp_path):
    tool, user = tmp_path / "tool", tmp_path / "user-config"
    (tool / ".claude").mkdir(parents=True)
    user.mkdir()
    line = lambda command: json.dumps({"statusLine": {"type": "command", "command": command}})  # noqa: E731
    assert workspace_statusline(tool, user) is None
    # Set only for the user: kept, not replaced by nothing (codex review round 1).
    (user / "settings.json").write_text(line("user"))
    assert workspace_statusline(tool, user) == "user"
    (tool / ".claude" / "settings.json").write_text(line("shared"))
    assert workspace_statusline(tool, user) == "shared"
    (tool / ".claude" / "settings.local.json").write_text(line("local"))
    assert workspace_statusline(tool, user) == "local"
    (tool / ".claude" / "settings.local.json").write_text("{broken")
    assert workspace_statusline(tool, user) == "shared"


def test_the_user_settings_follow_claude_config_dir(tmp_path, monkeypatch):
    tool, user = tmp_path / "tool", tmp_path / "elsewhere"
    tool.mkdir()
    user.mkdir()
    (user / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "from env"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user))
    assert workspace_statusline(tool) == "from env"


def test_a_work_page_claude_gets_the_relay_environment(world):
    tmp_path, work, claude = world
    tool = tmp_path / "tool" / ".claude"
    tool.mkdir(parents=True, exist_ok=True)
    (tool / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "echo hi"}}))
    service = _service(tmp_path, work, claude)
    run(service.open(work_key(work, "alpha")))
    env = service.manager.sessions[-1].env
    assert env["AGORA_STATUSLINE_COMMAND"] == "echo hi"
    assert Path(env["AGORA_USAGE_DIR"]) == tmp_path / "data" / "work-usage"


def test_usage_through_the_api(api, tmp_path, monkeypatch):
    client, service, work = api
    monkeypatch.setattr(usage, "read_codex", lambda: {"state": "unavailable", "reason": "codex is not installed"})
    assert client.get("/api/work/usage").status_code == 401
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    body = client.get("/api/work/usage").json()
    assert set(body) == {"claude", "codex", "kiro"}
    assert body["claude"] == {"state": "unavailable", "reason": "no_hook"}
    assert body["kiro"]["reason"] == "kiro_no_api"


def test_a_codex_that_never_answers_is_ended_with_its_children(tmp_path, monkeypatch):
    """A stand-in app-server that starts a child holding the pipe and never answers: the
    read times out, the tree is ended, and no reader is left behind (codex review round 1)."""
    script = tmp_path / "fake_codex.py"
    marker = tmp_path / "child.pid"
    script.write_text(
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{marker}', 'w').write(str(child.pid))\n"
        "time.sleep(120)\n", encoding="utf-8")
    monkeypatch.setattr(usage, "CODEX_TIMEOUT", 3.0)
    real_popen = subprocess.Popen
    monkeypatch.setattr(usage.subprocess, "Popen",
                        lambda args, **kw: real_popen([sys.executable, str(script)], **kw))
    before = {t.name for t in __import__("threading").enumerate()}
    started = time.monotonic()
    reading = usage._ask_codex("codex")
    assert reading == {"state": "unavailable", "reason": "codex app-server did not answer"}
    assert time.monotonic() - started < 30
    child = int(marker.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(child):
        time.sleep(0.2)
    assert not _alive(child)
    assert {t.name for t in __import__("threading").enumerate() if t.is_alive()} <= before


def _alive(pid: int) -> bool:
    if os.name == "nt":
        # Asked of the process itself: a `tasklist` child here waited out the stand-in's
        # two minutes on its pipes (measured), which says nothing about the process.
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
