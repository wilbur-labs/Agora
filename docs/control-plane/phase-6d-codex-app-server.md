# Phase 6d — Codex app-server execution integration

Status: implemented with protocol-mock coverage; live account smoke test remains optional.

Agora's Codex execution adapter now supports `bridge_mode: codex_app_server`. In this mode the dispatcher starts `codex app-server --listen stdio://` and performs the version-matched JSONL sequence:

1. `initialize` request and `initialized` notification.
2. `thread/start` with the isolated workspace, `workspace-write`, interactive approvals, and ephemeral history.
3. `turn/start` with the task prompt.
4. Capture stable command/file approval server requests in Attention Center.
5. Write approved/rejected JSON-RPC results to the same process stdin.
6. Finish after `turn/completed` or map protocol failure, timeout, and cancellation into the existing run lifecycle.

The app-server process is registered in the dispatcher's active process table, so existing cancellation and shutdown paths terminate it. PID attachment, concurrency limits, workspace confinement, timeout, bounded/redacted output tails, and durable terminal states remain in force.

Protocol stdout and stderr are bounded to 128 KiB in memory before the execution store applies its existing 64 KiB persisted tail. Server requests arriving before the matching `turn/start` response are captured rather than discarded.

The integration suite uses a deterministic fake app-server process and covers:

- initialize/thread/turn handshake;
- an approval request racing ahead of the turn response;
- human approval returned on the original JSON-RPC id;
- durable delivered state;
- malformed protocol output;
- timeout and process termination;
- user cancellation and PID cleanup.

The default project configuration enables app-server mode for Codex. Operators can set `bridge_mode: cli` to return to the previous `codex exec` path.
