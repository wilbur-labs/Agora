# Agora Protocol & Domain Freeze v1

Status: implementation baseline
Consensus date: 2026-07-16
Protocol major version: 1

## 1. Product boundary

Agora is a local-first delivery control plane for Git projects and multiple AI
CLI runtimes. Its only product mainline is:

```text
Project -> Task -> Stage -> Run
        -> Artifact/Evidence
        -> Gate
        -> Handoff/Done
```

Codex, Claude Code, and Kiro CLI retain their native sessions, rules, skills,
memory, and methodology. They submit events, artifacts, evidence, native-state
assertions, and completion proposals. Only Agora changes cross-runtime Task,
Stage, and Gate state.

This freeze implements the protocol foundation. It does not yet migrate the
0.5 database, replace the existing UI, or claim that the 1.0 control plane is
complete.

## 2. Normative artifacts

The checked-in schemas under `docs/architecture/schemas/` are generated from
the Pydantic models in `backend/agora/protocol/`:

- `context-pack.schema.json`
- `handoff-pack.schema.json`
- `native-state-snapshot.schema.json`
- `artifact.schema.json`
- `evidence.schema.json`
- `approval.schema.json`
- `run-protocol-state.schema.json`
- `gate-requirement.schema.json`
- `runner-isolation-contract.schema.json`
- `authenticated-methodology-migration-gate.schema.json`
- `methodology-activation-definition.schema.json`
- `methodology-completion-review-claim-receipt.schema.json`
- `methodology-completion-review-claim-request.schema.json`
- `methodology-execution-contract.schema.json`
- `methodology-migration-activation-receipt.schema.json`
- `methodology-migration-preview-request.schema.json`
- `methodology-migration-preview-decision.schema.json`
- `methodology-route-activation-receipt.schema.json`
- `methodology-route-activation-request.schema.json`
- `methodology-run-claim-receipt.schema.json`
- `methodology-run-claim-request.schema.json`
- `methodology-run-dispatch-claim.schema.json`
- `methodology-run-dispatch-policy-decision.schema.json`
- `methodology-run-dispatch-receipt.schema.json`
- `methodology-stage-gate-receipt.schema.json`
- `methodology-stage-gate-request.schema.json`
- `methodology-stage-run-claim-receipt.schema.json`
- `methodology-stage-run-claim-request.schema.json`
- `methodology-stage-run-dispatch-claim.schema.json`
- `methodology-stage-run-dispatch-policy-decision.schema.json`
- `methodology-stage-run-dispatch-receipt.schema.json`
- `methodology-source-graph.schema.json`
- `stage-inventory.schema.json`
- `provider-usage-observation.schema.json`
- `native-runtime-capability-observation.schema.json`
- `pinned-runtime-preflight-decision.schema.json`
- `consultation-candidate-draft.schema.json`
- `consultation-candidate.schema.json`
- `consultation-candidate-disposition.schema.json`

`scripts/export_protocol_schemas.py --check` fails when a checked-in schema
does not match its executable model.

Protocol objects reject unknown fields. Version `1.x` permits additive optional
minor-version fields only. An unsupported major version blocks the Stage;
adapters must not guess unknown required fields.

## 3. Stable identities and hashes

Protocol IDs are stable, bounded identifiers. SHA-256 values are lowercase
hexadecimal strings.

Canonical hashes use UTF-8 JSON with keys sorted by Unicode code-point order
and compact separators. Every consumer must use that exact ordering. The
`content_sha256` field is excluded from its own calculation.

Artifact identity is versioned and traceable to either:

- managed UTF-8 content held by Agora; or
- a referenced repository/ref/commit/path with a content hash.

Native snapshot identity is:

```text
project_id
+ repository_id
+ canonical_ref
+ commit_sha
+ native_state_sha256
+ reconciliation_rule_version
+ methodology
```

Snapshot generation contains no wall-clock field. Identical normalized inputs
must produce byte-identical output.

Context Packs are different: `generated_at` is part of the sealed pack content,
so two pack instances generated at different times intentionally have different
content hashes.

## 4. Domain state machines

### Task

```text
backlog | ready | active | blocked | needs_review |
completed | failed | cancelled
```

Completed work may reopen to `active` only through an explicit invalidation or
reconciliation event. Runtime adapters cannot invoke Task transitions directly.
Persisted Task lifecycle is derived from the complete hash-sealed Stage
inventory plus authoritative Stage, Gate, Attention, invalidation, and
reconciliation state. Passing every Stage Gate enters `needs_review`; explicit
human approval completes the Task.

### Stage

