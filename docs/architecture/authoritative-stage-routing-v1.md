# Authoritative Stage activation and routing v1

Status: reviewed implementation baseline.

This increment makes the sealed grouped Stage inventory, rather than the 0.5
compatibility Plan cursor, authoritative for the current Stage and its pinned
runtime. It remains a deterministic linear route for the currently pinned
provisional methodology; risk/capability-aware methodology selection and
dynamic graph branches remain separate work.

## Route decision

Agora flattens inventory groups and Stages in their sealed order. The current
route is the first Stage that has not reached `completed` with its exact formal
Gate `passed`. Completed Stages must form an ordered prefix. A completed Stage
without its passed Gate, an out-of-inventory Stage/Gate, a mismatched Gate
binding, or a later Stage already active before the routed Stage finishes fails
closed.

The route carries the inventory hash, group and Stage positions, Stage/Gate
keys, title, role, and runtime. Runtime and role are read from the sealed
inventory, never from the compatibility Plan. A route is dispatchable only
when its formal Stage is `ready` and the persisted frozen Task lifecycle agrees
with a dispatchable authoritative decision. Its `runnable` flag therefore
remains false during lifecycle drift or blocking Attention even when the routed
Stage itself is `ready`.

Gate passage and semantic Stage result remain separate dimensions. A Run may
produce passing formal Evidence while its Handoff reports a semantic blocker;
the valid settled result is then a `blocked` Stage with a `passed` Gate. Routing
keeps that Stage current and non-runnable until explicit retry; Gate passage by
itself never marks the Stage complete or advances the route.

## Activation and advancement

Activation is an idempotent Control Plane operation. It creates only the
inventory-selected Stage as `pending`, advances it to `ready` in the same
transaction, emits a bounded `stage.activated` event, reconciles frozen Task
state, and stores an operation receipt. A caller cannot activate a later
inventory Stage by supplying a Stage key, and the legacy Stage ensure/configure
path cannot create a `ready` Stage after an inventory has been sealed.

When a formal Run settles its Stage as `completed`, Agora derives and activates
the next route inside that same settlement transaction. The settlement receipt
therefore carries the authoritative next route. The 0.5 Plan cursor and
operational Stage ledger consume that receipt afterward as a compatibility
projection; they do not independently choose the next Stage. A crash between
those transactions leaves the Control Plane route authoritative and `task
resume` repairs the compatibility projection without redispatch.

`start_protocol_run` independently revalidates the route, exact Stage/Gate
binding, ready Stage status, and frozen Task lifecycle before sealing a Context
Pack. Exit code, compatibility state, Agent suggestions, or caller-selected
runtime cannot bypass those checks.

## Read model and recovery

Unified Task projection schema `5.0` exposes the bounded route and labels the
current Stage source `control_plane_route`. Reads derive the route inside their
existing rollback-only snapshot and never activate a Stage. Missing Task state
or inventory keeps routing unavailable and directs explicit resume recovery;
all completed Stages produce no current route.

Create and attach explicitly activate the first route after Task state and
inventory initialization. Resume activates a missing/pending route only after
recovering any live or interrupted formal Run.

An explicit protocol retry cancels only the still-open blocker Attention that
the same failed protocol Run generated, using the existing Attention store and
its optimistic version. It then prepares the Stage and reconciles Task state.
Unrelated human, invalidation, question, or approval Attention is never cleared
to make a route dispatchable.

## Deferred boundaries

This increment does not add authenticated HTTP commands, Task Workbench UI,
methodology migration, parallel/DAG routing, risk-driven runtime substitution,
or the missing authoritative AI-DLC graph. It does not change the existing
runtime budget allocator or provider-specific usage measurement.
