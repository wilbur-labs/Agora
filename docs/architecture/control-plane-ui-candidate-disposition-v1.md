# Control Plane UI Consultation Candidate Disposition v1

Status: frozen for the fifth Agora 1.0 UI increment.

## Purpose

The authenticated Task console may perform the explicit human action that
settles one immutable consultation candidate. The candidate is advice produced
only after a user explicitly invoked one pinned runtime consultation. It has no
authority over Task, Stage, Run, Artifact, Evidence, Gate, Approval, routing,
or methodology state.

Adoption writes the candidate's one bounded key/value into the versioned
`TaskDecision` ledger and increments the Plan version once. Rejection records
only the hash-bound disposition and leaves the Plan version unchanged. Neither
action advances the delivery lifecycle or invokes a runtime.

## HTTP boundary

`POST /api/control-plane/projects/{project_id}/tasks/{task_id}/consultation-candidates/{candidate_id}/dispositions`

The request is `ControlPlaneCandidateDispositionRequest@1.0`:

```json
{
  "action": "adopt",
  "reason": "bounded human rationale",
  "expected_candidate_sha256": "64 lowercase hexadecimal characters",
  "expected_plan_version": 4,
  "operation_key": "stable-exact-retry-key"
}
```

- `action` is exactly `adopt` or `reject`.
- The route requires `control_plane.candidate.dispose` and membership in the
  path project. Authentication and authorization happen before Task lookup.
- The actor is always the verified bearer principal. Caller-supplied actor,
  decision, approval, Stage, Gate, or runtime fields are unknown and rejected.
- `reason` must contain 1 to 500 characters after trimming and is redacted
  before hashing or persistence. No raw secret or secret-derived digest is
  retained.
- The candidate ID, its expected canonical content hash, and the exact expected
  Plan version are mandatory optimistic-concurrency bindings. Mismatch or stale
  authority returns 409.
- `operation_key` is globally unique among authenticated Control Plane writes
  in the shared `control_operations` registry. An exact successful retry
  returns the original receipt with `replayed=true`; different input, scope,
  actor, candidate hash, Plan version, action, or command reuse returns 409.

The response is `ControlPlaneCandidateDispositionReceipt@1.0`. It contains the
existing hash-sealed `ConsultationCandidateDisposition@1.0`, operation key, and
replay status. It explicitly reports candidate authority, lifecycle mutation,
formal approval creation, and runtime invocation as false. It reports whether
a TaskDecision is bound and whether the Plan version changed.

## Authority preconditions

The write fails closed unless one transaction observes all of these facts:

- the path Task exists in the path project;
- the candidate exists in that exact project and Task and verifies its
  canonical content hash;
- no prior disposition exists for the candidate;
- the candidate belongs to the Task's current Plan and its observed Plan
  version equals `expected_plan_version`;
- the request's expected candidate hash equals the verified stored candidate;
- the Plan is `active` with the candidate's current Stage `pending`, or the Plan
  is `blocked` with that Stage `blocked`;
- the authoritative current Stage route still has the exact candidate
  inventory, Stage, role, and pinned runtime bindings; and
- no operational Run is active for the Plan and no formal protocol Run is
  unsettled for the Task.

The existing candidate-domain invariants remain authoritative. In particular,
adoption invalidates stale Plan claims by incrementing the Plan version once;
rejection does not change it. A candidate can receive only one disposition.

## Atomic mutation and audit

The following writes use one `BEGIN IMMEDIATE` SQLite transaction:

1. on adoption only, create or reuse the exact versioned TaskDecision;
2. insert the immutable hash-sealed candidate disposition;
3. on adoption only, increment the Plan version once;
4. append bounded Task audit events without candidate body or raw rationale;
5. persist the authenticated exact-replay receipt in `control_operations`.

Any write failure rolls back every effect. The command never changes frozen
Task state, Stage state, Gate state, Run state, Artifact, Evidence, formal
Approval, routing, methodology contracts, or native runtime files. It never
starts Codex, Claude, Kiro, a provider, or a local model.

## Console behavior and errors

The console renders an action only when an authoritative
`candidate_disposition` human action has a matching candidate in the same
projection. It displays the candidate's advisory/non-formal markers, pinned
runtime, bounded decision, analysis, source references, Plan version, and
content hash. Pending candidates are ordered before disposed candidate history
in the paginated candidate collection so every bounded current action has its
matching body in the default console snapshot. The user chooses adopt or reject
and supplies a rationale.

The request binds the displayed candidate hash and Plan version. One operation
key is retained across an unchanged failed or uncertain attempt. Editing the
action or reason, or receiving a new candidate/hash/version, creates a new key.
Success is followed by a fresh authenticated projection read; stale responses
are discarded with the existing abortable request-lease rules. Plan approval
continues to remain hidden until every candidate is disposed.

- 401: missing or invalid bearer credential;
- 403: missing `control_plane.candidate.dispose` or project membership;
- 404: missing or mismatched project/Task/candidate resource;
- 409: stale candidate/hash/Plan/route, active Run, prior disposition, or
  conflicting operation-key reuse;
- 422: invalid request shape, reason, action, hash, or key;
- 503: sanitized transient SQLite contention.

This increment does not add consultation dispatch to the UI, autonomous AI
discussion, AI voting, formal approval, Stage/Gate transition, runtime
substitution, configurable roles, local-model configuration, or any change to
Kiro support.
