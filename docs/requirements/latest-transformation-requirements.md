# Latest Agora Transformation Requirements

Status: active product requirements baseline

Captured: 2026-07-18

Applies to: Agora Control Plane transformation after the protocol/domain freeze

## 1. Provenance and authority

This document preserves the effective product requirements recovered from the
user-provided external file `最新的需求.txt` dated 2026-07-18. The source file
contained a conversation transcript, tool output, proposed designs, and partial
implementation history. Those transient details are not copied as repository
truth.

This checked-in document is the durable requirements source. The external file
is provenance only and must not be required to resume development. Current
implementation and review status remains authoritative in
`.agora/development/PROGRESS.md`; protocol semantics remain authoritative in
`docs/architecture/protocol-domain-freeze-v1.md` and its executable models.

## 2. Corrected product definition

Agora must be a unified, durable Task orchestration entry point for coordinated
work by Codex, Claude Code, and Kiro. Its architecture is a control plane, but
the product must not degrade into a passive monitoring console or require the
user to operate three native CLIs and copy prompts between them.

UI is not a prerequisite for the first usable vertical slice. A CLI, TUI, or
workflow entry point is acceptable if it provides the same authoritative Task
workflow. A later UI should project that workflow rather than invent parallel
state or business rules.

The five non-negotiable capabilities are:

1. one unified entry point for a Task;
2. explicit, risk-aware division of work across the three runtimes;
3. truthful Token and cost budgeting, reservation, and settlement;
4. durable, inspectable, idempotently resumable workflow state;
5. quality Gates that budget pressure or process success cannot bypass.

## 3. Unified Task workflow

A Task is the user's durable unit of consultation, decision, execution,
review, intervention, and completion. The target interaction sequence is:

```text
create/start Task
  -> bind and pin a versioned MethodologyDefinition
  -> consult and compare options
  -> explicitly decide or adopt a candidate
  -> execute the derived next safe action
  -> independently review implementation and methodology boundaries
  -> evaluate Artifact/Evidence/Approval Gates
  -> perform bounded rework or human escalation
  -> hand off or complete
```

The unified interface must eventually support the equivalent of:

```text
agora task start
agora task consult
agora task decide
agora task next
agora task status
agora task resume
agora task retry
agora task approve
```

The exact command syntax is not frozen here. The behavioral contract is.
Consultation output is a candidate, not authoritative state. Only an explicit
adopt, approve, reject, execute, or block action may create or change formal
Artifacts, Approvals, Runs, Stages, or Gates.

## 4. Runtime roles and routing

Default responsibilities are:

- Codex: implementation planning, code changes, tests, and fixes;
- Claude Code: independent correctness, safety, and regression review;
- Kiro: AI-DLC methodology, protocol, lifecycle, and delivery-boundary review;
- Agora: the only cross-runtime workflow-state writer, responsible for routing,
  budgets, Context/Handoff contracts, Gates, reconciliation, and recovery.

Agora must not make all three runtimes repeat every step. Routing must consider
Stage, capability, risk, budget, and required independence. Ordinary
implementation may use Codex plus Claude review; AI-DLC boundary changes add
Kiro; high-risk work may require both independent reviewers; simple
consultation may use only the best-suited runtime. Every reduction or expansion
of the reviewer set must record an explainable reason. Material disagreement
must escalate to the user under an explicit adjudication rule.

## 5. Token, cost, and quality budgets

Budgeting and accounting are first-class Task capabilities:

```text
Task envelope
  -> Stage allocation
    -> Run reservation before dispatch
      -> Run settlement after termination/reconciliation
```

Every Run must record, where available:

- runtime and model;
- input, output, and cache Tokens;
- monetary cost or native CLI credits;
- duration;
- `exact`, `estimated`, or `unavailable` measurement status;
- source and estimation method;
- allocated, reserved, settled, and remaining amounts.

Unavailable provider usage must never be written as zero. Independent review
and final verification require protected budget. Cost limits and risk tolerance
are separate policies: insufficient budget must not silently weaken a required
quality Gate. Agora must instead block and ask the user to increase the budget,
reduce scope, or choose a cheaper runtime/model that still satisfies the
capability and independence requirements.

## 6. Executable methodology contract

AI-DLC must be a versioned executable definition, not metadata. A complete
`MethodologyDefinition` must include:

- the Stage graph and stable identities;
- entry and exit conditions;
- allowed branches and rework edges;
- Stage Contracts and required outputs;
- required Artifacts, Evidence, Approvals, and Gate requirements;
- runtime role/routing policy;
- budget and quality policy;
- maximum rework counts and human escalation conditions;
- downstream invalidation rules for upstream changes.

A Task pins the methodology identity, version, and hash when it is created.
Changing methodology during execution requires an explicit migration Gate and
must never silently alter the workflow.

On 2026-07-28 the user identified the AWS AI-DLC Method Definition and
`awslabs/aidlc-workflows` as Agora's authoritative AI-DLC source. The
source-bound `MethodologySourceGraph@1.0` pins upstream release `v2.3.0`, its
peeled commit, every Stage/scope source hash, all 32 source Stages, all 9 scope
profiles, and their dependency DAG.

