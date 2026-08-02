# Control Plane UI Task Discovery v1

Status: frozen for the second Agora 1.0 UI increment.

## Purpose

The Task console needs authenticated navigation without reading the legacy
unauthenticated `/api/tasks` collection or requiring a manually copied Task ID.
This boundary returns a bounded project-scoped index of Tasks that are already
inspectable by the 1.0 unified read model.

## HTTP boundary

`GET /api/control-plane/projects/{project_id}/tasks`

- Requires a bearer principal with `control_plane.read` for `project_id`.
- Accepts `limit` from 1 through 200 and `offset` from 0 through 1,000,000.
- Returns `UnifiedTaskIndexPage@1.0` from one SQLite read snapshot.
- Includes only Tasks joined to both frozen Control Plane Task authority and an
  orchestration Plan. A legacy Task without either boundary is not silently
  initialized and is not advertised as inspectable.
- Returns an empty page for an authorized project with no inspectable Tasks;
  project membership is checked before any query.
- Uses the startup-initialized cached store and performs no mutation, resume,
  reconciliation, expiration, or routing while listing.

## Authority and compatibility

- `task_state`, `task_state_source=control_plane`, and `task_state_version` are
  the lifecycle authority displayed by the picker.
- `compatibility_state` is returned only for an explicitly labelled comparison.
- Plan state and methodology identity are descriptive Plan facts. This index
  does not derive or return a formal current Stage route.

## Deferred scope

This index does not expose cross-project search, free-text search, lifecycle
mutation, Stage activation, Run dispatch, Gate evaluation, attention response,
or approval authority. Pagination controls beyond the bounded first page remain
deferred to a later UI increment.
