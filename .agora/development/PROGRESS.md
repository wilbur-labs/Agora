# Agora Control Plane Development Progress

## 2026-07-31 - AWS AI-DLC first Stage route activation (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyRouteActivationRequest@1.0` and
  `MethodologyRouteActivationReceipt@1.0` contracts plus checked-in JSON
  Schemas. The request binds the exact live Task, Control Task, Plan,
  inventory, execution-contract hash, repository revision, and first
  Stage/Gate and structurally cannot request runtime dispatch.
- [x] Added
  `agora task migration-route-activate SUCCESSOR_TASK_ID --request PATH
  --credential-env NAME`. The credential is authenticated before
  service/storage initialization; the principal must retain
  `control_plane.approve`, exact Project scope, and identity equality with the
  migration Gate and execution contract.
- [x] The writer uses one immediate transaction and revalidates the complete
  migration/contract/Task/Plan/inventory ledger, exact unconfigured first
  route, current repository before/after bounded Artifact hashing, runtime
  registry/command hashes, and absence of any Stage/Gate, Run, consultation,
  protocol Artifact, or Evidence state.
- [x] Configured only inventory position one: one pending formal Stage, its
  exact execution-contract Gate requirements, then the pending-to-ready route
  transition and frozen Task lifecycle reconciliation. Compatibility Plan and
  Stage rows remain unchanged and do not select the route.
- [x] Registered only the first Stage's external Task seed
  `ArtifactVersionRef` bindings in a separate provenance table. No fabricated
  producer `Artifact`, Run, or Evidence row is created.
- [x] Recorded explicit Task metadata/version and immutable receipt bindings
  while retaining `methodology_dispatch_authority=false`. Exact concurrent
  replay serializes to one receipt/Stage/Gate and rechecks current
  authorization; changed requests or drift fail closed.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-route-activation-v1.md`.
  Context Pack materialization, formal Run claim, runtime spawn, HTTP, UI,
  provider substitution, automatic cross-Stage rework, and native AI-DLC file
  installation remain deferred.

### Verification and review state

- [x] Focused migration/route activation and Control Plane routing
  regressions: 79 passed.
- [x] Related methodology, Stage routing, Task orchestration, and formal
  protocol regression suite: 206 passed.
- [x] Exact complete non-integration backend suite:
  641 passed, 18 deselected, with only the existing Starlette/httpx and Windows
  Proactor cleanup warnings.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`,
  migration-route-activate CLI help, and `git diff --check` passed.
- [x] Kiro CLI session `151a4305-8497-45e9-897c-2a7ffff77b21`
  independently reviewed AI-DLC methodology fidelity, authority boundaries,
  transaction/replay behavior, exact bindings, seed-reference provenance,
  schemas, and non-dispatch guarantees and returned `VERDICT: APPROVE` with no
  high/medium findings. Its LOW requests were resolved by explicitly
  preserving dispatch authority as false and adding a real two-thread
  concurrent replay regression; targeted final re-review returned
  `VERDICT: APPROVE`.
- [x] Independent Claude Code safe-mode review inspected the complete
  implementation, DDL, authentication, transaction/rollback, optimistic
  bindings, Control Plane lifecycle, schema/model invariants, refactoring
  regression risk, and tests and returned `VERDICT: APPROVE` with no
  high/medium findings. Its actionable LOW unrelated CLI indentation change
  was reverted; targeted final confirmation returned `VERDICT: APPROVE`.

### Current checkpoint and next safe action

Implementation, automated verification, actionable-finding resolution, and
both review gates are complete on baseline `75c0c88`. This bounded first-route
activation is ready for its reviewed commit/push. The next backend slice is a
contract-hash-bound transaction that materializes and claims the first formal
Run and exact Context Pack from the activated Stage template and registered
seed references, while keeping provider process spawn separate.

## 2026-07-31 - AWS AI-DLC successor execution contract (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyExecutionContract@1.0` and its checked-in
  JSON Schema. It binds the authenticated migration request/Gate/receipt,
  successor Task/Control Task/Plan versions, unchanged grouped-inventory hash,
  repository revision, activation/source hashes, runtime pins, and selected
  scope.
- [x] Added
  `agora task migration-contract SUCCESSOR_TASK_ID --credential-env NAME`.
  The credential is authenticated before service/storage initialization; the
  principal must retain `control_plane.approve`, Project scope, and exact
  identity equality with the persisted migration Gate.
- [x] The writer uses one immediate transaction, revalidates the complete
  successor/migration ledger, requires every compatibility Stage pending and
  no Control Stage/Gate, orchestration Run, consultation, or protocol Run, and
  rechecks Git before/after bounded migration Artifact hashing plus current
  Codex/Claude/Kiro command hashes.
- [x] Materialized each selected inventory Stage instance into source-bound
  Context/Handoff/Gate templates with exact Run reservations, source role
  profiles, sensors, required/optional outputs, and deterministic
  `single`/`matching_unit`/`all_units` dependency routing.
- [x] Required inputs outside the selected scope retain exact hash-bound Task
  seed `ArtifactVersionRef` values; only optional absent inputs can remain
  unbound. Required seed drift or dependency misrouting fails closed.
- [x] Production Handoffs contain only Evidence attributable to the pinned
  Codex Run. The final Stage Gate separately requires formal independent
  correctness Evidence from Claude and methodology stewardship Evidence from
  Kiro before existing human Task completion approval.
- [x] The contract is stored separately and does not rewrite the
  migration-receipt-bound inventory. Insert/event failure rolls back, exact
  replay is immutable but rechecks current authorization, and no Task/Plan/
  inventory, route, Stage/Gate, Run, Artifact/Evidence, Approval, charge,
  runtime, HTTP, or UI state is created.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-execution-contract-v1.md`.
  First-route authorization/activation remains a separate reviewed increment.

### Verification and review state

- [x] Migration and successor execution-contract regressions: 54 passed.
- [x] Related methodology/protocol/Control Plane regression suite:
  268 passed.
- [x] Exact complete non-integration backend suite:
  626 passed, 18 deselected, with only the existing Starlette/httpx and Windows
  Proactor cleanup warnings.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`,
  migration-contract CLI help, and `git diff --check` passed.
- [x] Kiro CLI session `9c580e57-c3c3-4d58-85f9-aa9df7f69149`
  independently reviewed source fidelity, instance/input closure,
  Handoff/Gate producer separation, transaction/lifecycle behavior, schemas,
  and non-authority flags. Its initial process emitted no visible verdict, so
  the same session was resumed; it returned `VERDICT: APPROVE` with no
  actionable findings. Targeted final re-review also returned
  `VERDICT: APPROVE` after the defensive fixes.
- [x] Independent Claude Code safe-mode review inspected the complete final
  implementation, DDL/schema, docs, authentication, transaction, dependency
  routing, and adversarial tests and returned `VERDICT: APPROVE` with no
  high/medium findings. Its three LOW items were fixed by rejecting unused
  seeds, validating producer Stage references in the sealed model, and adding
  direct coverage for single/optional inputs plus missing/extra seed,
  allocation/order, dangling-producer, and unit-count drift. Targeted re-review
  confirmed all LOW items resolved and returned `VERDICT: APPROVE`.

### Current checkpoint and next safe action

Implementation, automated verification, actionable-finding resolution, and
both review gates are complete on baseline `472cc38`. This bounded contract
materializer is ready for its reviewed commit/push. The next backend slice is
an authenticated, contract-hash-bound transaction that configures and
activates only the exact first Stage route without dispatching a provider.

## 2026-07-30 - AWS AI-DLC transactional successor activation (reviewed)

### Bounded implementation

- [x] Added hash-sealed `AuthenticatedMethodologyMigrationGate@1.0` and
  `MethodologyMigrationActivationReceipt@1.0` contracts plus checked-in JSON
  Schemas. The receipt embeds the fresh atomic recheck and authenticated Gate;
  no credential or credential-environment name is serialized.
- [x] Added
  `agora task migration-activate TASK_ID --request PATH --credential-env NAME`.
  It resolves one configured Control Plane principal before storage
  initialization, requires `control_plane.approve` for the exact Project, and
  requires the authenticated principal to equal the Gate assertion's
  `approved_by`.
- [x] The writer opens one immediate transaction, rereads the exact
  Task/Control Task/Plan/current-methodology/inventory/quiescence facts,
  re-resolves Git before and after bounded Artifact hashing, and reruns every
  preview constraint against the current source/activation definitions,
  runtime registry, budgets, seeds, proposal, and human assertion before any
  insert.
- [x] A successful transaction persists the authenticated migration Gate,
  request, fresh recheck decision, and receipt; creates one distinct successor
  Task; pins the complete activation-definition payload/hash in a new Plan; and
  seals the selected-scope grouped inventory with exact per-instance budgets.
  The predecessor Task manifest, events, Plan, inventory, lifecycle, and Run
  ledgers remain unchanged.
- [x] Preserved source and authority boundaries. Selected Stages remain in
  source order and source phase groups; the five unit-of-work Stages expand to
  deterministic `STAGE-unit-NNN` instances. AWS lead/support/reviewer
  identities remain profiles in the sealed definition; inventory execution
  responsibility is the exact approved production runtime pin.
- [x] The successor Plan is fixed at `ready_for_implementation`,
  `methodology_route_activated=false`, and
  `methodology_dispatch_authority=false`. No Control Plane Stage/Gate, Run,
  route activation, runtime spawn, HTTP, or UI surface is created. `resume`
  preserves that inert boundary. Legacy budget amendment also fails closed
  rather than interpreting the activation payload as a legacy methodology.
- [x] Exact retries return the original sealed receipt without duplicate
  state. Request/Gate identity drift, a second successor for the same source
  Task, stale bindings, repository drift, authorization failure, or any
  mid-transaction exception fails closed and rolls back every successor row.
- [x] Refactored migration snapshots to read directly from the caller-owned
  transaction, removing the redundant nested Control Plane schema
  initialization noted as LOW in the prior Claude review.
- [x] An authorized but unregistered Project now fails before opening the
  writer transaction with a typed domain conflict and a regression proving
  the database remains byte-for-byte logically unchanged.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-migration-activation-v1.md`; native
  execution-contract materialization, first-route activation, provider
  dispatch, HTTP, UI, and native AI-DLC installation remain deferred.

### Verification and review state

- [x] Migration preview/activation regressions: 34 passed.
- [x] Migration activation/preview, source/definition, Control Plane auth and
  inventory, Task orchestration, formal protocol orchestration, and protocol
  freeze: 236 passed.
- [x] Exact complete non-integration backend suite:
  606 passed, 18 deselected, with only the existing Starlette/httpx and Windows
  Proactor cleanup warnings.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`,
  migration-activation CLI help, and `git diff --check` passed.
- [x] Kiro CLI session `50bfd9b3-63c9-4991-b852-34dfe690681a`
  independently reviewed the full methodology/protocol/lifecycle boundary and
  returned `VERDICT: APPROVE` with no high/medium findings. Its one LOW
  reachable legacy-payload reader was fixed by making budget amendment
  explicitly fail closed; targeted re-review also confirmed the final
  unregistered-Project guard, with no actionable findings remaining.
- [x] Independent Claude Code safe-mode review inspected the complete
  implementation, transaction/auth ordering, schemas, docs, and adversarial
  regressions and returned `VERDICT: APPROVE` with no high/medium findings.
  Its actionable LOW unregistered-Project error path was fixed and targeted
  re-review returned `VERDICT: APPROVE`; its SQLite external-I/O lock-duration
  and global semver-pattern notes were informational and non-blocking.

### Current checkpoint and next safe action

Implementation, automated verification, actionable-finding resolution, and
both review gates are complete on baseline `63cb70e`. This bounded writer is
ready for its reviewed commit/push. The next backend slice is a source-bound
executable per-Stage Context/Handoff/Evidence/Gate contract for the sealed
successor, followed by separately reviewed first-route activation.

## 2026-07-29 - AWS AI-DLC successor migration preview (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyMigrationPreviewRequest@1.0`,
  `MethodologyMigrationGateAssertion@1.0`, and
  `MethodologyMigrationPreviewDecision@1.0` contracts plus checked-in JSON
  Schemas. The only accepted strategy is `successor_task`; no in-place
  methodology mutation is representable.
- [x] Added `agora task migration-preview TASK_ID --request PATH`. It reads one
  rollback-only Task/Control-Task/Plan/inventory/quiescence snapshot, observes
  bounded repository Artifacts, and prints a deterministic ordered eligibility
  decision. Eligible returns 0 and blocked returns 2; neither result writes
  state or grants authority.
- [x] The preview validates exact Task, authoritative Control Task, Plan,
  current-methodology and grouped-inventory optimistic bindings; clean
  repository/ref/commit; pinned AWS AI-DLC source/activation hashes; one of the
  nine scopes and its exact hash-bound seed set; current Codex/Claude/Kiro
  command and registry hashes; explicit per-selected-Stage instance budgets
  plus protected runtime reservations; an exact human assertion; and Task
  quiescence.
- [x] Preserved source semantics without invented budgets. The request supplies
  every selected Stage allocation and maximum Run reservation. The five
  `for_each_artifact=unit-of-work` Stages must use the explicit unit count;
  other selected Stages have one instance. Stage and protected runtime totals
  must fit the current Task Token/cost envelope, including the explicit
  Token-only branch.
- [x] Kept the human Gate assertion non-authoritative: it hash-binds the exact
  successor strategy, current optimistic locks/methodology, migration proposal
  path/hash, repository/source/activation, scope, seeds, runtime registry, and
  budget, but the preview does not authenticate identity, persist/consume a
  Gate, or authorize migration.
- [x] Every decision fixes `preview_only=true`, all mutation/execution flags
  false, and routing/dispatch/migration authority false. Repository Artifact
  observation rejects paths outside the project, unreadable/changing files,
  files over 16 MB, and aggregate input over 64 MB.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-migration-preview-v1.md`; HTTP, UI,
  runtime dispatch, routing, and compatibility migration remain deferred.

### Verification and review state

- [x] New migration-preview regressions: 18 passed.
- [x] Migration preview, activation/source contracts, protocol freeze,
  inventory/lifecycle, Task orchestration, and formal protocol orchestration:
  235 passed.
- [x] Complete non-integration backend surface outside two legacy literal
  `/tmp/...` Windows path tests: 588 passed, 20 deselected. The exact required
  suite reached 588 passed and 18 deselected; only
  `test_tool_calling_loop` and `test_multi_tool_sequence` failed because this
  Windows sandbox denied `D:\tmp`, not because of migration-preview behavior.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`, CLI help,
  and `git diff --check` passed.
- [x] Final post-review verification repeated the 18 focused regressions and
  the complete backend surface outside the two legacy literal `/tmp/...`
  tests: 588 passed, 20 deselected. Protocol Schema export/check,
  system-Temp-isolated `compileall`, migration-preview CLI help, and
  `git diff --check` also passed.
- [x] Kiro CLI session `87107c54-bde9-4707-a7f8-0159524b03fc`
  independently reviewed the methodology/protocol/lifecycle boundary,
  deterministic bindings, budget/quiescence checks, rollback-only snapshot,
  bounded Artifact observation, schemas, and non-authority guarantees. It
  returned `VERDICT: APPROVE` with no high/medium findings. Its one LOW
  documentation finding was fixed by documenting the existing stable stdout
  error behavior, and its targeted final re-review confirmed the prior
  approval remains valid.
- [x] Independent Claude Code safe-mode review inspected the complete final
  tracked and untracked implementation, schemas, wiring, docs, and adversarial
  regressions. It returned `VERDICT: APPROVE` with no high/medium findings.
  Its non-blocking LOW notes concern the rollback snapshot's redundant
  idempotent Control Plane schema initialization and the deliberately
  overloaded documented exit-2/stdout contract; its informational aggregate
  byte-accounting observation is fail-closed.

### Current checkpoint and next safe action

Implementation, automated verification, actionable finding resolution, and
both review gates are complete on baseline `74e2acd`. This bounded preview is
ready for its reviewed commit/push. The next implementation slice after that is
a reviewed transactional successor-Task activation path that authenticates and
persists the human migration Gate and atomically rechecks every preview binding
before sealing the successor Plan/inventory.

## 2026-07-29 - AWS AI-DLC activation definition (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyActivationDefinition@1.0`, its generated
  activation manifest, checked-in JSON Schema, and a guarded export/check
  script. It binds source graph
  `668a379e4b6ecbed1aaf47e0823b43df147b7c239a8a4ab03ba43b71030e057d`,
  compiled graph
  `9de074e882c18bcc1285a953366a7793149d05a349657a7989f0e54b2fdd1430`,
  and activation manifest
  `219d863f92b9162bef04f133623d020fb9c0ff48676d68521b5ddab47c2ede12`.
- [x] Materialized all 32 source Stage Contracts, 132 input bindings, 122
  unique required/optional output Artifacts, source lead/support/reviewer
  profiles, five per-`unit-of-work` expansions, the `code-generation`
  workspace requirement, four hash-bound sensors, and 232 globally unique Gate
  requirements. It also records all 27 required-input gaps in the non-closed
  upstream scope matrix as exact hash-bound Task seed requirements; missing
  seeds must block activation instead of silently widening the scope.
- [x] Fixed the runtime/independence policy as Task-pinned Codex production,
  independent Claude correctness review, Kiro methodology stewardship, and
  explicit human completion approval. Source AWS agent identities remain
  profiles only and do not gain routing authority.
- [x] Preserved truthful budget and quality boundaries: no invented static
  per-Stage Token/cost limits; Task envelope and per-Run reservations remain
  mandatory; exact-or-conservative settlement, four independent Run
  dimensions, and one format-only repair remain required.
- [x] Preserved all eleven authored source reviewer bounds at two iterations.
  Automatic cross-Stage rework remains forbidden; exhaustion blocks and
  escalates, and Keep/Modify/Redo requires an explicit Task decision.
- [x] Kept existing Tasks and `agora-aidlc-foundation@0.1` unchanged. The
  activation definition records `routing_authority=false`,
  `dispatch_authority=false`, and `migration_authority=false`; it exposes no
  activation command. Its deterministic hash is
  `c9d9b075a5219292d94e1fa3aff2383dc1e98bb5518cd486227f85b20b45af6d`.

### Verification and review state

- [x] New activation-definition regressions: 12 passed.
- [x] Activation definition, source graph, protocol freeze, Task orchestration,
  and formal protocol orchestration: 181 passed.
- [x] Final post-review non-integration backend suite: 572 passed, 18
  deselected, with only the existing Starlette/httpx and Windows Proactor
  cleanup warnings.