```text
pending | ready | running | blocked | needs_review |
reconciliation_required | completed | failed | cancelled
```

A completed Stage may reopen to `ready` when an approval or required artifact
becomes stale.

The user-confirmed AWS AI-DLC source is frozen separately as
`MethodologySourceGraph@1.0`. It pins release `v2.3.0`, commit and source-file
hashes, 32 source Stages, 9 scope profiles, and the source dependency DAG. It
has `routing_authority=false` and `dispatch_authority=false`: source authority
does not itself create an executable Agora methodology or alter any Task.
Upstream rework semantics remain prose-only in this release, so Agora records
`structured_rework_edges=false` rather than inventing transitions or limits.
`MethodologyActivationDefinition@1.0` separately materializes the 32 Stage
Contracts, input/output Artifact graph, source sensors and reviewer bounds,
Gate requirements, pairwise-distinct Codex/Claude/Kiro responsibilities,
budget/quality policy, bounded escalation, approval bindings, and migration
policy. It remains definition-only with `routing_authority=false`,
`dispatch_authority=false`, and `migration_authority=false`. Existing Tasks
remain pinned. A sealed, read-only Task-scoped successor migration preview now
evaluates exact Task/Plan/inventory versions, repository, source/activation
hashes, scope seeds, runtime pins, explicit Stage/runtime budgets, a human
assertion, and quiescence. Even `eligible=true` has no migration authority.
The separate migration writer authenticates the asserted approver through a
configured Control Plane credential, persists an
`AuthenticatedMethodologyMigrationGate@1.0`, rechecks every preview binding
inside one write transaction, and atomically creates a distinct successor
Task, activation-hash-bound Plan, and sealed grouped inventory. The predecessor
is not mutated. The successor Plan remains non-dispatching and its first route
is not activated. A separate authenticated materializer now seals one
`MethodologyExecutionContract@1.0` against the unchanged inventory hash. It
expands each source-bound Stage instance into Context/Handoff templates,
deterministic input routing, Run reservations, and repository-scoped
Evidence/Gate requirements. Production Handoffs may contain only production
Evidence; the final Stage Gate separately requires Claude correctness and Kiro
methodology completion Evidence before human Task approval. The immutable
contract retains routing and dispatch authority as false. A later authenticated
`MethodologyRouteActivationRequest@1.0` now atomically rechecks the live
contract/repository/runtime bindings, registers only first-Stage external seed
Artifact references, configures the exact first Stage/Gate, and records
`MethodologyRouteActivationReceipt@1.0`. It activates no later Stage, creates no
Run or protocol Artifact/Evidence, and retains dispatch authority as false.

For a Task with a sealed grouped inventory, Agora routes the first incomplete
Stage in inventory order and only that route may start a formal Run. Successful
formal settlement activates the next route atomically; compatibility Plan state
does not select the Stage or runtime.

Before dispatching that pinned route, Agora records a hash-sealed routing-policy
decision that verifies the Stage/runtime capability binding, Task-risk reviewer
coverage, reviewer independence, and protected budget for every unfinished
required reviewer Stage. The policy is re-derived in the Run-claim transaction;
it cannot substitute a runtime or alter the sealed methodology graph. Budget
pressure must block before process spawn rather than remove a required review.

When that protected-budget check is the only policy blocker, a versioned Task
budget amendment may increase the total Task/Plan envelope without changing
Stage allocations, reviewer requirements, or historical usage. The amendment
records sealed policy snapshots before and after the increase and commits only
when the resulting policy passes. Every subsequent Run claim still derives a
new per-Run policy inside its own transaction; the amendment receipt is audit
evidence, not dispatch authority.

Native provider usage and native runtime capability observations are separate
read-only, hash-sealed contracts. Usage observations bind measured Run results
without rewriting historical ledger entries. Capability observations bind
local installation/version probes and declared model/capability provenance but
carry `routing_authority: false`; they cannot select a runtime/model or alter
the sealed route.

Task, Stage, and Run Token envelopes are admission-control reservations, not a
provider-side hard execution cap unless the pinned adapter exposes and records
an enforceable native limit. Terminal provider usage remains authoritative for
settlement and may exceed a reservation; Agora must record that overrun
truthfully and block later dispatch when protected review capacity is no longer
available. Interactive account summaries such as native `/usage` output may
corroborate account-level availability, but they do not replace a Run-bound
usage observation.

