"""The work page's session reporter (CR-75 §3) — a Claude Code ``SessionStart`` /
``SessionEnd`` hook, injected only into claude processes the work page starts.

Copied as a file into Agora's data directory and run by the server's own Python, so it
imports nothing from Agora. It appends one JSON line per event to
``$AGORA_WORK_DIR/$AGORA_WORK_LAUNCH.jsonl`` and never rewrites or truncates it. It
prints nothing: a SessionStart hook's standard output is added to the conversation.
It never fails the session — every error ends in a silent exit 0.
"""
import json
import os
import re
import sys
import time

_HEX = re.compile(r"^[0-9a-f]{16,64}$")
_FIELDS = ("hook_event_name", "source", "reason", "session_id", "transcript_path", "cwd")
_LIMIT = 4096


def _text(value):
    return value[:_LIMIT] if isinstance(value, str) else None


def main():
    folder = os.environ.get("AGORA_WORK_DIR", "")
    key = os.environ.get("AGORA_WORK_KEY", "")
    launch = os.environ.get("AGORA_WORK_LAUNCH", "")
    if not folder or not _HEX.fullmatch(key) or not _HEX.fullmatch(launch):
        return
    try:
        # Bytes, decoded as UTF-8: what claude sends. ``sys.stdin`` would decode with the
        # console code page (cp932 on Japanese Windows) and mangle a non-ASCII path.
        data = json.loads(sys.stdin.buffer.read(1 << 20).decode("utf-8") or "null")
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}
    picked = {name: _text(data.get(name)) for name in _FIELDS}
    record = {
        "v": 1, "key": key, "launch_id": launch,
        "event": picked["hook_event_name"], "source": picked["source"], "reason": picked["reason"],
        "session_id": picked["session_id"], "transcript_path": picked["transcript_path"],
        "cwd": picked["cwd"], "at": time.time(),
    }
    line = json.dumps(record, ensure_ascii=True) + "\n"
    with open(os.path.join(folder, launch + ".jsonl"), "a", encoding="utf-8") as handle:
        handle.write(line)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