- [x] Activation-manifest export/check passed against the pinned upstream
  checkout.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`,
  activation JSON round-trip/hash smoke, `agora task --help`, and
  `git diff --check` passed.
- [x] Kiro CLI session `2c9215c4-53de-4bbb-ab33-f03cec9de836` completed the
  methodology/protocol/source-fidelity review and returned
  `VERDICT: APPROVE` with no high/medium findings. Its three informational
  LOW observations concern optional manifest defense-in-depth, automated
  exporter-drift fixtures, and additional direct source-binding branch tests;
  the review used 1.16 credits.
- [x] Independent Claude Code safe-mode review inspected the complete tracked
  and untracked implementation, packaging/resource path, exporter, hash/schema
  binding, authority boundaries, and adversarial tests. It found no actionable
  high/medium defect and returned `VERDICT: APPROVE`; the text invocation did
  not expose a session or cost field. Its optional LOW hardening notes concern
  installed-wheel package-data assurance, more direct fail-closed branch tests,
  and explicitly documenting the sensor manifest as its integrity anchor.

### Current checkpoint and next safe action

The implementation, verification, and both review gates are complete. After
the reviewed commit, the next bounded backend slice is a sealed, Task-scoped
methodology migration preview/decision that validates Task, repository
revision, selected scope, source/activation hashes, runtime pins, budget, and
explicit human Gate. It must remain read-only and non-migrating until a later
reviewed transactional activation path exists. HTTP and UI stay deferred.

## 2026-07-28 - AWS AI-DLC authoritative source graph (reviewed)

### Bounded implementation

- [x] The user identified the AWS AI-DLC Method Definition and the public
  `awslabs/aidlc-workflows` repository as Agora's authoritative AI-DLC source.
  The baseline pins stable release `v2.3.0` to peeled commit
  `29a31f7899731b53f2b8d7f76cd223f9a8a25859`; it does not follow upstream
  `main`.
- [x] Added hash-sealed `MethodologySourceGraph@1.0` and its checked-in JSON
  Schema. The source manifest binds the external Method Definition Paper by
  URL and SHA-256, plus the repository specification, Stage format, recovery
  protocol, compiled Codex graph, scope grid, all 32 canonical Stage files, and
  all 9 scope files: 46 exact upstream paths and SHA-256 values.
- [x] Froze the five source phases, all 32 Stage identities/order/modes,
  dependency DAG, 9 scope branch profiles, and the five Construction nodes
  that expand per `unit-of-work`. Validation fails closed for manifest/graph
  drift, duplicate or unknown identities, missing source bindings, and
  self/forward/cyclic dependencies.
- [x] Preserved the authority boundary. The graph records
  `routing_authority=false`, `dispatch_authority=false`, and
  `structured_rework_edges=false`. AWS `v2.3.0` keeps rework/halting partly in
  prose and reserves structured `on_failure`/`timeout`/`retry`, so Agora does
  not invent transitions, limits, Gates, budgets, or runtime assignments.
- [x] Kept `agora-aidlc-foundation@0.1` unchanged and provisional. Source
  authority alone cannot initialize or migrate a Task, create a Stage, alter
  routing, or fetch/install native AI-DLC files. The deterministic source-graph
  hash is
  `668a379e4b6ecbed1aaf47e0823b43df147b7c239a8a4ab03ba43b71030e057d`.

### Verification and review state

- [x] New source-graph regressions: 9 passed.
- [x] Source graph plus protocol freeze and existing Task orchestration:
  104 passed.
- [x] Complete non-integration backend suite: 560 passed, 18 deselected, with
  only the existing Starlette/httpx and Windows Proactor cleanup warnings.
- [x] Protocol Schema export/check, isolated `compileall`, source-graph JSON
  round-trip/hash smoke, `agora task --help`, and `git diff --check` passed.
- [x] Read-only source verification matched all 46 repository artifact hashes
  against `v2.3.0` and matched the downloaded Method Definition Paper hash.
- [x] Kiro CLI session `0f3a4caa-78d1-4f41-831a-4c3e48806135`
  independently verified the pinned tag/commit, source graph, representative
  source hashes, scope profiles, seal, Schema, and non-dispatch boundary. It
  reported no high/medium findings and returned `VERDICT: APPROVE`; its three
  low observations are deferred activation hardening (`workspace_requires`,
  `for_each` producer linkage, and automated upstream transcription checks).
- [x] Independent Claude Code review inspected the complete in-repository
  model, fixed graph, seal path, Schema, adversarial regressions, unchanged
  foundation method, and boundary documentation. It reported no blocking
  correctness, safety, or regression findings and returned
  `VERDICT: APPROVE`.

### Current checkpoint and next safe action

The implementation, verification, and both review gates are complete. The next
bounded backend slice is a separate Agora activation definition that
materializes Stage Contracts, Artifacts/Evidence/Approvals/Gates, runtime
independence, budget/quality policy, bounded rework/escalation, and explicit
Task methodology migration. The source graph must remain non-dispatching until
that mapping is reviewed.

## 2026-07-27 - Native Task consultation dispatch (reviewed)

### Bounded implementation

- [x] Added strict `ConsultationCandidateDraft@1.0` output and a separate
  append-only consultation execution ledger. Every claim binds the observed
  Plan version, authoritative grouped-inventory Stage route, pinned runtime,
  clean repository ID/ref/commit, prompt hash, exact-input operation key, and
  Token/cost admission reservation.
- [x] Added `agora task consult`. It requires explicit unbounded-native-usage
  acknowledgement, dispatches only the already pinned Stage runtime, supplies
  a bounded read-only advisory context instead of a transcript, and accepts
  only exact draft JSON or one whole-document fence repair.
- [x] Kept process, transport, schema, repository, and provider-usage results
  independent. Settlement rechecks repository revision and transactionally
  revalidates Plan/route bindings. Only a valid current result creates one
  redacted hash-sealed candidate; failures still settle usage and cannot
  mutate Task, Stage, Gate, formal Run, Artifact, Evidence, or decision state.
  Repository drift preserves an interrupted process dimension, and
  cancellation before PID attachment settles as provable exact-zero usage.
- [x] Protected every unfinished reviewer Stage allocation, blocked concurrent
  formal Run/consultation claims, made exact-input retries non-dispatching, and
  added crash recovery that refuses live/uninspectable PIDs and settles dead or
  never-attached processes without duplicate execution. Formal and consultation
  admission now share the same conservative cross-ledger Token/cost snapshot.
- [x] Unified Task projection schema `11.0` adds paginated consultation
  executions and includes their active reservations and terminal usage in
  truthful aggregate budget state. Prompts and raw native output remain
  omitted.
- [x] Documented the boundary in
  `docs/architecture/native-task-consultation-v1.md` and updated the protocol,
  candidate, and unified projection baselines.

### Verification and review state

- [x] New native consultation suite: 14 passed.
- [x] Related Task orchestration: 62 passed; formal protocol orchestration:
  65 passed; protocol freeze: 33 passed.
- [x] Complete non-integration backend suite: 551 passed, 18 deselected, with
  only the existing Starlette/httpx and Windows Proactor cleanup warnings.
  Protocol Schema export/check, `compileall`, CLI help smoke, and
  `git diff --check` passed.
- [x] Kiro initial review: `CHANGES_REQUESTED` for symmetric formal/
  consultation budget accounting, interrupted-process truth under repository
  drift, missing bidirectional concurrency/budget regressions, and the stale
  verification record. Targeted code re-review confirmed every implementation
  finding fixed.
- [x] Kiro final targeted re-review: `APPROVE`; the corrected verification
  record resolved the sole remaining persistence-gate blocker.
- [x] Independent Claude Code review: `APPROVE`, with one optional
  cancellation-before-PID truthfulness observation subsequently fixed.
- [x] Claude targeted re-review: `APPROVE`; pre-attach cancellation settles
  exact-zero usage and post-attach interruption accounting remains intact.

### Current next safe action

After committing and pushing this reviewed increment, do not invent the next
methodology graph. The next product slice remains the authoritative full
AI-DLC method graph and branch/rework/DAG semantics, but its formal freeze must
wait for the missing complete authoritative source. Until that source is
available, preserve this clean checkpoint rather than widening native
consultation, dynamic routing/model selection, HTTP, or UI scope.

## 2026-07-26 - Consultation candidate authority boundary (reviewed)

### Bounded implementation

- [x] Added hash-sealed `ConsultationCandidate@1.0` and
  `ConsultationCandidateDisposition@1.0` contracts plus checked-in JSON
  Schemas. Candidate records bind the current authoritative grouped-inventory
  Stage route, its pinned runtime, and the exact observed Plan version while
  carrying explicit `advisory_authority=false` and `formal_artifact=false`
  markers.
- [x] Added append-only candidate/disposition persistence. Registration is
  idempotent, redacted, bounded, and cannot change Task, Plan, Stage, Gate,
  Run, Artifact, Evidence, Approval, or decision state. It fails closed for a
  stale Plan, a compatibility/Control Plane route mismatch, runtime
  substitution, or an active Run.
- [x] Added explicit `agora task adopt` and `agora task reject` actions.
  Adoption atomically creates or reuses the bounded versioned TaskDecision and
  increments the Plan version once to invalidate stale claims. Rejection
  creates no decision and does not change the Plan version. Both actions are
  exact-operation-key idempotent and one candidate may receive only one
  disposition.
- [x] Unified Task projection schema `10.0` exposes paginated, hash-verified
  candidates and dispositions, true totals/pages, pending candidate human
  actions, and CLI disposition labels. Candidate content remains separate from
  formal Artifact/Evidence/Approval and Gate-derived next-safe-action truth.
- [x] Added the architecture boundary in
  `docs/architecture/task-consultation-candidate-v1.md`. Native provider
  `consult` dispatch and parsing remain deliberately separate so provider
  output cannot become authoritative by accident.

### Verification and review state

- [x] Focused protocol, orchestration, and candidate suite: 158 passed.
- [x] Initial complete non-integration backend suite: 535 passed, 18
  deselected. After the Claude-requested fixes, the final complete suite passed
  537 tests with 18 deselected and
  only the existing Starlette/httpx and Windows Proactor cleanup warnings.
  Protocol Schema export/check, `compileall`, and `git diff --check` passed.
- [x] Initial Kiro methodology/protocol review returned `VERDICT: APPROVE`
  with no high/medium findings. It used 14.82 credits and independently reran
  6 focused and 125 broader orchestration tests with the correct isolated
  Windows temp boundary.
- [x] Initial Claude review returned `VERDICT: CHANGES_REQUESTED` with two
  medium findings: omitted operation keys were random rather than
  deterministic, and documented fail-closed conflict paths lacked direct
  regressions. The review session
  `b0a05865-7b1e-4c0e-9121-4ae330373e39` used `$1.635861`.
- [x] Replaced random default candidate/disposition operation keys with
  canonical hashes of the complete redacted/normalized request and bound the
  registration actor into the sealed candidate. Identical CLI retries now
  return the original receipt.
- [x] Added direct active-Run, changed-input operation replay, different-Task,
  single-disposition, default-key replay, stale Plan, and wrong-runtime
  regressions. The post-fix focused consultation suite passed 8 tests.
- [x] Final Kiro post-fix re-review inspected the complete current diff,
  independently reran 6 focused tests and both changed suites (127 passed),
  and returned `VERDICT: APPROVE` with no high/medium findings. It used 11.38
  credits.
- [x] Final Claude post-fix re-review verified both requested fixes and all
  authority, transaction, hash, route, and projection invariants, then returned
  `VERDICT: APPROVE` with no remaining high/medium findings. Review session
  `6301006c-606a-4b17-9eef-cbfc942ac519` used `$1.741692`.

### Current checkpoint and next safe action

- [x] Committed and pushed the reviewed increment as `9969e0f`; `main` and
  `origin/main` matched that commit immediately after push. User-owned
  `.kiro/` and historical pytest temp directories remained untracked and were
  not staged.
- [x] The repo-grounded whole-program estimate is now about 82%, with a
  practical 80-84% range. The completed real formal end-to-end acceptance and
  this candidate/adoption authority boundary close two previously high-weight
  gaps. The remaining work is native consultation dispatch, the missing
  authoritative complete AI-DLC graph plus branch/rework/DAG semantics,
  authenticated serviceability/dynamic routing policy, authenticated API and
  Workbench, and migration/release closeout.

The next independent backend slice is native
`agora task consult` dispatch with candidate-only result parsing and truthful
provider usage settlement. The missing authoritative complete AI-DLC source
still blocks formal method-graph freeze, but does not block this consultation
path.

## 2026-07-26 - Formal Kiro chat transport and settlement recheck (reviewed)

### Live acceptance evidence and bounded implementation

- [x] The eighth formal Claude `correctness_review` attempt passed. Run
  `orun_b6a00651f8b54a738d8e99a5c29ea6c2` completed with a schema-valid,
  semantically successful Handoff, passed its Gate, and settled 681,411 exact
  Tokens plus `$3.740426`. The accepted Handoff hash is
  `edadcba737d3df9199f6b72b0fa86aff2f95503953a731b9ea445dd453055c27`.
- [x] Budget amendment `budget_7132f9f2986c47c88dfd030e1c825e08`
  raised the Task envelope to 6,988,936 Tokens, exactly preserving the
  conservative settled debit plus the final sealed 7,500-Token Kiro
  reservation. The first Kiro `methodology_review` Run
  `orun_6a87f8f54bbe4c2eae8f66587f48f3c7` exited zero but emitted its ANSI chat
  transcript rather than a Handoff. It settled `handoff_json_invalid` with
  11,285 estimated Tokens and unavailable cost; Gate and Stage remained
  blocked with Attention `attn_protocol_bd5ad70fc70b88b1fc4952c90bba06a3`.
- [x] Added the versioned `KIRO_CHAT_V1` result format. It selects only the
  final exact Kiro assistant-turn marker, removes the UI trailer, and never
  searches tool arguments or intermediate transcript JSON. Missing markers,
  empty final turns, and non-Handoff final turns remain fail-closed.
- [x] The default Kiro command now uses `--wrap never` and trusts only
  `execute_bash`, which is required to calculate frozen Artifact and Handoff
  hashes. It does not trust all tools. The sealed Context still forbids
  repository mutation.
- [x] Added a post-process repository recheck before settlement. Dirty or
  unavailable repositories and clean ref/commit changes invalidate an
  otherwise valid Handoff as `handoff_context_mismatch`, preserve the actual
  process, transport, raw output, and usage observations, and cannot pass a
  Gate. The documentation now states that this recheck is not an OS sandbox:
  hostile-runtime deployments require an external container or VM boundary
  for network and out-of-repository effects.
- [x] Kiro usage remains explicitly estimated, now from the sealed prompt plus
  the raw captured UI transcript before semantic extraction rather than the
  shorter normalized Handoff candidate.
- [x] Replayed the saved 25,601-character failed Kiro transcript through the
  new normalizer. It selected the 13,086-character final assistant turn ending
  in a request for tool approval, did not harvest the intermediate JSON
  script, and remained non-JSON. A direct Kiro CLI 2.14.2 non-interactive
  probe verified the exact ANSI markers; `--format json` still emitted the UI
  transcript, so transport normalization remains necessary.

### Verification and review state

- [x] Focused provider-usage, runtime, and protocol orchestration suite:
  129 passed. Kiro-review fixes added the clean changed-revision regression;
  the focused review-fix suite passed 67 tests.
- [x] Final complete non-integration backend suite:
  531 passed, 18 deselected, with only the existing Starlette/httpx and
  Windows Proactor cleanup warnings. Protocol Schema export/check,
  `compileall`, and `git diff --check` passed.
- [x] Kiro independently inspected the implementation, surrounding authority
  code, tests, and docs, then returned `VERDICT: APPROVE` using 8.04 credits.
  Its actionable recommendations were addressed by adding the second
  repository-drift regression and documenting the required external isolation
  boundary. The real CLI marker probe satisfied its remaining compatibility
  recommendation.
- [x] Claude independently reviewed the final post-Kiro-fix diff in safe mode,
  found no actionable correctness, safety, or regression defects, and returned
  `VERDICT: APPROVE`. Review session
  `1353385b-678c-4b81-b808-c9622ed149f2` used `$1.704422`.

### Current checkpoint and next safe action

- [x] Committed and pushed the reviewed transport increment as `781f9fd`.
  Formal `retry` then cancelled only
  `attn_protocol_bd5ad70fc70b88b1fc4952c90bba06a3`; it did not spawn a
  provider or alter either passed Stage.
- [x] A fresh policy preview conservatively debited 6,992,721 Tokens and
  required the remaining sealed 7,500-Token reservation. Budget amendment
  `budget_aedbfb0aa8e249cebbf4f87b71259160` therefore set the exact
  admission envelope to 7,000,221 Tokens. The fresh Kiro 2.14.2 preflight was
  allowed and bound resolved command hash
  `38357ea664f1ab69481102846821260442115fc08617788107922e18108cc65d`.
- [x] Final Kiro Run `orun_e6152ef5a04a4088ae8cdedb0771fcbe`
  exited zero, produced a schema-valid and semantically successful Handoff,
  passed `gate:methodology_review`, and settled 23,654 estimated Tokens with
  unavailable cost. Its Handoff hash is
  `d3e079c1c408a73fb9d0a3e449b40133623789cdaf66e1d0ba49fa7397113ff0`.
- [x] All 3/3 frozen Stages and 3/3 Gates are passed. The user's explicit
  instruction to continue through final completion was persisted as the
  terminal human approval. Task `task_f67b410c5aea4133bd7c1316a3e485f7`
  is `completed`, Plan `plan_08d74257c5dd452baf9110bd4d95b0b1`
  is `ready_for_implementation`, `current_stage_key=null`, and no Attention
  remains open.
- [x] The completed Task contains 11 formal Runs, 3 managed Artifacts, and
  3 active Evidence records. Reported usage is 7,007,375 Tokens:
  6,972,436 exact and 34,939 estimated. One timed-out Claude Run remains
  explicitly unavailable and was conservatively debited at admission time.
  Reported provider cost totals `$14.230025`; Kiro cost remains unavailable.
  The final Kiro Run exceeded its reservation, so the aggregate budget
  projection correctly remains unavailable rather than inventing remaining
  capacity.

The completed Task and its frozen acceptance worktree at `2916060` must remain
immutable. The next safe product action is a new bounded implementation Task
that references these accepted Artifact versions and pins the repository to
the commit containing this terminal checkpoint. Keep UI deferred until that
implementation Task proves its CLI/control-plane path.

## 2026-07-26 - Memory source-reference guidance (reviewed)

### Seventh Claude attempt and final remaining schema class

- [x] Used the reviewed nested-object guide for formal Claude
  `correctness_review` attempt 7. Run
  `orun_cb8dc38e1aa7427884159af9c716abdd` completed in 471.022849
  seconds and settled 698,516 exact Tokens plus `$1.6727375`.
- [x] Every previously observed structural issue was corrected: raw JSON,
  managed Artifact, nested ProducerRefs, structured Evidence details, null
  NativeStateSnapshot, and complete MemoryCandidate objects. The parser still
  failed closed with `handoff_schema_invalid` only because the two non-empty
  MemoryCandidate `source_refs` contained file-path `/` and `#` characters
  outside frozen StableId. Attention
  `attn_protocol_938a858b707eb190aae994073c6882fc` remains open until formal
  retry.
- [x] Added a bounded instruction that memory source references are non-empty
  StableIds using only `A-Z a-z 0-9 _ . : -`. Legitimate memory suggestions
  remain allowed, no output was rewritten, and no parser, schema, repair,
  Evidence, Gate, or state behavior changed.
- [x] A copied authoritative database produced a 24,499-byte prompt with the
  source-reference rule, leaving 77 bytes below the frozen 24 KiB bound. The
  diagnostic Runner spawned no provider.

### Verification and review state

- [x] Complete protocol orchestration file: 63 passed. The first complete
  backend attempt was invalidated after an infrastructure timeout returned no
  result and left no matching pytest/python process. A fresh isolated rerun
  completed with 527 passed, 18 deselected and only the existing
  Starlette/httpx and Windows Proactor cleanup warnings. Schema export/check,
  isolated `compileall`, and `git diff --check` passed.
- [x] Kiro verified the source-reference type, minimum length, frozen regex,
  memory/state separation, tight but enforced prompt bound, docs, and tests,
  then returned `KIRO_APPROVE`; the review used 3.25 credits. It noted only
  that the compact instruction does not separately state the leading
  alphanumeric constraint, which remains fail-closed in the schema.
- [x] Claude independently checked the same frozen regex, authority boundary,
  77-byte actual margin, docs, and tests with tools and customizations
  disabled, then returned `CLAUDE_APPROVE`. It made the same non-blocking
  leading-character observation; the invocation exposed no cost field.
- [x] Committed and pushed this reviewed prompt-only increment as `42f728d`.
  Budget amendment `budget_43cd01c5a81d4b8a8f2232222524db7f`
  set the envelope to 6,316,525 Tokens before Claude attempt 8. That attempt
  passed as Run `orun_b6a00651f8b54a738d8e99a5c29ea6c2`; the subsequent
  final-Kiro amendment and first Kiro result are recorded in the current
  checkpoint above.

## 2026-07-26 - Nested Handoff object guidance (reviewed)

### Sixth Claude attempt and exact failure

- [x] Used the reviewed optional-extension guide for formal Claude
  `correctness_review` attempt 6. Run
  `orun_82dfc3f762ec4fe3bd8e6c1589fb19c9` completed in 227.825901
  seconds and settled 290,593 exact Tokens plus `$0.9153835`.
- [x] Claude correctly returned raw JSON, set `native_state_snapshot=null`,
  and used an empty `memory_candidates` list. The parser still failed closed
  with `handoff_schema_invalid`: the output Artifact and Evidence flattened
  their nested `producer` values to `"claude"` strings, and Evidence
  `details` was free-form text instead of a JSON object. Attention
  `attn_protocol_a57b1c0712a1f75b0df502ee93969264` remains open until formal
  retry.
- [x] Added one bounded instruction that every producer field uses the already
  declared `[runtime,run_id,stage_key]` ProducerRef object and that
  `Evidence.details` is a JSON object. No output was reinterpreted and no
  parser, schema, repair, Gate, Evidence, or state mutation changed.
- [x] Compressed adjacent reviewed guidance without semantic change so the
  actual copied-database prompt is 24,424 bytes, leaving 152 bytes below the
  frozen 24 KiB argv bound. The diagnostic Runner spawned no provider.

### Verification and review state

- [x] Complete protocol orchestration file: 63 passed. Complete
  non-integration backend suite: 527 passed, 18 deselected, with only the
  existing Starlette/httpx and Windows Proactor cleanup warnings. Schema
  export/check, isolated `compileall`, and `git diff --check` passed.
- [x] Kiro independently verified every producer field is a ProducerRef,
  Evidence details is a JSON object, the compact wording is unambiguous, the
  fail-closed authority boundary is unchanged, and the prompt remains bounded,
  then returned `KIRO_APPROVE`; the review used 3.18 credits.
- [x] Claude independently reviewed the supplied diff, frozen models, live
  error evidence, actual prompt size, docs, and tests with tools and
  customizations disabled, then returned `CLAUDE_APPROVE`. The invocation did
  not expose a cost field.
- [x] Committed and pushed the reviewed prompt-only increment as `4fe4458`,
  then cancelled only the latest matching Attention through formal retry.
  Budget amendment `budget_fd0950e826a14e0299e007d38f8c9ab1` set the
  envelope to 5,618,009 Tokens from the conservative settled debit plus the
  sealed 9,000 Claude and 7,500 Kiro reservations before dispatching one
  Claude attempt.

## 2026-07-26 - Optional Handoff extension shape guidance (reviewed)

### Safe-mode Claude evidence and bounded fix

- [x] Committed native customization isolation was used for formal Claude
  `correctness_review` attempt 5. Run
  `orun_fb7329ad13804d7e94e72402692c43a9` completed in 524.519498
  seconds and settled 853,543 exact Tokens plus `$1.8793085`.
- [x] Safe mode removed the previous SessionEnd/memory stdout replacement.
  Claude returned an 8,858-character raw JSON object whose first and last
  non-whitespace characters were `{` and `}`, with no Markdown fences. Core
  Handoff, required Artifact, and Gate Evidence shapes were correct.
- [x] The parser still failed closed with `handoff_schema_invalid`. The only
  validation errors were optional extensions: an invented ad hoc
  `native_state_snapshot` lacked the frozen snapshot fields and contained
  extras, while two `memory_candidates` were strings instead of frozen
  `MemoryCandidate` objects. Attention
  `attn_protocol_47576b9f9cdb4f76c437e07cb32e9f09` remains open until formal
  retry.
- [x] Added the exact MemoryCandidate keys
  `[candidate_id,kind,title,content,source_refs]`, forbade string candidates,
  and required `native_state_snapshot=null` unless the Context supplies a
  complete frozen object. Legitimate Agent-suggested MemoryCandidate objects
  remain allowed; native state, memory, Evidence, and authoritative state
  remain separate. No parser, schema, repair, Gate, or state mutation changed.

### Verification and review state

- [x] A copied authoritative database was retried and budget-amended only in
  the copy. It generated a 24,410-byte Claude prompt containing both optional
  extension rules, remained 166 bytes below the frozen 24 KiB bound, and used
  a no-spawn diagnostic Runner.
- [x] Complete protocol orchestration file: 63 passed. Complete
  non-integration backend suite: 527 passed, 18 deselected, with only the
  existing Starlette/httpx and Windows Proactor cleanup warnings. Schema
  export/check, isolated `compileall`, and `git diff --check` passed.
- [x] Kiro independently verified schema fidelity, memory-versus-authority
  separation, legitimate candidate preservation, prompt bound, docs, and
  tests, then returned `KIRO_APPROVE`; the review used 2.27 credits. It noted
  only that MemoryCandidate `source_refs` is non-empty in the model, which was
  non-blocking for this exact-key guide.
- [x] Claude independently reviewed the supplied diff, frozen model excerpt,
  live failure evidence, and prompt-bound result with tools and customizations
  disabled, then returned `CLAUDE_APPROVE`. The invocation did not expose a
  cost field.
- [x] Committed and pushed the reviewed prompt-only increment as `4f72068`,
  then cancelled only the latest matching Attention through formal retry.
  Budget amendment `budget_3b34f78520e94632b8afd0c04341451a` set the
  envelope to 5,327,416 Tokens from the conservative settled debit plus the
  sealed 9,000 Claude and 7,500 Kiro reservations before dispatching one
  Claude attempt.

## 2026-07-26 - Native Claude customization isolation (reviewed)

### Live runtime-boundary evidence and fix

- [x] After the raw-managed-Handoff increment, formally retried only Claude
  `correctness_review`. Run `orun_c1be84aef72642a2b9619461ef960171`
  reached the configured 600-second process limit with no stdout or provider
  usage observation. It settled `process_timed_out` with unavailable Token and
  cost measurements; no Handoff, Artifact, Evidence, or Gate success was
  inferred.
- [x] Verified that the timed-out subprocess was stopped and left no matching
  child process. Formal retry restored the same immutable Stage. Routing
  conservatively debited the unavailable Run at its sealed 9,000-Token
  reservation rather than treating unknown usage as zero, so budget amendment
  `budget_567db27ed6844983a706e58559e629a8` increased the envelope to
  3,680,347 Tokens and preserved both remaining reviewer reservations.
- [x] Re-ran the same Stage with the supported service timeout raised to 1,800
  seconds. Run `orun_106c2a04e650460a865d93d139bfff47` completed in
  432.369399 seconds and settled 793,526 exact Tokens plus `$1.810576`.
  Exit zero still did not advance the Stage: its 1,143-character normalized
  stdout was a `Session memory saved` summary with no JSON braces, so
  `handoff_json_invalid` blocked the Stage and opened Attention
  `attn_protocol_d31c6d09b632cd0a0e5ed99acd7a6b96`.
- [x] The captured summary states that Claude had emitted a valid Handoff
  before a native SessionEnd/memory customization replaced the final stdout.
  Added Claude CLI's native `--safe-mode` to the audited default command while
  retaining print mode, JSON transport, plan permissions, and disabled session
  persistence. This isolates hooks, skills, plugins, MCP servers, output
  styles, and other unbound customizations without editing native Claude files
  or weakening parser, repair, Gate, or state authority.

### Verification and review state

- [x] Focused provider-usage and runtime-orchestration tests: 63 passed.
  Complete non-integration backend suite: 527 passed, 18 deselected, with only
  the existing Starlette/httpx and Windows Proactor cleanup warnings.
- [x] Installed Claude Code `2.1.220` accepts `--safe-mode`; the resolved
  default tuple includes it alongside `--permission-mode plan` and
  `--no-session-persistence`. Schema export/check, isolated `compileall`, and
  `git diff --check` passed.
