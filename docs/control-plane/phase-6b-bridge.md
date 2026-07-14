# Phase 6b — Vendor attention bridge

Status: capture foundation implemented; bidirectional delivery deferred pending protocol verification.

Agora normalizes vendor lifecycle events into `POST /api/attention/bridge-events`. The endpoint is idempotent on `(vendor, run_id, vendor_event_id)`, accepts events only for queued/running executions, validates task/run ownership, redacts persisted text, and currently accepts only `capture_only` delivery mode.

The dispatcher supplies `AGORA_PROJECT_ID`, `AGORA_TASK_ID`, and `AGORA_RUN_ID` to child processes. A command hook can forward its JSON stdin with:

```powershell
python -m agora.attention.bridges.hook_cli claude
python -m agora.attention.bridges.hook_cli codex
```

Set `AGORA_API_BASE` when Agora is not listening at `http://127.0.0.1:8000`.

## Capability truth table

| Adapter | Verified capture path | Delivery state in this phase |
|---|---|---|
| Claude Code 2.1.207 | command hooks such as `PermissionRequest` and `Notification` | `capture_only` |
| Codex CLI 0.144.1 | stable command `PermissionRequest` hooks | `capture_only` |
| Kiro CLI 2.12.1 | neutral normalizer/ingress available; no command-hook contract verified | not auto-installed |

`capture_only` means Agora notifies and records a human response, but cannot claim that the response reached the blocked vendor process. The Attention UI labels this limitation. Agora does not inject terminal keystrokes and does not auto-install or auto-trust hooks.

Codex app-server and Kiro V3 server are candidate bidirectional transports. They require version-matched generated schemas and independent lifecycle tests before Agora may advertise `bidirectional`. Claude structured response delivery also remains disabled until its supported input/output contract is verified end to end.

## Example hook fragments

Claude Code project settings:

```json
{
  "hooks": {
    "PermissionRequest": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "python -m agora.attention.bridges.hook_cli claude", "timeout": 10}]
    }]
  }
}
```

Codex project `.codex/hooks.json`:

```json
{
  "hooks": {
    "PermissionRequest": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "python -m agora.attention.bridges.hook_cli codex", "timeout": 10}]
    }]
  }
}
```

Review and trust project hooks in each vendor's own UI before use.
