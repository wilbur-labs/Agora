# Grouped Stage inventory v1

Status: reviewed implementation baseline.

This increment persists the complete Stage identity and order for the
methodology already pinned to an Agora Task. It is the authority for which
Stages belong to that Task and is now consumed by the separately reviewed
frozen Task lifecycle derivation.

## Authority and grouping

The inventory is generated only from the immutable `MethodologyDefinition`
payload stored with the Task's Plan. Before generation, Agora verifies the
methodology identity, version, hash, provisional flag, Task, and project against
the Plan ledger. It never copies Stage identity or order from the temporary
`orchestration_stages` compatibility rows.

Inventory schema `1.0` groups the current linear workflow by its pinned Plan.
The existing provisional `agora-aidlc-foundation@0.1` method therefore has one
group containing its complete three-Stage planning and review loop. This is
complete for that pinned provisional method only; it is not the missing
authoritative AI-DLC graph and must not be presented as a full delivery method.

Each sealed inventory binds:

- Task, project, Plan, and inventory identity;
- methodology identity, version, SHA-256, and provisional status;
- the pinned concrete Task-contract identity and hash when one exists;
- ordered group identity and title;
- ordered Stage key, Gate key, title, role, and runtime.

Group and Stage sequences must be contiguous. Group, Stage, and Gate identities
are unique, the complete inventory is bounded to 200 Stages, and its canonical
content is SHA-256 sealed.

## Persistence and mutation boundary

`control_stage_inventories` is an additive Control Plane table. It stores one
immutable sealed inventory per Task after frozen Task-state initialization. An
identical replay is idempotent; any different definition for the same Task
fails closed. The row and mirrored Task/Control Plane audit event commit in one
SQLite transaction.

For Tasks with an inventory, subsequent formal Stage creation must use an
inventory Stage and its exact Gate binding. This prevents a runtime, API
caller, or compatibility ledger from introducing an unpinned Stage. Tasks that
predate this increment remain readable without a guessed inventory; explicit
`task resume` reconstructs the inventory from the Plan's stored methodology
payload and verifies the original hash before persisting it.

Task creation, Plan creation, frozen Task-state initialization, and inventory
initialization remain separate durable transactions. The unified read model is
side-effect free and reports a missing inventory explicitly rather than
backfilling it during status reads.

## Unified projection

Unified Task projection schema `4.0` retains Stage identity, grouping, order,
title, role, runtime, and total progress from compatibility interpretation to
the sealed Control Plane inventory. Formal completion still counts only a
`control_stages` record in `completed`; an inventory Stage with no live Stage
record is remaining work, not completed work.

The projection labels the current Stage as sourced from the compatibility Plan
because authoritative Control Plane Stage activation/routing remains deferred.
If the inventory is absent, total, completed, and remaining progress are
unavailable rather than inferred from compatibility rows.

## Lifecycle and deferred boundaries

The lifecycle reconciler now consumes the complete inventory plus authoritative
Stage, Gate, Attention, invalidation, and reconciliation state. This inventory
increment still does not:

- activate later Stages or derive dependency edges;
- migrate a Task to a different methodology;
- expose inventory commands or fields through the authenticated HTTP API;
- recover or invent the missing authoritative AI-DLC graph;
- start Task Workbench UI work.

Lifecycle precedence, explicit completion approval, atomic transitions, and
read-only drift reporting are defined in
`task-lifecycle-derivation-v1.md`.
