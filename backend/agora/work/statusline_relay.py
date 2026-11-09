"""The work page's status-line relay (workspace CR-87) — a Claude Code ``statusLine``
command, injected only into claude processes the work page starts.

Claude Code sends its status line a JSON document; for claude.ai Pro/Max/Ultra
subscribers it carries ``rate_limits`` (the 5-hour and 7-day windows, what ``/usage``
shows). This relay records that part in ``$AGORA_USAGE_DIR/claude.json`` and passes the
document on, unchanged, to standard output — the command after it in the pipe is the
workspace's own status line, so the person sees the same line as before.

Copied as a file into Agora's data directory and run by the server's own Python, so it
imports nothing from Agora. It never fails the status line: on any error it still passes
the input on, and exits 0.
"""
import json
import os
import sys
import tempfile
import time

_LIMIT = 1 << 20
_WINDOWS = ("five_hour", "seven_day")


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _limits(data):
    """The windows as Claude Code sent them, numbers only; None when there are none."""
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return None
    out = {}
    for name in _WINDOWS:
        window = limits.get(name)
        if isinstance(window, dict):
            used, resets = _number(window.get("used_percentage")), _number(window.get("resets_at"))
            if used is not None:
                out[name] = {"used_percentage": used, "resets_at": resets}
    return out or None


def _record(raw):
    folder = os.environ.get("AGORA_USAGE_DIR", "")
    if not folder or not os.path.isdir(folder):
        return
    try:
        data = json.loads(raw.decode("utf-8") or "null")
    except Exception:
        return
    limits = _limits(data)
    path = os.path.join(folder, "claude.json")
    try:
        with open(path, "rb") as handle:
            previous = json.loads(handle.read(_LIMIT).decode("utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except Exception:
        previous = {}
    now = time.time()
    # "seen" says the status line reported at all; the windows are kept from the last
    # report that had them: they are absent before a session's first answer.
    record = {"v": 1, "seen_at": now,
              "limits": limits if limits is not None else previous.get("limits"),
              "limits_at": now if limits is not None else previous.get("limits_at")}
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="claude.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main():
    raw = sys.stdin.buffer.read(_LIMIT)
    try:
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
    finally:
        try:
            _record(raw)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
