# Versioned Task budget amendment v1

Status: reviewed implementation baseline.

This increment adds one CLI-first recovery operation for a formal retry that is
blocked only because the current Task envelope can no longer reserve the routed
Run while protecting every unfinished required reviewer Stage. It does not
reallocate Stage budgets, change reviewers, select another runtime, or rewrite
historical usage.

## Entry point

The bounded command is:

```text
agora task amend-budget TASK_ID \
  --tokens NEW_TOTAL \
  [--cost-usd NEW_TOTAL] \
  --expected-task-version TASK_VERSION \
  --expected-plan-version PLAN_VERSION \
  --reason REASON \
  [--actor ACTOR] \
  [--operation-key KEY]
```

`--cost-usd` is an amended total, not an increment. Omitting it preserves the
current cost envelope. An unbounded cost envelope remains unbounded; this
operation cannot introduce or remove a cost ceiling.

The command requires optimistic Task and Plan versions. A caller-supplied
operation key is globally idempotent. When omitted, Agora derives a stable key
from the Task, both expected versions, and both requested total envelopes.
Replaying the same key and inputs returns the existing sealed receipt; different
inputs under that key fail closed. Reusing a key for another Task is therefore a
conflict, matching the global Control Plane operation-key contract.

## Preconditions

Agora accepts a new amendment only when all of these facts hold inside one
SQLite write transaction:

- the concrete Task contract, Plan methodology, grouped inventory, and current
  authoritative route retain their exact stored identities and hashes;
- the Control Plane route is currently runnable, the compatibility Plan is
  active at that route, and its Stage is pending;
- no operational Run is active and no formal Run is unsettled;
- the Task and Plan versions equal the caller's expected versions;
- the Task and Plan cost envelopes agree;
- the proposed Token and configured cost totals never decrease, with at least
  one strict increase; and
- every routing-policy check except `protected_budget` already passes, while
  `protected_budget` is currently blocked.

This makes the command a recovery action for protected review capacity rather
than a general budget editor.

## Atomic mutation and invariants

Before any update, Agora derives a hash-sealed routing-policy snapshot. It then
updates only:

- the Task budget envelope and Task version; and
- the Plan total Token/cost envelope, Plan version, and update time.

The ordered Stage Token/cost allocations are hashed before and after the
mutation and must remain byte-equivalent. Reviewer requirements and assignments
in the two policy decisions must remain identical. Existing Run reservations,
settlements, and usage-ledger rows are never updated.

Agora re-derives the policy after the envelope update in the same transaction.
The amendment commits only if every routing-policy check now passes. Otherwise
the Task, Plan, event, and amendment row all roll back.

The resulting immutable `BudgetAmendment` binds:

- amendment, operation, Task, project, Plan, and version identities;
- inventory, methodology, contract, Stage, and Stage-allocation hashes;
- previous and amended total envelopes;
- complete prior and resulting hash-sealed policy decisions;
- actor, redacted reason, timestamp, and the explicit rule that the next Run
  claim must derive a new per-Run policy rather than reuse either audit snapshot.

The receipt is stored in the append-only
`orchestration_budget_amendments` ledger and mirrored into the Task audit log.
Payload hashes and row bindings are verified on every read.

## Projection and recovery

Unified Task projection schema `7.0` adds the paginated
`budget_amendments` history and reports its total/window alongside the amended
current budget. Amendments are ordered by `amendment_version` ascending within
the Plan. Reads validate each sealed receipt and never synthesize or repair one.

A concurrent Run claim and amendment serialize on the same SQLite
`BEGIN IMMEDIATE` writer lock; no second writer can mutate Stage allocations
between the amendment transaction's before/after hashes.
If the claim commits first, the active/unsettled Run guard rejects the
amendment. If the amendment commits first, the claim's transaction re-derives
the policy against the new Task/Plan versions and envelope. A receipt's
`resulting_policy` is audit evidence only and is never reused as dispatch
authority.

## Deferred boundaries

Stage reallocation, reviewer-set changes, cheaper runtime/model substitution,
provider capability discovery, policy or methodology migration, authenticated
HTTP commands, exact provider usage, the missing authoritative AI-DLC graph,
parallel/DAG routing, and Task Workbench UI remain separate reviewed increments.
