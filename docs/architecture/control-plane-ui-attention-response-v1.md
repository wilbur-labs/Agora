# Control Plane UI Attention Response v1

Status: frozen for the third Agora 1.0 UI increment.

## Purpose

The Task console may settle one open human Attention item without acquiring
Task, Stage, Gate, Run, Evidence, or formal Approval authority. This is a new
authenticated command boundary. It does not call, replace, or widen the legacy
`POST /api/attention/{item_id}/respond` route.

## HTTP boundary

`POST /api/control-plane/projects/{project_id}/tasks/{task_id}/attention/{item_id}/responses`

The request is `ControlPlaneAttentionResponseRequest@1.0`:

```json
{
  "action": "answer | approve | reject",
  "response": "bounded human text",
  "expected_version": 1,
  "operation_key": "stable-exact-retry-key"
}
```

- The route requires `control_plane.attention.respond` and membership in the
  path project. Authentication and project authorization run before any Task
  or Attention lookup.
- The path project, Task, and item must identify one existing bound resource.
  Cross-project and cross-Task item lookups return the same non-enumerating
  404 as a missing item.
- The audit and response actor is always the verified bearer principal. An
  actor in the request body is an unknown field and is rejected.
- `expected_version` is optimistic concurrency over the open Attention item.
  A stale version or non-open item returns 409.
- `operation_key` is globally unique across the existing shared
  `control_operations` registry and the Attention response detail ledger. The
  first successful settlement stores a canonical request fingerprint and
  result binding in both ledgers, in the same transaction as the item update
  and audit event. An exact retry returns the same receipt without another
  update or event; reuse by any other Control Plane command or for any
  different request, scope, item, actor, or version returns 409.
- Response text is redacted before persistence. The idempotency fingerprint
  binds the exact persisted, redacted response rather than retaining raw
  secrets or a secret-derived digest.

The response is `ControlPlaneAttentionResponseReceipt@1.0`. It returns the
settled `AttentionItem`, the operation key, and one response effect:

- `local_recorded`: a local Attention response with no native bridge;
- `capture_only_recorded`: a captured native event whose response is recorded
  in Agora but cannot be delivered to the native runtime;
- `delivery_ready`: a trusted bidirectional bridge response atomically moved
  from `pending` to `ready`; this does not claim delivery or runtime acceptance.

The receipt explicitly reports `task_state_mutated=false` and
`formal_approval_created=false`.

## Action, assignee, and expiry rules

- `question` and `blocker` items accept only `answer`, with non-blank response
  text.
- `approval` items accept only `approve` or `reject`. These actions answer an
  Attention prompt and never create a protocol `Approval` or satisfy a Gate.
- If `assignee` is present, it must equal the authenticated principal. An
  authorized but different principal receives 403.
- Expiry is settled inside the same write transaction before a response can be
  accepted. An overdue open item becomes `expired`, its version advances, its
  undelivered bidirectional bridge fails, and the response returns 409 without
  an operation receipt.
- Response text and any audit payload are redacted. The audit event contains
  identity, action, operation key, item identity, and response effect, but not
  response text or bearer credentials.

## Transaction and delivery boundary

The Attention update, `attention.responded` audit event, optional bridge
`pending -> ready` transition, and operation receipt are one SQLite
transaction. Any failed write rolls the whole response back. A bridge state
outside the expected transition fails closed.

Capture-only rows never become ready or delivered through this command.
Bidirectional delivery remains a later bridge-worker action with its own
claim/acknowledgement states. Process exit, transport, schema, semantic result,
Task lifecycle, Stage routing, and Gate state are unchanged.

## Console behavior

- The client renders only open Attention items from the authoritative unified
  projection and uses the item version returned by that snapshot.
- Each unchanged draft keeps one operation key across a failed or uncertain
  network attempt. Editing the action/text or receiving a new item version
  creates a new key.
- A successful response is followed by a fresh authenticated unified
  projection read. The client does not synthesize a settled projection.
- Response and refresh requests use abortable leases. Project, Task,
  credential, Forget, superseding mutation, and unmount changes invalidate the
  lease so an old browser response cannot update the visible Task.
- The bearer remains header-only and tab-scoped. It is never placed in the
  URL, operation key, response body, audit event, or local storage.

## Error mapping and deferred scope

- 401: missing or invalid bearer credential;
- 403: missing response permission, project membership, or assignee match;
- 404: missing or mismatched project/Task/Attention resource;
- 409: stale/non-open/expired state or conflicting operation-key reuse;
- 422: invalid request shape or action/kind combination;
- 503: sanitized transient SQLite contention.

This increment does not add Attention creation/cancellation, formal Approval,
Task completion, Stage activation, Run dispatch, Gate evaluation, generic
mutation controls, streaming, provider substitution, or methodology migration.
