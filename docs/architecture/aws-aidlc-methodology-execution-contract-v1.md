# AWS AI-DLC methodology execution contract v1

Status: reviewed execution-contract baseline; first-route activation is
defined separately and runtime dispatch remains deferred

## Purpose and entry point

The migration writer seals an inert successor Task, Plan, and grouped Stage
inventory. It does not reinterpret that inventory as a legacy `TaskContract`
or mutate its migration-receipt-bound hash. The next explicit command is:

```powershell
agora task migration-contract SUCCESSOR_TASK_ID `
  --credential-env ENVIRONMENT_VARIABLE
```

The credential is resolved before service or storage initialization. The
principal must still hold `control_plane.approve` for the Project and must be
the same principal authenticated by the persisted migration Gate. The
credential and its environment-variable name are never serialized.

Successful output is one hash-sealed `MethodologyExecutionContract@1.0`.
It binds the existing inventory by identity and hash; it does not replace or
modify that inventory.

## Atomic live binding

The materializer opens one `BEGIN IMMEDIATE` transaction and requires:

- the exact persisted migration request, authenticated Gate, recheck decision,
  and activation receipt with matching hashes;
- the successor Task, authoritative Control Task, Plan, all compatibility
  Stage rows, and sealed grouped inventory at the versions recorded by the
  migration receipt;
- every compatibility Stage still pending, with no Control Plane Stage or
  Gate, orchestration Run, consultation, or formal protocol Run;
- `methodology_route_activated=false`,
  `methodology_dispatch_authority=false`, and a
  `ready_for_implementation` Plan;
- the current clean repository/ref/commit before and after bounded rehashing
  of every scope seed and the migration proposal Artifact;
- the current Codex/Claude/Kiro registry and command hashes matching the
  migration request.

Any drift fails before persistence. Contract insertion and its audit event
commit or roll back together. Exact replay by the same currently authorized
principal returns the original sealed contract. Revoked permission, changed
Project scope, or another principal cannot replay it as current authority.

## Per-Stage executable templates

The contract materializes every inventory Stage instance in exact source and
inventory order. Each instance binds:

- its source Stage key, inventory Stage/Gate identities, source role profile,
  and the Task-pinned production runtime;
- the frozen `StageContract`, source input text, source sensors, and the
  Context/Handoff protocol versions;
- exact per-Run Token/cost reservations from the migration request plus the
  configured bounded process/output limits;
- every source output as a Run-scoped Artifact identity template, preserving
  required versus optional status and unit-kind applicability;
- every source Gate requirement as a repository/ref/commit-scoped formal
  Evidence requirement.

Input routing is closed over the selected scope without inventing Artifacts:

- `single` binds one selected upstream Stage;
- `matching_unit` binds the same deterministic unit index between expanded
  producer and consumer Stages;
- `all_units` binds every expanded producer instance to one downstream
  aggregate consumer;
- `task_seed` retains the exact repository path/hash as an
  `ArtifactVersionRef`;
- `optional_absent` represents only an optional input whose producer is
  outside the selected scope.

A required input may never use `optional_absent`. A missing, extra, stale, or
misrouted required seed blocks materialization.

## Handoff, Evidence, and Gate authority

The production Handoff contract contains only Evidence that the pinned Codex
Run may produce. Contract-completion, required-output registration, sensors,
and any bounded AWS source-review role remain production Evidence. AWS agent
roles are sealed profiles, not independent Agora runtimes or routing
authority.

The final selected Stage Gate additionally requires two separate formal
Evidence records:

- independent correctness completion from the Task-pinned Claude runtime;
- methodology stewardship completion from the Task-pinned Kiro runtime.

Those requirements are Gate inputs, not Codex Handoff fields. Their producers
must remain distinct. After every Stage Gate passes, the authoritative Task
lifecycle enters `needs_review`; the existing explicit human completion
approval remains required and retains all activation-definition binding
fields.

Agent suggestions, native state, source profiles, and process exit status have
no Task, Stage, or Gate authority. One format-only Handoff repair remains the
maximum.

## Persistence and deferred route authority

`orchestration_methodology_execution_contracts` stores one immutable contract
per successor Task/Plan/inventory and binds it to the migration receipt. The
operation adds no Control Plane Stage/Gate, Run, Artifact, Evidence, Approval,
or provider charge and leaves the Task/Plan/inventory unchanged.

The contract fixes:

```text
route_activated = false
runtime_spawned = false
routing_authority = false
dispatch_authority = false
```

The separately reviewed first-route transaction is defined in
`aws-aidlc-methodology-route-activation-v1.md`. It authenticates a hash-sealed
activation request, rechecks this contract and every live dependency,
registers only the first Stage's required seed Artifact references, configures
the exact first Stage/Gate, and activates that route without dispatch.
HTTP, UI, provider substitution, automatic cross-Stage rework, and native
AI-DLC file installation remain deferred.
