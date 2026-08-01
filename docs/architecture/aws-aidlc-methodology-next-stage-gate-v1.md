# AWS AI-DLC methodology next-Stage Gate v1

Status: reviewed implementation; sequences 2 through 6

## Purpose and entry point

After the first formal AWS AI-DLC Run settles successfully, the Control Plane
uses the immutable grouped Stage inventory to create and activate the next
formal Stage. Stage readiness and formal Run claimability remain separate:
the routed Stage can be `READY` while its contract-bound Gate is still absent.

The authenticated Gate configuration command is:

```powershell
agora task migration-next-stage-gate SUCCESSOR_TASK_ID `
  --request .\methodology-stage-gate-request.json `
  --credential-env AGORA_CONTROL_TOKEN
```

The command configures only the Gate for the exact current Stage at execution
contract sequence 2 through 6. Sequence 2 is authorized by the settled first-Run
dispatch; each later position is authorized only by the settled immediately
preceding later-Stage dispatch. It does not create a Run or Context Pack, start
a process, register an Artifact or Evidence item, or grant dispatch authority.

## Sealed request and predecessor authority

`MethodologyStageGateRequest@1.0` binds:

- the live Task, Control Task, Plan, grouped inventory, and execution contract;
- the repository/ref/commit and pinned production runtime;
- the immutable predecessor `MethodologyRunDispatchReceipt@1.0` for sequence 2
  or `MethodologyStageRunDispatchReceipt@1.0` for sequences 3 through 6;
- the settled predecessor Run, completed Stage, passed Gate, and exact Handoff
  identity and hash; and
- the next Stage sequence, Stage/Gate/runtime identity, and expected formal
  Stage version.

The request is authenticated with `control_plane.approve`. The principal must
be authorized for the exact project and must be the same principal that
authenticated the migration Gate, execution contract, and first Run claim.
Authentication and request loading occur before service or storage
initialization.

## Transaction and fail-closed checks

One `BEGIN IMMEDIATE` transaction reloads and revalidates the complete sealed
chain. In addition to the request fields, it checks:

- the migration source artifacts, repository revision, runtime registry, and
  every command pin remain unchanged;
- the predecessor dispatch is durably settled and its receipt agrees with the
  formal Run, protocol result, Handoff, completed Stage, and passed Gate;
- the Control Plane route is the exact requested sequence-2, sequence-3, or
  sequence-4, sequence-5, or sequence-6 contract Stage in `READY`;
- the next formal Gate does not already exist; and
- no compatibility Run, consultation, formal Run, Artifact, or Evidence exists
  for the next Stage.

Any mismatch is zero-write. The transaction calls the Control Plane's
authoritative Gate configuration primitive with the exact canonical
requirements from the immutable execution contract. It then requires the
same Stage to remain `READY`, the new Gate to be version 1 and `PENDING`, and
the Task, Control Task, Plan, and Stage records to remain byte-for-byte
unchanged.

The operation and its Task/Control Plane events commit together. An event or
ledger failure rolls back the Gate and all associated records. Concurrent
identical callers converge on one immutable receipt; a different request for
the same Stage conflicts. The original sequence-1 dispatch id remains the
compatibility lineage root in the existing Gate ledger. Sequences 3 through 6 also
store a foreign-keyed direct-predecessor row that binds the settled immediately
preceding dispatch id, receipt id/hash, Run, Stage, Gate, Plan, and adjacency.
Missing or tampered direct-predecessor authority blocks Gate/Run replay, budget
admission, and unified projection.

## Receipt semantics

`MethodologyStageGateReceipt@1.0` binds the authenticated request, complete
predecessor receipt/Run/Handoff chain, exact next Stage and canonical Gate
requirements, and before/after authority facts.

The route is runnable both before and after configuration because
`StageRouteDecision.runnable` represents a ready routed Stage, not the
existence of a formal Gate. The receipt therefore records the distinct
claimability boundary:

- `formal_run_claimable_before=false`;
- `formal_run_claimable_after=true`.

It also records that no Task, Control Task, Plan, or Stage mutation occurred
and that no Context Pack, Run, process, Artifact, Evidence, dispatch authority,
or provider substitution was created.

## Deferred boundaries

This increment configures execution contract sequence 2 after the successfully
settled first Run, sequence 3 after the successfully settled sequence-2 Run,
sequence 4 after the successfully settled sequence-3 Run, sequence 5 after the
successfully settled sequence-4 Run, and sequence 6 after the successfully
settled sequence-5 Run. The succeeding
claim boundary is defined in
`aws-aidlc-methodology-stage-run-claim-v1.md`. It consumes the immutable Gate
receipt, seals the exact Context Pack, and creates only the formal Run without
starting a process.

Generalizing the same transition beyond sequence 6, automatic rework, dynamic
provider substitution, authenticated HTTP, Task Workbench UI, and native AWS
AI-DLC file installation remain separate reviewed
increments.
