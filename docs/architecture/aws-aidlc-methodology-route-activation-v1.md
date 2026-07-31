# AWS AI-DLC methodology first-route activation v1

Status: authenticated first-route configuration and activation; no runtime
dispatch

## Purpose and entry point

An inert migrated successor becomes ready at exactly its first sealed Stage
through:

```powershell
agora task migration-route-activate SUCCESSOR_TASK_ID `
  --request ROUTE_ACTIVATION_REQUEST.json `
  --credential-env ENVIRONMENT_VARIABLE
```

The request is a hash-sealed `MethodologyRouteActivationRequest@1.0`. It binds
the current Task, Control Task, Plan, grouped inventory, repository revision,
execution-contract identity/hash, and exact first Stage/Gate. It can request
only first-route activation and structurally cannot request runtime dispatch.

The credential is authenticated before service or storage initialization.
The principal must retain `control_plane.approve` for the exact Project and
must be the same principal already authenticated by the migration Gate and
execution contract. Neither the credential nor its environment-variable name
is serialized.

## Atomic live recheck

One `BEGIN IMMEDIATE` transaction reloads and verifies:

- the migration request, authenticated migration Gate, activation receipt, and
  immutable `MethodologyExecutionContract@1.0`;
- the exact Task, frozen Control Task, Plan, compatibility Stage rows, grouped
  inventory, and first authoritative route versions named by the request and
  contract;
- the current clean repository/ref/commit before and after bounded hashing of
  every migration seed and proposal file;
- current Codex, Claude, and Kiro registry and command hashes;
- an inert successor with no formal Stage/Gate, orchestration Run,
  consultation, protocol Run, protocol Artifact, or Evidence.

Any drift fails before persistence. Stage/Gate configuration, seed-reference
registration, Task metadata/version advancement, Control Task reconciliation,
the activation receipt, and audit events commit or roll back together.

Exact replay returns the immutable original receipt only for the same request
and same currently authorized principal. A different request, revoked
permission, changed Project scope, or another principal cannot reuse the
activation.

## Seed Artifact reference boundary

Only hash-bound Task seed inputs consumed by the first Stage are registered.
Each registration retains the consumer Stage, source Artifact kind, Artifact
identity/version/hash, and repository/ref/commit/path from the execution
contract.

These are external `ArtifactVersionRef` bindings, stored separately in
`orchestration_methodology_seed_artifact_refs`. They are not fabricated
`Artifact` records: no producer Run exists yet, so activation creates no row in
`protocol_artifacts` and never attributes a seed to a future runtime.

## Stage, Gate, and route authority

The transaction verifies that the current route is inventory position one and
has no formal state. It then:

1. creates only that Stage as `pending`;
2. configures only its exact Gate with the complete execution-contract
   requirement set;
3. advances that Stage to `ready`;
4. reconciles the frozen Task lifecycle; and
5. records `MethodologyRouteActivationReceipt@1.0`.

The immutable execution contract remains a pre-activation definition with
`route_activated=false`. The activation receipt is the later authoritative
fact. Compatibility Plan and Stage rows remain unchanged and do not select or
activate the route.

The Task metadata records the exact activation request and execution-contract
hashes, changes `methodology_route_activated` to true, and retains
`methodology_dispatch_authority=false`.

The route read model's `runnable` field continues to describe frozen lifecycle
readiness (`ready` Stage plus dispatchable Task status). It does not override
the separate methodology dispatch-authority flag or authorize a provider
claim.

## Deferred dispatch boundary

The receipt fixes:

```text
route_activated = true
protocol_artifacts_created = false
run_created = false
runtime_spawned = false
dispatch_authority = false
provider_substitution = false
```

No Context Pack is instantiated, no Run reservation or runtime preflight is
created, and no provider process is launched. The reviewed successor slice is
`aws-aidlc-methodology-run-claim-v1.md`: it materializes and claims the first
formal Run from the exact Stage execution template and registered seed
references while keeping process launch separate. HTTP, UI, provider
substitution, automatic cross-Stage rework, and native AI-DLC file installation
remain deferred.
