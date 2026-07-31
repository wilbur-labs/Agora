# AWS AI-DLC methodology later-Stage Run claim v1

Status: reviewed implementation; sequence 2 only; no runtime process

## Purpose and entry point

After the first methodology Run settles successfully and the next formal Gate
is configured, the authenticated Control Plane may claim exactly the
execution-contract sequence-2 Run:

```powershell
agora task migration-next-stage-run-claim SUCCESSOR_TASK_ID `
  --request .\methodology-stage-run-claim-request.json `
  --credential-env AGORA_CONTROL_TOKEN
```

`MethodologyStageRunClaimRequest@1.0` binds the live Task, Control Task, Plan,
grouped inventory, immutable execution contract, next-Stage Gate receipt,
settled predecessor dispatch receipt, repository revision, exact
Stage/Gate/runtime and expected versions. The formal Run identity is
deterministically derived from Task, execution-contract hash, Stage sequence,
and Stage key. The Context Pack identity must be `context:<run_id>`.

The request structurally fixes `claim_formal_run=true` and
`start_runtime_process=false`. Authentication and bounded request loading
complete before service or storage initialization. The principal must retain
`control_plane.approve` for the exact Project and match the principal already
bound to the migration, execution-contract, predecessor-Run, and next-Gate
chain.

## Atomic live recheck and claim

One `BEGIN IMMEDIATE` transaction reloads and revalidates:

- the migration request/Gate/receipt, execution contract, settled first
  dispatch receipt, first Run/Handoff, completed predecessor Stage, passed
  predecessor Gate, and immutable sequence-2 Gate request/receipt;
- the exact Task, frozen Control Task, compatibility Plan/Stages, grouped
  inventory, sequence-2 `READY` Stage, `PENDING` Gate, and current runnable
  route;
- the clean repository/ref/commit before and after bounded rehashing of every
  migration source file;
- the complete runtime registry and every pinned runtime command;
- the canonical Gate requirements and deterministic Run identity;
- the absence of any earlier sequence-2 claim, formal or compatibility Run,
  consultation, Artifact, or Evidence; and
- sufficient remaining Plan Token and cost budget for the exact Stage
  reservation.

It then seals one `ContextPack@1.0`, invokes the Control Plane's private formal
Run-start primitive, advances only the authoritative sequence-2 Stage from
`READY` to `RUNNING`, reconciles the Control Task lifecycle, advances Task
metadata/version once, and persists one immutable
`MethodologyStageRunClaimReceipt@1.0` plus both audit streams. Any validation,
Control Plane, event, or ledger failure rolls back every effect.

Exact replay returns the same receipt only for the same hash-sealed request and
the same currently authorized original principal. Different requests for the
same Task/Stage, Gate, sequence, Run, or Context identity conflict.
Concurrent identical callers converge on one row.

## Exact sequence-2 Context

The frozen AWS AI-DLC execution contract names sequence 2
`workspace-detection`. Its input and output contract sets are both empty.
Consequently this claim must seal the exact empty `input_artifacts` and
`required_outputs` sets. It must not fabricate a predecessor Artifact merely
because the predecessor Handoff is part of the authority chain.

The protocol retains a versioned `MethodologyStageInputArtifactBinding` shape
for later contract positions that genuinely consume selected prior outputs,
but the sequence-2 builder rejects any such binding. Five bounded policy
entries preserve:

- the execution-contract and repository binding;
- the settled predecessor dispatch/Handoff and configured Gate receipt;
- the exact Stage role, runtime, and sensor template;
- the frozen methodology source-input text; and
- the complete Handoff and Gate templates.

No transcript, inferred Evidence, native state, Task memory, Project knowledge,
user preference, Artifact, or provider output is injected. A policy entry over
20,000 characters fails before persistence.

## Authority, compatibility, and projection

The new `protocol_runs` row is the only formal Run authority. No legacy
`orchestration_runs` row, PID, prompt, preflight, process attachment, provider
usage, Artifact, Evidence, Gate evaluation, or dispatch authority is created.
The compatibility Plan and Stage rows remain unchanged.

The immutable claim ledger records the active Token/cost reservation. Unified
Task projection includes the protocol-only sequence-2 Run with its pinned
runtime and reservation, while the formal Run remains unsettled. The receipt
fixes:

```text
context_pack_materialized = true
formal_run_created = true
usage_reservation_recorded = true
compatibility_run_created = false
protocol_artifacts_created = false
protocol_evidence_created = false
runtime_preflight_created = false
process_started = false
runtime_spawned = false
process_spawn_authority = false
provider_substitution = false
```

## Succeeding process boundary

The next reviewed slice may attach exactly one pinned sequence-2 runtime
process to this existing formal Run and settle it through the unchanged
Handoff parser and Control Plane Gate evaluator. It must generalize the
first-Run dispatch/recovery ledger without creating a second Run or Context
Pack, must settle the active reservation truthfully, and must not infer
semantic success from process exit code.

Later inventory positions, cross-Stage rework, dynamic provider substitution,
authenticated HTTP, Task Workbench UI, and native AWS AI-DLC file installation
remain separate reviewed increments.
