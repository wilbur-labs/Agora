# AWS AI-DLC methodology completion-review claim v1

Status: implemented and independently reviewed; dispatch successor implemented

## Purpose and entry point

After the final selected production Stage has settled successfully, its seven
declared outputs and production Evidence are durable, but the formal Gate,
Stage, and Task remain `BLOCKED` until both independent completion reviewers
act. An authenticated operator may claim exactly one reviewer Run without
starting any native process:

```powershell
agora task migration-completion-review-claim SUCCESSOR_TASK_ID `
  --request REVIEW_CLAIM.json `
  --credential-env AGORA_CONTROL_CREDENTIAL
```

The request and receipt use
`MethodologyCompletionReviewClaimRequest@1.0` and
`MethodologyCompletionReviewClaimReceipt@1.0`. A claim is an immutable
authority checkpoint. It does not dispatch the runtime, register Evidence,
evaluate the Gate, finalize the Stage, or mutate Task/Plan lifecycle state.

## Exact authority binding

One `BEGIN IMMEDIATE` transaction rechecks and seals:

- the authenticated migration principal, project permission, Task and Control
  Plane Task versions/status, Plan, grouped Stage inventory, and immutable
  execution contract;
- the final sequence-8 settled dispatch receipt, semantically successful
  production Run, sealed Handoff, seven registered output Artifact payloads,
  and existing active production Evidence;
- the final `BLOCKED` Stage and Gate, including their exact versions and all
  formal Gate requirements;
- the selected completion-review responsibility, its pairwise-distinct runtime
  pin and resolved command hash, the unchanged complete runtime registry, and
  the exact protected Token/cost reservation;
- the frozen migration Token/cost envelope against the live Plan totals, the
  validated provider usage ledger, and enough remaining capacity to preserve
  both completion reviewers' reservations concurrently; and
- repository/ref/commit plus every live seed Artifact hash from the original
  migration request.

The output Artifact and active Evidence reads validate both their protocol
payloads and denormalized ledger columns. Missing, extra, stale, resealed, or
row-only tampering fails closed before the claim ledger or audit events are
written.

The reviewer Run id is derived from the Task id, execution-contract hash,
final-dispatch receipt hash, and responsibility. It is therefore stable for
exact replay, distinct between the two responsibilities, and distinct from the
production Run. The claim ledger globally reserves that identity: ordinary
Control Plane protocol Runs and compatibility orchestration Runs cannot occupy
it, including from another Task. The completion-review dispatcher consumes the
exact claim rather than using either ordinary creation path. A responsibility
can be claimed only once; exact replay by the same authenticated principal
returns the original receipt before dispatch and also after the reserved Run is
occupied by its exact bound dispatch. A different request or outside Run
occupancy conflicts.

## Preserved independence boundary

The frozen execution contract remains authoritative:

- `independent_correctness` is pinned to the Claude runtime;
- `methodology_stewardship` is pinned to the Kiro runtime; and
- production execution remains pinned to Codex.

These are product protocol responsibilities, not this repository increment's
development-review tooling. The user's temporary replacement of the Kiro code
review gate with Codex does not rewrite or impersonate a frozen runtime role.

The receipt explicitly records that no Task, Control Plane Task, Plan, Stage,
or Gate version changed; no protocol Artifact/Evidence was created; no process
started; and no provider substitution or spawn authority was granted.

## Persistence and successor boundary

`orchestration_methodology_completion_review_claims` stores one sealed request
and receipt per Task/responsibility and emits matching Task and Control Plane
audit events in the same transaction. The final production state is otherwise
unchanged.

The implemented successor is documented in
`aws-aidlc-methodology-completion-review-dispatch-v1.md`. It dispatches only an
already claimed reviewer Run, settles its protocol and usage facts, and
registers only its exact completion-review Evidence. Both independent passed
requirements finalize the Gate and Stage while leaving the Task
`NEEDS_REVIEW` for a separate human approval. Automatic rework, HTTP/UI
surfaces, dynamic runtime substitution, and native AWS AI-DLC file mutation
remain outside these boundaries.