- [x] Kiro independently verified the protocol/AI-DLC authority boundary,
  native-file non-mutation, CLI flag support and ordering, custom-command
  scope, documentation, and tests, then returned `KIRO_APPROVE`; the review
  used 1.65 credits.
- [x] Claude independently reviewed the supplied diff and live failure
  evidence with tools and customizations disabled, found no discrepancy, and
  returned `CLAUDE_APPROVE`. The invocation did not expose a cost field.
- [x] Committed and pushed the reviewed runtime-isolation increment as
  `6583b92`, then cancelled only the matching protocol Attention through
  formal retry. Budget amendment
  `budget_f6f87eb8b0b146899f804534d9b87e97` set the envelope to 4,473,873
  Tokens from exact settlements plus the timed-out Run's conservative
  reservation and both remaining reviewer reservations before dispatching one
  safe-mode Claude attempt.

## 2026-07-26 - Raw managed Handoff output guidance (reviewed)

### Second live Claude failure and bounded fix

- [x] Retried only the authoritative Claude `correctness_review` Stage after
  the exact-key increment. Run `orun_7741de8eba3c48a598c67a9f5e2b5d66`
  exited zero and settled 825,733 exact Tokens plus `$2.12133425`, but its
  response placed prose before a fenced JSON object. The one permitted
  whole-document-fence repair therefore rejected it as `handoff_json_invalid`;
  Stage and Task remained blocked with Attention
  `attn_protocol_4b3f55ab5ab727d34514fe0479d49a3e`.
- [x] A read-only diagnostic extracted the fenced object without mutating
  authoritative state. It was structurally complete and became fully
  `HandoffPack@1.0`-valid only after changing the non-frozen Artifact storage
  alias `inline` to the frozen `managed` value. This diagnostic substitution
  was not used as a repair or accepted result.
- [x] Strengthened only the dispatch guidance: the first byte must be `{`, the
  last non-whitespace byte must be `}`, prose and Markdown fences are
  forbidden, and generated output Artifacts must use `storage="managed"` with
  UTF-8 `content` and `location=null`. No parser, schema, repair, Evidence,
  Gate, or authoritative-state behavior changed.
- [x] Retried and budget-amended only a copied authoritative database. It
  generated a 24,169-byte Claude prompt containing the raw-JSON and managed
  storage requirements, remained below the frozen 24 KiB bound, and used a
  no-spawn diagnostic Runner.

### Verification and review state

- [x] Focused rendered-prompt regressions: 3 passed. Complete protocol
  orchestration file: 63 passed. Complete non-integration backend suite:
  527 passed, 18 deselected, with only the existing Starlette/httpx and
  Windows Proactor cleanup warnings.
- [x] Schema export/check, isolated `compileall`, and `git diff --check`
  passed.
- [x] Kiro independently verified the prompt-only authority boundary, frozen
  `managed|referenced` enum, unchanged whole-document-fence repair, rendered
  brace escaping, 24 KiB bound, and regressions, then returned
  `KIRO_APPROVE`. The substantive review used 6.73 credits; a 0.45-credit
  resumed response restated the machine-readable verdict after terminal
  truncation.
- [x] Claude independently reviewed the complete supplied diff and frozen
  models with tools disabled, found no discrepancy, and returned
  `CLAUDE_APPROVE`. The invocation did not expose a cost field.
- [x] Committed and pushed the reviewed prompt-only increment as `4168fb2`,
  then formally retried only the blocked Claude Stage, cancelled only its
  matching Attention, amended the envelope to exactly the 3,654,847 settled
  Tokens plus the sealed 9,000 Claude and 7,500 Kiro reservations, and
  dispatched the next Claude attempt.

## 2026-07-26 - Exact formal Handoff key guidance (reviewed)

### Live failure evidence and bounded fix

- [x] Resumed formal Task `task_f67b410c5aea4133bd7c1316a3e485f7`
  with the reviewed Context-materialization engine and dispatched only the
  authoritative Claude `correctness_review` Stage. Kiro was not started.
- [x] Claude Run `orun_1c1e67a1de7a499d83d07a6ecc38316c` exited zero and
  settled 1,112,330 exact Tokens plus `$2.09025925`. Its semantic content
  reported no high or medium finding, but the returned object used
  non-protocol aliases including `pack_type`, `blockers`, `repository`,
  `commit`, `requirement`, and `result`, and omitted required Handoff,
  Artifact, and Evidence fields.
- [x] The one allowed format repair did not invent the missing fields or map
  aliases. Schema remained `protocol_failed` with
  `handoff_schema_invalid`, so the Stage and Task blocked and Attention
  `attn_protocol_b68f3f06d9ce462150baa96d4b4af2ad` remained open. Exit zero
  was not treated as semantic or Gate success.
- [x] Added a fixed bounded exact-key guide for `HandoffPack`, `ProducerRef`,
  `Artifact`, `Evidence`, and `ArtifactVersionRef`, plus the exact Evidence
  statuses and `handoff:<run_id>` pack identity convention. The guide
  explicitly rejects the observed aliases; it performs no parser mapping,
  schema repair, Evidence creation, state mutation, or semantic inference.
- [x] A copied authoritative database was retried and budget-amended only
  inside the diagnostic copy. It generated a 23,993-byte Claude prompt with
  the exact-key guide and observed-alias prohibition, remained below the
  frozen 24 KiB bound, and used a no-spawn diagnostic Runner.

### Verification and review state

- [x] Focused prompt/large-Artifact regressions: 4 passed. Complete protocol
  orchestration file: 63 passed. Complete non-integration backend suite:
  527 passed, 18 deselected, with only the existing Starlette/httpx and
  Windows Proactor cleanup warnings.
- [x] Schema export/check, isolated `compileall`, and `git diff --check`
  passed.
- [x] Kiro independently cross-checked every guide field against the frozen
  models, verified authority and repair boundaries, and returned
  `KIRO_APPROVE`; the review used 3.96 credits.
- [x] Claude independently reviewed the supplied diff and frozen model
  excerpts, found no high or medium issue, and returned `CLAUDE_APPROVE`.
  The tool-disabled text invocation did not expose a cost field.
- [x] Committed and pushed the reviewed guide as `bb8067e`, then cancelled
  only the failed Run's Attention through formal `retry`, increased the Task
  budget to exactly cover the 2,829,114 settled Tokens plus the sealed 9,000
  Claude and 7,500 Kiro reservations, and dispatched one new Claude attempt
  before Kiro.

## 2026-07-26 - Bounded formal Context materialization (reviewed)

### Failure evidence and implementation

- [x] Ran the authorized `solution_design` Stage for formal Task
  `task_f67b410c5aea4133bd7c1316a3e485f7`. Codex returned a schema-valid
  Handoff, the formal Gate passed, and usage settled exactly at 1,716,784
  Tokens, including 1,553,664 cached input Tokens. The 13,500-Token Stage
  reservation was admission control rather than a provider hard cap.
- [x] Increased the versioned Task envelope from 30,000 to 1,733,284 Tokens,
  exactly preserving the settled debit plus the sealed 9,000 Claude and 7,500
  Kiro reviewer reservations. The amendment retained the Stage allocations,
  reviewer set, and methodology graph.
- [x] The first Claude dispatch attempt failed before Run claim or process
  spawn because the valid 12,697-byte prior Artifact plus duplicated full
  routing and preflight objects exceeded the frozen 24 KiB Windows argv
  prompt bound. Authoritative state remained at one completed Run with the
  Claude Stage `ready`.
- [x] Replaced full policy/preflight prompt duplication with bounded
  projections whose source references retain the complete sealed decision
  hashes. Every prior Artifact remains an exact `input_artifacts`
  version/hash reference.
- [x] Managed Artifact content is now considered deterministically from the
  newest Stage backward and materialized exactly only when the fully rendered
  UTF-8 prompt still fits. Non-fitting, over-20,000-byte, and external content
  becomes explicit reference-only metadata with version, hash, location,
  byte count, and omission reason; content is never truncated or summarized.
  The final 24 KiB check remains fail-closed.
- [x] A copied real acceptance database produced a 23,028-byte Claude prompt
  with the exact 12,697-byte prior Artifact materialized. The diagnostic
  Runner spawned no provider and did not mutate the authoritative Task.

### Verification and review state

- [x] Focused prompt-bound regressions: 4 passed. Complete protocol
  orchestration file: 63 passed. Related orchestration, preflight,
  capabilities, adapter, and Control Plane group before review: 174 passed.
- [x] Pre-review complete backend suite: 525 passed, 18 deselected. Final
  post-review suite after the two additional Kiro-requested regressions:
  527 passed, 18 deselected, with the existing Starlette/httpx and Windows
  Proactor cleanup warnings.
- [x] Schema export/check, isolated `compileall`, CLI smoke, and
  `git diff --check` passed.
- [x] Kiro's initial review found only Low observations. Added explicit greedy
  ordering documentation, regressions for over-20,000-byte and external
  Artifact references, and a shared renderer that removes exception-message
  matching. Initial and follow-up reviews returned `KIRO_APPROVE`; they used
  9.19 and 3.03 credits.
- [x] Claude's first native review was invalidated by a broken user
  SessionEnd hook. Two subsequent tool-driven reviews exhausted their native
  budget before returning a verdict and reported `$0.5609395` and
  `$0.595759` respectively. A final tool-disabled, stdin-bound Sonnet review
  inspected the complete tracked diff and returned `CLAUDE_APPROVE`; that
  invocation did not expose a cost field. Both mandatory review gates are
  complete.
- [x] Committed and pushed the reviewed increment as `3becee0`, then resumed
  the same immutable acceptance Task with the reviewed orchestration engine.
  Provider Stages continue one at a time with exact settled-overrun amendments
  and formal Gates preserved.

## 2026-07-26 - Current-commit formal acceptance preparation

### Isolated acceptance state

- [x] Confirmed the reviewed implementation baseline at
  `main == origin/main == 2916060` while preserving the unrelated untracked
  `.kiro/` and historical pytest temp directories.
- [x] Created clean detached worktree
  `D:\Projects\Agora-acceptance-2916060` at exact commit `2916060`. Its local
  ignored acceptance contract is bound to that commit and has contract hash
  `1296133762e8d766ca44f9abe5ea36f6ff3df67d186c61c012247940712fe8fc`.
- [x] Created fresh formal Task
  `task_f67b410c5aea4133bd7c1316a3e485f7`, Plan
  `plan_08d74257c5dd452baf9110bd4d95b0b1`, and a 30,000-Token admission-control
  envelope split across pinned Codex, Claude, and Kiro Stages. No provider
  runtime was started.
- [x] The read-only capability observation found Codex CLI `0.145.0`, Claude
  Code `2.1.220`, and Kiro CLI `2.14.2`; observation hash
  `72af620df174189e2ca1b9a4b67bfb9180465ef7ff4f7eee962aded64ff72cc2`
  retains `routing_authority=false`.
- [x] Task-scoped preflight allowed the pinned Codex route with all six checks
  passing. Decision hash
  `05c6d2cba6c2bcdb8f34bcf548ff855d815fb5571ad22a7e8605d97d91803b77`
  retained `preview_only=true`, `run_claimed=false`,
  `observation_persisted=false`, `runtime_spawned=false`,
  `route_selection_authority=false`,
  `runtime_substitution_allowed=false`, and
  `provider_serviceability_verified=false`.
- [x] Deliberately invoked formal `task next` without
  `--allow-unbounded-native-usage`. It exited 2 at the safety interlock. The
  Task remains `ready`, the first Stage remains runnable, and authoritative
  status still reports zero Runs, zero reservations, zero settled usage, and
  no artifacts, evidence, Gates, or Attention.

### Next safe action

- [ ] Do not start the provider-backed Stage until the user makes a new
  explicit spend decision acknowledging that the 30,000-Token Task envelope
  is not a native provider hard cap. If authorized, run exactly the first
  Stage with
  `agora task next task_f67b410c5aea4133bd7c1316a3e485f7 --protocol-v1 --allow-unbounded-native-usage`,
  inspect settlement and actual usage, and stop before any second Stage.

## 2026-07-26 - Explicit unbounded native usage acknowledgement (reviewed)

### Scope and recovery decision

- [x] Re-read the recovery contract, active progress and protocol documents,
  requirements, and Git state. The reviewed baseline is
  `main == origin/main == 88d0137`; preserved `.kiro/` and historical pytest
  temp directories remain unrelated and untracked.
- [x] Kept the blocked acceptance Task and its databases read-only. The prior
  Run is already immutably settled by the Control Plane, so reinterpreting its
  stored output with a newer parser would rewrite authoritative history rather
  than perform restart recovery.
- [x] Added `--allow-unbounded-native-usage` to every CLI command that can
  dispatch a provider-backed Run: `start --run`, `next`, and `run`, for both
  compatibility and `--protocol-v1` paths.
- [x] Missing acknowledgement now fails before service construction, native
  capability collection, Task creation, Run claim, or process spawn. The flag
  permits dispatch only; it does not claim a provider hard cap or weaken
  routing, protected review budget, settlement, or Gates.
- [x] Updated the protocol freeze and formal orchestration entry-point
  documentation to distinguish the explicit safety interlock from a native
  execution limit.

### Verification and review state

- [x] Focused CLI regressions: 5 passed. The tests cover all three dispatching
  command shapes, prove that service construction is not reached without the
  acknowledgement, and preserve the explicitly acknowledged formal path.
- [x] Related Task/protocol/provider/capability/preflight suite: 135 passed.
- [x] Pre-review complete non-integration backend suite: 519 passed,
  18 deselected. Schema export/check, isolated `compileall`,
  `agora task next --help`, and `git diff --check` passed.
- [x] Initial Kiro review returned `CHANGES_REQUESTED`. Its retry concern was
  resolved by documenting and testing that `retry` is state-only and never
  claims a Run or starts a process; both compatibility and formal `next`/`run`
  do invoke native adapters and remain guarded. Added defensive `getattr` plus
  status/resume/preflight compatibility coverage. Targeted fixes: 8 passed.
  Targeted Kiro re-review returned `KIRO_APPROVE`; the two review calls used
  1.45 and 0.65 credits.
- [x] Initial Claude correctness/safety review returned `CHANGES_REQUESTED`
  only for explicit missing-ack regressions on the formal
  `next/run --protocol-v1` variants. Both variants were added to the pre-service
  rejection test. The review used a native `$0.12` cap and reported
  `$0.0837707` actual cost.
- [x] Final targeted safety group: 10 passed. Claude targeted re-review returned
  `CLAUDE_APPROVE` and reported `$0.0127122`; total recorded Claude review cost
  for the increment was `$0.0964829`. Kiro and Claude review gates are both
  complete.
- [x] Final post-review non-integration backend suite: 524 passed,
  18 deselected, with the existing Starlette/httpx warning and Windows
  Proactor cleanup warning. Schema export/check, isolated `compileall`,
  `agora task next --help`, and `git diff --check` passed.
- [x] Committed and pushed the reviewed implementation as `cc1a775`. The
  provider-backed acceptance Task was not retried as part of this safety
  increment.

## 2026-07-25 - Live formal acceptance preparation

### Recovered launcher and isolated acceptance state

- [x] Re-read the repository recovery contract, complete progress ledger,
  active protocol/preflight/orchestration architecture, current requirements,
  and Git state before continuing.
- [x] Confirmed `main == origin/main == bbc02e6` before this documentation
  checkpoint. The only root-worktree additions remain the preserved `.kiro/`
  and historical pytest temp directories.
- [x] Restored observation of the audited npm Codex launcher without changing
  the global environment. The current no-shell capability probe resolves Codex
  `0.145.0`, Claude Code `2.1.217`, and Kiro CLI `2.14.2`; every version probe
  exited zero.
- [x] Created clean detached worktree
  `D:\Projects\Agora-acceptance-bbc02e6` at exact commit `bbc02e6` so formal
  Evidence does not bind to the unrelated untracked files in the root
  worktree.
- [x] Created fresh formal Task
  `task_94b7c311827d4fa69d2dc5dc40718499`, Plan
  `plan_a237ba8dd98d4ad4adb534324fda9596`, and concrete contract hash
  `155c021bdf2c39c2daf0c1585ff2d4a7b5d4cc6d952c21321e5c885a25a2a3de`
  in the isolated worktree database. Its total Task ledger envelope is 30,000
  Tokens with no configured monetary amount because provider cost is not
  uniformly observable.
- [x] Ran the Task-scoped read-only preflight. Decision hash
  `27b9302cec7bef91401a53aabbc92997681ee77dfa1beb2fb7151b37a7178313`
  was allowed for the pinned Codex route with all six checks passing and
  `preview_only=true`, `run_claimed=false`,
  `observation_persisted=false`, `runtime_spawned=false`.
- [x] Verified the Task remains `ready`, the Plan remains `active` at
  `solution_design`, all three inventory Stages remain incomplete, and the
  Run count is zero. No provider-backed runtime was started.
- [x] Kept the root `data/agora.db` read-only; it still contains only the
  earlier compatibility demonstration Task and was not migrated for this
  acceptance.

### Current blocker and next safe action

- [x] Obtained explicit authorization for the bounded provider-backed
  acceptance envelope: the user replied `继续` directly to the presented
  30,000-Token, stage-by-stage authorization request. Monetary cost and Kiro
  native credits remain not uniformly enforceable or observable.
- [x] Ran exactly one Stage first with
  `agora task next task_94b7c311827d4fa69d2dc5dc40718499 --protocol-v1`
  from the isolated worktree. Codex exited zero after 488 seconds, but the
  persisted Run blocked with `handoff_json_invalid`; Claude and Kiro were not
  started.
- [x] Exercised restart-safe `task resume` after the blocked Run. Task/Plan and
  Stage remained blocked, the same Attention remained open, Run count and
  attempt count stayed one, and no runtime was redispatched.
- [ ] Do not retry the provider-backed Task until the local repair passes both
  review gates and the user makes a new explicit spend decision. Human approval
  remains required only after all formal Gates eventually pass.

### Live acceptance finding and local fix

- [x] Preserved the fail-closed terminal state: Task and Plan are `blocked`,
  authoritative `solution_design` is `blocked`, its Gate remains `pending`,
  and Attention `attn_protocol_78d5c07df67014e631c3031404c3fdbb` is open.
  Process exit zero did not advance the Stage.
- [x] Read the persisted 64 KiB output without mutating the acceptance
  database. The bounded tail began inside one prior command-execution JSONL
  event, followed by a valid command event, one fully schema-valid
  `succeeded` Handoff, and one valid `turn.completed` usage event.
- [x] Confirmed the real Codex usage was 2,745,043 input Tokens, including
  2,613,248 cached input Tokens, plus 18,998 output Tokens: 2,764,041 total.
  This proves the 30,000-Token Task envelope is an admission-control
  reservation rather than a provider-enforced hard cap. No uniform USD cost
  was available.
- [x] Stopped provider-backed acceptance after the first Run. No Claude or Kiro
  execution is authorized after the observed overrun without a new explicit
  decision.
- [x] Added format-only Codex JSONL tail recovery. The Runner now records
  whether its byte tail actually dropped a prefix; only in that case may the
  normalizer discard exactly one malformed leading transport frame. Every
  later line, final agent message, and unique usage event must remain valid.
- [x] Added a process-level regression that emits more than the capture bound
  and reproduces the split first event. Provider/Runner focused tests pass:
  56 passed. Formal orchestration/capability/preflight regressions pass:
  76 passed.
- [x] Final post-review non-integration backend suite excluding the deferred
  static Web UI test: 516 passed, 18 deselected, with the existing
  Starlette/httpx warning and Windows Proactor cleanup warning. Protocol Schema
  export/check, isolated `compileall`, `agora task --help`, and
  `git diff --check` passed.
- [x] Kiro methodology/protocol review returned `CHANGES_REQUESTED` for two
  regression gaps: a real UTF-8 code point split at the retained byte-tail
  boundary, and explicit fail-closed recovery when terminal usage is absent.
  Both tests were added; the focused provider/Runner suite remained 56 passed.
  Targeted Kiro re-review returned `KIRO_APPROVE`. The two recorded review
  calls consumed 1.87 and 1.13 Kiro credits.
- [x] Independent Claude correctness/safety review returned
  `CLAUDE_APPROVE`; no high/medium findings remained. The call used a native
  `$0.20` maximum and reported `$0.1062697` actual cost, with 2 direct input,
  8,760 cache-creation input, 3,289 cache-read input, and 3,122 output Tokens
  for Sonnet, plus the reported lightweight Haiku routing overhead.
- [x] The implementation review gate is complete. Do not retry the formal
  provider-backed Task as part of this repair close-out: a new acceptance Run
  still requires a separate explicit spend decision because the Codex adapter
  exposes no native hard Token cap.

Current branch: `main`

Current recovery baseline (2026-07-22):

- Before this increment, local `main` and `origin/main` were synchronized at
  reviewed provider-usage commit `847a616`. The commit containing this snapshot
  is the next intended recovery baseline after close-out.
- Active program: the ordered Agora Control Plane transformation. The first two
  stages (protocol/domain freeze and Control Plane v2 persistence/Registry) are
  complete; the bounded API, concrete Task contract, and Runner/Agent Adapter
  are also reviewed foundations. The sealed Run/Gate settlement boundary and
  explicit CLI orchestration wiring and read-only unified Task projection are
  reviewed. Frozen Task-state persistence, the grouped Stage inventory, and
  deterministic frozen Task-lifecycle derivation and authoritative linear Stage
  routing are the latest reviewed increments. The work-weighted program estimate
  has now been recalculated against the grouped inventory and current product
  acceptance gaps; see the 2026-07-25 calibration below.
  The historical 11-stage label and milestone-count estimate below predate the
  current grouped stage correspondence and must be normalized before they are
  used as a current count.
- Latest product requirements are now captured in
  `docs/requirements/latest-transformation-requirements.md`. This is the
  durable repository source derived from the external `最新的需求.txt`; the
  external transcript is provenance only and is not required for recovery.
- The corrected product goal is a unified, durable Task orchestration entry
  point for Codex, Claude Code, and Kiro with truthful Token/cost accounting,
  resumable workflow state, and non-bypassable quality Gates. UI is optional
  until the CLI-first orchestration path is proven.
- The reviewed `agora-aidlc-foundation@0.1` loop is provisional. It does not
  replace the missing authoritative AI-DLC graph and is not counted as a
  completed transformation stage.
- The authoritative AI-DLC diagram/source is still missing. Do not infer or
  invent it from chat history, Council screenshots, or the provisional method.
- The originating development worktree recorded
  `frontend/pnpm-workspace.yaml` as a user-owned unrelated change. The current
  synchronized worktree was clean before this documentation increment, but the
  ownership constraint remains: do not stage unrelated edits to that file.
- The current CLI root is the repository root, so the real vertical-slice Task
  is in `data/agora.db`, not `backend/data/agora.db`. Additive initialization
  preserved its 2 legacy `sessions` rows and added the Control Plane tables;
  it now contains 1 Task, 2 decisions, and 5 Runs. The ignored backend database
  contains no Tasks and is only a local initialization artifact.
- Latest Windows runtime recovery review gate: Kiro `APPROVE`, Claude Code
  `APPROVE`; focused suite 39 passed; comparable backend suite 317 passed and
  18 deselected; Schema export, compile, diff check, and CLI smoke passed.
- Review gates remain mandatory for subsequent implementation increments.
- The user explicitly approved Plan `plan_cf698b72d0c548ff99b763fef8e6c75b`;
  the authoritative Plan is now `ready_for_implementation` and its approval
  event is recorded in `data/agora.db`.
- The latest increment derives frozen Task lifecycle only from the immutable
  grouped inventory and authoritative Stage, Gate, Attention, invalidation,
  and reconciliation facts. Compatibility Plan state remains outside that
  authority. Do not begin Task Workbench UI work.
