"""Portable command-hook entry point for capture-only vendor events."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from .models import BridgeVendor
from .normalize import normalize_hook_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forward a vendor hook event to Agora")
    parser.add_argument("vendor", choices=[vendor.value for vendor in BridgeVendor])
    parser.add_argument("--task-id", default=os.environ.get("AGORA_TASK_ID"))
    parser.add_argument("--run-id", default=os.environ.get("AGORA_RUN_ID"))
    parser.add_argument("--api-base", default=os.environ.get("AGORA_API_BASE", "http://127.0.0.1:8000"))
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # argparse uses exit 2 for usage errors, but Claude interprets that as
        # a blocking hook decision. Normalize every configuration error.
        return 1
    if not args.task_id or not args.run_id:
        print("AGORA_TASK_ID and AGORA_RUN_ID are required", file=sys.stderr)
        # Claude Code reserves exit 2 for blocking hooks. Capture failures must
        # never interfere with the vendor's own permission flow.
        return 1
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        event = normalize_hook_event(
            BridgeVendor(args.vendor), payload, task_id=args.task_id, run_id=args.run_id,
        )
        body = event.model_dump_json().encode("utf-8")
        request = urllib.request.Request(
            f"{args.api_base.rstrip('/')}/api/attention/bridge-events",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            receipt = json.loads(response.read().decode("utf-8"))
        print(json.dumps({"systemMessage": f"Agora captured attention item {receipt['item_id']}"}))
        return 0
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
        print(f"Agora bridge capture failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
