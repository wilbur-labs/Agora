# Pinned runtime preflight v1

Status: reviewed implementation baseline.

This increment adds one fresh, hash-sealed allow/block decision immediately
before launching the native runtime already selected by the authoritative
Stage route. It cannot choose another runtime or model, alter the sealed Stage
inventory, or claim provider authentication or serviceability.

## Versioned decision

`PinnedRuntimePreflightDecision@1.0` binds:

- the exact Task, project, Run, inventory, Stage, role, and pinned runtime;
- the per-Run routing-policy decision and reviewed policy declaration hashes;
- one complete `NativeRuntimeCapabilityObservation@1.0`;
- the configured runtime-registry and pinned command-template hashes;
- the audited resolved launcher argv-prefix hash (not executable-file content);
- the observed installation and informational version result;
- declared models and capabilities with an explicit
  `provider_serviceability_verified: false` marker; and
- six deterministic checks, bounded blockers, and rationale.

The decision carries `route_selection_authority: false` and
`runtime_substitution_allowed: false`. A blocked decision never falls back to
another adapter, model, reviewer, or methodology Stage.

## Allow and block semantics

Agora allows the already pinned launch only when all six checks pass:

1. the sealed route and per-Run routing-policy decision match;
2. the native observation is no more than 60 seconds old;
3. the observation contains the pinned adapter and reviewed declaration
   provenance;
4. the configured registry, command template, and resolved no-shell launch
   target match the observation;
5. the pinned runtime is locally installed and inspectable; and
6. the observation's capability declarations match the reviewed routing-policy
   declaration.

An exact native version is recorded when available but is not required because
the current reviewed policy declares no version constraint. Declared models
and capabilities do not prove authentication, provider availability, quota, or
model serviceability.

## Transaction and spawn boundary

Collection occurs after the read-only route and routing-policy preview and
outside every SQLite write transaction. A blocked decision therefore creates
no Run, reservation, Gate mutation, or process.

An allowed decision is included in the sealed Context Pack and persisted in an
additive nullable `orchestration_runs.runtime_preflight_payload` column in the
same operational Run-claim transaction as its routing policy and reservation.
Historical Runs remain `NULL`.

After formal Control Plane Run start and immediately before
`asyncio.create_subprocess_exec`, the Runner rechecks the decision expiry,
observation hash, current registry and command-template hashes, reviewed policy
hashes, current resolved launch target, and the exact resolved spawn-command
prefix. A changed or expired binding fails before process creation and settles
the already claimed formal Run as a process-not-started failure with exact-zero
usage.

The launch-target seal covers the resolved no-shell executable or wrapper argv
prefix. It does not hash or pin the executable image at that path; native state
remains an assertion until the immediate launch check.

Unified Task projection schema `9.0` exposes the persisted decision on each
formal operational Run. Reads verify its content hash and Run/routing-policy
bindings but do not re-run probes or mutate state.

The AWS AI-DLC successor claims its formal Run before collecting preflight.
That path therefore persists the same decision in a separate single-use
methodology dispatch attachment instead of modifying the already sealed
Context Pack. A hash-sealed
`MethodologyRunDispatchPolicyDecision@1.0` revalidates the exact Run, Context,
route-activation receipt, repository, runtime pins, and reservation without
selecting or substituting a route. That per-Run decision supplies the
routing-decision identity/hash binding, while the same reviewed capability
declaration supplies the declaration binding. Unified projection schema
`12.0` exposes that attachment without creating a compatibility Run.

## Task-scoped read-only preview

`agora task preflight TASK_ID` evaluates the already initialized formal route
without calling inventory initialization, Stage activation, `resume`, Run
claim, observation persistence, or runtime spawn. It first revalidates the
existing sealed route and read-only routing-policy preview, then collects one
fresh native capability observation and calls the same
`derive_pinned_runtime_preflight` function used by dispatch.

The versioned `RuntimePreflightPreview@1.0` operational read model contains the
exact sealed `PinnedRuntimePreflightDecision@1.0` plus explicit
`preview_only=true`, `run_claimed=false`, `observation_persisted=false`, and
`runtime_spawned=false` markers. Its synthetic `preview_*` Run identity is
never written or supplied to the claim path. An allowed preview remains
informational: real dispatch creates a new Run identity, derives a new policy
and observation, and performs the reviewed claim and immediate pre-spawn
recheck.

Blocked previews return bounded remediation only for the already pinned route,
runtime installation, command binding, and reviewed capability declaration.
They never recommend or perform runtime/model substitution. An allowed preview
still states that provider authentication and serviceability are unverified.

## Deferred boundaries

Live provider/model catalogs, authentication and serviceability probes, version
range policy, dynamic runtime/model substitution, capability-driven route
selection, authenticated HTTP, the missing authoritative AI-DLC graph,
parallel/DAG routing, and Task Workbench UI remain separate reviewed
increments.