- The latest reviewed increment makes the sealed grouped inventory the authority
  for linear Stage activation, runtime selection, and advancement. Kiro and
  Claude Code both returned `APPROVE`; focused suite 106 passed and the complete
  comparable backend suite passed 458 tests with 18 deselected. `.kiro/` and
  historical pytest temp directories are unrelated local artifacts and were not
  staged.
- Latest reviewed increment: explain and enforce the already pinned formal route
  with a hash-sealed per-Run policy decision. It validates Stage/runtime
  capabilities, Task-risk reviewer coverage, contract independence, and
  protected future reviewer budget without runtime substitution or
  methodology-graph changes. Kiro and Claude Code both returned explicit final
  approval; the complete comparable backend suite passed 466 tests with 18
  deselected.
- Latest reviewed increment: a versioned, hash-sealed Task budget amendment for
  a retry blocked only by protected review capacity. It increases the Task/Plan
  total envelope under optimistic versions, preserves Stage allocations,
  reviewer requirements, and historical usage, and records policy snapshots
  before and after the atomic change. Kiro and Claude Code both approved; the
  complete backend suite passed 474 tests with 18 deselected.
- Latest reviewed increment: a versioned, hash-sealed provider usage
  observation for native CLI results. Codex and Claude defaults opt into their
  documented structured formats; Kiro/custom text remains an explicit
  estimate, malformed structured results are unavailable, and only a process
  that never started is exact zero. The additive Run column does not rewrite
  historical ledger rows. Kiro and Claude Code both returned explicit final
  approval; the complete backend suite passed 483 tests with 18 deselected.
- Latest reviewed increment: a read-only, versioned native runtime capability
  observation. It reports exact installed adapter versions where bounded native
  probes succeed, configured model declarations, and hash-bound policy
  capability declarations without persisting state or becoming routing
  authority. Kiro and Claude Code both returned explicit final approval; the
  complete backend suite passed 495 tests with 18 deselected.
- Latest reviewed increment: a versioned pinned-runtime preflight over one
  complete fresh native capability observation. It can only allow or block the
  already sealed route, persists the exact decision with its Run, and rechecks
  registry, policy, command, and resolved launcher argv immediately before
  process creation. It never substitutes a runtime/model or claims provider
  serviceability. Kiro and Claude Code both returned explicit final approval;
  the complete backend suite passed 506 tests with 18 deselected.
- Latest reviewed increment: a read-only Task-scoped preview of that exact
  pinned-runtime decision. `agora task preflight TASK_ID` revalidates the
  already initialized sealed route and routing policy, collects one fresh
  observation, and returns an explicit no-claim/no-persistence/no-spawn
  projection with pinned-route remediation. It never initializes missing
  Task/Stage state or substitutes a runtime/model. Kiro and Claude Code both
  returned explicit final approval; the complete backend suite passed 514
  tests with 18 deselected.
- Current work-weighted transformation estimate (2026-07-25): approximately
  75%, with a practical range of 72%-78%. The backend/control-plane path is
  substantially implemented and independently reviewed, but the authoritative
  full AI-DLC graph, real new-architecture three-runtime end-to-end acceptance,
  consult/adopt semantics, dynamic runtime/model selection and authenticated
  serviceability, parallel/DAG formal routing, and Task Workbench remain
  incomplete. This supersedes the older roughly 70% checkpoint and is not a
  milestone-count claim.

## 2026-07-22 — Native provider usage observation

### Scope and implementation

- [x] Added hash-sealed `ProviderUsageObservation@1.0` plus a checked-in JSON
  Schema. Every observation binds the Run, adapter, provider, structured source
  hash where available, model, duration, Token components/total, USD cost,
  native credits, measurement status, and measurement method.
- [x] Made runtime result formats explicit. Default Codex uses `exec --json`
  JSONL and default Claude print mode uses `--output-format json`; custom
  commands default to plain text unless their format is explicitly configured.
  Kiro remains plain text because its current chat help exposes no stable
  machine-readable result format.
- [x] Added strict, bounded native normalization. Codex reads the final agent
  message and one `turn.completed` usage event, treating cached input and
  reasoning output as subsets. Claude unwraps the semantic `result` and records
  exact input/output/cache Tokens, reported USD cost, model, and duration.
- [x] Kept failure semantics conservative: malformed/truncated structured
  output is unavailable, a started plain-text CLI is only estimated, a lost
  crash-recovery envelope is unavailable, and only a process that provably did
  not start records exact zero Tokens/cost/credits.
- [x] Persisted the sealed observation in an additive nullable Run column and
  revalidated its Run/adapter and aggregate settlement binding on every read.
  Existing Run rows remain `NULL`; append-only reservation/settlement ledger
  rows are not migrated or rewritten.
- [x] Added the observation to unified Run projection and bumped the unified
  Task projection to schema `8.0`. Routing policy, Stage runtime/model
  selection, and historical ledger entries are unchanged.
- [x] Corrected compatibility cost aggregation for mixed providers: any
  unavailable settled Run makes the Task cost total unavailable instead of
  presenting a partial known cost as an exact complete total.

### Native capability evidence

- Claude Code `2.1.217` was reconnected and exercised with a low-cost
  authenticated JSON probe. It returned exact input, output, cache-creation,
  cache-read Tokens, `total_cost_usd`, `duration_ms`, and `modelUsage`; the
  implemented fixture mirrors those observed fields.
- Kiro CLI `2.13.0` help was inspected. Chat output has no structured result
  mode, so Agora does not parse its human credits footer and leaves exact cost
  or credits unavailable.
- `codex` did not resolve in the current PowerShell `PATH`, so no local Codex
  smoke was claimed. The parser and regression fixture follow the official
  Codex manual's documented `exec --json` JSONL events and
  `turn.completed.usage` fields. This documentation is provenance, not a
  runtime dependency.

### Verification and review log

- Provider usage plus protocol/schema focused suite: 39 passed.
- Orchestration and formal protocol targeted suite: 93 passed.
- Complete non-integration backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 483 passed, 18 deselected, with one existing
  Starlette/httpx warning and the existing Windows Proactor cleanup warning.
- Protocol Schema export/check, isolated `compileall`, `git diff --check`, and
  `agora task --help`: passed.
- Kiro protocol/methodology review: `KIRO_APPROVE`; no high/medium findings.
  Its two low findings were fixed by removing the dead legacy estimator and
  explicitly testing/documenting non-zero Codex reasoning Tokens as an output
  subset. Targeted re-review returned `KIRO_APPROVE`.
- Claude Code correctness/security review: `CLAUDE_APPROVE`; no high/medium
  actionable findings across parsing, provenance, migration, recovery,
  aggregation, bounds, and regression coverage.
- No frontend or authenticated HTTP contract changed; frontend validation was
  not required. `.kiro/` and historical pytest temp directories remain
  unrelated local artifacts and were not modified or staged.

### Next safe action

After the reviewed commit, define the smallest read-only, versioned native
runtime capability-observation contract: installed adapter/version and
declared model/capability availability only, bound to collection provenance.
It must not substitute runtimes/models, alter the sealed Stage inventory or
pinned route, or infer the missing authoritative AI-DLC graph. Dynamic
substitution, authenticated HTTP, the missing authoritative AI-DLC graph,
parallel/DAG routing, and Task Workbench UI remain deferred.

## 2026-07-23 - Native runtime capability observation (reviewed)

### Scope and implementation

- [x] Added hash-sealed `NativeRuntimeCapabilityObservation@1.0` plus a
  deterministic checked-in JSON Schema.
- [x] Bound every observation to collection time, collector/platform, the
  configured runtime-registry hash, and the exact pinned routing-policy
  declaration ID/version/hash.
- [x] Kept installation, exact version, declared model availability, and
  declared capability availability separate. Empty model declarations remain
  unavailable rather than inferred from CLI defaults or provider output.
- [x] Added bounded no-shell default version probes for Codex, Claude, and Kiro;
  custom runtime commands require an explicit bounded `version_command`.
- [x] Added 10-second timeout, terminate/kill escalation, bounded concurrent
  stream capture, post-stop drain bounds, cancellation propagation, sibling
  cleanup, proxy scrubbing, and fail-closed Windows wrapper/lookup behavior.
- [x] Added `agora task capabilities` as a read-only JSON command that does not
  build the Task service, initialize SQLite, persist observations, or change
  the unified Task projection.
- [x] Preserved the sealed inventory and pinned routing policy as the only
  dispatch authority; every observation carries `routing_authority=false`.
- [x] Kept live provider/model discovery, authentication/serviceability probes,
  runtime/model substitution, HTTP, the missing authoritative AI-DLC graph,
  parallel/DAG routing, and Task Workbench UI deferred.

### Verification and review log

- Initial capability, provider-usage, orchestration, and protocol suite:
  92 passed.
- Pre-review complete system-Temp non-integration backend suite excluding the
  deferred static-export Web UI test: 489 passed, 18 deselected.
- Kiro protocol/methodology review returned `CHANGES_REQUESTED` for missing
  real-child timeout/reap and cancellation-propagation regressions. Both were
  added, along with direct `uninspectable`/`not_configured` coverage, proxy
  scrubbing, and a shared adapter/provider declaration; focused suite:
  96 passed.
- Kiro targeted re-review returned `KIRO_APPROVE`; no HIGH/MEDIUM findings.
- Claude Code correctness/safety review returned `CLAUDE_APPROVE`; no
  HIGH/MEDIUM findings. Its actionable LOW observation about sibling probe
  cleanup was nevertheless fixed: any probe exception now cancels and awaits
  every unfinished sibling, executable lookup `OSError` is fail-closed, and
  both paths have direct regressions.
- Final focused capability, provider-usage, orchestration, and protocol suite:
  98 passed.
- Final narrow Kiro and Claude Code confirmations both returned explicit
  `KIRO_APPROVE` / `CLAUDE_APPROVE`; no HIGH/MEDIUM findings remain.
- Final complete system-Temp non-integration backend suite excluding
  `tests/test_web_ui.py`: 495 passed, 18 deselected, with the existing
  Starlette/httpx deprecation warning and Windows Proactor cleanup warning.
- Final Protocol Schema export/check, isolated `compileall`, staged and
  unstaged `git diff --check`, `agora task --help`, and validated capability
  JSON smoke passed.
- Final read-only CLI observation reported Claude Code `2.1.217` and Kiro CLI
  `2.14.0` as exact versions, Codex as `not_found` in the current PowerShell
  `PATH`, all undeclared models as unavailable, and
  `routing_authority=false`.
- No frontend or authenticated HTTP contract changed; frontend validation was
  not required. `.kiro/` and historical pytest temp directories remain
  unrelated local artifacts and were not modified or staged.

### Next safe action

Define the smallest versioned pinned-runtime preflight decision over a fresh
native capability observation. It may only allow or block the already sealed
route, must bind the exact observation, runtime command, and policy declaration
hashes, and must collect outside the SQLite write transaction with an immediate
pre-spawn recheck. It must not substitute a runtime/model, treat declarations
as live provider serviceability, or mutate the sealed methodology graph. Live
provider/model catalogs, authentication probes, dynamic substitution,
authenticated HTTP, the missing authoritative AI-DLC graph, parallel/DAG
routing, and Task Workbench UI remain deferred.

## 2026-07-24 - Pinned native runtime preflight (reviewed)

### Scope and implementation

- [x] Added hash-sealed `PinnedRuntimePreflightDecision@1.0` plus a
  deterministic checked-in JSON Schema.
- [x] Bound the exact Task, project, Run, sealed inventory route, per-Run
  routing-policy decision, reviewed policy declaration, complete fresh native
  capability observation, configured runtime registry, command template, and
  audited resolved launch target.
- [x] Limited the decision to allow or block the already pinned runtime. It
  carries explicit false markers for route-selection authority, runtime/model
  substitution, and provider serviceability verification.
- [x] Required six deterministic checks: route binding, 60-second observation
  freshness, observation integrity, command binding, local installation, and
  capability-declaration binding. An unavailable version remains informational
  because the reviewed policy has no version constraint.
- [x] Collected the native observation outside SQLite write transactions. A
  blocked initial decision creates no Run, reservation, Gate mutation, or
  process.
- [x] Persisted an allowed decision in an additive nullable Run column, included
  it in the sealed Context Pack, and upgraded the unified Task projection to
  schema `9.0`. Historical Runs remain `NULL`.
- [x] Added a second recheck inside the Runner immediately before
  `asyncio.create_subprocess_exec`. Expiry or changed registry, policy, command,
  or resolved launch target fails before process creation and settles an
  already claimed formal Run with exact-zero process-not-started usage.
- [x] Kept live provider/model discovery, authentication/serviceability claims,
  dynamic substitution, authenticated HTTP, the missing authoritative AI-DLC
  graph, parallel/DAG routing, and Task Workbench UI deferred.

### Verification and review log

- Focused preflight, capability, provider-usage, formal orchestration, frozen
  protocol, Stage routing/lifecycle/inventory, protocol Run, and Registry suite:
  221 passed.
- Complete system-Temp non-integration backend suite excluding the deferred
  static-export Web UI test: 506 passed, 18 deselected, with the existing
  Starlette/httpx deprecation warning and Windows Proactor cleanup warning.
- Protocol Schema export/check, isolated `compileall`, `git diff --check`, and
  `agora task --help`: passed.
- Live read-only `agora task capabilities` smoke passed. It observed Claude
  Code `2.1.217` and Kiro CLI `2.14.0` as exact installed versions, while Codex
  remains `not_found` in the current PowerShell `PATH`. A real Codex-pinned
  route will therefore block before Run claim on this shell; no substitution
  is permitted.
- Direct regressions cover initial missing-runtime no-claim behavior,
  missing-adapter observation, observation expiry, post-claim command drift
  before spawn, exact-zero settlement, nullable migration, hash tamper,
  resealed Run forgery, and Context/projection bindings.
- Kiro protocol/methodology/reconciliation review returned `KIRO_APPROVE` with
  no HIGH/MEDIUM defects. Its LOW Runner-contract and test-callback notes were
  fixed; targeted re-review returned `KIRO_FIX_APPROVE`.
- Independent Claude Code correctness/safety/regression review returned
  `CLAUDE_APPROVE` with no HIGH/MEDIUM defects. Its LOW notes were fixed by
  clarifying that the resolved binding hashes launcher argv rather than binary
  content and returning a structured blocked decision when an injected
  observation omits the pinned adapter. Targeted re-review returned
  `CLAUDE_FIX_APPROVE`.
- No frontend or authenticated HTTP contract changed; frontend validation is
  not required. `.kiro/` and historical pytest temp directories remain
  unrelated local artifacts and were not modified or staged.

### Next safe action

After committing and pushing this reviewed checkpoint, define the smallest
read-only Task-scoped preflight preview that
reuses this exact decision without claiming a Run, persisting an observation,
or spawning a runtime. It may explain how to remediate the pinned route but
must not substitute a runtime/model, infer provider serviceability, or alter the
sealed methodology graph. Live provider/model catalogs, authenticated
serviceability probes, dynamic substitution, authenticated HTTP, the missing
authoritative AI-DLC graph, parallel/DAG routing, and Task Workbench UI remain
deferred.

## 2026-07-25 - Task-scoped runtime preflight preview (reviewed)

### Scope and implementation

- [x] Added `agora task preflight TASK_ID` as a read-only JSON entry point over
  an already initialized formal Task route.
- [x] Added versioned `RuntimePreflightPreview@1.0`, containing the exact sealed
  `PinnedRuntimePreflightDecision@1.0` plus literal `preview_only=true`,
  `run_claimed=false`, `observation_persisted=false`, and
  `runtime_spawned=false` markers.
- [x] Reused the same dispatch-input validation, routing-policy preview,
  capability collector, and preflight derivation as the real dispatch path.
  Preview selects `repair=false`, so it cannot initialize an inventory,
  activate a Stage, call `resume`, claim a Run, configure a Gate, persist an
  observation, or spawn the Task runtime.
- [x] Gave every preview a synthetic `preview_*` identity and enforced that
  prefix in the read model in addition to Task/project scope bindings. Real
  dispatch derives a different Run identity, policy, observation, and
  preflight.
- [x] Added deterministic remediation for all six preflight checks. Advice only
  repairs the already pinned route, command, installation, observation, or
  reviewed declaration; it never substitutes a runtime/model or claims
  provider serviceability.
- [x] Preserved policy ordering: a blocked routing policy stops before native
  capability probing. A blocked preflight returns JSON with CLI exit 2; an
  allowed informational preview returns exit 0.

### Verification and review log

- Initial preflight plus formal orchestration focused suite: 59 passed.
- Expanded protocol freeze, Control Plane Run, Agent Adapter, capability,
  usage, inventory, lifecycle, Stage routing, Task orchestration, and formal
  orchestration suite: 236 passed.
- Post-review focused suite: 64 passed.
- Final system-Temp non-integration backend suite excluding the deferred
  static-export Web UI test: 514 passed, 18 deselected, with the existing
  Starlette/httpx warning and Windows Proactor cleanup warning.
- Protocol Schema export/check, isolated `compileall`, `git diff --check`, and
  `agora task preflight --help`: passed.
- Direct regressions compare complete SQLite dumps before and after allowed,
  blocked, stale-observation, policy-blocked, and missing-inventory previews;
  they also verify no Runner prompt, no Run, no automatic repair, allowed and
  blocked CLI exit semantics, Task-scope forgery rejection, and resealed
  non-preview identity rejection.
- Kiro protocol/methodology/reconciliation review returned `KIRO_APPROVE` with
  no HIGH/MEDIUM findings. Its actionable LOW blocked-CLI exit coverage was
  added; targeted confirmation returned `KIRO_FIX_APPROVE` with no remaining
  actionable finding.
- Independent Claude Code correctness/safety/regression review returned
  `CLAUDE_APPROVE` with no HIGH/MEDIUM findings. Its LOW defense-in-depth and
  adversarial-coverage notes produced the preview-identity guard,
  policy-blocked-before-probe, stale observation, scope tamper, and resealed
  identity regressions. Targeted confirmation returned `CLAUDE_FIX_APPROVE`
  with no remaining actionable finding.
- No frontend or authenticated HTTP contract changed; frontend validation is
  not required. `.kiro/` and historical pytest temp directories remain
  unrelated local artifacts and were not modified or staged.

### Progress calibration and next safe action

- Work-weighted transformation progress is now approximately 75%, with a
  reasonable 72%-78% range. This is based on the checked-in backend/control
  plane plus current acceptance gaps, not the obsolete historical stage count.
- The root `data/agora.db` still contains the earlier compatibility demonstration
  Task (one Task, one approved Plan, three compatibility Stages, and five Runs)
  rather than a completed new-architecture formal acceptance Task. Source
  implementation progress therefore must not be mistaken for end-to-end
  product acceptance.
- After committing and pushing this reviewed checkpoint, the next safe action
  is to restore the audited pinned Codex launcher in the current PowerShell
  environment and, with an explicit bounded provider-spend authorization,
  execute one fresh concrete CLI-first formal Task through preflight, Run,
  independent review, Gate, intervention/resume, and handoff. Record any
  acceptance gaps before adding authenticated serviceability probes, dynamic
  runtime/model substitution, the missing authoritative AI-DLC graph,
  parallel/DAG routing, or Task Workbench UI.

## 2026-07-18 — Latest transformation requirements recovery

- [x] Recovered and fully read the user-provided external
  `C:\Users\wuche\Downloads\最新的需求.txt`.
- [x] Removed transient chat/tool output and captured the effective product
  requirements in `docs/requirements/latest-transformation-requirements.md`.
- [x] Recorded the corrected priority: unified Task orchestration, three-runtime
  role/risk/budget routing, truthful Token/cost ledger, durable recovery, and
  mandatory quality Gates; UI is a later optional projection.
- [x] Reconciled requirements against the current provisional orchestration
  implementation at `2750dfe` and separated covered capabilities from gaps.
- [x] Confirmed the authoritative full AI-DLC diagram/source is still absent;
  `agora-aidlc-foundation@0.1` remains explicitly provisional.
- [x] Confirmed the premature Task Workbench demo remains reverted. No UI or
  implementation scope was added in this documentation recovery.
- [x] Confirmed `backend/data/agora.db` is absent; the legacy root database has
  no Task/Run tables and was not modified.
- [x] Kiro CLI documentation review: `APPROVE`; no high/medium findings. The
  current baseline no longer repeats the inherited ambiguous 11-stage count,
  and this documentation-only increment requires diff/consistency checks rather
  than code, Schema, or frontend execution.
- [x] Claude Code documentation review: `APPROVE`; no high/medium findings.
  Corrected the inherited milestone-count wording and stale Task Workbench
  revert hash, and verified with SQLite that `data/agora.db` contains only the
  `sessions` table.
- [x] Review and commit this documentation-only recovery increment (the commit
  containing this snapshot).

### Transformation-stage correspondence

- Completed stage 1, protocol/domain freeze: retained unchanged as the
  authority for Context/Handoff, Artifact/Evidence/Approval, Gate, and Run
  semantics.
- Completed stage 2, persistence/Registry: retained unchanged as the
  authoritative storage foundation.
- Bounded Control Plane API: must include the unified Task
  status/progress/result projection and command boundary required by the new
  requirements.
- Runner/Agent Adapter and three-runtime orchestration stages: must implement
  explainable role/risk/capability/budget routing and real usage adapters where
  available.
- Context/Handoff and AI-DLC integration stages: must replace the provisional
  loop only after the authoritative method source is recovered and versioned.
- Integrated console UI: reinterpreted as an optional Task Workbench projection
  after CLI-first end-to-end proof, not as the next implementation action.

### Resume instruction

On the next session, strictly execute `AGENTS.md`, read this file and
`docs/requirements/latest-transformation-requirements.md`, inspect Git status,
and preserve the documentation changes if they remain uncommitted. Do not start
UI work. The next implementation decision must be based on a concrete Task
contract and the actual SQLite Task/Run state if a current
`backend/data/agora.db` becomes available.

## 2026-07-18 — Concrete Task contract vertical slice (active)

### Scope

- [x] Added a strict, versioned concrete Task contract model with bounded roles,
  ordered workflow, Context/Handoff expectations, acceptance criteria, and
  required Artifact/Evidence/Gate templates.
- [x] Added the first checked-in contract at
  `docs/examples/bounded-control-plane-api-task-contract.json` for the bounded
  Control Plane API planning and review slice.
- [x] Added `agora task start --contract PATH`; the legacy title-based start
  remains supported.
- [x] Persisted canonical contract content, identity, schema version, and
  SHA-256 with the authoritative Task and supplied the bounded contract to each
  runtime prompt.
- [x] Failed closed when contract stage order or runtime assignment diverges
  from the pinned provisional methodology.
- [x] Added contract parsing, size, semantic-reference, alignment,
  persistence, prompt, and CLI regression coverage.
- [x] Ran a real CLI-first Task through the Agora entry point; no native CLI
  files or state were modified.
- [x] Kiro methodology/reconciliation review: `APPROVE`; no high/medium
  findings.
- [x] Claude Code correctness/safety review: `APPROVE`; no high/medium
  findings. Its two actionable low observations were fixed and await targeted
  confirmation before commit.
- [x] Kiro targeted review-fix confirmation: `APPROVE`; no high/medium findings.
- [x] Claude Code targeted review-fix confirmation: `APPROVE`; no high/medium
  findings.
- [x] Commit the reviewed concrete Task-contract increment locally (the commit
  containing this snapshot).

### Verification and vertical-slice result

- Focused orchestration suite after review fixes: 35 passed.
- Comparable non-integration backend suite excluding `tests/test_web_ui.py`:
  320 passed, 18 deselected, with 2 existing Windows sandbox failures caused by
  tests that write literal `/tmp` paths.
