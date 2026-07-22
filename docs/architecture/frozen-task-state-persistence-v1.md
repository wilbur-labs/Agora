# Frozen Task-state persistence v1

Status: reviewed implementation baseline.

This increment persists the frozen Agora Task state machine without changing or
mapping the 0.5 Task manifest state. It is deliberately bounded before grouped
Stage inventory, lifecycle derivation, HTTP commands, and Task Workbench UI.

## Authority boundary

`control_tasks` is the only persistence projection for the frozen Task states:

```text
backlog | ready | active | blocked | needs_review |
completed | failed | cancelled
```

The existing `tasks.state` column remains the compatibility manifest and keeps
its 0.5 states such as `requirements`, `design`, `planned`, and `running`.
Initialization never reads that column and no backfill guesses a frozen state
for existing Tasks. New Tasks created or attached through the unified
orchestration service are explicitly initialized at `backlog`; an older Task
whose Plan already committed before initialization uses explicit `task resume`
recovery to initialize at `backlog`.

## Write contract

Only `ControlPlaneStore` writes `control_tasks`. Initialization is idempotent and
emits one `task.state_initialized` Control Plane event. A transition requires:

- the frozen Task state to exist;
- an exact expected version;
- a valid frozen state-machine edge;
- a bounded non-blank actor and reason;
- an explicit cause (`user_action`, `orchestration`, `reconciliation`, or
  `invalidation`);
- a bounded operation key whose replay input is content-fingerprinted.

Operation keys are globally unique across Tasks because `control_operations`
is one Control Plane ledger. A caller must namespace keys with Task/action
identity; reuse against another Task fails closed as different input.

The state update, mirrored Task/Control Plane event, and replay receipt commit in
one SQLite transaction. A reused operation key with different input fails
closed. Concurrent writers using the same version produce one winner.

`completed -> active` additionally requires an `invalidation` or
`reconciliation` cause, matching the frozen rule that completed work cannot
silently reopen.

## Read contract

The Control Plane exposes an internal Task-state reader. The unified Task
projection reads `control_tasks` in its existing rollback-only snapshot. If no
frozen state exists it returns an explicit unavailable reason rather than
showing the compatibility state as authoritative. The existing authenticated
Control Plane HTTP projection is unchanged in this bounded increment.

The unified JSON projection moves to schema version `2.0` because `task_state`
now means frozen Control Plane state instead of 0.5 manifest state. The legacy
manifest remains under `task`, and text output labels it as `legacy`. Unified
projection schema `4.0` now reports a bounded lifecycle decision and whether
persisted Task state is `control_plane_managed`, requires explicit
reconciliation, or is unavailable. The read remains side-effect free.

Task creation currently commits the compatibility Task, Plan, and frozen state
in separate transactions. If interruption occurs after Plan creation, the
projection reports the missing state and directs the user to `task resume`.
Resume is an explicit mutating reconciliation path and idempotently initializes
the missing state; ordinary status reads remain side-effect free.

## Lifecycle wiring

The complete grouped inventory now drives deterministic Task lifecycle
reconciliation as documented in `task-lifecycle-derivation-v1.md`. The
derivation uses authoritative Stage, Gate, Attention, invalidation, and
reconciliation state only; compatibility Plan state, Run exit codes, and the
unified projection remain non-authoritative. Explicit human approval is still
required for `needs_review -> completed`. HTTP and UI remain later projections
of the same command/read model.