The unified CLI must not present invocation of a provider-backed Run as if the
Task envelope were a hard provider limit. Until the pinned adapter exposes and
records such a limit, every dispatching CLI command requires an explicit
unbounded-native-usage acknowledgement and fails before Task or Run mutation
when it is absent. The acknowledgement permits dispatch only; it does not
weaken routing policy, protected review capacity, settlement, or Gates.
Compatibility and formal `next`/`run` commands both dispatch native adapters
and are covered. A state-only retry does not dispatch; its subsequent
`next`/`run` remains subject to the acknowledgement.

Before native process creation, a fresh hash-sealed pinned-runtime preflight may
only allow or block that already sealed route. It binds the exact capability
observation, command template and resolved launch target, and reviewed routing
policy hashes. Collection occurs outside SQLite write transactions; an
immediate Runner recheck rejects expiry or changed launch bindings before
spawn. The preflight cannot substitute a runtime/model or treat declarations
as provider serviceability.

The resolved launch binding hashes the no-shell launcher argv prefix, not the
contents of the executable image at that path.

A Task-scoped read-only preview may return the exact same sealed preflight
decision for an already initialized route, but it carries explicit no-claim,
no-persistence, and no-spawn markers. The preview cannot initialize or repair
Task/Stage state, and an allowed result is not dispatch authority; a real Run
must derive a fresh policy, observation, and decision.

### Gate

```text
pending | evaluating | passed | blocked | stale
```

`passed -> stale` is mandatory when a bound approval or artifact changes.

### Run protocol dimensions

Every Run records four independent dimensions:

```text
process_status
transport_status
schema_status
semantic_stage_result
```

Semantic success requires an exited process, completed transport, and a valid
or repaired schema. Exit code zero alone is never sufficient.

Schema repair is limited to one format-only attempt. A second invalid response
is `protocol_failed`, blocks the Stage, and creates Attention.

An advisory consultation is not a formal Stage Run and cannot change Stage or
Gate state. It uses the already authoritative Stage route and pinned runtime,
records a separate consultation execution and truthful provider-usage
observation, and accepts only `ConsultationCandidateDraft@1.0`. Agora binds
the Project, Task, Plan version, inventory, Stage, runtime, and repository
revision after strict parsing. The resulting hash-sealed candidate remains
non-authoritative until explicit human adoption. One whole-document Markdown
fence removal is the only consultation format repair; surrounding prose,
wrong decision keys, stale route/Plan bindings, sensitive source references,
and repository drift fail closed without creating a candidate.

## 5. Context and handoff contracts

A Context Pack is immutable input for one Run. It includes:

- task and stage identity;
- Stage Contract and required outputs;
- applicable Policies;
- verified M2 Task Memory;
- approved/pinned M3 Project Knowledge;
- minimal user preferences;
- versioned input Artifact references;
- forbidden constraints and budget.

A native dispatch may carry bounded projections of sealed policy and preflight
decisions when their entries retain the authoritative object hashes. Every
prior Artifact remains an exact version/hash reference. Managed Artifact
content may additionally be considered newest-first and materialized only
while the complete Windows argv prompt remains within its frozen bound. Each
candidate is considered independently, so a non-fitting item stays explicitly
reference-only while a smaller older item may still fit; content is never
truncated, summarized, or silently dropped.

An externally produced Task seed may enter a first Context Pack only through a
separate hash-bound registration that preserves repository/ref/commit/path and
consumer Stage. Because no Agora producer Run exists for that seed, registration
must not fabricate a protocol Artifact. The formal Run claim must revalidate
the exact registration in the same transaction that seals the Context Pack and
advances the authoritative Stage; generic Run start continues to require
registered protocol Artifacts unless its caller supplies those already
transaction-validated external bindings explicitly.

A Handoff Pack is immutable output from one Run. It includes:

- semantic Stage result;
- output Artifact versions;
- Evidence;
- unresolved questions;
- optional NativeStateSnapshot;
- M2 candidates;
- blocker requirement IDs;
- an Agent-suggested next action.

The authoritative `next_safe_action` is derived by the Gate evaluator, not
copied blindly from the Agent suggestion.

Dispatch prompts may include a bounded exact-key guide for frozen Handoff,
Artifact, Evidence, Artifact-version, and producer shapes. The guide is
instructional only and may require raw JSON output plus exact frozen enum
values. It cannot repair aliases, strip surrounding prose, fill missing
required fields, invent Evidence, or alter the one format-only repair limit.
When the Context does not supply a complete frozen native snapshot, the guide
may require its schema-valid null form rather than allowing an Agent to invent
native state. Memory candidates remain Agent suggestions but must use exact
frozen MemoryCandidate objects rather than strings or ad hoc shapes; their
non-empty source references are StableIds, not arbitrary file paths.
Nested producer fields remain ProducerRef objects and Evidence details remains
structured JSON; the guide cannot flatten either field or repair an Agent
result that does.

