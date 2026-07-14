# Phase 4: Execution Run Center

Status: implemented and independently reviewed
Specification owner: Kiro CLI
Implementation owner: Codex
Independent reviewer: Claude Code
Date: 2026-07-14

## Goal

Make the durable Phase 3 execution API operable from Agora: users can dispatch Codex,
Claude Code, or Kiro CLI, follow runs across projects, inspect bounded output, cancel
active runs, and opt into completion notifications.

## Scope

- A `/runs` route linked from the Delivery Control Plane navigation.
- Typed execution API client contracts.
- Cross-project project/state/adapter filters with URL persistence.
- A task-aware run composer for `planned` and `running` tasks.
- Optimistic create/cancel requests with explicit 409 recovery.
- Visibility-aware list and detail polling with stale-response guards.
- Safe prompt, command, metadata, stdout, and stderr presentation.
- Opt-in browser notifications only for observed transitions into terminal states.

WebSocket/SSE, backend changes, worktree creation, retry, pagination UI, and an
AskUserQuestion protocol are deferred.

## Invariants

1. Initial terminal runs establish a notification baseline and never create a notification storm.
2. Poll responses may not overwrite a locally known higher run version.
3. Browser notification permission is requested only from a direct user action.
4. Output and metadata are rendered as text; no execution data uses raw HTML.
5. Create and cancel conflicts refresh authoritative state before the user retries.
6. Polling pauses while the document is hidden and refreshes immediately on return.

## Acceptance

- Production build and changed-file lint pass.
- A user can create, monitor, inspect, filter, and cancel execution runs.
- Keyboard focus is contained in create/cancel dialogs and Escape closes them.
- Terminal state notifications fire once per observed run transition when enabled.
