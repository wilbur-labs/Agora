# Phase 3: Delivery Execution Layer

Status: implemented and independently reviewed
Specification owner: Kiro CLI
Implementation owner: Codex
Independent reviewer: Claude Code
Date: 2026-07-13

## Goal

Connect planned delivery tasks to real Codex, Claude Code, and Kiro CLI processes through durable, tool-neutral execution runs.

## Scope

- SQLite-backed run records and optimistic versions.
- Explicit `planned → running` gate when the first run is queued.
- Safe argv adapters using `create_subprocess_exec`, never a shell.
- Per-project working directories from the project registry.
- Global and per-project concurrency limits.
- Capped and redacted stdout/stderr tails.
- Timeout, cancellation, and abandoned-run recovery.
- Task audit events and REST endpoints.
- Fake-adapter tests that never launch paid CLIs.

Streaming, notifications, worktree creation, a full scheduler, and frontend run controls are deferred.

## Invariants

1. Runs can only be queued for tasks in `planned` or `running`.
2. The caller supplies the current task version; queueing and `planned → running` occur in one SQLite transaction.
3. Only configured adapters are accepted, and their workspace must be inside the project root or an explicit workspace allowlist.
4. Prompts are a single argv item, capped at 16,000 characters for Windows compatibility. Stored command metadata contains `{prompt}`, never the expanded prompt.
5. Run state changes use optimistic versions. Terminal states cannot be restarted.
6. Output is capped at 64 KiB per stream and common secret forms plus secret environment values are redacted.
7. A process left `running` across an Agora restart becomes `abandoned`; Agora does not attach to an unowned PID.

Workspace paths are resolved and checked again immediately before process creation. Python's
cross-platform subprocess API cannot bind `cwd` to an already-open directory handle, so a
malicious local writer with permission to replace workspace path components retains a very
small residual swap window; OS-level sandboxing/worktree hardening remains a later layer.

## Lifecycle

`queued → running → succeeded | failed | timed_out | cancelled`

`queued → cancelled` and startup-only `running → abandoned` are also valid.

## API

- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/cancel`
- `GET /api/tasks/{task_id}/runs`

## Acceptance

- Store, gate, adapter safety, output redaction, dispatcher success/failure/timeout, concurrency, cancellation, recovery, API filters, and reserved `run.*` events are covered without invoking real CLIs.
- Existing task and requirement tests remain green.
