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


KIRO_RESULT = {"success": True, "message": "Plan: KIRO POWER | 1 usage breakdowns", "data": {
    "planName": "KIRO POWER", "billingCycleReset": "2026-12-01", "overagesEnabled": False,
    "isEnterprise": True, "usageBreakdowns": [
        {"resourceType": "CREDIT", "displayName": "Credits", "used": 2874.17, "limit": 10000,
         "percentage": 28, "hasLimit": True}],
    "bonusCredits": [], "addOnCredits": []}}


def test_kiro_credits_are_read_as_left_of_the_limit():
    reading = usage.parse_kiro(KIRO_RESULT)
    assert reading["state"] == "ok"
    assert reading["credits_left"] == pytest.approx(7125.83)
    assert reading["credits_limit"] == 10000
    [window] = reading["windows"]
    assert window["used"] == pytest.approx(28.7417)
    assert window["resets_at"] == 1796083200.0  # 2026-12-01T00:00Z
    assert reading["limit_reached"] is False and reading["plan"] == "KIRO POWER"


def test_kiro_over_its_limit_is_zero_left_and_reached():
    data = json.loads(json.dumps(KIRO_RESULT))
    data["data"]["usageBreakdowns"][0]["used"] = 10400
    reading = usage.parse_kiro(data)
    assert reading["credits_left"] == 0 and reading["windows"][0]["used"] == 100
    assert reading["limit_reached"] is True


def test_kiro_without_a_limit_is_unavailable_not_unused():
    assert usage.parse_kiro({})["state"] == "unavailable"
    unlimited = json.loads(json.dumps(KIRO_RESULT))
    unlimited["data"]["usageBreakdowns"][0]["hasLimit"] = False
    assert usage.parse_kiro(unlimited)["state"] == "unavailable"
    assert usage.parse_kiro({"success": False, "message": "not logged in"}) == {
        "state": "unavailable", "reason": "kiro: not logged in"}


