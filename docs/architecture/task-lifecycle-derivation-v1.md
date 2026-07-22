# Frozen Task lifecycle derivation v1

Status: implementation increment under review.

This increment makes the persisted frozen Task state a deterministic result of
the immutable grouped Stage inventory and authoritative Control Plane facts. It
does not read the 0.5 Task manifest or compatibility Plan state as lifecycle
input, expose a new HTTP command, or start Task Workbench UI work.

## Authoritative inputs

Lifecycle reconciliation reads one SQLite write transaction containing:

- the hash-verified `control_stage_inventories` row;
- formal `control_stages` and their exact inventory Gate bindings;
- formal `control_gates` and their current states;
- open Task-scoped Attention counts by kind;
- the persisted `control_tasks` state and version.

The complete inventory is bounded to 200 Stages. Formal Stages or Gates outside
that inventory, mismatched bindings, duplicate Gates for one Stage, or a
completed Stage without a passed formal Gate fail closed. Process exit,
compatibility Run/Plan state, legacy `tasks.state`, and Agent suggestions are
not lifecycle inputs.

## Deterministic precedence

The target state is selected in this order:

1. explicit Task cancellation remains terminal;
2. a cancelled formal Stage makes the Task `cancelled`;
3. a failed formal Stage makes the Task `failed`;
4. reconciliation-required Stages, stale Gates, open blocker/question
   Attention, blocked Stages, or blocked Gates make the Task `blocked`;
5. a needs-review Stage, evaluating Gate, or open approval Attention makes the
   Task `needs_review`;
6. every inventory Stage completed with its exact Gate passed makes the Task
   `needs_review` until an explicit user approval completes it;
7. running work, prior formal completion, or a passed Gate with remaining
   inventory work makes the Task `active`;
8. an initialized inventory with no stronger signal makes the Task `ready`.

An explicitly completed Task remains completed while all inventory Stages and
Gates still satisfy the completion condition. A later stale Gate,
reconciliation requirement, blocking Attention, failure, or cancellation can
derive a different target only through the legal frozen transition graph.

## Atomic writes and transition paths

Agora computes the shortest deterministic legal path through the frozen Task
state machine. Multi-edge paths commit in the same transaction as the Control
Plane Stage, Gate, or invalidation mutation that triggered them; Attention
created by those Control Plane operations participates in that snapshot. Each
edge uses optimistic Task versioning and emits mirrored `task.state_changed`
audit events containing the bounded lifecycle decision.

The following Control Plane mutations reconcile in their existing transaction:

- Stage inventory initialization;
- formal Stage/Gate creation;
- protocol Run start and settlement;
- protocol retry preparation;
- active-Evidence replacement and Gate evaluation;
- Approval/Artifact invalidation propagation.

`completed -> active` remains legal only for invalidation or reconciliation.
An invalidated completed Task therefore follows `completed -> active ->
blocked` atomically when the new authoritative target is blocked.

## Human completion and recovery

Passing every Stage Gate does not silently approve the Task. It derives
`needs_review`. `agora task approve` records an explicit user-caused frozen Task
transition to `completed`, then updates the 0.5 Plan as an idempotent
compatibility projection. If that projection is interrupted after authoritative
completion, repeating the approval command repairs it without another Task
transition.

Attention response/cancellation remains owned by the existing Attention store.
Those writes do not acquire a second Task-state writer. `task resume` performs
explicit, idempotent lifecycle reconciliation after process recovery. The
read-only unified projection recomputes the decision in its existing snapshot
and reports `reconciliation_required` when persisted Task state no longer
matches current authoritative inputs; it never repairs state during a read.

## Unified projection and deferred boundaries

Unified projection schema `4.0` adds the bounded lifecycle decision and reports
one of `control_plane_managed`, `reconciliation_required`, or `unavailable`.
Grouped progress remains inventory-derived, and current-Stage selection remains
explicitly compatibility-sourced until authoritative Stage activation/routing
is a separate reviewed increment.

Authenticated HTTP lifecycle commands/fields, dynamic routing, methodology
migration, the missing authoritative AI-DLC graph, and Task Workbench UI remain
deferred.
