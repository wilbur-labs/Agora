# AWS AI-DLC methodology later-Stage Run dispatch v1

Status: reviewed implementation; sequence 2 only

## Purpose and entry point

After the authenticated sequence-2 claim has created one formal Run and sealed
Context Pack, the orchestrator may attach exactly one pinned native process and
settle that same Run:

```powershell
agora task migration-next-stage-run-dispatch SUCCESSOR_TASK_ID `
  --allow-unbounded-native-usage
```

The acknowledgement is mandatory because the Context Token/cost envelope is an
admission reservation, not a native provider hard cap. It grants no route
selection, runtime substitution, provider serviceability, or compatibility-Run
authority.

This boundary introduces `MethodologyStageRunDispatchPolicyDecision@1.0`,
`MethodologyStageRunDispatchClaim@1.0`, and
`MethodologyStageRunDispatchReceipt@1.0`. The first-Run dispatch contracts stay
frozen and unchanged; their `first_*` and route-activation bindings are not
silently reinterpreted for later Stages.

## Preflight and atomic claim

Before any durable dispatch claim, the service reads and checks:

- the immutable execution contract, sequence-2 Gate receipt, sequence-2 Run
  claim receipt, and settled sequence-1 dispatch receipt;
- the exact unsettled formal Run and sealed Context Pack;
- the current authoritative sequence-2 `RUNNING` Stage, `PENDING` Gate, and
  non-runnable route;
- the clean repository/ref/commit before and after native capability
  collection;
- the complete frozen runtime-registry hash (including result format, version
  probe, and declared models), every command pin, and the selected runtime
  installation; and
- the positive sequence-2 Token reservation.

The policy decision records six canonical checks. A fresh
`PinnedRuntimePreflightDecision@1.0` binds the native observation to that exact
policy, route, Run, Stage, runtime registry, and resolved launch command. Any
blocker occurs before the dispatch ledger is written.

One `BEGIN IMMEDIATE` transaction then rechecks the complete live snapshot and
persists a single-use dispatch claim. The claim reuses the existing Run and
Context identities, records only spawn authority, and creates no compatibility
Run. It seals a unique spawn-owner id, the selected result format, and a
five-minute recovery lease. The sequence-2 claim ledger remains immutable with
`process_started=false`.

## Process, protocol, and usage settlement

The default Runner repeats repository and resolved-command preflight immediately
before spawn. PID attachment is persisted before process execution continues.
Exit code, transport, Schema validity, semantic result, repository stability,
and provider usage remain separate facts:

- the Agent adapter accepts only the sealed Handoff contract, with at most one
  format-only repair;
- repository drift invalidates otherwise valid output;
- the Control Plane alone registers Artifact/Evidence, evaluates the Gate,
  settles the Run/Stage, and derives the next route; and
- the later-Stage usage ledger settles the active reservation without
  manufacturing a legacy Run.

Terminal runner facts commit before formal settlement. The immutable receipt is
written only after the Control Plane settlement and separate usage row both
succeed. It binds process facts, output/error hashes, repository result, usage,
protocol state, optional Handoff, Gate/Stage outcome, Artifact/Evidence ids, and
the next Stage. Usage normalization uses the claim's sealed result format, not
a mutable in-memory runtime registry.

Unified Task projection merges first-Run and later-Stage dispatches by formal
Run identity. The sequence-2 reservation becomes settled usage only when the
later usage ledger exists; unavailable native measurements consume the original
reservation rather than being recorded as zero. Projection and admission reads
cross-check the denormalized reservation and usage rows against their sealed
claim receipt, dispatch receipt, protocol Run, and usage observation; missing or
tampered authority fails closed. Claim enumeration is anchored through the
formal Run's Task and authoritative Plan, so a forged cross-Plan `plan_id`
cannot remove a reservation from admission or projection. Validation selects
the union of rows whose authoritative Plan, stored claim Plan, or stored usage
Plan matches the reader, so both the source and destination Plan fail closed.

## Recovery and replay

Recovery never launches a second process. A concurrent caller cannot interpret
a fresh `claimed` record as a crash: the spawn owner alone may attach the PID or
record its own pre-spawn failure while the five-minute lease is active. After
the lease expires, recovery and PID attachment compete through transactional
state checks, so only one can consume the claim.

- `claimed` with an active owner lease blocks recovery;

- `claimed` without a PID settles as launch failure with exact-zero usage;
- `running` with a live or unknown PID blocks duplicate dispatch;
- `running` with a dead PID settles as interrupted;
- `terminal_observed` replays the idempotent Control Plane settlement and
  finishes usage/receipt persistence; and
- `settled` returns the immutable receipt.

Concurrent callers may race through read-only preflight, but only one can claim
and spawn. A persisted claim, policy, preflight, terminal fact, or receipt that
does not match its sealed payload fails closed.

## Bounded authority and next boundary

This implementation dispatches sequence 2 (`workspace-detection`) only. It
does not generalize Gate predecessor storage for sequence 3, create automatic
rework, change native AWS AI-DLC files, expose HTTP, or add Task Workbench UI.

The next reviewed slice must make later-Stage predecessor identities generic
without weakening the already frozen sequence-1 and sequence-2 receipts. It may
then configure and claim sequence 3 from the settled sequence-2 Handoff and
selected Artifact versions. Cross-Stage rework remains a separate explicit
authority path.
