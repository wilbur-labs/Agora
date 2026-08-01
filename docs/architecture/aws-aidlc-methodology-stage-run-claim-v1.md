# AWS AI-DLC methodology later-Stage Run claim v1

Status: reviewed implementation; sequences 2 through 4; no runtime process

## Purpose and entry point

After the immediately preceding methodology Run settles successfully and the
next formal Gate is configured, the authenticated Control Plane may claim the
exact execution-contract sequence-2, sequence-3, or sequence-4 Run:

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

- the migration request/Gate/receipt, execution contract, settled immediate
  predecessor dispatch receipt, predecessor Run/Handoff, completed predecessor
  Stage, passed predecessor Gate, and immutable current Gate request/receipt;
- the exact Task, frozen Control Task, compatibility Plan/Stages, grouped
  inventory, requested `READY` Stage, `PENDING` Gate, and current runnable
  route;
- the clean repository/ref/commit before and after bounded rehashing of every
  migration source file;
- the complete runtime registry and every pinned runtime command;
- the canonical Gate requirements and deterministic Run identity;
- the absence of any earlier same-sequence claim, formal or compatibility Run,
  consultation, Artifact, or Evidence; and
- sufficient remaining Plan Token and cost budget for the exact Stage
  reservation.

It then seals one `ContextPack@1.0`, invokes the Control Plane's private formal
Run-start primitive, advances only the authoritative requested Stage from
`READY` to `RUNNING`, reconciles the Control Task lifecycle, advances Task
metadata/version once, and persists one immutable
`MethodologyStageRunClaimReceipt@1.0` plus both audit streams. Any validation,
Control Plane, event, or ledger failure rolls back every effect.

Exact replay returns the same receipt only for the same hash-sealed request and
the same currently authorized original principal. Different requests for the
same Task/Stage, Gate, sequence, Run, or Context identity conflict.
Concurrent identical callers converge on one row.

## Exact bounded Context

The frozen AWS AI-DLC execution contract names sequence 2
`workspace-detection`, sequence 3 `state-init`, and the selected sequence-4
production Stage (`reverse-engineering` in the exercised brownfield contract).
All three positions have an exact empty representable input-Artifact set, so a
claim must not fabricate predecessor Artifacts from descriptive source text or
a Handoff. Sequence 2 and 3 have empty required-output sets. Sequence 4 instead
materializes one deterministic `RequiredOutput` for every frozen output
contract, binding Task, Stage, Run, source-output id, kind, and requirement.

The protocol retains a versioned `MethodologyStageInputArtifactBinding` shape
for later contract positions that genuinely consume selected prior outputs,
but the bounded sequence-2/3/4 builder rejects any such binding. Five bounded
policy entries preserve:

- the execution-contract and repository binding;
- the settled predecessor dispatch/Handoff and configured Gate receipt;
- the exact Stage role, runtime, and sensor template;
- the frozen methodology source-input text; and
- a hash-bound Handoff/Gate projection. The full immutable contracts remain
  authoritative through the execution-contract hash, while required outputs
  and formal Gate requirements are supplied directly. This avoids duplicating
  large evidence templates in the Windows-bounded dispatch prompt.

No transcript, inferred Evidence, native state, Task memory, Project knowledge,
user preference, Artifact, or provider output is injected. A policy entry over
20,000 characters fails before persistence.

## Authority, compatibility, and projection

The new `protocol_runs` row is the only formal Run authority. No legacy
`orchestration_runs` row, PID, prompt, preflight, process attachment, provider
usage, Artifact, Evidence, Gate evaluation, or dispatch authority is created.
The compatibility Plan and Stage rows remain unchanged.

The immutable claim ledger records the active Token/cost reservation. Unified
Task projection includes each protocol-only sequence-2/3/4 Run with its pinned
runtime and reservation while the formal Run remains unsettled. The receipt
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

Sequence-2, sequence-3, and sequence-4 dispatch are defined in
`aws-aidlc-methodology-stage-run-dispatch-v1.md`. It attaches exactly one pinned
runtime process to each existing formal Run and settles it through
the unchanged Handoff parser and Control Plane Gate evaluator. It uses a
separate later-Stage dispatch/recovery ledger without changing the frozen
first-Run contracts, creating a second Run or Context Pack, or inferring
semantic success from process exit code.

Positions beyond sequence 4,
cross-Stage rework, dynamic provider substitution, authenticated HTTP, Task
Workbench UI, and native AWS AI-DLC file installation remain separate reviewed
increments.
