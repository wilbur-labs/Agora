# Agora Control Plane Development Progress

Current branch: `main`

Current recovery baseline:

- Last released commit: `fce21ad docs: add Ubuntu Docker acceptance`.
- Active program: Agora Protocol & Domain Freeze.
- Consensus source: Codex, Claude Code, and Kiro CLI accepted C1-C16 on
  2026-07-16 with no blocking objections.
- User-owned unrelated worktree change:
  `frontend/pnpm-workspace.yaml`. Preserve it and exclude it from Agora
  implementation commits unless the user explicitly assigns it.
- Review gate remains mandatory: tests, independent Claude review, fixes,
  rerun, then commit.
- Current next safe action: implement and verify the v1 protocol models,
  checked-in JSON Schemas, deterministic Gate evaluation, Approval
  invalidation, M2 publication rules, Runner isolation, and real-history
  regression fixtures.

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

- Default progress metric from this point forward: the agreed 11-stage Agora
  Control Plane transformation, not the legacy Agora 0.5 feature completion.
- Completed transformation stages: 2 of 11:
  1. protocol, domain model, and state-machine freeze;
  2. Control Plane persistence and Registry.
- Milestone-count completion: approximately 18%.
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

## 2026-07-17 — Task Workbench demo (active)

### Scope

- [x] Added a bounded HTTP surface for creating or attaching the provisional
  orchestration plan, reading status, running the next stage, reconciling an
  interrupted run, retrying a blocked stage, and recording human approval.
- [x] Enforced bounded identifiers, request payloads, budgets, project/Task
  scope, and explicit 404/409/422 mappings at the route boundary.
- [x] Moved orchestration SQLite and project-registry I/O off the FastAPI event
  loop while preserving cancellation reconciliation for native CLI runs.
- [x] Added a static `/tasks` Task Workbench with Task selection, guided Task
  creation, plan/stage/run/usage visibility, next-stage execution, retry,
  recovery, and human approval.
- [x] Kept the UI explicit that `agora-aidlc-foundation@0.1` is provisional,
  read-only planning and does not dispatch implementation.
- [x] Added API acceptance coverage, a static-route regression, and local demo
  instructions with the trusted-local-server limitation.
- [x] Run the required backend, frontend, Schema, compile, and whitespace
  verification.
- [x] Obtained Kiro methodology/API-boundary review and independent Claude
  correctness/security/UI review; fixed all actionable findings and received
  approval from both reviewers.
- [x] Commit the reviewed Demo increment locally (the commit containing this
  snapshot).

### Verification and review log

- Focused orchestration and API suite before review: 22 passed; after review
  fixes: 24 passed, with 1 existing Starlette/httpx deprecation warning.
- Backend non-integration suite without static-route tests: 309 passed,
  18 deselected, 3 existing dependency/Windows event-loop cleanup warnings.
- Full backend non-integration suite before review fixes: 354 passed,
  18 deselected; final post-review suite: 356 passed, 18 deselected, with the
  same 3 existing warnings.
- Full frontend ESLint: 0 errors and 12 pre-existing warnings in untouched
  legacy files.
- Next.js production build and static export: passed; 15 pages generated,
  including `/tasks`.
- Protocol Schema export check, Python compile, and `git diff --check`: passed.
- `corepack pnpm build` attempted a registry metadata check and failed in the
  restricted network environment; the repository-installed Next.js binary
  completed the same production build successfully without dependency changes.
- Kiro review: `APPROVE`; no high/medium methodology, state-ownership,
  reconciliation, API-boundary, budget, or provisional-labeling findings.
- Claude review pass 1 timed out without a verdict after three minutes. Its
  review process was identified by start time and terminated without touching
  older user-owned Claude processes.
- Claude review pass 2: `CHANGES_REQUESTED` for a cross-Task stale action-result
  race, missing API-level block/retry and live-PID resume regressions, and
  incomplete modal keyboard/focus handling.
- Bound every action result to its originating Task and request sequence; added
  block-to-retry and live-process duplicate-dispatch API tests; added initial
  focus, Tab containment, Escape handling, and focus restoration to the create
  dialog.
- Claude targeted re-review: `APPROVE`; all three findings were confirmed fixed
  with no remaining high/medium findings.
- Final changed-file and full frontend ESLint passed; the 12 warnings remain in
  untouched legacy files. Final production build/static export passed.
- No Kiro or review-owned Claude process and no `.gitconfig.lock` remained.
  Two Claude processes that predated the review were preserved as user-owned.

### Recovery note and next safe action

The pre-existing `frontend/pnpm-workspace.yaml` change remains user-owned and
must not be staged. The review gate is complete. If interrupted before commit,
stage only the files listed in this Demo increment and commit them together.
After commit, the next safe action is local user acceptance at `/tasks`; use
that feedback to bound the next consult/decide or formal Control Plane API
increment. This Demo does not count as completion of the full AI-DLC,
Context/Handoff, formal Gate, consult/decide, or implementation workflow
stages.