A formal runtime invocation must isolate the Handoff transport from unbound
native customizations that can replace or post-process stdout. Isolation is a
process-launch boundary, not permission to modify native runtime files, and it
does not weaken the Handoff parser or make native runtime state authoritative.
When a native CLI emits a chat transcript, its versioned transport normalizer
may select only an explicitly marked final assistant turn. It may not search
tool arguments or intermediate transcript content for a convenient JSON
object. A runtime granted a bounded execution tool remains prohibited from
mutating the repository, and repository/ref/commit cleanliness is rechecked
before settlement.

## 6. Evidence and Gate evaluation

Evidence statuses are:

```text
passed | failed_product | failed_external | missing | stale
```

Gate requirements have a stable `requirement_id`, severity, priority, and
failure action, plus repository/ref/commit scope. Evidence carries the same
scope. Lower numeric priority is more urgent.

Evaluation is deterministic and fail-closed:

1. no current Evidence becomes `missing`;
2. one `passed` status satisfies the requirement;
3. conflicting current statuses fail closed;
4. blocker requirements prevent Gate passage;
5. warning requirements are reported but do not block;
6. `next_safe_action` comes from the highest-priority blocker, with
   `requirement_id` as the deterministic tie-breaker.

The evaluator ignores Evidence from another repository, ref, or commit. Callers
should still provide only the active Evidence set for the evaluated Artifact
versions. Historical Evidence remains in the ledger but is not mixed into the
current Gate input.

## 7. Approval invalidation

Approval binds:

```text
repository + ref + commit + stage + artifact path + artifact hash
```

When a bound requirements, design, contract, or dependent Artifact hash
changes, its bound commit changes, or the Artifact disappears from the complete
current inventory:

1. matching Approval becomes `stale`;
2. the approved Stage and all configured downstream Stages reopen;
3. a deterministic impact-analysis Attention is required;
4. the prior Gate becomes `stale`;
5. retries and branch switches cannot reactivate the old Approval.

Approvals from another ref are not portable and cannot satisfy the active ref.

## 8. Native reconciliation

Native state is a declaration, not a completion fact. Reconciliation is a
deterministic, read-only, idempotent function over:

```text
declared native state
+ audit and version-bound approval
+ required Artifact set and hashes
+ Git ref, commit, and lineage
```

Blocking conflict classes:

- `state_stale`
- `audit_stale`
- `internal_contradiction`
- `required_evidence_missing`
- `approval_missing_or_stale`

Default warning classes:

- `branch_divergence`
- `policy_reassessment_required`
- `location_stale`

Other refs may create divergence Attention but cannot overwrite the active ref
or contribute Approval.

## 9. M2 publication rules

```text
Run running
  -> M1 ledger + M2 candidate only

failed | cancelled | protocol_failed
  -> preserve latest verified M2; append attempt/blocker

succeeded + Gate blocked
  -> publish unverified M2 draft; preserve verified facts

Gate passed
  -> atomically publish a new verified M2 version and Handoff
```

A retry starts from the latest verified M2, current Artifact versions, and
latest blockers. It does not inherit the failed Run's complete transcript.

## 10. Windows Embedded Runner contract

The 1.0 contract is explicitly `platform: windows`. Each Run receives isolated
writable HOME, temp, cache, and config directories under its run root. The
workspace must be inside an explicit allowed workspace root. Credentials are
injected only by opaque references and never serialized into packs, logs, or
artifacts. POSIX and remote runner contracts remain a 1.1+ extension rather than
being silently accepted by the Windows validator.

Global initialization or credential-helper operations that cannot be isolated
must be listed and serialized. Cleanup failure writes a recovery marker and
creates Attention; it must not silently discard the workspace.

## 11. Acceptance fixtures

The regression fixtures under `backend/tests/fixtures/protocol/` preserve two
real findings:

- `workflow-polish`: process launch can succeed while external authentication
  Evidence fails, so the Gate remains blocked.
- `deal_analysis`: declared “Build and Test Complete” conflicts with stale
  state/audit, missing required artifacts, and missing final approval; verified
  state is `reconciliation_required`.

These fixtures are sanitized protocol inputs, not copies of external project
content.

## 12. Freeze exit criteria

The first implementation stage is complete only when:

- all checked-in schemas match executable models;
- state-machine, Gate, invalidation, M2, hash, and Runner isolation tests pass;
- workflow-polish and deal_analysis fixtures pass deterministically;
- the full relevant backend suite passes;
- independent Claude Code review approves the diff;
- progress is updated with the reviewed commit and next safe action.