def test_a_kiro_without_the_call_is_said_to_be_unreadable(monkeypatch):
    monkeypatch.setattr(usage, "_ask", lambda *a: {
        "jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "Method not found"}})
    assert usage._ask_kiro("kiro-cli") == {"state": "unavailable", "reason": "kiro_no_api"}


def test_kiro_is_asked_at_most_every_five_minutes(monkeypatch):
    calls = []
    monkeypatch.setattr(usage, "_kiro_cache", None)
    monkeypatch.setattr(usage, "_kiro_exe", lambda: "kiro-cli")
    monkeypatch.setattr(usage, "_ask_kiro", lambda exe: calls.append(exe) or usage.parse_kiro(KIRO_RESULT))
    usage.read_kiro(now=1000.0)
    usage.read_kiro(now=1000.0 + usage.KIRO_TTL - 1)
    assert len(calls) == 1
    usage.read_kiro(now=1000.0 + usage.KIRO_TTL + 1)
    assert len(calls) == 2


def test_kiro_not_installed_is_said(monkeypatch):
    monkeypatch.setattr(usage, "_kiro_cache", None)
    monkeypatch.setattr(usage, "_kiro_exe", lambda: None)
    assert usage.read_kiro(now=1.0) == {"state": "unavailable", "reason": "kiro-cli is not installed"}


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
    monkeypatch.setattr(usage, "read_kiro", lambda: {"state": "unavailable", "reason": "kiro_no_api"})
    monkeypatch.setattr(usage, "read_claude_usage", lambda: {"state": "unavailable", "reason": "claude is not installed"})
    assert client.get("/api/work/usage").status_code == 401
    client.headers["X-Agora-Terminal-Token"] = load_or_create_token(tmp_path / "data")
    body = client.get("/api/work/usage").json()
    assert set(body) == {"claude", "codex", "kiro"}
    assert body["claude"] == {"state": "unavailable", "reason": "claude is not installed"}
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


def test_a_broken_kiro_reading_is_unavailable_not_ok():
    for used in (-1, float("nan"), float("inf")):
        data = json.loads(json.dumps(KIRO_RESULT))
        data["data"]["usageBreakdowns"][0]["used"] = used
        assert usage.parse_kiro(data)["state"] == "unavailable", used


def test_kiro_credits_are_chosen_by_resource_type_not_by_position_or_name():
    data = json.loads(json.dumps(KIRO_RESULT))
    credit = data["data"]["usageBreakdowns"][0]
    credit["displayName"] = "クレジット"
    other = {"resourceType": "AGENTIC_REQUEST", "displayName": "Requests", "used": 5, "limit": 50, "hasLimit": True}
    data["data"]["usageBreakdowns"] = [other, credit]
    assert usage.parse_kiro(data)["credits_limit"] == 10000
    data["data"]["usageBreakdowns"] = [other]
    assert usage.parse_kiro(data)["state"] == "unavailable"


def test_a_failed_handshake_is_said_and_nothing_more_is_asked(tmp_path, monkeypatch):
    """A stand-in server that refuses ``initialize``: the refusal is the answer, not a
    later 'no such method' (kiro review)."""
    script = tmp_path / "refuse.py"
    seen = tmp_path / "seen.txt"
    script.write_text(
        "import json, sys\n"
        f"log = open(r'{seen}', 'w')\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line); log.write(str(m.get('id')) + '\\n'); log.flush()\n"
        "    if m.get('id') == 1:\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32602, 'message': 'bad protocolVersion'}}), flush=True)\n"
        "    elif m.get('id') == 2:\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)\n",
        encoding="utf-8")
    real_popen = subprocess.Popen
    monkeypatch.setattr(usage.subprocess, "Popen",
                        lambda args, **kw: real_popen([sys.executable, str(script)], **kw))
    assert usage._ask_kiro("kiro-cli") == {"state": "unavailable",
                                           "reason": "kiro: initialize failed: bad protocolVersion"}
    assert seen.read_text().split() == ["1"]


CLAUDE_USAGE = """You are currently using your subscription to power your Claude Code usage

Current session: 5% used \u00b7 resets Nov 9, 6:59pm (Etc/GMT-9)
Current week (all models): 18% used \u00b7 resets Nov 10, 11:59am (Etc/GMT-9)
Current week (Fable): 0% used \u00b7 resets Nov 10, 12pm (Etc/GMT-9)

What's contributing to your usage?
"""
NOW_1109 = 1794204000.0  # 2026-11-09T06:00Z = 15:00 at UTC+9


def test_claude_usage_text_gives_the_session_and_week_windows():
    reading = usage.parse_claude_usage(CLAUDE_USAGE, now=NOW_1109)
    assert reading["state"] == "ok"
    session, week = reading["windows"]
    assert (session["label"], session["used"]) == ("5h", 5.0)
    assert (week["label"], week["used"]) == ("7d", 18.0)
    # 18:59 on 11-09 at UTC+9 = 09:59Z; 11:59 on 11-10 at UTC+9 = 02:59Z on 11-10.
    assert session["resets_at"] == 1794218340.0
    assert week["resets_at"] == 1794279540.0


def test_a_claude_reset_in_an_unknown_zone_is_no_time_not_a_wrong_one():
    text = "Current session: 40% used \u00b7 resets Nov 9, 6:59pm (Mars/Olympus)"
    [window] = usage.parse_claude_usage(text, now=NOW_1109)["windows"]
    assert window["used"] == 40.0 and window["resets_at"] is None


def test_the_reset_year_rolls_over_at_new_year():
    dec31 = 1830211200.0  # 2027-12-31T00:00Z
    at = usage._claude_reset_at("Jan 1, 9am", "UTC", dec31)
    assert at == 1830330000.0  # 2028-01-01T09:00Z


def test_claude_usage_without_windows_is_unavailable_not_unused():
    reading = usage.parse_claude_usage("Usage is only available for subscription plans.\n", now=NOW_1109)
    assert reading == {"state": "unavailable",
                       "reason": "claude /usage: Usage is only available for subscription plans."}
    assert usage.parse_claude_usage(None)["state"] == "unavailable"


def test_claude_usage_is_asked_at_most_every_two_minutes(monkeypatch):
    calls = []
    monkeypatch.setattr(usage, "_claude_cache", None)
    monkeypatch.setattr(usage, "_claude_exe", lambda: "claude")
    monkeypatch.setattr(usage, "_ask_claude", lambda exe: calls.append(exe) or usage.parse_claude_usage(CLAUDE_USAGE))
    usage.read_claude_usage(now=1000.0)
    usage.read_claude_usage(now=1000.0 + usage.CLAUDE_TTL - 1)
    assert len(calls) == 1
    usage.read_claude_usage(now=1000.0 + usage.CLAUDE_TTL + 1)
    assert len(calls) == 2


def test_the_status_line_record_stands_in_only_when_usage_cannot_be_read(tmp_path, monkeypatch):
    ok = usage.parse_claude_usage(CLAUDE_USAGE, now=NOW_1109)
    recorded = {"state": "ok", "windows": [], "read_at": 1.0}
    monkeypatch.setattr(usage, "read_claude", lambda d: recorded)
    monkeypatch.setattr(usage, "read_claude_usage", lambda: ok)
    assert usage._claude_reading(tmp_path) is ok
    failed = {"state": "unavailable", "reason": "claude /usage did not answer"}
    monkeypatch.setattr(usage, "read_claude_usage", lambda: failed)
    assert usage._claude_reading(tmp_path) is recorded
    monkeypatch.setattr(usage, "read_claude", lambda d: {"state": "unavailable", "reason": "no_hook"})
    assert usage._claude_reading(tmp_path) is failed


def test_claude_usage_through_a_stand_in_cli(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text("import json\nprint(json.dumps({'is_error': False, 'result': "
                      + repr(CLAUDE_USAGE) + "}))\n", encoding="utf-8")
    real_popen = subprocess.Popen
    monkeypatch.setattr(usage.subprocess, "Popen",
                        lambda args, **kw: real_popen([sys.executable, str(script)], **kw))
    reading = usage._ask_claude("claude")
    assert reading["state"] == "ok" and [w["used"] for w in reading["windows"]] == [5.0, 18.0]


def test_a_reset_after_a_clock_change_gets_that_days_offset(monkeypatch):
    """Read on 2026-03-06 (UTC-8), reset 2026-03-09 9am after a change to UTC-7: 16:00Z,
    not 17:00Z — the zone is asked at the reset's date (codex review). A stand-in zone,
    as this machine has no tz database."""
    from datetime import datetime, timedelta, timezone, tzinfo

    class Pacific(tzinfo):
        def utcoffset(self, dt):
            return timedelta(hours=-7 if dt.replace(tzinfo=None) >= datetime(2026, 3, 8, 2) else -8)

        def dst(self, dt):
            return timedelta(0)

    monkeypatch.setattr(usage, "_zone", lambda zone: Pacific())
    march6 = datetime(2026, 3, 6, 8, tzinfo=timezone.utc).timestamp()
    expected = datetime(2026, 3, 9, 16, tzinfo=timezone.utc).timestamp()
    assert usage._claude_reset_at("Mar 9, 9am", "America/Los_Angeles", march6) == expected


def test_etc_zones_are_read_with_the_sign_inverted():
    from datetime import datetime
    assert datetime(2026, 1, 1, tzinfo=usage._zone("Etc/GMT-9")).utcoffset().total_seconds() == 9 * 3600
    assert datetime(2026, 1, 1, tzinfo=usage._zone("Etc/GMT+5")).utcoffset().total_seconds() == -5 * 3600
    assert usage._zone("Mars/Olympus") is None


def test_a_cached_reading_is_not_served_past_its_reset(monkeypatch):
    calls = []
    monkeypatch.setattr(usage, "_claude_cache", None)
    monkeypatch.setattr(usage, "_claude_exe", lambda: "claude")
    reading = {"state": "ok", "windows": [{"label": "5h", "used": 99.0, "resets_at": 1060.0, "reset": False}],
               "read_at": 1000.0}
    monkeypatch.setattr(usage, "_ask_claude", lambda exe: calls.append(exe) or reading)
    usage.read_claude_usage(now=1000.0)
    usage.read_claude_usage(now=1050.0)
    assert len(calls) == 1
    usage.read_claude_usage(now=1061.0)  # within the TTL, past the reset
    assert len(calls) == 2


def test_a_repeated_hour_takes_the_reading_ahead(monkeypatch):
    """At the second 01:15 of a fall-back night, "1:30am" is fifteen minutes ahead, not
    forty-five behind (kiro review). A stand-in zone: UTC-4 until 06:00Z, then UTC-5."""
    from datetime import datetime, timedelta, timezone, tzinfo

    class Eastern(tzinfo):
        def utcoffset(self, dt):
            naive = dt.replace(tzinfo=None)
            if naive < datetime(2026, 11, 1, 1) or (naive < datetime(2026, 11, 1, 2) and not dt.fold):
                return timedelta(hours=-4)
            return timedelta(hours=-5)

        def dst(self, dt):
            return timedelta(0)

    monkeypatch.setattr(usage, "_zone", lambda zone: Eastern())
    second_0115 = datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc).timestamp()
    expected = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc).timestamp()
    assert usage._claude_reset_at("Nov 1, 1:30am", "America/New_York", second_0115) == expected


def test_an_out_of_range_etc_zone_is_no_reset_time():
    text = "Current session: 40% used \u00b7 resets Nov 9, 6:59pm (Etc/GMT+24)"
    [window] = usage.parse_claude_usage(text, now=NOW_1109)["windows"]
    assert window["used"] == 40.0 and window["resets_at"] is None


def test_one_agent_failing_leaves_the_others(monkeypatch):
    def boom(_):
        raise RuntimeError("x")
    monkeypatch.setattr(usage, "_claude_reading", boom)
    monkeypatch.setattr(usage, "read_codex", lambda: {"state": "ok", "windows": [], "read_at": 1.0})
    monkeypatch.setattr(usage, "read_kiro", lambda: {"state": "unavailable", "reason": "kiro_no_api"})
    body = usage.read_all(None)
    assert body["claude"] == {"state": "unavailable", "reason": "reading failed (RuntimeError)"}
    assert body["codex"]["state"] == "ok"


def test_a_claude_that_never_answers_is_ended_with_the_child_holding_its_pipe(tmp_path, monkeypatch):
    """A stand-in that at once starts a child inheriting stdout and never answers: the
    read times out, the child is ended with it — it started inside the job, as the
    process ran only once it had joined (codex review round 3)."""
    script = tmp_path / "fake_claude.py"
    marker = tmp_path / "child.pid"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{marker}', 'w').write(str(child.pid))\n"
        "time.sleep(120)\n", encoding="utf-8")
    monkeypatch.setattr(usage, "CLAUDE_TIMEOUT", 3.0)
    real_popen = subprocess.Popen
    monkeypatch.setattr(usage.subprocess, "Popen",
                        lambda args, **kw: real_popen([sys.executable, str(script)], **kw))
    started = time.monotonic()
    assert usage._ask_claude("claude") == {"state": "unavailable", "reason": "claude /usage did not answer"}
    assert time.monotonic() - started < 30
    child = int(marker.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(child):
        time.sleep(0.2)
    assert not _alive(child)

def test_a_reset_just_past_is_not_next_years():
    """"6:59pm" read at 18:59:30 is the reset half a minute ago, not 2027's (codex review
    round 2); and a date far from now is no reset time at all."""
    from datetime import datetime, timezone
    just_after = datetime(2026, 11, 9, 9, 59, 30, tzinfo=timezone.utc).timestamp()
    assert usage._claude_reset_at("Nov 9, 6:59pm", "Etc/GMT-9", just_after) == 1794218340.0
    assert usage._claude_reset_at("Mar 3, 6:59pm", "Etc/GMT-9", just_after) is None
