# Agora Control Plane Development Progress

Branch: `feat/control-plane-phase1`

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
