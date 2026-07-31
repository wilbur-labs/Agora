# AWS AI-DLC methodology first-Run claim v1

Status: authenticated formal Run and Context Pack claim; no runtime process

## Purpose and entry point

An activated methodology successor claims exactly its first formal Run through:

```powershell
agora task migration-run-claim SUCCESSOR_TASK_ID `
  --request RUN_CLAIM_REQUEST.json `
  --credential-env ENVIRONMENT_VARIABLE
```

The hash-sealed `MethodologyRunClaimRequest@1.0` binds the live Task and
Control Task versions, compatibility Plan, grouped Stage inventory, immutable
execution contract, route-activation receipt, repository revision, first
Stage/Gate/runtime, and caller-selected stable Run and Context Pack identities.
The Context Pack identity must be `context:<run_id>`. The request can authorize
only the formal claim and structurally fixes `start_runtime_process=false`.

The credential is authenticated before service or storage initialization. The
principal must retain `control_plane.approve` for the exact Project and must be
the same principal already authenticated by the migration Gate, execution
contract, and route activation. Exact replay requires the same request and the
same currently authorized principal.

## Atomic live recheck and claim

One `BEGIN IMMEDIATE` transaction reloads and verifies:

- the migration request, authenticated migration Gate, migration receipt,
  execution contract, route-activation request/receipt, and registered first
  Stage seed references;
- the exact Task, frozen Control Task, compatibility Plan/Stages, grouped
  inventory, authoritative first Stage/Gate, and current runnable route;
- the current clean repository/ref/commit before and after bounded hashing of
  every migration seed and proposal file;
- current Codex, Claude, and Kiro registry and command hashes; and
- the absence of any earlier compatibility Run, consultation, protocol Run,
  protocol Artifact, or Evidence for the successor.

It then seals the exact `ContextPack@1.0`, inserts the formal `protocol_runs`
record, advances only the authoritative first Stage from `ready` to `running`,
reconciles the Control Task lifecycle, records the Token/cost reservation,
advances Task metadata/version, and writes immutable audit events and
`MethodologyRunClaimReceipt@1.0`. Any failure rolls back every effect.

The shared Control Plane Run-start primitive still requires registered
`Artifact` records by default. This methodology path supplies only exact
external inputs that the same transaction has revalidated against
`orchestration_methodology_seed_artifact_refs`; it does not weaken the generic
Artifact Registry boundary.

## Exact Context Pack derivation

The first Context Pack is derived only from the sealed first
`MethodologyStageExecutionContract`:

- `stage_contract`, forbidden constraints, and Run budget are copied exactly;
- inputs are the exact registered, hash-bound Task seed
  `ArtifactVersionRef` values in contract order;
- optional-absent inputs stay absent, while any selected prior-Stage output on
  the first Stage fails closed;
- output identities are deterministically derived from Task, Stage, Run, and
  source output template, while output kind and requiredness remain exact; and
- bounded policy entries retain the execution/activation/repository binding,
  role and sensor template, source inputs, and complete Handoff/Gate template.

No transcript, inferred Evidence, native state, project knowledge, user
preference, or memory entry is injected. A policy entry over 20,000 characters
fails before persistence; Agora does not truncate or summarize contract
semantics.

## Compatibility and projection boundary

The formal `protocol_runs` row is the authoritative Run claim. No legacy
`orchestration_runs` row, PID, prompt process, preflight, provider output, or
usage settlement is fabricated. The 0.5 Plan and Stage rows therefore remain
unchanged.

The unified projection includes the protocol-only Run and reads its pinned
runtime and active Token/cost reservation from the immutable methodology claim
record. The active reservation is removed from the projected remaining budget
only while the formal Run is unsettled.

The receipt fixes:

```text
context_pack_materialized = true
formal_run_created = true
usage_reservation_recorded = true
compatibility_run_created = false
protocol_artifacts_created = false
runtime_preflight_created = false
process_started = false
runtime_spawned = false
process_spawn_authority = false
provider_substitution = false
```

## Successor process boundary

The separately reviewed successor is specified in
`aws-aidlc-methodology-run-dispatch-v1.md`. It collects a fresh pinned-runtime
capability observation and preflight outside the claim transaction, rechecks
the exact repository/registry/command/launch binding immediately before
process creation, and attaches process execution to this already claimed Run.
It does not create a second Run or Context Pack, substitute a runtime/model,
infer provider serviceability, or relax the existing
unbounded-native-usage acknowledgement.

Automatic later-Stage claim/dispatch and cross-Stage rework, HTTP, UI, dynamic
provider substitution, and native AI-DLC file installation remain deferred.
