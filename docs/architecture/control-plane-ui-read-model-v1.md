# Control Plane UI Read Model v1

Status: frozen for the first Agora 1.0 UI increment.

## Purpose

The first 1.0 console is a Task-scoped inspection surface. It exposes the
existing `UnifiedTaskProjection@12.0` through an authenticated HTTP boundary
and renders the authoritative Task, Stage, Gate, Run, Artifact, Evidence,
Approval, attention, budget, and audit facts without acquiring mutation
authority.

## HTTP boundary

`GET /api/control-plane/projects/{project_id}/tasks/{task_id}/unified-projection`

- Requires a bearer principal with `control_plane.read` for `project_id`.
- Performs the existing non-enumerating project/Task scope check before the
  projection read.
- Accepts `limit` from 1 through 200 and `offset` from 0 through 1,000,000 for
  bounded historical collections.
- Returns a single SQLite read snapshot serialized as
  `UnifiedTaskProjection@12.0`.
- Maps missing resources to 404, ledger conflicts to 409, invalid pagination to
  422, and transient SQLite contention to a sanitized retryable 503.
- Initializes and migrates the cached Task and Control Plane stores during the
  FastAPI lifespan startup. The request dependency fails closed if startup was
  skipped; a GET never bootstraps persistence.
- Does not initialize, resume, reconcile, expire, register, transition, route,
  evaluate, or otherwise mutate Task state while reading.

## Authority and presentation rules

- `task_state` and `task_state_source=control_plane` are the Task lifecycle
  authority. `task.state` is displayed only as a labelled compatibility value.
- Formal Stage and Gate records and the grouped Stage inventory remain routing
  authority. Compatibility Plan cursors are visibly labelled when formal
  routing is unavailable.
- Process, transport, schema, and semantic result are rendered as independent
  Run dimensions. Exit code zero is never presented as semantic success.
- Missing authority facts remain unavailable with their explicit reason. The
  UI must not infer or synthesize them.
- The gate-derived `next_safe_action` is shown ahead of the compatibility next
  action and keeps its source Gate identity.

## Credential handling

- The bearer token is sent only in the `Authorization` header.
- Project and Task identifiers may be stored in the URL for navigation.
- The bearer token must never be placed in the URL or local storage. The first
  console may retain it in component memory and `sessionStorage`, which limits
  persistence to the current browser tab.
- The static frontend export contains no credential and grants no authority by
  itself.

## Deferred scope

This increment does not add lifecycle transitions, Stage activation, Run
dispatch, Gate evaluation, approval creation, attention responses, pagination
controls, streaming, dynamic routing, provider substitution, or methodology
migration. Each mutation requires a separately frozen command contract with
explicit concurrency, idempotency, authority, and audit semantics.
