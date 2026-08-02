# AWS AI-DLC methodology completion-review dispatch v1

Status: implemented; independently reviewed

## Purpose and entry point

This boundary consumes one already authenticated
`MethodologyCompletionReviewClaimReceipt@1.0`, creates its reserved formal
reviewer Run, dispatches only the runtime pinned by the frozen execution
contract, and settles its exact completion-review Evidence:

```powershell
agora task migration-completion-review-dispatch SUCCESSOR_TASK_ID `
  --responsibility independent_correctness `
  --allow-unbounded-native-usage
```

The two responsibilities remain separate invocations. The dispatch command
does not select a reviewer, substitute a provider, approve the Task, or create
a rework route.

## Spawn authority

The dispatcher derives a review-only `ContextPack@1.0` from the immutable
claim. It contains the seven final production Artifact versions as inputs, no
required outputs, no memory, and a sealed policy describing the only eligible
Evidence. Before process creation, one transaction rechecks:

- the exact claim receipt, final production dispatch/Handoff, execution
  contract, inventory, Task/Plan, repository/ref/commit, Stage, and Gate;
- the responsibility-scoped deterministic reviewer Run id and unused Context
  id;
- the complete frozen runtime registry, selected command hash, native
  capability observation, and pairwise reviewer/production independence;
- all settled provider-usage ledgers and the protected reservations still
  required by incomplete reviewers; and
- the exact review Context hash, dispatch policy, runtime preflight decision,
  prompt hash, spawn owner, and recovery lease.

The same transaction creates the formal protocol Run, moves only the final
Stage from `BLOCKED` to `RUNNING`, and persists the single-use dispatch claim.
The ordinary protocol and compatibility Run creation paths continue to reject
the reserved reviewer identity.

`--allow-unbounded-native-usage` is an explicit acknowledgement that the Plan
reservation is admission control, not a native provider hard cap. It does not
weaken persisted usage accounting or Plan-budget checks.

## Exact reviewer Handoff

A semantically valid reviewer Handoff has no output Artifacts, unresolved
questions, native-state snapshot, or memory candidates. It contains exactly
one deterministic Evidence object bound to:

- the claimed reviewer Run, responsibility, and pinned runtime;
- the final repository/ref/commit and the single formal Gate requirement;
- all seven production Artifact version references in canonical order; and
- the policy-provided exact details for either `passed` or `failed_product`.

The reviewer cannot alter Evidence identity, scope, status vocabulary,
Artifact references, details, blocker requirement, or suggested next action.
A schema-valid but non-exact Handoff becomes a protocol failure with attention
required and registers zero Evidence. Process, transport, schema, and semantic
results remain distinct; exit code zero alone is not success.

## Settlement and lifecycle

The Control Plane settles the formal reviewer Run and is the only writer of
Evidence, Gate, Stage, and Task state. A passed first review activates its
Evidence but leaves the Gate and Stage blocked. When the other independent
review also passes, deterministic Gate evaluation produces:

```text
Gate PASSED -> Stage COMPLETED -> Task NEEDS_REVIEW
```

`NEEDS_REVIEW` is intentional. Completion-review Evidence is not a human Task
approval, so the dispatcher never transitions the Task to `COMPLETED`.
A `failed_product` result is recorded as exact Evidence and leaves Gate, Stage,
and Task blocked. It does not synthesize rework semantics.

The terminal dispatch receipt seals process facts, output/error hashes,
repository stability, provider usage, protocol state, Handoff identity,
Evidence ids, active Gate Evidence, and resulting lifecycle state. The exact
usage observation is debited once in the completion-review usage ledger.

## Recovery and replay

Dispatch state progresses through `claimed`, `running`, `terminal_observed`,
and `settled`. The spawn owner and recovery lease prevent duplicate process
creation. Recovery of a never-attached or dead process settles a blocked
result; a live or unknown PID refuses duplicate dispatch. A crash after Control
Plane settlement can finish the durable orchestration receipt without running
the reviewer again.

Exact service replay returns the sealed receipt. Replaying the underlying
authenticated claim accepts the reserved Run only when it is occupied by the
exact bound completion-review dispatch; any external occupancy still fails
closed.

## Persisted protocol

The protocol surface is checked in as:

- `MethodologyCompletionReviewDispatchPolicyDecision@1.0`;
- `MethodologyCompletionReviewDispatchClaim@1.0`; and
- `MethodologyCompletionReviewDispatchReceipt@1.0`.

The orchestration store persists the sealed dispatch and one exact provider
usage row per responsibility. Automatic rework, HTTP/UI surfaces, dynamic
runtime substitution, native AWS AI-DLC file mutation, and human completion
approval remain outside this boundary.