- Protocol Schema export check, Python compile, and `git diff --check`: passed.
- Kiro review: `APPROVE`; its low observations were cosmetic CRLF warnings,
  harmless duplicate canonical serialization, and the honestly deferred
  instantiation of formal Artifact/Evidence/Gate records.
- Claude review: `APPROVE`; it identified a low worst-case prompt-allocation
  mismatch and a low role-label alignment gap. Contract prompts now reserve a
  fixed bounded allocation for compact verified prior results, avoid duplicating
  contract goal/acceptance content, reduce the contract allocation to 8,000
  UTF-8 bytes, and require exact pinned methodology role plus runtime alignment.
  Added worst-case prompt and both alignment-branch regressions.
- Real Task: `task_ea9c19f4794c447c8fe77aaf8a3b15d1`; Plan
  `plan_cf698b72d0c548ff99b763fef8e6c75b`.
- Codex solution-design Run exited zero and returned a schema-valid semantic
  `blocked` result, proving that process success did not advance the Stage.
- Usage was reserved at 13,500 Tokens and settled at an estimated 4,172;
  monetary cost remains `unavailable`, never zero. Claude and Kiro remained
  pending and were not incorrectly dispatched.
- The blocking missing authority is an approved inbound principal, permission,
  and project-membership policy. The design also requires a deliberate choice
  to authorize repository-wide invalidation or defer it from the task-scoped
  API, and must never accept a caller-supplied Stage dependency graph.

### Recovery note and next safe action

The original note incorrectly identified `backend/data/agora.db` as the live
database. Reconciliation in the next increment verified that root-launched CLI
state is in `data/agora.db`; preserve that database and use explicit retry
rather than creating a duplicate Run. UI remains deferred.

## 2026-07-18 — Explicit Task decisions and completed planning slice (active)

### Scope

- [x] Recorded the user's approved API boundary in
  `docs/architecture/control-plane-api-access-policy-v1.md`: fail-closed Bearer
  authentication with external credential references, stable principals,
  explicit permissions, and project memberships.
- [x] Deferred repository/ref-wide `invalidate_inventory` from the bounded
  Task API and prohibited caller-supplied Stage dependency graphs.
- [x] Added additive, immutable, versioned `orchestration_decisions` persistence
  and `agora task decide` without mutating the pinned Task contract or its hash.
- [x] Made identical latest decisions idempotent; changed decisions append a
  new version; active decision context is bounded and secret-like text is
  redacted before persistence.
- [x] Exposed decision history through authoritative status and supplied only
  the latest version per key to subsequent runtime prompts.
- [x] Limited decisions to a blocked Plan/current Stage and updated Plan version
  plus Task audit event atomically.
- [x] Replaced the full contract prompt copy with a hash-bound Stage projection
  and expanded the bounded verified-prior-result allocation.
- [x] Added bounded format-only extraction of exactly one valid semantic JSON
  object from prose/fences; multiple valid objects or excessive candidates fail
  closed without semantic repair.
- [x] Continued the existing Task through explicit decisions and retries; no
  duplicate Task was created.
- [x] Kiro methodology/reconciliation review: `APPROVE`; no high/medium
  findings.
- [x] Claude Code correctness/safety review: `APPROVE`; no high/medium
  findings.
- [x] Commit the reviewed explicit-Task-decision increment locally (the commit
  containing this snapshot).

### Verification and real Task result

- Focused orchestration suite: 41 passed.
- Comparable non-integration backend suite with an isolated system-Temp
  `--basetemp`: 326 passed, 18 deselected, with only the 2 existing literal
  `/tmp` Windows sandbox failures. Temporary test directories were removed.
- Protocol Schema export check, isolated Python compile, and `git diff --check`:
  passed.
- Kiro confirmed the policy/decision/contract/state/Evidence/Approval
  separation, additive migration, bounded Context, format-only recovery,
  recovery semantics, and preserved human Gate; `APPROVE`.
- Claude confirmed SQLite FK enforcement, atomic decision/audit writes,
  concurrent idempotency, redaction, bounded parser complexity, Stage projection
  truthfulness, CLI compatibility, and regression coverage; `APPROVE`.
- Task `task_ea9c19f4794c447c8fe77aaf8a3b15d1` remains bound to Plan
  `plan_cf698b72d0c548ff99b763fef8e6c75b`; its pinned contract hash did not
  change.
- Persisted user decisions:
  `inbound_authorization_policy@1` and
  `repository_invalidation_scope@1`.
- Codex `solution_design` attempt 2: semantic `pass`.
- Claude `correctness_review` attempt 1 returned prose plus a schema-valid
  `needs_work` object. The old first-brace extraction encountered an earlier
  `{project_id}` and correctly failed closed as a Schema blocker. After the
  bounded format-only extraction and richer verified handoff fix, explicit
  attempt 2 returned semantic `pass`.
- Kiro `methodology_review` attempt 1: semantic `pass`.
- Plan state: `awaiting_approval`. All three Stages passed; human approval is
  intentionally not inferred from the user's earlier policy decisions.
- Total usage: estimated 21,665 Tokens, 8,335 remaining; monetary cost remains
  `unavailable`, never zero.

### Recovery note and next safe action

The authoritative runtime database for this Task is `data/agora.db` when Agora
is launched from the repository root. Preserve it. The next action after the
reviewed code commit is for the user to review the three semantic results and
explicitly run or authorize `agora task approve`; implementation remains
forbidden until that human Gate passes.

## 2026-07-18 — Bounded Control Plane API (active)

### Scope

- [x] Recorded the user's explicit human approval of the three-runtime Plan;
  implementation began only after it reached `ready_for_implementation`.
- [x] Added fail-closed Bearer authentication backed only by external secret
  references, stable principals, explicit permissions, and project membership.
- [x] Added Task-scoped Gate configuration, Artifact/Evidence/Approval
  registration, active-Evidence selection, Gate evaluation, bounded projection,
  paginated events, and scoped entity reads under `/api/control-plane/`.
- [x] Bound audit actors and Approval identity to the authenticated principal;
  rejected project/Task payload mismatches and returned non-enumerating 404s.
- [x] Kept `invalidate_inventory` outside the HTTP boundary and did not accept a
  caller-supplied Stage dependency graph.
- [x] Offloaded all synchronous SQLite work from FastAPI's event loop and
  sanitized conflict, validation, unavailable, and unexpected error responses.
- [x] Added explicit collection bounds/totals and strict bounded request models.
- [x] Added API acceptance coverage for fail-closed auth, external secret
  resolution, permissions, project isolation, audited Gate flow, bounded reads,
  and scope spoofing.
- [x] Obtained Kiro methodology/boundary review and independent Claude Code
  correctness/security review; fixed all actionable findings before commit.
- [x] Commit the reviewed bounded Control Plane API increment locally (the
  commit containing this snapshot).

### Verification and review log

- Focused Control Plane API, Registry, and Attention suite after review fixes:
  39 passed.
- Full non-integration backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 339 passed, 18 deselected, with only existing
  Starlette/httpx, pytest-cache, and Windows event-loop cleanup warnings.
- A first repository-local basetemp run passed 333 tests and produced one false
  failure because its nominal non-Git fixture inherited the parent repository;
  the isolated system-Temp rerun passed, as did the final full suite.
- Protocol Schema export check, isolated Python compile, and `git diff --check`:
  passed.
- Kiro initial review: `APPROVE`; no high/medium methodology or boundary
  findings. Its actionable low observations on ambiguous credential mappings,
  actor-path coverage, malformed configuration, and lock translation were
  addressed with fail-closed validation and regressions.
- Claude Code initial review: `CHANGES_REQUESTED`; no high findings and two
  medium read-path findings. The projection no longer expires Attention under
  read permission and now returns Task, registry collections, Attention,
  events, totals, and collection windows from one explicit SQLite read
  snapshot.
- Claude low observations were also addressed: every bounded collection now
  reports its limit/offset/total, and cross-Gate next-safe-action selection uses
  global blocker priority plus deterministic requirement/Gate tie-breaking.
- The suggested configure-Gate CAS was verified as non-actionable because Gate
  configuration is create-once and immutable: identical replay is idempotent,
  while any changed Stage or requirements fail with a conflict.
- Kiro final targeted re-review: `APPROVE`; no high/medium findings remain.
- Claude Code final targeted re-review: `APPROVE`; both medium findings and all
  actionable low findings are resolved with dedicated adversarial coverage.
- Five generated repository-local pytest basetemp directories remain excluded
  and untracked because their sandbox-created ACLs deny deletion even after an
  escalated cleanup attempt; no implementation file depends on them.

### Next safe action

After the reviewed commit, begin the smallest Runner/Agent Adapter integration
that binds execution to the frozen Run dimensions and Context/Handoff boundary
without changing protocol semantics. UI and repository-wide invalidation
remain deferred.

## 2026-07-20 – Runner/Agent Adapter boundary (active)

### Scope

- [x] Added a tool-neutral terminal Runner observation that keeps process and
  transport facts separate from schema and semantic outcomes.
- [x] Added a fail-closed Agent adapter that accepts only an exact JSON Handoff
  Pack or one whole-document Markdown-fence removal; it never extracts JSON
  from prose or repairs semantic content.
- [x] Bound accepted Handoffs to the sealed Context Pack's project, Task,
  Stage, Run, input Artifact, required-output, and forbidden-constraint values.
- [x] Rejected cross-scope Artifact/Evidence producers and Native State project
  mismatches before exposing a Handoff for registration.
- [x] Added a thin bridge from the existing CLI `RuntimeResult` to the frozen
  adapter without changing the provisional orchestration state machine.
- [x] Added adversarial coverage for exit-zero protocol failure, semantic
  blockers, non-zero semantic results, duplicate JSON keys, prose extraction,
  UTF-8, size limits, hash tampering, context spoofing, timeout, cancellation,
  interruption, launch failure, and transport failure.
- [x] Run the complete verification set and Schema consistency check.
- [x] Obtain Kiro protocol/methodology review and independent Claude Code
  correctness/safety review; fix all actionable findings.
- [x] Commit only after both review gates approve (the commit containing this
  snapshot).

### Verification and review log

- Initial Agent Adapter, frozen-protocol, and provisional-orchestration suite:
  92 passed.
- Kiro initial review: `CHANGES_REQUESTED`; no high findings and one medium
  fail-closed defect. A schema-valid `cancelled` Handoff from a normally exited
  process could raise the frozen Run validator instead of returning a
  structured protocol failure.
- The contradiction now returns `HANDOFF_PROCESS_MISMATCH`,
  `protocol_failed`, semantic `blocked`, and required Attention. A normally
  exited semantic `failed` Handoff remains valid and distinct from process
  failure. Started/no-exit Runner results now map to `interrupted`, and the
  order-sensitive Context echo contract is explicit and tested.
- Kiro targeted re-review: `APPROVE`; no high/medium findings. Its two low
  hardening suggestions were addressed by narrowing the contradiction branch
  and adding a real multi-element reorder regression.
- Claude Code independent review: `APPROVE`; no implementation defect. Its one
  medium regression-coverage gap was fixed with sealed, schema-valid Evidence
  scope and Native State project-spoofing cases. Additional low-edge coverage
  now includes missing input, non-finite JSON, both allowed fence forms, and a
  changed required-output echo.
- Final Kiro targeted review: `APPROVE`; no actionable findings.
- Final Claude Code targeted review: `APPROVE`; the added spoofing cases reach
  the intended context-mismatch branches and expose no Handoff.
- Final Agent Adapter, frozen-protocol, and provisional-orchestration suite:
  101 passed.
- Full non-integration backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 366 passed, 18 deselected, with only existing
  Starlette/httpx, pytest-cache, and Windows event-loop cleanup warnings.
- Protocol Schema export check, isolated Python compile, and `git diff --check`:
  passed. No frontend files or shared API contracts changed, so frontend
  validation was not required.

### Next safe action

After the reviewed commit, generate and persist a sealed Context Pack for one
concrete orchestration Run, then consume the validated Handoff through the
adapter without allowing it to write Task, Stage, or Gate state directly. UI
remains deferred.

## 2026-07-20 - Context/Handoff and Gate settlement boundary (active)

### Scope

- [x] Added an additive immutable `protocol_runs` ledger binding one sealed
  Context Pack and at most one accepted Handoff Pack to Task, Stage, Run, and
  Gate identity.
- [x] Added replay-safe protocol Run start that verifies every input Artifact
  binding and moves only the authoritative Control Plane Stage to `running`.
- [x] Added one atomic settlement boundary for Run protocol state, accepted
  Handoff, Artifact/Evidence registration, active-Evidence selection, formal
  Gate evaluation, Attention creation, and Stage settlement.
- [x] Made formal Gate passage plus semantic success the only path from a
  running Stage to `completed`; exit code and Agent suggestion cannot advance
  the Stage or supply `next_safe_action`.
- [x] Preserved existing active Gate Evidence on protocol failure and created a
  durable Attention item tied to the protocol Run through bounded context.
- [x] Preserved multi-source active Evidence for Gate requirements not
  re-observed by the current Handoff and replaced prior Evidence only for a
  requirement explicitly represented in the Handoff.
- [x] Added the frozen contract's missing deterministic impact-analysis
  Attention to Approval invalidation, atomically with Approval/Gate/Stage
  changes and the replay receipt.
- [x] Added sealed-payload read verification, exact Gate Evidence scope checks,
  transaction rollback, restart replay, operation-key conflict, missing input,
  process-exit independence, semantic-blocker, and protocol-failure coverage.
- [x] Run the complete verification set and Schema consistency check.
- [x] Obtain Kiro protocol/methodology review and independent Claude Code
  correctness/safety review; fix all actionable findings.
- [x] Commit only after both review gates approve (the commit containing this
  snapshot).

### Verification and review log

- Initial Context/Handoff settlement, Registry, and Agent Adapter suite:
  52 passed; post-Kiro-fix suite: 55 passed.
- Isolated Python compile and `git diff --check`: passed.
- Kiro protocol/methodology review: `APPROVE`; no blocking findings. Its three
  medium conformance observations identified missing invalidation Attention,
  replacement of unrelated multi-source active Evidence, and the pre-existing
  independent Registry Gate writer surface. The first two are fixed with
  atomic regressions. The architecture now explicitly records that independent
  Registry operations cannot settle a Stage and that Run settlement always
  re-evaluates the Gate, so they cannot bypass completion.
- Kiro low observations: schema-valid failed Handoffs no longer churn the Gate;
  infrastructure retry/Attention policy and global operation-receipt retention
  remain explicitly deferred orchestrator/operations concerns.
- Claude Code independent correctness/safety review: `APPROVE`; no high or
  blocking defects. Its medium observation is the already documented
  repository/ref-wide invalidation write-lock bound, which remains outside the
  API pending load testing or a semantics-preserving batching design.
- Claude's actionable low audit-order observation is fixed by paginating
  append-only Control Plane events in SQLite insertion order; a settlement
  regression proves an Agent timestamp cannot place Artifact registration
  before Context sealing. Its dead-import observation is also fixed.
- Final Kiro targeted re-review: `APPROVE`; all five post-review invariants
  hold and no actionable findings remain.
- Final Claude Code targeted re-review: `APPROVE`; both prior low findings are
  resolved, the Kiro fixes remain correct, and no new defect was introduced.
- Final focused settlement, Registry, and Agent Adapter suite: 55 passed.
- Full non-integration backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 378 passed, 18 deselected, with only existing
  Starlette/httpx, pytest-cache, and Windows event-loop cleanup warnings.
- Protocol Schema export check, isolated Python compile, and `git diff --check`:
  passed. No frontend or shared API contract changed, so frontend validation
  was not required.

### Next safe action

The mandatory verification and independent review gates are complete for this
commit. The requested current-state architecture diagram was delivered before
the next increment. Continue with the formal orchestration wiring below; UI
remains deferred.

## 2026-07-20 - Formal protocol orchestration wiring (active)

### Scope

- [x] Added an explicit `--protocol-v1` path for `task start --run`, `next`,
  `run`, and `retry`; legacy Tasks keep their existing behavior unless the
  formal path is selected.
- [x] Added a bounded Context builder that verifies the pinned Task contract,
  resolves a clean Git ref/commit, maps Stage requirements and exact Gate
  Evidence bindings, carries formal prior Artifact references, and seals the
  result with the frozen Context Pack model.
- [x] Made required output Artifact identities unique per Task/Run so reusable
  contract templates cannot collide in the global immutable Registry.
- [x] Connected the existing read-only Runner to the fail-closed Agent Adapter,
  rejected configured Gate Evidence scope/kind spoofing before Registry writes,
  and called the reviewed atomic Control Plane settlement boundary.
- [x] Kept the provisional 0.5 Plan/Run tables only as dispatch, Token ledger,
  and compatibility status projection; they advance only after an authoritative
  completed Stage receipt and never parse the legacy semantic JSON in this path.
- [x] Added replay-safe retry preparation, including staling a passed Gate when
  a semantically blocked/failed Stage is retried.
- [x] Added recovery for start rejection, a disappeared process, and the crash
  window after authoritative settlement but before compatibility projection;
  resume never redispatches or duplicates usage settlement.
- [x] Preserved cancellation as a distinct formal and operational result.
- [x] Added CLI, clean-revision, three-Stage success, exit-zero protocol
  failure, Evidence spoofing, cancellation, retry, and crash-recovery tests.
- [x] Run the complete verification set and Schema consistency check.
- [x] Obtain Kiro protocol/methodology review and independent Claude Code
  correctness/safety review; fix all actionable findings.
- [x] Commit only after both review gates approve (the commit containing this
  snapshot).

### Current verification log

- Focused orchestration, Agent Adapter, and protocol settlement regressions:
  90 passed before independent review.
- New formal orchestration suite: 9 passed.
- Full non-integration backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 388 passed, 18 deselected, with only the existing
  Starlette/httpx and Windows event-loop cleanup warnings. The first sandboxed
  run produced only the two known literal `\tmp` failures; an escalated run
  without an isolated basetemp encountered the pre-existing denied-ACL pytest
  temp root. The final run used a fresh explicit system-Temp basetemp and passed.
- Resume audit found and fixed a pre-review partial-start recovery gap: when the
  operational reservation committed but formal Run start failed, the Control
  Plane Stage correctly remained `ready` while the compatibility Stage blocked;
  `retry --protocol-v1` now accepts that split state and repairs the compatibility
  projection without attempting an invalid authoritative transition. Focused
  orchestration, Agent Adapter, and settlement regression rerun: 49 passed.
- Kiro review pass 1: `CHANGES_REQUESTED`; no high findings and one medium
  immutable-Gate recovery gap. A retry after the clean repository ref/commit
  changed could reset the operational projection before immutable Gate
  configuration rejected the new Evidence scope. Formal retry now resolves and
  compares the configured repository/ref/commit before either projection is
  mutated, rejects revision rebinding with an explicit new-Task action, and has
  a changed-revision regression. Gate configuration now also follows the
  documented operational-reservation-first ordering. Post-fix focused suite:
  50 passed.
- Kiro targeted re-review: `APPROVE`; the revision comparison precedes all
  writes, both same-revision retry branches remain recoverable, Gate setup now
  follows operational reservation, and the changed-revision regression reaches
  the intended no-mutation branch. Its two residual observations are low and
  non-blocking: the durable-record reconciliation after an ambiguous start
  exception, and intentionally carrying bounded prior-attempt Artifacts into a
  retry Context.
- Claude Code independent correctness/safety review: `APPROVE`; no high or
  medium findings. It independently confirmed the transaction-gap recovery,
  retry revision guard, exit-code/Gate separation, Evidence scoping,
  cancellation, idempotency, redaction, and bounds. Its two low informational
  observations concern only an unexpected exception outside the Runner's
  normal result contract and cosmetic compatibility-projection labeling.
- Final post-review full backend suite: 389 passed, 18 deselected, with the same
  existing Starlette/httpx and Windows Proactor cleanup warnings.
- Protocol Schema export check, isolated Python compile, `git diff --check`, and
  the `agora task --help` CLI smoke: passed.
- No frontend or shared HTTP API contract changed; frontend validation is not
  required for this increment.

### Next safe action

After the reviewed commit, add the smallest read-only unified Task projection
that reports authoritative formal Stage/Gate/Attention state alongside the
existing operational usage ledger without introducing a second state writer.
Dynamic routing, exact provider usage, the full AI-DLC graph, and UI remain
later bounded increments.

## 2026-07-20 - Read-only unified Task projection

### Scope

- [x] Added `agora task status TASK_ID --protocol-v1` with text and JSON output;
  legacy status remains unchanged without the flag.
- [x] Added one versioned projection containing Task/Plan state, formal and
  compatibility Stage state, Gate evaluations, current and historical Runs,
  elapsed/wait state, Artifact references, Evidence, Approvals, Attention,
  decisions, usage, human actions, and merged audit history.
- [x] Read every contributing table through one SQLite connection and one
  rollback-only snapshot; the projection never expires Attention or writes
  state/events.
- [x] Kept process, transport, Schema, semantic, Gate, and compatibility state
  distinct; formal progress counts only authoritative completed Stages.
- [x] Exposed only the Gate-derived `next_safe_action`; the legacy orchestration
  hint is separately labeled non-authoritative.
- [x] Added truthful SQL-aggregate budget reporting for allocation, active
  reservation, settlement, measurement, and remaining capacity. Missing cost
  in any settled Run makes aggregate cost unavailable rather than zero.
- [x] Bounded history pages and current inventories, omitted Artifact bodies and
  Run output/prompts, bounded failure/finding text, and replaced audit payloads
  above 16 KiB with a hash/byte-count summary.
- [x] Added completion, protocol-failure, single-snapshot/no-write, pagination,
  audit-bound, recovery-wait, budget, and CLI compatibility regressions.
- [x] Run the complete verification set and Schema consistency check.
- [x] Obtain Kiro protocol/methodology review and independent Claude Code
  correctness/safety review; fix all actionable findings.
- [x] Commit only after both review gates approve (the commit containing this
  snapshot).

### Current verification log

- Unified projection plus orchestration, Control Plane API, and protocol
  settlement related suite: 79 passed with the existing Starlette/httpx warning.
- Full non-integration backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 394 passed, 18 deselected, with only the existing
  Starlette/httpx and Windows Proactor cleanup warnings.
- Self-audit removed an initially unbounded internal compatibility-status read;
  budget totals now use SQL aggregates while histories remain paginated.
- Protocol Schema export, isolated Python compile, `task status --help` CLI
  smoke, and `git diff --check`: passed before independent review.
- Kiro CLI protocol/methodology review: `APPROVE`; no high or medium findings.
  It confirmed the single rollback-only snapshot, Agora-only authority,
  authoritative Stage progress, separate result dimensions, Gate-derived next
  action, truthful budget aggregation, bounded payloads, recovery wait states,
  legacy CLI compatibility, and the CLI-first HTTP/UI deferral boundary.
- Claude Code independent correctness/safety review: `APPROVE`; no high or
  medium findings. Its two non-blocking observations were that text output has
  no direct regression test and human actions intentionally reflect the bounded
  200-item Attention window; both branches were inspected as correct and the
  bound is documented.
- Final post-review full backend suite: 394 passed, 18 deselected. Protocol
  Schema export, isolated compile, CLI help smoke, and `git diff --check` also
  passed after both approvals.
- No frontend or shared HTTP API contract changed; frontend validation is not
  required for this increment.

### Next safe action

After this reviewed commit, define the smallest frozen Task-state persistence
increment without inferring state from the compatibility manifest or projection.
Keep the authenticated HTTP surface as a later bounded reuse of this read model;
UI stays deferred.

