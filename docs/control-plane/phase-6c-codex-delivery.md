# Phase 6c — Codex approval delivery protocol

Status: durable protocol foundation; execution-process integration is the next increment.

This phase is bound to schemas generated locally by `codex app-server generate-json-schema` from Codex CLI 0.144.1. It supports only the stable server requests:

- `item/commandExecution/requestApproval`
- `item/fileChange/requestApproval`

Both map Agora `approve`/`reject` responses to Codex `accept`/`decline` JSON-RPC results using the original request id. Experimental `item/tool/requestUserInput` is intentionally excluded.

## Durable lifecycle

`pending → ready → delivering → delivered|failed`

- Creating the bridge event stores version-matched correlation data.
- Responding to a bidirectional approval atomically moves delivery to `ready`.
- A broker atomically claims one response as `delivering`.
- Successful JSON-RPC write produces `attention.delivery_delivered`.
- Serialization/write failure produces a redacted `attention.delivery_failed` event.
- Startup recovery marks stale `delivering` rows failed because the system cannot safely know whether a pre-crash write reached Codex. It does not retry and risk a duplicate response.

Public hook ingestion remains loopback-only and capture-only. Only an in-process trusted app-server session may create a bidirectional event. A run may have at most 50 open attention items.

The next increment will supervise `codex app-server --listen stdio://`, perform the initialize/thread/turn handshake, feed server requests into `CodexApprovalBroker`, and write broker responses to the same process stdin. That process integration must preserve existing timeout, cancellation, output redaction, and restart semantics.
