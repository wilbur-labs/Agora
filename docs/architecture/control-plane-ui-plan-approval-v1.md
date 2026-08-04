# Control Plane UI Plan Approval v1

Status: frozen for the fourth Agora 1.0 UI increment.

## Purpose

The authenticated Task console may perform the one explicit human action that
closes a non-methodology Task after every authoritative Stage and its exact
formal Gate have passed. This command changes the orchestration Plan from
`awaiting_approval` to `ready_for_implementation` and the frozen Control Plane
Task from `needs_review` to `completed` in one transaction.

Plan approval is not a protocol `Approval`, does not satisfy or evaluate a
Gate, and does not authorize a runtime. Methodology Tasks retain their separate
artifact-bound completion approval command.

## HTTP boundary

`POST /api/control-plane/projects/{project_id}/tasks/{task_id}/plan-approvals`

The request is `ControlPlanePlanApprovalRequest@1.0`:

```json
{
  "reason": "bounded human rationale",
  "expected_task_version": 7,
  "expected_plan_version": 4,
  "operation_key": "stable-exact-retry-key"
}
```

- The route requires `control_plane.plan.approve` and membership in the path
  project. Authentication and authorization happen before Task lookup.
- The actor is always the verified bearer principal. Caller-supplied actor or
  approval identity fields are unknown and rejected.
- `reason` must contain 1 to 4000 characters after trimming and is redacted
  before persistence. The idempotency fingerprint binds the persisted,
  redacted reason and never retains a raw secret or secret-derived digest.
- Both expected versions are mandatory optimistic-concurrency inputs. A stale
  Task or Plan version returns 409.
- `operation_key` is globally unique in the shared `control_operations`
  registry. The exact successful retry returns the original receipt with
  `replayed=true`; different input, scope, actor, versions, or command reuse
  returns 409.

The response is `ControlPlanePlanApprovalReceipt@1.0` and contains the updated
frozen `TaskRecord`, updated `OrchestrationPlan`, their previous states, the
operation key, and replay status. It explicitly reports:

```text
task_completed=true
formal_approval_created=false
methodology_completion_approval_created=false
```

## Authority preconditions

The write fails closed unless one transaction observes all of these facts:

- the path Task exists in the path project;
- frozen Task state is exactly `needs_review` at `expected_task_version`;
- the Task has no methodology execution contract;
- one Plan exists for the Task in the same project, is exactly
  `awaiting_approval`, and is at `expected_plan_version`;
- the immutable grouped Stage inventory exists;
- deterministic lifecycle derivation returns `needs_review` with reason
  `all_stages_passed`, which proves every inventory Stage and exact formal Gate
  passed and no blocking human Attention remains;
- the Plan has no running operational Run, unsettled formal protocol Run,
  running explicit consultation, or consultation candidate still awaiting
  human `adopt`/`reject` disposition.

Already-completed or already-approved state is not accepted as a new command.
Only an exact operation-key replay returns the prior successful receipt.

## Atomic mutation and audit

The following writes use one `BEGIN IMMEDIATE` SQLite transaction:

1. transition the frozen Task to `completed` with cause `user_action`;
2. transition the Plan to `ready_for_implementation`, increment its version,
   and bind `approved_at` and `approved_by` to the authenticated principal;
3. append the Task state-change audit and the redacted
   `orchestration.plan_approved` audit;
4. persist the exact-replay receipt in `control_operations`.

Any write failure rolls back all four effects. The command never inserts into
`protocol_approvals`, changes Stage/Gate/Run/Artifact/Evidence rows, calls a
provider or local model, or starts a runtime process.

## Console behavior and errors

The console renders the action only from an authoritative unified projection
whose sole required human action is `plan_approval`; Attention and candidate
disposition are settled first. It binds the displayed Task and Plan versions
into the request and keeps one operation key across an unchanged failed or
uncertain attempt. Editing the reason or receiving a new version creates a new
key. Success is followed by a fresh authenticated projection read; stale
responses are discarded with the existing abortable request-lease rules.

- 401: missing or invalid bearer credential;
- 403: missing `control_plane.plan.approve` or project membership;
- 404: missing or mismatched project/Task resource;
- 409: stale versions, invalid authority state, methodology Task, incomplete
  Stage/Gate lifecycle, or conflicting operation-key reuse;
- 422: invalid request shape, reason, or key;
- 503: sanitized transient SQLite contention.

This increment does not add formal Approval creation, Gate evaluation, Stage
activation, Run dispatch, candidate disposition, autonomous AI discussion,
runtime substitution, configurable roles, or local-model configuration.
