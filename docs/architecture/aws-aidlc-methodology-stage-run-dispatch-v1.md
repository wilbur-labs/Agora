# AWS AI-DLC methodology later-Stage Run dispatch v1

Status: reviewed implementation; sequences 2 through 6

## Purpose and entry point

After an authenticated sequence-2 through sequence-6 claim has created
one formal Run and sealed Context Pack, the orchestrator may attach exactly one
pinned native process and settle that same Run:

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

- the immutable execution contract, current Gate and Run-claim receipts, and
  settled immediately preceding dispatch receipt;
- the exact unsettled formal Run and sealed Context Pack;
- the current authoritative sequence-2/3/4/5/6 `RUNNING` Stage, `PENDING` Gate,
  and non-runnable route;
- the clean repository/ref/commit before and after native capability
  collection;
- the complete frozen runtime-registry hash (including result format, version
  probe, and declared models), every command pin, and the selected runtime
  installation; and
- the positive current-Stage Token reservation.

The policy decision records six canonical checks. A fresh
`PinnedRuntimePreflightDecision@1.0` binds the native observation to that exact
policy, route, Run, Stage, runtime registry, and resolved launch command. Any
blocker occurs before the dispatch ledger is written.

One `BEGIN IMMEDIATE` transaction then rechecks the complete live snapshot and
persists a single-use dispatch claim. The claim reuses the existing Run and
Context identities, records only spawn authority, and creates no compatibility
Run. It seals a unique spawn-owner id, the selected result format, and a
five-minute recovery lease. The formal Run-claim ledger remains immutable with
`process_started=false`.

The current Run is selected by the highest formal later-Stage claim and is
cross-checked against the Task's compatibility cursor and authoritative route.
Every later-Stage dispatch row retains the sequence-1 dispatch id as its
compatibility lineage root. For sequences 3 through 6, the process claim also
reuses and validates the foreign-keyed immediately preceding later-Stage
dispatch binding from the Gate/Run-claim chain. Authority validation walks
that chain toward sequence 1, so a tampered transitive predecessor cannot
authorize a new Gate, claim, process, budget read, or projection.

## Process, protocol, and usage settlement

The default Runner repeats repository and resolved-command preflight immediately
before spawn. PID attachment is persisted before process execution continues.
Exit code, transport, Schema validity, semantic result, repository stability,
and provider usage remain separate facts:

- the Agent adapter accepts only the sealed Handoff contract, with at most one
  format-only repair;
- every returned Artifact id/kind pair must belong to the Context Pack's full
  declared output set, including optional declarations; unbound outputs are
  rejected before registration, and the Control Plane repeats that check at
  settlement;
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
Run identity. Each sequence-2/3/4/5/6 reservation becomes settled usage only when
its later usage ledger exists; unavailable native measurements consume the
original reservation rather than being recorded as zero. Projection and admission reads
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

This implementation dispatches sequence 2 (`workspace-detection`), sequence 3
(`state-init`), and the exact selected sequence-4, sequence-5, and sequence-6
Stages.
Sequence 4 is the first bounded later-Stage position with non-empty required
outputs; successful
settlement registers every required Artifact and the formal Gate Evidence. The
sealed Context Pack uses a full-hash-bound Handoff/Gate projection so the exact
requirements and required outputs fit the unchanged Windows 24 KiB prompt
bound. Sequence 5 consumes only the three exact hash-bound prior Artifact
version references already sealed into its Context Pack and produces its two
contract outputs. It creates no automatic rework, changes no native AWS AI-DLC
files, exposes no HTTP, and adds no Task Workbench UI.

Sequence 6 reuses the sealed Context Pack's exact external `unit-of-work` seed
reference plus registered sequence-5 `requirements` version and produces its
two contract outputs. The native process receives references, never invented
seed content or a fabricated producer Run. Returning that seed reference as an
undeclared output is a protocol failure and registers neither the seed nor any
other Handoff Artifact or Evidence.

The next reviewed slice may extend the same chain to sequence 7, the second
selected code-generation unit, and prove repeated-unit isolation without
weakening the frozen sequence-1 through sequence-6 receipts. Cross-Stage rework
remains a separate explicit authority path.