## 2026-07-21 - Frozen Task-state persistence

### Scope

- [x] Added additive `control_tasks` persistence for the eight frozen Task
  states while leaving the 0.5 `tasks.state` column and transitions unchanged.
- [x] Initialized new unified-orchestration Tasks explicitly at `backlog` without
  reading or mapping legacy state; existing Tasks remain explicitly unavailable
  until initialized rather than receiving a guessed state.
- [x] Added optimistic, operation-key-idempotent frozen transitions with bounded
  actor/reason, explicit cause, mirrored Task/Control Plane audit events, and one
  atomic SQLite commit.
- [x] Enforced the frozen transition graph and required invalidation or
  reconciliation to reopen `completed -> active`.
- [x] Added a Control Plane state reader and updated the unified projection to
  read frozen state in its existing rollback-only snapshot. The unified JSON
  shape is version `2.0` because `task_state` changed from compatibility to
  authoritative semantics; the legacy manifest remains separately visible.
- [x] Added explicit initialization, non-inference, idempotency, stale-version,
  invalid-transition, completed-reopen, bounds, concurrency, rollback, service,
  projection, and CLI compatibility coverage.
- [x] Run the complete backend verification set and Schema consistency checks.
- [x] Obtain Kiro protocol/methodology review and independent Claude Code
  correctness/safety review; fix all actionable findings.
- [x] Commit and push only after both review gates approve (the commit containing
  this snapshot).

### Current verification log

- Frozen Task state, Control Plane registry, and formal orchestration suite:
  37 passed before independent review.
- Initial full-suite self-audit exposed five additive fields accidentally added
  to the existing authenticated Control Plane HTTP response and one obsolete
  last-event assertion. The HTTP fields were removed to preserve the deferred
  boundary, and the audit test now verifies both plan creation and explicit
  Task-state initialization.
- Full non-integration backend suite after those corrections: 400 passed,
  18 deselected, with only the existing Starlette/httpx and Windows Proactor
  cleanup warnings.
- Kiro protocol/methodology review pass 1: `CHANGES_REQUESTED`; no high findings
  and three medium findings. It requested an explicit deferred-lifecycle signal,
  a production recovery path for interruption between Plan and frozen-state
  initialization, and additional operation-key/not-found/bounds/separation
  adversarial coverage.
- Added `task_state_lifecycle=stage_derivation_deferred` to JSON and text output;
  `task resume` now idempotently initializes a missing frozen state without
  making status reads mutate; attach also initializes explicitly.
- Added invalid/global-cross-Task operation-key, missing Task, maximum actor and
  reason, post-transition legacy separation, interrupted-create recovery, and
  idempotent repeated-resume regressions.
- Post-Kiro-fix full non-integration backend suite: 403 passed, 18 deselected,
  with only the existing Starlette/httpx and Windows Proactor cleanup warnings.
- Kiro targeted re-review: `APPROVE`; all three medium findings are resolved and
  no high/medium findings remain. Its non-blocking low note requested direct
  attach-path symmetry, so an attach initialization/non-mapping regression was
  added before the independent Claude review.
- Final full non-integration backend suite after the attach regression:
  404 passed, 18 deselected, with only the existing Starlette/httpx and Windows
  Proactor cleanup warnings.
- Independent Claude Code correctness/safety review: `APPROVE`; no actionable
  high/medium findings. It independently verified frozen/legacy separation,
  transaction atomicity, optimistic concurrency, global operation-key
  fail-closed behavior, projection truthfulness, and explicit resume recovery.
- Final Protocol Schema export, isolated Python compile, `task status --help`
  smoke, and `git diff --check`: passed after both review gates approved.
- No HTTP or frontend contract changed; frontend validation is not required for
  this increment.

### Next safe action

Add the complete grouped Stage inventory before deriving Task lifecycle
transitions from Stage/Gate state. Do not infer lifecycle completion from the
current partial Stage projection. HTTP and UI remain deferred.

## 2026-07-21 - Grouped Stage inventory (complete)

### Scope

- [x] Added one immutable, canonical hash-sealed grouped Stage inventory per
  frozen Task without reading Stage identity or order from compatibility rows.
- [x] Bound the inventory to Task, project, Plan, pinned methodology identity,
  version, SHA-256, provisional status, and the concrete Task-contract hash when
  present.
- [x] Grouped the current workflow by its pinned Plan and persisted its complete
  ordered Stage/Gate/title/role/runtime definition without claiming the
  provisional foundation is the missing authoritative AI-DLC graph.
- [x] Required frozen Task-state initialization first; made identical inventory
  initialization idempotent, rejected conflicting definitions, and committed
  the sealed row plus mirrored audit events atomically.
- [x] Restricted formal Stage/Gate creation to the immutable inventory when one
  exists while preserving legacy Registry behavior for Tasks without one.
- [x] Added explicit create, attach, `task next`, and idempotent `task resume`
  initialization/recovery from the Plan's stored methodology payload and hash.
- [x] Moved unified Stage identity, grouping, order, runtime, and total progress
  to the Control Plane inventory in projection schema `3.0`; formal completion
  still counts only authoritative completed Stages.
- [x] Kept current-Stage selection explicitly compatibility-sourced and left
  frozen Task lifecycle derivation, authenticated HTTP, and UI deferred.
- [x] Added hash, ordering, uniqueness, scope, idempotency, conflict,
  concurrency, rollback, tamper, Stage/Gate binding, recovery, projection, CLI,
  and Registry regressions; the unchanged HTTP boundary remains covered by its
  existing acceptance suite.
- [x] Run the complete backend verification set and Schema consistency checks.
- [x] Obtain Kiro protocol/methodology review and independent Claude Code
  correctness/safety review; fix all actionable findings.
- [x] Commit and push only after both review gates approve.

### Current verification log

- Focused frozen Task-state, grouped inventory, orchestration, Registry, and
  authenticated HTTP API suite: 104 passed with one existing Starlette/httpx
  deprecation warning.
- An initial run using `D:\\tmp` never reached test bodies because the sandbox
  denied basetemp creation; the repository-local isolated rerun succeeded.
- Repository-local full-suite self-audit reached 410 passes and exposed five
  known environment-only failures: two app-lifespan writes to the root database,
  two tests using literal `/tmp`, and one non-Git fixture nested under the Git
  checkout. No grouped-inventory test failed.
- Pre-review isolated system-Temp non-integration backend suite excluding
  static-export-dependent `tests/test_web_ui.py`: 415 passed, 18 deselected,
  with only the existing Starlette/httpx and Windows Proactor warnings.
- Added `stage-inventory.schema.json` to the deterministic protocol Schema
  registry. Protocol Schema export check, isolated Python compile,
  `task status --help` smoke, and `git diff --check`: passed before review.
- Kiro protocol/methodology review: `APPROVE`; no high/medium findings. Its low
  notes identified private snapshot-parser coupling, untested projection
  divergence branches, and minor wording; no authority or lifecycle boundary
  defect was found.
- Independent Claude Code correctness/safety review: `APPROVE`; no high/medium
  findings. It requested hardening tests for four projection divergence guards,
  projection-path tamper detection, and the cross-group 200-Stage bound.
- Added a public caller-owned-snapshot inventory reader, all requested
  corruption/bound/tamper regressions, and corrected the HTTP-test wording.
  Post-review focused protocol/inventory/orchestration suite: 108 passed.
- Targeted Kiro and Claude review-fix confirmations: both `APPROVE`; no
  high/medium findings. Their remaining low notes identified a duplicate-Gate
  read-model hardening opportunity and an unavailable-source label nuance.
- Rejected duplicate formal Gates for one inventory Stage before projection
  dictionary construction and report `progress.source=unavailable` when no
  inventory exists. Added both regressions; final focused suite: 109 passed.
- Final narrow Kiro protocol/methodology confirmation: `APPROVE`; no blocking
  findings and no high/medium issues.
- Final narrow independent Claude Code correctness/safety confirmation:
  `APPROVE`; no blocking findings and no high/medium issues.
- Final post-review isolated system-Temp non-integration backend suite excluding
  `tests/test_web_ui.py`: 422 passed, 18 deselected, with only the existing
  Starlette/httpx and Windows Proactor warnings. Protocol Schema export check,
  isolated Python compile, `task status --help` smoke, and staged diff check all
  passed.

### Next safe action

Derive frozen Task lifecycle transitions from the complete grouped inventory
plus authoritative Stage, Gate, Attention, invalidation, and reconciliation
state as a separate bounded increment. Do not infer transitions from process
exit or compatibility Plan state. Authenticated HTTP inventory fields and UI
remain deferred.

## 2026-07-22 - Frozen Task lifecycle derivation (active)

### Scope

- [x] Added a pure deterministic lifecycle decision over the complete sealed
  Stage inventory, formal Stage/Gate states, and open Task Attention counts.
- [x] Failed closed on out-of-inventory Stage/Gate rows, mismatched or duplicate
  Gate bindings, oversized formal inputs, and completed Stages without passed
  formal Gates.
- [x] Reconciled Task state in the same Control Plane transaction as inventory
  initialization, Stage/Gate setup, protocol start/settlement/retry, Gate
  Evidence/evaluation, and invalidation propagation.
- [x] Preserved the frozen transition graph, optimistic versions, explicit
  causes, mirrored audit events, and the invalidation/reconciliation-only
  `completed -> active` boundary.
- [x] Required all inventory Stages and Gates to pass before `needs_review` and
  retained explicit user approval as the only Task-completion action.
- [x] Made compatibility Plan approval idempotent so interruption after the
  authoritative Task transition can be repaired by repeating approval.
- [x] Added explicit `task resume` lifecycle repair after process recovery;
  Attention writes remain owned by the existing Attention store.
- [x] Upgraded the read-only unified projection to schema `4.0` with a bounded
  lifecycle decision and explicit managed/reconciliation-required/unavailable
  status; reads never repair state.
- [x] Kept compatibility Plan/Task state, process exit, Agent suggestions,
  authenticated HTTP, and UI outside the lifecycle authority boundary.
- [x] Run the complete backend verification set and Schema consistency checks.
- [x] Obtained Kiro protocol/methodology review with no HIGH/MEDIUM findings;
  final verdict: `APPROVE`.
- [x] Obtain independent Claude Code correctness/safety review; fix all
  actionable findings.
- [x] Commit and push only after both review gates approve (the commit
  containing this snapshot).

### Current verification log

- Initial lifecycle, frozen Task-state, grouped inventory, and formal
  orchestration suite after migration fixes: 56 passed.
- Expanded Registry, protocol Run, authenticated Control Plane API, Task
  orchestration, and projection suite: 139 passed with one existing
  Starlette/httpx deprecation warning.
- Added Attention response/reconciliation, read-only drift reporting/resume
  repair, lifecycle-event rollback, explicit completion, and completed-Task
  invalidation regressions. Current focused suite: 95 passed.
- Isolated system-Temp full non-integration backend suite excluding
  static-export-dependent `tests/test_web_ui.py`: 441 passed, 18 deselected,
  with only the existing Starlette/httpx and Windows Proactor warnings.
- Protocol Schema export check, isolated Python compile, `task status --help`
  smoke, and `git diff --check`: passed before independent review.
- Hardened the explicit completion entry point after the full-suite run so a
  caller cannot bypass authoritative Stage/Gate facts by manually reaching
  `needs_review`; focused orchestration/lifecycle regression: 86 passed.
- Kiro final staged-diff review: `APPROVE`; no HIGH/MEDIUM protocol,
  methodology, authority, atomicity, recovery, or coverage findings.
- Independent Claude Code correctness/safety review after the session reset:
  `APPROVE`; no HIGH/MEDIUM findings. It verified deterministic precedence,
  fail-closed inventory/Gate validation, transaction rollback, optimistic
  transitions, the completed-Task reopen boundary, explicit human completion,
  read-only projection behavior, and regression coverage. Its three LOW notes
  were non-actionable unreachable/corruption-only hardening observations.
- Final isolated system-Temp non-integration backend suite excluding
  `tests/test_web_ui.py`: 442 passed, 18 deselected, with only the existing
  Starlette/httpx and Windows Proactor cleanup warnings.
- Final Protocol Schema export check, Python compile, `task status --help`
  smoke, unstaged diff check, and staged diff check: passed after both review
  gates approved.

### Next safe action

Add authoritative Control Plane Stage activation and routing over the sealed
inventory as a separate bounded increment. Preserve compatibility projection
labels until that writer is reviewed, and do not infer activation from process
exit or compatibility Plan state. Keep authenticated HTTP lifecycle commands,
methodology migration, the missing authoritative AI-DLC graph, and Task
Workbench UI deferred.

## 2026-07-22 - Authoritative Stage activation and routing (complete)

### Scope

- [x] Added a deterministic read-only route over the first incomplete Stage in
  the sealed grouped inventory, including its exact Gate, role, runtime, group,
  order, and inventory hash.
- [x] Failed closed on out-of-inventory Stage/Gate rows, mismatched bindings,
  non-prefix completion, later active Stages, lifecycle drift, and compatibility
  Plan or runtime tampering.
- [x] Added idempotent transactional Stage activation with one bounded event and
  operation receipt; sealed-inventory Tasks can no longer use legacy Stage
  ensure/configure to create a `ready` Stage outside that route.
- [x] Activated the next authoritative route atomically with successful formal
  Run settlement, while leaving the 0.5 Plan/Stage ledger as a repairable
  compatibility projection.
- [x] Required formal dispatch to revalidate the route, exact Stage/Gate,
  pinned role/runtime metadata, frozen Task lifecycle, and compatibility cursor
  before process spawn.
- [x] Made create/attach initialize the first route and made `task resume`
  repair route activation or compatibility projection drift without duplicate
  dispatch.
- [x] Limited explicit protocol retry to cancelling only the blocker Attention
  created by the same failed formal Run; unrelated Attention remains blocking.
- [x] Upgraded the read-only unified projection to schema `5.0` with the
  authoritative route and lifecycle-aware `runnable` state; missing inventory
  or frozen Task state directs explicit resume recovery without read-time writes.
- [x] Kept authenticated HTTP commands, parallel/DAG routing, dynamic
  risk/capability substitution, methodology migration, exact provider usage,
  the missing authoritative AI-DLC graph, and Task Workbench UI deferred.
- [x] Run focused and complete backend verification plus Schema, compile, diff,
  and CLI smoke checks.
- [x] Obtain Kiro protocol/methodology/reconciliation review and fix all
  actionable findings.
- [x] Obtain independent Claude Code correctness/safety/regression review and
  fix all actionable findings.
- [x] Commit and push only after both review gates approve (the commit
  containing this snapshot).

### Current verification and review log

- Focused route, inventory, protocol settlement, orchestration, projection, and
  compatibility suite after all review fixes: 106 passed.
- Isolated system-Temp full non-integration backend suite excluding
  static-export-dependent `tests/test_web_ui.py`: 458 passed, 18 deselected,
  with only the existing Starlette/httpx deprecation and Windows Proactor
  cleanup warnings.
- Protocol Schema export check, Python compile, `agora task --help` smoke, and
  `git diff --check`: passed.
- Kiro initial review: `APPROVE`; no HIGH/MEDIUM protocol, methodology,
  authority, atomicity, recovery, or coverage findings.
- Claude Code core review: `CHANGES_REQUESTED` for a public
  `ensure_stage(..., PENDING)` path that could pre-create a sealed-inventory
  Stage. The path is now private to authoritative route activation; READY and
  PENDING public writes are both rejected and covered by regression tests.
- Claude's proposed symmetric rejection of `Gate=passed` with a non-completed
  Stage was reconciled against the frozen two-dimension contract: a semantic
  blocker may validly leave `Stage=blocked` with passing formal Evidence. The
  architecture and regression now make this explicit; targeted core re-review:
  `CORE_APPROVE`.
- Claude orchestration/recovery review: `ORCHESTRATION_APPROVE`; no HIGH/MEDIUM
  findings. Its one LOW coverage gap was fixed with a crash-after-settlement
  resume regression for `Stage=blocked` plus `Gate=passed`, proving no advance
  or redispatch. Final targeted review: `APPROVE`; no remaining findings.
- Kiro targeted review of all Claude-driven fixes and new recovery coverage:
  `APPROVE`; no HIGH/MEDIUM findings.

### Next safe action

After committing and pushing this reviewed checkpoint, define the next bounded
explainable routing-policy increment: record why the pinned runtime and required
independent reviewer set satisfy Stage, risk, capability, and protected-budget
constraints without yet permitting runtime substitution or changing the sealed
methodology graph. Keep authenticated HTTP lifecycle commands, the missing
authoritative AI-DLC graph, parallel/DAG routing, and Task Workbench UI deferred.

## 2026-07-22 - Explainable pinned routing policy (reviewed)

### Scope

- [x] Added versioned `agora-foundation-routing-policy@1.0` capability,
  reviewer-role, risk-minimum, and protected-budget facts for the pinned
  provisional methodology.
- [x] Added a hash-sealed per-Run routing decision covering exact inventory and
  methodology bindings, Stage/role/runtime, Task risk, capabilities, required
  reviewers, independence, conservative budget inputs, five explicit checks,
  blockers, and rationale.
- [x] Kept the sealed inventory route authoritative and prohibited runtime or
  reviewer substitution; unknown or changed policy inputs fail closed.
- [x] Included the policy in the sealed Context Pack and re-derived it inside
  the same SQLite transaction as the operational Run and usage reservation.
- [x] Protected every unfinished later required reviewer Stage allocation; a
  retry that would consume review capacity now blocks before process spawn.
- [x] Preserved exact-zero Token and cost settlement when a process provably
  never started, while unavailable settlements conservatively debit their Run
  reservations.
- [x] Added additive `routing_policy_payload` migration and upgraded the
  read-only unified Task projection to schema `6.0`; historical legacy Runs may
  explicitly have no policy.
- [x] Added capability/risk/reviewer/budget, Context binding, preview/claim
  race, hash-tamper, CLI/projection, and exact-zero recovery regressions.
- [x] Run the complete backend verification set and Schema consistency checks.
- [x] Obtain Kiro protocol/methodology/reconciliation review and fix all
  actionable findings.
- [x] Obtain independent Claude Code correctness/safety/regression review and
  fix all actionable findings.
- [x] Commit and push only after both review gates approve (the commit
  containing this snapshot).

### Current verification log

- Formal protocol orchestration suite after review fixes: 38 passed.
- Complete non-integration backend suite excluding the deferred static-export
  Web UI test: 466 passed, 18 deselected. The existing Starlette/httpx
  deprecation warning and Windows Proactor cleanup warning remain unrelated to
  this increment.
- Schema export consistency, isolated `compileall`, `agora task --help`, and
  `git diff --check` passed. Compile bytecode and pytest basetemp were redirected
  to system Temp because pre-existing repository and `D:\tmp` ACLs denied
  isolated directory creation.
- Kiro core, migration, and projection reviews returned `APPROVE`; broad CLI
  attempts that ended in `channel closed` without a verdict were discarded.
  Its post-fix targeted re-review returned `KIRO_FIX_APPROVE` with no
  high/medium findings.
- Claude core review returned `CHANGES_REQUESTED` for one medium structured-
  blocker issue plus two low hardening observations. Empty capability/reviewer
  evidence now remains a valid blocked policy decision, inventory sequence is
  independently bound by group and Stage keys, and the missing-current budget
  snapshot uses neutral zero; both new regressions block before process spawn.
- Claude post-fix review returned `CLAUDE_FIX_APPROVE`. Its integration review
  initially questioned `exclude_if` using older Pydantic behavior; the installed
  Pydantic 2.13.4 signature and a minimal dump prove the parameter is supported,
  and a passing legacy-Run regression now locks key omission. Claude then
  returned `CLAUDE_INTEGRATION_APPROVE`; no high/medium findings remain.
- No frontend or authenticated HTTP contract changed; frontend validation is
  not required for this increment.

### Next safe action

After the reviewed commit, define the smallest versioned Task budget-amendment
workflow bound to the exact Task, Plan, and policy inputs. It should add explicit
retry headroom without mutating sealed Stage allocations, reviewer requirements,
or historical usage, then force the routing policy to re-derive before another
claim. Keep provider discovery, dynamic runtime substitution, authenticated
HTTP, the missing authoritative AI-DLC graph, parallel/DAG routing, and Task
Workbench UI deferred.

## 2026-07-22 - Versioned Task budget amendment (reviewed)

### Scope

- [x] Added an append-only, hash-sealed `BudgetAmendment` bound to exact Task,
  Plan, grouped inventory, methodology, concrete contract, routed Stage, Stage
  allocation, and routing-policy inputs.
- [x] Added `agora task amend-budget` with required Task/Plan optimistic
  versions, bounded actor/reason, and replay-safe explicit or derived operation
  keys.
- [x] Limited amendments to a currently runnable formal route whose only policy
  blocker is `protected_budget`; active operational or unsettled formal Runs
  reject the mutation.
- [x] Allowed only a strict increase to the Task/Plan total Token or configured
  cost envelope. Unbounded cost remains unbounded, while Stage Token/cost
  allocations, reviewer assignments, and historical usage remain unchanged.
- [x] Re-derived the routing policy before and after the envelope update inside
  one SQLite write transaction and rolled back unless the resulting policy is
  fully dispatchable. The next Run claim still derives a new per-Run policy.
- [x] Added additive `orchestration_budget_amendments` migration, Task audit
  event, payload/row tamper validation, and unified projection schema `7.0`
  with paginated amendment history.
- [x] Added success/retry, insufficient-increase rollback, stale/replay,
  active-Run, unbounded-cost, tamper, migration, projection, and CLI coverage.
- [x] Run the complete backend verification set and Schema consistency checks.
- [x] Obtain Kiro protocol/methodology/reconciliation review and fix all
  actionable findings.
- [x] Obtain independent Claude Code correctness/safety/regression review and
  fix all actionable findings.
- [x] Commit and push only after both review gates approve (the commit containing
  this snapshot).

### Current verification log

- Formal protocol orchestration suite after all review fixes: 46 passed.
- Expanded orchestration, routing, lifecycle, grouped-inventory, frozen-state,
  protocol-Run, and Registry suite: 164 passed.
- Final isolated system-Temp non-integration backend suite excluding the
  deferred static-export Web UI test: 474 passed, 18 deselected, with the
  existing Starlette/httpx warning and Windows Proactor cleanup warning.
- A sandboxed full-suite run first passed 471 tests and hit only the two known
  legacy tests that hard-code literal `/tmp` paths. The required sandbox-external
  rerun passed all 474 selected tests.
- Protocol Schema export consistency, isolated `compileall`, `agora task --help`
  amendment smoke, and `git diff --check`: passed after all review fixes.
- The migration regression drops the new ledger from an existing populated
  database, reinitializes additively, and confirms the Task data is unchanged.
- Claude Code core review initially returned `CHANGES_REQUESTED` for two medium
  findings: replay matching omitted route/contract bindings, and store/model
  cost comparisons used inconsistent tolerance. Replay now binds the route and
  concrete contract, all cost comparisons are exact, and direct regressions
  cover tiny decreases, route/contract forgery, a second amendment/version
  chain, and an unsettled formal Run. Targeted re-review returned
  `CLAUDE_CORE_APPROVE`.
- Kiro protocol/methodology review initially returned `CHANGES_REQUESTED` for
  receipt semantic hardening and clarity around redacted replay identity and
  SQLite Stage-allocation serialization. Prior/resulting policy decision IDs
  are now required to differ with resealed-forgery coverage; comments and docs
  explicitly bind the canonical redacted reason and `BEGIN IMMEDIATE` writer
  lock. Kiro re-reviewed the actual lock semantics and returned `KIRO_APPROVE`.
