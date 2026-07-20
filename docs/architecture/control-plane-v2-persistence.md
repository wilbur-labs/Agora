# Control Plane v2 persistence

Status: reviewed implementation increment; API exposure is intentionally deferred.

This increment persists the frozen Agora 1.0 Artifact, Evidence, Approval, Gate,
and Stage contracts in the existing task SQLite database. It does not replace
or rewrite the 0.5 Task, Run, Requirement, Attention, or Workflow tables.

## Migration boundary

- Initialization is additive and idempotent: only new `control_*` and
  `protocol_*` tables and indexes are created.
- Existing table definitions and rows are not altered.
- Schema initialization is safe to retry after partial `CREATE TABLE IF NOT
  EXISTS` execution. Future column or data migrations must use an explicit,
  versioned transactional migration rather than extending the bootstrap script.
- SQLite foreign keys remain enabled. Gate requirements, active Evidence, and
  Approval bindings cannot outlive or cross their owning Task/Gate records.

## Registry invariants

- Artifact identity is immutable by `(artifact_id, version)`.
- Evidence identity is immutable by `evidence_id` and is bound to its producing
  Task and project.
- Approval identity is immutable by `approval_id`; every bound Artifact version
  must already be registered for the same Task and project.
- A Gate is configured once for one Stage and one canonical set of requirements.
- Agora 1.0 Gate requirements share one repository/ref/commit scope.
- Active Evidence must belong to the Gate Task/project and match the exact
  repository/ref/commit/requirement/kind tuple.
- All mutating replayable operations use a caller-provided operation key plus a
  canonical input fingerprint. Reusing a key with different input fails closed.

## Gate evaluation

Evidence selection and evaluation use optimistic Gate versions inside
`BEGIN IMMEDIATE` transactions.

Changing Evidence keeps `pending`, `blocked`, or `stale` Gates in that state.
A previously `passed` Gate becomes `stale`. Evaluation then follows the frozen
state machine atomically:

```text
pending | blocked | stale
          -> evaluating
          -> passed | blocked
```

Both transitions are persisted with version increments and append-only audit
events. A failed evaluation transaction exposes neither the intermediate state
nor partial events.

## Approval invalidation

An Artifact inventory is a complete snapshot for one repository/ref/commit.
Approvals for the same repository/ref are evaluated across projects because the
repository identity is shared, while each resulting mutation and event remains
bound to its original Task and project. Unchanged Artifact bindings remain
active.

For each changed binding, one transaction atomically:

1. marks the Approval `stale`;
2. moves a previously `passed` Gate to `stale`;
3. propagates the invalidation through the configured downstream Stage graph;
4. appends control-plane and Task audit events;
5. creates one bounded deterministic impact-analysis Attention per affected
   Task;
6. stores the idempotent operation receipt.

Stage propagation follows only legal frozen transitions:

- `pending`, `blocked`, `reconciliation_required`, `completed`, or `failed`
  become `ready`;
- `needs_review` moves through `blocked` to `ready` in the same transaction;
- `running` becomes `reconciliation_required` so active work is not silently
  declared safe;
- `ready` is already reopened and `cancelled` remains terminal.

Any projection or event failure rolls the entire invalidation back.

The repository/ref-wide transaction is deliberately atomic, but its write-lock
duration grows with the number of affected Tasks and Approvals. Before enabling
this path for large shared repositories, run load tests against the 10-second
SQLite busy timeout and define an operational bound or a resumable batching
protocol that preserves the same all-or-nothing semantics.

## Deferred surface

This increment intentionally exposes no new REST endpoints. API authorization,
pagination, request bounds, and Task/Stage orchestration integration will be a
separate reviewable increment built on this registry.
