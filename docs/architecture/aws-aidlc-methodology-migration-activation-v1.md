# AWS AI-DLC methodology migration activation v1

Status: transactional successor-Task writer; no route activation or runtime
dispatch

## Purpose and CLI boundary

The migration activation path consumes the same hash-sealed
`MethodologyMigrationPreviewRequest@1.0` used by the read-only preview, but an
eligible preview is never reused as authorization. The writer is invoked as:

```powershell
agora task migration-activate TASK_ID `
  --request PATH `
  --credential-env ENVIRONMENT_VARIABLE
```

The named environment variable contains a configured Control Plane bearer
credential. Agora resolves it to exactly one principal, requires
`control_plane.approve` for the request's Project, and requires the principal
identity to equal the Gate assertion's `approved_by`. The credential and its
environment-variable name are not written to SQLite, the receipt, events, or
stdout.

Successful output is one hash-sealed
`MethodologyMigrationActivationReceipt@1.0`. Errors return exit code `2`
through the existing CLI error contract and create no partial successor.

## Atomic recheck

The writer opens one `BEGIN IMMEDIATE` transaction and, before any insert:

1. rereads the source Task manifest, authoritative Control Task, Plan, current
   methodology payload, sealed grouped inventory, active orchestration Runs,
   active consultations, and unsettled formal protocol Runs;
2. re-resolves the registered Git repository identity, ref, and commit;
3. rehashes every selected scope seed and the migration proposal Artifact with
   the existing per-file and aggregate bounds;
4. rechecks the pinned source and activation definitions, selected scope,
   runtime registry and command hashes, explicit Stage/runtime budgets, human
   assertion, and Task quiescence;
5. derives a fresh sealed `MethodologyMigrationPreviewDecision@1.0` and
   requires `eligible=true`;
6. authenticates the exact asserted approver.

Repository and Artifact observation deliberately occurs inside the writer
transaction. This keeps the external facts as close as possible to the SQLite
commit boundary. A changed or unavailable external fact blocks before any
write. A later distributed or remote runner contract would require a stronger
cross-resource transaction protocol rather than weakening this fail-closed
check.

## Persisted migration Gate and idempotency

`AuthenticatedMethodologyMigrationGate@1.0` binds:

- the complete hash-sealed human assertion and assertion hash;
- the live authenticated principal;
- the exact `control_plane.approve` permission and Project scope;
- credential verification and persistence time.

The Gate, source request, fresh recheck decision, and activation receipt are
stored together in `orchestration_methodology_migrations`. The table permits
one committed successor per source Task and one use of each request/Gate
identity. An exact retry with the same request id, request hash, source Task,
and authenticated principal returns the originally sealed receipt without
duplicating Tasks, events, Gates, Plans, inventories, or charges. Reusing an
identity with different bindings fails closed.

## Successor materialization

The transaction creates a new Task and never updates the source Task manifest,
source Task events, source Plan, source inventory, Stage, Gate, Run, budget, or
lifecycle row.

The successor Task records:

- predecessor Task, migration request, authenticated Gate, selected scope,
  source-graph hash, and activation-definition hash;
- production Codex, independent correctness Claude, and methodology
  stewardship Kiro pins from the approved request;
- the request's Task Token/cost envelope;
- `methodology_route_activated=false` and
  `methodology_dispatch_authority=false`.

The successor Plan pins the complete sealed
`MethodologyActivationDefinition@1.0` payload and uses its content hash as the
authoritative methodology hash. It is created in
`ready_for_implementation`, which is intentionally non-dispatching.

The grouped inventory:

- selects exactly the source Stages in the approved scope and preserves their
  source order and five phase groupings;
- expands every `for_each_artifact=unit-of-work` Stage to the exact approved
  instance count using deterministic `STAGE-unit-NNN` keys;
- gives every instance the exact approved Token/cost allocation;
- records `production_execution` as the Agora execution responsibility and
  the approved production runtime as the runtime;
- retains AWS lead/support/reviewer profiles only inside the sealed activation
  definition, where they remain source profiles rather than routing
  identities.

No Control Plane Stage or Gate is activated, no formal or compatibility Run is
created, and `resume` does not activate the first route while the successor
metadata keeps route activation false. The legacy budget-amendment path also
fails closed for this inert successor until a reviewed executable routing
contract is activated; it cannot reinterpret the activation-definition
payload as a legacy methodology definition.

## Authority and deferred work

This writer has authority only to authenticate and persist the migration Gate
and create the sealed successor Task/Plan/inventory transaction. It does not
grant the activation definition authority of its own, mutate the predecessor,
dispatch a provider, create completion Evidence, substitute a runtime, or
claim that an AWS AI-DLC Stage completed.

The separately reviewed materializer in
`aws-aidlc-methodology-execution-contract-v1.md` now seals the executable
per-Stage Context/Handoff/Evidence/Gate templates against this exact
successor inventory without changing it or activating a route. The next
backend slice must explicitly authorize and activate the first route. HTTP,
UI, automatic provider substitution, and native AI-DLC file installation
remain deferred.