- Claude integration review initially questioned the globally unique amendment
  operation key. The repository-wide Control Plane contract, architecture text,
  and a new cross-Task no-mutation regression prove that reuse must fail closed;
  Claude retracted the finding and returned `CLAUDE_INTEGRATION_APPROVE`.
- No frontend or authenticated HTTP contract changed; frontend validation is
  not required for this increment.

### Next safe action

After the reviewed commit, define the smallest read-only, versioned provider
usage-observation contract for native CLI results. It must distinguish exact,
estimated, and unavailable Token/cost measurements, bind each observation to
the Run and adapter provenance, and leave routing, runtime/model selection, and
historical ledger entries unchanged. Provider capability discovery, dynamic
runtime/model substitution, authenticated HTTP, the missing authoritative
AI-DLC graph, parallel/DAG routing, and Task Workbench UI remain deferred.

## 2026-07-13

- [x] Confirmed local CLI versions: Codex 0.144.1, Claude Code 2.1.207, Kiro CLI 2.11.1.
- [x] Created an isolated Git worktree for Phase 1.
- [x] Inspected existing project registry, research artifacts, SQLite sessions, FastAPI routes, and tests.
- [x] Kiro completed a read-only Phase 1 specification review (0.34 credits reported by Kiro).
- [x] Implemented tool-neutral task manifest and lifecycle state machine.
- [x] Implemented SQLite task/event persistence and optimistic version checks.
- [x] Added REST endpoints and acceptance tests.
- [x] Migrated development commands to uv and generated `backend/uv.lock`.
- [x] Relevant regression suite: 18 passed, 1 dependency warning.
- [x] Full non-integration baseline: 163 passed, 37 pre-existing Windows/environment failures, 15 deselected.
- [x] Claude review pass 1: `CHANGES_REQUESTED` (project-id validation, sync DB in async routes, limits/order/tests).
- [x] Addressed all requested changes; relevant regression suite increased to 19 passed.
- [x] Claude review pass 2: `APPROVE` with no blocking correctness, transaction, concurrency, or security findings.
- [x] Phase 1 committed as `d92bf93` after Claude approval.

## 2026-07-13 — Network policy

- [x] Phase 1 foundation committed as `d92bf93` after Claude approval.
- [x] Confirmed external TCP/HTTPS works without a proxy.
- [x] Confirmed Agora inherited `HTTP_PROXY`/`HTTPS_PROXY` and failed with HTTP 407.
- [x] Added configurable `web.network_mode`: `direct` ignores proxy variables; `system` inherits them.
- [x] Network policy tests and existing WebTools regression: 7 passed.
- [x] Claude review: `APPROVE`; no correctness, security, or compatibility defects found.
- [x] Network policy committed as `797e8e9` after Claude approval.

## 2026-07-13 — Requirements Studio backend

- [x] Network policy committed as `797e8e9` after Claude approval.
- [x] Kiro produced the Phase 2 Requirements Studio specification (0.90 credits).
- [x] Added versioned structured specs, human approval/rejection, CR lifecycle, and per-requirement traceability.
- [x] Added the approved-spec gate for `requirements → design`.
- [x] Added REST endpoints and append-only `spec.*` / `cr.*` audit events.
- [x] Relevant regression suite: 28 passed, 1 dependency warning.
- [x] Claude review pass 1: `CHANGES_REQUESTED` (design-gate bypass, schema init, optimistic revision, rollback clarity).
- [x] Addressed all requested changes; relevant regression suite increased to 29 passed.
- [x] Claude review pass 2: `APPROVE`; no remaining high/medium defects.
- [x] Requirements Studio backend committed as `ed00446` after Claude approval.

## 2026-07-13 — Portfolio and Requirements Studio UI

- [x] Defined typed frontend contracts for task lifecycle and requirement specs.
- [x] Added a reusable Delivery Control Plane navigation shell.
- [x] Added a live cross-project Portfolio board, attention lane, filters, and task creation.
- [x] Added Requirements Studio task selection, lifecycle entry, structured draft creation, version viewing, and human approval.
- [x] Changed-file ESLint passes; production build and static export pass (11 pages).
- [x] Recorded pre-existing full-frontend lint debt separately (16 errors in untouched legacy pages/components).
- [x] Claude review pass 1: `CHANGES_REQUESTED` (design transition, rejection recovery, refresh selection, spec-local state, 409 recovery, dialog accessibility).
- [x] Addressed every high/medium finding and reran changed-file lint plus production build successfully.
- [x] Claude review pass 2: `CHANGES_REQUESTED` (composer remount and stale spec-response race).
- [x] Added task-keyed composer state, loading isolation, and cancellation guards; lint/build pass again.
- [x] Claude review pass 3: `APPROVE`; no remaining high/medium issues.
- [x] Complete independent Claude review and address findings.
- [x] Frontend increment committed as `7a3574b` after Claude approval.

## 2026-07-13 — Delivery Execution Layer

- [x] Estimated complete-platform progress at 35% before Phase 3.
- [x] Kiro produced the bounded Phase 3 execution specification (1.39 credits).
- [x] Added durable execution-run contracts, SQLite schema, adapters, dispatcher, and REST API.
- [x] Added atomic `planned → running` queue gate and `run.*` task audit events.
- [x] Added workspace isolation, bounded concurrency, timeout, cancellation, output redaction, and restart recovery.
- [x] Replaced machine-specific `/work01` defaults with repository-anchored project and agent workspaces.
- [x] Execution plus task/requirements/project/dispatch/config regression suite: 39 passed.
- [x] Kiro security review: `CHANGES_REQUESTED`; addressed allowed workspace roots, safe errors/redaction, queued recovery, schema transactions, and API bounds.
- [x] Kiro security re-review: `APPROVE`; its remaining pagination observation was fixed before final tests.
- [x] Final full non-integration suite: 197 passed, 32 pre-existing Windows/sandbox/environment failures, 15 deselected.
- [x] Claude core review pass 1: `CHANGES_REQUESTED` (pre-claim double spawn, Python 3.10 timeout leak, store-level redaction, symlink residual risk, cancelled-output audit).
- [x] Addressed all findings with atomic pre-spawn claim, PID attachment CAS, cross-version timeout handling, persistence-boundary redaction, documented/rechecked workspace launch, and a final cancel-output event.
- [x] Claude core review pass 2 found the same Python 3.10 timeout mismatch in terminate-to-kill escalation; fixed it and added a stubborn-process regression test.
- [x] Claude core review pass 3: `APPROVE`; no remaining high/medium core findings.
- [x] Claude integration review pass 1: `CHANGES_REQUESTED` (project IDs with separators rejected and blocking SQLite writes on the event loop).
- [x] Fixed project-id validation and offloaded run queue/cancel persistence from the FastAPI event loop.
- [x] Claude integration review pass 2: `APPROVE`; no remaining high/medium integration findings.
- [x] Complete independent Claude review and address findings.
- [x] Commit the reviewed Phase 3 increment.

### Full-suite baseline failure groups

- POSIX-only path and shell assertions (`/`, `pwd`, `sleep`) on Windows.
- Tests that write literal `/tmp` paths are blocked by the managed Windows sandbox.
- External web tests blocked by the managed test network/environment.
- Agent profile YAML read with CP932 instead of UTF-8.
- Static frontend route tests run without a built `frontend/out` directory.

## 2026-07-14 — Execution Run Center UI

- [x] Phase 3 execution layer committed as `48359a3` after Claude approval.
- [x] Estimated complete-platform progress at 45% before Phase 4.
- [x] Kiro produced the bounded Phase 4 Run Center specification (0.94 credits).
- [x] Added typed execution API contracts and a Delivery Control Plane `/runs` route.
- [x] Added cross-project filters, run composition, optimistic cancel, and detailed output inspection.
- [x] Added visibility-aware polling, stale-version guards, and opt-in terminal browser notifications.
- [x] Added accessible create/cancel dialogs, live status announcements, and text-only execution data rendering.
- [x] Changed-file ESLint passes; production build and static generation pass (12 pages).
- [x] Claude lifecycle review: `CHANGES_REQUESTED` (enum filter value, local notification noise, URL validation, request cancellation).
- [x] Claude UI/accessibility review: `CHANGES_REQUESTED` (focus restoration, modal background isolation, in-flight dialog unmounts).
- [x] Fixed all findings with validated filters, AbortControllers, silent notification baselines, portal/inert modals, focus restoration, and mounted guards.
- [x] Claude targeted re-review: `APPROVE`; no remaining high/medium findings or fix regressions.
- [x] Complete independent Claude review and address findings.
- [x] Commit the reviewed Phase 4 increment.

## 2026-07-14 — Git Worktree Workspace Provisioning

- [x] Phase 4 Run Center committed as `3c10131` after Claude approval.
- [x] Estimated complete-platform progress at 53% before Phase 5.
- [x] Kiro produced the bounded Phase 5 provisioning specification (1.82 credits).
- [x] Added explicit, idempotent, path-confined linked-worktree provisioning APIs.
- [x] Added safe Git argv execution, deterministic collision-resistant branches, timeouts, and sanitized failures.
- [x] Refused foreign directories, path escapes, checked-out branches, non-Git projects, and stale worktree metadata without deleting user data.
- [x] Added Run Center workspace readiness checks and an explicit provision action.
- [x] Workspace/execution/project focused backend suite: 49 passed; frontend lint/build pass (12 pages).
- [x] Claude core review: `CHANGES_REQUESTED` (rmdir race mapping, list-endpoint 422 mapping, pre-mutation confinement recheck).
- [x] Added safe filesystem-race mapping and tests for real foreign worktrees, missing Git, and escaped list status.
- [x] Claude core re-review: `APPROVE`; no remaining high/medium provisioning findings.
- [x] Claude frontend review: `CHANGES_REQUESTED` (stale provision response, POST cancellation, authoritative resync, live status).
- [x] Added selection-keyed abort/guards, failure resync/retry, current-selection locking, and an aria-live status region.
- [x] Claude frontend re-review: `APPROVE`; no remaining high/medium integration findings.
- [x] Complete independent Claude review and address findings.
- [x] Commit the reviewed Phase 5 increment.

## Review Gate

No implementation commit may be created until:

1. Relevant automated tests pass.
2. Claude Code reviews the staged or working-tree diff independently.
3. Actionable findings are fixed or explicitly recorded with rationale.
4. Tests are rerun after fixes.

## 2026-07-15 — Human Intervention / Attention Center (Phase 6, active)

### Synchronized status snapshot

- Last reviewed commit: `1ccc37b feat: provision isolated agent worktrees`.
- Branch: `feat/control-plane-phase1`.
- Committed-platform estimate: 70%.
- Working-tree estimate including uncommitted Phase 6d: 74%.
- Phase 6 state: implementation, review gate, and local commit complete.
- Source of truth: this section must be updated whenever Phase 6 scope, tests, review state, or commit state changes.

### Scope and task alignment

- [x] Kiro produced a bounded, read-only Phase 6 specification (1.06 credits).
- [x] Kept the core tool-neutral: questions, approvals, and blockers share one durable contract.
- [x] Added urgency, optional expiry, project/task linkage, optional run linkage, response/cancel lifecycle, optimistic versions, and append-only `attention.*` audit events.
- [x] Added REST create/list/get/count/respond/cancel endpoints; synchronous SQLite work is offloaded from async routes.
- [x] Added cross-reference validation so a linked run must belong to the requested task/project.
- [x] Added context secret sanitization and bounded request fields.
- [x] Added `/attention` inbox UI with state/kind/project filters, response and approval actions, cancellation, polling, conflict refresh, and opt-in browser notifications.
- [x] Added the Attention entry to the Delivery Control Plane navigation.
- [ ] Add vendor bridge implementations for Claude Code, Kiro CLI, and Codex; explicitly deferred to Phase 6b so vendor rules do not enter the core model.
- [x] Ran the full Phase 6 related backend regression suite: 43 passed, 1 dependency deprecation warning.
- [x] Claude Code independent review pass 1: `CHANGES_REQUESTED` (write-path expiry race, unconditional read-path writer locks, incomplete free-text redaction).
- [x] Fixed all findings with transactional expiry conflicts, a read-before-write expiry sweep, persistence-boundary text redaction, and additional regression tests.
- [x] Claude Code review pass 2: `APPROVE`; no remaining high/medium findings.
- [x] Reran frontend changed-file ESLint and production build successfully after review; 13 pages generated.
- [x] Committed the reviewed Phase 6 increment locally (the commit containing this synchronized snapshot).

### Verified data at this snapshot

- Backend Attention lifecycle/API suite: 7 passed, 1 dependency deprecation warning.
- Phase 6 related backend regression suite: 43 passed, 1 dependency deprecation warning.
- Frontend changed-file ESLint: passed.
- Frontend production build/static generation: passed, 13 pages including `/attention`.
- Phase 6 backend, tests, frontend client/hook/page, and navigation integration are committed locally.
- No push has been performed.

### Next ordered actions

1. Keep vendor CLI bridge work as the next separately reviewable Phase 6b task.
2. Define the neutral bridge delivery/acknowledgement contract before implementing vendor adapters.
3. Implement and test Claude Code, Kiro CLI, and Codex adapters independently against that contract.

## 2026-07-15 — Vendor Attention Bridge Foundation (Phase 6b, active)

- [x] Verified local versions and surfaces: Claude Code 2.1.207, Codex CLI 0.144.1, Kiro CLI 2.12.1.
- [x] Used the current official Codex manual to verify stable command hooks and app-server JSON-RPC boundaries.
- [x] Kiro produced a read-only Phase 6b architecture review (1.50 credits).
- [x] Rejected unverified assumptions about a Codex `--json-rpc` flag and automatic Claude approval delivery.
- [x] Added a neutral `BridgeEventRequest` with explicit `capture_only` versus `bidirectional` capability.
- [x] Added atomic database idempotency on vendor/run/event identity and append-only `attention.bridge_captured` events.
- [x] Restricted public hook ingestion to active runs and `capture_only` mode until a delivery protocol is verified.
- [x] Added pure Claude/Codex/Kiro hook payload normalization with redaction at the persistence boundary.
- [x] Added a portable command-hook forwarding CLI without terminal keystroke injection.
- [x] Added run/task/project correlation environment variables to execution subprocesses.
- [x] Updated Attention Center to disclose capture-only limitations instead of claiming delivery.
- [x] Added Phase 6b capability documentation and manually reviewed hook fragments.
- [x] Bridge-focused Attention tests: 10 passed, 1 dependency deprecation warning.
- [x] Final related backend regression suite: 49 passed, 1 dependency deprecation warning.
- [x] Final frontend changed-file ESLint and production build passed; 13 pages generated.
- [x] Claude review pass 1: `CHANGES_REQUESTED` (blocking hook exit code, unstable fallback identity, example portability, public-input bounds).
- [x] Fixed all pass-1 findings and added regression coverage.
- [x] Claude review pass 2: `CHANGES_REQUESTED` (argparse could still emit blocking exit 2; malformed success receipt handling).
- [x] Normalized all hook CLI configuration failures to non-blocking exit 1 and covered malformed receipts.
- [x] Claude review pass 3: `APPROVE`; no remaining high/medium findings.
- [x] Committed Phase 6b locally after the review gate passed (the commit containing this snapshot).
- [ ] True bidirectional delivery remains a later increment requiring generated, version-matched vendor protocol schemas and end-to-end tests.

## 2026-07-15 — Codex Bidirectional Approval Protocol (Phase 6c, active)

- [x] Generated JSON schemas from the installed Codex CLI 0.144.1 app-server.
- [x] Selected only stable command-execution and file-change approval server requests.
- [x] Explicitly excluded experimental `item/tool/requestUserInput`.
- [x] Added strongly typed Codex correlation and schema-bounded request/response codecs.
- [x] Added durable `pending → ready → delivering → delivered|failed` lifecycle.
- [x] Added atomic delivery claim, redacted delivery audit, and at-most-once-safe stale claim recovery.
- [x] Added a 50-open-item per-run circuit breaker.
- [x] Restricted public hook ingestion to loopback and retained capture-only trust boundaries.
- [x] Kiro architecture review approved the foundation and identified recovery/cap hardening, which was applied.
- [x] Codex bridge and Attention integration tests: 18 passed, 1 dependency deprecation warning.
- [x] Kiro review observations applied: stale-delivery recovery, per-run cap, typed correlation, and loopback ingress.
- [x] Full related regression suite: 57 passed, 1 dependency deprecation warning.
- [x] Claude review pass 1: `CHANGES_REQUESTED` (cancelled/expired approval left delivery pending).
- [x] Added atomic failed delivery transitions and audit events for cancellation and expiry.
- [x] Claude review pass 2: `APPROVE`; no remaining high/medium findings.
- [x] Committed the reviewed protocol foundation locally (the commit containing this snapshot).
- [ ] Next increment: app-server process supervision and real stdin/stdout handshake integration.

## 2026-07-15 — Codex App-Server Execution Integration (Phase 6d, active)

- [x] Added configurable `codex_app_server` execution-adapter mode with audited app-server argv.
- [x] Added version-matched initialize, thread/start, and turn/start JSONL handshake.
- [x] Connected stable approval server requests to `CodexApprovalBroker` and returned human decisions on original JSON-RPC ids.
- [x] Preserved dispatcher concurrency, workspace confinement, PID attachment, timeout, cancellation, and shutdown behavior.
- [x] Added bounded protocol stdout/stderr capture and existing persistence-boundary redaction.
- [x] Captured server requests that race ahead of request responses during handshake.
- [x] Enabled app-server mode in the default Agora project configuration; `bridge_mode: cli` remains the fallback.
- [x] Added deterministic process-level tests for success, approval round-trip, invalid JSON, timeout, and cancellation.
- [x] Full related backend regression suite: 64 passed, 1 dependency deprecation warning.
- [ ] Optional authenticated live smoke test against the installed Codex app-server.
- [x] Claude runner review pass 1: `CHANGES_REQUESTED` (handshake approval deadlock, callback cleanup coupling, completion identity).
- [x] Fixed all high/medium runner findings and strengthened the early-request process test.
- [x] Final post-fix related regression suite: 64 passed, 1 dependency deprecation warning.
- [x] Claude post-fix runner review: `APPROVE`; early approval delivery, identity checks, cancellation, and cleanup verified.
- [x] Claude dispatcher/config review: `APPROVE`; CLI fallback, audit command, state races, and documentation verified.
- [x] Commit the reviewed Phase 6d increment locally (the commit containing this snapshot).

## 2026-07-15 — Truthful Adapter Capability Routing (Phase 6e, active)

- [x] Revalidated local versions: Claude Code 2.1.210 and Kiro CLI 2.12.2.
- [x] Verified Claude stream-json surfaces and the Agent SDK `can_use_tool` integration boundary.
- [x] Rejected Kiro's speculative `claude agent --host stdio://` proposal because the installed CLI exposes no such command.
- [x] Confirmed Claude Managed Agents supports indefinite confirmation events but uses Platform API authentication/billing rather than the Claude Code subscription.
- [x] Kept Claude and Kiro on CLI/capture-only paths; no SDK install or global settings mutation.
- [x] Added a typed execution-adapter capability contract and `GET /api/execution-adapters`.
- [x] Updated Run Center dispatch cards to show bidirectional approval delivery versus capture-only behavior from live backend data.
- [x] Execution adapter tests: 23 passed, 1 dependency deprecation warning.
- [x] Changed-file frontend ESLint passed using the repository-installed toolchain.
- [x] Related backend regression suite: 66 passed, 1 dependency deprecation warning.
- [x] Frontend production build/static generation passed; 13 pages generated.
- [x] Claude review pass 1: `APPROVE` with two low UI observations (fetch-warning scope and disabled-adapter selection).
- [x] Applied both low-risk UI improvements before commit.
- [x] Reran changed-file ESLint and production build after both UI improvements; 13 pages generated.
- [x] Final focused Claude re-review: `APPROVE`; capability failure isolation and disabled-adapter reconciliation verified with no high/medium issues.
- [x] Commit the reviewed Phase 6e increment locally (the commit containing this snapshot).

## 2026-07-15 — Cross-Project Workflow DAG Foundation (Phase 7a, active)

- [x] Estimated complete-platform progress at 77% before Phase 7a.
- [x] Kiro produced a bounded Phase 7a DAG persistence/API specification (2.22 credits).
- [x] Kept workflow lifecycle independent from referenced task lifecycle so shared tasks are not cancelled implicitly.
- [x] Added bounded workflow/step contracts, cycle detection, cross-project references, and optional task integrity checks.
- [x] Added transactional SQLite workflow, step, and append-only event persistence.
- [x] Added optimistic workflow/step versions, atomic root readiness, dependency promotion, failure closure, completion, and cancellation.
- [x] Added create/list/get/activate/step-transition/cancel/event REST endpoints.
- [x] Added deterministic linear, cyclic, cross-project, stale-version, failure, cancellation, 50-way fan-out, completion, and API tests.
- [x] Initial related backend regression suite: 61 passed, 1 dependency deprecation warning.
- [x] Claude review pass 1: `CHANGES_REQUESTED` (individual-step cancellation deadlock, fragile terminal derivation, unbounded metadata).
- [x] Removed individual step cancellation, made terminal derivation explicit, bounded metadata to 64 KiB, and added persistence-boundary redaction.
- [x] Documented workflow-wide `ready_count` semantics and added cancellation/redaction regression coverage.
- [x] Post-fix related backend regression suite: 63 passed, 1 dependency deprecation warning.
- [x] Claude final re-review: `APPROVE`; cancellation, terminal derivation, bounds, redaction, and regression coverage verified.
- [x] Recorded low-review rationale: metadata size validation is conservative; `created_by` remains an exact bounded audit identity rather than sanitized free text.
- [x] Commit the reviewed Phase 7a increment locally (the commit containing this snapshot).

## 2026-07-15 — Workflow Execution Dispatch (Phase 7b, active)

- [x] Added durable per-step run binding, dispatch claims, bounded blockers, and schema migration for Phase 7a databases.
- [x] Added explicit workflow dispatch/reconciliation service and REST endpoint.
- [x] Atomically claims ready steps before queueing to prevent duplicate runs under concurrent dispatch calls.
- [x] Added recovery for crashes before and after execution-run creation using persisted workflow claim IDs.
- [x] Reconciles run success into dependency promotion and queues newly ready cross-project steps.
- [x] Maps non-success terminal runs to workflow failure and cancels active sibling runs.
- [x] Leaves missing-task, unready-task, unavailable-adapter, and workspace failures as retryable ready-step blockers.
- [x] Applies task time budgets with the existing 7,200-second execution cap.
- [x] Deterministic tests cover cross-project dependencies, fan-out, duplicate concurrent dispatch, blockers, recovery, and sibling cancellation.
- [x] Related backend regression suite: 69 passed, 1 dependency deprecation warning.
- [x] Claude full review attempt timed out without a verdict; split the review by risk domain.
- [x] Claude concurrency review: `CHANGES_REQUESTED` (stale recovery could race queue/bind; binding failure was under-audited).
- [x] Added per-workflow single-node serialization, retained recoverable claims, audited binding errors, and verified blocker event deduplication.
- [x] Claude concurrency re-review: `APPROVE`; claim recovery and blocker deduplication verified.
- [x] Claude terminal/API review: `CHANGES_REQUESTED` (one sibling cancellation conflict could strand later active runs with no cleanup retry).
- [x] Added independent cancellation retries, audited cleanup failures, and failed-workflow cleanup-only dispatch; targeted recovery/cancellation tests pass.
- [x] Claude terminal/API re-review: `APPROVE`; retry, audit, cleanup-only dispatch, migration, timeout, and blocker semantics verified.
- [x] Final post-review related backend regression suite: 69 passed, 1 dependency deprecation warning.
- [x] Commit the reviewed Phase 7b increment locally (the commit containing this snapshot).

