# Phase 7a — Durable cross-project workflow DAGs

Status: backend implementation active. Specification owner: Kiro CLI. Implementation owner: Codex. Independent reviewer: Claude Code.

## Goal

Represent one complex delivery as a bounded acyclic graph of steps that may target different registered projects and agent adapters. Phase 7a persists and validates plans and projects readiness; Phase 7b will dispatch ready steps into execution runs.

## Contract

- A workflow contains 1–200 immutable step definitions.
- A step declares a stable key, project, optional existing task, adapter, prompt, and dependency keys.
- Creation rejects duplicate keys, unknown dependencies, self-dependencies, cycles, unknown projects at the API boundary, missing tasks, and task/project mismatches.
- Activation atomically promotes all roots from `pending` to `ready`.
- A succeeded step atomically promotes pending steps whose dependencies all succeeded.
- A failed step terminates the workflow and cancels every other non-terminal workflow step.
- Individual step cancellation is rejected in Phase 7a; cancellation is a workflow-level operation.
- Completion is derived when every step succeeds.
- Workflow and step writes use optimistic versions and append-only `workflow.*` events.
- Cancelling a workflow never mutates a referenced task. Tasks may be shared independently of a workflow plan.

## API

- `POST /api/workflows`
- `GET /api/workflows`
- `GET /api/workflows/{workflow_id}`
- `POST /api/workflows/{workflow_id}/activate`
- `PATCH /api/workflows/{workflow_id}/steps/{step_id}/state`
- `POST /api/workflows/{workflow_id}/cancel`
- `GET /api/workflows/{workflow_id}/events`

List filtering by project matches any step in that project, which allows a single workflow to appear in every affected project's portfolio.
`ready_count` remains the workflow-wide ready count even when the list is filtered by project.

## Deferred to Phase 7b

Automatic run creation, scheduler leases, retry/backoff, per-workflow concurrency and budget enforcement, output-to-step reconciliation, and UI DAG visualization are intentionally excluded from this persistence increment.
