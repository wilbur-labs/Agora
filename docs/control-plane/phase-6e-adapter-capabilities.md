# Phase 6e — Truthful adapter capability routing

Status: implementation active.

Agora exposes the enabled execution adapters through `GET /api/execution-adapters`. Each entry reports its configured execution transport, attention delivery mode, stable tool-approval support, user-question support, and an operator-facing limitation. The Run Center uses this response instead of implying that all three vendors have equivalent interactive control.

Current verified matrix:

| Adapter | Execution transport | Attention behavior |
|---|---|---|
| Codex | `codex_app_server` when configured | Stable command/file approvals are bidirectional; experimental user input is excluded. |
| Claude Code | subscription CLI | Capture-only. Delayed decisions cannot be returned to the current CLI process. |
| Kiro CLI 2.12.2 | non-interactive CLI | Capture-only; no verified machine-readable bidirectional prompt protocol. |

Claude Code 2.1.210 exposes stream-json input/output and lifecycle hook events, but its command hooks remain a bounded synchronous mechanism. The Claude Agent SDK `can_use_tool` callback is a viable future locally hosted boundary, while Claude Managed Agents supports indefinite `requires_action` confirmation events through the Platform API. The latter uses API authentication and billing rather than the user's Claude Code subscription, so Agora does not enable it implicitly.

This phase does not install SDK packages, mutate `.claude` or Kiro settings, change execution commands, or claim support for `AskUserQuestion`. A future SDK-host adapter can add a new execution mode behind the same capability contract without changing the tool-neutral Attention model.

Official references:

- https://platform.claude.com/docs/en/managed-agents/permission-policies
- https://platform.claude.com/docs/en/managed-agents/migration
- https://platform.claude.com/docs/en/manage-claude/authentication