## 2026-07-15 — Workflow Operations UI (Phase 7c, active)

- [x] Added typed frontend workflow contracts and list/get/activate/dispatch clients.
- [x] Added a Delivery Control Plane Workflow navigation entry and `/workflows` route.
- [x] Added workflow list selection, active read polling, state summaries, and explicit refresh.
- [x] Added dependency-depth DAG lanes with project, adapter, task, run, state, and blocker context.
- [x] Added explicit activate, dispatch/reconcile, and failed-workflow cleanup controls; read polling never mutates workflow state.
- [x] Added workflow-to-run deep links and Run Center `run` query selection.
- [x] Changed-file frontend ESLint passed.
- [x] Frontend production build/static generation passed; 14 pages generated including `/workflows`.
- [x] Claude review pass 1: `CHANGES_REQUESTED` (poll/action stale responses, cross-selection overwrite, 409 resync, adapter type, selection semantics, malformed DAG warning).
- [x] Added request-id and version guards, target-workflow action checks, action-time selection locking, and conflict resync.
- [x] Matched backend adapter strings, added `aria-pressed`, narrowed the live region, reconciled missing selections, and surfaced unresolved DAG data.
- [x] Post-fix changed-file ESLint and production build passed; 14 pages generated.
- [x] Claude final re-review: `APPROVE`; lifecycle guards, selection integrity, conflict recovery, types, DAG fallback, and accessibility verified.
- [x] Commit the reviewed Phase 7c increment locally (the commit containing this snapshot).

## 2026-07-15 — Workflow Composer (Phase 7d, active)

- [x] Added typed workflow-create client contracts.
- [x] Added an accessible modal composer using existing planned/running tasks as authoritative project references.
- [x] Added multi-step task, agent, title, prompt, and dependency editing.
- [x] Constrained dependencies to earlier rows so the client cannot construct cycles; backend validation remains authoritative.
- [x] Removing a step clears dependent references and payload keys are regenerated deterministically at submit time.
- [x] Added draft versus create-and-activate choice with explicit no-auto-dispatch disclosure.
- [x] Changed-file frontend ESLint passed.
- [x] Frontend production build/static generation passed; 14 pages generated.
- [x] Claude review pass 1: `CHANGES_REQUESTED` (activation partial success, duplicate task assignment, submit re-entry, unmount guard, Escape propagation, dependency fallback).
- [x] Preserved and opened created drafts after activation failure so retry never duplicates the workflow.
- [x] Enforced one-task-per-step in both Composer choices and the authoritative backend workflow model.
- [x] Added synchronous submit guard, mounted checks, modal Escape containment, and defensive dependency lookup.
- [x] Post-fix workflow backend tests: 11 passed, 1 dependency deprecation warning.
- [x] Post-fix changed-file ESLint and production build passed; 14 pages generated.
- [x] Claude review pass 2: `APPROVE`; partial-success recovery, task uniqueness, submit guard, modal behavior, and dependency safety verified.
- [x] Added Composer-local unmount guards for every post-await callback/state update; focused Claude re-review: `APPROVE`.
- [x] Final changed-file ESLint and production build passed; 14 pages generated.
- [x] Commit the reviewed Phase 7d increment locally (the commit containing this snapshot).

## 2026-07-15 — Opt-in Workflow Supervision (Phase 8a, active)

- [x] Added opt-in `auto_dispatch` and bounded per-workflow `max_concurrent_runs` contracts with SQLite migration support.
- [x] Enforced the concurrency policy in the authoritative orchestrator for both manual and automatic dispatch.
- [x] Added a lifecycle-managed supervisor that isolates and audits per-workflow scheduling failures.
- [x] Made supervisor lifecycle primitives restart- and event-loop-safe for cached FastAPI dependencies.
- [x] Added Composer controls and Workflow Operations policy visibility; manual dispatch remains the default.
- [x] Added deterministic opt-in, manual exclusion, concurrency-cap, and interval-bound tests.
- [x] Related backend regression suite after review fixes: 57 passed, 1 dependency deprecation warning.
- [x] Full backend baseline: 248 passed, 12 skipped, 34 Windows/CP932 and shell compatibility failures; the one lifecycle-related failure was fixed and included in the passing related suite.
- [x] Changed-file frontend ESLint passed using the repository-installed toolchain.
- [x] Frontend production build/static generation passed; 14 pages generated.
- [x] Claude backend review pass 1: `CHANGES_REQUESTED` (Python 3.10 timeout compatibility, audit failure isolation, lifecycle/isolation coverage).
- [x] Fixed both supervisor failure modes and added real tick/shutdown plus dispatch/audit isolation tests.
- [x] Claude backend final re-review: `APPROVE`; compatibility, lifecycle, persistence, cap semantics, and isolation verified.
- [x] Claude frontend review pass 1: `APPROVE` with non-blocking validation, accessibility, and activation-copy observations.
- [x] Added string-backed integer validation, accessible inline guidance, and explicit active-only automatic-dispatch copy.
- [x] Claude frontend final re-review: `APPROVE`; UX and backend contract consistency verified.
- [x] Commit the reviewed Phase 8a increment locally (the commit containing this snapshot).

## 2026-07-15 — Agora 0.5 Release Closure (Phase 8b, active)

- [x] Standardized Agora-owned configuration, profile, agent, skill, memory, and file-tool text I/O on explicit UTF-8.
- [x] Corrected cross-platform test assumptions for native paths, shell commands, Python 3.13 event loops, and network-test classification.
- [x] Full non-integration backend suite on Windows: 281 passed, 18 deselected, 3 dependency/runtime cleanup warnings.
- [x] Python package compile check passed.
- [x] Full frontend ESLint passed with zero errors (12 pre-existing non-blocking warnings after cleanup).
- [x] Frontend production build and TypeScript validation passed; 14 pages generated.
- [x] Authenticated Codex smoke: `AGORA_CODEX_OK` using Codex CLI 0.144.1.
- [x] Authenticated Kiro smoke: `AGORA_KIRO_OK` using Kiro CLI 2.12.2 (0.05 credits).
- [x] Claude CLI 2.1.210 reached the provider session limit; retry after the reported 20:40 JST reset window. This is an external account availability result, not an Agora failure.
- [x] Docker acceptance unavailable because Docker CLI is not installed on this machine; local uv/Next acceptance is complete.
- [x] Prepared the 0.5.0 changelog, synchronized backend/frontend/uv-lock versions, and added release operations documentation.
- [x] Updated English, Chinese, and Japanese project summaries and roadmaps with the Delivery Control Plane capabilities.
- [x] Claude final release review pass 1: `CHANGES_REQUESTED` (locale-decoded file reads and an artifact-panel reopen regression).
- [x] Fixed UTF-8 read paths for tools, artifact previews, and configuration; synchronized API/health versions; preserved explicit artifact-panel close behavior.
- [x] Added UTF-8 round-trip, artifact preview, configuration, and API-version regression coverage.
- [x] Reran the targeted backend suite (101 passed), full non-integration suite (281 passed), compile check, full ESLint, and production build.
- [x] Claude targeted release re-review: `APPROVE`; both blockers resolved with no remaining high/medium findings.
- [x] Commit the reviewed Phase 8b release closure locally (the commit containing this snapshot).

## 2026-07-16 — Ubuntu Docker Acceptance Follow-up

- [x] Removed the clean-build dependency on an untracked top-level `skills/` directory.
- [x] Copied backend sources before the Hatchling editable install so a clean image build sees the package.
- [x] Corrected Compose skills mounts and persisted both SQLite `data/` and project `.agora/` state.
- [x] Added an API container health check and matched CLI/API durable mounts.
- [x] Added a complete Ubuntu build, health, UI/API, persistence, evidence, and cleanup acceptance procedure.
- [x] Documented that the Docker socket mount is host-privileged and that native CLI adapters are not installed in the stock image.
- [x] Compose YAML parsing, release-layout contract tests (9 passed), and `git diff --check` passed locally.
- [x] Claude Docker/acceptance review: `APPROVE`; no high/medium release blockers.
- [x] Claude targeted clean-build-order re-review: `APPROVE`.
- [ ] Actual Docker build and runtime acceptance remain pending on the user's Ubuntu Docker host.
- [x] Commit the reviewed Ubuntu Docker acceptance follow-up locally (the commit containing this snapshot).

## 2026-07-16 — Agora Protocol & Domain Freeze

### Scope

- [x] Recovered the final architecture and C1-C16 three-runtime consensus.
- [x] Confirmed `main` is at `fce21ad` and matches `origin/main`.
- [x] Identified and protected the pre-existing
  `frontend/pnpm-workspace.yaml` change.
- [x] Added the repository development/resume contract in `AGENTS.md`.
- [x] Added the normative v1 freeze document in
  `docs/architecture/protocol-domain-freeze-v1.md`.
- [x] Add executable Context Pack, Handoff Pack, NativeStateSnapshot,
  Artifact, Evidence, Approval, Run protocol, Gate, and Runner contracts.
- [x] Generate and check in deterministic JSON Schemas.
- [x] Add Task/Stage/Gate transition guards.
- [x] Add deterministic Gate evaluation and next-safe-action derivation.
- [x] Add Approval invalidation and downstream Stage/Gate stale propagation.
- [x] Add M2 publication rules and Runner isolation/recovery validation.
- [x] Add workflow-polish and deal_analysis regression fixtures.
- [x] Run focused and full relevant backend verification.
- [x] Obtain Kiro CLI protocol/AI-DLC review and independent Claude Code review;
  address all actionable
  findings.
- [x] Commit the reviewed freeze increment locally (the commit containing this
  snapshot).

### Recovery note

No product database migration or frontend navigation change is part of this
increment. If interrupted, resume from the first unchecked item above after
reconciling Git status.

### Verification and review log

- Focused protocol suite before external review: 18 passed.
- Schema export check, Python compile, and `git diff --check`: passed.
- Backend non-integration run: 293 passed, 18 deselected, 7 static-frontend
  route failures because `frontend/out` was not present.
- Comparable backend run excluding `tests/test_web_ui.py`: 256 passed,
  18 deselected.
- Kiro CLI review pass 1: `CHANGES_REQUESTED`.
- Kiro findings being addressed: bounded Evidence details; explicit Gate stale
  outputs; repository/ref/commit-scoped Evidence; methodology-scoped snapshot
  identity; canonical snapshot ordering; explicit Windows-only Runner schema;
  recovery marker/Attention plan; bounded repair decision.
- Claude Code review pass 1 did not return a verdict because the external API
  connection was refused. Retry after the Kiro fixes and automated rerun.
- Kiro CLI review pass 2: `APPROVE`; no remaining high/medium protocol or
  AI-DLC findings.
- Claude Code smoke in safe mode: passed.
- Claude Code core review: `CHANGES_REQUESTED`.
- Claude findings being addressed: reject Windows drive/UNC paths in every
  repository-relative path contract; require Approval and bound Artifacts to
  share a commit; stale Approval on Artifact deletion or commit change; ignore
  unrelated Evidence kinds; reject credential-reference traversal.
- Focused protocol suite after both review fixes: 33 passed.
- Final comparable backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 271 passed, 18 deselected, 3 existing dependency/
  Windows event-loop cleanup warnings.
- Final Schema export check, Python compile, and `git diff --check`: passed.
- Claude Code targeted re-review: `APPROVE`; no high/medium findings.
- Kiro CLI targeted security/AI-DLC re-review: `APPROVE`; no high/medium
  findings.
- A timed-out Claude review process was detected and terminated; no lingering
  Claude/Kiro review process or `.gitconfig.lock` remained before commit.

### Next safe action

Start the Control Plane v2 persistence increment with a migration-safe
Artifact/Evidence/Approval/Gate registry:

1. define SQLite tables and append-only events without rewriting existing 0.5
   Task/Run data;
2. persist canonical Artifact versions and active Evidence scope;
3. persist Approval invalidation plans atomically with Gate stale and Stage
   reopen events;
4. add restart/idempotency tests before exposing new API routes.

## 2026-07-16 — Control Plane v2 persistence

### Scope

- [x] Added an additive SQLite registry for Stages, Gates, Gate requirements,
  active Evidence, immutable Artifact versions, immutable Approvals, audit
  events, and replayable operation receipts.
- [x] Preserved all existing Task, Run, Requirement, Attention, and Workflow
  tables and rows; the legacy-schema regression verifies their SQL and data are
  unchanged.
- [x] Bound Artifact, Evidence, and Approval registration to an existing Task,
  project, and configured Stage/Gate.
- [x] Enforced Evidence-to-Gate Task isolation in both the store and a composite
  SQLite foreign key.
- [x] Made Stage plus Gate configuration atomic and restart-idempotent.
- [x] Persisted legal Gate evaluation transitions through `evaluating`, with
  optimistic versions and append-only audit events.
- [x] Made Approval stale, Gate stale, Stage reopen/reconciliation propagation,
  Task events, and operation receipts one atomic transaction.
- [x] Added legal invalidation paths for pending, blocked, needs-review,
  running, reconciliation-required, completed, and failed Stages.
- [x] Added migration, rollback, restart replay, conflicting replay,
  concurrency, cross-task, cross-project, and invalid operation-key tests.
- [x] Documented the persistence boundary and deferred API/capacity work in
  `docs/architecture/control-plane-v2-persistence.md`.
- [x] Obtained independent Kiro CLI and Claude Code reviews, fixed all blocking
  findings, and received `APPROVE` from both re-reviews.
- [x] Commit the reviewed persistence increment locally (the commit containing
  this snapshot).

### Verification and review log

- Focused registry suite before review fixes: 10 passed.
- Kiro CLI review pass 1: `CHANGES_REQUESTED` for defensive row-count checks,
  Stage invalidation policy/tests, and adversarial replay coverage.
- Claude Code review pass 1: `CHANGES_REQUESTED` for cross-task Evidence
  activation, Gate state-machine divergence, and non-atomic Stage/Gate
  configuration.
- Fixed the Evidence isolation vulnerability at both application and database
  layers.
- Made Gate evaluation follow
  `pending|blocked|stale -> evaluating -> passed|blocked`.
- Folded Stage creation and Gate configuration into one transaction.
- Added version/status guards to Approval, Gate, and Stage invalidation writes.
- Expanded downstream invalidation to legal frozen Stage transition paths.
- Added rollback, cross-task, cross-project, operation-key, and state-path
  regressions.
- Final registry plus frozen-protocol suite: 49 passed.
- Final comparable backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 287 passed, 18 deselected, 3 existing dependency/
  Windows event-loop cleanup warnings.
- Protocol Schema export check, Python compile, and whitespace diff check:
  passed.
- Kiro CLI targeted re-review: `APPROVE`; no blocking high/medium findings.
- Claude Code targeted re-review: `APPROVE`; no high correctness findings.
- Claude noted that a repository/ref-wide invalidation can hold SQLite's writer
  lock as affected Task count grows. The architecture document now requires a
  load-tested operational bound or resumable atomic batching protocol before
  large shared-repository deployment.
- Completed/timeout-returned Claude child processes were terminated; no
  lingering Claude/Kiro process or `.gitconfig.lock` remained before commit.

### Next safe action

Expose the reviewed registry through a bounded Control Plane API increment:

1. define authorization and project/task scope checks at the route boundary;
2. add create/read/evaluate/invalidate request limits and conflict mappings;
3. offload synchronous SQLite work from the FastAPI event loop;
4. add API acceptance tests without changing the frozen persistence semantics.

### Program-level progress calibration

- Default progress metric from this point forward: the ordered Agora Control
  Plane transformation, not the legacy Agora 0.5 feature completion.
- Completed transformation stages: 2:
  1. protocol, domain model, and state-machine freeze;
  2. Control Plane persistence and Registry.
- Milestone-count completion is deferred until the grouped remaining-stage
  inventory is normalized; use the work-weighted estimate below meanwhile.
- Work-weighted completion: approximately 20% to 25%, because the first two
  stages are architecture-heavy foundations.
- The current persistence increment itself is 100% complete, but the overall
  transformation is not user-visible or near completion yet.
- Existing 0.5 Runner, Workflow, Attention, and UI capabilities do not count as
  completed transformation stages until they are integrated with the new
  protocol, Registry, Context/Handoff, and orchestration contracts.
- Remaining ordered program work: bounded Control Plane API; Runner/Agent
  Adapter integration; Context/Handoff and layered memory runtime; AI-DLC and
  Skills integration; Claude/Kiro/Codex orchestration; integrated console UI;
  M2/M3 legacy-data migration; Docker and end-to-end acceptance.

## 2026-07-17 — AI-DLC foundation orchestration

### Scope

- [x] Added a provisional, version-pinned Codex -> Claude -> Kiro planning and
  review method under the authoritative Task entry point.
- [x] Added append-only Plan, Stage, Run, and usage-ledger persistence with
  semantic-result and explicit human-approval gates.
- [x] Added read-only, bounded native runtime adapters and CLI task operations.
- [x] Added restart recovery that refuses duplicate dispatch for live or
  uninspectable runtime processes.
- [x] Replaced the POSIX-only `os.kill(pid, 0)` assumption on Windows with a
  non-destructive `OpenProcess`/`GetExitCodeProcess` inspection.
- [x] Added Windows-safe recovery regressions; the previously self-terminating
  focused suite now completes normally.
- [x] Run the complete required verification set.
- [x] Obtain Kiro methodology/reconciliation review and independent Claude
  implementation review; fix all actionable findings.
- [x] Commit only after both review gates approve (the commit containing this
  snapshot).

### Verification and review log

- Focused orchestration suite after the Windows process-inspection fix:
  10 passed.
- Related orchestration, Task control-plane, and project suite: 18 passed,
  1 existing Starlette/httpx deprecation warning.
- Full backend suite excluding static-export-dependent `test_web_ui.py`:
  297 passed, 18 deselected, 3 existing dependency/Windows event-loop cleanup
  warnings.
- Protocol Schema export check, Python compile, `git diff --check`, and the
  `agora task --help` CLI smoke: passed.
- Kiro review pass 1: `APPROVE`; no high/medium methodology, state-ownership,
  reconciliation, or Windows process-inspection findings.
- Claude review pass 1: `CHANGES_REQUESTED` for incomplete semantic-text
  redaction and timeout success depending implicitly on a non-zero exit code.
- Fixed the persistence boundary for semantic summaries, findings, blockers,
  and audit payloads; timeout is now a separate persisted dimension and an
  unconditional failure condition.
- Also rejected duplicate claims before process spawn, stopped children on PID
  attachment failure, failed closed when no PID was persisted, mapped missing
  Tasks to bounded CLI errors, and validated budgets before Task creation.
- Expanded focused regressions from 10 to 19 tests; post-fix related suite:
  27 passed, 1 existing Starlette/httpx deprecation warning.
- Kiro targeted re-review: `APPROVE`; no high/medium findings. It identified a
  non-blocking interruption-audit gap, which was fixed with an atomic,
  redacted `orchestration.run_interrupted` Task event and regressions.
- Claude targeted re-review: `APPROVE`; both original medium findings and all
  actionable low findings were confirmed fixed.
- Final narrow Kiro and Claude audit-event re-reviews: `APPROVE`; no actionable
  findings.
- Final post-review focused orchestration suite: 19 passed.
- Final post-review backend suite excluding `test_web_ui.py`: 306 passed,
  18 deselected, 3 existing dependency/Windows event-loop cleanup warnings.
- Final Protocol Schema export check, Python compile, `git diff --check`, and
  `agora task --help` smoke: passed.
- Review subprocesses completed. One unrelated Claude process that predates
  this development session remains user-owned; no Kiro process or
  `.gitconfig.lock` remains.

### Recovery note and next safe action

The pre-existing `frontend/pnpm-workspace.yaml` change remains user-owned and
must not be staged. The review gate is complete. The next safe action after the
commit containing this snapshot is to return to the ordered bounded Control
Plane API increment before claiming the provisional planning loop as a
completed transformation stage.

## 2026-07-17 — Windows native runtime recovery (active)

### Scope and recovery findings

- [x] Reverted the premature Task Workbench UI demo in `740329a`; UI remains
  deferred until the orchestration path is operationally proven.
- [x] Reproduced the original Codex failure as a Windows launcher failure:
  native CLI names resolve to PowerShell/CMD wrappers that cannot be passed
  directly to `asyncio.create_subprocess_exec` without a shell.
- [x] Added fail-closed resolution for audited direct-executable and npm
  Windows wrapper shapes without enabling `shell=True` or interpolating the
  runtime prompt into a command string.
- [x] Separated a process that never started (exact zero usage), a started
  process with estimated usage, and an interrupted/uninspectable process with
  unavailable usage in both the run record and append-only usage ledger.
- [x] Added a configurable orchestration network policy; the repository default
  is `direct` so inherited broken proxy variables are removed from native CLI
  subprocesses.
- [x] Made CLI status/error rendering safe on legacy Windows code pages without
  changing the terminal byte encoding.
- [x] Completed a real Codex smoke through the resolved npm wrapper and direct
  network mode. The native runtime returned a valid semantic blocker because
  the demo Task lacks roles, process, handoff contract, and success criteria;
  this is an expected Agora Task outcome rather than a transport failure.
- [x] Obtained the required Kiro methodology/reconciliation review using the
  user-requested `claude-opus-4.8` model and an independent Claude Code
  correctness/security review.
- [x] Fixed all actionable findings and reran the complete verification set;
  both final review gates returned `APPROVE`.
- [x] Committed the reviewed Windows native runtime recovery locally (the
  commit containing this snapshot).

### Verification log

- Initial focused orchestration/config suite: 33 passed.
- Kiro Opus 4.8 review pass 1: `CHANGES_REQUESTED` for unbounded child-output
  buffering and missing security-critical wrapper rejection regressions.
- Replaced `communicate()` buffering with concurrent bounded-tail stream
  capture and added traversal, relative target, unreadable wrapper, unavailable
  Node, oversized-output, and real no-shell injection tests.
- Kiro runtime re-review: `APPROVE`; both findings resolved.
- Kiro ledger/reconciliation review: `CHANGES_REQUESTED` for treating unknown
  interrupted usage favorably in the retry budget and for swallowing genuine
  asyncio cancellation. Its migration concern was disproved against the
  initial orchestration commit, where both measurement columns already exist.
- Charged unavailable settlements at their per-run reservation in the token
  gate and propagated cancellation after stopping/reaping the child; added
  repeated-interruption budget and real-child cancellation regressions.
- Kiro ledger re-review: `APPROVE`; no remaining high/medium findings.
- Claude independent review: `APPROVE` with a non-blocking inherited-pipe drain
  observation. The observation was nevertheless addressed by bounding
  post-stop capture drain and rejecting prompt-at-argv-zero configurations.
- Claude targeted re-review: `APPROVE`; no remaining high/medium findings.
- Kiro Opus 4.8 final targeted review of the post-stop drain changes:
  `APPROVE`; no remaining high/medium findings.
- Final focused orchestration/config suite: 39 passed.
- Full non-integration backend suite excluding static-export-dependent
  `tests/test_web_ui.py`: 317 passed, 18 deselected, 3 existing dependency/
  Windows event-loop cleanup warnings.
- Protocol Schema export check, Python compile, `git diff --check`, and
  `agora task --help` smoke: passed.
- No frontend validation is required for this increment because the demo UI was
  reverted and the only frontend worktree difference remains the user-owned
  `frontend/pnpm-workspace.yaml` change.

### Current next safe action

The reviewed runtime recovery is committed locally. Preserve and exclude the
user-owned `frontend/pnpm-workspace.yaml` change. The next safe product action
is to write a concrete Task contract (roles, workflow, Context/Handoff
expectations, and acceptance criteria) and run the CLI-first three-runtime loop
before returning to UI work.
