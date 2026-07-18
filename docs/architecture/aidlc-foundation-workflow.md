# AI-DLC orchestration foundation

Status: provisional CLI-first vertical slice

This increment restores one unified Task entry point without claiming that the
authoritative AI-DLC phase graph has been recovered. The built-in
`agora-aidlc-foundation@0.1` method is deliberately limited to a read-only
planning and review loop:

1. Codex produces an engineering solution design.
2. Claude Code independently reviews correctness, safety, and regression risk.
3. Kiro independently reviews methodology boundaries and quality gates.
4. A human explicitly approves the reviewed plan.

The three native CLIs run sequentially so later reviewers receive bounded,
verified prior-stage results rather than a full transcript. They are instructed
and configured for read-only planning. A successful process is not enough: each
runtime must return the required structured semantic result. Invalid output,
`needs_work`, `blocked`, a non-zero exit, timeout, or estimated token overrun
blocks the plan.

Process exit, timeout, semantic status, and usage measurement are persisted as
separate dimensions. A timeout blocks even if a racing process reports exit
code zero and returns a schema-valid semantic result.

## Unified entry point

```powershell
agora task start "Describe the delivery goal" --tokens 30000 --cost-usd 20 --run
agora task start --contract docs/examples/bounded-control-plane-api-task-contract.json --run
agora task status TASK_ID
agora task decide TASK_ID inbound_authorization_policy --value "Use control-plane-api-access-policy-v1" --reason "Human-approved API boundary"
agora task next TASK_ID
agora task run TASK_ID
agora task resume TASK_ID
agora task retry TASK_ID STAGE_KEY
agora task approve TASK_ID --reason "Reviewed all three results"
```

`start` creates a normal Agora Task plus a version-pinned method plan. `attach`
can bind the method to an existing Task. `status --json` exposes plans, stages,
runs, blockers, usage ledger entries, and the next safe action for a future UI
or TUI without duplicating business rules.

`start --contract PATH` loads a strict UTF-8 JSON Task contract containing the
roles, ordered workflow, Context/Handoff expectations, acceptance criteria, and
required Artifact/Evidence/Gate templates. The contract must align exactly with
the pinned provisional methodology stage order and runtime assignments. Agora
persists its canonical content, identity, schema version, and SHA-256 with the
Task and supplies a hash-bound, Stage-scoped projection to every runtime. This
leaves bounded room for verified prior-stage results without weakening contract
identity or sending a full transcript. The checked-in
`docs/examples/bounded-control-plane-api-task-contract.json` is the first
concrete contract; it defines planning and review for the next bounded API
increment and does not claim that the planned formal outputs already exist.

`decide` is available only while the Plan and current Stage are blocked. It
records an immutable, versioned human decision, redacts secret-like content at
the persistence boundary, updates the Plan version atomically, and appends a
Task audit event. Repeating the same latest decision is idempotent; changing it
adds a new version. Latest decisions are included in subsequent bounded runtime
prompts, while full decision history remains visible in `status --json`.

Runtime result extraction permits one format-only recovery: Agora may locate
one schema-valid semantic JSON object inside prose or a fenced block. No fields
are invented or altered, more than one valid result fails closed, and candidate
scanning is bounded. A recovered `needs_work` or `blocked` result remains a
semantic blocker.

## Budget and accounting boundary

The Task token envelope is allocated 45/30/25 across Codex, Claude, and Kiro.
Each Run writes an append-only reservation before process launch and a
settlement afterward. Because the installed native CLIs do not expose one
portable billing contract, token use is currently a UTF-8-size estimate and
cost is explicitly `unavailable`. Missing cost is never written as zero.

The token envelope is a soft native-runtime limit in this foundation: the
prompt asks the runtime to stay within it, and Agora blocks a semantic pass when
estimated usage exceeds the reservation. Exact provider usage and hard model
limits require version-matched adapter support in a later increment.

## Safety and non-goals

- This method does not implement or rename the missing authoritative AI-DLC
  phases. Its identity and `provisional` flag prevent that confusion.
- It does not modify repository files, advance the legacy Task to done, or
  claim product delivery.
- Human approval means only that the reviewed plan is ready for a later
  implementation workflow.
- Existing Context/Handoff, Artifact/Evidence/Approval, Gate, invalidation, and
  Runner contracts remain the target for the implementation-stage increment.
- A hard-crashed CLI Run is never silently duplicated. `resume` refuses while
  the recorded PID appears alive or cannot be inspected, and otherwise records
  an interruption that requires an explicit retry. Windows uses a read-only
  process handle query; `os.kill(pid, 0)` is used only on POSIX, where signal
  zero is a non-destructive liveness check.
- A claimed Run whose PID was not durably attached is treated as unknown and
  requires investigation; recovery never assumes that its child process is
  dead. Duplicate operation claims conflict before another runtime is spawned.
- Runtime output, errors, semantic summaries, findings, blocker text, and audit
  payloads cross one redacting persistence boundary before they are stored.
- Run start, finish, interruption, retry, and approval decisions append Task
  audit events in the same transaction as their authoritative state changes.
