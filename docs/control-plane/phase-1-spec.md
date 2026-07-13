# Phase 1: Tool-neutral Task Control Plane

Status: In progress

Specification reviewer: Kiro CLI 2.11.1

Implementation owner: Codex

Independent code reviewer: Claude Code

## Goal

Add a durable task manifest, lifecycle state machine, append-only events, SQLite persistence, and REST API without replacing the existing research artifact workflow.

## Scope

- Tasks belong to an existing Agora project.
- A task records delivery metadata, assignments, acceptance criteria, budget, and arbitrary adapter metadata.
- State changes are validated and atomically append an event.
- Optimistic versions prevent two agents from silently overwriting each other's transitions.
- Events cannot be updated or deleted through the application API.
- Existing research IDs and file artifacts remain unchanged in Phase 1; tasks may reference them through metadata.

## Lifecycle

`backlog → requirements → design → planned → running → review → verified → done`

`blocked`, `failed`, and `cancelled` cover interruption and terminal outcomes. Invalid transitions return HTTP 409.

## API

- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `PATCH /api/tasks/{task_id}/state`
- `GET /api/tasks/{task_id}/events`
- `POST /api/tasks/{task_id}/events`
- `DELETE /api/tasks/{task_id}` (controlled cancellation, never physical deletion)

## Compatibility Boundary

- Reuse Agora's configured SQLite data location but do not migrate chat sessions yet.
- Keep `.agora/research/<legacy-id>` as the research artifact owner.
- Store `research_task_id` and `artifact_dir` in task metadata when the orchestrator is integrated in Phase 2.
- Preserve the current JSONL research audit until dual-write integration is implemented.

## Acceptance

- CRUD reads and filtering work across projects.
- Valid transitions update state/version and append immutable events atomically.
- Invalid or stale transitions fail with conflict.
- Unknown projects and invalid task input fail validation.
- Terminal tasks cannot be cancelled or transitioned.
- Existing tests remain green.
