# Agora Control Plane Development Progress

## 2026-08-10 - Chinese UI, control-plane refactor, task retire, Windows Codex launch (review gate: CHANGES_REQUESTED)

Branch `feature/frontend-i18n`, PR #6. NOT merged to main — both review gates
returned CHANGES_REQUESTED; the frontend is mergeable on its own, the backend
retire change is blocked pending a design decision (item 2 below).

Frontend (self-contained; no backend contract change):
- Client-side i18n under `frontend/src/lib/i18n/` (EN/ZH dictionaries, 545 keys,
  fully paired; language toggle; enum/status localization; pre-paint guard that
  removes the locale flash).
- `control-plane/page.tsx` split into `shared` / `ledgers` / `human-actions` /
  `projection-dashboard` components; retired-council `welcome.tsx` removed.
- Portfolio task-card lifecycle advance controls and a cancel/retire control,
  each behind an explicit confirm step; deep-link dispatch into the New Run panel.
- Verified: `pnpm build` (10 static pages), `pnpm lint` (0 errors), `tsc`, and an
  EN==ZH dictionary key-parity check.

Backend increments (MUST NOT merge to main until item 2 is resolved):
1. `tasks/transitions.py`: allow `done`/`failed` -> `cancelled` so a finished
   compatibility Task can be retired from the board; `cancelled` stays terminal.
   `FINISHED_STATES`/`TERMINAL_STATES` are documentation constants derived from
   `ALLOWED_TRANSITIONS` and asserted by a graph-consistency test; `can_transition`
   now fails closed via `.get(current, set())`.
2. `execution/dispatcher.py`: route the CLI spawn (non-bridge path only) and the
   Codex app-server spawn through `resolve_runtime_command()` (PATHEXT + npm
   `.cmd` unwrap) so Codex launches on Windows; `RuntimeLaunchError` fails the run
   (redacted) before any child starts.

Tests: transition-graph invariant test + store cancel/retire test.
`tests/test_tasks_controlplane.py` + `tests/test_execution.py`: 29 passed (run
with `--basetemp=<clean dir>` to avoid the environment's `pytest-of-wuche`
temp-dir permission error). End-to-end: a dispatched Codex run reached running
(real pid) and succeeded (exit 0), writing its output file in the worktree.

Review gates (both independent, read-only):
- Codex: CHANGES_REQUESTED. Applied fixes: bridge-path resolution scope,
  TERMINAL_STATES/graph consistency, regression tests.
- Kiro: CHANGES_REQUESTED. Applied fixes: `can_transition` fail-closed `.get`,
  redact the CLI launch-failure message, clarify the derived-constants comment,
  and this PROGRESS entry (Kiro flagged it was missing/misplaced).
- No formal APPROVE from either gate yet.

Resolved / open:
2a. RESOLVED (both gates' HIGH): removing a finished task from the board is now a
   display-only `archived_at` flag (`TaskStore.set_archived`, version-checked +
   idempotent, does NOT change lifecycle state) with `POST /tasks/{id}/archive|
   unarchive` and a board "Show archived" toggle. The `done`/`failed` -> `cancelled`
   overload was reverted; `cancelled` is terminal again. No compat-vs-authoritative
   divergence — the authoritative state is never touched.
2c. RESOLVED for archive: the archive API is version-checked and idempotent. (The
   legacy cancel API's optional-version behavior is unchanged and now unused by
   the UI.)
2b. MEDIUM (still open): persist the redacted resolved launcher argv + hash to the
   run record for audit provenance.

### Current next safe action (this increment)

The frontend can merge independently. Backend: 2a/2c resolved; 2b (launcher audit)
is the remaining backend item. Re-run both review gates for a formal APPROVE
before merging the backend to main.

## 2026-08-04 - Agora 1.0 release closeout (reviewed)

### Release boundary

- [x] Froze `docs/architecture/agora-1.0-release-readiness-v1.md` before
  closeout. Agora 1.0 is the reviewed local Task delivery control-plane
  baseline; native subscription/serviceability, dynamic roles, arbitrary
  local-model adapters, runtime substitution, POSIX embedded isolation, and
  large shared-repository operations remain explicit external conditions or
  post-1.0 enhancements.
- [x] Published consistent `1.0.0` metadata through the Python package, API,
  frontend package, locked Python graph, and changelog. Reconciled the
  authoritative latest requirements so completed Stage routing, Task contract,
  consultation, unified projection, AWS AI-DLC sequence, and authenticated UI
  are no longer listed under a stale `Still required` heading.
- [x] Replaced corrupted Chinese and Japanese READMEs with valid UTF-8 pages,
  updated the English entry page and locked startup commands, archived the 0.5
  operations document as historical, and added the practical Chinese
  `docs/usage/agora-1.0-tutorial.md` covering install, auth, startup,
  deterministic acceptance, first Task, provider safety acknowledgement,
  status, consult/adopt/reject, approval, resume/retry, and shutdown.
- [x] Added `scripts/run_http_startup_acceptance.py`. It copies current config
  into an isolated temporary instance, generates a non-printed bearer, starts
  a real uvicorn subprocess, checks health/static/authenticated/fail-closed
  HTTP behavior, stops the process, and removes all temporary state. It does
  not create a Task or call a provider/model.
- [x] Preserved all Codex, Claude Code, and Kiro runtime adapters, route pins,
  workspaces, normalization, research, execution, and UI adapter choices.
  Kiro was not invoked, `.kiro/` was not changed or staged, and unavailable
  Kiro serviceability remains a truthful Stage blocker rather than a release
  correctness failure or silent substitution trigger.

### Verification and review state

- [x] Locked `uv sync --locked --extra dev` updated the environment from
  Agora 0.5.0 to 1.0.0; locked pnpm install passed. The complete current
  non-integration collection passed 723/723 using the previously verified
  mutually exclusive long-file split: methodology 214 + 7 + 8 + 6, and all
  remaining files 488. The sole warning is the existing FastAPI TestClient
  notice that Starlette's `httpx` integration is deprecated in favor of
  `httpx2`.
- [x] Frontend Control Plane tests passed 14/14, ESLint passed without errors
  or warnings, and Next.js 16 production build/static export passed with all
  eight supported routes. Protocol Schema check, isolated `compileall` for the
  package and HTTP acceptance script, UTF-8 user-doc scan, stale version/
  migration-marker scan, and `git diff --check` passed.
- [x] Deterministic formal Task acceptance passed with
  `provider_or_model_called=false`, three passed Stages/Gates, explicit human
  Plan approval, cold SQLite reopen to `completed`, and temporary workspace
  removal.
- [x] Isolated live HTTP acceptance passed with
  `provider_or_model_called=false`: `/health` returned 1.0.0, `/` and
  `/control-plane` served the static Task UI, the protected Task index returned
  401 without a bearer and 200 with the scoped bearer, `/api/chat` returned
  `410 legacy_council_retired`, and the server/temp state were removed.
- [x] Docker client 29.3.1 is installed, but the local Docker daemon is not
  running, so no Docker image/runtime result is claimed. This is an external
  environment condition; the documented direct Windows startup was exercised
  with a real uvicorn process and passed.
- [x] Two bounded read-only Claude Code Haiku reviews independently inspected
  release correctness/startup safety and documentation/command usability.
  Both returned exact `VERDICT: CLAUDE_APPROVE` with no HIGH/MEDIUM issue.
  Neither review could write files. Kiro was intentionally not called because
  its current contract is unavailable.

### Current checkpoint and next safe action

The reviewed release closeout is committed and pushed as
`920573d9e990e850661ed044a51487dbfa6067d4` on `origin/main`, directly atop
pushed Council-cleanup commit
`e2e30cec209024271b49942365766498a07d2b12`. User-owned `.kiro/` and legacy
pytest temporary directories remain untracked and untouched.

No repository-defined Agora 1.0 implementation work remains. The next safe
action is to keep this baseline stable and begin a new, separately frozen
post-1.0 increment only when explicitly requested. The known candidates are
dynamic role/local-model configuration, policy-governed runtime substitution,
additional provider usage/cost integrations, and wider isolation/operations
support. Kiro remains configured for later reactivation; no post-1.0 item is a
hidden prerequisite for using the current local Control Plane or tutorial.

## 2026-08-04 - unreachable 0.5 Council source cleanup (reviewed)

### Bounded implementation

- [x] Froze `docs/architecture/legacy-council-source-cleanup-v1.md` before
  implementation. The boundary deletes only the already-unreachable 0.5
  autonomous Council implementation and preserves the central fail-closed
  compatibility response, the complete Task control plane, explicit
  single-runtime consultation, and future runtime extension points.
- [x] Removed the Council-only agent/profile, direct model-provider, shared
  context, memory extraction, learned-skill, tool-execution, web-tool, Docker
  sandbox, unregistered API/session/artifact, test-mock, and runnable demo
  source. Git history remains the recovery path; no compatibility flag can
  reactivate autonomous AI-to-AI discussion.
- [x] Removed unreachable chat/session/artifact React components, hook, types,
  streaming client, markdown/highlight dependencies, and dead package-lock
  entries. `frontend/src/lib/api.ts` now exposes only the active static-console
  API-origin helper. The authenticated Delivery Control Plane remains the
  exported product UI.
- [x] Removed Council-only direct-provider, agent, skill-store, sandbox, and web
  tool configuration plus their Python dependencies and Docker mounts. Kept
  `memory.data_dir` as the active generic data-root fallback and regenerated
  both locked dependency graphs.
- [x] Preserved Codex, Claude Code, and Kiro project workspaces, research
  workers, orchestration runtime commands/result normalization, execution
  adapters, UI adapter choices, and capability boundaries. `.kiro/` was not
  read, changed, staged, or removed. No Kiro process was invoked, and dynamic
  role/local-model configuration was intentionally not implemented.

### Verification and review state

- [x] The complete current non-integration collection is 723 tests. The
  methodology migration file passed 235/235 as four mutually exclusive shards
  (214 + 7 + 8 + 6), and every other file passed 488/488. A single serial run
  was attempted first with 4-minute and then 12-minute tool limits; both were
  cut off without an assertion failure while repeatedly reconstructing the
  eight-Stage methodology/completion-review chain. The exact apparent stop
  test passed independently in 22.28 seconds. The verified shards use separate
  temporary roots and together cover the identical 723-item collection.
- [x] Deterministic Task acceptance passed with `provider_or_model_called=false`,
  all three formal Stages and Gates passed, explicit human Plan approval,
  persisted reopen verification, and runtime labels `codex`, `claude`, and
  `kiro`. Protocol Schema check, isolated `compileall`, root config/Kiro
  retention assertions, `git diff --check`, and post-lock-sync focused tests
  passed.
- [x] Frontend Control Plane tests passed 14/14, ESLint passed with zero errors
  or warnings, and Next.js 16 production build/static export passed with the
  eight supported routes. The old `use-chat.ts` warnings disappeared with the
  unreachable bundle.
- [x] Locked dependency synchronization removed `lxml`, `prompt-toolkit`,
  `sse-starlette`, and their obsolete transitives from the Python environment;
  the locked `dev` extra was restored immediately and the post-sync config/UI
  shard passed 20/20.
- [x] Two bounded read-only Claude Code Haiku reviews inspected the
  backend/config/runtime boundary and the frontend/packaging/route boundary.
  Both returned explicit `VERDICT: CLAUDE_APPROVE` with no HIGH/MEDIUM issue.
  Two earlier Claude sessions requested broader Bash access and produced no
  verdict; they are not counted. The final frontend review used only
  Read/Grep/Glob, and the backend review used only preapproved read-only Git
  commands.
- [x] Kiro was intentionally not invoked because its current contract is
  unavailable. Per the user's instruction, Claude Code is the temporary
  auxiliary reviewer while Kiro support remains intact for later use.

### Current checkpoint and next safe action

This reviewed cleanup is directly atop pushed candidate-disposition commit
`31903f4a81912c9b632dd86ff6fa88da83e1777f`. User-owned `.kiro/` and legacy
pytest temporary directories remain untracked and untouched.

The exact next action is to stage only the frozen cleanup files, commit, and
push. Then continue the repository-defined 1.0 closure audit: repair the
mojibake Chinese/Japanese READMEs, exercise a clean documented startup path,
resolve only concrete remaining release/readiness gaps, and produce the
practical end-user Agora tutorial. Do not reintroduce autonomous discussion,
remove Kiro, or implement dynamic role/local-model configuration in that
closeout.

## 2026-08-04 - authenticated consultation candidate disposition (reviewed)

### Bounded implementation

- [x] Froze
  `docs/architecture/control-plane-ui-candidate-disposition-v1.md` before
  implementation. The boundary exposes only explicit human `adopt`/`reject`
  of an already-registered immutable advisory candidate; it does not expose
  consultation dispatch, AI discussion, voting, runtime substitution, or
  lifecycle authority.
- [x] Added the distinct `control_plane.candidate.dispose` permission and the
  authenticated project/Task/candidate-scoped
  `POST /api/control-plane/projects/{project_id}/tasks/{task_id}/consultation-candidates/{candidate_id}/dispositions`
  command. Its actor comes only from the bearer principal. Action, bounded
  redacted rationale, exact candidate content hash, expected Plan version, and
  stable operation key are mandatory; caller-supplied actor/decision/approval
  fields are rejected.
- [x] Reused the frozen candidate-domain semantics in one `BEGIN IMMEDIATE`
  transaction. Adoption creates or reuses the exact versioned TaskDecision,
  records a hash-sealed disposition, and increments the Plan version once;
  rejection records only the disposition. Both append bounded Task audit and
  the shared authenticated exact-replay receipt atomically. The receipt makes
  candidate authority, frozen Task/Stage/Gate mutation, formal Approval
  creation, and runtime invocation explicitly false.
- [x] Added global `control_operations` collision protection in both
  directions with Attention/other authenticated commands, exact replay of the
  original receipt, project/candidate non-enumeration, candidate hash and Plan
  concurrency checks, authoritative inventory/Stage/role/runtime route
  revalidation, and guards against both an active operational Run and an
  unsettled formal protocol Run. Raw rationale is redacted before hashing or
  persistence; injected receipt-write failure rolls back decision,
  disposition, Plan version, audit, and receipt together.
- [x] Ordered pending candidates ahead of disposed history in the paginated
  projection and made the console request the bounded 200-record snapshot, so
  every permitted pending human action can expose its matching immutable body.
  The UI shows advisory/non-formal markers, pinned runtime, bounded decision,
  analysis, sources, Plan version, and candidate hash; binds the visible hash
  and version; preserves one retry key across an unchanged uncertain attempt;
  aborts stale mutation/projection leases; refreshes authority after success;
  and keeps Plan approval hidden until all candidates are disposed.
- [x] Preserved `.kiro/`, Kiro runtime pins, configuration, workspaces, and the
  future configurable-role/local-model boundary. No Kiro command was invoked,
  no Kiro support was removed, and no role/local-model configurability was
  implemented in this increment.

### Verification and review state

- [x] New candidate disposition API regressions passed 8 tests. They cover
  atomic adoption and rejection, principal actor binding, redaction and secret
  absence, exact replay, permission whitelist, project/Task/candidate
  non-enumeration, strict request shape, stale hash/Plan conflicts, active
  operational and unsettled formal Runs, second action/conflicting replay,
  shared Attention operation-key collisions in both directions, transaction
  rollback, no formal Approval/lifecycle mutation, and pending-first projection
  behavior after 100 disposed historical candidates.
- [x] The candidate domain/API focus passed 13 tests and the related Control
  Plane, lifecycle, routing, protocol, orchestration, and Plan approval shard
  passed 147 tests. The complete non-integration suite including Web UI ran all
  tests: 833 passed and 15 were deselected. Its only two failures were the same
  retired Council/executor tests whose literal Linux `/tmp/...` paths resolve
  to sandbox-denied `D:\tmp` on Windows; those exact two tests passed 2/2 in
  0.26 seconds outside the filesystem sandbox. No Agora assertion or semantic
  behavior failed.
- [x] Frontend Control Plane tests passed 14 tests. ESLint completed with zero
  errors and the same 11 warnings in retired `use-chat.ts`; Next.js 16
  TypeScript production build and static export passed with the eight supported
  routes. Protocol Schema check, isolated `compileall`, root YAML/config load,
  permission resolution, and `git diff --check` pass.
- [x] Two bounded read-only Claude Code Haiku reviews independently inspected
  the backend/transaction/test boundary and the frontend/config/lease boundary.
  Both returned explicit `VERDICT: CLAUDE_APPROVE` with no HIGH/MEDIUM issue.
  A broad Codex CLI review was initially started from a mistaken interpretation
  of the user's replacement instruction, produced no verdict, and timed out; a
  later bounded Codex attempt was stopped immediately after the user clarified
  that Claude Code, not another Codex process, is the current auxiliary
  reviewer. Neither Codex CLI attempt is counted as a gate.
- [x] Kiro was intentionally not invoked because its current contract is
  unavailable. Per the user's clarification, implementation remains with
  Codex and the currently available auxiliary review surface is Claude Code;
  Kiro remains intact for later use.

### Current checkpoint and next safe action

This reviewed increment is directly atop pushed Plan approval commit
`fe21feee6e8d969cc9e03ba137eb2fd6cd7675ce`. User-owned `.kiro/` and legacy
pytest temporary directories remain untracked and untouched.

The exact next action is to stage only this candidate-disposition increment,
commit, and push. Then audit the repository-defined Agora 1.0 closure criteria
from the protocol freeze, active architecture documents, executable acceptance,
current README/CLI startup path, and remaining progress entries. Exercise the
clean local end-user startup path and close only concrete mainline gaps before
writing the practical Agora tutorial. Do not reintroduce autonomous discussion,
remove Kiro, or begin dynamic role/local-model configuration during that audit.

## 2026-08-04 - authenticated atomic Plan approval and live service closeout (reviewed)

### Bounded implementation

- [x] Froze `docs/architecture/control-plane-ui-plan-approval-v1.md` before
  implementation. Added the distinct `control_plane.plan.approve` permission;
  the existing `control_plane.approve` remains the artifact-bound formal
  protocol/methodology authority and is not silently widened.
- [x] Added the authenticated project/Task-scoped
  `POST /api/control-plane/projects/{project_id}/tasks/{task_id}/plan-approvals`
  command. Its principal-derived actor, bounded redacted rationale, expected
  frozen Task version, expected Plan version, and globally shared operation key
  are mandatory. Exact retry returns the original receipt with
  `replayed=true`; cross-command or changed-input reuse fails 409.
- [x] Made non-methodology Task completion and Plan approval one
  `BEGIN IMMEDIATE` transaction. It fails closed unless the Task is exactly
  `needs_review`, the Plan is exactly `awaiting_approval`, deterministic
  lifecycle authority is `all_stages_passed`, and there is no methodology
  execution contract, running operational Run, unsettled formal protocol Run,
  running explicit consultation, pending consultation candidate, or blocking
  Attention. Task/Plan writes, redacted audit events, and the shared receipt
  all roll back together.
- [x] Kept plan approval separate from every protocol dimension. The receipt
  reports `formal_approval_created=false` and
  `methodology_completion_approval_created=false`; no Stage, Gate, Run,
  Artifact, Evidence, provider, local model, Codex, Claude, or Kiro process is
  invoked by the command.
- [x] Connected the static authenticated console. It renders the form only
  when the sole authoritative human action is the matching `plan_approval`,
  binds visible Task/Plan versions, retains one retry key across an uncertain
  unchanged attempt, aborts superseded mutation/projection leases, refreshes
  from the authoritative projection after success, and clears protected state
  when project, Task, credential, Forget, or unmount changes.
- [x] Added a fail-closed local-owner bearer mapping in `config.yaml` through
  the unresolved `AGORA_CONTROL_PLANE_TOKEN` secret reference and documented it
  in `.env.example`. An unset token authorizes nobody. Added optional
  `AGORA_CONFIG_PATH` selection, below an explicit function argument and above
  normal config discovery, so isolated/local instances can use a separate DB
  without editing repository configuration.
- [x] Preserved `.kiro/`, Kiro runtime pins/configuration, and future
  configurable role/local-model boundaries. Kiro was not called. Autonomous
  AI-to-AI discussion and candidate disposition remain outside this increment.

### Verification and review state

- [x] New Plan approval API regressions passed 9 tests. They cover successful
  mutation and exact replay, redacted fingerprint/audits, cold unified
  projection, distinct permission and non-enumerating scope, body actor
  rejection, blank rationale, stale Task/Plan versions, Gate drift, shared
  Attention-operation collisions in both directions, methodology exclusion,
  every active/unsettled/pending guard, no protocol Approval, and injected
  transaction-tail rollback. The final related Control Plane/lifecycle/protocol
  shard passed 124 tests before the last strictly additive active-work guard;
  the complete suite below includes its final form.
- [x] The complete non-integration suite including the rebuilt static UI ran
  all tests: 825 passed and 15 were deselected. Its only two failures were old
  retired Council/executor tests whose literal Linux `/tmp/...` paths resolve
  to sandbox-denied `D:\tmp` on Windows. The exact two tests passed 2/2 in
  0.28 seconds when rerun outside the filesystem sandbox; there was no
  assertion or Agora behavior failure.
- [x] Frontend Control Plane tests passed 11 tests. ESLint completed with zero
  errors and the same 11 warnings in retired `use-chat.ts`; the Next.js 16
  production build and static export passed with the eight supported routes.
  Protocol Schema check, isolated `compileall`, YAML parsing,
  `git diff --check`, and root configuration loading pass.
- [x] A live isolated FastAPI/static-export instance used a temporary bearer
  and SQLite database. Authenticated discovery observed
  `needs_review / awaiting_approval` with exactly `plan_approval`; the real HTTP
  command returned `completed / ready_for_implementation`, an exact retry
  returned `replayed=true`, a new projection had zero human actions and zero
  formal Approvals, and a full server stop/restart cold-read preserved those
  facts. The temporary service, DB, config, token, and directory were removed.
  Browser setup reported no available browser instances, so no click-through
  result is claimed; frontend interaction remains covered by build and focused
  lease/retry tests.
- [x] The user-designated current Codex review found that the first boundary
  could complete with active/unsettled Runs or an undisposed consultation
  candidate. The final code and UI fail closed on those facts; substantive
  re-review found no remaining actionable issue: `CODEX_APPROVE`.
- [x] Several broad/default Claude attempts produced no verdict, exceeded a
  USD 1 read budget, or timed out and were not counted. Two bounded read-only
  Claude Code Haiku reviews then inspected the actual backend/test boundary and
  the frontend/config boundary independently. Both returned explicit
  `VERDICT: CLAUDE_APPROVE` with no HIGH/MEDIUM findings.
- [x] Kiro was intentionally not invoked under the user's temporary review
  exclusion. This does not remove Kiro or substitute another runtime inside a
  Task; only the development review gate used Codex temporarily.

### Current checkpoint and next safe action

The implementation is directly atop pushed deterministic acceptance commit
`9a1dd27`. User-owned `.kiro/` and legacy pytest temporary directories remain
untracked and untouched.

This reviewed Plan approval/config/UI increment is the next commit directly
atop `9a1dd27`. After it is pushed, the exact next action is to freeze the
separate authenticated human consultation-candidate `adopt`/`reject` boundary
so an explicitly requested candidate can be disposed from the same console
before Plan approval. Do not add autonomous discussion, remove Kiro, implement
dynamic role/local-model configuration, or conflate candidate disposition with
Task/Stage/Gate authority.

## 2026-08-04 - deterministic formal Task mainline acceptance (reviewed)

### Bounded implementation

- [x] Froze `docs/architecture/deterministic-task-acceptance-v1.md` and added
  `scripts/run_task_acceptance.py`. The command creates a script-owned
  temporary Git project and SQLite database, then uses the production
  `TaskOrchestrationService`, `ReadOnlyCliRunner`, protocol parser, Control
  Plane store, routing/preflight decisions, and unified projection.
- [x] The three contract-pinned runtime commands launch real local child
  processes. Their fixture is deterministic and non-AI: it consumes only the
  sealed Context Pack, cross-checks the routing-policy `pinned_runtime` against
  the Task contract, and emits exact sealed Artifact, Evidence, and Handoff
  structures. It does not contact Codex, Claude, Kiro, a provider API, or a
  local model and is guarded by an inherited acceptance-only marker.
- [x] The acceptance run proves all three formal Stages/Runs and Stage Gates
  pass, the Task stops at `needs_review` with exactly one `plan_approval` human
  action, explicit approval completes the authoritative Task, a newly opened
  store reproduces the completed projection, and the temporary workspace is
  removed. Its JSON receipt reports three child process exits at zero, three
  managed Artifacts, three Evidence records, and no remaining human action.
- [x] Replaced the old manually executable `backend/tests/test_scenarios.py`,
  which still routed into Council multi-agent discussion, with a regression
  that invokes the formal deterministic acceptance as a subprocess. The
  obsolete Council scenario is no longer presented as end-to-end product
  validation.
- [x] Kept native runtime semantics unchanged. The receipt's runtime labels
  remain the contract pins `codex`, `claude`, and `kiro`; they identify formal
  routing, not provider calls. Kiro support is retained, and configurable
  roles/local-model adapters plus native provider-backed acceptance remain
  deferred while the Kiro subscription is unavailable.

### Verification and review state

- [x] Direct system-Temp execution completed in about six seconds and emitted
  `task_state_before_approval=needs_review`,
  `task_state_after_reopen=completed`, three passed Stages/Gates/Runs,
  `artifact_count=3`, `evidence_count=3`,
  `persisted_reopen_verified=true`, and
  `temporary_workspace_removed=true` with
  `provider_or_model_called=false`.
- [x] The executable acceptance regression passed 1 test. The combined new
  acceptance plus existing formal protocol orchestration suite passed 66 tests
  in 17.33 seconds. Protocol Schema export/check, isolated script `compileall`,
  `git diff --check`, legacy Council scenario search, and debug-log cleanup
  checks pass.
- [x] The user-designated Codex substitute review found that the first fixture
  version derived `producer.runtime` only from the contract. The final version
  derives it from the sealed routing-policy projection and fails unless that
  pin matches the contract. Final Codex review found no remaining actionable
  issue: `CODEX_APPROVE`.
- [x] The first broad Claude review exceeded its USD 1 budget without a
  verdict and was not counted. A narrowed independent review verified bounded
  execution, Windows handle/cleanup behavior, exact routing and protocol
  bindings, real child process evidence, approval, and cold SQLite reopen, and
  returned `VERDICT: CLAUDE_APPROVE` with no HIGH/MEDIUM/LOW.
- [x] Kiro was not invoked under the user's temporary development-review
  exclusion. This exception does not alter or remove Kiro from the product
  runtime registry or Task contracts.

### Current checkpoint and next safe action

This reviewed increment is directly atop pushed Council-entrypoint retirement
commit `7907a03`. User-owned `.kiro/` and legacy pytest temporary directories
remain untracked and untouched.

The exact next action is to stage only the four acceptance files plus this
progress record, commit, and push. Then start the actual API/static Control
Plane in an isolated configuration and perform browser/API acceptance against
the persisted deterministic Task receipt path. Do not claim native
Codex/Claude/Kiro quality acceptance from this fixture, do not remove Kiro,
and do not begin configurable-role/local-model implementation yet.

## 2026-08-04 - autonomous council entrypoint retirement (reviewed)

### Bounded implementation

- [x] Reconciled the user's "AI must not discuss by itself" requirement with
  repository truth. The consensus 1.0 architecture already defines only an
  explicit, single-pinned-runtime, candidate-only Task consultation, but the
  0.5 default CLI, chat API, static pages, navigation, landing page, and README
  still activated or advertised Scout / Architect / Critic / Synthesizer
  debate. Those surfaces survived because the incremental transformation had
  deliberately added the 1.0 Control Plane alongside the 0.5 product rather
  than retiring compatibility entrypoints.
- [x] Froze `docs/architecture/legacy-council-retirement-v1.md`. Agora is the
  only cross-runtime workflow-state writer; explicit `agora task consult`
  remains one non-authoritative advisory candidate and is not AI-to-AI
  discussion or a formal Run/Artifact/Evidence/Approval/Gate action.
- [x] Replaced the default interactive Council CLI with a minimal `agora task`
  dispatcher. Bare/help invocation provides migration guidance, unsupported
  legacy commands return exit code 2, and both paths remain provider-inert.
- [x] Removed chat/agent/session/share/skill/memory/profile routers and the
  chat-only caller-path Artifact preview/download router from the active
  FastAPI app. A middleware outside CORS returns stable
  `410 legacy_council_retired` for every method on exact or nested legacy API
  paths before any legacy state can initialize. Exact and nested legacy UI
  paths permanently redirect to `/control-plane`; unknown `/api/*` GETs return
  404 rather than a successful frontend fallback.
- [x] Removed the five Council-only Next.js pages and their primary-navigation
  links. Replaced the public landing page, package description, environment
  guidance, and English/Chinese/Japanese READMEs with the authoritative
  Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
  product semantics. The successful export now contains only the eight current
  routes and no chat/agents/skills/settings/shared artifact.
- [x] Kept existing user databases and native runtime files untouched. The now
  unreachable 0.5 Council source modules have no supported CLI/API/UI activation
  path. Their cleanup is deferred until the authoritative mainline runs end to
  end and a reference audit proves each deletion is Council-only. Codex, Claude,
  Kiro, and the future explicitly configured local-runtime boundary remain
  product capabilities; configurable roles/local-model adapters are deferred.

### Verification and review state

- [x] Final focused retirement plus authenticated Control Plane API suite:
  38 passed. Isolated subprocess regressions prove importing the application
  and rejecting a legacy CLI command do not load Council, provider, memory,
  skill, tool, context, or legacy API state. HTTP regressions cover all methods,
  real CORS preflight, exact/nested prefixes (including the retired
  caller-path Artifact reader), deep UI links, OpenAPI exclusion, unknown API
  fallback, and bare/explicit-help/rejected CLI behavior.
- [x] Before the final caller-path Artifact router was retired, the complete
  non-integration backend suite (including the rebuilt static UI) passed 816
  tests with 15 deselected in 848.11 seconds. After that narrowly scoped app
  routing change, the focused retirement/Control Plane suite passed 38 tests.
  A complete post-change rerun lost its pytest summary when the outer command
  timed out and therefore is not counted as a pass. Completed follow-up shards
  passed 233 tests with 12 deselected; the remaining slow methodology-migration
  preview retry was stopped because it is independent of the app-router change.
  The final post-change broad suite deliberately excluded only the unchanged,
  previously passing methodology-migration-preview file and passed 581 tests
  with 15 deselected in 97.75 seconds. The focused entrypoint/Control Plane
  suite passed 38 tests, and the Kiro/runtime registration, capability,
  preflight, usage, and Task-orchestration preservation suite passed 88 tests.
  This separates direct product evidence from that unrelated slow file rather
  than presenting a partial or timed-out run as full-suite success.
- [x] Frontend Control Plane tests passed 8 tests; lint passed with zero errors
  and 11 warnings in the now-dead 0.5 `use-chat` hook. The Next.js 16 production
  build/static export passed and generated the eight current routes. Its output
  contains no `AI Council`, multi-perspective-council, Council Discussion, or
  Ask-the-council product copy. Protocol Schema export/check,
  system-Temp-isolated `compileall`, CLI help smoke, and `git diff --check`
  pass. A preliminary `D:\tmp` compile attempt failed only at pycache creation
  with uniform `WinError 5`; the required system-Temp rerun passed.
- [x] Independent Claude runtime review first returned `CHANGES_REQUESTED` for
  missing isolated proof that app import cannot initialize Council/provider
  modules, plus LOW method/CORS/deep-link/help gaps. All were fixed and
  regression-pinned; targeted re-review returned `CLAUDE_APPROVE`. Its separate
  frontend/documentation review found one PowerShell-pipeline encoding issue
  around non-ASCII language labels; the English README now uses ASCII-safe
  links to the UTF-8 translations, and final surface re-review returned
  `CLAUDE_APPROVE` with no HIGH/MEDIUM.
- [x] After the unauthenticated caller-path Artifact reader was added to the
  retired API set, a targeted Claude re-review confirmed that the change does
  not affect Task-scoped Control Plane Artifact routes and returned
  `CLAUDE_APPROVE` with no HIGH/MEDIUM.
- [x] Kiro did not review the increment. The CLI returned
  `Authentication failed. Your session may have expired`, and
  `kiro-cli profile` then confirmed `No profiles available`. Two subsequent
  profile checks through 2026-08-04 returned the same administrator-access
  error, for three consecutive blocked goal turns in total. On 2026-08-04 the
  user explicitly suspended Kiro as a development review gate while its
  subscription is unavailable and designated Codex review as the
  temporary substitute for this increment. This process exception does not
  remove Kiro from Agora, change pinned runtime semantics, or authorize runtime
  substitution inside an active Task.
- [x] The user-designated Codex substitute review found one active public
  surface missed by the earlier reviews: `frontend/src/app/layout.tsx` still
  advertised `AI Council` in global metadata. It now describes the Task
  delivery control plane, the rebuilt export is regression-pinned, and the
  final substantive Codex review found no remaining actionable finding:
  `CODEX_APPROVE`. Multiple nested Codex CLI attempts (custom, native, and JSON
  event modes) produced no review/session output and were terminated together
  with only their own child processes; they are recorded as tool failures, not
  verdict evidence. Claude remains the independent implementation reviewer.

### Current checkpoint and next safe action

The validated but uncommitted increment is directly atop clean pushed commit
`dda74b2`. User-owned `.kiro/` and legacy pytest temporary directories remain
untracked and untouched.

The exact next action is to stage only this reviewed entrypoint-retirement diff,
commit it, and push it. After that checkpoint, prioritize a real end-to-end
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate acceptance run.
Do not begin configurable roles/local-model work yet, and do not mechanically
remove Council-era provider/runtime files until the mainline runs and a
separate reference audit proves active Kiro and future runtime boundaries are
preserved.

## 2026-08-03 - authenticated idempotent Attention response console (reviewed)

### Bounded implementation

- [x] Froze
  `docs/architecture/control-plane-ui-attention-response-v1.md` for one new
  authenticated Task-scoped response command. The legacy unscoped
  `/api/attention/{item_id}/respond` route remains unchanged and is not used by
  the 1.0 console.
- [x] Added
  `POST /api/control-plane/projects/{project_id}/tasks/{task_id}/attention/{item_id}/responses`
  behind the new `control_plane.attention.respond` permission. Authentication,
  project membership, Task scope, non-enumerating item binding, optional
  assignee binding, action/kind validation, expiry, and optimistic versioning
  all fail closed; the response/audit actor comes only from the bearer
  principal.
- [x] Added exact replay through one globally unique `operation_key`. Attention
  stores its detail receipt and also participates in the existing shared
  `control_operations` namespace in the same transaction. Exact retries
  validate both ledgers; reuse by any other Control Plane command or different
  Attention input returns 409.
- [x] Made the item update, redacted audit event, optional bidirectional bridge
  `pending -> ready` transition, detail receipt, and shared receipt atomic.
  Capture-only responses are recorded without claiming delivery. Expiry
  commits without a response receipt, and a late `AFTER INSERT` failure
  regression proves all response writes roll back together.
- [x] Added `ControlPlaneAttentionResponseReceipt@1.0`, which reports the
  settled `AttentionItem` and local/capture/delivery-ready effect while
  explicitly keeping `task_state_mutated=false` and
  `formal_approval_created=false`. Approval-shaped Attention remains distinct
  from a formal protocol `Approval` and cannot satisfy a Gate.
- [x] Expanded the frontend Attention type and added bounded question/blocker
  and approval response forms. Unchanged drafts keep one retry key across an
  uncertain network attempt; edits or item-version changes rotate the key.
  Response and refresh requests use separate abortable leases, project/Task/
  credential/Forget/unmount changes invalidate them, and a successful response
  always reloads the authoritative unified projection.
- [x] Kept the slice Attention-only. Creation/cancellation, formal Approval,
  Task completion, Stage/Run/Gate commands, provider substitution, methodology
  migration, and generic mutation surfaces remain deferred.

### Verification and review state

- [x] Final focused Attention and Control Plane API coverage passes 37 tests.
  It includes missing permission, wrong project/Task/item scope, actor spoofing,
  assignee mismatch, action/kind mismatch, redaction, stale/replayed/colliding
  operations, shared-namespace collisions in both directions, expiry,
  capture-only versus bidirectional effects, no Task/formal-Approval mutation,
  and late transactional rollback.
- [x] Frontend lifecycle/path/retry coverage passes 8 tests, including stable
  uncertain-network keys and projection-refresh blocking while a response can
  start. Frontend lint passes with zero errors and the same 12 pre-existing
  warnings. The Next.js 16 production build and 15-page static export pass.
- [x] The final complete non-integration backend suite, including
  `tests/test_web_ui.py` and `tests/test_web_ui_extra.py`, passed 871 tests with
  18 deselected in 1138.39 seconds. Only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warning remain. A preliminary
  ad-hoc `test_web_ui_extra.py` run reached a real retired Anthropic model and
  returned 404; it was not counted as product success or failure, and the
  canonical full suite later passed the same tests in their normal isolated
  order.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`,
  `git diff --check`, and the 15-page frontend build pass after review fixes.
- [x] Independent Codex review returned `CHANGES_REQUESTED` for a separate
  operation-key namespace and a refresh/response loading race. Both were fixed
  and regression-pinned. Targeted re-review returned `CODEX_APPROVE`; a final
  LOW about pre-receipt rollback injection was closed with the late SQLite
  trigger regression, and final confirmation found no HIGH/MEDIUM/LOW.
- [x] Independent Claude Code review initially returned `CLAUDE_APPROVE` with
  one LOW about displaying a context-derived bridge mode; that non-authoritative
  badge was removed. One later provider stream stalled without a verdict and
  was not counted. The narrowed final re-review verified every Codex/Claude fix
  and returned `CLAUDE_APPROVE` with no remaining HIGH/MEDIUM.
- [x] Kiro was not used, preserving the user's recorded exclusion for these UI
  increments. This slice does not change protocol, AI-DLC, reconciliation, or
  methodology boundaries that would independently require the Kiro gate.

### Current checkpoint and next safe action

The reviewed increment is the commit containing this snapshot, directly atop
clean pushed pause checkpoint `e7cda76`. User-owned `.kiro/` and legacy pytest
temporary directories remain untracked and unstaged.

The next safe action is a read-only audit of the remaining
`required_human_actions` types (`plan_approval` and `candidate_disposition`),
then freeze exactly one authenticated, Task-scoped, optimistic, idempotent
command contract before any further runtime or UI edit. Do not infer generic
mutation, formal Approval, Task completion, Stage/Run/Gate control, or
methodology authority from this Attention-only checkpoint.

## 2026-08-03 - paused after authenticated UI read/navigation checkpoint

### Durable stop state

- [x] `main`, local `HEAD`, and `origin/main` are all
  `5cf1f13c9409fafba12319d518c3e2a807ab9dba` (`Add authenticated Task
  discovery to console`). The preceding read-console checkpoint is `ccf1e1e`.
- [x] No third-increment implementation was started. After `5cf1f13`, only a
  read-only audit of the existing Attention/API/UI boundaries was performed.
  There are no uncommitted backend, frontend, schema, or architecture-contract
  changes at this stop point.
- [x] User-owned `.kiro/` and legacy pytest temporary directories remain
  untracked and untouched. Kiro is not an active review gate per the user's
  instruction; the completed UI increments used independent Codex and Claude
  approvals.
- [x] The latest complete verification remains 865 non-integration backend
  tests passed with 18 deselected, 5 focused frontend tests passed, frontend
  lint at zero errors with 12 pre-existing warnings, a successful 15-page
  static production build, and passing Schema export/check, `compileall`, and
  `git diff --check`.

### Recovered next boundary (analysis only)

- The legacy `POST /api/attention/{item_id}/respond` route is not suitable for
  the 1.0 Control Plane UI: it is outside the authenticated project/Task path,
  accepts a caller-selected `actor`, and treats an exact retry as a conflict.
  The new console must not call or silently repurpose that endpoint.
- The next increment should first freeze a separate Task-scoped Attention
  response contract. It should use an explicit response permission, verified
  project membership, non-enumerating project/Task/item binding, an actor
  derived only from the bearer principal, optimistic `expected_version`, and
  an exact-replay `operation_key` whose conflicting reuse fails closed.
- The contract must define question/blocker versus approval actions, optional
  assignee binding, expiry settlement, redaction, audit identity, and the
  distinction between bidirectional delivery and capture-only recording. It
  must not mutate authoritative Task, Stage, or Gate state or create a formal
  protocol Approval.
- The unified backend projection already returns complete `AttentionItem`
  records, while the new frontend type currently exposes only a read subset.
  After the command is frozen and regression-pinned, expand that client type
  and add a bounded response form with abortable mutation leases and stable
  retry keys; refresh the authoritative projection after settlement.
- Required adversarial coverage includes missing permission, wrong project or
  Task/item scope, actor spoof prevention, assignee mismatch, stale version,
  exact replay, operation-key collision, expired items, action/kind mismatch,
  delivery-state effects, transactional rollback, credential changes, stale
  browser responses, and uncertain-network retry behavior.

### Next safe action

Resume from clean pushed commit `5cf1f13`. Re-read this file and Git status,
then freeze the authenticated idempotent Attention response contract before
editing runtime or UI code. Keep the increment Attention-only and obtain
independent Codex and Claude approval before any implementation commit.

## 2026-08-03 - authenticated project Task discovery and console picker (reviewed)

### Bounded implementation

- [x] Froze `docs/architecture/control-plane-ui-task-discovery-v1.md` for a
  project-scoped, authenticated, bounded, read-only Task index. The index is a
  navigation aid only: authoritative Task lifecycle remains in the frozen
  Control Plane row, compatibility state remains informational, and every
  listed Task must also have a project-bound orchestration Plan.
- [x] Added
  `GET /api/control-plane/projects/{project_id}/tasks?limit=&offset=` behind
  `control_plane.read`. Authorization runs before the projection query; the
  production dependency is startup-cached and fails closed if lifespan startup
  was skipped; count and page rows are read from one SQLite snapshot with a
  deterministic `updated_at DESC, task_id` order.
- [x] Added `UnifiedTaskIndexItem` and `UnifiedTaskIndexPage@1.0`, including the
  authoritative and compatibility Task states, Plan identity/methodology, and
  page metadata. Limits are bounded, legal priority zero is preserved, and
  neither authority-only nor Plan-only rows are exposed or counted.
- [x] Extended `/control-plane` with explicit project discovery and an
  URL-encoded Task picker while retaining manual Task-ID entry beyond the
  current page. The UI exposes shown-versus-total counts and keeps discovery
  and projection request lifecycles independent.
- [x] Project, Task, credential, Forget, invalid reconnect, and unmount changes
  invalidate the appropriate request leases. Credential changes atomically
  clear every protected Task/index/error fact, and bearer credentials remain
  header-only and tab-scoped rather than entering URLs or local storage.
- [x] Kept this increment zero-write and discovery-only. Cross-project search,
  pagination controls, Task creation, approval/attention responses, Stage/Run/
  Gate mutations, and methodology migration remain outside this contract.

### Verification and review state

- [x] Focused Control Plane API coverage passes 19 tests, including project
  authorization, zero-write production dependencies, authority/Plan exclusion,
  deterministic order/offset/total behavior, and priority zero. Frontend
  request-lifecycle/path coverage passes 5 tests.
- [x] Frontend lint passes with zero errors and the same 12 pre-existing
  warnings. The Next.js 16 production build and 15-page static export pass.
- [x] Independent Codex review found three MEDIUM issues: priority zero was
  rejected, the first page could be presented as complete and hide other IDs,
  and credential edits could leave an old protected view visible. All were
  fixed and regression-pinned; final review returned `CODEX_APPROVE` with no
  HIGH/MEDIUM. No Kiro review was used per the user's instruction.
- [x] Independent Claude Code review found missing separate authority-only /
  Plan-only exclusion coverage and insufficient pagination/order coverage.
  Both were added; final re-review returned `CLAUDE_APPROVE` with no actionable
  HIGH/MEDIUM.
- [x] The final complete non-integration backend suite, including
  `tests/test_web_ui.py`, passed 865 tests with 18 deselected in 1006.09
  seconds. Its first attempt was terminated by the outer runner at 20 minutes
  before pytest emitted a semantic summary; that transport/process timeout is
  not counted as either test success or assertion failure. The successful
  second run used a fresh system-Temp basetemp and a longer command window.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`, and
  `git diff --check` pass. The only backend warnings are the existing
  Starlette/httpx deprecation and Windows Proactor cleanup warning.

### Current checkpoint and next safe action

The reviewed and fully validated implementation was committed and pushed as
`5cf1f13` atop `ccf1e1e`. The next safe UI increment is to freeze one
authenticated, idempotent human-action command contract; no generic mutation
surface should be inferred from this read-only discovery endpoint.

## 2026-08-02 - authenticated Agora 1.0 Task console read model (reviewed)

### Bounded implementation

- [x] Froze `docs/architecture/control-plane-ui-read-model-v1.md` for the
  first 1.0 UI increment: one authenticated, Task-scoped, bounded, read-only
  `UnifiedTaskProjection@12.0` snapshot with explicit authority, credential,
  error, and deferred-mutation boundaries.
- [x] Added
  `GET /api/control-plane/projects/{project_id}/tasks/{task_id}/unified-projection`
  behind `control_plane.read`, the existing non-enumerating project/Task scope,
  sanitized orchestration errors, and bounded history pagination. The real
  production dependency is initialized during the FastAPI lifespan and fails
  closed when startup was skipped, so GET never bootstraps persistence.
- [x] Added the static-exported `/control-plane` Task console and linked it
  from the delivery shell and non-backlog Portfolio cards. The console renders
  authoritative Task lifecycle, Stage inventory/route source, Stage/Gate
  ledger, independent Wait/Process/Exit/Transport/Schema/Semantic Run facts,
  budgets, Artifacts, Evidence, Approvals, required human actions, and audit
  events from one snapshot.
- [x] Kept the compatibility Task state visibly labelled informational-only.
  A compatibility Plan cursor is labelled separately and never receives the
  formal Control Plane route styling. Missing authority remains unavailable
  with its recorded reason.
- [x] Kept the bearer token out of URLs and local storage. It is retained only
  in component memory and tab-scoped `sessionStorage`; Forget, invalid
  reconnects, superseding loads, and unmount all abort and invalidate the
  prior request so an old response cannot resurrect a credential or snapshot.
- [x] Kept this increment read-only. Task transitions, Stage activation, Run
  dispatch, Gate evaluation, approvals, attention responses, pagination UI,
  and methodology migration still require separately frozen authenticated
  command contracts.

### Verification and review state

- [x] Final complete non-integration backend suite, including
  `tests/test_web_ui.py`, passed 861 tests with 18 deselected in 838.91 seconds.
  The focused Control Plane API suite passed 15 tests, including a production
  dependency-path before/after SQLite dump proving zero GET writes; the focused
  static Web UI suite passed 45 tests.
- [x] The frontend request-lifecycle and Run-dimension suite passed 3 tests.
  Frontend lint passed with zero errors and the same 12 pre-existing warnings;
  the Next.js 16 production build and 15-page static export passed with the new
  `/control-plane` route.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`, and
  `git diff --check` pass. The only backend warning remains the existing
  Starlette/httpx deprecation plus the known post-run Windows Proactor cleanup
  warning.
- [x] Independent Codex review initially found four MEDIUM issues: request-time
  persistence initialization, bearer resurrection after Forget, a compatibility
  cursor presented as formal routing, and mixed Run wait/process/exit display.
  All four were fixed and regression-pinned; final review returned
  `CODEX_APPROVE` with no remaining HIGH/MEDIUM. No Kiro review was used per the
  user's instruction.
- [x] Independent Claude Code reviewed the initial implementation and all four
  fixes, then returned `CLAUDE_APPROVE` with no actionable HIGH/MEDIUM.

### Current checkpoint and next safe action

This reviewed first 1.0 UI increment is ready for a bounded commit and push atop
clean pushed baseline `c6744a1`. After that checkpoint, the next safe UI action
is to freeze an authenticated project-scoped Task discovery/read contract so
the 1.0 console can navigate authoritative Tasks without relying on the legacy
unauthenticated Portfolio list or manual identifiers. Mutation controls remain
deferred until their separate authority and idempotency contracts are frozen.

## 2026-07-31 - AWS AI-DLC explicit human Task completion (reviewed)

### Bounded implementation

- [x] Added hash-sealed
  `MethodologyCompletionApprovalRequest@1.0`,
  `AuthenticatedMethodologyCompletionApproval@1.0`, and
  `MethodologyCompletionApprovalReceipt@1.0` contracts plus the authenticated
  `agora task migration-completion-approve` CLI command.
- [x] Bound explicit human approval to the exact Task/Plan/inventory and
  versions, methodology execution contract, repository/ref/commit, source
  graph, activation definition, final production dispatch/Handoff, both
  independent reviewer claim/dispatch/Run/Handoff/Evidence chains, every
  active passed Evidence item, all seven final managed Artifact versions, and
  the migration proposal/scope-seed path hashes.
- [x] Enforced chronology from final production and reviewer settlement through
  human approval, request, and credential authentication. Reviewer identities
  are pairwise distinct and each review Evidence hash is cross-bound to its
  exact reviewer Run.
- [x] Added one atomic store path that rechecks repository/files and all live
  authority, registers exactly one active formal `Approval@1.0`, transitions
  only the authoritative Control Plane Task from `needs_review` to `completed`,
  and writes the sealed receipt plus mirrored audit events. Concurrent and
  exact replay return one receipt; any mismatch or audit failure is zero-write.
- [x] Closed generic completion and Approval bypasses for methodology-bound
  Tasks. Legacy orchestration approval, public Control Plane Task completion,
  and public formal Approval registration cannot impersonate the authenticated
  atomic completion path; caller-supplied recheck output is independently
  validated again inside the store transaction.
- [x] Revalidate the formal Approval payload, hash, status, artifact bindings,
  and all denormalized identity/repository/time columns on read and replay.
  Path/hash inventory invalidation marks the Approval and Gate stale, reopens
  the final Stage, moves the Task out of `completed`, and makes completion read
  and replay fail closed.
- [x] Added checked JSON Schemas and the architecture boundary note. This
  completes the frozen backend-first implementation-stage path through
  explicit human Task completion without adding HTTP/UI, 0.5 database
  migration execution, dynamic provider substitution, or native AI-DLC file
  mutation.

### Verification and review state

- [x] Complete final methodology coverage passed all 235 collected nodeids in
  four modulo-balanced process-isolated shards (59 + 59 + 59 + 58). The
  remaining non-integration backend suite passed 577 tests with 18 deselected,
  for 812 backend tests passed in total.
- [x] Completion-approval coverage includes concurrent exact replay, generic
  Task and Approval bypasses, malicious recheck output, revoked/wrong
  principals, stale nested Approval, reviewer identity/Evidence cross-wiring,
  pre-review/future timestamps, path tamper before and after completion,
  denormalized-row drift, event rollback, and correct-ref inventory
  invalidation through Task reopen.
- [x] Protocol freeze passed 34 tests. Protocol Schema export/check,
  system-Temp-isolated `compileall`, completion-approval CLI help, and
  `git diff --check` pass. Only the existing Starlette/httpx deprecation and
  Windows Proactor cleanup warnings remain in the backend run.
- [x] Independent Codex replacement review found and closed six actionable
  authority gaps covering public Approval registration, caller-supplied
  recheck output, stale Approval status, reviewer cross-binding, denormalized
  Approval columns, chronology, and invalidation coverage. Its final static
  and regression recheck returned `CODEX_APPROVE` with no remaining actionable
  HIGH/MEDIUM. No Kiro development review was used per the user's instruction.
- [x] Independent Claude Code reviewed the complete implementation, protocol,
  persistence, schemas, CLI, docs, and adversarial coverage and returned
  `CLAUDE_APPROVE` with no actionable HIGH/MEDIUM. Direct Codex validation,
  rather than the review sandbox, supplied the complete executable test
  results recorded above.

### Current checkpoint and next safe action

The reviewed increment is ready for a bounded commit and push atop clean pushed
baseline `fd905d8`. After that checkpoint, the frozen first implementation
stage is complete through explicit Task `completed`. No further implementation
scope should be inferred: a later 0.5 data migration, HTTP/UI replacement,
automatic rework, provider substitution, or native methodology installation
must begin from a separately frozen user-approved requirement.

## 2026-07-31 - AWS AI-DLC completion-review dispatch and settlement (reviewed)

### Bounded implementation

- [x] Added hash-sealed
  `MethodologyCompletionReviewDispatchPolicyDecision@1.0`,
  `MethodologyCompletionReviewDispatchClaim@1.0`, and
  `MethodologyCompletionReviewDispatchReceipt@1.0` contracts plus a CLI command
  that consumes one already authenticated responsibility-scoped reviewer claim.
- [x] Added a review-only Context with the exact seven production Artifact
  versions, no outputs or memory, the single eligible Gate requirement, and
  deterministic passed/failed Evidence details. The runtime, result format,
  command, capability observation, repository, prompt, budget reservation,
  spawn owner, and recovery lease are sealed before process creation.
- [x] Added an exact Control Plane Run-start path for the reserved reviewer id.
  Its transaction authenticates the claim, creates the formal Run, and moves
  only the final Stage from `blocked` to `running`; ordinary Run creation still
  rejects that identity.
- [x] Added fail-closed settlement that accepts no output Artifacts and exactly
  one claim-bound completion-review Evidence. Schema-valid but forged Evidence
  becomes a protocol failure with attention and zero Evidence registration;
  process/transport/schema/semantic results remain separate.
- [x] Added durable dispatch lifecycle, PID attachment, cancellation/dead-PID
  recovery, terminal observation, exact provider-usage debit, and idempotent
  receipt finalization. The original authenticated claim can replay after Run
  creation only when occupancy belongs to its exact bound dispatch.
- [x] Preserved independent finalization: one passed review leaves Gate/Stage/
  Task blocked; both passed reviews produce Gate `passed`, Stage `completed`,
  and Task `needs_review`. Product failure remains blocked and creates no
  automatic rework or approval authority.
- [x] Added checked JSON Schemas and an architecture boundary note. HTTP/UI,
  automatic rework, dynamic provider substitution, native AWS AI-DLC mutation,
  and human Task completion approval remain outside this increment.

### Verification and review state

- [x] Complete methodology coverage passed all 232 collected nodeids in four
  process-isolated shards (58 + 58 + 58 + 58). The remaining non-integration
  backend suite passed 577 tests with 18 deselected, for 809 backend tests
  passed in total.
- [x] Completion-review coverage includes the two-review success path, exact
  `failed_product`, schema-valid forged Evidence, live-authority drift,
  preclaimed sibling dispatch, reserved generic-settlement bypass, settled
  replay, missing formal Run/usage, concurrent single-spawn, unstarted success
  forgery, deterministic Run-id negatives, and the former-HIGH three-row
  failed-to-passed reseal attack with unchanged adapter result.
- [x] Protocol freeze passed 34 tests. Protocol Schema export/check,
  system-Temp-isolated `compileall`, completion-review CLI help, and
  `git diff --check` pass.
- [x] Independent Codex replacement review found and closed reserved-settlement,
  settled-replay/provenance, live-authority, deterministic-id, missing-Run,
  unstarted-process, schema-lifecycle, and concurrent-spawn gaps. Its final
  regression/static recheck returned `CODEX_APPROVE` with no remaining
  actionable HIGH/MEDIUM. No Kiro development review was used per the user's
  instruction.
- [x] Independent Claude Code reviewed the complete final worktree and returned
  `CLAUDE_APPROVE` with no actionable HIGH/MEDIUM. It confirmed authority,
  deterministic identity, transactionality, single-spawn, exact Handoff/
  Evidence/usage, replay/tamper, sibling preclaim, independence, lifecycle,
  Schema, CLI, and regression boundaries; two informational LOW observations
  required no change.

### Current checkpoint and next safe action

Implementation, complete validation, and both independent review gates are
ready to checkpoint on pushed baseline `9d87cb8`. Next stage only the explicit
implementation/docs/Schema/test files, commit/push, then recover the frozen
requirements for the bounded explicit human Task approval/completion slice;
the reviewer dispatch itself intentionally stops at Task `needs_review`.

## 2026-07-31 - AWS AI-DLC completion-review Run claim (implementation)

### Bounded implementation

- [x] Added strict hash-sealed
  `MethodologyCompletionReviewClaimRequest@1.0` and
  `MethodologyCompletionReviewClaimReceipt@1.0` contracts plus a CLI command
  for one authenticated, non-dispatching final reviewer Run claim.
- [x] Bound each claim to the exact final settled production dispatch, Run,
  Handoff, seven registered output Artifact versions, active production
  Evidence, blocked Stage/Gate versions, Task/Plan/inventory, repository and
  seed hashes, reviewer responsibility/runtime command pin, complete runtime
  registry, and protected Token/cost reservation.
- [x] Preserved reviewer independence from production and from the other
  reviewer through deterministic responsibility-scoped Run ids and the frozen
  pairwise-distinct Claude/Kiro/Codex runtime pins. The temporary Codex
  development-review replacement does not alter that product contract.
- [x] Persisted one immutable claim per Task/responsibility with exact replay
  and atomic Task/Control Plane audit events. The receipt proves no process,
  Evidence, Artifact, Task, Plan, Stage, or Gate mutation authority was used.
- [x] Added fail-closed validation of both protocol payloads and denormalized
  ledger columns for the final output Artifacts and active production Evidence.
  Row-only tampering blocks the claim with zero writes.
- [x] Reconciled the frozen migration budget with the live Plan and validated
  first-Stage plus later-Stage usage ledgers back to their sealed dispatch
  receipts. A claim preserves both completion reviewers' Token/cost
  reservations concurrently; Plan, claim, usage-payload, and usage-row drift
  all fail before writes.
- [x] Bound the successful production Run's Context/Handoff and separate
  error/Attention columns, and globally reserved each deterministic reviewer
  Run id. Ordinary protocol and compatibility Run creation cannot occupy that
  id, including across Tasks; exact claim replay also detects raw ledger
  occupation.
- [x] Added checked request/receipt JSON Schemas and the architecture boundary
  note. Reviewer dispatch, completion Evidence registration, Gate evaluation,
  final Stage/Task settlement, rework, HTTP/UI, and provider substitution remain
  outside this increment.

### Verification and review state

- [x] Final completion-review selection: 3 passed, 220 deselected. The final
  route-replay plus reserved-identity selection passed 2 tests, 221 deselected.
  Coverage includes CLI, concurrent exact replay, revoked-principal rejection,
  event rollback, cross-Task Run-id collision, raw occupancy, and Plan/claim/
  Context/usage/Artifact/Evidence tampering with zero claim writes.
- [x] Final complete methodology coverage: all 223 collected nodeids passed in
  four process-isolated shards (56 + 56 + 56 + 55). This avoided a Windows
  execution-tool timeout that killed the equivalent single long pytest process
  without a test verdict.
- [x] The remaining complete non-integration backend suite passed 577 tests
  with 18 deselected. Together with the 223 methodology nodeids, final backend
  coverage is 800 passed and 18 deselected; only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warnings remain.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`, Task and
  completion-claim CLI help, and `git diff --check` passed.
- [x] Independent Codex replacement review found and closed migration/Context,
  Plan/budget/usage, successful-Run, and reviewer-Run reservation authority
  gaps. After final static review and independent focused/schema/compile/diff
  checks it returned `CODEX_APPROVE` with no remaining actionable HIGH/MEDIUM.
- [x] Independent Claude Code reviewed the complete final worktree across
  protocol/orchestration/Control Plane code, persistence, generated Schemas,
  tests, architecture docs, and this checkpoint. It confirmed transactional
  atomicity, fail-closed provenance and budget/usage binding, reviewer
  independence, global Run-id reservation, exact replay, compatibility, and
  regression coverage, then returned `CLAUDE_APPROVE` with no actionable
  HIGH/MEDIUM. No Kiro development review was used per the user's instruction.

### Current checkpoint and next safe action

Implementation, complete final validation, and both independent review gates
were committed and pushed as `9d87cb8`. The next bounded slice was dispatch and
settlement of an already claimed reviewer Run without allowing production to
impersonate a reviewer.

## 2026-07-31 - AWS AI-DLC sequence-8 all-units aggregation (reviewed)

### Bounded implementation

- [x] Extended the authenticated Gate, formal Run claim, native dispatch/
  recovery, settlement, usage, and projection chain to exact execution-contract
  sequence 8 (`build-and-test`), the final selected Stage in the exercised
  bugfix inventory.
- [x] Added bounded `all_units` resolution for exactly the two selected
  `code-generation` instances. The Context order is input-contract order then
  producer-instance order: sequence-6/7 `code-generation-plan`, followed by
  sequence-6/7 `code-summary`.
- [x] Reused one extracted selected-output authority path for both historical
  `single` inputs and the four aggregate bindings. Every binding recomputes the
  concrete producer Stage/Run, settled dispatch, deterministic required
  output, sealed Handoff, and registered Artifact payload/version/hash; no unit
  can substitute for a missing or tampered sibling.
- [x] Preserved the independent completion-review boundary. The sequence-8
  production prompt/Handoff exposes only production-owned requirements; the
  formal Gate retains the Claude correctness and methodology-stewardship
  requirements. A valid production settlement registers its seven outputs but
  leaves the final Gate/Stage and Task `blocked` pending those reviews, with no
  next route.
- [x] Enforced the same production Evidence subset in the runtime adapter and
  again in the Control Plane before any Artifact/Evidence registration. A
  production Handoff that forges completion-review Evidence is a protocol
  failure with zero registration.
- [x] Added end-to-end final-inventory settlement/replay and one-producer
  Artifact-tamper zero-write coverage. Updated the Gate, claim, dispatch, and
  first-dispatch architecture notes through sequence 8.
- [x] Updated the two later-Stage CLI summaries from the obsolete
  `sequence-2` wording to the exact current contract-bound Stage; command and
  protocol surfaces are unchanged.

### Verification and review state

- [x] Sequence-8 focused selection after the review fix: 3 passed, 217
  deselected. Control Plane Handoff-subset selection: 3 passed, 11 deselected.
- [x] Focused sequence-5/6/7/8 regression selection passed 13 tests; complete
  adapter/protocol-orchestration coverage passed 94 tests; complete
  methodology coverage passed 220 tests.
- [x] Complete Control Plane protocol-run coverage passed 15 tests, including
  both zero-registration rejection of out-of-scope Handoff Evidence and an
  exact legacy settlement-operation fingerprint assertion.
- [x] Final complete non-integration backend suite: 797 passed, 18 deselected,
  with only the existing Starlette/httpx deprecation and Windows Proactor
  cleanup warnings.
- [x] Protocol freeze: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, all relevant CLI help checks, and
  `git diff --check` passed.
- [x] Independent Codex methodology/protocol review found one HIGH completion-
  review authority bypass; the production-prompt, adapter, Control Plane, and
  regression fixes closed it. Its closure review then found one MEDIUM replay-
  compatibility issue because the new allow-list had changed legacy operation
  fingerprints. The default path now preserves the exact legacy fingerprint,
  only sequence 8 opts into the bounded allow-list, and focused plus complete
  validation pass. The final re-review returned `CODEX_APPROVE` with no
  actionable finding.
- [x] Independent Claude Code reviewed the complete final diff across
  production, tests, architecture docs, and PROGRESS. It confirmed aggregate
  producer authority, the independent completion-review boundary, zero-write
  rejection, atomicity, replay compatibility, deterministic identity/order,
  and regression coverage, then returned `CLAUDE_APPROVE` with no actionable
  finding. No Kiro review was used, per the user's current instruction.

### Current checkpoint and next safe action

Implementation, complete validation, the user-authorized independent Codex
replacement gate, and the independent Claude gate were committed and pushed as
`bdadeb8`. The next bounded slice is the independently authorized
completion-review Evidence and final-Stage finalization path without allowing
the production Run to impersonate a reviewer or inventing automatic rework
semantics.

## 2026-07-31 - AWS AI-DLC sequence-7 repeated-unit isolation (reviewed)

### Bounded implementation

- [x] Extended the authenticated Gate, formal Run claim, native dispatch/
  recovery, settlement, usage, and projection chain to exact execution-contract
  sequence 7 (`code-generation-unit-002`) without changing a protocol model or
  checked JSON Schema.
- [x] Reused the frozen external `unit-of-work` ArtifactVersionRef and exact
  registered sequence-5 `requirements` version while binding them to the
  distinct sequence-7 consumer Stage. The seed retains no producer Run, the
  requirements producer remains sequence 5, and settled sequence 6 is only the
  direct lifecycle predecessor.
- [x] Proved repeated-unit isolation through distinct deterministic Run,
  Context, and output identities. Sequence-6 and sequence-7
  `code-generation-plan`/`code-summary` output sets are disjoint even though
  both consumers intentionally receive the same two input version references.
- [x] Made the successful fake methodology Runner report a minimal sealed exact
  native usage observation. This keeps the 30k test Plan from being consumed by
  fallback prompt-size estimates after six synthetic runs; production usage
  normalization and budget admission remain unchanged.
- [x] Updated the Gate, claim, dispatch, and first-dispatch architecture notes
  through sequence 7. The next bounded input-resolution boundary is the exact
  sequence-8 `all_units` multi-producer contract.

### Verification and review state

- [x] Sequence-7 focused selection: 2 passed, 215 deselected. Combined
  sequence-6/7 selection: 6 passed, 211 deselected.
- [x] Complete methodology module: 217 passed. Complete non-integration backend
  suite: 792 passed, 18 deselected, with only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warnings.
- [x] Protocol freeze: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, all three relevant CLI help checks, and
  `git diff --check` passed.
- [x] Independent Codex methodology/protocol replacement review found no code,
  authority, budget, or regression defect. It requested one LOW checkpoint
  wording correction so PROGRESS did not call completed validation pending;
  after that fix it returned `CODEX_APPROVE`.
- [x] Independent Claude Code reviewed the complete final diff in production,
  tests, and docs/PROGRESS partitions after earlier large-input CLI attempts
  hung without verdict and were not counted. The partitions returned
  `CLAUDE_CORE_APPROVE`, `CLAUDE_TESTS_APPROVE`, and
  `CLAUDE_DOCS_APPROVE`; the final merge returned `CLAUDE_APPROVE`.

### Current checkpoint and next safe action

Implementation, complete validation, the user-authorized independent Codex
replacement gate, and the independent Claude gate are complete on clean pushed
baseline `5e2b463`. This increment is ready for an exact-file commit/push. The
next bounded slice is sequence 8 `all_units` multi-producer input resolution.

## 2026-07-31 - AWS AI-DLC sequence-6 Task seed and prior output (reviewed)

### Bounded implementation

- [x] Extended the authenticated Gate, formal Run claim, native dispatch/
  recovery, settlement, usage, and projection chain to exact execution-contract
  sequence 6 (`code-generation-unit-001`).
- [x] Generalized the existing input binding without changing old receipt
  payloads: a non-null producer Run continues to identify a selected formal
  output, while a null producer Run is accepted only for an exact external
  Artifact location. The generated receipt Schema now documents that nullable
  producer-Run boundary.
- [x] Resolved the sequence-6 Context in frozen order from the hash-bound
  `unit-of-work` Task seed and the registered sequence-5 `requirements`
  Artifact. Six unavailable design inputs remain explicitly absent; the seed
  is never fabricated as a protocol Artifact or attributed to a future Run.
- [x] Recomputed seed identity/version/hash/repository/ref/commit/path from the
  persisted migration request and execution contract, rehashed the live seed
  before claim, and allowed only null-producer refs through the Control Plane's
  external-input exception. Selected outputs retain the complete recursive
  dispatch/Handoff/Artifact authority added for sequence 5.
- [x] Added end-to-end settlement/replay, prompt-bound, six-Run usage,
  live-seed-tamper zero-write, and resealed seed-as-producer tamper regressions.
  Updated the Gate, claim, dispatch, first-dispatch, checked Schema, and next
  repeated-unit boundary documentation.
- [x] Closed the independent Codex review's high-severity unbound-output
  finding at both pre-registration boundaries. The Agent adapter now rejects
  every Artifact id/kind pair outside the Context Pack's complete declared
  output set, and the Control Plane repeats the same check before registration.
  A malicious sequence-6 Handoff that returns the external Task seed as a
  current-Run output settles as protocol-failed/blocked with no Handoff,
  Artifact, or Evidence registration.

### Verification and review state

- [x] Sequence-6 focused selection: 4 passed, 211 deselected. Combined
  sequence-5/6 selection: 8 passed, 207 deselected. Combined sequence-4/5/6
  selection: 11 passed, 204 deselected.
- [x] Protocol freeze: 34 passed after regenerating the one changed checked
  Schema.
- [x] Complete methodology module: 215 passed. Complete non-integration backend
  suite: 790 passed, 18 deselected, with only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warnings.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`, all three
  relevant CLI help checks, and `git diff --check` passed.
- [x] Independent Codex methodology/protocol replacement review found one
  high-severity unbound-output registration path. After the adapter and
  Control Plane checks plus the sequence-6 zero-registration regression were
  added and final counts corrected, the same reviewer returned
  `CODEX_APPROVE`.
- [x] Independent Claude Code review covered the complete final diff in a
  production/Schema partition and a tests/docs/PROGRESS partition after an API
  connection failure and two hung attempts produced no verdict and were not
  counted. The partitions returned `CLAUDE_CORE_APPROVE` and
  `CLAUDE_COVERAGE_APPROVE`; a final no-tools merge returned
  `CLAUDE_APPROVE`.

### Current checkpoint and next safe action

Implementation, complete validation, the user-authorized independent Codex
replacement gate, and the independent Claude gate are complete on clean pushed
baseline `3217007`. This increment is ready for an exact-file commit/push. The
next bounded slice is sequence 7 repeated-unit isolation for
`code-generation-unit-002`.

## 2026-07-31 - AWS AI-DLC sequence-5 selected input Artifacts (reviewed)

### Bounded implementation

- [x] Extended the authenticated next-Stage Gate, formal Run claim, native
  dispatch/recovery, Control Plane settlement, usage, and unified projection
  to exact execution-contract sequence 5 without changing a protocol model or
  checked JSON Schema.
- [x] Added deterministic sequence-5 input resolution for exactly the selected
  sequence-4 `business-overview`, `architecture`, and `code-structure`
  Artifacts. `intent-statement`, `scope-document`, and `team-practices` remain
  explicitly absent according to the frozen contract.
- [x] Bound each selected input to the producer formal Run and Stage,
  deterministic RequiredOutput, settled dispatch receipt, sealed Handoff,
  registered Artifact version/payload hash, and consumer Context Pack. Reads,
  claim replay, budget admission, unified projection, and dispatch all
  recompute the same transitive authority and fail closed on drift.
- [x] Added end-to-end coverage for the three exact input bindings, two exact
  outputs, prompt bound, settlement/replay and five-Run usage projection;
  source Artifact or producer Handoff ledger tamper blocks every consumer; an
  unrelated same-kind Artifact cannot substitute for the missing deterministic
  producer output.
- [x] Updated the Gate, later-Stage claim/dispatch, and first-dispatch boundary
  notes through sequence 5. Sequence 6 task-seed resolution, later inventory,
  rework, HTTP, UI, provider substitution, and native AI-DLC installation
  remain deferred.

### Verification and review state

- [x] Sequence-5 focused selection: 4 passed, 207 deselected. Combined
  sequence-4/5 selection: 7 passed, 204 deselected.
- [x] Complete methodology module: 211 passed. Complete non-integration backend
  suite: 785 passed, 18 deselected, with only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warnings.
- [x] Protocol freeze: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, all three relevant CLI help checks, and
  `git diff --check` passed.
- [x] Independent Codex methodology/protocol replacement review found no code
  or authority defect. It requested one PROGRESS checkpoint consistency fix;
  after the final counts and review-pending wording were corrected, it returned
  `CODEX_APPROVE` on the latest tree.
- [x] Independent Claude Code reviewed the complete final supplied diff after
  two earlier tool-driven attempts ended without a verdict and were not
  counted. It traced contract-order selection, producer dispatch/Handoff/
  Artifact authority, consumer Context and recursive consumers, compatibility,
  tests, and docs, found no actionable issue, and returned `CLAUDE_APPROVE`.

### Current checkpoint and next safe action

Implementation, complete validation, the user-authorized independent Codex
replacement gate, and the independent Claude gate are complete on clean pushed
baseline `dc994ed`. This increment is ready for an exact-file commit/push. The
next bounded slice is exact sequence-6 input resolution, including its
hash-bound Task seed.

## 2026-07-31 - AWS AI-DLC sequence-4 formal execution (reviewed)

### Bounded implementation

- [x] Extended the existing authenticated next-Stage Gate, formal Run claim,
  process dispatch/recovery, Control Plane settlement, and usage projection to
  exact execution-contract sequence 4 without changing any protocol model or
  checked JSON Schema.
- [x] Generalized the later-Stage predecessor resolver for sequence 4 while
  retaining the sequence-1 compatibility lineage root and a foreign-keyed
  direct sequence-3 predecessor. Read and write validation now walks the
  transitive later-Stage dispatch/claim/Gate chain toward sequence 1, so a
  tampered predecessor cannot authorize a new Gate, Run, process, budget read,
  or unified projection.
- [x] Sequence 4 is the first bounded successor position with non-empty output
  contracts. Its Context Pack deterministically materializes every required
  output, and successful settlement registers the exact Artifacts and formal
  Gate Evidence before activating sequence 5.
- [x] Replaced the duplicated full Handoff/Gate template policy payload with a
  deterministic full-contract-hash-bound projection. Required outputs and
  formal Gate requirements remain explicit, while the resulting native prompt
  stays within the unchanged Windows 24 KiB argv safety bound.
- [x] Added end-to-end regressions for Gate/claim replay, required-output
  materialization, Artifact/Evidence settlement, sequence-1 lineage plus direct
  sequence-3 predecessor persistence, prompt size, unified Run/usage
  projection, dispatch replay, transitive predecessor tamper, and sequence-4
  binding tamper across all consumers.
- [x] Updated the Gate, Run-claim, first-dispatch, and later-Stage dispatch
  architecture notes for sequences 2 through 4. Positions beyond sequence 4,
  input-Artifact selection, and automatic rework remain deferred.

### Verification and review state

- [x] Sequence-4 focused selection: 3 passed, 204 deselected. Combined
  sequence-3/4 selection: 24 passed, 183 deselected.
- [x] Complete methodology module: 207 passed. Complete non-integration backend
  suite: 781 passed, 18 deselected, with only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warnings.
- [x] Protocol freeze: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, all three CLI help checks, and
  `git diff --check` passed.
- [x] Independent Codex methodology/protocol replacement review inspected the
  final transitive authority chain, shared deterministic RequiredOutput
  materialization, prompt projection, settlement, compatibility, tests, and
  docs. It found no actionable issue and returned `CODEX_APPROVE`.
- [x] The first exhaustive Claude Code command timed out without a verdict and
  was not counted as approval. A focused read-only final-diff review then found
  no actionable correctness or safety defect and returned `CLAUDE_APPROVE`.

### Current checkpoint and next safe action

Implementation, complete validation, the user-authorized independent Codex
replacement gate, and the independent Claude gate are complete on baseline
`e8d1b57`. This increment is ready for an exact-file commit/push. The next
bounded slice may add the exact sequence-5 input-Artifact selection contract;
automatic rework, HTTP, UI, dynamic provider substitution, and native AI-DLC
installation remain deferred.

## 2026-07-31 - AWS AI-DLC sequence-3 Run dispatch/settlement (reviewed)

### Bounded implementation

- [x] Generalized the existing later-Stage dispatch policy, native preflight,
  process-claim, PID attachment, recovery, formal settlement, receipt, and
  usage-ledger path from sequence 2 to the exact sequence-3 `state-init` Run.
  No protocol contract or checked JSON Schema changed.
- [x] The dispatcher selects the highest formal later-Stage claim and
  cross-checks it against the Task's current Run cursor plus the authoritative
  Stage/Gate route. A stale cursor cannot replay an older settled dispatch as
  though it were the current Run.
- [x] Sequence-3 policy/claim uses the settled immediate sequence-2 dispatch
  receipt/Handoff. The persisted dispatch row retains the sequence-1 id as its
  compatibility lineage root, while the Gate/Run-claim direct-predecessor
  ledger remains the sequence-2 authority and is revalidated before claim,
  PID attachment, terminal observation, finalization, recovery read, budget
  admission, and unified projection.
- [x] Settlement reuses the existing sequence-3 formal Run and Context Pack,
  keeps exit/transport/Schema/semantic/repository/usage facts separate, lets
  only the Control Plane create Artifact/Evidence and evaluate Gate/Stage, and
  activates sequence 4 only after semantic success.
- [x] Added sequence-3 regressions for successful settlement/replay, direct
  predecessor and current-Run tamper, dispatch-lineage tamper across getter,
  budget, and projection, concurrent single spawn, and crash recovery without
  respawn. Missing, empty, or non-string current-Run cursors now fail closed
  across getter, resume, and dispatch before PID inspection or any write.
  Existing sequence-2 dispatch behavior remains covered unchanged.
- [x] Updated the Gate, Run-claim, first-dispatch, and later-Stage dispatch
  architecture boundaries for sequences 2 and 3; positions beyond sequence 3
  and cross-Stage rework remain deferred.

### Verification and review state

- [x] Sequence-3 selection after final hardening: 21 passed, 183 deselected.
  Independent Codex combined later-Stage dispatch regression: 28 passed, 176
  deselected.
- [x] Complete methodology module: 204 passed. Complete non-integration backend
  suite: 778 passed, 18 deselected, with only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warnings.
- [x] Protocol freeze: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, CLI help, and `git diff --check` passed.
- [x] Independent Codex methodology/protocol replacement review first returned
  `CODEX_REQUEST_CHANGES` for a missing/empty current-Run cursor fail-open read
  and three stale architecture notes. The shared getter now distinguishes true
  absence from corrupted authority, the new running-dispatch regression covers
  missing/empty/non-string cursors, all docs were corrected, and re-review
  returned `CODEX_APPROVE`.
- [x] Independent Claude Code statically traced the final complete diff and its
  load-bearing predecessor/claim validators. It found no actionable HIGH,
  MEDIUM, or LOW issue and returned `CLAUDE_APPROVE`. Its environment could not
  invoke pytest, so the direct complete validation results above remain the
  executable gate evidence.

### Current checkpoint and next safe action

Implementation, complete post-fix validation, the user-authorized independent
Codex replacement gate, and the independent Claude gate are complete on
baseline `32d7516`. This increment is ready for an exact-file commit/push. The
next bounded slice may extend the same Gate/claim/dispatch authority to sequence
4; automatic rework, HTTP, UI, dynamic provider substitution, and native AI-DLC
installation remain deferred.

## 2026-07-31 - AWS AI-DLC sequence-3 Gate/formal Run claim (reviewed)

### Bounded implementation

- [x] Generalized the existing `MethodologyStageGateRequest/Receipt@1.0` and
  `MethodologyStageRunClaimRequest/Receipt@1.0` execution paths to the exact
  sequence-3 `state-init` position without changing their checked JSON Schemas
  or the frozen sequence-1/2 receipt semantics.
- [x] Added common typed accessors over first-Run and later-Stage dispatch
  receipts. Sequence 3 is accepted only from the settled, immediately adjacent
  sequence-2 dispatch/Handoff whose Run claim, Stage, Gate, Task, Plan,
  repository, execution contract, and authenticated principal still match.
- [x] Added an additive direct-predecessor ledger for sequence 3. Existing Gate
  and Run-claim rows retain the sequence-1 dispatch id as their compatibility
  lineage root; the new row foreign-keys the actual sequence-2 dispatch and
  seals its receipt id/hash, Run, Stage, Gate, Plan, and adjacency.
- [x] Sequence-3 Gate configuration and formal Run claim reuse the existing
  authenticated CLI/service operations and immediate transactions. They create
  no process, compatibility Run, Artifact, Evidence, provider substitution, or
  dispatch authority. The `state-init` Context Pack preserves the contract's
  exact empty representable input and required-output sets.
- [x] Missing or tampered direct-predecessor authority blocks Gate and Run
  reads/replays, new claims, budget admission, and unified status projection.
  Event failure rolls back both the formal Gate and the new binding.
- [x] Addressed the first independent Codex review's two findings by routing
  Run-claim getter, exact replay, budget admission, and unified projection
  through one complete Gate/predecessor authority validator. The validator
  cross-checks the claim's sequence-1 lineage root and every sequence-3 direct
  predecessor identity; new regressions cover forged lineage roots and
  Task/Plan/Stage/Gate metadata drift.
- [x] Updated the Gate, Run-claim, first-dispatch, and later-Stage dispatch
  architecture notes to record the sequence-3 boundary and leave sequence-3
  dispatch, positions beyond sequence 3, and rework separate.

### Verification and review state

- [x] Post-review-fix sequence-3 selection: 12 passed, 183 deselected. Existing
  sequence-2 Gate/Run-claim selection: 52 passed, 143 deselected.
- [x] Complete methodology module: 195 passed. Complete non-integration backend
  suite: 769 passed, 18 deselected, with only the existing Starlette/httpx
  deprecation and Windows Proactor cleanup warnings.
- [x] Protocol freeze: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, CLI help, and `git diff --check` passed.
- [x] Independent Codex methodology/protocol replacement review first returned
  `CODEX_REQUEST_CHANGES` for incomplete claim lineage-root validation and a
  partial direct-predecessor check in the Run-claim getter. Both findings were
  fixed with the shared authority validator and new tamper regressions; the
  frozen-diff re-review returned `CODEX_APPROVE`.
- [x] Independent Claude Code statically inspected the final complete diff,
  including the untracked predecessor helper, both Codex fixes, Schema/FKs,
  transaction/replay/admission/projection paths, tests, and docs. It reported
  no actionable HIGH, MEDIUM, or LOW finding and returned `CLAUDE_APPROVE`.
  Its only optional note was future consolidation of two-connection immutable
  getters; this mirrors existing style and does not weaken fail-closed reads.

### Current checkpoint and next safe action

Implementation, complete post-fix validation, the user-authorized independent
Codex replacement gate, and the independent Claude gate are complete on
baseline `c7668c3`. This increment is ready for an exact-file commit/push. The
next bounded slice may dispatch and settle the already claimed sequence-3 Run
without generalizing beyond sequence 3 or introducing automatic rework.

## 2026-07-31 - AWS AI-DLC sequence-2 Run dispatch/settlement (reviewed)

### Bounded implementation

- [x] Added additive `MethodologyStageRunDispatchPolicyDecision@1.0`,
  `MethodologyStageRunDispatchClaim@1.0`, and
  `MethodologyStageRunDispatchReceipt@1.0` contracts, executable registry
  entries, and three checked-in JSON Schemas. The frozen first-Run dispatch
  contracts remain unchanged.
- [x] Added
  `agora task migration-next-stage-run-dispatch SUCCESSOR_TASK_ID
  --allow-unbounded-native-usage`. The acknowledgement is checked before
  service construction because the Stage reservation is admission control,
  not a native provider hard cap.
- [x] Read-only policy and native preflight bind the exact sequence-2 Run claim,
  configured Gate, settled sequence-1 predecessor receipt, execution contract,
  repository/ref/commit, authoritative `RUNNING` Stage/`PENDING` Gate route,
  full runtime pins, sealed Context Pack, and positive usage reservation before
  any dispatch write.
- [x] One immediate transaction rechecks the complete live chain and records a
  single-use later-Stage process attachment. PID, terminal runner facts,
  Control Plane protocol settlement, immutable receipt, separate later-Stage
  usage ledger, and both audit streams retain crash-recoverable boundaries.
- [x] The dispatch reuses the existing formal Run and Context Pack; it creates
  no compatibility Run. The Control Plane remains the only Artifact/Evidence,
  Gate, Stage, Run, and next-route authority. Exit code, transport, Schema,
  semantic result, repository stability, and usage remain separate facts.
- [x] Unified Task projection merges first and sequence-2 dispatch state by
  formal Run id. Active sequence-2 Token/cost reservation becomes settled only
  when the later-Stage usage row exists; unavailable usage consumes the sealed
  reservation rather than becoming fabricated zero usage.
- [x] Recovery never respawns: un-PIDed claims settle as launch failure, live or
  unknown PIDs block, dead PIDs settle as interrupted, terminal observations
  replay formal settlement, and settled dispatches return the immutable
  receipt. Concurrent callers cannot spawn twice; policy, Gate ledger,
  terminal, and receipt tampering fail closed.
- [x] Addressed the first independent Codex review: a sealed spawn-owner id and
  five-minute recovery lease protect the claim-to-PID boundary; policy and
  settlement bind the full runtime-registry hash and sealed result format; and
  projection/admission cross-check later-Stage reservation and usage rows
  against sealed receipts and observations. Added regressions for the delayed
  claim-to-PID race, expired-lease recovery, non-command runtime drift, sealed
  result-format settlement, and claim/usage tamper or deletion.
- [x] Addressed the Codex re-review finding that a forged claim `plan_id` could
  escape plan-scoped validation. Validation now enumerates claims through the
  formal Run's authoritative Task-to-Plan relationship and cross-checks both
  stored and sealed Plan/Task identities. Active admission and settled unified
  projection cross-Plan tamper regressions fail closed.
- [x] Addressed the next Codex re-review finding on the destination side of the
  same trust boundary. Validation now selects the union of authoritative Plan,
  stored claim Plan, and stored usage Plan matches, so neither a foreign claim
  nor a foreign usage row can be consumed by a destination Plan. Regressions
  cover both source and destination admission/projection reads.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-stage-run-dispatch-v1.md` and linked
  it from the first-dispatch and sequence-2 claim documents.

### Verification and review state

- [x] Focused sequence-2 dispatch and review-fix selection: 27 passed, 156
  deselected.
- [x] Post-review-fix complete methodology module: 183 passed. Complete
  non-integration backend suite: 757 passed, 18 deselected, with only the
  existing Starlette/httpx deprecation and Windows Proactor cleanup warnings.
- [x] Post-review-fix protocol freeze: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, CLI help, and `git diff --check` passed.
- [x] Independent Codex methodology/protocol review completed. The initial
  review returned `CODEX_REQUEST_CHANGES` for claim-to-PID ownership/recovery,
  complete runtime-registry/result-format sealing, and reservation/usage ledger
  integrity. A second review found plan-scoping depended on mutable claim data.
  A third review found the destination-Plan side of that validation also needed
  to select foreign stored claim/usage rows. All five findings are now
  implemented and fully retested; the final frozen-diff review returned
  `CODEX_APPROVE`. This is the user-authorized temporary Codex replacement
  gate, not Kiro approval.
- [x] Independent Claude Code reviewed the complete implementation and each
  later cross-Plan hardening delta. It confirmed the owner/lease, full runtime
  registry and sealed result-format, source/destination ledger validation,
  counting, transaction, Schema, and regression boundaries and returned
  `CLAUDE_APPROVE` on the final frozen diff.

### Current checkpoint and next safe action

Implementation, complete post-fix validation, the user-authorized independent
Codex replacement gate, and the independent Claude gate are complete on
baseline `67aa8aa`. This increment is ready for an exact-file commit and push.
The next bounded backend slice must make later-Stage predecessor persistence
generic enough to configure and claim sequence 3 from the settled sequence-2
Handoff without weakening the frozen sequence-1 or sequence-2 receipts.
Automatic cross-Stage rework, HTTP, UI, dynamic provider substitution, and
native AI-DLC file installation remain deferred.

## 2026-07-31 - AWS AI-DLC sequence-2 formal Run/Context claim (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyStageRunClaimRequest@1.0` and
  `MethodologyStageRunClaimReceipt@1.0`, deterministic later-Stage Run
  identity derivation, executable registry entries, and checked-in JSON
  Schemas.
- [x] Added
  `agora task migration-next-stage-run-claim SUCCESSOR_TASK_ID --request PATH
  --credential-env NAME`. Authentication and bounded request loading finish
  before service/storage initialization; the original migration-chain
  principal must retain `control_plane.approve` and exact Project scope.
- [x] One immediate transaction revalidates the live Task/Control
  Task/Plan/inventory/contract, immutable sequence-2 Gate receipt, settled
  sequence-1 dispatch/Run/Handoff/Stage/Gate chain, clean repository before
  and after bounded source hashing, complete runtime pins, exact current
  route, canonical Gate requirements, absence of sequence-2 execution state,
  and remaining Token/cost budget.
- [x] The transaction seals the exact `workspace-detection` Context Pack,
  creates one formal `protocol_runs` row, advances only the authoritative
  sequence-2 Stage `READY -> RUNNING`, reconciles Control Task lifecycle,
  advances Task metadata/version once, and records the active reservation,
  immutable claim ledger, and both audit streams. Any failure rolls back every
  effect.
- [x] The frozen sequence-2 contract has no input or output contracts, so the
  claim enforces exact empty `input_artifacts` and `required_outputs`; it does
  not fabricate a predecessor Artifact. The Context carries five bounded
  contract/Gate/Handoff/Stage policy entries and injects no transcript,
  memory, knowledge, preference, native state, Artifact, Evidence, preflight,
  provider output, compatibility Run, or process authority.
- [x] Unified projection includes the second protocol-only Run with pinned
  runtime and active reservation. Exact replay requires the same request and
  currently authorized original principal; different identity, ledger drift,
  stale authority, repository/runtime drift, insufficient budget, event
  failure, and concurrent callers fail closed or converge without partial
  writes.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-stage-run-claim-v1.md`, linked it
  from the prior Gate/dispatch documents, and kept the normative Schema list
  exact.

### Verification and review state

- [x] Focused sequence-2 claim selection: 29 passed, 127 deselected,
  including the post-Claude cost-budget and pre-existing execution-state
  regressions.
- [x] Complete AWS AI-DLC methodology module: 156 passed.
- [x] Related methodology, Control Plane protocol/Stage routing/lifecycle,
  protocol orchestration/freeze, Task orchestration, provider usage, runtime
  capability/preflight, and projection suite: 413 passed.
- [x] Exact complete non-integration backend suite: 730 passed, 18 deselected,
  with only the existing Starlette/httpx and Windows Proactor cleanup
  warnings. A restricted-sandbox pass first showed four environment-only
  default-database/`/tmp` permission failures; the same suite passed completely
  in the approved outer environment.
- [x] Protocol freeze suite: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, CLI help, and `git diff --check` passed.
- [x] Kiro CLI remained unavailable: its review attempt produced no findings
  or verdict and reported `Authentication failed. Your session may have
  expired`; an independent `kiro-cli profile` check returned `No profiles
  available`. The user explicitly directed Agora to stop using Kiro
  temporarily and authorized an independent Codex methodology/protocol review
  as the replacement gate for this checkpoint. The read-only Codex reviewer
  inspected the exact frozen worktree, reported no actionable finding, and
  returned `CODEX_APPROVE`. This records a scoped user-authorized substitution,
  not a Kiro approval.
- [x] Independent Claude Code safe-mode review inspected the complete tracked
  diff and all untracked implementation/Schema files, transaction/replay/
  tamper/concurrency paths, exact empty sequence-2 contracts, budget/projection
  behavior, documentation, and tests. It found no actionable HIGH, MEDIUM, or
  LOW defect and returned `CLAUDE_APPROVE`. Its two non-blocking coverage
  suggestions were implemented as cost-budget-exhaustion and pre-existing
  execution-state zero-write regressions; focused, module, and complete suites
  were rerun successfully afterward.

### Current checkpoint and next safe action

Implementation, automated validation, independent Claude review, and the
user-authorized independent Codex replacement gate are complete on baseline
`f97a485`. This sequence-2 formal Run/Context claim increment is ready for its
reviewed commit/push. The next bounded backend slice may dispatch and settle
exactly this already claimed sequence-2 Run while preserving the pinned
runtime/repository, single-process attachment, formal Handoff/Gate authority,
recovery semantics, and truthful usage settlement. It must not create a second
Run or Context Pack. Later inventory positions, cross-Stage rework, HTTP, UI,
dynamic provider substitution, and native AI-DLC file installation remain
deferred.

## 2026-07-31 - AWS AI-DLC next-Stage formal Gate configuration (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyStageGateRequest@1.0` and
  `MethodologyStageGateReceipt@1.0` contracts plus checked-in JSON Schemas.
  The request binds the live Task, Control Task, Plan, grouped inventory,
  execution contract, repository/runtime pins, immutable predecessor dispatch
  receipt, settled formal Run/Handoff, completed predecessor Stage, passed
  predecessor Gate, and exact execution-contract sequence-2 Stage/Gate.
- [x] Added
  `agora task migration-next-stage-gate SUCCESSOR_TASK_ID --request PATH
  --credential-env NAME`. Authentication and strict bounded request loading
  complete before service/storage initialization. The principal must retain
  `control_plane.approve`, exact Project scope, and identity equality with the
  migration Gate, execution contract, and first Run claim.
- [x] One immediate transaction revalidates the complete sealed predecessor
  chain, live repository before/after bounded Artifact hashing, runtime
  registry and all command pins, the exact current grouped-inventory route,
  and absence of any sequence-2 Run, Context, compatibility Run,
  consultation, Artifact, or Evidence.
- [x] Only the Control Plane's authoritative `_configure_gate_tx` creates the
  exact canonical sequence-2 Gate requirements. The transaction proves the
  Stage stays `READY`, the new Gate is version 1 and `PENDING`, the route stays
  runnable, and Task, Control Task, Plan, and Stage remain unchanged. Task and
  Control Plane audit events plus the immutable Gate ledger commit or roll
  back together.
- [x] Preserved the semantic distinction that
  `StageRouteDecision.runnable=true` means the routed Stage is ready even when
  its formal Gate is absent. The receipt separately records
  `formal_run_claimable_before=false` and
  `formal_run_claimable_after=true`; it grants no Run, Context, process,
  Artifact, Evidence, dispatch, routing, or substitution authority.
- [x] Added exact replay with current original-principal authorization,
  different-request and identity conflicts, real two-thread convergence,
  event-failure rollback, twelve stale-binding cases, three authorization
  denials, unsettled predecessor, repository/runtime drift, CLI secret
  non-persistence/auth-before-store, loader, schema, and zero-write
  regressions.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-next-stage-gate-v1.md` and linked it
  from the reviewed first-Run dispatch boundary.

### Verification and review state

- [x] Focused next-Stage Gate selection: 24 passed, 103 deselected.
- [x] Complete AWS AI-DLC migration test module: 127 passed.
- [x] Related methodology, Control Plane Stage inventory/routing/lifecycle,
  protocol adapter/orchestration/freeze, and Task orchestration suite:
  388 passed.
- [x] Exact complete non-integration backend suite: 701 passed, 18 deselected,
  with only the existing Starlette/httpx and Windows Proactor cleanup warnings.
- [x] Protocol freeze suite: 34 passed. Protocol Schema export/check,
  system-Temp-isolated `compileall`, `migration-next-stage-gate --help`, and
  `git diff --check` passed.
- [x] Kiro CLI session `805cf6a4-12fc-4f6a-8319-fa1a052c0ecc`
  independently inspected the full diff, governing documents, generated
  Schemas, transaction/authority/replay surface, and regression coverage. It
  independently reran all 24 focused and 127 methodology tests plus Schema and
  compile checks, found no HIGH or MEDIUM actionable issue, and returned
  `KIRO_APPROVE`.
- [x] Independent Claude Code safe-mode review inspected every changed
  implementation, DDL, protocol model, generated Schema, CLI, documentation,
  and test. It found no actionable HIGH, MEDIUM, or LOW defect and returned
  `CLAUDE_APPROVE`. Its environment could not execute the venv, so its verdict
  used static inspection; the complete 701-case execution above is the local
  validation authority.

### Current checkpoint and next safe action

Implementation, automated verification, and both review gates are complete on
baseline `38f047d`. This sequence-2 formal Gate configuration increment is
ready for its reviewed commit/push. The next backend slice must claim exactly
one sequence-2 formal Run and Context Pack only after the persisted Gate exists
with the execution contract's canonical requirements. It must bind selected
predecessor Artifact versions from the settled sequence-1 Handoff, preserve
the current route/runtime/repository/usage reservation, create no compatibility
Run, and start no process. Dispatch, later inventory positions, automatic
rework, HTTP, UI, dynamic provider substitution, and native AI-DLC file
installation remain deferred.

## 2026-07-31 - AWS AI-DLC first claimed-Run dispatch and settlement (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyRunDispatchPolicyDecision@1.0`,
  `MethodologyRunDispatchClaim@1.0`, and
  `MethodologyRunDispatchReceipt@1.0` contracts plus checked-in JSON Schemas.
  The per-Run policy authenticates the exact claim, Context Pack, running
  Stage/pending Gate, route activation, repository revision, complete runtime
  pins, and usage reservation without route-selection, substitution, or
  provider-serviceability authority.
- [x] Added
  `agora task migration-run-dispatch SUCCESSOR_TASK_ID
  --allow-unbounded-native-usage`. Both CLI and service boundaries require the
  explicit provider-usage acknowledgement before any dispatch mutation.
- [x] Fresh capability observation and policy-bound pinned-runtime preflight
  occur outside the write transaction. The immediate claim transaction
  revalidates the complete methodology ledger and live repository/runtime
  bindings, then consumes exactly one process attachment for the existing
  formal Run and Context Pack. It creates no second `protocol_runs` row and no
  compatibility `orchestration_runs` row.
- [x] The default no-shell Runner repeats repository, registry, command,
  resolved-launch, observation, and preflight checks immediately before
  process creation. PID attachment, bounded terminal output, adapter result,
  repository result, and provider usage are durable recovery facts.
- [x] Formal Handoff parsing and the one format-only repair remain unchanged;
  only the Control Plane settles Artifact/Evidence, Gate, Stage, Run, and next
  route. A separate methodology usage ledger and immutable receipt finalize
  afterward, with the active reservation retained across an interrupted
  post-Control-Plane/pre-ledger window.
- [x] Recovery never starts a replacement process: unattached claims settle as
  launch failures, live/unknown PIDs block, dead PIDs settle as interrupted,
  terminal observations replay formal settlement/finalization, and settled
  receipts replay exactly. Cancellation, concurrency, transaction rollback,
  drift, and persisted policy/preflight/terminal tamper cases fail closed.
- [x] Unified Task projection schema `12.0` exposes the existing formal Run's
  preflight, operational/protocol state, provider usage, budget accounting,
  and recovery timing without fabricating compatibility state.

### Verification and review state

- [x] Focused review-fix selection covering dispatch, tamper/recovery, blocked
  reservation, and normative Schema enumeration: 19 passed, 118 deselected.
- [x] Related methodology, runtime preflight, protocol settlement, Task
  orchestration, consultation, projection, and provider-usage suite:
  258 passed.
- [x] Exact complete non-integration backend suite: 677 passed, 18 deselected,
  with only the existing Starlette/httpx and Windows Proactor cleanup warnings.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`,
  `migration-run-dispatch --help`, and `git diff --check` passed.
- [x] Kiro CLI session `7adeb87a-54fa-409f-9b06-8729c126b49a` completed a
  substantive methodology/protocol review and requested one LOW documentation
  fix: the normative freeze Schema list was stale. All five missing
  run-claim/dispatch entries, an exact-list regression, and its informational
  zero-reservation defense were added. The same session independently reran
  the 34 protocol-freeze cases, 20 dispatch-selected cases, Schema check, and
  complete 677-case suite, confirmed no new actionable issue, and returned
  `VERDICT: APPROVE`.
- [x] The first independent Claude Code attempt exhausted its configured
  budget before emitting a verdict and was not treated as approval. A fresh
  complete read-only review inspected every tracked diff and untracked
  implementation/Schema file across policy/preflight, process/PID,
  transaction/concurrency, cancellation/recovery, formal settlement,
  provider accounting, tamper defense, projection, CLI, documentation, and
  regression coverage. It found no actionable HIGH, MEDIUM, or LOW issue and
  returned `VERDICT: APPROVE`.

### Current checkpoint and next safe action

Implementation, automated validation, actionable-finding resolution, and both
review gates are complete on baseline `795ffab`. This bounded first claimed-Run
dispatch/settlement increment is ready for its reviewed commit/push. The
succeeding slice must remain backend-first and derive later-Stage
claim/dispatch authority from the settled formal Gate and grouped inventory;
automatic rework, HTTP, UI, dynamic provider substitution, and native AI-DLC
file installation remain deferred.

## 2026-07-31 - AWS AI-DLC first formal Run and Context Pack claim (reviewed)

### Bounded implementation

- [x] Added hash-sealed `MethodologyRunClaimRequest@1.0` and
  `MethodologyRunClaimReceipt@1.0` contracts plus checked-in JSON Schemas.
  The request binds the exact live Task, Control Task, Plan, inventory,
  execution contract, route-activation receipt, repository, first
  Stage/Gate/runtime, and caller-selected stable Run/Context identities. It
  structurally fixes `start_runtime_process=false`.
- [x] Added
  `agora task migration-run-claim SUCCESSOR_TASK_ID --request PATH
  --credential-env NAME`. Authentication completes before service/storage
  initialization and requires the original migration-chain principal with
  current `control_plane.approve` and exact Project scope.
- [x] One immediate transaction revalidates the complete
  migration/contract/activation ledger, Task/Control Task/Plan/inventory
  versions, authoritative ready first route, current repository before/after
  bounded Artifact hashing, runtime registry/command hashes, and absence of
  prior execution state. It atomically seals the exact `ContextPack@1.0`,
  creates the formal `protocol_runs` record, advances the authoritative Stage
  `ready -> running`, reconciles the Control Task, records the reservation,
  advances Task metadata/version, and writes both audit streams.
- [x] The Context Pack copies the Stage Contract, forbidden constraints, and
  Run budget exactly; retains registered first-Stage seeds in contract order;
  rejects selected prior-Stage outputs; derives output identities from
  Task/Stage/Run/source-output template; and carries bounded execution,
  activation, repository, role, sensor, source-input, Handoff, and Gate policy
  entries. It injects no transcript, memory, project knowledge, preference,
  Native State, Artifact, or Evidence.
- [x] Every materialized first-Stage seed path/hash must be present in the
  live-rehashed migration seed set and match its immutable registration.
  External seed references remain separate from `protocol_artifacts`; the
  generic Control Plane Run-start path still rejects unregistered inputs unless
  its private caller supplies exact already-transaction-validated bindings.
- [x] Persisted one immutable methodology claim/reservation record and exposed
  its pinned runtime and active Token/cost reservation through the unified
  projection. No compatibility `orchestration_runs` row, PID, prompt process,
  runtime preflight, provider output, protocol Artifact, or spawn/substitution
  authority is fabricated.
- [x] Added exact replay/current-authorization, real two-thread serialization,
  full rollback, repository/Artifact/runtime drift, stale request, different
  replay, generic external-input escape, oversized Context-entry, CLI
  auth-before-store, schema-registration, compatibility, and projection/budget
  regressions.
- [x] Documented the boundary in
  `docs/architecture/aws-aidlc-methodology-run-claim-v1.md` and updated the
  frozen Context/external-seed contract.

### Verification and review state

- [x] Final focused negative/security regression selection: 16 passed,
  81 deselected.
- [x] Related methodology, Control Plane Run/routing/lifecycle, protocol
  orchestration, and Task orchestration suite: 252 passed.
- [x] Exact complete non-integration backend suite: 657 passed, 18 deselected,
  with only the existing Starlette/httpx and Windows Proactor cleanup warnings.
- [x] Protocol Schema export/check, system-Temp-isolated `compileall`, and
  `git diff --check` passed.
- [x] Kiro CLI sessions `d51e88be-e85a-4e66-98e4-8516de097ec7` and
  `a7d2fa58-4114-42da-9e4d-8f3ac45c47bb` independently reviewed the
  methodology/Context boundary and the transaction, Control Plane, schema,
  projection, CLI, and regression surface. Their only non-blocking seed-set
  recommendation became an explicit live-rehash guard. Final verified
  post-fix session `03ae8ea2-fc01-433e-aadc-993127404dc3` read the exact final
  security-test surface, reran the 16 focused cases, and returned
  `VERDICT: APPROVE`.
- [x] Independent Claude Code safe-mode review found no implementation defect
  but initially returned `VERDICT: REQUEST_CHANGES` for four direct
  security-regression gaps. The generic external-input boundary, stale request,
  different replay, and 20,001-character Context rollback tests were added;
  targeted review of the exact final files and results returned
  `VERDICT: APPROVE` with no remaining actionable findings.

### Current checkpoint and next safe action

Implementation, automated verification, all actionable-finding resolution, and
both review gates are complete on baseline `4f9dcc9`. This bounded first formal
Run/Context claim is ready for its reviewed commit/push. The next backend slice
must attach process dispatch to this already claimed Run without creating a
second Run or Context Pack: collect a fresh pinned-runtime observation and
preflight outside the SQLite write transaction, require the existing explicit
unbounded-native-usage acknowledgement, immediately recheck the exact
repository/registry/command/resolved-launch binding before spawn, and preserve
no-substitution/fail-closed settlement. HTTP, UI, automatic cross-Stage rework,
dynamic provider substitution, and native AI-DLC file installation remain
deferred.

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
both review gates were completed on baseline `75c0c88`; the bounded first-route
activation was committed and pushed as `4f9dcc9`. The succeeding formal
Run/Context Pack claim is recorded in the active section above.

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

## Increment: workspace isolation via provisioned linked worktrees (2026-08-22)

Context: an earlier increment tried to isolate agent workspaces by `git init`ing
each one inside `ProjectRegistry` (commit 9e8b84d). Independent Codex review
found this violated the Phase 5 workspace contract (provisioning is explicit and
never at queue/run time) and broke the provisioner; a follow-up that moved the
`git init` into `dispatcher.queue()` was also rejected (contract violation,
event-loop-blocking Git in the async path, and — critically — a workspace mapped
to a repo root could have the whole repository staged and committed).

Resolution (reviewed across three Codex rounds):

- Removed all automatic `git init` from the registry and the dispatcher; queueing
  no longer touches the workspace.
- Deliverable discovery, content/raw serving, and handoff commit now fail closed
  unless the workspace is a *linked worktree* whose own top-level is exactly the
  workspace (`deliverables._is_provisioned_worktree`). A bare repo root — a
  project root or the outer Agora checkout — is rejected, so a run can never
  list, read, or commit the surrounding repository. Verified: the guard rejects
  the Agora repo root and independent repo roots, accepts a real linked worktree,
  and a repo-root handoff leaves the outer HEAD and index untouched.
- `POST /api/projects` now provisions the new project's workspaces as linked
  worktrees at creation (the explicit Phase 5 action) via
  `WorkspaceProvisioner.ensure_source_repo` + `provision`. `ensure_source_repo`
  makes the root its own repo only when it is its own `--show-toplevel` (so a
  root nested in Agora is `git init`ed rather than sourcing worktrees from
  Agora), seeds an unborn HEAD, and ignores `.agora/`. `source_is_git` now
  requires the root's own top-level, closing the ancestor-sourcing hole on the
  direct `POST /api/workspaces/provision` path too. Pre-existing non-empty
  project directories are rejected before any Git operation.

Tests: linked-worktree happy path and fail-closed cases (nested dir, repo root,
non-repo) for discovery; a handoff safety regression (repo-root workspace leaves
the outer index empty and HEAD unchanged); create-project provisioning inside an
outer repo leaves that outer repo's HEAD and worktree list unchanged; direct
provision on a nested non-repo root is rejected without adding an outer worktree;
unborn-HEAD seeding; pre-existing-non-empty-dir rejection; and a content/raw
guard test. Focused suite (workspaces, execution, handoff, deliverables,
projects, settings, provisioning) passed.

Full provenance is now enforced. `deliverables.is_ready_worktree` positively
proves a workspace is exactly a run's provisioned worktree — its own
`--show-toplevel`, a linked worktree, sharing the project root's Git common
directory, on `worktree_branch(project_id, adapter)`, with a HEAD — and fails
closed on anything indeterminate (git missing/timeout/nonzero, unexpected
output, empty HEAD). The project root must itself be a repository's own main
worktree root (`_is_repo_root`: its `--show-toplevel` is itself AND its git dir
equals its common dir), closing both the ancestor-nesting bypass and the
linked-worktree-as-root bypass — otherwise a sibling worktree of some other repo
could be trusted as the source. Every consumer gates on the same predicate:
`dispatcher.execute` before launch, the deliverable list/content/raw endpoints,
`handoff` commit, and the review-prompt's reading of worker deliverables. So an
agent can never execute in, and deliverables can never be read from or committed
to, a repo root, the project/Agora checkout, an ancestor repo, or an unrelated
or wrong-branch worktree.

Off the event loop: the launch gate, the git-heavy review-prompt build
(`_dispatch_review` is async and offloads `_review_prompt`), and every
create-project provisioning inspection (`provision`'s `_inspect` calls and
`ensure_source_repo`'s `_git_sync` calls all via `asyncio.to_thread`).
Provisioning locks are shared process-wide across all provisioner instances
(Invariant 6); the create-time provisioner is built inside the best-effort
boundary (never a 500 after the project is created); unborn-HEAD seeding fails
closed on a non-empty OR an indeterminate index inspection rather than committing
pre-existing staged files.

Reviewed across seven independent Codex rounds. Regressions cover the predicate
(wrong-branch, unrelated-repo, ancestor-root, linked-root, repo-root, plain dir),
the launch gate, the real list/content/raw HTTP endpoints, handoff (repo-root and
wrong-branch), the auto-review chain, and provisioning (nested-root reject,
unborn-HEAD seed, pre-existing-dir reject). Full backend suite: 759 passed with
one unrelated pre-existing failure (`test_web_ui` expects a retired `/settings`
path to redirect; that path was un-retired earlier when the Settings page
shipped) — untouched by this increment and tracked separately.

Next safe action: commit this workspace-isolation increment; fix the stale
`test_web_ui` retired-path expectation separately.

## Increment: control-plane repositioning — 1.2 parts 1–2 (2026-08-24)

Repositioning Agora as a user-controlled project control plane (thin version:
adopt the ideas that make the human's mental model clear — six questions,
progress vs verified-completion, deterministic next action, charter/alignment —
implemented thin; roles / milestones / CQRS / full capability tokens explicitly
cut or deferred for a solo local tool). Design confirmed via a mockup (project
portfolio in four groups; a merged project page = cockpit header + tasks that
expand inline into their thread with an in-context decision; requirements-intake
with in-UI clarification; human-readable runs).

1.2 plan: (1) Charter, (2) acceptance criteria + metrics, (3) Next Action engine,
(4) UI. Parts 1–2 landed on branch `feature/control-plane-1.2`:

- Project Charter (goal / non-goals / success_criteria) on the registry Project;
  human-facing `PUT /api/projects/{id}/charter`; surfaced in project summaries
  and listings. No agent code path writes it.
- `agora/tasks/progress.py` (pure): acceptance criteria derived from a task's
  APPROVED spec; a task-derived criterion status pending -> produced -> reviewed
  -> verified where nothing is produced/reviewed without a successful worker run;
  `progress_pct` (produced weight) vs `verified_pct` (only human-approved weight)
  so an agent cannot make a project look done by producing code; half-up
  rounding; a no-spec task counts as one self-consistent unit; cancelled tasks
  leave the denominator; health = attention when a task is blocked/failed.
- `GET /api/projects/{id}/dashboard` projection: per-task criteria + metrics and
  the project rollup + charter; APPROVED specs only; exact run-role matching;
  scans succeeded runs and stops once both roles seen; marks `truncated` past the
  task cap.
- Tests: metric math, charter round-trip/endpoint, dashboard projection; broad
  regression 310 passed. Also surfaced the handoff delivery (commit + files) on
  done task cards.

Reviewed by Codex; its metric-integrity findings were fixed (produced-without-
worker, draft-spec leakage, fail-open roles, weight reconciliation, rounding,
N+1). Two findings deliberately deferred to 1.3: the task-state and charter
routes are not yet authenticated (the app's non-control-plane routes gain auth in
1.3), so verified_pct and charter integrity are not tamper-proof on a shared host
— acceptable for a solo local tool, and noted in code.

Next safe action: build (3) the Next Action engine (one explicit next action or a
blocker per task/project), then (4) the project-page / four-group-portfolio UI
consuming the dashboard. Branch is not yet pushed.

## Increment: control-plane repositioning — 1.2 part 3 (2026-08-24)

Part (3) of the 1.2 plan — the Next Action engine — landed on branch
`feature/control-plane-1.2`. It answers the human's "what do I do next?" without
reading the whole board.

- `agora/tasks/next_action.py` (pure, mirrors `progress.py` — no I/O, derives
  from signals only): `task_next_action(state, *, spec_state,
  pending_clarifications, has_worker_success)` derives exactly one next step per
  task — `owner` (human / agent / none), `action` (a stable slug the UI
  localizes, single source of truth in `ACTIONS`), `attention` (True only for
  blocked/failed, matching the `health` metric), and an English `summary`
  fallback. Requirements routes on signals in order: unanswered clarifications ->
  answer; no spec -> draft; draft -> approve; approved -> advance; rejected/
  superseded -> revise. `spec_state` accepts a `SpecState` enum or its value.
- `project_next_action(items)` rolls the per-task actions up to one project-wide
  next action: attention outranks every routine step, then human decisions
  outrank starting a run, which outranks waiting on a run; ties break on task
  priority then list order. Also reports `attention_count` and `pending_count`
  for the cockpit header. All-terminal / empty -> a `none` action.
- `has_pending_clarifications()` treats a blank answer as unanswered.
- Wired into `GET /api/projects/{id}/dashboard`: each task view gains a
  `next_action`, and the projection gains a project-level `next_action`. Metrics
  still count APPROVED specs only; the next action reads the *current* spec state
  (draft/rejected) so it can route to approval/revision.
- Tests: `tests/test_next_action.py` (lifecycle ladder, attention priority, spec
  routing, enum-or-value, tie-breaks, empty/all-terminal) + dashboard integration
  assertions. Narrow run 28 passed; full backend suite 786 passed.

Reviewed by Codex (review --commit); three findings, all confirmed against the
real code and fixed:
- [P1] `running` returned `await_run` unconditionally, but a finished run leaves
  the task in `running` (the dispatcher raises a review/approval Attention and
  the human's response advances it via `review_gate`). Added a `has_active_run`
  signal: active run -> `await_run`; no active run -> `verify_review` (the human
  must review the result). The dashboard checks for a QUEUED/RUNNING run.
- [P2] a rejected spec made `RequirementStore.current()` (draft/approved only)
  return None, so the engine emitted `draft_spec` and the `revise_spec` branch
  was unreachable. The dashboard now falls back to `list_for_task()[0]` (the
  latest spec of any state) when current() is None.
- [P2] task-page truncation (>500) could hide a lower-priority blocked/failed
  task, letting the rollup return a routine action with `attention_count: 0`.
  When truncated, the dashboard now also pulls the project's BLOCKED/FAILED tasks
  and folds them into the rollup, and `next_action.partial` flags that the
  non-attention rollup may be incomplete.
Added tests for each fix (running active/finished, partial flag, rejected->revise
endpoint, blocker-past-the-page endpoint). Full backend suite 790 passed.

Next safe action: (4) the project-page / four-group-portfolio UI consuming the
dashboard's metrics + next_action. Branch is not yet pushed.

## Increment: Task Workspace — repositioning pivot (2026-08-25)

After reviewing Kandev (a competitor) the human set the next-phase priority: stop
spreading capability across pages and build ONE task-centric three-column
**Task Workspace**, so opening a task immediately answers "what's the goal, is it
trustworthy, has it drifted, what do I do next?". Kandev is a reference only
(AGPL-3.0 — no code copied); the switcher idea is reimplemented on Agora's own
data model. This is a scoped validation iteration with explicit exit criteria
(30s to create a task; 5s to see where a project stands; every Done shows
acceptance evidence; mid-task course-correction; no page-hopping from need to
Verified). Everything else is frozen (more agents, Docker/SSH, workflow visual
editor, cost stats, arbitrary layouts, etc.). See memory task-workspace-next-phase.

Part 4a — project cockpit in the portfolio (`12c4284`): when scoped to one
project, the portfolio renders the 1.2 dashboard (charter with an edit slide-over,
progress vs verified vs health, the one deterministic next action + attention/
pending counts); task cards gain their per-task next action and a verified bar.
Dashboard types + fetch fn and the nextAction/criterionStatus/health enum labels
(EN/ZH) become the workspace's foundation.

Part 4b — Task Workspace shell (`68dbdfc`): new `/workspace?project=&task=`
(static-export query params), set as the default landing (`/` redirects). Left
TaskRail (persistent): project filter + search + a dead-simple 3-question create
(accomplish / done-when / must-not-do; the rest uses project defaults under
Advanced), tasks grouped Needs you / Running / Review / Done. Always-on Agora
header for the selected task (goal, stage, verified completion, blocker, next
step) reusing the dashboard. Right tabs reuse existing data read-only
(Requirements / Run / Attention / Changes) with deep links to the full pages.

Part 4c — timeline + message box (`5bad34a`): the center column is a unified,
chat-like timeline of the task's event log (created, stage changes, spec
drafted/approved/rejected, runs, attention, handoff, plus human notes), with a
bottom box that appends a non-destructive `user_message` event (no agent
dispatched — session resume / run-from-chat is a later phase).

Reviewed by Codex (review --base); seven P2 findings, all confirmed and fixed
(`ddb5c97`): clear a selection the project filter excludes; verified -> Review
group (not Done); a blocked task never shows "Blocker: None"; load the rail with
limit=500; render failed reads as "Unavailable" not a false "empty"; don't show a
no-spec task's implicit weight as "0/1 criteria"; order the rail by real activity
(updated_at + latest run) not the stale manifest updated_at.

Verified end-to-end against a running backend (dashboard metrics/next_action,
event stream, user_message round-trip). Frontend build (12 static pages incl.
/workspace), tsc, eslint, and EN==ZH key parity (796 each) pass. Branch not pushed.

Next safe action: run the exit-criteria validation on a real task in the browser,
then decide continue-vs-stop per the agreed test.

## Increment: Task Workspace — make each step show its substance (2026-08-25/26)

Driven by direct user feedback while looking at the workspace: it was showing
labels, not substance ("显示的是屁"). A run of content-surfacing changes, all
frontend, all reusing existing unauthenticated APIs (no backend change):

- `04cde90` Run output, not a ledger: the timeline drops the noisy run.* events
  and renders one rich RunCard per run (listRuns + getRun) — the prompt, the
  actual stdout/stderr (terminal-style, expandable), exit code, error, and the
  changed files (lazy getRunDeliverables).
- `0c2bbc7` Two plain-language layers lead the workspace (user chose this shape):
  a ProjectStrip (name, done/total, verified %, the one project next action,
  attention count — orientation that survives task switches) and an
  OutcomeSummary per task ("what it did" = first meaningful line of the latest
  succeeded run + delivered files/commit; trustworthy completion; the one next
  step; a done task reads "Done — nothing to do", not an empty grid). Replaced the
  abstract fact-grid header.
- `e7fdc88` Content-bearing steps show what they produced: spec.created/approved
  expand to the spec (summary, requirements, given/when/then scenarios); a
  review-role run is titled "review".
- `382c3c4` Plain-language milestones replace state-machine jargon (running ->
  review => "Development done — in review", etc.).
- `1c37439` Deliverables are openable: each changed file in a RunCard can be
  viewed inline (getDeliverableContent) or opened rendered in a new tab
  (deliverables/raw) — so the design/architecture doc and the built page are
  actually visible, not just filenames.

Ground truth checked against the running backend: a hotel task's spec, run stdout,
and deliverables (incl. docs/hotel-site-design.md, mockup.html) all exist and are
now surfaced. Honest gaps recorded for the user: structured FE/BE progress and
"architecture status" are NOT tracked as data; decision points (attention +
choice) and reviewer feedback only exist for tasks that actually ran a
review/attention flow (the demo tasks had none). Each increment: build (12 static
pages), tsc, eslint, EN==ZH parity all pass. Branch not pushed.

## Proposal (awaiting review): Kandev-style live terminal + switchable sessions

Wrote `docs/control-plane/live-terminal-integration-proposal.md` (DRAFT, not
implemented) for borrowing Kandev's switchable per-task session + live run status
+ direct terminal, kept subordinate to Agora's frame (goal / verified completion /
next step / timeline-as-record). Core: dispatcher writes an in-memory per-run
ring buffer with a cursor; SSE `GET /api/runs/{id}/stream?cursor=N` +
`GET /api/tasks/{id}/active-run`; a Terminal mode in the workspace center (xterm.js)
with a live status pill and a live TaskRail dot; read-only by default, stdin only
for `bidirectional` adapters; Windows uses piped stdout first, ConPTY only if a
TTY is needed; phased P0 poll -> P1 SSE -> P2 xterm -> P3 interactive.

Codex review of the proposal was attempted but did NOT complete — its tool host
crashed ("code-mode host exited during handshake") and the task was stopped before
producing findings. No verdict was recorded. Re-running the proposal review is the
open action here.

Next safe action: re-run the Codex review of the live-terminal proposal (avoiding
the code-mode path), then decide whether to build P0/P1. Branch not pushed.

## Repositioning: Agora becomes an AI agent development workspace (2026-08-27)

The workspace record for this project had been stale since April (it still
described the retired council). Reconciled against the code, with the decision
and its rejected alternatives recorded in the workspace layer rather than here:
`tasks/agora/adr/0001` in claude-workspace.

The short version, because it changes what "in scope" means for every increment
below: Agora has had three theses. v0.1-0.4 was the autonomous council, whose
code is gone. v0.5-1.1 was the AWS AI-DLC formal control plane, ~45k lines that
remain and are now **frozen** - not deleted, not extended, not surfaced in the
UI, and no new code depends on it. The current thesis is an AI agent development
workspace: the claude-workspace working model (resume-where-you-left-off, an
anchored goal, change control, decision records, lifted lessons) productized
across several agents, projects and environments. Its single acceptance test is
"I am willing to use this every day myself".

What carries forward from 1.1: `execution/adapters.py` (the three agent
adapters), `workspaces/provisioner.py` (worktree isolation), and the Task
Workspace frontend. Everything else in orchestration / protocol / control_plane
is the frozen layer.

Also verified, because the product depends on it: all three agent CLIs can now
resume a session by id (claude 2.1.246 `--resume` / `--session-id`, codex-cli
0.149.1 `exec resume`, kiro-cli 2.19.1 `--resume-id`). This **reverses**
`docs/control-plane/phase-6e-adapter-capabilities.md`, which was written against
Claude 2.1.210 / Kiro 2.12.2 and concluded Kiro exposed no machine-readable
bidirectional protocol. That matrix has to be regenerated by probing rather than
trusted as written. Details in claude-workspace
`lessons/coding-agent-cli-headless-resume.md`.

## Increment: a run's delivered file list becomes a fact about the run (2026-08-27)

`10e3140`. `deliverables.list_changes` reads the worktree at request time, so a
finished run's card answered "what is changed right now", not "what did this run
deliver". Wrong in three ordinary situations: a handoff commit empties the list,
a later run in the same worktree is attributed to the earlier one, and two runs
in one worktree show an identical list. This was the headline evidence on every
run card, so it was confidently wrong.

A run now freezes its delivery on reaching a terminal state, and later reads are
answered from that record. Seven guarantees were needed, each closing a way the
claim could still be false:

- snapshot on completion, in both the CLI and app-server paths;
- baseline subtraction (`baseline_state`), because the queue allows another run
  on an already-RUNNING task and a retry reuses the worktree, so it is not clean
  at launch;
- collection failure is not emptiness (`_read_changes` returns None on git
  failure; only a successful read yields `[]`);
- recorded digests are actually verified on content/raw (409 on mismatch, 404 on
  a path the run did not deliver);
- no digest means fail closed, since a missing digest covers deleted and
  non-regular files, not only oversized ones;
- cancelled and abandoned runs also record what they wrote;
- **one run at a time per worktree** (`_workspace_limits`). Workspaces are keyed
  by adapter, so two runs of one project+adapter resolve to the same directory
  while `max_concurrent_per_project` defaults to 2 - they were free to write one
  git worktree concurrently. This is a behavior change beyond delivery reporting
  and fixes a pre-existing hazard that the snapshot only made visible. The slot
  is acquired before the capacity limits, so a run queued behind a busy worktree
  does not sit on capacity other projects need.

Also: MAX_FILES truncation is recorded, so a path absent from a partial list is
unknown rather than known-not-delivered; content and raw are built from one
bounded read whose bytes are the ones verified, closing the check-then-serve
window; `content_available` tells the UI when the worktree behind a list is gone;
and both frontend consumers honor the new contract instead of keeping only
`files`.

Two existing tests asserted the defective behavior and were corrected, not
relaxed: `test_project_dashboard_progress_vs_verified` forced a task to DONE with
raw SQL and no runs at all, then asserted 67% verified completion.

Review: six rounds of Codex review, 24 findings, all confirmed and addressed.
Tests: backend 809 passed; frontend tsc, eslint and build (12 static pages)
clean. Blockers: none. Known defects are recorded as D-1..D-6 in the workspace
RESUME.

## Increment: "where did I leave off", answered from the record (2026-08-27/28)

`4e7cfa2` plus the still-uncommitted local-day change. Opening a task showed its
state but not what had just happened to it, so the first move was to read the
event log. `tasks/recap.py` derives the last day the task moved and what moved on
it - from records only, never a model call, because that question is about
recorded facts and generating it would make the answer plausible instead of true.

The day is the most recent one with activity, not a per-viewer watermark: a task
answers on first open, nothing has to be stamped when a page closes, and it
matches the model being productized (a RESUME file's "last updated" is a date,
not a reader's high-water mark). Pure like progress.py and next_action.py; items
are stable slugs the UI localizes, aggregated rather than enumerated.

Codex round 1 raised five findings, all fixed: the run scan ordered by
`queued_at` while days derive from `finished_at`, so a long run queued earlier
but finished later could be missed and point the recap at the wrong day;
bookkeeping events (archived, spec.updated, attention.expired) could win the day
and then produce nothing, hiding the last day with real content; a truncated
snapshot count was presented as exact; a succeeded worker run with an empty
snapshot was called "delivered" when it is a verified no-op; and both reads were
unbounded over a task's whole history (now one indexed lookup per side, then that
day only, reporting itself `partial` past its caps).

Days are the **viewer's**, not UTC's. In UTC+9, 02:30 local is 17:30 UTC on the
previous date, so bucketing by UTC files a late night under yesterday - off by a
day exactly when someone worked late. `tasks/localday.py` owns this. The zone
comes from `control_plane.timezone_offset`, with the process zone as a fallback
only for a plain local run: Codex correctly noted that the supported Docker image
is `python:3.12-slim` with no `TZ`, so relying on the process zone silently
reverts to UTC in a container - and a backend on a remote server sits in that
server's zone, which the remote-project direction makes routine. Offsets are
written `+09:00` rather than as zone names because Windows ships no tz database
and `ZoneInfo` raises there without `tzdata`. Store queries changed from a UTC
string prefix to a UTC **range**, since one local day spans two UTC dates.

Three further problems, each the original bug re-entering by another door: adding
a fixed 24 hours to a local midnight is wrong across a DST change (a local day is
23 or 25 hours), so both bounds are now computed as local midnights;
`parse_offset` rejected `Asia/Tokyo` but accepted `+05:60`, which `timedelta`
normalizes to `+06:00`, so a typo silently selected another zone; and the
committed `config.yaml` carried `+09:00`, which would have overridden every other
deployment's zone with one person's. The committed default is now unset, and a
test asserts it stays unset.

Review: Codex rounds 1-3 on this increment, 8 findings, all addressed. The last
round found no code defects and one process finding - that this file was stale,
which is what this entry corrects.

Tests: backend 835 passed; frontend tsc, eslint and build clean; 26 cases in
test_recap.py.

Blockers: **the review gate in AGENTS.md names a Kiro CLI review and an
independent Claude Code review; this work used Codex as the independent
reviewer**, following the claude-workspace rule that outsources review to a
different model so nobody grades their own homework. The Kiro clause scopes that
review to protocol / AI-DLC / reconciliation / methodology boundaries - the frozen
layer, which none of this work touches. Reconciling the two rule sets is an open
decision for the maintainer, recorded here rather than resolved unilaterally.

Next safe action at the time of writing: obtain the Kiro review for the
local-day change, then commit it. (Done: Kiro returned APPROVE and the change is
committed as `20876b6`. The recap rendering that followed is its own entry
below.)

## Increment: the recap rendered in the Task Workspace (2026-08-28)

The recap endpoint existed; nothing showed it. `ResumeBrief` sits between the
project strip and the outcome summary, because "where was I" is the question you
open a task with — it comes before "what state is it in" and "what is next".

Every line is localized from the stable `kind` slugs the backend derives from
records. `item.summary` (the server's English fallback) is deliberately not
rendered as display prose, except for an unrecognized kind, where showing the
recorded fact in English beats dropping it silently.

Three distinctions the backend keeps are carried through rather than flattened:

- `delivered` fans out to three separate message keys — an exact count, a floor
  (`files_at_least`, when the snapshot itself was truncated), and an unknown
  count (`files === null`, no snapshot). One template with a condition would have
  merged claims the backend separates on purpose.
- Four display states, four wordings: loading (render nothing), error
  (unavailable), never-active (`day === null`), and **a day with activity but no
  summarizable items** — a real backend state, reachable when the day is picked
  by an activity-only event or a cancelled run, which was initially rendered as
  "nothing has ever happened" and contradicted the timeline below it.
- The heading follows `days_ago`: today / yesterday / N days ago / the date
  itself, so a task untouched for three weeks never claims "yesterday".

D-6, fixed in the same change because it is the same principle: a failed delivery
read was swallowed, leaving `delivery` null, `fileCount` 0 via `?? 0`, and the UI
saying "nothing produced yet" about a load failure. All three reads on this view
(dashboard, specs, delivery) now clear their error on success as well as setting
it on failure, so an explicit Refresh after a transient failure cannot leave real
data sitting next to "unavailable"; and the error reaches both consumers, since
the summary saying "unavailable" while the Changes panel said "no changes" is the
same page contradicting itself about one fact.

Staleness was the whole second half of this increment, and it is a class of
problem the backend does not have: the endpoint computes a correct answer every
time it is called, so everything here is about *when* it is called.

- Posting a note changes the answer, so `onPosted` refetches. Without it the
  timeline showed today's message beside a heading reading "yesterday".
- Concurrent reads are guarded by a request token, because the recap is **not**
  idempotent across time — the same URL returns a different answer before and
  after a note. An older response resolving last would overwrite the fresher one.
- An explicit Refresh reaches this component through a `refreshNonce` prop: a run
  finishing without changing the task's state leaves every effect dependency
  untouched, so Refresh silently updated only the rail.
- A page left open past midnight kept saying "today". The endpoint now returns
  `next_rollover` — the instant the display zone's day changes, computed
  server-side because the current offset cannot locate the next midnight across a
  DST change — and the client schedules a refetch just past it, plus one on
  `visibilitychange`. The reschedule keys on a settle counter rather than on a
  field of the response: keying on `recap.day` looked right but does not advance
  on a quiet task (day is the last *active* day; midnight moves `days_ago`), so
  the timer fired once and never rescheduled, and a page left visible went stale
  after a single night.

Review: Codex 6 rounds (3 + 1 + 2 + 5 + 2 + 1 findings) and Kiro 6 rounds
(APPROVE, APPROVE, APPROVE, CHANGES_REQUESTED, then two clean), all findings
confirmed and addressed. Three things worth recording about the gate itself:

The reviewers disagreed once, on whether the refetch race was benign. Kiro judged
it benign, reasoning that the recap is idempotent and self-corrects on the next
activity; Codex called it a defect. Codex was right — the recap is not idempotent
across time, and "self-corrects on the next activity" presupposes further
activity, which a user who posts once and then watches the screen does not
produce. Kiro withdrew the call in a later round and identified its own error as
begging the question. Two gates agreeing is not the signal; two gates plus
checking their reasoning is.

Both reviewers then independently found the same reschedule defect, from
different directions. That convergence is the strongest signal either produced.

Round 5 produced the two defects that mattered most, and they were the same
defect on both sides of the wire — a derivation reading the clock more than once.

- Codex P1, the client: the rollover effect clamped its delay to zero, so a
  boundary already in the past fired immediately. The failure path settles too,
  and the effect keys on that settle counter, so a failed midnight read became
  refetch → fail → settle → refetch, a tight loop from an offline page. Fixed by
  splitting the effect into a future-boundary path (schedule the real read, clear
  any backoff) and a passed-boundary path (30s doubling to a 5-minute cap). The
  backoff lives in a ref, not state: writing it must not re-run the effect and
  restart the timer it exists to slow down.
- Codex P2 and Kiro P3, the server: the response read the clock and resolved the
  zone several separate times. A request straddling local midnight took its day
  from before the boundary and its `next_rollover` from after it, so the client
  waited for the midnight *after* the day it was showing and called yesterday
  "today" for up to a day. The endpoint now takes one `(zone, now)` snapshot and
  threads it through every derivation, on the empty-return path as well as the
  main one; `local_today`, `zone_offset_minutes` and `next_rollover` gained an
  optional `now=`, and the two stores an optional `zone=`, both defaulting to the
  previous behaviour so no other caller changes.

Round 6 found one thing, and it is the sharpest comment either gate produced this
increment: `test_next_rollover_is_the_zone_s_next_midnight_not_now_plus_24h`
captured `now` once and then let `next_rollover` read the clock again — the test
re-enacted the exact cross-midnight race it exists to catch, and would have
failed intermittently. Pinning the instant into the call fixes it. A test that
reproduces its own subject is worth more attention than its P3 severity suggests.

Tests: backend 826 passed, 31 cases in test_recap.py; frontend tsc, eslint,
build (12 static pages) and the 14 control-plane view tests clean.

Recorded, unresolved: this suite collected 826 where the previous entry recorded
836. Nothing here regressed — the only test file changed since that commit is
`test_recap.py`, and it only gained cases — so the earlier figure was measured in
some other state or misrecorded, and cannot now be reproduced. The record itself
is the defect: "N passed" cannot distinguish "fewer tests ran" from "fewer tests
exist", which is the `none-means-unknown-not-absent` failure repeated at the
process layer. Entries from here record the collected count alongside the passed
count.

Gate: the AGENTS.md gate names a Kiro review and an independent Claude Code
review; review goes to Codex instead, decided by the maintainer on 2026-08-29 —
implementation is dispatched to Kiro CLI (claude-opus-5), so Codex is the model
that did not write the code. AGENTS.md still says otherwise and needs updating.

Next safe action: W0(b) step 3 — dispatch a run for the selected task into its
provisioned worktree from the workspace view, then write the outcome back so the
next morning's recap reads as yesterday's step. Branch: `main`, three commits
ahead of `origin/main`, not pushed.

## W0(b) step 3 — Continue: start a run from what the task recorded

Continue sits with the Resume Brief because it acts on exactly what that panel
says. It never dispatches an invented prompt: the seed is shown and editable, and
only the button below it sends anything. Adapter is pinned to Codex; choosing
executor and environment at dispatch time is later work.

The decisions — may this be offered, what does a refusal mean, what does the seed
say — live in `frontend/src/lib/continue-run.ts`, free of React so `node --test`
runs them. That module is where this project's standing rule is actually decided:
an unknown must never render as an absence.

### What review turned this into

Five rounds, both gates. What arrived as a UI wiring task left as a control-plane
change, because every guard added to close one bypass opened the next:

1. The gate lived only on the client. Two tabs could each observe zero active runs
   and POST with the same `expected_task_version` — a task already RUNNING is not
   version-bumped by queueing — so both succeeded: two paid runs on one worktree,
   the second working on the first's output. Closed by a refusal inside
   `create`'s transaction, scoped to the role.
2. Role came from client metadata. `{"agora_role": "anything"}` matched no
   existing role and walked straight past the new guard. Role is no longer
   settable from outside; the reserved keys are refused the way reserved event
   types already were.
3. A run cancelled while QUEUED was never settled — first when the coroutine's
   handler was skipped, then when the coroutine never ran at all — and the guard
   then refused every new worker run on that task, permanently.
4. The ordering was still only advisory: an Attention item does not bump the task
   version, so nothing stopped a worker starting while a human gate waited. Moved
   into the create transaction. Decision, alternatives and accepted failure mode:
   `docs/architecture/run-gate-ordering-v1.md`.
5. That refusal then created its own denial of service. The Codex permission
   bridge raises `approval`, not `question`, with no expiry and nothing settling
   it at run end — so one unanswered permission prompt locked its task out of all
   future work. Bridge prompts are now swept in the transaction that settles their
   run, cut **by origin** rather than by run state: settling by run state would
   have destroyed the review gate, which is raised *after* its review run is
   already terminal.

The recurrence is the finding, not any one of the five. Each fix was correct and
each created the next lockout, because moving an ordering constraint into a
transaction forces the question the client version never had to answer: when does
the constraint lift?

### The test that guarded nothing

Round five asked a different question — not "is this code right?" but "does the
test that guards it bite?" The regression protecting the origin-vs-state cut never
constructed the one arrangement that distinguishes them, so widening the sweep
back to run state would have kept it green while the review gate quietly died. The
production code was correct; its guard was theatre. No review of the
implementation could have found that, because the implementation was not wrong.

### Tests

Backend 851 collected, 851 passed (from 826 at the start of this slice; the
increase is this change's own tests). `test_web_ui.py` 14 collected, 14 passed
once a static export existed. Frontend: 65 collected, 65 passed; tsc, eslint and
build (12/12 static pages) clean; `export_protocol_schemas.py --check` and
`git diff --check` clean. Collected counts are recorded alongside passed counts
from this entry on, per the gate item in AGENTS.md — "N passed" cannot tell a
shrinking suite from a shrinking run.

### Review

Eight rounds, both gates. Codex ran the first four and hit its usage limit twice;
the remaining rounds used GPT-5.6 Sol through Kiro CLI, which is the same model
Codex itself runs, so the independent-reviewer rule held throughout: the reviewer
was never the model that wrote the code. P1 counts by round: 2, 3, 1, 2, 3, 1, 0, 0.

Two findings were rejected rather than fixed. A reported risk of committing
`.claude/settings.local.json` was wrong — a global gitignore excludes it, which
the reviewer could not see. A Chinese hardcoded attention title was flagged as
inconsistent; it matches what `_raise_review_attention` already does, so the
convention was the code's, not the finding's.

### Not done here

The durable "review owed" marker is not built. The window between a worker run
settling and its review being queued is not atomically closed, and a reviewer
reproduced a worker being accepted inside it through the API. What exists is the
client's bounded window plus a blocker recorded when dispatch fails outright. The
cost of closing it properly — a marker written in the settling transaction, six
terminal call sites, and its own startup reconciliation or a crash leaks it — is
recorded in the ADR rather than described as covered.

`portfolio/page.tsx`'s `bucketOf` has the rail's defect, reading an empty kind set
from the same capped page as "nothing waiting". It is a separate surface with its
own taxonomy and is left as its own item rather than half-fixed here.

## Both gates hold the order, and what it cost to make them

The maintainer decided on 2026-08-30 that neither case may start a new run: an
unanswered question gates a task the same way an approval or a blocker does, and
the window between a worker run settling and its review being queued is closed
rather than left to the client's timer.

`question` gates because a run started while the agent's question is unanswered
proceeds on an unanswered assumption, which makes asking pointless. The safety
argument is that such an item is always human-clearable — **not**, as the code
first said, that every question auto-settles with its run. Only bridge-origin
questions do; `POST /api/attention` writes no bridge row and nothing settles
those with any run. The absolute was wrong and is corrected wherever it was
stated, because the next person relaxing this rule will rely on the sentence that
is written down.

The review debt is derived, never written: a task owes a review when its most
recent terminal worker run succeeded and no gate item — the approval a review
raises, or the blocker every path that produced no review records — is scoped to
it. Nothing to leak on a crash, and startup reconciliation records the blocker
rather than dispatching a review, because `_review_prompt` reads the worker's
live worktree and worktrees are reused per project and adapter: a boot-time
review can review the wrong content and report a verdict on it.

### Five rounds, and what each defeated

The gates were defeated four times before they held.

A review that failed **before it started** had its `agora_role` and
`reviews_run_id` cleared by `finish()` writing an empty result, after which the
derived question read that row as the latest *worker* run, saw `failed`, and
concluded nothing was owed. A run's identity is now carried across every write
that carries a result, and one private writer owns that column.

The evidence releasing the debt was **forgeable**: `POST /api/attention` accepted
the control plane's own requester, so a caller could write the review-failure
blocker itself and then cancel it. That is the third time in this work a field
used as an authority marker turned out to be client-settable — after `agora_role`
in run metadata, and after the generic attention API was noticed and treated as a
documentation problem rather than a forgery path. The rule is that such a field is
refused at the boundary, never adjudicated at the point of use.

A **succeeded review run was counted as a gate that was never established** —
when both the approval and its fallback blocker failed to write, the accounting
concluded from the run's state alone. Review evidence and a human gate are
different facts and the ordering depends on them staying different.

An **incomplete reconciliation still resumed the queue**. A prior round had asked
whether the empty list returned by an unreadable reconcile is read as "nothing
owes a review"; it is not, and that check was clean. The defect was one step
further on — the continuation, not the value.

### A residual that was accepted twice, and was wrong

"A cancelled review still accounts for the review" was recorded as an accepted
residual in two separate rounds, on the reasoning that separating a human's cancel
from a sweep's would mean parsing a reason string. The premise was false: shutdown
cancels queued reviews automatically, so an ordinary restart admitted a worker run
onto work no review had ever seen. Two further parts of the reasoning were also
wrong — the fix never needed to distinguish the cancels, since what a cancelled
review shares with a failed one is that it produced no gate item; and the exit the
residual named, re-dispatching the review, is not an exit any human has, because
no endpoint dispatches a review run.

Accepting a residual is a claim, and it needs the same scrutiny as a fix: write
down the premise it rests on, then check whether the system itself has a path that
satisfies that premise.

### Unknowns

Three more places treated an unknown as an absence or a permission, which is now
the eleventh, twelfth and thirteenth in this work: a missing `task_version` skipped
the version check entirely (with a test asserting that admission), an unusable
`finished_at` was ranked as oldest and could hide the newest successful run behind
an older failed one, and the gate exclusion dropped every item carrying the judged
run's id rather than only the prompts that run provably raised.

### The guard that would not hold

A test asserting no raw write of `result_metadata` was revised five times. Two
string literals missed named parameters and split literals; an AST check truncated
its own SET clause at the first `WHERE`, so a nested subquery hid the assignment;
and the version that tied a statement to its enclosing function exempted the one
function that both builds a dynamic statement and calls the writer. It was retired
in favour of making the column unwritable from anywhere else: one private writer
owns it, and `_transition`'s dynamic update is built from a fixed allowlist that
cannot name it. Worth recording that the mutation is now caught loudly by every
test that performs a transition, but still not by a test that names the property.

Scanning source text to enforce an invariant fails in the direction of a green
light. Making the invariant unexpressible does not.

### The text was the least-reviewed thing here

Six rounds found the logic and then kept finding the sentences. The safety
argument for gating `question` was an absolute that was false. Correcting the
user-facing strings left the same premise in three comments, then a fourth. The
fix for a residual that named an exit nobody has wrote that same exit into the
message a human reads. The caveat added to be honest about a later review's
coverage overstated it — a later review does not read the worktree, it reads a
prompt bounded at 1,500 characters per file and 12,000 overall.

The cause is worth recording plainly: the analysis and the human-facing text are
different artifacts produced by the same change, and only one of them was under
review. Rounds asked whether the logic was right; nobody was asked to read the
sentences as claims. A caveat is a claim. A docstring a maintainer would follow is
an instruction. A refusal message names an action, and that action either exists
or it does not.

`test_review_owed_text_claims_only_what_holds_for_every_cause` is the check every
prior round lacked: it builds the cause that decides what the shared strings may
say and asserts the strings do not overclaim. Verified by mutation — restoring any
of the three earlier wordings fails it.

One instance of this class is recorded and not fixed: `_raise_review_attention`'s
approval title says the review completed, on the strength of exit code zero, which
AGENTS.md says is not semantic success. It is outside this change and is left as
the next one to correct.

Backend 882 collected, 882 passed (861 at the start of this increment). Frontend
66 collected, 66 passed; tsc, eslint, build and the schema check clean.

Review: six rounds, both gates. Codex ran the first two and hit its usage limit
twice more, after which the independent gate was GPT-5.6 Sol through Kiro CLI —
the same model Codex runs, so the reviewer was never the model that wrote the
code. P1 counts by round: 1, 2, 2, 1, 2, 1.

Twice the two gates read the same code and only one found the defect, because they
asked different questions. Whether the empty list from an unreadable reconcile is
misread: clean, and correct — the defect was the continuation after it. Whether
N3's break bounds one blocker per invocation: clean, and correct — the defect was
the lifetime bound. Two reviewers are worth having for the questions they differ
on, not for the models they are.

Next safe action: close W0(b) by demonstrating the loop end to end — dispatch a run
through the live Continue path, let it settle, advance the clock, and assert the
recap names yesterday's step. The recap already reports a run's outcome from the
run record rather than from events, so what this needs is the demonstration, and
whatever gap the demonstration exposes.

## W1 — projects in the database, and a workspace that says what is happening

Four pieces, committed as 376eb9b and cf4994b. The plan called W1 the largest
slice; reading the code first said otherwise. Run and task persistence already
existed, and the three-column shell was already there. What was missing was
projects, and the interactions the mockup review had decided and nobody had built.

Projects lived in a YAML file while tasks and runs lived in SQLite, so a run could
be gated, dispatched and settled against a project the database could not see. The
store is now authoritative and the YAML is an import source that is never rewritten
— it is the maintainer's own file, and it stays on disk so a bad import can be
compared against it. Every finding on that import was one family of mistake:

- Conflict detection and replacement were two statements, so a change made through
  Agora between them was overwritten. They are one decision under a write lock now.
- `yaml.safe_load` keeps the last of a repeated key, so a file with two `projects:`
  blocks imported only the second and presented a shorter list as authoritative —
  data loss reported as success. A strict loader rejects the file instead.
- `Path.exists()` answers false for a permission failure exactly as for a missing
  file, so an unreadable source read as absent and seeded defaults a caller could
  not distinguish from a real list.
- "The stored row matches its snapshot, so Agora did not change it" was false —
  nothing marked an Agora write, so changing a value and changing it back defeated
  the comparison. Rather than narrow the promise, the mechanism now earns it: every
  write bumps a counter, and anything unprovable reads as touched.

The right column stopped being four tabs and became the answer to "what is
happening": a live run's output, or a settled run's deliverables and the gate. Six
states, because the ways of not knowing are not one thing. Two claims had to be
unlearned — that declining to mirror a backend rule licenses inventing one, and
that the newest settled run is the one that produced something (an automatic
review settles after its worker and its snapshot is empty).

Runs nest by work unit. Retries do not nest, because nothing records a retry
relation and inventing one would be a guess. A run whose parent chain does not end
at a top-level worker is rendered with the reason it could not be placed, and a
test pins that the rendered set equals the given set — including for a cycle.

The progress bar reads the project's own snapshot and counts its markers. Only the
counted variant carries a percentage. It also takes the display off the frozen
control plane's verified percentage, which any historical success could set, so
after a requirements change old work made new criteria read as already produced.

Two absences that were not: an unstattable snapshot path read as "no snapshot",
and this repository's own record — a thousand checkbox lines — read as a snapshot
tracking nothing. Whether checkboxes should be counted is left open rather than
settled by a mapping nobody asked for.

`Path.exists()` was fixed twice in one night in two modules before it became a
shared probe returning bool or None, so "I could not tell" is expressible in the
type instead of lost at the call site. Three ACL tests skip on Windows: the deny
that creates the condition cannot be reversed without elevation, and a test that
creates state it cannot remove does not belong in a suite. The invariant is pinned
by raising each error directly at three levels instead.

Backend 958 collected, 951 passed, 7 skipped. Frontend 138 collected, 138 passed.

Recorded and not fixed: tests entering the lifespan write to the live database,
and which database that is depends on the working directory. W1 deepened both —
project state now goes through the same path. That is the next thing to fix, and
it is the reason a full-suite run should not be trusted while a dispatch is still
writing files: one gate here ran against a half-written tree and reported two
failures that did not exist.

Next safe action: the test/live-database isolation, then W2's environment
abstraction, for which projects now carry the note that root is a property of a
project in an environment and never its identity.

## 2026-09-01 → 09-02 — checkbox counting, test isolation, two recorded gaps closed

The progress bar counts Markdown checkboxes. The objection that stalled it — a
checkbox cannot express "in progress" — is true and does not cost the percentage:
an item in flight is written as an unchecked box, so done plus not-done still
partitions the whole checklist and `done / total` is exact. What the convention
cannot support is the split of not-done, so `in_progress` is reported as unknown
rather than zero, and the display carries a variant with no in-progress field at
all. This repository's own record — 1057 boxes — now reads 1050 done, 99%, where
it previously rendered nothing.

The block structure comes from a CommonMark parser instead of a line scan. That was
not the first attempt. Hand-rolling it produced a run of miscounts where each fix
uncovered the next, and the worst was self-inflicted: fence tracking added to stop a
documented example from being counted let a stray indented `~~~` open a fence that
never closed, and a file full of items read as tracking nothing at all — this
module's own worst failure, built by the code meant to prevent it. Eleven findings
disappeared at once when the parser took over, none of them needing a special case.
Inline parsing is disabled and both counters share one parse: 273ms to 53ms on the
real file, by removing work rather than caching a result.

What is deliberately not done there: a checkbox is never resolved against the
document's link reference definitions. One `[x]: url` line makes markdown-it render
every `- [x]` in the file as a link, so that rule would silently drop the whole
checklist. GFM takes the task marker first, and so does this. For any other mark the
references do settle a real ambiguity and are consulted.

The test suite no longer reaches live state. Both kinds of it. The database was the
known one: `make test` and `make dev` both start from `backend/`, so a relative
`data_dir` resolved both to the same file, and `lifespan` calls its store factories
directly rather than through `Depends`, so no `dependency_overrides` could reach
them. Every `TestClient` in the suite ran startup recovery — which resumes the
persisted queue, i.e. dispatches runs — against a developer's own database. Nothing
had gone wrong yet only because that database happened to hold no queued run.

The second kind was found by review after the first was already called done:
constructing a `ProjectRegistry` creates `.agora/research`, `.agora/decisions`,
`.agora/specs` and every registered workspace under each project's root. One
`TestClient` was measured issuing twelve such directory creations into this
developer's repository and into a second real project beside it — no-ops only while
those directories happen to exist. Delete one and the suite puts it back.

Both are redirected per test, and a guard fails the accidental reach: it intercepts
`sqlite3.connect` and `Path.mkdir` / `os.mkdir` / `os.makedirs` as module
attributes, which covers every store in `agora/`, and resolves links before
deciding. The guard's own history is the argument
for its shape: its first version watched `sqlite3.connect` alone, which is exactly
why it could not see those twelve directories, and why it read as proof that nothing
was left. A guard narrower than the claim it supports is worse than no guard. Five
variants of that same mistake were made and corrected in one night — a string-prefix
path test that admitted a sibling directory, a lexical path test a junction walked
through, a type test that accepted an empty string as a usable path, and a YAML load
that erased duplicate keys the production reader rejects. The last was the second
time that afternoon the same PyYAML behaviour bit, in a second file.

Not guarded: file writes. Measured rather than assumed — the startup path was
instrumented and writes nothing outside the temp tree — so it is a stated boundary.
Three further ways past the guard are known, need a test to go out of its way, and
are carried as skipped tests that name themselves in every run's summary rather than
as a paragraph. See CR-8 in the workspace record.

Two gaps recorded during W1 are closed. Recap items now name their run: a worker and
its automatic review finish on the same day by design, and two runs of one adapter
with the same outcome were previously indistinguishable in the payload, so anything
binding an item to a run had to cross-check adapter, kind and file count and hope
the day held nothing similar. The W0(b) acceptance test asserted exactly that
cross-check, with a note that the three outcomes differed enough to tell apart —
true of those three, not a property of the payload. It now asserts the run id.

And the Portfolio board no longer reads a truncated page as an empty one.
`listOpenAttention` has always returned `truncated`; the page kept only `items`, so a
task whose question fell past the cap arrived with no kinds and was filed by
lifecycle state alone — a task waiting for an answer shown under Running, in the
product's main triage surface. The rule is the rail's, because it is the same rule:
presence is provable from any page, absence is not. The nine-column mapping had to
be decided here rather than borrowed, so the columns that assert "nothing is waiting"
are named explicitly and only they downgrade; a needs-approval or blocked claim that
rests on lifecycle state does not depend on the read and is kept. Uncertain tasks go
to a column of their own, above the settled work, never dropped from the board. The
same component's "Nothing needs you right now" had the same defect and now requires a
complete read — and, after the rounds below, a *complete* task-inventory read as well:
a task read that succeeds with a full page has not established what the board holds.

Backend 996 collected, 986 passed, 10 skipped (21m31s). Frontend 155 collected, 155
passed; tsc, eslint and build (12 pages) clean. The collected counts belong here per
the gate item in AGENTS.md and this entry had dropped them: "986 passed" cannot tell
a suite that shrank from one that ran fewer tests, which is the mistake this file
already recorded once. Three of the ten skips are the guard's known boundaries
(CR-8); the other seven are platform capabilities this account lacks (symlinks,
self-denied traversal). Two of the three print their CR-8 reason on any platform. The
third is `dir_fd`, which is POSIX-only, so on Windows `skipif` answers first and the
CR-8 reason would never be seen — its skip reason now names CR-8 as well, because a
boundary that goes unreported on the machine this suite mostly runs on is a debt with
no artifact.

Codex reviewed the three uncommitted slices together and returned three P2 findings,
all reproduced and all fixed:

1. The board settled its four reads with one `Promise.all` under one `catch`, so a
   failing tasks, projects or runs read was recorded as an *unreadable attention
   read*: every calm task moved to "Could not tell" and the board stated "Open items
   could not be read" about a read that had succeeded. The correction is not new
   logic — `rail-group.ts` already implements "a read speaks for itself"
   (`settleRead` + `railReadReport`) and is tested for exactly this case, and the
   board now uses it instead of its own copy, which is deleted. A read that fails is
   still reported, in the banner, as its own failure. **The same shape as the rest of
   this stretch: the claim was wider than what was checked** — the module's docstring
   said "from the read's own result and nothing else" while its only caller handed it
   a combined failure. Mutation: derive the reach from any rejection and two tests
   fail (the board's and the rail's).
   A failed attention read now also clears the items it could not re-read; keeping
   the previous page alongside `unreadable` would let a stale ask keep asserting
   itself, which is the same error pointing the other way.
2. The capped notice advised narrowing the project filter to see the items past the
   cap. `listOpenAttention` takes no arguments: the filter narrows the tasks already
   loaded on the client and does not rescope or page the read, so the advice changes
   nothing while reading as though it does. The sentence now states the limit and
   stops. A project-scoped or paged attention read is a real improvement and is not
   this fix.
3. This checkpoint recorded passed/skipped without the collected counts (fixed
   above).

A second round found one more, also reproduced and fixed: the backend put `run_id`
into the recap response while the shared client type `RecapItem` did not declare it,
so no TypeScript consumer could reach the field the slice exists to provide. Declared
`run_id?: string | null`, which is the payload as it actually is — the key is absent
on items no run produced, and null on a run-derived item whose assembler supplied no
id. The acceptance test already asserts the id over HTTP, so the type now matches
something that is pinned rather than something that is intended.

Fixing (1) removed a try/catch-around-await that had been making the React compiler
bail out of the Portfolio component — and with it, that component's whole lint rule
set. Once the component was analysed, exactly one violation surfaced, pre-existing:
the dashboard effect calls a callback that clears state synchronously. Deferred by
one tick, the way the load effect one screen above already was.

A third round, run on 09-04 through kiro-cli's gpt-5.6-sol because the codex channel
was out of credit until 09-07 (same model family, different channel), found two more
in the work that the first two rounds had already passed:

4. **The reassurance survived a failed task read.** With the four reads settled
   independently, a failing `listTasks` leaves the board holding no tasks, so every
   group is empty — and "Nothing needs you right now" printed directly beneath the
   board's own load-failure banner, about an inventory that had not been read. The
   task list could hold `review`, `blocked` and `failed` tasks, each of which reaches
   an action column without consulting the attention page at all. This one was
   **introduced by the fix above**: the shared `catch` used to mark attention
   unreadable on any failure, which suppressed the line by accident. Removing the
   accident is right; leaving the condition unstated is what let the claim escape.
   The rule is now named (`portfolioAllClearSayable`) and takes both reads, and
   `railReadReport`'s `staleOrigins` — previously dropped here with a comment calling
   it rail-only — is what answers for the task read. `projects` and `runs` are
   deliberately not in it: they decorate cards, and their failure costs a stale
   detail, not the board's knowledge of what exists. Mutation: drop the task-read
   term and the new test fails; restored, 157/157.

5. **The guard's stated scope was wider than the guard.** The conftest docstring said
   it fails "any test" that opens a database or creates a directory outside the temp
   tree, while `tests/test_guard_boundaries.py` — written in the same change — lists
   three ways past it. A guard described as absolute is the thing that stops anyone
   looking, which is the failure mode this whole stretch is about. The claim is now
   an explicit two-part list: what is intercepted, and what is not (file writes,
   `uri=True`, `dir_fd=`, a cached ancestor swapped for a junction, a `connect` bound
   before the fixture, and subprocesses — none of which appears in `agora/` today).
   Behaviour unchanged; only the claim was wrong.

The same round also raised a concurrent-refresh hazard on the same board, accepted
and fixed: `load()` is called by the mount effect, the refresh button and a card's
conflict retry, and the four settled reads always resolve, so an older call always
reached the state writes and could publish its answer over a newer one — a stale
"could not be read" replacing a read that had since succeeded. A monotonic token now
lets only the newest call write. It is pre-existing (the old `Promise.all` raced the
same way) and it is **not covered by a test**: the frontend suite tests library rules,
not components, and there is no DOM harness here to overlap two refreshes in.

Gate after those fixes: backend 996 collected, 986 passed, 10 skipped (12m20s — the
same counts as before the round, which is the point: two of the three fixes were a
claim and a comment, and the third is frontend-only). Frontend 157 collected, 157
passed (155 plus the two that pin the reassurance); tsc, eslint and build (12 pages)
clean.

A fourth round, on those fixes, returned four more. Two were the fixes themselves
still being wider than what they checked:

6. **A fulfilled task read is not a complete inventory.** The new rule required the
   task read to have *succeeded*; `listTasks` asks for 500 and returns a bare array —
   no total, no `truncated` — so a page filled to the cap is indistinguishable from
   "there is more", and with 501 tasks the 501st can be the `blocked` one. The reach
   now comes from the read's own result the way the attention page's does
   (`taskInventoryReachOf`): rejected is unreadable, a full page is capped, anything
   short of the cap is complete. Pessimistic by exactly one case — a list of exactly
   500 that is genuinely complete loses a reassurance it was entitled to — and never
   optimistic. `TASK_PAGE_LIMIT` is exported from the client so the reading side
   compares against the number it asked for rather than a literal of its own.
7. **The guard's new scope statement was still wrong in two places.** It said every
   store calls `sqlite3.connect` itself (several arrive through another store's
   `_connect` — still the patched attribute, so still covered, but not what the
   sentence said), and it put file writes and subprocesses on a list that ended
   "none of those appears in `agora/` today", which is false: both appear. Writes
   and subprocesses are a *boundary*, and the list is now split so it says that.
8. **The deferred URI test would have passed for the wrong reason.** `pytest -rs`
   promises "delete this skip to pick it up", but run from `backend/` the guard's
   lexical misreading of `file:D:/…` lands outside the temp tree, so the guard raises
   for the wrong reason and the test goes green with the bypass wide open — a
   deferred debt whose artifact silently reclaims itself. It now `chdir`s into
   `tmp_path`, where the misreading lands *inside* the allowed tree, so only real URI
   parsing can make it pass. Measured both ways: without the skip and without the
   chdir it passes; without the skip and with it, it fails as it should.

The fourth finding is two false absences this change set did not introduce and does
not close — the group badges report an exact count from a capped or failed inventory,
and `TaskCard` renders "No runs yet" when the runs read failed or was capped. They are
CR-10 and CR-11 in the workspace record, open, because "the same defect elsewhere in
the file I was editing" is where scope goes to die; the reassurance was fixed because
this change set is what made it reachable.

Gate after the fourth round: frontend 158 collected, 158 passed; backend unchanged at
996 collected, 986 passed, 10 skipped (12m09s).

A fifth round, on those fixes, returned two — both of them the same lesson in the two
places it had not yet been applied:

9. **Suppressing a claim is not the same as explaining it.** With 500 tasks on the
   board and every one of them calm, the reassurance was correctly withheld and
   *nothing* was rendered in its place: an empty "Needs you" column, no notice, no
   banner. The uncertainty was kept inside the component and dropped at the display
   boundary — which is the bug this whole stretch is about, one layer out. The board
   now carries a task-inventory notice beside the attention one, in both locales, and
   a capped read is disclosed nowhere else, so the test that pins every outcome to a
   sentence covers it too (mutation: delete the English capped string and it fails).
   `pending` joined the reach type for the same reason it exists on the attention
   side: before the first read, "could not be read" is its own falsehood.
10. **A deferred debt listed one item it could never hand back.** The scope statement
   named four ways past the guard and said the file of skipped tests carries them;
   the file has three. The fourth — a `from sqlite3 import connect` bound before the
   fixture runs — is not reclaimable at all: patching a module attribute cannot reach
   a reference that already exists. It is a limit of the technique, and it now says
   so instead of appearing on a list of debts someone could pick up.

A sixth round read the sentences the fifth one added, and all four of its findings
were in that copy — the fix had moved the defect into the words:

11. **The capped notice asserted a fact the board does not have.** "There are more
    tasks than this page reads" is exactly what a full page cannot tell you: the reach
    is capped *because* 500 is indistinguishable from complete. And it spoke of "an
    empty column" while rendering above a column that may hold cards. Both locales now
    state the read's limit and stop.
12. **The unreadable notice claimed missing tasks.** A refresh that fails keeps the
    previous snapshot, which may have been complete; what is known is that it could
    not be re-read. The sentence now says that instead.
13. **`**不**` rendered as `**不**`.** The board's `Notice` puts its children in a div
    as plain text, so the emphasis in the zh attention line — on the word carrying the
    sentence's meaning — was literal asterisks on screen. Removed, and pinned: no
    notice string in either locale may write bold, code or a link as markdown
    (mutation: put it back and the i18n test fails). Three forms, named — not "no
    markdown", which the assertion does not check and could not sensibly: underscores
    and lone asterisks occur in ordinary prose, and a check that fires on those gets
    deleted rather than obeyed.
14. The boundary file's own opening still said the guard "fails any test". Qualified,
    the same way conftest's was.

Gate after the sixth round: frontend 158 collected, 158 passed; tsc, eslint and build
(12 pages) clean; backend 996 collected, 986 passed, 10 skipped (12m08s).

A seventh round found five more, every one of them a sentence claiming more than the
code knows — the same defect the code fixes, in the prose about the fix:

15. **The attention notices asserted counts too.** "There are more open items than
    this page reads" is the same unavailable fact as the task one — `truncated` is set
    when the page comes back *full*, which a complete list of exactly that size also
    does — and "some tasks could not be sorted" says tasks exist that may not. All
    three sentences, in both locales, now state what the read did and stop.
16. **The `pending` inventory notice could never render.** It was gated on `!loading`,
    and `pending` is only true while the first load is in flight, so the string and
    the test pinning it were both dead. The notice now renders the way the attention
    one beside it always did — ungated — and a refresh keeps showing the previous
    reach, which is still what the board is displaying.
17. **"Outside the pytest temp tree" was not the guard's boundary.** The whole
    platform temp directory is allowed, deliberately, so `tempfile.gettempdir()/x.db`
    passes — and every message and docstring said "the pytest temp tree" anyway. They
    now say "the allowed temp roots", including the assertion a failing test reads.
18. The `dir_fd` boundary's skip reason (above).
19. The markdown assertion claimed more than it checked (above).

Gate after the seventh round: frontend 158 collected, 158 passed; tsc, eslint and
build (12 pages) clean; backend 996 collected, 986 passed, 10 skipped (13m05s).

An eighth round, asked for a commit/no-commit verdict, said no and gave four:

20. **Two `pending` sentences described a read, and `pending` is not about a read.**
    The page settles its four reads and publishes them together, so "Still reading the
    task list" kept saying it after the task read had returned, held up by a sibling.
    Publishing each read as it lands is a bigger change than this is worth; the state
    is what it is, and the words now match it — "Still loading the board" — with the
    type and the comments saying the same.
21. **The board's section order existed twice and had already drifted.** `GROUPS` in
    the component decided what renders; `PORTFOLIO_ORDER` in the library was what the
    tests asserted, and they disagreed (the constant put shaping last, the component
    renders it before the completed section). The test was passing about an order
    nothing rendered — a check that reads as coverage and is not. The sections now
    live in the library as `PORTFOLIO_GROUPS`, the page renders from them, and the
    flat order is derived from that, so it cannot disagree again. Two assertions
    added: every bucket appears exactly once, and only the settled section collapses.
22. The gate line above recorded "backend re-run" without the counts AGENTS.md asks
    for — the third time this entry has had to be corrected for that, and the reason
    the rule exists.
23. **"Not intercepted" was the wrong name for three of the boundaries.** `uri=True`,
    `dir_fd=` and the junction swap all *do* reach the patched wrapper; it then decides
    about the wrong path and allows the call. Same outcome, different cause, and the
    cause is what a reader needs to fix it.

Gate after the eighth round: frontend 158 collected, 158 passed; tsc, eslint and build
(12 pages) clean; backend 996 collected, 986 passed, 10 skipped (14m52s).

A ninth round found one, in the assertion the eighth round had just added:

24. **"Exactly once" could not fail on its own.** The line above it compared the
    flattened order to the expected buckets as sorted *arrays*, and array equality
    already counts multiplicity — so a duplicated bucket failed there, and the new
    length assertion had no mutation of its own. The set is now compared as a set, so
    one line answers "is any bucket missing" and the other answers "is any bucket
    twice". Both verified: duplicating `running` into the unsorted section fails the
    length line; dropping `closed` fails the set line; restored, 158/158. That this
    was found in a test written to fix *another* test that could not fail is the whole
    lesson of this stretch in one place.

Gate after the ninth round: frontend 158 collected, 158 passed; tsc, eslint and build
(12 pages) clean; backend 996 collected, 986 passed, 10 skipped (14m52s).

A tenth round, asked to check the intended commit split as well, found four:

25. **One of the four commits would not have compiled on its own.** The split put all
    of `control-plane.ts` in the recap commit, and the portfolio commit imports
    `TASK_PAGE_LIMIT` from it — so applying the first two commits gives TS2305. The
    file's two hunks go to their own commits instead. Worth the trouble: a commit that
    does not build is a commit nobody can bisect through.
26. **`CALM_STATES` said "every lifecycle state" and was missing two.** `requirements`
    and `design` reach `shaping` through the same default as the four that were
    listed, so a regression special-casing either would have passed. Added.
27. **Two comments still described `capped` as "there was more than I read"** — the
    exact claim the notices had just been corrected for making, left behind in the
    prose that explains them. Both now say the page reached its read limit.
28. **A comment said the reassurance needs the task read to have "succeeded"**, which
    is what the code stopped requiring three rounds ago: succeeding is not enough,
    because a page filled to the cap succeeds.

Gate after the tenth round: frontend 158 collected, 158 passed; tsc, eslint and build
(12 pages) clean; backend 996 collected, 986 passed, 10 skipped (14m52s).

Next safe action: W2's environment abstraction. It owns the half of the database
problem left open here — which file a process uses still depends on the directory it
was started from, which is a product defect and not only a test one, and moving the
resolved path switches a developer onto a different existing database unless the
change refuses to start rather than switching. The embeddings store is a second
database resolved the same way, and W2 should take both.

## W2 — the environment seam, and refusing to guess which database is ours

Two decisions were written down before any code, as `tasks/agora/adr/0002` and
`adr/0003` in the workspace that drives this project.

**The database.** The control-plane database's relative paths are now anchored to the
directory holding `config.yaml`, not to the working directory (that resolver's list,
not every path in the file — several other configured paths answer to other roots). `make dev` from the repository
root and from `backend/` were two different control planes — two project lists, two
task lists, nothing on screen saying so — and `resolve_control_plane_db_path` carried
a docstring section titled "Consequences this change deepened (accepted, not fixed
here)" that described this precisely and protected nothing. That section is deleted;
this is what replaces it.

Changing the rule under an existing install can move it onto its *other* database, so
it is paired with `assert_no_database_ambiguity`, which refuses to start and prints
both absolute paths. Two things about that check are worth recording:

- **The condition is wider than the one first agreed.** The plan said "refuse when the
  anchored path has no database and the old one does". On the machine it was written
  for, that check would never have fired: `data/agora.db` (1,343,488 B, Aug 29) and
  `backend/data/agora.db` (1,208,320 B, Aug 31) *both* exist and differ. The condition
  is now "the two rules name different files and the old one exists", regardless of the
  new one. A guard narrower than its own claim is worse than none, because it reads as
  proof.
- **Both doors are guarded.** The API's lifespan and `agora task`'s `build_service`
  open the same database. Guarding only the first would have left the CLI switching
  silently while the change claimed it refuses to.

An unreadable old location raises too. "Could not find out" is not "not there", and it
is the one answer that must not be read as permission to proceed.

**The environment.** `agora/environments.py` defines `Environment` with exactly two
capabilities — `run(argv, cwd, timeout)` for short bounded commands, `probe(path)` for
`agora.fsprobe` — plus `LocalEnvironment`. `CommandResult` keeps `unstartable` apart
from a non-zero exit, the same distinction `PathProbe` keeps for paths: a missing tool
and a tool that said no call for opposite responses.

It deliberately does **not** cover launching agent processes. The dispatcher needs a
pid, two live streams and a kill; that is a process handle, not a result, and an
interface for it designed now — with no remote implementation to check it against —
would be a guess. W3 adds it. This sentence belongs beside every claim that "W2 added
an environment abstraction" — and the sentence that used to stand here, "the seam covers
three of the four places that run commands", was wrong twice over. See the round-6 and
round-8 entries below and `environments.py` for the counted inventory; this paragraph
deliberately no longer carries its own copy of it.

Call sites moved onto it: `workspaces/provisioner.py` (both git helpers, its
`Path.exists()`/`is_dir()` checks, and the `shutil.which` git pre-check that was asking
this machine's PATH about a possibly-remote host), `execution/deliverables.py` (three
`subprocess.run` sites) and `execution/handoff.py` (one). Behaviour changes that came
with it:

- An unreadable workspace is `ERROR` with the reason, not `MISSING`. `MISSING` tells
  the operator to provision a worktree that may be sitting right there; the fix is a
  permission.
- A workspace that stats but cannot be listed is `ERROR` too — the second half of the
  same question, which the emptiness check would have read as "empty".
- `handoff` no longer records a missing git as "workspace is not a provisioned
  worktree". That note is read by a person, and it was a verdict about the wrong thing.
- `doctor.py` no longer answers "Create workspace directory: X" for a directory that is
  already there and merely unreadable.

**One process note.** The full run caught a call site the targeted runs did not:
`tests/test_project_provisioning.py` still passed a `str` where an `Environment` now
goes. The lesson this project already paid for is "after fixing a read, search the
other call sites of the same function name" — it was searched, but only under
`agora/`, not under `tests/`. Narrowing the search is how the lesson stops working.

Gate: backend 1021 collected, 1011 passed, 10 skipped (12m05s) — 996/986/10 before,
so 25 added and the skipped count aligned. Frontend untouched, 158/158 not re-run.
Mutation-verified: narrowing the ambiguity check back to "only when the anchored path
is empty" fails `test_it_still_refuses_when_the_anchored_path_also_has_a_database`;
removing the `probe.unknown` branch in `_inspect` fails two workspace tests. Restored,
all green.

Next safe action: independent review, then commit. The API and CLI will refuse to
start on this machine until the operator says which of the two databases is theirs —
that is the check working, not a regression.

### Four review rounds, 27 findings, all valid

Codex was rate-limited until 2026-09-07, so review ran through kiro-cli on the same
model (`gpt-5.6-sol`) — a different channel, not a different reviewer. Twenty-seven
findings across four rounds, **every one confirmed and fixed, none rejected**. What the
rounds were actually for is worth recording, because it was not "polish":

**Round 1 (9).** The anchoring change had introduced a *new* split: rule 3 took a base
from its caller, `ProjectRegistry` passed one and the task store did not, and while both
were the repository root that was invisible. Anchoring pulled them apart, so a project
list and a task list in one process would have gone to two files — the exact invariant
the change was written to protect. Also: `hand_off` recorded "workspace is not a
provisioned worktree" for a *missing git*, permanently, on a task moving to DONE; the
`shutil.which` git pre-check was asking this machine's PATH about a possibly-remote
host; and a comment claimed W3 could "swap the implementation rather than this file"
while every filesystem mutation and the `_assert_allowed` confinement check remained
local — the confinement one matters, because it would compare remote paths against this
host's symlinks and roots.

**Round 2 (8).** Mostly damage from round 1's own fixes. `ready_worktree_check`'s new
`(ready, reason)` tuple carried a reason only from its *first* git call. `get_config`
stored a relative config path, so `config_dir()`'s `.parent` was `.` and resolved
against whatever directory it was asked from — reintroducing, one level up, the
cwd-dependence this whole change removes.

**Round 3 (5), and the one worth remembering.** `_commit_workspace` had been given a
careful argument that the *preceding* successful `add` and `status` licensed calling a
non-zero `rev-parse HEAD` an unborn branch. They do not: they establish that git could
read the repository at those instants, and state changes between commands. The
replacement — a positive `symbolic-ref` probe, with git's exit codes measured directly —
was wrong in the same place: it succeeds whenever HEAD names a branch, including one
whose ref is corrupt, so it would have reported "no commits" for a repository that has
them. The resolution was to **stop claiming it**. Any HEAD read without a sha now says
it could not be read. An Agora worktree always has a HEAD (`ensure_source_repo` seeds
one), so the branch that claim served is unreachable anyway.

**Round 4 (5).** The guard's new `kind != "file"` shortcut read a symlink whose *target*
could not be stat'd as "not a database" — `probe_path` calls that `other`, not `unknown`,
because something is definitely at the path. A link pointing at a real database with a
denied target would have let startup proceed on the other file: the silent switch again,
one representation away from where it was being looked for. The line now follows
fsprobe's own rule applied to the target — only `FileNotFoundError` settles it as
absence — and is pinned from both sides, including a test that fails if the check is
made too wide and bricks startup on a dangling link.

Two dead compatibility wrappers (`_rev_parse`, `_is_repo_root`), left by threading
reasons through every call, were removed. A sibling docstring in `projects/registry.py`
still described the cwd-dependent behaviour this change deletes; corrected.

**Measured, not assumed.** `ready_worktree_check` makes **eight** git calls on the
successful path — counted with an instrumented Environment, not inferred — so the
parametrized regression covers `after_call` 0..7, making each of calls 1..8 in turn the
one where git stops answering. An earlier version started at 1, so the first call was
never the failing one while the test's name said "at any point".

**One observation, not fixed.** Calls 4 and 6 are the identical command
(`git -C <project> rev-parse --path-format=absolute --git-common-dir`): `_repo_root_check`
reads the project's common dir and `ready_worktree_check` reads it again to compare. The
duplication predates this change (the old `_is_repo_root` did the same), and this gate
runs before every agent launch, so one fewer subprocess is worth having — but removing it
shifts every index in the parametrized test just verified above, so it is left for a
change that can re-verify them.

Gate after round 4's fixes: backend **1054 collected, 1044 passed, 10 skipped**
(15m22s). Baseline before this stretch was 996/986/10, so 58 tests were added and the
skipped count held at 10 — two runs with the same passed count and different skipped
counts are not the same suite, which is why it is recorded. Frontend untouched, 158/158
not re-run. Blocker at that point: round 4 had ended NOT READY on the symlink finding
above; it is fixed and the round-5 result is below.

**Round 5 (6 findings, P1 empty for the first time).** The reviewer also produced an
exhaustive `(kind, error)` table for `probe_path` and walked each pair to the guard
branch it lands in — the audit that should have been written alongside the check.

Two of the six are the same defect as round 1's, still standing in the prose: the
`config.yaml` header claimed *every* relative path in the file anchors to the file's
directory. It does not — `projects.registry_path`, project roots and workspace paths
resolve against `ProjectRegistry.project_root`. Round 1 had reported this about
`vectors.db`; the store was anchored and the sentence was left universal, so the fix
made the claim *more* wrong, not less. Narrowed in three places (`config.yaml`,
`resolve_control_plane_db_path`'s docstring, ADR 0002) with the excluded paths named.
`research.artifact_dir` is the other cwd-relative path and stays that way: like
`VectorStore`, `ResearchOrchestrator` has no production constructor — measured, not
assumed.

The symlink regression tests from round 4 could not fail on the thing they were about:
their fake probe ignored `follow_symlinks`, so switching production to `False` — which
makes every symlink probe as `other` with no error and slip past the guard — left them
green. The fake now asserts the flag; mutating production to stop following links fails
both.

`tests/conftest.py`'s own docstring still explained, at length, that the
working-directory dependence "is *not* fixed here" and was deferred to the environment
work. This *is* that work. Corrected, along with the guard's assertion message.

`fsprobe`'s "pure apart from the single stat" became false the moment round 4 leaned on
the followed-symlink path: it is one `lstat` plus a `stat` of the target. Corrected
there and in the two places that repeated it (`environments.py`, ADR 0003).

Gate after round 5's fixes: backend **1054 collected, 1044 passed, 10 skipped**
(15m46s) — identical to round 4's, which is the expected result: round 5 changed prose
and one assertion inside an existing test helper, and added no tests. Round 5 ended
NOT READY.

**Round 6 (6 findings, P1 empty again).** Two of them are the same sentence failing a
third time. The "every relative path is anchored" claim had been narrowed once in round
5; it was still wrong, because `projects.registry_path` has **two** consumers with
**two** anchors — this resolver places a database beside it, and the registry opens the
file itself relative to `ProjectRegistry.project_root`. Assigning the key wholesale to
either list is false either way. The wording is now a three-row table (anchored to
config.yaml's directory / anchored to the registry's root / relative to the process)
with `registry_path` deliberately appearing twice, and `research.artifact_dir` and
`research.workspaces` named in the third row where earlier versions omitted them.
`config_dir`'s own docstring still carried the universal claim round 5 was supposed to
remove — the same file, four lines away — along with `api/app.py` and `conftest.py`.

**The seam's own scope statement was wrong, and it was the one written to prevent this.**
It said the abstraction "covers three of the four places that run commands". Counted:
every git call site outside the frozen `orchestration/` tree is on the seam (three), and
five further modules spawn processes for other reasons. The fourth git call site,
`orchestration/protocol_context.resolve_git_revision`, is **not** on it — it runs
`git -C <project root>` with a bare `subprocess.run` and is reached from
`orchestration/service.py`. The search that produced "three of four" had excluded
`orchestration/` because it is frozen, but the question was "what runs git", not "what
runs git that I am allowed to change". Filtering a search by a category irrelevant to the
question drops a whole tree from the answer.

That one is a real hazard for W3 — a remote Environment would have provisioning talk to
the far side while this call runs local git against a path that is not there — and W2
deliberately does not modify the frozen tree. So it is a `defer`, and per adr/0015 it
gets something that reappears on its own rather than a paragraph: **CR-14, status OPEN**
(injected into every session), plus a **skipped test with its reason**,
`test_every_git_call_site_goes_through_the_environment`, which `pytest -rs` prints on
every run. Its assertion body is real, not a placeholder: `resolve_git_revision`'s source
contains `subprocess.run` today, so deleting the skip fails immediately. **This is why
the skipped count moves from 10 to 11** — a deliberate, recorded change, not drift.

Also: `Environment.run`'s `timeout` lost its `None` default. "Short, bounded" was
documented and not enforced, so an unbounded wait violated the contract without
violating the interface. Every caller already passed one. And `fsprobe.py`, changed in
round 5, had been left out of the commit assignment entirely; it belongs with A.

Next safe action: gate, then round 7. The change is not committed. The API and
`agora task` now start: the database ambiguity this work introduced a guard for was
resolved by moving `backend/data/agora.db` to `data/agora.db` (7 tasks, 2 projects, the
live one) and renaming the older pre-W1a file aside — the operator chose, which is what
the guard exists to make them do.

Gate after round 6's fixes: backend **1055 collected, 1044 passed, 11 skipped**
(16m00s). The +1 skip is CR-14's, recorded above before the run rather than explained
after it.

**Round 7 (5 findings, P1 and P3 both empty).** Three of the five are the same
paragraph failing for the fourth time, which is the finding.

The path taxonomy went: "every relative path in this file is anchored here" → two rules
→ three rules → **four**. Each version was checked against part of the code and stated
about all of it. What was still wrong after round 6: workspace paths anchor to *each
project's own resolved root*, not to the registry root (`importer._parse_project`
resolves the root first, then the workspaces against it), and `research.artifact_dir` /
`research.workspaces` are not process-relative at all under the shipped configuration —
`ResearchOrchestrator` **overwrites** both from the current project whenever a `projects`
key is present, so what the file says is ignored rather than resolved.

The process-spawner count was wrong the same way and for the same reason. The paragraph
saying "five modules still spawn processes" was written to record that excluding
`orchestration/` from a search had hidden the fourth git call site — and it was itself
produced by a search that excluded `orchestration/`. There are four more in there
(`runtime.py`, `runtime_auth.py`, `runtime_capabilities.py`, plus the git one). Nine, and
the qualifier "outside the frozen tree" is now in the sentence. **Writing a criterion
down is not executing it.**

CR-14's acceptance artifact was too weak: it scanned the source for the literal
`subprocess.run`, which `from subprocess import run` or `subprocess.Popen` would satisfy
while the hazard stood — a test for one spelling of the defect rather than for the
defect. Rewritten as three positive checks on the parsed tree: the module imports nothing
that can spawn a process, makes no `create_subprocess*` call, and
`resolve_git_revision` accepts an `env`. Two of the three fail today.

Also: the test doubles kept `timeout=None` defaults, so if production ever stopped
passing one they would forward `None` to `subprocess.run` — an unbounded wait, with the
suite still green. They now require it, matching the protocol.

Next safe action: gate, then round 8. Still not committed.

Gate after round 7's fixes: backend **1055 collected, 1044 passed, 11 skipped**
(15m57s) — unchanged, as expected for a round that added no tests.

**Round 8 (5 findings, P1 empty for the fourth round).** Every one of them is the same
kind, and by now that is the report: prose claiming more than the code does.

The path taxonomy — corrected in `resolve_control_plane_db_path`, `config.yaml` and ADR
0002 in round 7 — was still wrong in `config_dir()`, **fifty lines up in the same file**,
because that docstring carried its own copy of the list and only the original was being
fixed. The copy is gone; `config_dir` now points at the one statement instead of
restating it. That is the structural fix, not a wording fix: a taxonomy stated twice
drifts, and this one drifted twice while being corrected.

It was also **incomplete**: `execution.allowed_workspace_roots` holds paths and is
anchored to `ProjectRegistry.project_root` by three separate constructors. It was missed
because the enumeration behind the previous version was made from `config.yaml` — and
that key is supported but not written there. Enumerating the set from the artifact rather
than from the code that reads it is the same defect as filtering a search by an
irrelevant category: both produce a complete-looking answer over an incomplete domain.

The spawner count was wrong a second time, in the paragraph written to record it being
wrong the first time. Ten modules under `agora/` can start a process; one is
`environments.py`, the implementation. "Nine" silently excluded it, exactly as "four" had
silently excluded the frozen tree. **Both exclusions were correct and unstated, and
unstated is what makes an exclusion a defect rather than a shorthand.**

CR-14's guard needed a third version. Structural checks kept being evadable — the first
scanned for the literal `subprocess.run` (`from subprocess import run` passes), the
second parsed the module and forbade `subprocess` imports and `create_subprocess*` calls
while requiring an `env` parameter (`os.popen(...)` with an unused `env` passes). Every
structural version tests for *a way of writing the defect*. The guard is now behavioural:
hand `resolve_git_revision` a recording Environment and require that it was used. No
alternative spelling leaves the recorder non-empty. It fails today at the first
assertion, because the function takes no `env`.

Next safe action: gate, then round 9. Still not committed.

Gate after round 8's fixes: backend **1055 collected, 1044 passed, 11 skipped**
(17m42s) — unchanged for a third round.

**Round 9 (3 findings, P1 empty for the fifth round).** The reviewer states it plainly:
**no defect was found in the production code.** Both blocking findings are about CR-14's
deferred guard, and the third is a count typo in an ADR next to a table that says the
right thing.

The guard needed a fourth version, and the two failures it had were opposite ones:

- **A miss.** It swallowed the outcome and asserted only that the recorder was
  non-empty, so a *partial* conversion passed — route one call through the environment
  for show, leave the real revision reads local. The guard is supposed to be the thing
  that says the hazard is gone; accepting a token call means it says so early.
- **A false alarm.** It asserted the injection parameter is named `env`. A genuine fix
  naming it `environment` — which is exactly what `WorkspaceProvisioner` already calls
  it — would have failed a guard that has to survive until W3, and failed with the wrong
  reason attached.

The fourth version hands in a root that **does not exist** and does **not** swallow the
outcome, so any call still made locally raises and the function can only return
successfully when every one of them went through the environment; and it looks the
injection parameter up from `{env, environment}` rather than pinning a spelling. Both
directions were then verified by construction rather than argued: a stand-in fixed
implementation using the `environment=` spelling passes, and the exact partial conversion
the review described fails.

Four versions of one guard, and each earlier one *read* as though it established the
thing it was named for. That is the same failure as a paragraph that reads as though a
hazard is handled — which is what adr/0015 is about — except it was wearing a test's
clothes, and a test is supposed to be the cure.

Next safe action: gate, then round 10. Still not committed.

**Rounds 10-14: one guard, seven versions.** Round 10 found the only remaining defect in
the change, and it was in CR-14's deferred guard rather than in any shipped code — as had
been true since round 5. The rounds after it were spent on that one test, and the
sequence is worth keeping because each version *read* as though it established the thing
it was named for:

1. scan the source for the literal `subprocess.run` — green for
   `from subprocess import run`;
2. parse the module: no `subprocess` import, no `create_subprocess*`, and an `env`
   parameter — green for `os.popen(...)` with an `env` that is never read;
3. behavioural, but swallowing the outcome and asserting only a non-empty recorder —
   green for a token `env.run` with the real reads still local;
4. the same against a hard-coded absent root — a path never checked for absence, and on
   Windows a real location that could hold a clean repository;
5. plus all three reads recorded — green for an implementation that delegates *and also*
   fires a redundant local process, because the refusal was an `AssertionError` and the
   redundant spawn sat inside `try/except Exception`;
6. refuse `subprocess.Popen.__init__` and a named list of `os` spawn functions, raising a
   `BaseException` — still missed `os.spawnve` while covering `os.spawnv`;
7. refuse the whole `os.spawn*` family taken from `dir(os)`, and **narrow the claim to
   match the code** instead of widening the code toward the claim.

Version seven is the first that says only what it checks: it names the refused paths, and
states two things it does not establish — a native spawn through a C extension is not
observable from inside the process, and a deliberate `except BaseException` swallows the
refusal. It also documents that an unrecognised injection parameter name makes it *fail*,
which for a guard whose job is to block until a hazard is gone is the safe direction.

Every version was checked by construction against stand-in implementations before
submission, and versions 5 and 6 were caught that way rather than by argument — version 5
passed the very scenario it was written for until a fake was built to try it.

Round 13 and round 14 both returned READY TO COMMIT.

Final gate: backend **1055 collected, 1044 passed, 11 skipped** (13m10s). Baseline before
W2 was 996/986/10, so 59 tests were added and the skipped count moved by exactly one —
CR-14's, recorded when it was introduced rather than explained afterwards. Frontend
untouched, 158/158 not re-run.

Next safe action: W4 (Goal / Requirement / CR Log / ADR into the UI), per CR-9's ordering.
CR-14 stays OPEN and belongs to W3.

## W8 — the primitive becomes a conversation, and four rounds of being wrong about it

A task is now one continuing session with an agent: say something, it does it; say the
next thing, it remembers the last one. Decision and the four rejected alternatives are in
`tasks/agora/adr/0008` (mechanism) under `adr/0007` (the primitive itself). Runs did not
disappear — a turn *is* a run, with its workspace, exit code, files and review — the
person just stopped having to think in them.

### Measured, not argued

Three of the four mechanism decisions exist because a CLI refused the obvious spelling,
found by running the installed binaries on 2026-09-08 rather than reading a matrix:

- `codex exec resume` accepts **no** `--sandbox` (nor `-C/--cd`), so the sandbox travels
  as `-c sandbox_mode="workspace-write"`. Without it every follow-up turn silently loses
  the ability to edit a file — and the failure reads like a model refusing to work.
- `claude -p --output-format stream-json` **requires** `--verbose`; it exits 1 otherwise.
- `kiro --output-format stream-json` **requires** `--agent-engine v2`, and the v1 engine's
  refusal arrives as a `runError` *event*, so a misconfigured engine surfaces as a
  readable line rather than an empty window.

kiro also grew structured chat output since 2026-08-27, which `lessons/coding-agent-cli-
headless-resume.md` had recorded as absent. That lesson's own first gotcha — capability
matrices expire fast — has now been right twice about itself.

Acceptance is a re-runnable test, not a screenshot: `tests/test_session_continuity_live.py`
(marked `integration`, so the ordinary gate never spends quota) drives **three real codex
turns** in a throwaway worktree. Turn two answers with the word turn one was given; turn
three edits a file from inside the resumed session. 1 passed.

### Four review rounds, 35 findings, every one confirmed

Rounds 1, 2 and 4 were codex; **round 3 was kiro**, standing in while codex's quota was
exhausted (`adr/0017`). Counts: 7, 15, 6, 7. **No false positives in any round.** Three
findings are worth carrying forward as a pattern, because all three are the same mistake:

1. **A comment claiming protection the code did not provide.** `_read_streaming` said "a
   secret cannot be split across two writes" — false for exactly the two adapters whose
   streaming it was written for, since claude and kiro emit text token by token. The first
   fix then named its own buffer `_OVERLAP` and did not overlap: the cut had already been
   written by the time the retained half arrived. Three attempts, and only the third
   (redact the whole buffer, then cut) is what the sentence claimed. Its residual limit —
   a secret straddling a forced flush of a single 8 KiB line — is now written down instead
   of covered over.
2. **A key that defeated a gate from outside it.** Sessions keyed by `(task_id, adapter)`
   made the automatic review *resume the implementation session* whenever worker and
   reviewer share an adapter, which is an ordinary configuration. The independent second
   opinion inherited the implementer's entire context — `adr/0002` undone by a primary
   key. The role is now part of that key.
3. **A fix that produced the next finding.** Each round's repairs created the following
   round's defects: role in the backend key left the frontend picking rows by adapter
   alone; `SESSION_LOST` made the refusal path downgrade it to the self-healing state;
   marking a caller-chosen id `active` at launch left `--resume` pointing at a session
   that may never have been created. This is the argument for re-reviewing after fixing
   rather than committing on the round that found them.

Also fixed, each with a test: the repository's own `config.yaml` pinned all three adapter
commands to their pre-W8 form, so none of this would have taken effect on this
installation (CR-44 — and codex found it, not me); an overridden `command` inherited the
real CLI's resume command, sending turn two of a stubbed `claude` to the real binary;
`/runtime-capabilities` claimed "token capture unsupported" for every adapter when the
registry failed to build; the usage table was keyed by adapter name while argv decided the
format, and the two had already drifted — codex's JSONL reader existed and was unreachable
because the argv never passed `--json`; kiro's measured credits were discarded by a token
check; a 64 KiB stdout tail starting mid-event lost the usage of exactly the long runs
worth measuring; transcripts grew without bound; a missing transcript rendered every
pre-W8 run as "finished, said nothing"; a dropped fetch was recorded as a permanent hole;
a settled run with more than one page of log was unreachable.

### The window

`/workspace` keeps its rail and its two-state right column and gains a centre tab pair:
the conversation, and the timeline it did not replace (the timeline's completeness
judgement, CR-26, is not something to drop for a redesign). Navigation becomes mockup v2's
four screens; **Control Plane / Requirements / Workflows leave the first level** — `adr/0001`
froze that layer and said the UI must not expose it, and they had stayed in the sidebar for
eleven days anyway. Runs stops being a place: a run is a turn inside a task.

Environments and Skills show what can be read and say plainly what cannot. There is one
environment implementation and no skill reader at all, so neither screen invents an
inventory to fill the space.

### Gates

Backend **1374 collected, 1361 passed, 12 skipped** (12m32s), one deselected — the live
acceptance test. Baseline before W8 was 1301/1289/12, so **72 tests were added and the
skipped count did not move**. Frontend **293 tests, 292 passed, 1 skipped** (the
pre-existing one), tsc 0, eslint 0, build 14 pages.

Those figures are the last gate of six review rounds, not of the first: rounds five and six
added twelve more tests, and the earlier draft of this paragraph quoted the round-four
numbers for a while. A record whose figures cannot be reproduced is not a record, which is
the whole reason AGENTS.md asks for the collected count as well.

Not verified, and stated rather than implied: the conversation component itself is not
exercised by a test — CR-18 is why, and its decisions therefore live in
`lib/conversation.ts`, which the node runner can mount. The browser path was checked only
by rendering every route; nobody has typed into the window.

### Next safe action

W8's remaining exit criteria: the W7 workspace tasks belong in the workbench rather than a
settings card, and changed files plus gates belong inside the conversation stream. Then
claude and kiro need the same three-turn live test codex has. CR-43 (a file an agent writes
through its sandbox can be unreadable to the control plane) stays OPEN and wants a
measurement before a fix: whether `snapshot_changes` records those files as *unknown* or as
*absent* decides whether it is a nuisance or a lie.

### Committed, and what the remaining twenty-six rounds cost

`4fb5a3a`, on `feature/w8-session-ui`: 42 files, +11202 lines. The paragraph above
was written after round six; review ran to **thirty-two**. The final gate is
**1435 passed / 13 skipped** against a pre-W8 baseline of 1289 — **146 tests added**,
one per finding that could be pinned to behaviour. Frontend: 298 tests, tsc 0,
eslint 0, build 14 pages.

**154 findings: 153 confirmed and fixed, one false positive.** The false positive is
round 31's claim that the Today page asks for 501 rows against a limit of 500. The
constants say otherwise — `MAX_PAGE = SERVER_PAGE_MAX - PROBE` = 499, so the request is
exactly 500 — and `paged.ts` already documents that an earlier round fixed precisely
that. The clamp written in response to it was reverted rather than kept: a guard that
fixes nothing leaves a comment asserting a defect that does not exist.

Rounds 1-4 were codex; from round 5 on **kiro substituted** because codex's quota was
exhausted (adr/0017 — a substitute for the gate, never a second opinion on one diff).

Three findings reached past W8's own surface, and each is worth more than its round:

* **A run that committed its own work was recorded as delivering nothing.** With a clean
  baseline, an agent that edits and then commits leaves an empty `git status`, so the
  snapshot returned `files: []` — automatic review saw nothing to review, and handoff then
  bound a commit no review had read. The baseline now records HEAD and the snapshot asks
  what moved; a baseline that cannot answer says `commits_unread` rather than reporting an
  empty list.
* **A stop did not take the process tree.** An agent that starts a background script and
  exits left it writing the worktree after the deliverables were frozen and the lock
  released. Windows is now covered by a job object assigned at spawn — measured against a
  grandchild whose parent had already exited, which `taskkill /T` cannot reach. Two of
  that fix's own bugs were measured rather than reasoned about: an undeclared ctypes
  `restype` truncates a 64-bit HANDLE and the next call is a *fatal* process exception,
  and `AssignProcessToJobObject` needs `PROCESS_SET_QUOTA`. POSIX is untouched and says
  so, carried by a skipped test naming the platform (CR-46).
* **Japanese filenames were recorded as paths that do not exist.** `core.quotePath` is on
  by default, so `git status` octal-escapes any non-ASCII path; the parser stripped quotes
  and stopped. Now the snapshot reads `--porcelain=v1 -z`, where nothing is quoted and a
  rename's two paths are two records — the format stops being ambiguous instead of the
  parser getting cleverer.

The recurring shape, stated because it cost the most: **most of each round's findings were
consequences of the previous round's fixes.** One redaction guard took five attempts —
leak, then silent deletion, then leak again, then deletion again — because each attempt
answered "was this value cut, or did it end here?", which is not answerable from either
half. The fifth stopped answering it: `safe_cut` makes the cut itself illegal anywhere
inside a match. Twice the defect was a guard that existed but sat on a path the failure did
not take, and once a bound that could not fire because a pending pipe read on Windows does
not answer a cancel.

### Next safe action (unchanged, plus what is waiting on the user)

Nobody has typed into the conversation window yet — that is exit criterion 1 and only the
user can close it. Four CRs wait on a decision: CR-44 (codex `bridge_mode`), CR-45
(whether to narrow the seven-state session machine, which is where most late findings
landed), CR-46 (the POSIX half of stopping a tree), CR-47 (adopting a session recorded
before roles existed).

## W8 exit criterion 6 — workspace tasks in the workbench, and the progress verdict reuse

`feature/w8-session-ui`, on top of `4fb5a3a`, not yet committed. Two things landed in
this slice: the W7 workspace-task list moved off the Settings card into the workbench
rail (exit criterion 6), and — the substance of codex rounds 33–34 — the task progress
bar stopped deciding progress a second time and now delegates to the one honest module
that already answered that question.

### The progress-verdict reuse (round-3 finding B)

`taskProgressOf` in `frontend/src/lib/workspace-sources.ts` had grown a parallel copy of
a decision `frontend/src/lib/progress-snapshot.ts` already owns: which checklist
convention a snapshot was counted in, an unknown convention, a numeric `in_progress`
smuggled under checkboxes, nulls or non-integers among the counts, the done-only
percentage, and every wordless "there is no number here". Round 2 had already removed the
only justification for the duplication — it changed `_read_resume` to report
counts + `convention` + `reason`, which is exactly `ProgressVerdict`'s shape (the RESUME
reader and the dashboard snapshot now share `count_progress_verdict`). Two modules
deciding one question is what produced round-2 finding 3 and round-3 finding B.

The fix maps the RESUME verdict onto `ProgressSnapshotLike` — the same map the backend's
`ProgressVerdict.as_snapshot` makes — and hands it to `progressBarOf`. The one answer
kept in `taskProgressOf` is the only one with no snapshot counterpart: the RESUME **file**
could not be read (`progressBarOf` reasons about a snapshot's *text* and has no notion of
the file being unreadable; folding that into a snapshot reason would state a fact about
the snapshot from a fact about the file). `progress: null` on a *readable* RESUME is not
that case — it means the file records no 进度快照 section, which maps to the snapshot's own
`absent` and renders "no snapshot", blank, never 0%. `TaskProgress` is now
`{ show: "resumeUnreadable" } | { show: "bar"; bar: ProgressBar }`; the workspace renderer
draws the `ProgressBar` and decides nothing, wording the wordless outcomes in task terms
(a `WORKSPACE_PROGRESS_KEY` map) rather than re-deciding them.

Delegating fixed round-3 finding A's twin gaps for free: the payload-shape contradictions
codex named — an unknown convention with a credible count, `checkboxes` carrying a numeric
`in_progress`, `markers` carrying nulls — were already refused by `progressBarOf`, and are
now pinned at this boundary too (each comes out `unknown`, never a percentage).

### Round-3 finding A — the onward link was inert

`taskOnwardOf` targets `/workspace?project=X`, which is the page the row is already on. The
URL-reading effect in `workspace/page.tsx` is mount-only (`[]` deps, no `useSearchParams`),
so a `next/link` client-navigation to the same page never remounted it and `projectFilter`
never moved — the link looked live and did nothing. Fixed by reusing the `selectTask`
pattern: a new `openProject` handler sets the filter and `window.history.replaceState`s the
URL (so a real reload still lands on the same view), threaded down
`TaskRail → WorkspaceTasksPanel → WorkspaceSourceBlock → WorkspaceTaskRow`. The row keeps
the `href` as the accessible real-navigation fallback (new tab, reload, no-JS) and a plain
primary click sets the filter directly.

### Round-4 (codex round 35) — two navigation defects in that same click, and two record fixes

* **Finding 2: modifier-key clicks must keep the browser's navigation.** The round-3 handler
  called `event.preventDefault()` unconditionally, which cancelled Ctrl/Cmd/Shift-click and
  middle-click — the exact new-tab/new-window cases the `href` was kept for. The decision
  "should this click be intercepted?" is now a pure predicate `shouldInterceptClick(event)`
  in `frontend/src/lib/workspace-sources.ts`: it returns true only for a plain primary-button
  click (`button === 0` and no meta/ctrl/shift/alt), so a modified or non-primary click falls
  through to the default against the real `href`. The `WorkspaceTaskRow` click handler now
  bails (`if (!shouldInterceptClick(e)) return;`) before `preventDefault`.
* **Finding 3: opening a project clears the stale task selection.** `openProject` set the
  `project` filter and param but left any existing `task=A` in the URL, so the URL named a
  task that did not belong to the project it named; a copied or reloaded URL would mount the
  wrong task briefly before the in-page effect corrected it. `openProject` now also calls
  `setSelectedTaskId(null)` and `params.delete("task")`, so the URL it produces matches the
  project-only `href` the click claims.

### Gate

Backend `pytest tests -m "not integration" -q`: **1440 passed / 13 skipped**, 1 deselected
— **collected 1454**, the 1 deselected being the single integration test. This slice *did*
change the backend: `backend/agora/projects/progress_snapshot.py`,
`backend/agora/projects/workspace_tasks.py`, `backend/tests/test_progress_snapshot.py`, and
`backend/tests/test_workspace_tasks.py`. Baseline collected was 1448 before this slice;
those changes took it to **1454** (**+6 backend tests**). The 1440 passed / 13 skipped / 1
deselected figure was measured in round 2, *after* those backend changes landed; round 3
changed only frontend files, so that measurement still stood then — the backend suite was
not re-run in round 3 because no backend file moved. Do not read this as "no backend file
touched": the backend suite must be re-run whenever a backend file changes, and it did
change in this slice. Schema check `scripts/export_protocol_schemas.py --check` exits 0 (no
drift). Frontend `npm run test:control-plane`: **collected 319, 318 passed, 1 skipped** (the
pre-existing one) — up from 293 collected before this slice, so **26 frontend tests added**,
covering the verdict→snapshot mapping, the resume-unreadable answer kept out of
`progressBarOf`, the four distinct non-bar facts, and the three payload-shape
contradictions. `npx tsc --noEmit` 0, `npx eslint` 0, `npm run build` 14 pages.

**Round-4 gate (run against the tree as it is about to be committed).** Backend
`pytest tests -m "not integration" -q` re-run in full this round: **1440 passed / 13 skipped
/ 1 deselected — collected 1454** (16m26s). This is the figure that stands, measured against
the current state of all four backend files; it matches the round-2 number, confirming round
3's frontend-only changes did not disturb the backend. Frontend `npm run test:control-plane`:
**collected 323, 322 passed, 1 skipped** (the pre-existing one) — up from 319 in round 3, so
**+4 frontend tests**, all covering `shouldInterceptClick` (a plain primary click is
intercepted; each of meta/ctrl/shift/alt yields to the browser; middle/right button yields;
a modifier on the primary button still yields). `npx tsc --noEmit` 0, `npx eslint` 0,
`npm run build` 14 pages. Schema check `scripts/export_protocol_schemas.py --check` exits 0.

Not verified, stated rather than implied: no automated test exercises the click *behaviour*
in a browser. Three things remain browser-owed and the node runner cannot mount them: (i)
round-3 finding A's inert-link fix (client navigation of `next/link` against a mount-only
effect); (ii) round-4 finding 2, that a Ctrl/Cmd/Shift/Alt or middle click actually opens a
new tab/window rather than being cancelled; (iii) round-4 finding 3, that opening a project
leaves a URL with no stale `task` param. What *is* tested: `shouldInterceptClick` is a pure
predicate with node tests, and the `openProject` param-clearing is a plain state/URL write
checked by tsc, eslint, and the static build. Nobody has clicked these in a browser; that
check is owed.

### Review state

* **codex round 33** and **round 34** on this slice. Round 34 raised four P2: (A) the inert
  onward link, (B) `taskProgressOf` validating `reason`/numbers but not `convention` against
  the field shape so an unknown convention rendered a credible percentage and a `checkboxes`
  numeric `in_progress` / `markers` nulls were accepted, (C) this PROGRESS.md entry was owed,
  and (D) exclude `.claude/settings.local.json` from the change.
* **A, B, C confirmed and fixed** as above.
* **D corrected as an over-claim.** On the author's machine `.claude/settings.local.json` is
  covered by the user's global git ignore:
  `git check-ignore -v .claude/settings.local.json` resolves to
  `"C:\Users\wuche/.config/git/ignore":1:**/.claude/settings.local.json`, and
  `git status --porcelain --untracked-files=all | grep -c settings.local` is 0 here. But that
  is a property of *this environment* with that per-user config readable, not a property of
  the repo: codex reports that in its environment `git status` and
  `git ls-files --others --exclude-standard` both list the file, because
  `~/.config/git/ignore` was not readable there. Both measurements are true. The earlier
  wording ("never appears untracked and cannot be swept into a commit by any bulk `git add`")
  read as a guarantee about the repo and was not one — a guard narrower than the claim it
  makes. The actual mitigation is procedural, not a repo property: **the commit stages this
  slice's files explicitly (the ten modified paths named above); there is no bulk `git add`.**
  No repo-level ignore entry was added and the file was not touched — repo-ignore policy is a
  separate decision that does not belong in a slice about the workspace task list.
* **codex round 35** raised four items on this slice, all addressed this round: (1) a record
  defect with a wrong diagnosis — the backend suite *had* covered the change (round-2 number,
  measured after the backend files changed), but the sentence around it falsely said "no
  backend file touched"; the Gate paragraph is rewritten to name the four backend files and
  the 1448→1454 (+6) collected change, and the full backend suite was re-run this round
  against the current tree (1440 passed / 13 skipped / 1 deselected, collected 1454). (2)
  modifier/middle clicks must keep browser navigation — fixed via `shouldInterceptClick`.
  (3) opening a project must clear the stale task selection — fixed in `openProject`. (4) the
  finding-D wording was an over-claim about repo behaviour — corrected to say what was
  measured and where, and to name the explicit-staging mitigation. Findings 2 and 3 are
  browser-owed behaviours; the testable predicate was lifted out and given node tests, and
  the residual browser check is recorded as owed above.
* **The independent-reviewer gate is closed for this slice, by Kiro standing in for codex.**
  codex round 37 was interrupted mid-review by `You've hit your usage limit` (quota resets
  19:57 local), so the gate ran as `scripts/kiro-review.sh` — model pinned `gpt-5.6-sol`,
  `--effort max`, read-only. This is the documented substitution (adr/0017): a substitute for
  that run, never a second opinion alongside codex, and never chosen because codex was slow or
  noisy. The reviewer is not the model that wrote the code. It raised **one P2 and no P1**:
  this file said "nine slice files" where the diff is ten and where its own Gate paragraph
  says "ten modified paths" — a maintainer following it literally would have staged nine and
  most likely dropped this record from the commit. Fixed in the Next safe action below. The
  commit message says that Kiro stood in, so the history does not claim a codex review that
  did not happen.

### Blockers

None.

### Next safe action

Commit the **ten** slice files on `feature/w8-session-ui`, staged explicitly. (The count is
ten, matching the ten modified paths named in the Gate paragraph: eight implementation/test
files, `frontend/src/app/settings/page.tsx`, and this record. An earlier revision of this
line said nine, which would have left one file — most likely this record — out of the
commit.)

W8's remaining exit criteria: **exit criterion 1 (typing into the conversation window) still
waits on the user**, and with it three browser-owed navigation checks named above. **CRs 44–47
no longer wait on a decision** — all four were decided on 2026-09-10: CR-44 keep codex
`bridge_mode: cli` (session continuity over app-server's two-way approvals); CR-45 narrow the
session state machine to four states, but only after W8's remaining exit criteria (carried by
a new W8-b milestone, so the decision has something to land on); CR-46 accept the POSIX
process-tree gap as a known platform difference, the skipped test staying as its trigger;
CR-47 session claiming for `role=unknown` goes to the backlog.


## W9 — Today's three tiers (screen 1), the frontend half

`feature/w8-session-ui`, on top of `72a1aac`, not yet committed. The backend half of D-1
(`today_focus` on `GET /api/projects`, `PUT`/`DELETE /api/projects/{id}/focus`) landed on
2026-09-11 as `bfeeb7c`; this slice is the screen that uses it. Decisions and the mockup
comparison are in `docs/W9-mockup-alignment.md`; the how-to this slice followed is
`docs/NEXT-STEPS.md` §一.1.

### What the screen now is

Today reads three things, each settled on its own (projects with `today_focus`, the task
page, open attention), and draws the mockup's tiers from them:

* **今天主推** — up to `limit` big cards in the server's promotion order. Each card reads
  its own dashboard (three requests at most) for the next step, the blocking count and the
  progress snapshot bar; the goal comes off the project list. "3 / 3 已满 —— 第 4 个要上来，
  得先放下一个" is the server's `is_full` repeated, never `length >= limit` recomputed here.
  A refused promotion (409) shows that sentence on the row that asked; an unresolved
  promoted id is shown holding its seat, with a release button.
* **在推进，但不是今天的重点** — compact rows: an open task updated in the last 14 days,
  measured from the task read; one-click 提升为主推.
* **N 个休眠项目** — folded to one line; only on a complete task read.
* **在推进还是休眠 —— 说不了** — the tier the mockup does not have and this product needs:
  projects that are neither observed advancing nor provably dormant, each row saying why
  (the read fell short, or an open task's update time could not be read).
* **卡住 / 需要你** — the cross-project stuck list, migrated here from the workbench rail
  (W9 first cut's recorded debt #1), deliberately not grouped by project.

Card fields the mockup draws that this product cannot read — milestone, environment,
branch/commit/pushed, 上次, 发现 — render as capability slots (`lib/nav-capability.ts`,
shared `components/capability-slot.tsx` moved out of the project nav), never blank and
never a number. 在册 (known defects) shares the nav's slot, so the two surfaces cannot
disagree. The mockup's "换个 agent" button is not on the card (cut by decision); its resume
footnote is not either (sessions are per task; nothing rolls one up per project). "继续工作 →"
opens the project's most recently updated open task and is offered only from a complete
task read (the global page is ordered by priority, so a capped page cannot name the latest);
"先看看这个项目" opens the workbench inside the project without starting anything.

### Files

`frontend/src/lib/today-focus.ts` (new; the judgements), `frontend/tests/today-focus.test.mts`
(new; 21 tests as collected — 18 top-level `test(` calls plus one loop declaring three,
one per short read outcome — incl. two source scans), `frontend/src/app/page.tsx` (rewritten),
`frontend/src/components/today-focus-card.tsx` (new), `frontend/src/components/capability-slot.tsx`
(new, extracted), `frontend/src/components/project-nav.tsx` (imports the shared slot),
`frontend/src/lib/control-plane.ts` (`charter` on `ProjectListItem`, `TodayFocusPayload`,
`today_focus?` on `ProjectList`, the two mutations), `frontend/src/lib/i18n/dictionaries/today.ts`
(rewritten), `frontend/package.json` (test script), and this record. No backend file.

### Gate

* Frontend: `pnpm run test:control-plane` **361 collected / 360 passed / 1 skipped**
  (was 340 / 339 / 1 at `d6e2c99`; +21 collected, all in the new suite). `tsc --noEmit` 0.
  `eslint` 0 on the changed files. `next build` 14 pages.
* Backend: no backend file touched. `tests/test_web_ui.py` re-run against the fresh build
  as the one backend test that depends on it: **14 collected / 14 passed** in 28.95s. The full backend suite was not
  re-run for a frontend-only change.
* End to end against the running server: `GET /` 200 with the build mounted;
  `GET /api/projects` carries `today_focus`; `PUT /api/projects/agora/focus` →
  `{project_ids:[agora], limit:3, is_full:false}`; unknown project → 404; `DELETE` → `[]`.
  This machine registers two projects and no tasks, so the 409 path and the tier placement
  were exercised by the node tests, not by hand.

### Review state

Fourteen rounds of independent review on this slice, 35 findings: 27 confirmed and
fixed, eight rejected — two findings, one raised six times and one twice, with their
reasons below; round 14 raised nothing else, which is what closed the gate. codex ran
rounds 1, 2 and 7; **Kiro stood in for rounds 3–6 and 8–14** (`scripts/kiro-review.sh`, `gpt-5.6-sol`, `--effort max`) because
codex answered `You've hit your usage limit` from 20:1x local and again from the eighth
round on — the documented substitution (adr/0017), never alongside codex. The commit
message says so.

What the rounds caught, in the order they came, because each is a shape this repository
keeps producing:

1. `failed` treated as open work — it is terminal (`tasks/transitions.py`), and a "continue"
   pointed at it would start something, not continue it.
2. Focus mutations replacing the whole snapshot without ordering — a write token now
   guards every `setFocus`, buttons are exclusive while one is in flight, and refresh is
   disabled during a mutation.
3. Promote buttons drawn under a server that reported no focus tier — hidden there.
4. Demote's transport failure asserted as semantic failure — the endpoint is idempotent;
   the tier is re-read and the notice says "unconfirmed" until that lands, then retires.
5. "Most recently updated" picked off a priority-ordered capped page — complete reads only.
6. Heading counted only the second tier — the mockup's "4 个在推进" counts the focus cards.
7. `atLeast: 0` on a failed read — `TierCount` gained `unknown`, same contract as
   `portfolioGroupCountOf`.
8. A stale "3/3 full" refusal surviving a demotion — retired when any snapshot shows
   the tier open.
9. Archived tasks filtered from the stuck list while all-clear stood — archiving is a
   display property, not a state; they stay, and they count as open work everywhere here.
10. An unparseable `updated_at` read as inactivity — now `unknown`; such a project is
    unplaced, not dormant, and its row says which doubt it carries.
11. A retained dashboard rendered as current across demote → promote and across refresh —
    dropped the moment its project leaves the tier (`applyFocus`) and re-read per load.
12. The dashboard effect's bookkeeping re-reading unchanged cards and racing its own
    abort — it now remembers what it answered for and forgets the rest synchronously.
13. (round 8, P1) An open item whose task is not on the task page was dropped, and the
    two "complete" reads then supported an all-clear over it. The reads are not one
    snapshot; the item is on the page, so it is now shown from its own words
    (`OrphanRow`), counted, and never invents the task's title or state.
14. (round 8) On refresh the cards rendered the dashboards from before the refresh until
    an effect's timer dropped them — dropped in `load` now, in the same batch as the new
    tiers.
15. (round 8) A promote whose answer was lost read as "the change was not made"; the
    PUT may well have landed. Worded as unconfirmed until the re-read lands, and every
    refusal but a still-true "full" is retired by a successful re-read.
16. (round 9, P1) Orphan items were deduplicated by task — a task off the page holding an
    approval *and* a blocker showed only the first. Every item is now its own row.
17. (round 9) A dashboard answer arriving after a refresh emptied the map, or after a
    demotion pruned it, wrote itself back before the effect could abort it. Requests now
    carry a generation and write only while their id is still wanted; a demotion forgets
    its ids synchronously, not in the effect.
18. (round 9) Two record defects: the W9 alignment table repeated a review tally that had
    already drifted from this file (it now points here instead of restating numbers), and
    this file claimed 24 new tests where the suite defines 21 as collected.
19. (round 10) The stuck list was one row per task, so a task holding an approval and a
    blocker showed one row and an "exact" count of 1. It is now one row per stuck thing —
    each observed item its own row, a task reached on state alone one row — which is also
    how the mockup draws the list and how the orphan items were already shown.
20. (round 11) A task carrying items lost its own state row: a `failed` task with an open
    approval showed the approval and not the failure. The state that demands action by
    itself is now a row of its own, alongside the item rows.
21. (round 11) Item rows took the task's aggregate column, so a blocker under a question
    was coloured as a question. Each row is now placed by its own signal — an item by its
    kind, a state by its state — through the same `portfolioBucketOf`.
22. (round 12) A promotion started during a refresh whose project read then failed left
    the new focus in hand and the project list empty, so every focus id was placed as
    "unresolved" — each with a release button that really demotes a registered project.
    The focus tier is now placed only against a project list that was read (otherwise it
    says the list could not be read and offers nothing), and no mutation button is
    enabled while a refresh is in flight.
23. (round 13) The dormant line said "no task activity in 14 days" while the tier measures
    whether any *open* task moved — a task that closed an hour ago made its project
    dormant under a sentence that denied it. The sentence now says what is measured; the
    capped-read notice was corrected the same way.

Rejected, one other, twice (rounds 13 and 14): the three reads are awaited together, so one hung
request holds back the two that answered, and the page's header comment said "each
settles on its own" — which described the failure paths, not the timing. The comment is
reworded; the structure is not changed: it is the pattern every board in this app uses,
and a hung request is a request-layer concern (a timeout in `request`) that would have to
land for all of them at once, not in one screen.

Rejected, the same finding six times (rounds 8, 11, twice in 12, 13, 14): (round 8, P2) the
projects read and the task read are not one snapshot, so a project created — with a
recent task — between the two requests of one load could be filed dormant until the next
refresh; (round 11, P1) likewise the task read and the attention read, so an item raised
between them against a task the task read did carry is missed and the all-clear sentence
stands until the next refresh. Both true,
neither fixed here: the three requests are issued concurrently inside one `Promise.all`,
the window is the difference in their round trips, a refresh closes it, and closing it for
good means a joint snapshot the backend does not offer — a contract change outside a
frontend-only slice, and the same window the Portfolio and the old Today have always had.
Recorded so it is not mistaken for an oversight; if it is to be closed, it is a backend
CR, not a frontend fix. Noted and left: `.claude/settings.local.json` appears untracked in
codex's environment (it is ignored by this machine's global git ignore, not by the repo);
the commit stages this slice's files explicitly, as the previous slice's record already
requires, so it cannot be swept in.

One contract wrinkle recorded rather than fixed here (backend untouched by instruction):
the 409 detail is `{error: "today_focus_full", limit}` while the shared error parser reads a
`code` field, so the discriminator does not survive the trip. The client maps by status —
this endpoint documents exactly one 409 — and `promoteRefusalOf` says where a second 409
would have to be told apart.

### Blockers

None. The browser check of the rendered page is owed: the Chrome extension was not
connected this session, so the page was verified by build + the API round trip + the node
tests, not by eye.

### Next safe action

Commit the slice files on `feature/w8-session-ui`, staged explicitly, and push to
`private`. Then, per `docs/NEXT-STEPS.md`: the middle column's Run card stream (§一.2), and
the shell/top bar (§一.3) which also moves the projects entry to where the design puts it.
Update the progress table in `docs/W9-mockup-alignment.md` (Today 三档：前端 → ✅) in the
same commit.


## W9 — the middle column is one stream (screen 2), and W8 exit criterion 4 closes

`feature/w8-session-ui`, on top of `cf2156a`, not yet committed. The fourth of W9's cuts
(`docs/NEXT-STEPS.md` §一.2): the workbench's middle column stops being two tabs and
becomes the mockup's stream of run cards with the task's history between them.

### What changed on screen

* **One column, no tabs.** The conversation now takes the page's events read and
  interleaves it with its turns by time — notes, stage changes, specs, gates between the
  cards — through the same `timelineVerdictOf` / `timelineReadOf` the separate timeline
  tab used, so the completeness caveats (partial page, read in flight, record known to be
  missing, genuinely empty) are the same four sentences in the same places. The tab bar
  and `TimelineColumn` are gone; `TimelineEntry` and its wording moved to
  `components/timeline-entry.tsx` unchanged.
* **The card carries what the mockup draws and the records hold.** Executor · run id ·
  state with exit code · when · duration (settled turns only, from both stamps; a live
  turn says "in progress" rather than ticking a clock) · the prompt and the terminal ·
  **the files it delivered**, through a `DeliverablesBlock` shared with the right column
  (view / open per file, and the six ways of saying "no list here" kept apart) · reviews
  indented under the turn they review, each with its own state pill and its transcript on
  demand · a footnote with the worktree the run ran in and the nested count.
* **Not drawn, because the data does not carry it:** a retry nested under the run it
  retries (no record says "retries" — lib/run-tree.ts; retries stay top-level and the
  footnote says "other", never "retry"); an environment picker (runs execute on this
  machine until W3-a; a labelled "环境 · 本机" says so and no dropdown promises otherwise);
  `exec resume <session>` on the card (the session id is per adapter, not per run).
* **The message box does both things the mockup's placeholder names:** 派活 (a turn) and
  記一笔 (a `user_message` event, whose id goes up to the page so the stream can say it is
  missing an entry until the read that fetches it lands).
* **Events older than the oldest shown turn are folded with the earlier turns** and the
  "earlier turns exist" line says how many; on a complete page nothing is folded.

W8's exit criterion 4 — "Run 仍然看得见：改动文件、门禁、复核/重试的嵌套，显示在对话流里面" —
is met by this: files on the card, reviews nested, gate events interleaved; the gate's
own block stays in the right column where the mockup draws it.

### Files

`frontend/src/components/conversation.tsx` (the stream), `frontend/src/components/timeline-entry.tsx`
(new; moved out of the page), `frontend/src/components/run-deliverables.tsx` (new; the shared
files block, `FileRow` moved into it), `frontend/src/app/workspace/page.tsx` (tabs, timeline column,
`RunCard`, `FileRow` and `relTime` removed; `SettledRunChanges` uses the shared block),
`frontend/src/lib/conversation.ts` (`turnDurationMs`, `formatDuration`, `streamEventsOf`,
`nestedSummaryOf`), `frontend/tests/conversation.test.mts` (+5 tests incl. one source scan), `frontend/tests/run-tree.test.mts`
(+1: summaries nest their reviews), `frontend/src/lib/run-column.ts` (identity from both shapes),
`frontend/src/lib/i18n/dictionaries/workspace.ts`,
`frontend/src/lib/i18n/dictionaries/conversation.ts`, and this record. No backend file.

### Gate

* Frontend: `pnpm run test:control-plane` **367 collected / 366 passed / 1 skipped**
  (was 361 / 360 / 1). `tsc --noEmit` 0. `eslint` 0 on the changed files. `next build`
  14 pages. The page shrank by about 480 lines (moved, not deleted).
* Backend: no backend file touched.
* Not exercised by hand: this machine holds no task, so the stream was verified by the
  node tests and the build, not by a live conversation. The browser check is owed, as it
  was for the Today slice.

### Review state

Nine rounds of independent review on this slice, 23 findings: 17 confirmed and fixed,
six rejected with their reasons below. **Every round ran as Kiro standing in for codex**
(`scripts/kiro-review.sh`, `gpt-5.6-sol`, `--effort max`): codex answered
`You've hit your usage limit` at every attempt this session — the documented substitution
(adr/0017), never alongside codex. The commit message says so.

What the rounds caught, because each is a shape this repository keeps producing:

1. Events folded behind "earlier turns" at the 500-turn ceiling, where the probe is
   gone and older turns are not proven — folded behind *proven* older turns only now,
   and at the ceiling a page shorter than the ceiling is the whole history.
2. Review runs' files excluded from the card — offered on the nested card on demand: a
   reviewer that wrote a report delivered something.
3. A failed files read with no retry for the session — the block offers one, and the
   right column's own read re-asks too.
4. A note's success cleared whatever was in the box, including text typed meanwhile —
   both send and note clear only if the box was not edited since (by edit count, not by
   text: the next message may be the same words).
5. A stale comment at the call site still describing the tabs.
6. The nested card showed the reviewed run's id and not its own — two reviews of one turn
   by the same executor were indistinguishable.
7. The file preview ignored the server's `truncated` — said now, so half a file is not
   approved as the whole. And every preview failure read as "changed since"; only a 409
   does now, the rest say unavailable and can be retried.
8. "Only the turns are shown" while the page retained the previous events on a failed
   refresh — two sentences now: nothing ever landed, or the entries are from the last read.
9. **Run summaries carry their identity as top-level `role` / `reviews_run_id` and no
   `result_metadata`; `runRole` / `reviewedRunId` read only the metadata, so every review
   on a summaries page was a worker turn of its own.** The conversation had this before
   this slice — the old timeline tab, fed full records, hid it. `lib/run-column.ts` reads
   both shapes; pinned in `tests/run-tree.test.mts`.
10. "0s" for a turn whose end precedes its start — a clock set back measures nothing;
    the duration is unknown, not instantaneous.
11. No scroll to the newest entry — restored from the timeline column, keyed on the entry
    count so a transcript poll does not yank a reader who scrolled up.
12. The conversation had no way to learn of a run the page knew — a review queued after
    its worker settled stayed invisible until something local bumped a nonce. It now
    derives the gap from the page's authoritative active-run read (`runsLackKnown`, the
    page's own pattern) and re-reads once per time the gap appears; the stream says
    "not shown yet" meanwhile.
13. A note whose answer was lost was told to "check the stream" while nothing re-read it —
    the page's history read is asked for on that path now.
14. "Not recorded" for a run's files, frozen for the session — a re-ask is offered there
    too; a run settled a moment ago may still be having its snapshot written.

Rejected, six, in three groups:

* **The note endpoint has no idempotency key** (rounds 2, 7, 8): a `POST` whose answer is
  lost after the server committed leaves the client unable to make a retry safe. True.
  The client now says the answer was lost rather than that the note failed, re-reads the
  history, and points at the stream; making the retry *safe* is a backend contract
  change (an idempotency key on `appendTaskEvent`) and is recorded here as the CR it
  would be, not fixed in a frontend slice.
* **Relative times do not tick** (rounds 3 and 9): `relTime` is computed at render and a
  card left on screen keeps saying "just now". True of every relative time in this app
  — the rail, the right column, the Today page — since they were written; a minute
  ticker is an app-wide change and would be one, not one card's.
* **A review nested under its worker sits at the worker's time** (round 9), so a note
  written between the two renders after both. That is the recorded ordering rule — 按工作
  单元排，不按进程启动次数排 (mockup v2 screen 2; lib/timeline.ts) — and the timeline tab
  ordered the same way. Not a defect of this slice; changing it is changing the decision.

Also raised and not counted above: the page's `onPosted` issues a read and the
`eventsGap` effect a second, and if the second fails while the first succeeds the later
answer is dropped (round 3). Pre-existing wiring of the page, untouched by this slice,
noted for whoever next works on `loadEvents`.

### Blockers

None.

### Next safe action

Commit the slice files on `feature/w8-session-ui`, staged explicitly, and push to
`private`. Then `docs/NEXT-STEPS.md` §一.3: the shell and top bar (four tabs, the
environment and CLI versions on the right), which also moves the projects entry to where
the design puts it. Update `docs/W9-mockup-alignment.md`'s table in the same commit.


## W9 — the shell: one top bar over every screen

`feature/w8-session-ui`, on top of `ac3bf39`, not yet committed. The last of W9's three UI
cuts (`docs/NEXT-STEPS.md` §一.3): the left sidebar becomes the mockup's top bar, and the
workbench — which had its own chrome — carries the same bar.

### What changed on screen

* **Four tabs, the mockup's four, in its order**: Today · 项目工作台 · 环境 · 技能库. The list
  is `SHELL_TABS` in `lib/shell.ts`, read by the shell and by its test, so the two cannot
  disagree about what the product presents.
* **The right-hand cluster**: the environment runs execute in ("本机 · <platform>", a label
  with the W3-a reason on hover, not a picker), the three CLIs with their versions, and the
  doors to the projects inventory and Settings, then language and theme.
* **The projects inventory left the tab row**, as `NEXT-STEPS` said it would once Today was
  rebuilt. It is a door on every screen (the icon at the right) and the link on Today; the
  mockup's bar has exactly four tabs and names the inventory only in Today's design note.
  The regression guard from W9's second cut still holds — `href: "/portfolio"` is in the
  shell — because what it guarded was reachability, not a tab.
* **The workbench carries the bar** and its rail header keeps only the refresh; the brand,
  language, theme and Settings it duplicated moved up.
* **The CLI versions are one of four sentences** (`cliVersionLineOf`): the version, "not
  installed", "could not be probed", "version unknown" — never a blank beside a name, never
  a zero. The probe's own outcome is separate: "probing…" and "could not be read".

### The probe, shared

`/api/runtime-capabilities` spawns each CLI twice (`--version`, sign-in). Three surfaces now
show its answer (the bar, Environments, Settings); each asking on its own would have been
twelve child processes per page load and two answers that could disagree on one screen.
`lib/runtime-probe.ts` is the one ask: a call joins one in flight, a fresh answer is reused
for a minute, `force` (the Settings refresh) asks now and drops the cached answer before
asking, a forced ask overtakes one in flight (the overtaken caller is told so and shows
nothing), every outcome — answer or failure — is broadcast to subscribers so the bar and the
page under it say one thing, and a failed probe is not cached.

### Files

`frontend/src/components/delivery-shell.tsx` (`TopBar` + `DeliveryShell`), `frontend/src/lib/shell.ts`
(new; tabs and the version sentence), `frontend/src/lib/runtime-probe.ts` (new; the shared ask),
`frontend/tests/shell.test.mts` (new; 4 tests incl. one source scan), `frontend/src/app/workspace/page.tsx`
(mounts the bar; rail header slimmed), `frontend/src/app/settings/page.tsx` and `frontend/src/app/environments/page.tsx`
(read the shared probe; Settings publishes each read as it lands, under a load token),
`frontend/src/lib/i18n/dictionaries/common.ts`, `frontend/package.json`, and this record. No backend file.

### Gate

* Frontend: `pnpm run test:control-plane` **371 collected / 370 passed / 1 skipped**
  (was 367 / 366 / 1). `tsc --noEmit` 0. `eslint` 0 on the changed files. `next build`
  14 pages.
* Backend: no backend file touched.
* Not exercised by hand: the browser check is owed for all three W9 UI cuts; this machine
  holds no task, and the bar's version strip needs the CLIs on the path to show anything
  but "not installed".

### Review state

Seven rounds of independent review on this slice, 13 findings: 11 confirmed and fixed,
two rejected — the same finding twice, with its reason below. **Every round ran as Kiro
standing in for codex** (`scripts/kiro-review.sh`, `gpt-5.6-sol`, `--effort max`): codex
answered `You've hit your usage limit` at every attempt — the documented substitution
(adr/0017), never alongside codex. The commit message says so.

What the rounds caught, nearly all of it about the one shared probe — which is the point
of sharing it: one place to get right instead of three.

1. A module-level cache that never refreshed: a CLI installed after the first probe stayed
   "not installed" until a reload. Now a one-minute reuse, and `force` asks again.
2. The bar probed on its own beside Environments and Settings probing on theirs — twelve
   child processes per page load, two answers per screen. One shared ask now.
3. A forced ask that failed kept the answer it was meant to refuse; the cache is dropped
   before asking, not after.
4. Settings' two reads under one `Promise.all`, so a failed overview threw away a probe
   that had succeeded while the bar already showed it — each read publishes itself now.
5. `force` ignored while an ask was in flight — a forced ask now overtakes it, and the
   overtaken caller is told so (`ProbeSupersededError`) rather than handed the older answer.
6. Only successes broadcast — a failed forced refresh left the bar showing the disowned
   version; failures are broadcast too, and the bar says "could not be read".
7. Settings kept the disowned cards after a failed forced probe; it clears them.
8. Settings' load had no token — an older load's late answer could overwrite a newer one;
   it has one now, and only the newest writes.
9. A failure held until the sibling read settled; each read publishes its own failure the
   moment it lands.

Rejected, the same finding twice (rounds 6 and 7): a request that never settles — a half-open
connection on `/api/runtime-capabilities` or `/api/settings/overview` — holds the shared
`inFlight` and Settings' spinner, so neither retries until a reload. True, and the request
layer's to fix: `request()` in lib/control-plane.ts carries no timeout for any call in this
app, and adding one is one change for every surface, not one screen's. The same class was
rejected on the stream slice for the same reason; recorded here so it is not read as unnoticed.

### Blockers

None.

### Next safe action

Commit the slice files on `feature/w8-session-ui`, staged explicitly, and push to
`private`. W9's three UI cuts are then all on the branch; what remains of W9 is D-5 step B
(the known-defects reader — its source is now `docs/known-defects.md`) and the browser
check of all three screens against the mockup. Then `docs/NEXT-STEPS.md` §二: W3-a.


## W10 — project kickoff: the agent reads once, asks, proposes; the owner adopts

`feature/w8-session-ui`, on top of `b86b4c6`, not yet committed. CR-54 (2026-09-12): the
owner created a project and asked for what the mockup already names on Today's card for
a project without a goal — "接入 Agora 时会跑一次项目初始化 … 让 agent 读一遍 →" — and
for the alignment loop after it ("需要跟我对齐的需求发给我，一起对齐"). The workspace's
own `/kickoff` interview, as a product feature.

### The flow, one round

1. **Start** — on the projects screen (and from Today's card, "让 agent 读一遍 →"), the
   owner writes a brief (required in practice for an empty repository) and clicks. The
   backend gathers what the repository can say — README head, top-level tree, recent
   git log — **in Python, not by letting the agent explore**: what the agent read is
   recorded beside what it concluded, and a part that could not be read is handed to it
   as "could NOT be read, do not guess". One `claude -p` call, parsed fail-closed like
   `tasks/clarify.py`, returns a charter draft, 4–6 questions only the owner can answer,
   whether the project has a UI, and read notes.
2. **Answer** — suggestion chips and free text; saved to the record. Changing an answer
   withdraws a proposal built on the old one.
3. **Propose** — a second `claude -p` call, from the answers: the charter to adopt and the
   first tasks in order. **If the project has a UI the first task is the mockup** —
   draw the screens under `docs/mockups/`, get sign-off, and every later acceptance line
   points at it. A UI project whose proposal forgot that task is *reported* as missing it
   (`mockup_task_missing`), never silently repaired.
4. **Adopt** — the owner edits the charter and unticks tasks; adopting writes the charter
   through the existing human path (`set_charter`) and creates the ticked tasks in the
   store, mockup first by priority. Nothing before this step wrote anywhere but the record.

### Where the record lives, and what a read of it says

`<root>/.agora/kickoff.json` — the project's own, versioned with it, the rule of adr/0004
for the CR Log and the decisions. A read reports one of: **absent** (never started — the
only state that offers "start"), **present**, **unreadable** (the file is there and could
not be read), **malformed** (read fine, not a record). The panel words each separately;
`lib/kickoff.ts` is where the step is decided, and a reason this build does not know is
"unreadable", not "start".

The charter still lives in the control-plane DB: adr/0005 decided it belongs in the
repository too, and that migration is separate work (recorded in the ADR's Consequences).
Adoption goes through the path that exists.

### Files

Backend: `agora/projects/kickoff.py` (new; context, two prompts, two parsers, the record),
`agora/api/kickoff_router.py` (new; GET / POST / answers / refine / adopt),
`agora/api/app.py` (mounts it), `tests/test_kickoff.py` (new; 130 tests, five skipped where
symlinks are not permitted — parsing, context, the record's readings, the whole flow through
`TestClient` with the agent stubbed, sealed/resumable adoption, the races, revision pinning,
502 on agent failure writes nothing, 404).
Frontend: `src/lib/kickoff.ts` (new; the step and the caveats), `src/components/kickoff-panel.tsx`
(new), `src/lib/control-plane.ts` (five calls), `src/app/portfolio/page.tsx` (panel under the
cockpit, `?project=` deep link), `src/components/today-focus-card.tsx` (the card's link),
dictionaries `portfolio.ts` / `today.ts`, `tests/kickoff.test.mts` (new; 4 tests incl. a
source scan), `package.json`, and this record.

### Gate

* Backend: `pytest tests/test_kickoff.py tests/test_projects.py tests/test_project_store.py
  tests/test_project_provisioning.py tests/test_tasks_controlplane.py tests/test_workspace_tasks.py`
  — **260 passed, 6 skipped** (`test_kickoff.py` alone 125 passed, 5 skipped). The full
  suite (40 minutes) was not re-run for a slice that adds a router and touches no
  existing backend module but `app.py`.
* Frontend: `pnpm run test:control-plane` **376 collected / 375 passed / 1 skipped**
  (was 371 / 370 / 1). `tsc --noEmit` 0. `eslint` 0 on the changed files. `next build`
  12 pages (the app has 12 routes; earlier entries' "14" counted two build lines that are
  not pages).
* Not exercised end to end with a real agent: the first real run is the owner's, on
  NichiMate, with their brief — that is W10's first exit line, and it is theirs to check.

### Review state

**Fifty-three rounds. Codex reviewed rounds 2, 3 and 40; kiro (gpt-5.6) stood in for rounds 1, 4–38 and 41 onward —
every one of those a Codex usage-limit refusal the same minute, the substitution adr/0017
allows and the commit message names. Round 29's first kiro report came back empty after
44 seconds; it was run again on the same, unchanged tree and came back with two findings,
both recorded as round 29. Round 39 went wrong in the other direction: Codex's quota had
come back and it reviewed, but the runner's "over the limit → kiro" check matched the words
"usage limit" quoted from this very file in Codex's output, so kiro ran on the same tree
as well and Codex's own report was not kept — two reviewers on one diff by accident, the
thing adr/0017 says not to do on purpose. Kiro's four findings are round 39; Codex's are
unknown, which is why round 40 was Codex again, captured whole — five findings, all
fixed. Round 42 was run while two tests were red — two expectations that the round-41
fix had rightly changed, not code — because the runner's green-gate only guarded the
record update, not the review; the findings stand on the code as it was, and the tree was
green again before round 43.** 182 findings: 143 fixed (numbered below), three rejected
(named after them, one of them raised twice), three deferred with CR rows (named after
that; two of them raised repeatedly — CR-57 eighteen times, CR-56 eleven), and one
carried to the backlog unfixed (workspace CR-58, below). Round 53 brought the two
repeats and that one.

**Stopped here, and the stopping is the finding.** The gates were green from round 6 on;
the rounds after that each turned up two or three real edge cases of the record's
honesty, and each was fixed and re-reviewed — for a surface the owner could not reach,
because the slice was never committed and the backend never restarted. That is the
inversion adr/0020 exists to stop, done a third time, and the owner said so ("what are you
doing all day?"). From this commit the owner's first real run on NichiMate decides the
next cut; a review loop on this slice does not resume until they have been stuck on it.

What the rounds caught, in order — nearly all of it about `adopt`, the one step that
commits, which is where the honesty of the whole flow is decided:

1. (round 1, P1) `adopt` was neither atomic nor idempotent: a retry after a failure
   halfway, or after a lost response, created the tasks again. Made **sealed and
   resumable**: the first call seals the approved charter and tasks (each with a stable
   key) into the record before any side effect; each task is created with its key in its
   metadata and the store is asked for the key before creating; a retry resumes from the
   sealed payload and ignores its own body; a completed adoption answers 409.
2. (round 1) `refine` read the record, called the agent for minutes, and wrote the whole
   record back — over answers saved meanwhile. It re-reads before writing, under the
   project's lock, and refuses (409) if the answers, the questions, the round or the
   status moved.
3. (round 1) A record was "available" on the strength of a status field; the UI would
   have crashed on `questions.length`. `_is_record` now checks every field the flow
   dereferences, per status, down to the nested strings.
4. (round 1) The prompt was a command-line argument; a brief plus a README head can pass
   the Windows limit (~32K). It travels on stdin; the README head is capped in characters
   as well as lines.
5. (round 1) Duplicate question ids from the agent would have shared one answer — renamed
   `id-2`, never merged.
6. (round 1) The adopted summary counted the proposal's tasks, not the created ones.
7. (round 2, P1) Task identity by title: two approved tasks with one title lost the
   second; a crash between "created" and "recorded" duplicated on retry. Keys, and the
   store consulted by key.
8. (round 2, P1) A README that is a symlink was followed — a project could point it at a
   file outside the root and have it sent to the agent. Read without following links,
   regular files only, and a link is named as unread.
9. (round 2) Blank answers counted as answered; an empty brief on an empty repository
   asked the agent to analyse nothing. Both refused.
10. (round 3, P1) The analyst call ran `claude -p` with default permissions from the
    project root. It now uses the runtime registry's audited read-only shape: plan mode,
    safe mode, no session left behind — the analyst is handed the context; it is not to
    act on the project.
11. (round 3, P1) Task keys restarted at 1 every round, so a round after an adoption
    found the previous round's tasks under its keys and skipped its own. Keys carry a
    round id.
12. (round 3, P1) Two requests could both read "proposed" and both adopt. A per-project
    lock (this control plane is one process over one SQLite file) is held across the
    whole adoption and around every read-compare-write of the record; the second request
    sees what the first wrote.
13. (round 3, P1) An old tab's answers could walk an adopted record back to "answered"
    and reopen adoption. Terminal records refuse answers; ask again starts a new round.
14. (round 3) "Mockup task present" was accepted anywhere in the list; the rule is
    *first*, because adoption turns order into priority. Checked as first.
15. (round 4, P1) A `threading.Lock` taken on the event-loop thread and held across an
    `await` — the first request could never resume to release it, the second waited
    forever. Every locked section is now one synchronous function run in the threadpool,
    lock held inside it.
16. (round 4, P1) A slow first ask finishing after a faster second one overwrote the
    newer round — answers, proposal, adoption receipt gone, tasks left in the store. The
    ask remembers the round it started over and refuses (409) if it moved.
17. (round 4, P1) Crash recovery looked at the first page of tasks only. It pages
    through all of them.
18. (round 4) The record kept counts of what the agent was shown, not the text; the
    context text is in the record now, so the draft can be checked against it later.
19. (round 4) `needs_mockup` was read with `bool()` — `"false"` is true. A non-boolean is
    a refusal.
20. (round 5, P1) The ask's "did the record move" check compared the round id only;
    answers, a proposal or an adoption inside the same round were overwritten, and an
    absent record was indistinguishable from a malformed one. It compares a fingerprint
    of the whole read now.
21. (round 5, P1) `.agora` or the record file as a symlink or junction: the write would
    land wherever the link points. Refused, as `kickoff_path_unsafe`.
22. (round 5) The second pass re-read the repository and recorded nothing of it; the
    proposal's own context is on the record beside the first pass's.
23. (round 5) A second pass saying "no UI" lifted the first-task-is-mockup rule for a
    project the first pass saw screens in. Either pass saying UI means UI.
24. (round 5) A failed action left the panel on the step it was on — after an adoption
    whose answer was lost, every retry said "already adopted" and nothing re-read. Any
    failed action re-reads the record.
25. (round 6, P1 ×3) The multi-tab family in one: a tab still showing round one's
    proposal could adopt it under round two's keys; a tab saving one answer overwrote a
    sibling tab's other answer; a tab starting over compared against what was on disk
    when *its request arrived*, not what *it* had seen. One mechanism closes all three —
    **the record carries a `revision`, every mutation names the revision the caller saw,
    and a stale one is refused (409 `kickoff_stale`)**; the panel re-reads on refusal.
26. (round 6, P1) The temporary file was written without a link check — a symlink at
    `kickoff.json.tmp` would be truncated wherever it pointed, then renamed over the
    record. Directory, file and temp file are each refused when they are links.
27. (round 6, P1) "Refuses junctions" was said and not done: `lstat` reports a Windows
    junction as a directory. `is_junction()` is asked as well as `is_symlink()`.
28. (round 6) The mockup warning was judged on the proposal, not on what the person
    ticked — unticking the mockup was exactly the case it should catch. Judged on the
    selection now.
29. (round 6) The parsers skipped malformed items and ignored the contract's counts; one
    usable question out of four became "one question". Any malformed item, or a count
    outside 3–8 questions / 1–8 tasks, fails the whole answer.
30. (round 7) "Refuses junctions" was still only true on Python 3.12+: `Path.is_junction`
    does not exist on the 3.10/3.11 the project supports, and the `getattr` fallback made
    the guard silently a no-op there. On Windows the reparse-point attribute of `lstat` is
    asked directly, on every supported interpreter.
31. (round 7, P1) The revision was a counter, compared alone, while the record is a file
    versioned with the project: two branches can hold "revision 4" over different answers,
    and a tab that read one passed the check against the other. **The revision is now a
    hash of the record's content**, computed at write time, so it cannot be stale against
    what it names; a stored revision that does not match its content reads as `malformed`
    (a hand edit, a merge), and the fingerprint that was defined and unused is the same
    thing under another name.
32. (round 7) A resumed adoption wrote the sealed charter again — over a charter the person
    had edited through the charter editor while the adoption sat half done. The resume
    writes the charter only when the goal is still empty; the tasks it finishes as before.
33. (round 7) An `adopt` whose response was lost re-read the kickoff record but not the
    cockpit or the task list, so a page that had in fact adopted showed the old charter
    until a reload. The failure path of `adopt` now refreshes those too.
34. (round 8, P1) The resume's "write the charter only if the goal is still empty" (item
    32) was a guess, and wrong for a project that had a charter before this kickoff: a
    crash before `set_charter` left the old goal in place, the retry saw a non-empty goal,
    skipped the write, finished the tasks and said *adopted*. **The sealed payload now
    records `charter_written`**, set right after the write; a resume writes the charter
    when the flag is unset and never otherwise. Guessing from state replaced by recording
    the step.
35. (round 8, P1) The record was not bound to a project, and the registry allows two
    project ids on one root: project B would read A's record from the shared directory,
    could adopt A's proposal into B's charter and tasks, and the shared file then said
    *adopted* for A too — with A's control plane holding nothing. The record carries its
    `project_id`; a read for another project answers **`foreign`** (a fourth reason
    beside absent / unreadable / malformed, with its own sentence in the UI), and every
    mutation refuses it (409 `kickoff_foreign`) — a start would erase theirs, an adoption
    would mark theirs done. The per-project lock is now keyed by the resolved root, since
    two locks over one file would be no lock.
36. (round 8) Task kinds outside the contract were rounded to `custom` — `"mock-up"`
    became a custom task and was then reported as "no mockup task". Refused by name
    instead; and the string-list helper that trimmed to a cap and skipped non-text items
    without saying so now refuses the list, consistent with "one bad item fails the
    answer" (item 29), which the code had claimed and not done for those lists.
37. (round 8) The adopt note said "nothing before this was written", which was true of
    the control plane and false of the repository: `.agora/kickoff.json` is written from
    the first step. The note now says both.
38. (round 9, P1) Item 34's flag was written *after* the charter, and a crash between
    the two made the resume write the sealed charter over an edit made meanwhile — the
    very thing item 32 promised not to do, and the comment called it "the cheap side".
    Neither order of two writes can be right without a transaction, so the resume now
    **looks instead of trusting a flag**: the seal also holds the charter as it was
    *before* adopting, and on a resume the project's current charter is one of three
    things — still the "before" (write the sealed one now), already the sealed one (the
    flag write was what the crash skipped), or neither (a person edited it: theirs
    stands, and the record says `charter_kept_edit`, which the panel turns into a
    sentence instead of "the charter is set").
39. (round 9, P1) `start` over a record in `adopting` replaced it with a fresh questions
    record — and with it the only copy of the approved task that had not been created,
    leaving the created one an orphan. Refused now (409 `kickoff_adopting`), before the
    agent is spent and again under the lock; resuming is the way out, and there is no
    path that discards a sealed adoption, on purpose.
40. (round 9) Task titles were cut at 300 characters and descriptions at 20,000, silently
    — the acceptance line at the end of a long description would reach the task store
    missing, with nothing saying so. Refused over the cap, like every other malformed
    field since item 29.
41. (round 10, P1) The task key was round + position, and the record is versioned with
    the project: two branches can carry the same round with a different first task, and
    the task created under `r1-1` on branch A would be found as branch B's and B's never
    created — with the record marked adopted. **The key now carries a hash of the task's
    content** (title, description, kind), so "found by key" means "this task, as approved".
42. (round 10, P1) The restart block kept the brief typed into it across record changes:
    a tab that re-read after a 409 held its old brief and would send it with the *new*
    revision, which the server rightly accepts — another tab's round overwritten through
    the front door. The block is keyed by the record's revision; when the record moves it
    remounts and the stale brief is dropped.
43. (round 10) The parsers took `str()` of text fields — `{"zh": "画原型"}` became the text
    of a Python dict as a task title, `123` became "123" as a goal — while claiming to fail
    closed. Every text field is now required to be text, or the answer is refused.
44. (round 11) "The same request again resumes" was not true: the seal moved the revision,
    so the very request whose answer was lost carried a stale one and got 409 before the
    sealed payload was read. A resume is now exempt from the revision check — its body is
    ignored and it is idempotent — so a retry means what it says; a first adoption is
    pinned as before.
45. (round 11) A task without `kind` became `custom` and one without `description` became
    "" — the first could not be the mockup task, the second had no "done looks like".
    Both required now; and the charter to *adopt* must carry at least one success
    criterion (the draft may not: the questions settle them).
46. (round 11) The prompt filler substituted tokens one after another, so a brief that
    contained the text "{context}" had the repository pasted into it — and the record
    said the agent was shown the brief as written. One-pass substitution, with a test that
    puts a token inside a value.
47. (round 11, follow-through) `adopt` met a foreign record through the generic
    "not available" refusal after item 44 reordered the reads; it answers
    `kickoff_foreign` there too, the one name for that case.
48. (round 12, P1) The link guard on the write was check-then-write: a symlink planted at
    the temporary file's fixed name between the look and `write_text` would have been
    followed. The temporary file now has a fresh random name and is created with
    `O_EXCL` (and `O_NOFOLLOW` where the platform has it) — nothing placed there first can
    be opened through. **What remains is said, not implied**: `.agora` and `kickoff.json`
    are inspected just before the write, and a process with write access to the project
    root that swaps `.agora` for a junction inside that gap is not stopped — closing that
    takes a directory handle (`openat`) Windows does not offer here. Workspace CR-56
    (`defer`) names the condition under which that matters.
49. (round 12) The adopt route accepted what the parsers refused: a whitespace goal, no
    success criterion (the editor lets the person clear the list), tasks with any kind or
    an empty description. The request models hold the person to the same contract (422),
    and the adopt button is off — and says why — while the goal or the criteria are missing.
50. (round 12) The README was probed as "a regular file, not a link" and then opened by
    path — two looks with a gap. It is now opened first and checked on the descriptor
    (`fstat` against `lstat` of the path, `O_NOFOLLOW` where it exists); a link at the
    path, or a different file behind the descriptor, is refused and named as unread.
51. (round 12) A README.md that was a directory or a link stopped the search: a readable
    README.txt behind it was never tried and the record only mentioned the first. Every
    candidate is tried until one reads; each that could not be is named.
52. (round 13, P1) "Sealed before any side effect" was not durable: the record was
    renamed into place without `fsync`, while the tasks went into SQLite (which syncs).
    A power cut after the tasks' commit could bring the record back as the *previous*
    version, offering the adoption again over tasks it did not know about. The temporary
    file is flushed and fsynced before the rename, and the directory after it where the
    platform allows (POSIX; Windows offers no directory handle here, which is said).
53. (round 13) The question and proposal forms were keyed by question ids + saved
    answers and by the proposal's text, so after a 409 re-read a tab kept its typed text
    and could save it under the *new* revision — another round's questions with the same
    ids, or a word-for-word repeated proposal. Every form is keyed by the record's
    revision, like the restart block since item 42.
54. (round 15, P1) Item 52 synced the file and, on Windows, skipped the directory —
    saying so — which left the rename itself undurable there, and this control plane
    runs on Windows. The rename now goes through `MoveFileExW` with
    `MOVEFILE_WRITE_THROUGH` on Windows (the platform's "return once it is on disk"),
    and `os.replace` + directory `fsync` elsewhere.
55. (round 15) A README that was not UTF-8 (a Shift-JIS one is ordinary on this machine)
    was decoded with replacement characters and reported as read. It is now named as
    unread ("not UTF-8 text") and the next candidate is tried; no encoding is guessed.
56. (round 15) The panel's reads and mutations could land out of order: a re-read started
    by a failed adopt could arrive after the resume that succeeded and put the panel back
    on "adopting". Every request that may set the record takes a ticket, and only the
    newest ticket's answer is applied.
57. (round 15) The JSON object was cut "from the first brace to the last", so prose with
    braces of its own before the object (an example, a template) made a fine answer fail
    and cost another agent call. The first object that *decodes* is taken, wherever it
    starts.
58. (round 16, P1) The write refused links and the read did not: `read_kickoff` opened
    the path as given, so a `.agora` or `kickoff.json` that was a link would have been
    read from wherever it pointed. The read now honours the same boundary — a linked path
    answers **`linked`** (a fifth reason, with its own sentence in the UI: neither read
    nor written), and the content is read on a checked descriptor like the README.
59. (round 16) After a *completed* adoption, the very request that adopted — carrying the
    revision from before it — heard `kickoff_stale` instead of the promised
    `kickoff_already_adopted`, because the revision was looked at before the status.
    "Already adopted" is now the first answer.
60. (round 16) The README was read up to a byte limit and then decoded strictly, so a
    multibyte character cut by the limit made a fine Japanese README "not UTF-8". An
    incremental decoder holds back a cut sequence; an invalid byte inside still refuses.
61. (round 16) The answer boxes stayed editable while a save or the second pass was out;
    the answer landed as a new revision, the block remounted on it (item 53), and text
    typed meanwhile was dropped without a word. Read-only while a request is out.
62. (round 17, P1) The read stopped at 4 MB and the write had no ceiling: an answer of a
    few million characters would have been written over a good record and read back as
    malformed, with no way on. Answers are bounded per item and in number, charter items
    per item, and **the write refuses any record larger than the read would take back**
    — before touching the file.
63. (round 17) The descriptor read was one `os.read`, and a short read (a network share)
    would have passed as EOF: a partial README sent as whole, a whole record called
    malformed. It reads to the limit or EOF.
64. (round 17) A task in a record could lack `description` (the validator defaulted it),
    while the adopt route requires one — a resume of such a record would have been 422
    forever. The record validator requires it, as the parser has since item 45.
65. (round 17) The extractor took the first object that *decoded*; an example object in
    the prose before the answer would have been taken and the answer behind it refused.
    It takes the first object carrying the contract's key (`questions` / `tasks`), and
    only failing that the first that decodes, so the refusal names what is missing.
66. (round 18) The record read stopped at its bound without asking whether the file went
    on: a valid record followed by a tail the flow never wrote (a merge's conflict
    marker) would have read as present, and the next save would have replaced the whole
    file, tail and all. One byte past the bound is asked for; a record past it is
    `malformed`, read entire or not at all, while the README head stays a head by design.
67. (round 19, P1) An adopted record was believed on the word of the file: the record
    travels with the repository, and on a clone — or after a database not restored — the
    panel said "charter set, tasks created" over an empty control plane, and `adopt`
    answered "already adopted". **The read now says what this control plane holds** of
    an adopted record (charter present or not, N of M recorded tasks found by id), the
    panel shows the counts instead of the claim when they fall short, and adopting again
    re-applies the seal here: what is missing is created (found by key first), what is
    present is left alone, the first adoption's time stands.
68. (round 19) The agent's output was decoded with replacement characters, so a CP932
    console or one broken byte would have put "�" into a charter that parsed, was adopted,
    and reached the control plane as text the agent wrote. Bytes, decoded strictly;
    not UTF-8 is a refusal.
69. (round 19) The parser put no length on question ids while the answers route holds
    them to 128 — an interview whose ids the route refuses could never be answered. One
    constant, checked before the interview is shown.
70. (round 20, P1) "The record says what the agent was shown" was a claim the agent could
    falsify: plan mode still allows the read tools, and the process ran with the project
    as its working directory, so it could go and read a `.env` or the source past the
    README head on its own. **The agent now runs with `--tools ""` and an empty scratch
    directory as cwd** — no tools, nothing at hand but the prompt — with a test on both.
71. (round 20) A duplicate question id made unique by a suffix could grow past the cap
    item 69 introduced. Checked again after the rename.
72. (round 20) The brief stayed editable while pass one ran, and the restart brief
    likewise; the request carried the text at the click and the later edits vanished
    with the step. Read-only while the request is out, like the answers since item 61.
73. (round 20) The proposal's charter and ticks stayed editable while `adopt` ran; the
    seal held the click-time snapshot and edits made meanwhile vanished with the form.
    Read-only while the request is out.
74. (round 20) On Windows every reparse point was taken for a link, and OneDrive
    placeholders carry one: a project under a synced folder would have read `linked`
    and been unwritable. Only the symlink and mount-point (junction) reparse tags count.
75. (round 20) `git log` was decoded with replacement characters; a repository whose
    log output is Shift-JIS would have reached the agent with "�" for words, reported as
    read. Bytes, decoded strictly; not UTF-8 is named as unread.
76. (round 21, P1) Item 67's "charter present" meant "some goal is there": after a branch
    switch a leftover charter A counted as the approved B, re-applying left A and then
    said complete. The view now says `matches` / `differs` / `absent` against the
    approved charter; an ordinary edit after the adoption (`differs`, tasks all present)
    is not incomplete, and a re-apply sets the charter to the approved one unless it
    already matches — the panel says so before the click.
77. (round 22) The proposal form was locked only while *its own* request was out; a
    refine started from the answers above it moves the record too, and edits made while
    it ran vanished with the remount. Locked while any request is out.
78. (round 22) The parsers put no length on charter fields while the adopt route holds
    each to 2,000 characters — a proposal the person could not adopt as it stood. One
    constant, checked in the parser.
79. (round 22) The agent's scratch directory was left behind when the call timed out or
    failed to launch. Removed in a `finally`.
80. (round 23, P1) Item 39 refused a start over `adopting` and not over an *adopted*
    record this control plane has not applied (item 67's clone case) — whose seal is
    likewise the only list of what to create here. Refused now (409
    `kickoff_not_applied_here`, with the counts), and the panel offers no restart there
    until the seal is applied.
81. (round 23) After a refused save the panel unlocked its forms while the re-read was
    still in flight; text typed in that gap was dropped by the remount the re-read then
    caused. The re-read is awaited before the forms unlock.
82. (round 24, P1) The revision was checked on one read of the file and the record taken
    from another: a `git checkout` between the two — the one writer no process lock
    reaches — would have had a save from page A's record written onto record B. **Every
    mutation now decides on one snapshot**: read once under the lock, check that read,
    mutate that read, write; a source test keeps the two-read helpers from returning.
    The gap between that one read and the rename stays open to an outside writer, and
    CR-56 now names `git checkout` beside the junction swap.
83. (round 24) The restart block was locked only during a restart, so it could be
    started while a save or a second pass was out, and whichever finished first unlocked
    the forms under the other. Locked while any request is out, like everything else.
84. (round 24) The panel required a brief for every start while the server requires one
    only over an empty repository — a project with a README could not be started from
    the UI on its repository alone. The buttons no longer demand it; the server's 422
    over an empty repository is shown as before.
85. (round 25, P1) Item 76's "differs with all tasks present is an ordinary edit" had a
    hole: a charter-only adoption has no task to prove itself by, so a sealed B arriving
    over a charter A read as complete, and a restart could then erase B's only copy.
    Inference from the charter and the count is gone: **the adoption writes a marker
    with the round id into this control plane's settings**, and "applied here" is that
    marker — a differing charter is an edit where the marker is, another tree's where
    it is not; a charter-only adoption from elsewhere is incomplete until re-applied.
86. (round 25) Any error while inspecting a path for links was reported as "a link" —
    a disconnected share would have sent the person to replace a real directory. The
    error is the caller's now: the read says `unreadable`, the write "could not be
    inspected"; only a symlink or a junction is `linked`.
87. (round 26, P1) Item 85's marker held the round id, and two branches can fork one
    questions record and adopt different things under the same round — the other
    branch's charter-only seal would have read as applied here. **The marker is a
    fingerprint of the seal** (approved charter and task keys); "applied here" means this
    seal.
88. (round 26) A charter cleared through its editor after an adoption applied here was
    never restored: the re-apply wrote the charter only when the adoption was someone
    else's. An absent charter is written back whoever applied it.
89. (round 26) The halfway view counted what the record had written down, and the one
    crash the resume exists for is "created, not yet recorded". For an `adopting` record
    the backend now counts the approved keys the store holds and the panel shows that.
90. (round 27) A re-apply that wrote the approved charter here left the record's
    `charter_kept_edit` from the machine it came from, and the panel said "the charter
    was not overwritten" of a charter that had just been written. Cleared when written.
91. (round 28) The key scan the resume relies on paged with OFFSET, and two pages are
    not one snapshot: an ordinary task inserted between them shifts the rows, and the
    one "created, not yet recorded" task could fall off the seam and be created twice.
    One statement now, with a ceiling past which the scan refuses rather than resumes on
    a partial list.
92. (round 29, P1) Item 44 exempted a resume from the revision check altogether, so a
    page still showing branch A's half-done adoption after a checkout to B could resume
    — and execute — B's seal. A resume is pinned to *this seal*: the record's current
    revision, or the revision the proposal had when it was sealed (kept in the seal, so
    the request whose answer was lost still goes through); anything else is stale.
93. (round 29) The README was looked for under five fixed spellings; `Readme.md` on a
    case-sensitive filesystem was neither read nor named as unread, while the tree
    listing still counted it as content. The directory is listed and matched without
    regard to case.
94. (round 30) Starting a new round over a fully applied adopted record replaced the
    file, and with it the receipt — the approved charter the resume note promised was
    "still in the record", the task keys and ids, the time. A new round now carries the
    receipts of the adoptions before it (`previous_adoptions`), never trimmed.
95. (round 31) The receipt kept key, title and kind of each approved task and dropped
    the description — where the acceptance lived. The whole approved task travels.
96. (round 31) A `.git` that could not be inspected (an ACL, a dropped share) was
    reported as "not a git repository", and the agent would have drafted on that as a
    fact. Named as unread: "could not be inspected".
97. (round 32) Item 94's "never trimmed" receipts would, round after round of long task
    descriptions, have carried a new record past the write bound — a start refused after
    the agent had already run, with no way to make room. The receipts now have a byte
    budget a quarter of the record's; past it the oldest are dropped and **the record
    counts how many were dropped**, so the loss is a stated fact.
98. (round 32) The tree's "(N more)" counted the raw directory, `.git` and
    `node_modules` included — a false fact handed to the agent. It counts what was left
    out of the list that is shown.
99. (round 33, P1) The record validator held a seal to field *types* while the adopt
    route — which a resume sends the seal back through — holds it to the contract: a
    versioned record with a blank description, a kind outside the set or a charter with
    no criterion read as resumable and was refused on every click. The seal is validated
    to the same contract; such a record is `malformed`, said once.
100. (round 34, P1) "What this control plane holds" counted the record's *written-back*
    task list, and a re-apply that failed halfway writes that list back shorter than the
    approved one — the next look would have said complete over a missing task. The
    approved list is the measure now, for adopted and adopting alike, and the store
    (found by key) is the evidence.
101. (round 34) A README directory listing that failed was "(none)". Named as unread.
102. (round 35, P1) The first kickoff creates `.agora`, a write to the project root's
    directory that was not synced while the rename inside it was — a power cut could
    have taken the directory with the seal in it, after the tasks were in SQLite. Synced
    on POSIX when created (Windows journals the creation).
103. (round 35) The early revision check treated unreadable, malformed and linked as
    "no record", so a page that had seen none ran a 240-second agent call over a file
    that had since broken, to be refused at the commit. Refused before the call.
104. (round 35) The README head's "(N more lines)" counted the lines of a cut read as if
    they were the file's. Over a cut read it says the file goes on unread; the count is
    stated only for a file read whole, and the notes are appended after the size cap so
    the cap never eats them.
105. (round 36, P1) A seal that fit the record bound could grow past it on the writes
    that follow side effects (task ids, the time stamp) — an adoption stuck half done,
    a restart refused, every retry failing the same way. **Before the seal, the record
    is projected to its adopted shape and refused if that would not fit** (409
    `kickoff_record_too_large`), with nothing written.
106. (round 36) `.git` was probed following links and its gitfile form was never looked
    at: a linked `.git`, or a `gitdir:` pointing outside the project, would have had
    `git log` read another repository's commits into the prompt. Both are named as
    unread and not followed; a gitfile is followed only inside the project.
107. (round 37) A project root that was gone (moved, deleted, misregistered) was
    recreated by the record write as an empty tree holding one file — the registry split
    from the real repository. The write refuses a missing root; only `.agora` is created.
108. (round 37) The seal's lists were not held to the adopt route's counts (20 items,
    20 tasks): a versioned record past them read as resumable and was refused on every
    click. Held to the same counts; such a record is `malformed`.
109. (round 37) A re-apply trusted a recorded task by its id while the view counts by
    key: a partially restored database with the id under an ordinary task and no key
    would have looped — present by id, absent by key. Identity is the key, both places.
110. (round 37) The extractor took the first object *with the contract's key*, so an
    example shaped like the answer (`{"questions": []}`) before the real one was taken
    and refused. Every candidate with the key is tried in turn; the first that parses is
    the answer, and a refusal is about the first.
111. (round 38, P1) On a re-apply the charter was written first and the "applied here"
    marker only after the tasks, so a retry after a task failed saw "not applied", and
    wrote the approved charter a second time — over an edit made meanwhile. The marker
    goes down with the charter.
112. (round 38) Twenty approved tasks are allowed and the priority formula ran out at
    the seventeenth (the rest all 0), so the store's tie-break reordered the tail.
    Twenty distinct priorities, in the approved order.
113. (round 39, P1) Item 111 put the marker "right after" the charter — two writes, and
    a crash between them was the same gap one row further down. **The charter and the
    marker are one SQLite transaction now** (`ProjectStore.set_charter_with_setting`,
    the one store change in this slice): written together or not at all.
114. (round 39) A re-apply whose last task reached the store while the record write
    after it failed read as complete next time — every approved task present by key —
    and "already adopted" would have left the receipt short for good. The receipt is
    part of complete; the adopt route then finds that task by key, records it, creates
    nothing.
115. (round 39, P1) `git log` inherited `GIT_DIR` / `GIT_WORK_TREE` from whatever
    started Agora, so after this project's `.git` had passed every check git could read
    another repository. Run in an environment scrubbed of `GIT_*`.
116. (round 39, P1) The per-directory lock was keyed by the normalised path string, and
    one Windows directory reached as a drive letter and as a UNC path is two strings —
    two locks over one file. Keyed by the directory's identity (device and file id)
    where the platform gives one; the path only where it does not.
117. (round 40, Codex) The kickoff's charter caps (2,000 per item) were wider than the
    existing charter editor's (500 per list item): a charter that came through the
    kickoff could not be saved again through the editor. The editor's caps, everywhere.
118. (round 40, Codex) The cleared "kept a person's edit" note lived in memory until the
    task stage; a failure there and the retry — seeing "applied here" — wrote the old
    note back. Persisted before the task stage.
119. (round 40, Codex) `json.loads` on a record can raise a plain `ValueError` (an integer
    past the interpreter's digit limit) or `RecursionError` (nesting), neither caught:
    a 500 where the read contract promised `malformed`. Caught.
120. (round 40, Codex) A request whose answer was lost left its failure banner over the
    state the re-read then showed. Withdrawn when the re-read shows the record moved;
    kept when nothing did, since then the failure is the news.
121. (round 40, Codex) The refusals used an `error`-keyed shape the shared client does
    not parse, so the panel showed raw JSON. One shape — `code`, `message`, extras — with
    a sentence per code.
122. (round 41, P1) The receipt check (item 114) asked only whether each key was named;
    a receipt carrying another machine's task ids, over a re-apply that crashed before
    the record write, read as complete with the wrong ids in it for good. The receipt
    must name, per key, the task this store holds under it; a re-apply repairs it.
123. (round 41) `Path.resolve()` on a gitfile's target can raise (a share that dropped,
    a symlink loop) — a 500 where "could not be read" was promised. Named as unread.
124. (round 41) `is_empty` took any entry beginning with "…" for the tree's cut marker,
    and a file named `…仕様書.txt` — ordinary in Japanese — made the repository "empty".
    The marker is matched whole.
125. (round 42, P1) Item 92 let a request on the pre-seal revision resume, and two tabs
    can hold that revision with different edits: the one that sealed second would have
    "resumed" the first's seal as its own, its choices silently replaced. On the
    pre-seal revision the body has to be the seal's (409 `kickoff_seal_mismatch`
    otherwise); the request that sealed still goes through.
126. (round 42, P1) A re-apply over a charter that already matched wrote neither charter
    nor marker; a task failure, an edit, a retry — and the edit read as another tree's
    charter and was overwritten. The marker is written on its own in that case, before
    the task stage.
127. (round 42) The size projection (item 105) spelled the flags `true`, a byte shorter
    than `false`: a record at the exact bound could pass the check and fail the write.
    The projection takes the longer spellings.
128. (round 43) After the charter-and-marker transaction on a re-apply, a record write
    that failed left the file saying "kept a person's edit" of a charter that had just
    been written — and a charter-only adoption then read as complete, so nothing would
    ever correct it. **The marker now says what was done** (`…:wrote` / `…:kept`); a
    record whose note disagrees with the marker is not complete, and the next adopt
    rewrites the note from the marker.
129. (round 43) The agent's JSON was decoded catching `JSONDecodeError` alone; nesting
    past the recursion limit or an integer past the digit limit was a 500. An integer
    past the limit is a decode failure like any other; nesting past the limit is refused
    outright rather than rescanned from every inner brace (a hundred thousand of those
    took over a minute in the test).
130. (round 44, P1) The task-scan ceiling (item 91) was met *after* the seal, and a read
    of such a record raised too — an adoption stuck half done with no read to see it by.
    The scan runs before the seal (refused, nothing written), and a read answers with
    `scan_failed` said and nothing counted.
131. (round 44) A resume of a half-done record trusted the task receipts it carried; a
    clone of such a record carried another machine's ids, and the first resume would
    have said adopted with the tasks missing. Whatever the status, a receipt counts only
    when this store holds the task under its key with that id.
132. (round 44) Every write refusal was reported as "the path is unsafe" — a record too
    large to read back sent the person to inspect `.agora`. The refusals carry their own
    names (`kickoff_record_too_large`, `kickoff_project_root_missing`, `kickoff_path_unsafe`).
133. (round 45) The panel ignored item 130's `scan_failed` and showed its placeholders
    ("charter absent, 0 tasks") with a re-apply button the server refuses. It says the
    scan failed and offers nothing.
134. (round 46, P1) A task under an approved key was taken for the approved task by the
    key alone, and a key is a string any ordinary task write can carry: a stranger under
    it would have let the approved task go uncreated and the receipt name the stranger.
    A task counts only if it *is* the approved task (created by an adoption, approved
    title and kind); a stranger is a conflict (409 `kickoff_key_conflict`, said in the
    view too), neither taken nor doubled.
135. (round 46) The half-done state showed placeholder counts and a resume the server
    refuses when the scan had failed (item 133 handled the adopted state only). Said
    there too, nothing offered.
136. (round 47) A project root that was gone read as `absent` — "never started" — and a
    start on that reading ran the agent and failed at the write. A missing root is its
    own reason (`root_missing`, the seventh reading, with its own sentence), refused
    before the agent.
137. (round 47) The panel ignored item 134's `key_conflict` and offered a resume or a
    re-apply the server refuses. Said, with what to do about the task, nothing offered.
138. (round 48) Item 134's "is the approved task" compared title and the recorded kind
    only; a task with the approved title and another description (the acceptance) or
    another actual kind would have been claimed. The whole approved content is compared.
139. (round 49) The key-conflict hint told the person to "rename or remove" the stranger,
    and the product can do neither to a task; it can cancel or archive one. A cancelled
    or archived stranger no longer blocks (the approved task is created beside it), and
    the hint says exactly that.
140. (round 49) A project root reached through a symlink — ordinary on POSIX — read as
    `root_missing` after item 136. The root itself is followed; the boundary is inside it.
141. (round 50, P1) Item 139 collapsed several tasks under one key to "the active one":
    an archived stranger beside an approved task the person had since cancelled won the
    key, the approved task read as missing, and a re-apply would have revived it. The
    scan keeps every task under a key and the choice is made against the approved
    task: the approved one wins whatever its state; a stranger counts only while
    active, and then as a conflict.
142. (round 51) Start, answers and refine answered with the record alone, and their
    final re-read can find an adopted record (a checkout between the lock and the read)
    — the panel then said "charter set, tasks created" on the file's word. Every route
    answers with the control-plane view; and a panel that has no view says "unchecked"
    rather than "set".
143. (round 52, P1) Item 88 wrote an absent charter back "whoever applied it", and the
    panel promised the rewrite only where the seal was never applied — a person who
    cleared the charter on purpose after adopting here got it restored. The words win:
    on the control plane that applied the seal the charter is the person's from then
    on, cleared or edited, and neither makes the adoption incomplete; the re-apply sets
    the charter only where the seal was never applied.

Rejected, three. (round 11, P1; raised again in round 14) Item 38's look-based resume has an ABA case: the charter
was written, the flag write crashed, the person deliberately restored the *old* charter,
and a resume — seeing the "before" — writes the approved one again. Real, and inherent in
comparing content: no second write can tell "never written" from "written and put back".
What is done instead is to say it before the click — the resume note now states exactly
what a resume does to the charter in each case — rather than to stop writing on resume,
which would recreate item 34 (a charter never written, silently). (round 8, P2) The parsers accept 3–8 questions and 1–8 tasks while the
prompts ask for 4–6 and 3–6. Kept apart on purpose and now said at the constants: the
prompt's band is what a good answer looks like, the parser's bounds are where an answer
stops being the interview at all — three questions over a one-line brief is usable and the
person sees all three; a truncated JSON answer fails to parse regardless. (round 1, P2)
the kickoff always runs the Claude CLI rather than the
project's configured worker agent. Deliberate and stated in the UI: this is an analyst
call handed a prompt, the same choice `tasks/clarify.py` makes for the requirements
interview, not a run in the project's worktree; a project whose worker is Codex still gets
its runs from Codex. A machine without the Claude CLI gets "agent is unavailable" (502),
which is true.

Deferred, two, as CR rows. (workspace CR-55, `defer`, OPEN; widened in round 49): the task store has no unique
constraint on a kickoff key, and no index — the lookup is one full scan with a ceiling
(item 91); within one process the lock above makes the lookup sufficient, and this control
plane runs as one process over projects far below the ceiling. Round 49 named the corner:
a project at the ceiling exactly, grown past it by an ordinary insert between the pre-seal
scan and the resume, would be stuck. A key column with a unique index removes both the
race and the scan. The row names what brings it back — the first multi-process
deployment, or any project past ten thousand tasks.
(workspace CR-56, `defer`, OPEN; raised again in round 21 on the read side, in rounds 27,
31, 32, 38, 43, 45, 46 and 48 as the `git checkout` window between the one read and the
rename, in round 45 as the junction swap once more, in round 51 as the temporary file
swapped by a writer inside `.agora` between its close and the rename, and in rounds 52 and
53 again): the `.agora`
directory swap inside the gap between the link check and the open, on the write (item 48)
and on the read — where a swapped-in adopted record could, through a re-apply, put another
tree's charter on this control plane. The row names the condition (a project root writable
by another user, or a shared host) and the fix (directory-handle-anchored opens and writes
where the platform offers them); the comment at each site names the row.
(workspace CR-57, `defer`, OPEN; round 14, P1, raised again in rounds 18, 20, 22, 27, 28, 30, 31, 34, 35, 38, 43, 45, 46, 48, 49, 51, 52 and 53): the adopt's charter write is read-compare-
write against a store with no compare-and-set, and the charter editor takes no lock — a
save from the editor landing inside that gap (milliseconds, in one request) is overwritten
by the approved charter. The fix is the editor's own versioning (adr/0005's migration),
through which the adopt write should go; the row names it, the comment at the write
names the row.

Backlog, one, as a CR row (workspace CR-58, `refine`, OPEN; round 53): the mockup rule is
enforced as "the first approved task is of kind `mockup`" and no further — nothing checks
that the task names `docs/mockups/`, that later tasks' acceptance points at the mockup, or
that the owner signed it off. The exit line says later acceptance lines must be able to
point at the mockup; whether that is the kickoff's job or the workbench's is the owner's
call, after they have run it once.

### Blockers

None. The prompts are English with Chinese output requested; the first real run will show
whether `claude -p` honours the "ONLY a JSON object" instruction reliably enough — the
parser tolerates prose around the object and fails closed otherwise.

### Next safe action

Commit the slice files on `feature/w8-session-ui`, staged explicitly, and push to
`private`. Then the owner runs the kickoff on NichiMate from the projects screen; what
they get stuck on decides the next cut (Reach before polish). Still open from W9: D-5
step B (known defects reader) and the browser check of all screens.

## CR-75 slice 1 — the work page and restart recovery

Branch `feature/w8-session-ui`, on top of the design note `619ede1`
(`docs/CR75-slice1-work-page.md`). Reach slice of CR-75 (Agora as the one place claude,
codex and kiro work happens): a **Work** tab whose left column lists the active projects of
every registered workspace source and whose right side is that project's terminal; projects
keep running in the background; after a restart, claude projects left open are reopened on
their last recorded conversation, with a banner that says only what the evidence supports.

Backend (`agora/work/`, `api/work_router.py`):
- `projects.py` — its own scan of each source's `tasks/` (one unreadable entry is that
  entry's problem; `stat` itself is the probe, since from Python 3.14 `is_dir`/`is_file`
  answer False for a read that failed).
- `store.py` — `work_sessions`: the person's intent (`want_open`, written before any process
  operation), the launch, the shell's pid + creation time, and the recovery point (`ck_*`)
  kept apart from the launch columns.
- `report_hook.py` + `hooks.py` — a SessionStart/SessionEnd hook injected per process with
  `claude --settings <file>` (merges with the project's own hooks; nothing global touched),
  appending to a per-launch journal in an owner-only directory (icacls checked afterwards,
  domain-qualified; links and junctions refused; contents re-verified before each launch).
- `reports.py` — the journal reader: fail-closed per line, the launch's *first*
  SessionStart decides what it was (`resumed` / `resumed_other` / `not_resumed` /
  `started`), `/clear` and anything unreadable make the recovery point stale.
- `service.py` — open/close/restore. Restore runs once per server life, skips what may still
  be running (pid + creation time; unknown counts as running), resumes only a transcript
  inside claude's projects directory. A close is the service's own task once the intent is
  written; an open arriving during a close waits for it; a close that could not end the
  terminal says so (`closed: false`) and a second agent is never started beside it.
- `DELETE /terminals/{id}` on a work terminal is the person's close too.
- `command_argv(agent_args=…)` appends each argument as a shell literal; terminals no longer
  inherit the launching agent's session variables (`AGENT_SESSION_VARS`).

Frontend: `/work` page (fifth top-bar tab), `lib/work-state.ts` (banner wording from the
evidence), `lib/work.ts`, three-language `work` dictionary; the terminal uses Tabby's Grape
scheme and JetBrains Mono (`lib/terminal-theme.ts`).

Tests (collected counts):
- Backend `tests/test_work.py` + `tests/test_terminal.py`: **125 collected, 123 passed,
  2 skipped** — the CR-78 test (skip reason names the row; verified to fail without the
  skip) and one POSIX-only command-line test on Windows. `compileall` clean, schema check
  clean, `git diff --check` clean. The full backend suite was last run before the review
  rounds: `test_deliverable_snapshot::test_work_the_run_committed_is_still_reported_as_delivered`
  fails at `86af0b4` in a clean worktree too (pre-existing, not this slice).
- Frontend `pnpm test:control-plane`: **587 tests, 586 passed, 0 failed**; `tsc`, `lint`,
  `build` clean.

Review gate. Implementation by Claude Code; reviewers were codex and, when codex's
workspace credits ran out, kiro as the substitute (`kiro-review.sh`, same gate):
- codex rounds 1–2 (10-30): 3 + 3 findings, all fixed.
- kiro substitute rounds 3–8 (10-30): 9/9/9/7/10/5 — almost all fixed; rejected: duplicate
  YAML keys (system-wide parser behaviour); deferred CR-77.
- codex, six rounds (11-02): one finding each, all fixed — open/close ordering (CR-77
  closed), restore request loop and unmount, cancelled close request, close sequence across
  retries, restore after unmount, ACL principal by domain-qualified name.
- kiro substitute rounds 9–13 (11-02; codex out of credits again): 8/8/4/10/2. Fixed the
  large majority; rejected with reasons — `AGORA_CLAUDE_WORKSPACE_TOOL` binding every source
  (a pre-existing development override), old journals pruned (never read again once a newer
  launch exists), 1 MiB journal cap (truncation is marked stale), macOS liveness (not a
  target; answers "unknown", shown as maybe running), a stale point cleared after a launch
  that was warned as stale; deferred CR-78. Round 13 had nothing new beyond these.

Defers (rows in the workspace CR log, each with what brings it back):
- CR-76 — a process that could not join the job object may outlive the server; restore
  will not start a second one (pid check), the orphan itself is unmanaged.
- CR-78 — a project wanted open under a source that cannot be read at all has no close on
  the page; skipped test `test_a_project_wanted_open_under_an_unreadable_source_can_be_closed`.

### Blockers

None for the commit. Open with the owner: the terminal colours look missing next to their
Tabby (awaiting a side-by-side screenshot).

### Next safe action

The owner checks the exit line by hand on the development install (port 8001): open two
projects, `/clear` one and say something, restart the machine, open the Work page — both
report 「已接回」, the cleared one on its later conversation. What they get stuck on picks the
next cut; slice 2 (mandatory commit review through a git hook) is next in the agreed order.

## CR-79 — quieter restore banner, an empty conversation is not resumed, Today and the workbench removed

Owner, 11-03, after the first restart: all three projects said 「記録された会話が見つからない」.
Nothing was lost — they had been opened on 10-30 and never spoken in, so claude never wrote
their transcripts. Asked whether that could go unsaid, and to delete the old front page and
the project workbench.

- `reports.has_conversation` + `fresh_empty`: claude now writes the transcript at startup, so
  a file with no user/assistant entry is nothing to resume — the launch starts fresh instead
  of `--resume` on an empty session. Unreadable → `fresh_untrusted`, as before.
- `lib/work-state.ts`: the banner speaks only when something asks for the person — a
  confirmed resume and a fresh start with nothing to bring back (no record, file gone,
  empty) are silent, including while pending; failures, unconfirmed starts, a possibly older
  point (`stale`) and `fresh_untrusted` are still said. Silence never stands for "resumed".
- Removed `/` (Today) and `/workspace` (workbench) and the components only they used; `/`
  renders the work page; the bar is Work · Environments · Skills. The portfolio's "settle
  the goal" button pointed into the workbench and is gone (the fact stays). Dispatch, run
  history and the project conversation have no way in on this build — the owner was told
  before choosing. Source-scan tests of the removed pages were dropped (7); `lib/` modules
  they used stay, with their tests.

Tests (collected counts): backend `test_work` + `test_terminal` + `test_web_ui`: **140
collected, 138 passed, 2 skipped** (same two as slice 1). Frontend `test:control-plane`:
**581 tests, 580 passed, 1 skipped, 0 failed**; `tsc`, `lint`, `build` clean; `compileall`
and `git diff --check` clean.

Review: codex round 1 — no findings (script exit 0; it read the patch).

### Next safe action

Restart dev 8001; the owner rechecks the restart path. The exit line now reads: the cleared
project resumes its post-clear conversation (seen in the terminal; a good resume shows no
banner).

## CR-75 slice 3 — the overview page

Owner, 11-03: 「评审完了接着做概览页」. The overview is the front page (`/`); the work page
moves back to `/work`; the bar is Overview · Work · Environments · Skills.

- `lib/overview.ts`: one card per project the work page lists, joined on (source, task) with
  the workspace-sources read (`/api/workspace-sources`, which already reads task.yaml,
  RESUME 进度快照 and the CR table). A project that read did not cover — failed, still out,
  or not in it — is said ("could not read this project's record"), never drawn as an empty
  bar or zero. CRs not closed = OPEN + DECIDED; rows with an unreadable status are counted
  apart, never as closed. Progress reuses `taskProgressOf` / `progressBarOf`.
- `app/page.tsx`: the cards (title, task · agent · terminal state, progress bar, CR line),
  click → work page with that project selected (`WORK_SELECTED_KEY`, now shared). The page
  asks for the restore after a restart as the work page does, and says when a project needs
  a look there. A failed poll keeps the cards and says they are out of date; a failed
  restore request is said; a superseded sources read is cancelled.
- No "current task" on the card: RESUME has no fixed place for one, and a line cut from
  「下一步」 is often a heading. Needs a recorded field first.
- Three-language `overview` dictionary; `common.nav.overview`.

Tests: frontend `test:control-plane` **586 tests, 585 passed, 1 skipped, 0 failed** (5 new in
`tests/overview.test.mts`); `tsc`, `lint`, `build`, `git diff --check` clean. No backend
change.

Review: codex round 1 — 2 (stale list after a failed poll not said; restore request failure
not said), fixed; round 2 — 1 (an older sources read could overwrite a newer one), fixed;
round 3 — none.

### Next safe action

Restart dev 8001; the owner looks at the overview. Slice 2 (commit review through a git hook)
is next in the agreed order unless use says otherwise.

## CR-80 — pause, archive and restore projects from the overview

Asked 2026-11-04: the overview listed 24 projects, many not being worked on. A project's
own `task.yaml` status decides whether it is listed, so pausing and archiving write there
(no Agora-side hide list that could disagree with claude-workspace). Nothing is committed
to the work data repository: several sessions write to it at once.

- `agora/work/shelf.py`: line-level status edit, verified by parsing (status reads back,
  nothing else differs; a layout it cannot change safely is refused, never rewritten).
  Archiving a claude-workspace task matches `cw archive` (status archived + completed_at,
  directory into `tasks/_archive/`), done in Python rather than by running `cw.sh`;
  `test_archive_matches_cw_archive_layout` pins the layout. The workspace's
  `.current-task` is not archived; a failed move puts task.yaml back.
- Scan lists `shelved` (paused / other status / everything in `tasks/_archive/`, which has
  its own key). `POST /api/work/projects/{key}/shelve {action}`,
  `POST /api/work/shelved/{key}/restore`; refused (409) while any terminal of the project
  has not ended or it is meant to be open.
- Overview: Pause / Archive (second click to confirm, says closing checks are not run) on
  each card; a collapsed "Paused N · Archived N" section with Restore.

Tests: backend test_work + test_work_shelf + test_terminal + test_workspace_sources_api
collected 175, 173 passed, 2 skipped; frontend test:control-plane collected 588, 587
passed, 1 skipped; tsc, lint, build, compileall, schema check, diff --check clean. Full
backend suite not run (release runs it).

Review: codex 3 rounds — round 1 (2): an open that scanned before the shelve still
launched; a terminal whose close timed out did not block. Round 2 (1): an open scanning
while the write ran. Round 3 (2): a cancelled request released the lock while the write
still ran (now a service-held task); an archived entry shared its key with a same-named
task. All fixed with regression tests; round 3's fixes were not reviewed again (three-round
limit for a slice).

## 2026-11-04 — workspace CR-81/82: todo / doing and the order of the projects

The owner: the work page lists too many conversations; add todo/doing on the overview so
the work page shows only what is doing, and let the order be dragged (overview included).
Decided with the owner: the stage is Agora's own (never written into task.yaml); opening a
project makes it doing; "todo" is refused while its terminal is open or meant to be; a
restart reopens only what is doing.

- `work_board` table (`agora/work/store.py`): key, stage (todo|doing), position. At start
  every project meant to be open with no board row is written as doing (idempotent).
- `WorkService.set_stage` / `set_order`; `open()` (not a restore) writes doing under the
  lock; the restore returns `closed` for anything not doing. `view()` carries `stage` and
  `position`. Routes: `POST /api/work/projects/{key}/stage {stage}` (409 terminal_open),
  `PUT /api/work/order {keys}` (404 unlisted, 422 repeated).
- Work page: only doing — and anything whose terminal is open, opening or meant to be,
  whatever its stage — in the person's order; drag to reorder, one save at a time, the
  order being saved laid over list reads, reads sent before the save dropped after it.
- Overview: "Doing" and "To do" columns; drag a card onto another card (placed before it)
  or into a column (its end) to move it; Start / To do buttons on each card; opening a todo
  project from the overview starts it first.

Tests: backend test_work_board + test_work + test_work_shelf + test_terminal collected
156, 154 passed, 2 skipped; frontend test:control-plane collected 592, 591 passed,
1 skipped; tsc, lint, build, diff --check clean.

Review: codex 3 rounds — round 1 (2): a project open before the board existed fell to
todo when closed (now written as doing at start, regression test); a drop over a column's
heading or gap used a stale target. Round 2 (1): overlapping reorder saves and polls could
undo a move. Round 3 (2): a read sent before the save could land after it; an order
failure showed only with a project selected. All fixed; round 3's fixes were not reviewed
again (three-round limit for a slice).

## 2026-11-05 — terminal drawn on WebGL: the cursor sits where the text ends

The owner: "终端的输入光标跟实际的位置有偏差". Measured in headless Chromium with the
terminal's own fonts at 14px: cells are 8.4px and CJK characters fill their two cells
exactly, but the DOM renderer drew each full-width mark (，。：、) 6.8px wider than two
cells, so every one pushed the rest of the line right — on `> 你，好。世：界？（中）「文」、日本語です。`
the text ended about 27px (three cells) past the cursor. `@xterm/addon-webgl` 0.19.0
(released with xterm 6.0.0) draws every glyph inside its cells; screenshots of the same
line show the text ending at the cursor. Without WebGL, or on context loss, the terminal
stays on / falls back to the DOM renderer.

Tests: frontend test:control-plane collected 592, 591 passed, 1 skipped; tsc, lint,
diff --check clean. The build is run when the owner is not navigating 8001 (it rewrites
the files 8001 serves).

Review: codex 1 round, no findings.

## 2026-11-05 — CR-75 slice 2: a commit is refused unless its content was reviewed

Owner (2026-11-05, three recommended options): block and check afterwards; skipping takes
a written reason; installed on Agora-private only for now. Design and its three codex
design rounds (1+2+3 findings, all folded in): `docs/CR75-slice2-review-gate.md`.

The gate itself is claude-workspace's (`scripts/review-gate.sh` + three hooks; reviewed
and committed there). What this repository adds:

- `backend/agora/projects/review_gate.py` — part D, the check afterwards: commits since
  the install sorted into reviewed / skipped / merge / unrecorded by their trailers, a
  record counting only when its `Review-patch` equals the commit's own patch-id (so a
  trailer carried along by `--amend --no-verify` or a hand-resolved replay counts as no
  record). Patch-ids cached by sha; 500 commits at most, said when cut; a failed read is
  "unreadable", never zero. Its diff options must stay identical to the hooks' (named in
  both files). Attached to each workspace task as `review_gate`.
- Edge cases from review (codex round 1, kiro round 2 standing in for codex out of
  credits): a malformed or field-less install record, a relative `primary_repo`, a marker
  that cannot be stat'd, a branch without the install commit — all "unreadable", never
  "not installed" or a count from the wrong range; an empty skip reason is no reason;
  `--root` so `log.showRoot=false` cannot change a root commit's patch-id; an installed
  empty repository reads zero.
- Overview card: one line only when there is something to say (commits without a record
  in the warning colour, skipped-with-reason muted, a failed read said).
- `scripts/release.ps1`: the "release: vX.Y.Z" commit carries
  `AGORA_REVIEW_SKIP="release version bump"`.

What it does not claim: that a review approved, or that a record cannot be forged — it
stops forgetting; `--no-verify` shows up afterwards, a forged stamp does not.

Checked end to end: real hooks writing trailers in a scratch repository, then this
reader classifying them (reviewed 2 / skipped 1 / a `--no-verify` commit unrecorded 1,
an amend kept with its whole-commit patch).

Tests: backend test_review_gate_read collected 19, 19 passed; frontend test:control-plane
collected 593, 592 passed, 1 skipped; tsc, lint clean. claude-workspace
`scripts/tests/test_review_gate.sh` 39 passed (symlink case skipped: no symlinks on this
machine); the gate is claude-workspace `cd0f7c6` + `54c41c6` (codex 3 rounds + 1).

Installed on this repository 2026-11-05 (install commit `7e9fbe9`); this slice's own
commit is the first to pass it. Review of this repository's part: written before the
review, so its outcome is in the commit message and trailers, not here.

## 2026-11-06 — CR-75 slice 4 (first cut): the workflow, chosen per project — claude first

Owner (2026-11-06, three recommended options): claude first (kiro/codex read the same
selection in a later slice); "off" is declared at session start, CLAUDE.md is not split;
the selection lives in the project's own `task.yaml` and is edited from the overview card.
Design: `docs/CR75-slice4-workflow.md` (codex design round 1: no findings).

Six switches — requirements, breakdown, aidlc (off by default), deviation, review,
records. No `workflow:` block = today's behaviour, and nothing is said about it.

- claude-workspace (`a9f46fd`, codex 3 rounds 1+1+0): the SessionStart hook prints
  「本项目的工作流」: which CLAUDE.md sections do not apply, what no switch turns off
  (tests, the defer rule, an installed review gate), and that aidlc is not supported in
  claude sessions yet. Its tests: `scripts/tests/test_workflow_section.sh` 37 passed.
- `backend/agora/projects/workflow_selection.py`: the same reading rules as the hook —
  block form only, known keys only, YAML 1.1 booleans only; anything else "unreadable",
  never the defaults. The edit writes all six switches, verified by parsing the result
  (nothing else in the document may change), refuses an unreadable block, and refuses a
  file whose sha256 is not the one the page read ("changed"). Not committed.
- `workspace_tasks.py`: the manifest carries `workflow` (state / features / reason /
  base). The manifest is now read as bytes: `read_text` translated CRLF, so the base
  the page got never matched the bytes the edit hashes and every save on a CRLF file
  would have come back "changed" (caught by this slice's own API test on Windows).
- `POST /api/work/projects/{key}/workflow` (terminal token, like the shelf routes);
  claude-workspace projects only — kiro does not read the block yet.
- Overview card: a 工作流 button opening six checkboxes with their meaning, the AI-DLC
  note, the gate note when review is turned off on a repository with the gate, what no
  switch changes, and "takes effect at the next session start or /clear". A one-line
  summary only when something is off or AI-DLC is selected; an unreadable block is said.

Codex round 1 (2 findings, both fixed): the reader now applies the hook's exact line grammar and
reads anything YAML takes but the hook cannot (an indented `{review: off}`, a quoted key, a
tag) as unreadable, and requires both readings to agree; the write runs as a task the
service owns, so a cancelled request cannot release the lock mid-write. The hook's header
rule was tightened to match (`workflow:#x` is not the key; claude-workspace).

Tests: backend test_workflow_selection collected 25, 25 passed; with test_work_shelf,
test_review_gate_read, test_workspace_tasks: 128 passed; test_workspace_sources_api,
test_workspaces, test_work: 148 passed, 2 skipped. Frontend test:control-plane collected
597, 596 passed, 1 skipped; tsc, lint clean. Review outcome: in the commit message.

## 2026-11-06 — CR-75 slice 4 (second cut): kiro reads the same workflow selection

Owner (2026-11-06): approved the kiro steering text before it was written ("写"), with
AI-DLC on by default for kiro and the known-hazard row as a rule no switch turns off.

- kiro-workspace `1ee192e` (not pushed): `.kiro/steering/workflow-selection.md`, one
  file for every task. A kiro agent already loads its `tasks/<name>/task.yaml` as a
  resource; the steering says what each switch turns off there, the same reading rules
  (unreadable → say so, apply everything), and what no switch turns off. No existing
  steering or skill was changed.
- `workflow_selection.py`: `KIRO_DEFAULTS` (all six on — kiro proposes AI-DLC today, so
  a task without the block must not read as AI-DLC off) and `defaults_for(workspace)`;
  the reader and the edit take the workspace's defaults, the grammar is unchanged.
- `workspace_tasks.py`: a kiro-workspace (`.kiro/`, no `.claude/`, as the terminal
  targets decide) reads `workflow` even though its task.yaml is `unknown_schema` to the
  claude-shaped manifest reader; outside a kiro-workspace an unknown shape carries none.
- `POST /api/work/projects/{key}/workflow` accepts kiro projects, with kiro defaults.
- Overview card: the 工作流 button on kiro cards too. AI-DLC note and summary only for
  claude (kiro supports it); kiro's AI-DLC turned off counts as a switch turned off;
  "takes effect when the next kiro session starts".
- codex: nothing to read. Agora opens no codex session per project (work projects are
  claude or kiro); codex is the reviewer, which the switches do not configure.
- Not covered: the gate note on a kiro card — the review-gate read follows
  `primary_repo`, which kiro task.yaml does not have (it has `project_path`), so the note
  is absent rather than wrong; the steering itself tells kiro about an installed gate.

Review: codex round 1 exited 4 (its Windows sandbox failed every local command, so the
review never showed this patch) → kiro substituted. Kiro round 1, 5 findings:
- fixed: `shelf._write` used one fixed temp name, so two Agora instances (stable and
  dev read the same work roots) could put each other's text in place — now `mkstemp`.
- fixed: a kiro-workspace without `.kiro/steering/workflow-selection.md` was offered
  the switch its agents would ignore — `workflow_workspace()` offers it only with the
  steering file, and the write refuses otherwise.
- fixed: an unknown `.claude/` probe read as absent (→ kiro defaults) — unknown now
  carries no workflow.
- fixed: a file mixing CRLF and LF was rewritten whole — only the block's lines change.
- deferred: sha compared, then replaced, no lock across processes (a write landing in
  between is lost; also CR-80's shelf) → workspace CR-84 (defer, OPEN), named at
  `_write_workflow`.

Kiro round 2, 4 findings:
- fixed: `mkstemp` makes the file 0600, so a save on POSIX dropped task.yaml's mode —
  the original mode is copied onto the temp file before the replace.
- rejected: the sha/replace window — already CR-84.
- rejected: no gate note on a kiro card — kiro task.yaml has `project_path` in an MSYS
  spelling (`/e/...`), not `primary_repo`; the card then says nothing (absent, not
  wrong), and the steering tells kiro about an installed gate. Recorded above.
- rejected: checking the steering file's content — the file is installed by a person;
  its presence is the claim the card relies on, a content check would be guesswork.

Kiro round 3 (the last), 4 findings:
- fixed: new block lines take only an LF or CRLF break — a lone CR, NEL or U+2028 at
  the end of the file would have left `workflow:` on no line of its own for the hook.
- fixed: a failed write of a read-only task.yaml (Windows) left its temp file behind,
  read-only like the original — made writable before it is removed.
- rejected: the sha/replace window — CR-84.
- rejected: a workspace changing kind (claude → kiro) while the page is open — the
  write would apply the new workspace's defaults to a stale reading; too remote.
These two fixes came after the third round: committed with a skip reason.

Tests: backend test_workflow_selection collected 35, 35 passed; with test_work,
test_workspace_tasks, test_workspace_sources_api, test_workspaces, test_work_shelf:
269 collected, 267 passed, 2 skipped. Frontend test:control-plane
collected 598, 597 passed, 1 skipped; tsc, lint clean.

## 2026-11-09 — v1.8.0 released; CR-85: the card's settings (a name in Agora, the workflow)

Release (owner said 「发布」): main fast-forwarded to `8da0944`, `release: v1.8.0`
`f2bac68` tagged and pushed to origin, stable install upgraded 1.7.0 → v1.8.0 (backup
`agora-1.7.0-before-v1.8.0-20261109-104547.db`), `/health` reports 1.8.0. Every release
gate passed (the release log does not print the pytest counts). origin's default branch
is `main` again (owner: 要改回main).

Remote account (CR-9 → adr/0002): dev environment `wu-35` tested — host key trusted,
auth ok, root present, claude/codex/kiro-cli all in `/home/wu`; a remote shell as `wu`
ran all three `--version` and `claude -p` answered. The stable install has no remote
environment registered, so there is nothing to switch yet: it waits on which project
to bind.

CR-85 (workspace, owner 2026-11-09): the card's 工作流 button becomes 设置.
- `work_names` table (Agora's own, beside the board; never written into task.yaml),
  `WorkSessionStore.names/set_name`, `WorkService.set_name` (only a listed project;
  blank removes; one line of at most 80 characters, control characters refused, not
  stripped), `POST /api/work/projects/{key}/name`; the work list carries `name`.
- Overview card: title = name, else the record's title, else the task; the sub-line
  keeps the task name. 设置 is on every card — a name can be given even where the
  workflow is unreadable (the panel then says why). Save sends only what changed: the
  name first (cannot be refused for the file's sake), then a changed workflow through
  the same task.yaml write as before.
- Work page: the list and the terminal bar show the name, with the task beside it.

Tests: backend test_work_board collected 17, 17 passed; with test_work,
test_workflow_selection, test_work_shelf, test_workspace_tasks: 233 collected, 231
passed, 2 skipped. Frontend test:control-plane collected 599, 598 passed, 1 skipped;
tsc, lint clean; `git diff --check` clean.

Review: codex round 1 (its gate exited 5 — the tree changed under it — so no stamp), 2 findings,
both fixed:
- Enter in the name field saved while an IME was composing (Chinese/Japanese names) —
  ignored while `isComposing` / keyCode 229.
- the save compared the draft with the card as the 10-second poll last showed it, so a
  name changed in another tab meanwhile was overwritten by an unrelated save — the draft
  keeps the values it opened on, and only fields that differ from those are sent.

Codex round 2: no findings (its own run: 17 backend, 26 frontend tests, tsc, lint).
Its gate exited 5 again: codex left `.tmp-review-cr85/` and `.tmp-review-names-1109/`
(its pytest temp directories) in the working tree while reviewing, so no stamp was
recorded although the code did not change after round 2 started. Removed; committed
with a skip reason naming this.

## 2026-11-09 — CR-86: bring back the newer conversation opened outside the work page

Owner (2026-11-09): the work page brought back an older conversation. Measured: the
newest gitlab-ultimate-automation conversation (`92d84e06`, 11-04 → 11-06) was started
with `cw open` in a terminal, in that task's primary_repo (pleasanter-workflow), so its
transcript sits under that directory; the work page knew only the conversations it
launched. Owner: 「如果你推荐做的话就做」.

- `reports.newer_task_conversation`: the newest transcript under Claude's projects
  directory written after the recovery point whose **last** claude-workspace SessionStart
  hook names this task (`# Active Task: <task>`) under this work root (`- 工作根：…`),
  and in which someone spoke. Nothing else claims a transcript; unreadable ones are
  passed over (it only ever adds a candidate, never says there is none).
- `WorkService._newer_elsewhere`: on a claude open with the hook installed, such a
  conversation is resumed instead of the recovery point (or of a fresh start). Measured
  first: `claude --resume <id>` from another directory brings it back and keeps writing
  its transcript where it is, so the launch directory does not change. One written to
  in the last 10 minutes may still be open in another window — resuming it here too
  would fork it — so it is named, not taken. `expect_session` is the conversation asked
  for, so the confirmation stays honest.
- The launch carries `note` (`newer` / `newer_busy`, when, cwd); the work page says it
  above the terminal.
- Real data (read only): gitlab-ultimate-automation → `92d84e06` (cwd
  pleasanter-workflow); three tasks scanned in about 3 s.

Codex round 1 (stamp recorded), 2 findings, both fixed:
- "newer" was measured by file mtime, but resuming the recorded conversation writes
  startup bookkeeping into its file without anyone speaking, so a conversation left
  while busy could never be taken afterwards — newer is now when someone last spoke
  (`reports.last_spoken`, the last user/assistant message's timestamp); mtime only
  prefilters (a file is written at least as late as anyone spoke) and decides busy.
- a fresh decision (e.g. the recorded conversation empty after /clear) dropped the
  cutoff to 0 and could bring back an older conversation called newer — the cutoff
  (`WorkService._cutoff`) is kept whatever was decided: when the recorded one last
  spoke; empty → when it was written; its file gone or unreadable → when the point was
  recorded (`ck_at`, now on `WorkSession`); not known → nothing is claimed.

Codex round 2 (stamp recorded), 2 findings, both fixed:
- the task was taken from the last hook output *mentioning* `Active Task`, so a later
  start that named no task kept the earlier attribution — every SessionStart output
  with content now decides (no task/work-root pair clears it); empty outputs (hooks
  that print nothing, Agora's reporter among them, measured in real transcripts) say
  nothing.
- the busy notice said the recorded conversation was brought back even when there was
  none and a new one started — the note carries `fallback` (resume / fresh) and the
  page says which.

Codex round 3 (the last; stamp recorded), 1 finding, fixed after it:
- with nothing recorded and the other conversation busy, the fresh start made instead
  became the recovery point; nobody speaking in it, its file time became the cutoff and
  shut the other conversation out for good — an empty recorded conversation now bounds
  only when it began by /clear (or its start is unknown); one a launch started and
  nobody used (`startup`, read from the recording launch's journal) bounds nothing.
Committed with a skip reason (fix after the third round).

Tests: backend test_work_newer collected 15, 15 passed; with test_work, test_work_board,
test_workflow_selection, test_work_shelf, test_workspace_tasks: 248 collected, 246
passed, 2 skipped. Frontend test:control-plane collected 600, 599 passed, 1 skipped;
tsc, lint clean. Real data again: gitlab-ultimate-automation → `92d84e06`, last spoken
11-09 10:57 — still in use in another window, which is exactly the busy case.

## 2026-11-09 — CR-87 (= CR-12 item 6): the usage bar

Owner (2026-11-09): each agent's /usage at the bottom of the page, like Orca, to see codex
nearing its limit and reset or switch account before it stops. Official paths only, no
credential read (CR-12):

- claude: Claude Code sends `rate_limits` (5-hour / 7-day: used_percentage, resets_at)
  to its status line, for claude.ai Pro/Max/Ultra and only after a session's first
  answer (docs: code.claude.com/docs/en/statusline). A `--settings` status line replaces
  the workspace's, so the work page's settings carry a relay —
  `"<python>" "<data>/work-hooks/statusline_relay.py" | bash -c "$AGORA_STATUSLINE_COMMAND"`:
  the relay records the windows in `<data>/work-usage/claude.json` and passes the
  document on unchanged; the launch puts the workspace's own command (`settings.json`,
  `settings.local.json` over it) in `AGORA_STATUSLINE_COMMAND`, so the person's status
  line is the same as before. A report without limits keeps the last ones; a window past
  its reset time is said as reset, with no figure. `still_installed` checks the relay.
- codex: `codex app-server` → `account/rateLimits/read` (measured: 5h 73%, 7d 11%, plan
  team, one "Full reset (Weekly + 5 hr)" credit available), one short-lived process per
  read, kept 60 s. Only the windows, reset-credit count, limit-reached and plan name are
  kept — no account id, no credit ids.
- kiro-cli: `/usage` is interactive only (`chat --no-interactive "/usage"` is sent to the
  model as a prompt, measured), so the bar says it cannot be read.
- `GET /api/work/usage` (terminal token, like the work routes); every reading is `ok`
  with when, or `unavailable` with why.
- Frontend: a fixed bar at the bottom of every page (`--usage-bar`, pages leave its
  height), polled each minute; amber from 80%, red from 95%, time to reset when loud,
  reset credits, the age of a claude reading over 10 minutes; hidden without the token.

Codex round 1 (stamp recorded), 2 findings, both fixed:
- the status line passed on was only the workspace's, so one set only in the user's
  Claude settings was replaced by nothing — `workspace_statusline` now resolves what
  claude would use: `$CLAUDE_CONFIG_DIR` (or `~/.claude`) `settings.json`, then the
  workspace's `settings.json`, then `settings.local.json`.
- on a timeout only the `codex.cmd` wrapper was killed; its node child held the pipes
  and the reader thread with them, once a minute — the app-server is put in a job object
  at once (`ExecutionDispatcher._adopt_process_tree`, as for runs) and the job is
  terminated, `taskkill /T` only without one (measured: it can take a minute), then the
  pipes are closed and the reader joined. Test: a stand-in that starts a child holding
  the pipe and never answers — read ends in about 3 s, the child is dead (asked of the
  process with GetExitCodeProcess; a `tasklist` child in the test waited out the
  stand-in's pipes, measured), no reader left.

Codex round 2 (its gate exited 5: codex left `.review-usage-1109/` in the tree — now
excluded locally like `.tmp-review-*`), 1 finding, fixed:
- on Linux/macOS there is no job object, so a timeout killed only the launcher and a
  child holding the pipe would block the close, and with `_codex_lock` every later read —
  the app-server starts in its own session (`start_new_session`) and the group is
  SIGKILLed before the pipes close. **Not run on Linux here** (this machine is
  Windows); the stand-in test covers it there.

Tests: backend test_work_usage collected 21, 21 passed; with test_work, test_work_board,
test_work_newer, test_workflow_selection, test_work_shelf, test_workspace_tasks: 269
collected, 267 passed, 2 skipped. Frontend test:control-plane collected 603, 602 passed,
1 skipped (usage.test.mts added to it); tsc, lint clean.

Codex round 3: clean; committed `b606ce9`.

### 2026-11-09 — CR-87 follow-up: what is left, and kiro's credits

The user, on the bar as described: not tokens used — what `/usage` says is **left**:
claude's and codex's percentage, kiro-cli's credits.

- The bar shows what is left of each window (`5h 剩 46%`), rounded down, and 1% rather
  than 0% while anything is left (`leftPercent`); colours still follow what is used.
- kiro: kiro-cli 3.1.0's own `/usage` calls the ACP extension method
  `_kiro/account/getUsage` (read from its `tui.js`). `kiro-cli acp --agent-engine=v3`,
  `initialize`, then that call answers in under a second **without a session** (with
  `session/new` it also works, but leaves a "New Session" in kiro's list — one was left
  in `%TEMP%` by the probe). The v2 engine answers -32601, shown as `kiro_no_api` (now
  "this kiro-cli has no usage call; 3.x has"). Measured here: KIRO POWER, 2874.17 /
  10000 credits used, resets 2026-12-01. Credits left = limit - used, floored at 0;
  add-on and bonus credits not counted, as kiro's own status line does not.
  Kept 300 s (`KIRO_TTL`).
- `_ask` is the JSON-RPC-on-stdio read `_ask_codex` was, shared by both: same job
  object / process group / pipe-close ending.

Tests: backend test_work_usage collected 26, 26 passed. Frontend test:control-plane
collected 604, 603 passed, 1 skipped; tsc, lint clean. Live reads from this machine:
kiro ok (above), codex ok (5h 0%, 7d 12% used, 2 resets).

Review round 1: codex said no findings but its gate exited 4 (its Windows sandbox failed
local commands; the patch was not in its output) — kiro stood in, 4 findings, all fixed:
- a refused `initialize` still sent the next request, so its later -32601 read as
  "no such call" — a failed handshake is now the answer ("kiro: initialize failed: …").
- kiro values were not checked: negative used showed more left than the limit, and
  NaN/inf would fail the whole `/work/usage` response — `_number` keeps finite numbers
  only (codex too), and negative used is unavailable.
- the credit breakdown was picked by its localisable display name, else the first
  limited one — now by `resourceType == "CREDIT"`, then the name, else unavailable.
- 0.5 credits left showed "0" — shown as "<1".
Tests: backend test_work_usage collected 29, 29 passed; tsc, lint clean; live kiro read ok.

Review round 2: codex, clean (it read the patch this time); committed `719ff9b`.

### 2026-11-09 — CR-87 follow-up 2: claude on a Team plan

The user is on a Claude Team plan, whose status line carries no `rate_limits`, so the
claude cell had nothing. Measured: `claude -p "/usage" --output-format json` answers
locally — 0 turns, 0 cost — with "Current session: 5% used · resets Nov 9, 6:59pm
(Etc/GMT-9)" and "Current week (all models): 18% used · …" on this Team account, in
about 6 s; `--no-session-persistence` leaves no transcript (the first probe, without
it, left one under `~/.claude/projects/C--Users-…-Local-Temp/`).

- `read_claude_usage`: that call in the temp directory, process tree ended as codex's,
  kept 120 s; the session line is "5h", the all-models week "7d" (per-model weeks are
  not shown). The status-line record stands in only when `/usage` cannot be read.
- Reset times: this Python has no tzdata and `TZ` does not change what claude prints, so
  `Etc/GMT±N` and UTC are read by hand (Etc's sign inverted), other zones through
  zoneinfo when it can; an unknown zone gives no reset time, never a wrong one. The year
  is the one that puts the reset nearest after now.
- `read_all` reads the three at once (7.1 s measured, was claude + codex + kiro in turn).

Tests: backend test_work_usage + test_work + test_work_board + test_work_newer collected
165, 163 passed, 2 skipped (test_work_usage alone 36). Live read_all: all three ok.

Review round 1: codex, 1 finding, but its gate exited 4 (its sandbox again; it also left
`backend/.tmp-review-usage-now/` with 79 test configs — removed, and `.tmp-review-*/` is
now excluded at any depth), so kiro stands in for the gate:
- an IANA zone's offset was taken now and applied to the reset date, an hour off across
  a clock change — `_zone` returns the zone itself (fixed offsets for `Etc/GMT±N` and
  UTC) and the reset is built in it. Tests: a stand-in zone that changes offset between
  the read and the reset (this machine has no tz database); Etc's inverted sign.
Tests: test_work_usage collected 38, 38 passed.
Kiro (standing in), 4 findings, all fixed:
- the job is joined after the start, so a child `claude.cmd` started first is outside
  it — a timed-out read also starts `taskkill /T` (not waited for: it takes a minute or
  two, measured; waiting made the stand-in test take 2 min).
- a cached reading was served past its window's reset — `_fresh` drops a cache entry
  once any window's `resets_at` has passed (claude, codex, kiro).
- the repeated hour of a fall-back night took the first reading — both folds are tried;
  the nearest at or after now wins, else the latest within a day before.
- `Etc/GMT+24` raised and took the whole response down — Etc offsets past 14 h are no
  zone, and `read_all` turns one agent's exception into that agent's `unavailable`
  (which caught my own slip while fixing: `os.killpg` reached on Windows).
Tests: test_work_usage collected 43, 43 passed; live read_all: all three ok.

Review round 2: codex (it read the patch, 3/4 files), 1 finding, fixed:
- "6:59pm" read at 18:59:30 had next year's date as the only one ahead, so the reset
  just past became a year-long countdown — candidates are bounded to a day before and
  8 days ahead (no window is longer than a week); the nearest ahead, else the latest past.
Tests: test_work_usage collected 44, 44 passed.

Review round 3: codex (read 3/4), 1 finding (P1), fixed after the last round:
- the job was joined after the start, and the round-1 answer (a later `taskkill /T`) ran
  after the job had already killed the parent, so it could not find the escaped child,
  which kept the pipe — `communicate()`'s reader blocked the close and `_claude_lock`
  with it. The race itself is gone: `_spawn` starts the reader **suspended** on Windows
  (`CREATE_SUSPENDED`), joins the job, then `NtResumeProcess` — nothing it starts can
  be outside the job; `taskkill` after a job is dropped. POSIX unchanged (own session,
  `killpg`). Test: a stand-in claude that at once starts a child inheriting stdout and
  never answers — the read ends in ~3 s and the child is dead.
Tests: test_work_usage collected 44, 44 passed. Live: claude 6.6 s, codex 1.5 s, kiro
2.7 s, all ok. Committed with AGORA_REVIEW_SKIP (fix after round 3).

## 2026-11-10 — CR-75 slice 5: skills management (first slice)

Owner's decisions (2026-11-10): one original per skill in Agora's library
(`<claude-workspace work root>/skills/<name>/SKILL.md`, `AGORA_SKILLS_LIBRARY` overrides);
installed copies stay out of the project's git (`.git/info/exclude`); the workflow's own
commands are shown, not managed; first slice = library + add/edit/delete + import +
install per local project for all three CLIs. Design: `docs/CR75-slice5-skills.md`.

- Backend: `agora/skills/{library,install,inventory}.py`, `agora/api/skills_router.py`
  (`/api/skills`, terminal token). Copies carry `.agora-skill.json` (name + content hash):
  current / outdated / modified (edited in place: never overwritten or removed without
  `force`) / foreign (no marker: never touched; refuses the whole install before writing).
  Library names clashing with a workflow command are refused (a claude skill replaces
  the command). Delete refuses while installed anywhere.
- Work page: claude starts with `--add-dir <primary_repo>` so the repository's
  `.claude/skills` load (claude docs: skills under an added directory are loaded); the
  session still starts and resumes in the tool directory.
- `/skills` and `/api/skills` left the 0.5 retirement lists (`/skills` had been
  redirecting to `/control-plane`, so the placeholder page was unreachable);
  `agora.skills` left the legacy-module import ban.
- Frontend: Skills page rewritten; `lib/skills.ts` + `lib/skills-state.ts`; en/zh/ja.
- Not covered (said on the page): kiro-workspace task agents read only the skills their
  profile lists; global ("every project") installs and remote projects are slice 2.

Tests: backend full non-integration suite 2354 passed, 12 skipped, 4 deselected
(2370 collected) before the `test_web_ui` fix; `test_web_ui.py` + `test_skills.py` +
`test_work.py` after it: 133 passed, 2 skipped (135 collected). New `test_skills.py`: 24.
Frontend `test:control-plane` 607 (606 passed, 1 skipped); `tsc --noEmit`, eslint on the
changed files clean. compileall, schema `--check`, `git diff --check` clean.

Review round 1 (codex, gate exit 0): 3 x P2, all fixed — the edit token hashed decoded
text while the save compared raw bytes (a CRLF SKILL.md could never be saved); os.walk
skipped an unreadable subdirectory (an incomplete copy reported as the whole skill; now
raises); copyfile dropped the mode (helper scripts lost +x; now shutil.copy). +3 tests:
test_skills.py 27 passed (27 collected).
Review round 2 (codex, gate exit 0): 3 x P2, all fixed — paused/archived projects were
dropped from install accounting (a library original could be deleted under their copies,
and they could not be uninstalled; now listed as shelved, archived task.yaml read from
tasks/_archive/); exclude lines were anchored at the repository top when primary_repo is
a subdirectory (now `git rev-parse --show-prefix` is prepended); uninstall removed lines
a linked worktree sharing the exclude file still needed (uninstall now leaves them).
+3 tests: test_skills.py 29 passed (29 collected); frontend 607 (606 passed, 1 skipped).
Review round 3 (codex, gate exit 0): 2 x P2, both fixed after the round limit, so
committed with AGORA_REVIEW_SKIP — a project whose task.yaml or repository could not be
read was dropped, so delete could remove the library original under its copies (now
`inventory_gaps` blocks delete and names the project; a directory with no task.yaml is a
definite non-project and is not a gap — otherwise kiro-workspace's `ppt-agent` folder
would block every delete on this machine); a project path with gitignore metacharacters
(`apps/[web]`) became a glob (now escaped). +2 tests.
Final: test_skills.py + test_web_ui.py + test_work.py 140 passed, 2 skipped (142
collected); frontend 607 (606 passed, 1 skipped); `git diff --check` clean. The full
non-integration backend suite was run once before the review rounds (2354 passed, 12
skipped, 4 deselected); the rounds changed only `agora/skills/`, `skills_router.py` and
their tests.
Next safe action: owner's acceptance on 8001 (needs a frontend build and an 8001
restart): Skills page → import or create a skill → install into a project → open that
project on the work page and check claude lists the skill.

## 2026-11-11 — CR-75 slice 5 follow-up: read a listed skill before importing it (workspace CR-89)

Owner on 8001: "the skill's content cannot be seen or edited". Only library skills had an
Edit button; the "found elsewhere" and "provided by the workflow" lists showed a name and
one truncated line, and the owner's library was still empty, so nothing on the page could
be opened.

- `GET /api/skills/found?path=` returns a listed skill's text and file names, read-only.
  It reads only a path the view itself lists (a workflow command or an importable skill);
  anything else is refused (409), and it needs the terminal token like every skills route.
- Skills page: a View button on each importable skill and each workflow command opens the
  text under the row, with where the file is and why it is read-only (import it to edit
  the library's copy / it belongs to the workflow). en/zh/ja.
- Unchanged on purpose (owner's decisions of 2026-11-10): the original is never edited in
  place; workflow commands are not edited here.

Tests: `test_skills.py` 32 passed (32 collected; +1 `test_a_listed_skill_can_be_read_and_nothing_else`).
Frontend `test:control-plane` 607 (606 passed, 1 skipped); `tsc --noEmit` and eslint on the
changed files clean. The full backend suite was not rerun: the change is one new route and
its test.
Review: pending (codex).
Next safe action: review, commit, then build + restart 8001 for the owner to check.

## 2026-11-11 — CR-75 slice 5 follow-up: a project's skills are chosen in its card (workspace CR-90)

Owner, after asking what Import on the Skills page was for: choosing skills for a project
belongs in that project's card, not on the Skills page — and as a dropdown, not a list of
checkboxes, because the library will grow.

- Overview card → Settings gains "Skills": the skills this project has (each with its
  state; update / replace / remove), and a dropdown with a filter to add one from the
  library. Choosing installs at once for claude, codex and kiro; it does not wait for the
  panel's Save, and the panel says so. A project with no repository, an unknown or
  unreadable library, and a failed refresh are each said, not shown as an empty list.
  `components/project-skills.tsx`; pure parts `projectSkillsOf` / `matchSkills` in
  `lib/skills-state.ts`. No backend change: it uses the slice-5 routes.
- Skills page: under a skill only the projects that already have a copy are shown
  (update / remove stay there — a paused or archived project has no card to do it from);
  the install-into-any-project buttons are gone, with a line saying where that is done
  now. "Found elsewhere" is folded into a closed `<details>` with its count.
- en/zh/ja.

The owner did not answer what the Skills page should keep; the above is the recommended
option put to them, and is said to them as a choice made, not as their decision.

Tests: frontend `test:control-plane` 610 (609 passed, 1 skipped; +3 in `skills.test.mts`);
`tsc --noEmit` and eslint on the changed files clean. Backend untouched.
Review: pending (codex).
Next safe action: review, commit, build (no 8001 restart needed: frontend only, the
static export is read from disk per request), owner checks on 8001.
Review round 1 (codex, gate exit 0): 1 x P2, fixed — library skills that cannot be read
were left out of the dropdown, so with none left to offer the panel said every library
skill was already installed (with only broken entries: "none installed" and "all
installed" at once). `projectSkillsOf` now counts them (`unreadable`); the panel says
"nothing to add" and that N cannot be read, and points at the Skills page. Test extended.
Frontend 610 (609 passed, 1 skipped); tsc, eslint clean. Round 2: pending.

## 2026-11-11 — CR-75 slice 5 follow-up: the library keeps to the Agent Skills standard (workspace CR-91)

Owner: every skill in one format that claude, codex and kiro can all use, and new ones
built that way. The common format exists — the Agent Skills standard
(agentskills.io/specification): `<name>/SKILL.md`, front matter `name` + `description`;
read from `.claude/skills`, `.agents/skills` and `.kiro/skills`. The library already
wrote that shape; this makes it check the standard's rules.

- Refused on write (the standard's "must" for the two required fields): a description
  over 1024 characters; a name with consecutive hyphens (frontend rule too).
- Reported as notes, not refused, so a skill written elsewhere can still be read and
  imported: no front matter, name not the folder's, no description, description too long,
  `compatibility` not text of 1-500 characters, fields outside the standard (other CLIs
  ignore them), SKILL.md over 500 lines. `standard_notes` in `agora/skills/library.py`;
  on each library entry and each importable skill; the Skills page words them (en/zh/ja).
- Not claimed: an empty notes list is not a certificate. The standard's own validator
  (`skills-ref validate`) is the authority and is not run here.

Tests: `test_skills.py` 34 passed (34 collected; +2). Frontend `test:control-plane` 610
(609 passed, 1 skipped); `tsc --noEmit`, eslint on the changed files clean.
Review: pending (codex).
Next safe action: review, commit, build + restart 8001 (backend changed), then convert
kiro-workspace's `.kiro/skills` to the standard (workspace CR-92).
Review round 1 (codex, gate exit 0): 1 x P2, fixed — the stricter name rule was put in the
shared `check_name`, so a skill already named `a--b` could no longer be read, deleted or
uninstalled. `check_name` is back to what every version accepted (lookup, delete,
uninstall); `check_new_name` adds the standard's rule for create and import. Test extended:
`test_skills.py` 34 passed (34 collected). Round 2: pending.

## 2026-11-12 — CR-75 slice 5 follow-up: a kiro-workspace's skills are listed in either layout (workspace CR-92)

Workspace CR-92 converts kiro-workspace's `.kiro/skills/<name>.md` to the Agent Skills
layout `.kiro/skills/<name>/SKILL.md` (measured on kiro-cli 3.1.0: a `skill://` resource
without front matter is not offered to the agent, so none of the ten was loading).
`workflow_commands` read only the flat layout, so after the conversion the Skills page's
"provided by the workflow" list would have lost every kiro-workspace skill and said
nothing — "none" where it meant "moved".

- `agora/skills/inventory.py` `workflow_commands`: a kiro-workspace is read as
  `*/SKILL.md` and as flat `*.md`, so a converted workspace and one that has not been
  converted are both listed. `README.md` is still skipped. Nothing else changes: the
  entries stay `kind: workflow`, shown and not edited, and reserve no library name.

Tests: `test_skills.py` 35 passed (35 collected; +1,
`test_a_kiro_workspaces_skills_are_listed_in_either_layout`). `compileall agora/skills`
and `git diff --check` clean. Backend only; the full non-integration suite was not run.
Review: pending (codex).
Next safe action: review, commit, restart 8001 (backend changed; ask the owner first when
the restart would end an open session).

## 2026-11-13 — overview card settings: a long skill description no longer widens the card (workspace CR-94)

Reported by the owner on stable 8000 (v1.10.0) once the library held 32 skills with
one-paragraph descriptions: opening "add skill" in a card's settings pushed the card to
3345 px (measured; the card is 364 px wide) and the whole overview scrolled sideways.
The description line was `truncate` (no wrap) inside a flex item with the default
`min-width: auto`, so the longest description set the panel's width. With the dropdown
closed, Save / Cancel were squeezed to one character per line by the hint beside them.

- `app/page.tsx`: the settings panel takes `min-w-0`; Save and Cancel do not shrink or wrap.
- `components/project-skills.tsx`: the block, the dropdown and each row take `min-w-0`;
  a description shows two lines (`line-clamp-2`), the full text is the row's `title`.

Tests: `tsc --noEmit` clean, `eslint` clean, `next build` ok; `tests/test_web_ui.py`
14 passed (14 collected). Layout checked by screenshot on dev 8001 (headless Edge):
card 364 px, scrollWidth 364 px with the dropdown open. No backend change; the full
suite was not run.
Review: pending (codex).
Next safe action: review, commit; reaching 8000 needs a release, which the owner asks for.

## 2026-11-13 — overview: a card's settings open in their own window (workspace CR-95)

Owner, on seeing the settings unfold inside the card: one open panel stretched every
card in the same grid row to its height. The settings (name, workflow switches, skills,
Save / Cancel) now render in a native modal `<dialog>` over the overview (max width
`2xl`): focus stays inside, the page behind is inert, it closes on Escape, on the
backdrop, on ✕ and on Cancel, and focus returns to the Settings button. Escape in the
add-skill filter closes that list only (codex round 1: two P2, both fixed). Same state, same save
path; nothing about what is saved or when changes. The card keeps showing a refusal
after the dialog closes, as before.

Tests: `tsc --noEmit` clean, `eslint` clean, `next build` ok; `tests/test_web_ui.py`
14 passed (14 collected). Checked by screenshot on dev 8001 (headless Edge): the dialog
opens over the grid, the card stays 364 x 222 px with the add-skill list open.
Review: pending (codex).
Next safe action: review, commit; reaching 8000 needs a release, which the owner asks for.

## 2026-11-13 — skills: a repository on another machine is said to be elsewhere (workspace CR-96)

A task whose project lives on a server declares that machine's path
(`primary_repo: /srv/...`). Read on Windows it is not absolute, so the card's settings
said "primary_repo is not an absolute path" — true, and no help: the owner asked why.

- `agora/skills/inventory.py`: a value that is absolute for the other kind of machine
  (POSIX path read on Windows, drive path read on POSIX) gives the reason
  "`<key>` is a path on another machine (`<value>`)" and `reason_code: "other_machine"`
  in the project's payload. A relative path keeps the old reason and no code.
- Frontend: `projectSkillsOf` carries `elsewhere`; the card says the repository is on
  another machine, that skills are installed only into a repository on this one, and
  that installing on a remote machine is not supported yet (en / zh / ja).

Not built: installing into a remote project over SSH. Nothing claims it exists.

Tests: `test_skills.py` 36 passed (36 collected; +1); `tests/skills.test.mts` passes
(count below); `tsc`, `eslint`, `next build` clean; `tests/test_web_ui.py` 14 passed
(14 collected); schema check and `git diff --check` clean. The full backend suite was
not run.
Review: pending (codex).
Next safe action: review, commit; reaching 8000 needs a release, which the owner asks for.

## 2026-11-13 — CR log: a RESUME's other tables, cut-off rows, revision ids (workspace CR-97)

The owner asked what "CR table unreadable (malformed)" on two cards meant. Four causes
in the reader, one of them silent:

- `workspace_tasks.py` read the whole `RESUME.md` as the log, so a phase table or an
  acceptance checklist naming a CR made it malformed. `read_cr_log_file` takes a
  `scope`; the workspace reader passes the 「变更记录」 section. No such section: the
  whole file, as before.
- **Silent**: a line between the delimiter and the rows (the task template's own
  example comment) or a blank line inside the log ends the table, and the rows after
  it were dropped unreported — a real log of 12 rows, 6 open, read as "0 rows, 0 open".
  A row line the parser left in a paragraph is now read as a row.
- `CR-113v6` is an id (`v` + digits only); refused, it made the log malformed and
  named CR-113, which was fine.
- The source-line count used `str.splitlines()`, which also breaks on a vertical tab;
  one in a cell shifted the count to the neighbouring row. Split as the parser does.

Not changed: a row with more than eight cells is still malformed. Eight such rows in
two tasks' records are data to repair, not reader behaviour.

Tests: `test_cr_log.py` + `test_workspace_tasks.py` 156 passed, 1 skipped (157
collected; +10, of which 3 fail without the change and were checked to; 3 came from
review rounds 1-2, after the full run: the section is now cut by `section_scope`, which
takes headings from the parser — not a fenced example — and keeps line boundaries). Full backend
non-integration suite without `test_web_ui.py`: 2373 passed, 12 skipped, 4 deselected
(2389 collected). `compileall`, schema check, `git diff --check` clean. No frontend change.
Review: pending (codex).
Next safe action: review, commit; 8001 needs a restart to show it, 8000 a release.

## 2026-11-13 — skills from outside: fetch, list, read, import (workspace CR-98, slice 1)

The owner asked for published skill repositories to be searchable and usable from Agora,
and decided: only the official repository preset, a skill must be read before it is
taken, downloads go direct.

- `agora/skills/sources.py`: one preset source (`https://github.com/anthropics/skills`).
  `fetch` makes a shallow clone into `<data dir>/skill-sources/<id>/` (no hooks, no
  submodules, no credential prompt, no autocrlf; `.git` removed; swapped in only when
  complete). `read` lists every directory holding a `SKILL.md` from that copy, without
  the network: `not_fetched` and `unreadable` are states, not empty lists. `content`
  returns `SKILL.md`, each file's name and size, and the tree hash. `import_skill`
  requires that hash — a skill never opened, or fetched again since, is refused — and
  then uses the library's own `import_from`, so its limits and name rules apply.
- Routes under `/api/skills/sources` (terminal token, like the rest of the page).
- Skills page: an "From outside" section — fetch, filter, open a skill (warning, file
  list, `SKILL.md`), import from the open panel only. en / zh / ja.

Not built, and the page says so: adding a source by hand, updating a library skill when
its source changes, recording which source a library skill came from. Agora does not
inspect a skill's other files; the page says that too.

Checked against the real repository (commit `2c5958e`): 20 skills listed in 5.5 s.
`claude-api` there has a description over the standard's 1024 characters, so the
library refuses to import it — shown as a note, not hidden.

Tests: `test_skill_sources.py` 9 passed (9 collected, new; the 9th from review round 1:
reads and the copy swap share a lock); with `test_skills.py` and `test_web_ui.py`
59 passed (59 collected). Frontend `test:control-plane` 610 passed,
1 skipped (611; +1); `tsc`, `eslint`, `next build` clean; schema check and
`git diff --check` clean. The full backend suite was not run for this change. Not yet
looked at in a browser: 8001 needs a restart to serve the new routes.
Review: pending (codex).
Next safe action: review, commit; restart 8001 and click through; 8000 needs a release.

## 2026-11-13 — skills from outside: a model's one-sentence summary (workspace CR-99)

The owner asked that opening a skill say, in one sentence they can read, what it is for —
in the language the page is set to.

- `agora/skills/summary.py`: one `claude -p` call (`--model haiku`, plan mode, safe mode,
  no session, `--tools ""`, empty scratch cwd; the skill on stdin as a quoted document).
  The answer must be one line of at most 200 characters or it is refused. Kept under
  `<data dir>/skill-summaries/<tree sha>.<language>.json`.
- `sources.summary` + `POST /api/skills/sources/{id}/summary` (`path`, `language`:
  en / zh / ja). The reply carries the content hash it was written from.
- Page: the open panel asks on open and again when the language changes, and shows the
  sentence only for the content and language on screen, labelled as a model's reading
  that can be wrong, can be steered by the skill, and replaces neither a check nor reading.

Only source skills get a summary; library skills and the ones found in CLI folders do not.

Checked with the real model on three skills (zh, zh, ja): one sentence each, 5-7 s.
Tests: `test_skill_sources.py` 17 passed (17 collected; +8, the last from review round 1:
one summary at a time, so overlapping requests ask the model once); with
`test_skills.py` and `test_web_ui.py` 67 passed (67 collected). Frontend `test:control-plane` 611 passed,
1 skipped (612; +1); `tsc`, `eslint`, `next build` clean; schema check clean. The full
backend suite was not run. Not yet looked at in a browser.
Review: pending (codex).
Next safe action: review, commit; restart 8001 and open a skill.
