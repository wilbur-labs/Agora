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

The authoritative full AI-DLC diagram or source specification is still
missing. The repository's `agora-aidlc-foundation@0.1` is intentionally
provisional and must not be renamed or presented as the recovered full method.
The authoritative graph must not be invented from chat history or Council
screenshots; the user must provide or identify the original source before it is
formally frozen.

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

Already present and reviewed as of commit `2750dfe`:

- frozen protocol/domain contracts and Control Plane v2 persistence;
- provisional version-pinned `agora-aidlc-foundation@0.1`;
- one `agora task` CLI entry point for start/attach/status/next/run/resume/
  retry/approve;
- sequential read-only Codex -> Claude -> Kiro planning/review loop;
- append-only Token reservation/settlement with measurement truthfulness;
- restart safety, Windows native runtime recovery, and bounded output capture.

Still required:

- the authoritative full AI-DLC method source and executable graph;
- a concrete Task contract with roles, process, Context/Handoff expectations,
  acceptance criteria, and required Artifacts/Evidence/Gates;
- consult and decide/adopt semantics under the authoritative Task;
- risk/capability-aware dynamic routing and recorded rationale;
- provider/model-specific exact usage where available and protected review
  budgets;
- the unified authoritative Task status/progress/result projection;
- a real CLI-first three-runtime end-to-end Task through review, Gate,
  intervention, resume, and handoff;
- only after that proof, an optional Task Workbench UI.

## 10. Acceptance criteria for the next vertical slice

The next product slice is acceptable only when a user can enter through Agora,
operate one concrete Task without manually coordinating native CLIs, inspect
truthful Run and budget state, receive an explicit semantic blocker or result,
resume safely, and pass or fail formal review/Gate checks based on version-bound
Artifacts and Evidence. The slice must demonstrate that implementation budget
exhaustion cannot remove mandatory review and that a process-level success
cannot advance a semantically blocked Stage.
