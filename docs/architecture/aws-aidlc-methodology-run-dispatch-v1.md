# AWS AI-DLC methodology first-Run dispatch v1

Status: reviewed implementation candidate

## Purpose and entry point

The first AWS AI-DLC formal Run is claimed separately from native execution.
The one-shot process attachment is:

```powershell
agora task migration-run-dispatch SUCCESSOR_TASK_ID `
  --allow-unbounded-native-usage
```

The acknowledgement is checked before service or storage initialization.
It acknowledges only that the Task and Run Token reservations are not a
provider-side hard cap. It does not relax the repository, runtime, preflight,
Handoff, Evidence, Gate, settlement, or reviewer boundaries.

The command reuses the exact `protocol_runs.run_id` and sealed
`ContextPack@1.0` created by `migration-run-claim`. It creates neither a second
formal Run nor a compatibility `orchestration_runs` row.

## Fresh observation and single-use attachment

Before any dispatch write transaction, Agora:

1. reloads the claimed methodology ledger as one read snapshot;
2. resolves the exact clean repository/ref/commit;
3. renders the bounded formal protocol prompt from the existing Context Pack
   and configured first Gate;
4. derives one hash-sealed
   `MethodologyRunDispatchPolicyDecision@1.0`;
5. collects one fresh `NativeRuntimeCapabilityObservation@1.0`; and
6. derives one `PinnedRuntimePreflightDecision@1.0`.

The per-Run dispatch-policy decision authenticates the exact Run claim,
Context Pack, running Stage and pending Gate, immutable route-activation
receipt, repository revision, complete runtime registry, command pins, and
usage reservation. It can only allow or block the already selected production
route: it has no route-selection, runtime-substitution, or provider
serviceability authority. Its identity and content hash supply the preflight's
routing-decision binding; the reviewed native capability declaration remains
the declaration binding.

A blocked dispatch policy, observation, or preflight leaves the database
unchanged. An allowed preflight enters one `BEGIN IMMEDIATE` transaction that
revalidates the Task, Control Task, Plan, inventory, execution contract, route
activation, Run claim, formal Run/Context, running first Stage, pending first
Gate, exact Gate requirements, repository binding, all three runtime command
pins, the exact dispatch-policy decision, and absence of any compatibility Run
or earlier dispatch. It then persists exactly one
hash-sealed `MethodologyRunDispatchClaim@1.0`.

The dispatch claim is the single-use spawn attachment. Concurrent callers
serialize to one owner; all others fail before process creation.

## Immediate pre-spawn checks

After the dispatch claim and immediately before process creation, Agora
re-resolves the clean repository and rechecks:

- preflight age and complete observation equality;
- current registry and pinned command-template hashes;
- reviewed capability-declaration hashes;
- current resolved no-shell launcher prefix; and
- the exact resolved spawn-command prefix.

The default Runner repeats the callback after its own command resolution,
immediately before `asyncio.create_subprocess_exec`. A custom Runner remains
covered by the service-boundary check. Any changed binding becomes a
process-not-started formal failure with exact-zero Token and cost usage.

After process creation, PID attachment changes only the dispatch record. The
original methodology Run claim remains immutable with `process_started=false`;
the dispatch attachment is the later process authority and observation.

## Terminal observation and settlement

Agora keeps process, transport, schema, and semantic result separate:

- the Runner returns terminal process/transport facts and bounded output;
- the Agent adapter accepts only an exact `HandoffPack@1.0` or its one allowed
  whole-document format repair;
- repository drift invalidates an otherwise valid Handoff;
- the Control Plane alone registers Artifacts/Evidence, evaluates the Gate,
  settles the Run and Stage, and derives the next inventory route; and
- a separate methodology usage ledger records the Run-bound provider
  observation without fabricating a compatibility Run.

Terminal runner facts are persisted before Control Plane settlement.
`MethodologyRunDispatchReceipt@1.0` is persisted only after both the formal
settlement and usage ledger succeed. It binds the dispatch claim, process
facts, output/error hashes, repository result, usage observation, protocol
state, Handoff identity, Gate/Stage outcome, registered Artifact/Evidence
identities, and derived next Stage.

Unified Task projection schema `12.0` exposes the methodology dispatch's
policy-bound preflight, process/protocol result, settled usage, and terminal
timing. The active claim reservation disappears only when the usage ledger
and immutable dispatch receipt finalize, including after a replayed formal
settlement.

## Recovery and replay

Recovery never starts a second process:

- `claimed` without a PID settles as process-not-started;
- `running` with a live or unknown PID blocks duplicate dispatch;
- `running` with a dead PID settles as interrupted;
- `terminal_observed` replays the same idempotent Control Plane settlement and
  completes the usage/receipt transaction; and
- `settled` returns the immutable receipt.

This covers crashes between dispatch claim, PID attachment, terminal
observation, Control Plane settlement, and final usage projection.

## Succeeding Gate boundary

The reviewed succeeding increment is defined in
`aws-aidlc-methodology-next-stage-gate-v1.md`. A successful first settlement
activates the next inventory Stage, but Stage readiness is not a substitute
for its formal Gate. The authenticated next-Stage operation configures only
that exact contract-bound Gate and still creates no Run or process. The
subsequent formal claim is defined in
`aws-aidlc-methodology-stage-run-claim-v1.md`; it creates the exact sequence-2
through sequence-6 Run and Context Pack without dispatching a process. The
sequence-2/3/4/5/6 one-shot process and settlement boundary is defined in
`aws-aidlc-methodology-stage-run-dispatch-v1.md`.

Continuation beyond sequence 6, automatic cross-Stage rework, dynamic provider
substitution, authenticated HTTP, Task Workbench UI, and native AWS AI-DLC file
installation remain separate reviewed increments.
