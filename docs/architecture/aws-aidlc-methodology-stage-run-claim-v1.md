# AWS AI-DLC methodology later-Stage Run claim v1

Status: reviewed implementation; sequences 2 through 8; no runtime process

## Purpose and entry point

After the immediately preceding methodology Run settles successfully and the
next formal Gate is configured, the authenticated Control Plane may claim the
exact execution-contract sequence-2 through sequence-8 Run:

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
Those three positions have an exact empty representable input-Artifact set, so a
claim must not fabricate predecessor Artifacts from descriptive source text or
a Handoff. Sequence 2 and 3 have empty required-output sets. Sequence 4 instead
materializes one deterministic `RequiredOutput` for every frozen output
contract, binding Task, Stage, Run, source-output id, kind, and requirement.

Sequence 5 is `requirements-analysis` in the same selected contract. Its frozen
input resolution leaves `intent-statement`, `scope-document`, and
`team-practices` explicitly `optional_absent`, and selects exactly the
sequence-4 `business-overview`, `architecture`, and `code-structure` outputs.
Each `MethodologyStageInputArtifactBinding` is derived in contract order from
the deterministic producer `RequiredOutput`, settled sequence-4 dispatch,
sealed Handoff, registered Artifact payload/version/hash, producer Stage and
formal Run. Same-kind or unrelated Artifacts are never substitutes. The three
exact version references are sealed into the sequence-5 Context Pack; Artifact
content is not copied into policy text. Sequence 5 materializes the two frozen
`requirements` and `requirements-analysis-questions` outputs.

Sequence 6 is the first selected `code-generation` unit. Six unavailable
upstream design inputs remain explicitly `optional_absent`. Its two exact
inputs stay in frozen contract order:

1. the required `unit-of-work` Task seed, whose Artifact id/version/hash and
   repository/ref/commit/path are derived from and cross-checked against the
   immutable migration request and execution contract; and
2. the required `requirements` Artifact produced by the settled sequence-5
   formal Run.

`MethodologyStageInputArtifactBinding.producer_run_id` is non-null for a
selected formal producer and null only for an external Task seed with an exact
repository location. This keeps old sequence-5 receipt payloads and hashes
stable while allowing the sequence-6/7 receipts and Context Packs to distinguish
a real prior Run from a seed that has no producer Run. The claim transaction
passes only those null-producer references through the Control Plane's bounded
external-input exception; selected prior outputs still require registered
`protocol_artifacts` rows. Live repository and seed hashes are rechecked before
the claim. Sequence 6 materializes `code-generation-plan` and `code-summary`.

Sequence 7 is the second selected `code-generation` unit. It intentionally
reuses the same external `unit-of-work` version reference and the same exact
sequence-5 `requirements` version, while its binding names the distinct
`code-generation-unit-002` consumer Stage. Its deterministic Run, Context, and
`code-generation-plan`/`code-summary` output ids are disjoint from sequence 6.
The settled sequence-6 dispatch remains the direct lifecycle predecessor; it is
not substituted as the producer of sequence-7 `requirements`.

Sequence 8 is the aggregate `build-and-test` Stage. Its two selected input
contracts each use `all_units` and expand in frozen input-contract order, then
producer-instance order: the sequence-6 and sequence-7
`code-generation-plan` versions followed by the sequence-6 and sequence-7
`code-summary` versions. Every binding retains its concrete producer Stage and
Run. Resolution recomputes the same settled dispatch, formal claim,
deterministic required output, sealed Handoff, and registered Artifact
authority used by `single` inputs; one unit cannot substitute for a missing or
tampered sibling. The Context therefore carries four distinct exact version
references and materializes the seven frozen build/test outputs. The Handoff
projection exposes only production-owned requirement ids and the count of
withheld formal-Gate requirements; the two independent completion-review ids
remain outside the production Context and Handoff contract.

Five bounded policy entries preserve:

- the execution-contract and repository binding;
- the settled predecessor dispatch/Handoff and configured Gate receipt;
- the exact Stage role, runtime, and sensor template;
- the frozen methodology source-input text; and
- a hash-bound Handoff/Gate projection. The full immutable contracts remain
  authoritative through the execution-contract hash, while required outputs
  and formal Gate requirements are supplied directly. This avoids duplicating
  large evidence templates in the Windows-bounded dispatch prompt.

No transcript, inferred Evidence, native state, Task memory, Project knowledge,
user preference, Artifact content, or unbound provider output is injected. A
policy entry over 20,000 characters fails before persistence.

## Authority, compatibility, and projection

The new `protocol_runs` row is the only formal Run authority. No legacy
`orchestration_runs` row, PID, prompt, preflight, process attachment, provider
usage, Artifact, Evidence, Gate evaluation, or dispatch authority is created.
The compatibility Plan and Stage rows remain unchanged.

The immutable claim ledger records the active Token/cost reservation. Unified
Task projection includes each protocol-only sequence-2/3/4/5/6/7/8 Run with its pinned
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

Sequence-2 through sequence-8 dispatch are defined in
`aws-aidlc-methodology-stage-run-dispatch-v1.md`. It attaches exactly one pinned
runtime process to each existing formal Run and settles it through
the unchanged Handoff parser and Control Plane Gate evaluator. It uses a
separate later-Stage dispatch/recovery ledger without changing the frozen
first-Run contracts, creating a second Run or Context Pack, or inferring
semantic success from process exit code.

Sequence 8 exhausts the selected bugfix inventory, but its production claim
does not authorize completion-review Evidence. Independent completion-review
dispatch/finalization, cross-Stage rework, dynamic provider substitution,
authenticated HTTP, Task Workbench UI, and native AWS AI-DLC file installation
remain separate reviewed increments.