This source freeze does not make the graph executable. Upstream `v2.3.0`
retains rework, retry, and halting behavior partly in prose and reserves
structured `on_failure`, `timeout`, and `retry` fields for future releases.
Agora must not invent those values. The separately sealed
`MethodologyActivationDefinition@1.0` now binds Stage Contracts, required
outputs, Artifacts/Evidence/Approvals/Gates, runtime independence, budget
policy, the authored source-review bounds, escalation, and migration policy.
It deliberately has no routing, dispatch, or migration authority. The
repository's `agora-aidlc-foundation@0.1` and every existing Task remain
unchanged. The read-only `MethodologyMigrationPreviewRequest@1.0` /
`MethodologyMigrationPreviewDecision@1.0` path evaluates an exact
`successor_task` proposal, including Task/Plan/inventory optimistic locks,
repository/source/activation hashes, scope seeds, runtime pins, per-Stage and
protected runtime budgets, a human assertion, and quiescence. It never writes
state or grants migration authority. The reviewed transactional writer
authenticates the asserted approver through a configured Control Plane
credential, persists the migration Gate, rechecks every binding inside one
write transaction, and atomically creates a distinct successor Task, Plan, and
sealed grouped inventory. It preserves the predecessor and leaves the
successor non-dispatching with no activated route.

## 7. Quality and recovery invariants

- Review and approval bind to repository, ref, commit, Stage, Artifact path,
  and Artifact hash.
- Independent review is a formal Gate record, not commentary embedded in the
  implementation Run.
- Exit code zero is not semantic success.
- Rework is bounded and escalates after its configured limit.
- Resume is idempotent and must not duplicate dispatch, state transitions, or
  charges.
- Context Packs may be minimal but must retain audit and Evidence references;
  full transcripts are not handoff contracts.
- Runtime disagreement has a deterministic resolution/escalation path.
- Progress is derived from authoritative Stage, Run, Artifact, Evidence, Gate,
  and Attention state, never an ungrounded percentage.

## 8. Task status and future workbench projection

The backend must expose one authoritative Task projection containing at least:

- Task state and current Stage;
- current and historical Runs, runtime, elapsed time, and wait state;
- completed/current/remaining Stage progress;
- semantic results and generated Artifacts;
- Evidence and Gate pass/block/stale reasons;
- unresolved Attention and required human actions;
- decisions, approvals, failures, retries, and audit history;
- the Gate-derived next safe action;
- budget allocation, reservation, settlement, and remaining capacity.

Run Center, Attention Center, and Portfolio remain useful cross-Task operational
views. If a Task Workbench UI is later built, it must be the single-Task work
surface for consultation, decision, execution, observation, and intervention.
It must consume the authoritative projection and command API. Ordinary Task
work should not require leaving that surface, but UI implementation remains
deferred until the CLI-first orchestration path is proven.

## 9. Current implementation alignment

The reviewed Agora 1.0 baseline now includes:

- frozen protocol/domain models, checked-in matching Schemas, Control Plane v2
  persistence, and deterministic reconciliation;
- concrete versioned Task contracts with roles, ordered workflow,
  Context/Handoff expectations, acceptance criteria, and required
  Artifacts/Evidence/Gates;
- authoritative grouped Stage inventory, lifecycle derivation, linear routing,
  protected review budgets, versioned amendment, retry, and resume semantics;
- bounded native capability observation and fail-closed preflight for pinned
  Codex, Claude Code, and Kiro CLI routes without runtime substitution;
- the source-bound AWS AI-DLC graph, migration preview and activation,
  execution contract, all eight formal Stage claim/dispatch/settlement paths,
  independent completion reviews, and artifact-bound human completion;
- an explicit single-runtime consultation path whose immutable result is
  advisory until a human adopts or rejects it;
- one unified authoritative Task projection plus authenticated project/Task
  discovery, Attention response, candidate disposition, and atomic Plan
  approval in the static Control Plane;
- deterministic end-to-end formal acceptance covering three runtime labels,
  every Stage/Gate, explicit human approval, cold SQLite reopen, and temporary
  cleanup without calling a provider or model; and
- fail-closed retirement and source removal of the 0.5 autonomous Council.

Native CLI subscriptions remain external dependencies. An unavailable pinned
runtime truthfully blocks its Stage; it is not silently replaced and does not
invalidate the deterministic control-plane acceptance. The current Kiro
adapter and configuration remain present while the user's Kiro contract is
temporarily unavailable.

Post-1.0 enhancements are intentionally separate:

- dynamically configurable role graphs, arbitrary local-model adapters, and
  policy-governed runtime substitution;
- additional provider/model-specific exact usage and cost integrations;
- POSIX/remote embedded-runner isolation and large shared-repository
  invalidation operations; and
- further UI convenience beyond the reviewed authoritative Task console.

## 10. Agora 1.0 acceptance

A user can enter through Agora, operate one concrete Task without manually
coordinating native CLI state, inspect truthful Run and budget dimensions,
receive explicit semantic blockers or results, resume safely, and pass or fail
formal Gates based on version-bound Artifacts and Evidence. Implementation
budget cannot remove mandatory review, and process exit zero cannot advance a
semantically blocked Stage.

`scripts/run_task_acceptance.py` is the reproducible no-provider control-plane
proof. The release checklist, locked startup requirements, live HTTP checks,
and explicit post-1.0 exclusions are frozen in
`docs/architecture/agora-1.0-release-readiness-v1.md`. Current test/review
evidence and the exact release commit remain recorded in
`.agora/development/PROGRESS.md`.
