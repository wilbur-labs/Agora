# Phase 2: Requirements Studio Backend

Status: In progress

Specification owner: Kiro CLI 2.11.1

Implementation owner: Codex

Independent reviewer: Claude Code

## Goal

Add versioned, structured requirement specifications to delivery tasks. Human approval is required before a task may advance from `requirements` to `design`.

## MVP

- Spec lifecycle: `draft → approved → superseded` or `rejected`.
- Structured functional/non-functional requirements, constraints, acceptance scenarios, out-of-scope, glossary, assumptions, and open questions.
- Traceability from individual requirement IDs to design, task, and test targets.
- Draft-only editing and explicit human approval/rejection.
- Change requests against approved specs; accepting a CR creates a copied draft at the next version and supersedes the old version atomically.
- Append-only `spec.*` and `cr.*` task events.
- Every allowed transition whose target is `design` requires an approved spec, regardless of source state.
- SQLite and REST APIs within Agora's existing single-node control plane.

## Important invariants

- At most one draft or approved spec exists for a task.
- Draft edits, approval, and rejection require the caller's expected revision; stale operations fail with conflict.
- Requirement IDs are unique within a spec.
- Acceptance scenarios and traceability links may only reference requirement IDs in that spec.
- A spec cannot be approved with no requirements, no acceptance scenarios, or unresolved open questions.
- A CR can only target an approved spec.
- Before accepting a CR, the delivery task must be returned to `requirements`; this prevents a design/build task from silently losing its approved requirement basis.
- Spec/CR lifecycle events are reserved and cannot be forged through the generic event API.

## Deferred

- Realtime collaborative editing and rendered version diffs.
- AI extraction from raw conversations.
- RBAC beyond recorded actor names.
- Notifications, full-text search, MoSCoW scoring, and requirement dependency graphs.
