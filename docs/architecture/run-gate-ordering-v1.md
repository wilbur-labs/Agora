# Run → Gate Ordering v1

Status: frozen. Decided 2026-08-29.

Changes **when an execution run may be created**. Read this before relaxing or
widening either refusal.

## The rule this enforces

`AGENTS.md` states the product mainline as
`Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done`,
and that Agora is the only writer of cross-runtime Task, Stage and Gate state.
A new worker run created while the previous run's gate is still open runs that
line backwards: it builds on work that has not been accepted, and the approval
that eventually lands is bound to a repo/ref/commit/artifact that the newer run
has already moved.

Until this change the ordering was enforced **only on the client**
(`frontend/src/lib/continue-run.ts`, `continueEntryGate`). That is not a
contract:

- `expected_task_version` cannot substitute for it. Raising an Attention item
  does not bump the task's version, and a task that is already `running` is not
  bumped by queueing a run either — so two pages, or one stale page, both pass
  the optimistic check.
- The pre-existing `_reject_second_active_run` guard is scoped to the *role*, so
  it refuses a duplicate worker run and says nothing about a gate.
- The client's `followUpArmed` window is a 30-second timer. A timer is a
  heuristic; the ordering is a contract.

## What is now refused, in the create transaction

Both checks run in `ExecutionStore.create`, inside the same `BEGIN IMMEDIATE`
transaction that inserts the run, and **only for `role == worker`**. A review
run is how a gate gets something to decide; gating it would deadlock the cycle it
belongs to.

### 1. An open human gate on the task — `attention_gate_open`

Refused when the task has an Attention item that is `open`, whose `expires_at`
has not passed, and whose `kind` gates. Per kind:

| kind | gates | why |
| --- | --- | --- |
| `approval` | **yes** | It *is* the gate. `AGENTS.md` binds an approval to repository, ref, commit, stage, artifact path and artifact hash. Creating the next run while the approval is open makes that binding meaningless, and the auto cross-review raises exactly this kind on success. |
| `blocker` | **yes** | The task is stuck on something only a human can clear. Another worker run does not clear it — it spends tokens on the same wall — and the auto cross-review raises this kind when the *review run itself* failed, which is the "the review did not pass" signal that must not be built on. |
| `question` | **no** | It binds to no artifact and settles no decision: it is the agent asking for information. Answering later is legitimate, and in practice a better-prompted run often *is* the answer. The client still declines to offer Continue while a question is open; that is a helpful default a human can override by answering or cancelling the item, not an invariant. |

**What excluding `question` does not buy.** An earlier draft of this document
claimed that exempting `question` also exempts the permission prompts the Codex
app-server bridge raises mid-run. It does not, and the claim was false when it
was written. `bridges/codex_protocol.decode_approval` raises
`kind="approval"`, and `bridges/normalize.normalize_hook_event` raises
`approval` for a vendor `permissionrequest` too — a permission prompt is a
decision bound to a command or a file change, so `approval` is the correct kind
and is not going to change. Every Codex permission prompt therefore gates, on
the default runtime path. The lockout that creates, and how it is closed, is the
next section. Do not re-derive an exemption from the `question` row: it covers
the agent asking a *question*, which is a different event from the same bridge.

`responded`, `cancelled` and `expired` items never gate. An item whose
`expires_at` has passed but whose row has not been swept by
`AttentionStore.expire_overdue` yet is treated as expired, so the refusal does
not depend on when that sweep last ran.

A database with no `attention_items` table cannot hold an Attention item, so
there is nothing to gate on. That is a fact about the schema, not an unreadable
state: `ExecutionStore` initializes only the execution schema, and a deployment
that never constructs an `AttentionStore` has never raised an item. This is the
one place the check returns "no gate" without reading rows, and it is not a
fail-open on an error path.

### 1a. The lockout this refusal created, and how it is closed

Because permission prompts are `approval`, the refusal above turned an
*unanswered* Codex permission prompt into a permanent task-level lockout. The
sequence, all on the default path:

1. Codex asks to run a command. The bridge raises an `approval`, bound to the
   run, with no `expires_at`, and blocks the turn.
2. The human does not answer. The run times out, is cancelled, or is abandoned.
3. Nothing settled the item. It is still `open`, and it can no longer be
   answered: the answer travels back over the app-server connection of a process
   that no longer exists.
4. Every subsequent worker run on that task is refused with
   `attention_gate_open`, until a human finds the orphan and cancels it through
   the API.

That is a denial of service this change caused, not a pre-existing one. It is
closed by settling the prompt in the same transaction that settles its run:
`ExecutionStore._transition` calls
`agora.attention.run_scope.settle_bridge_attention_for_run` on every terminal
transition, which cancels the run's still-open bridge items with the reason
"the run that raised this prompt ended before it was answered" and fails any
undelivered bidirectional delivery.

**The cut is by origin, not by run state**, and that distinction is the whole
design. `ExecutionDispatcher._raise_review_attention` raises the review gate
*after* its review run is already terminal, scoped to the worker run it
reviewed — which is terminal too. A rule of the form "the run ended, so cancel
its open items" would delete the review gate on the way to fixing this, which is
worse than the bug. So the sweep matches only items that have a row in
`attention_bridge_events` (present if and only if the item came from
`create_bridge_event`, i.e. a vendor bridge raised it during a run) and whose
`requester` is not `REVIEW_REQUESTER`. The second condition is redundant given
the first and is checked anyway.

`test_the_review_gate_survives_the_orphaned_prompt_sweep` in
`backend/tests/test_execution.py` is the regression that guards it. **What that
test has to contain, and originally did not.** An earlier version of this
document called that test the guard for the origin-vs-state distinction; it was
not, and the claim was stronger than the test delivered. Every arrangement it
built survives a run-state sweep too, because such a sweep keys on the settling
run's own id: a gate raised *after* both its runs are terminal has no run left to
settle, and an unrelated run of the same task settling carries a different
`run_id`. The sweep could have been widened back to run state with the test still
green. The one arrangement that separates the two rules is a `REVIEW_REQUESTER`
item scoped to the very run being settled and *open at the moment that run
settles* — a review gate on a review run that has not finished yet. That
arrangement is now built explicitly in the test, and the guard is verified by
mutation: widening `settle_bridge_attention_for_run` to match on run state alone
makes the test fail on that assertion.

**Residual, stated rather than covered.** Three things this does not fix:

- An item raised by `AttentionStore.create` with a `run_id` — anything other than
  a bridge — is not swept. That is deliberate: such items are control-plane
  decisions (the review gate is one), and they are supposed to outlive their run.
  A *new* caller of `create` that raises a genuinely run-scoped, unanswerable
  prompt would reintroduce the lockout, and nothing here detects that.
- A prompt raised against a run whose row is deleted, or whose terminal
  transition never happened (a database edited by hand), is still orphaned.
- The prompt is cancelled, so the work the agent was blocked on is lost. That is
  the honest outcome — the process is gone — but it means an unanswered
  permission prompt costs a run, not merely a delay.

### 2. An unsettled review run on the task — `review_run_active`

Refused when the task has a `queued` or `running` run tagged `role = review`.
This is the half of the ordering that *is* atomically enforceable: the review run
is a row, in this database, readable in this transaction. Without it, a worker run
created while its predecessor's review executes is refused by nothing — the
same-role guard only compares worker against worker.

## The half that is NOT enforceable atomically, and was not enforced

Between a worker run settling and its review run being created there is a real
window: `ExecutionDispatcher._after_run` publishes the worker run's terminal
state, *then* `_dispatch_review` shells out to git to build the review prompt
(readiness check, `list_changes`, file reads), *then* inserts the review run. In
that window no run is active, no gate exists, and both checks above pass.

Closing it atomically requires a durable "a review is owed" marker written **in
the same transaction that settles the worker run**. That was considered and not
done. The cost:

- The decision "is a review owed?" is dispatcher knowledge, not store knowledge:
  it depends on the `auto_review` config flag, on the project's `reviewer_agent`,
  and on that agent being in the adapter registry. `ExecutionStore.finish` would
  need a new `review_owed` argument computed by the caller before the settle.
- `finish` is called from four sites, and `record_cancelled_output` and `abandon`
  are two more terminal paths. Any one of them that forgot the argument would
  leak a marker or drop one.
- The marker is a second durable state machine with its own stale-lockout: a
  crash between the settle and the review insert leaves it set, so it needs its
  own startup reconciliation alongside `recover_abandoned` — either clearing it
  (silently dropping the review) or re-dispatching (re-running a reviewer against
  possibly-changed state).

That is a new failure surface to close a window measured in seconds, bounded by
the dispatcher's own next action, in a process that is already the only writer.
It is left to the client's `followUpArmed` window, and the residual is accepted
and recorded here rather than described as covered.

### What *is* done instead: the failure is visible and it gates

The marker above is about the *window*. A separate and larger hole was that a
cross-review dispatch which simply **failed** left no trace at all.
`_dispatch_review` returned silently when the project had left the registry or its
`reviewer_agent` was not in the adapter registry, and `_after_run` wrapped
everything else in `except Exception: pass` — so a failed prompt build, a request
the control plane rejected, a queue conflict, and a failed gate write all ended as
"no review exists". Both the Continue action and `create` then accepted another
worker run, because nothing was active and no gate was open. An unavailable
reviewer read as "this work needs no review", which is a silent substitution of
the kind `AGENTS.md` forbids.

So every path that ends without a review run now records a durable `blocker`
Attention item on the task (`ExecutionDispatcher._block_review_owed`), scoped to
the run whose review is missing, naming what failed in its body. `blocker` is one
of `GATING_ATTENTION_KINDS`, so while it is open the create transaction refuses
another worker run and the Continue action declines to offer one: the failure
blocks its work instead of deleting the review stage.

It is idempotent on `(task_id, run_id, requester, title)`. The title is fixed for
that reason and the varying detail lives in the body, so a retry or a redelivered
failure finds the existing open item and leaves it exactly as it is — not even
updated, because the first failure is the one that gated the task and a human may
already be reading it.

The existence test is `AttentionStore.create_once`: an exact query and the insert,
in one `BEGIN IMMEDIATE` transaction, over items that are open and not past their
expiry (the same set `_open_gate_items` counts as a gate, so an unswept expired row
cannot suppress an item that would gate). It was originally a scan of the first 50
open blockers on the task, read through `AttentionStore.list` from the dispatcher.
That was wrong twice over: attention ordering puts critical items first, so 50
critical blockers scoped to the same run pushed the existing item out of the page
and a redelivery created a duplicate carrying the identical key; and a capped page
cannot answer an existence question at all. The read-then-create also was not one
transaction, which no query in the caller could have fixed — hence the store
method.

Two things this does **not** do:

- It is not the "a review is owed" marker. It fires when a dispatch *fails*, not
  when one has not been attempted yet, so the window in the previous section is
  still the window in the previous section.
- A task whose row is gone gets nothing: an Attention item hangs on a task, so
  there is nowhere to record it. The run's own record is the only trace, and that
  is the honest end of that path.

## Alternatives rejected

- **Bump the task version when an Attention item is raised**, so
  `expected_task_version` refuses the dispatch. Rejected: it makes every attention
  item a task mutation, invalidates unrelated in-flight optimistic writes, and
  reports "the task changed" for what is actually "a gate is waiting" — the same
  loss of meaning this change exists to fix.
- **Refuse on any open Attention regardless of kind** (matching the client
  exactly). Rejected: `question` binds to no artifact and settles no decision, so
  a task-level refusal on one asserts a gate that does not exist — and unlike a
  permission prompt, a question is answerable after its run is gone (the answer is
  information, not a reply to a blocked process), so it has no natural point at
  which it can be settled automatically. Note what this rejection does **not**
  buy: Codex permission prompts are `approval`, not `question`, so they gate
  either way. See section 1a.
- **Settle a finished run's open Attention by run state** — "the run ended, so
  cancel its open items" — as the fix for the section 1a lockout. Rejected, and it
  is the trap here: `_raise_review_attention` raises the review gate *after* its
  review run is terminal, scoped to a worker run that is also terminal, so this
  rule deletes the gate this document exists to enforce. The cut is by origin
  (a row in `attention_bridge_events`), not by run state.
- **Give bridge prompts an `expires_at` instead** of settling them with their run.
  Rejected as the primary mechanism: a timeout makes the lockout shorter, not
  absent, and it makes the window depend on when `expire_overdue` last ran and on
  a duration nobody can pick correctly (a human may legitimately take an hour to
  answer a permission prompt while the run is still alive). Settling on the run's
  terminal transition is exact: the prompt becomes unanswerable at precisely that
  moment. An expiry could still be added as a second belt; it is not a substitute.
- **Enforce it in the router instead of the store.** Rejected: a check outside the
  transaction is the TOCTOU hole being closed, and `queue()` is not the only
  caller of `create()`.
- **A plain 409 with prose.** Rejected: the panel cannot tell "wait for the run
  that is already going" from "settle the gate that is waiting for you" without
  sniffing message text, and those need different words and a different action.

## Failure mode being accepted

A stale, unanswered `approval` or `blocker` blocks every new worker run on its
task. This is deliberate — that is what a gate is — and it is why the refusal is
narrow and reversible:

- Only two kinds gate, and only while genuinely open.
- The 409 body names each blocking item by `item_id`, `kind` and title, and states
  the two ways out: respond to it (approve/reject, or answer) or cancel it. Both
  are existing API operations, so no restart and no database surgery is needed.
- Review runs are exempt, so the review cycle can always complete.
- A prompt that has become *unanswerable* is not left as a gate: a bridge item is
  settled with its run (section 1a), so the items that persist are the ones a
  human can still act on.
- An unsettled *review run* cannot become a permanent lock either:
  `recover_abandoned` settles a RUNNING one on restart, and the dispatcher settles
  a QUEUED one on shutdown, and a human can cancel it. (The shutdown half is a
  same-change fix: a run whose coroutine was cancelled before the event loop ever
  entered it used to stay QUEUED forever.)

Note that this lockout already existed in the UI, which has refused to offer
Continue on any open Attention since the Continue action shipped. This change
makes the narrower half of that rule authoritative; it does not invent a new
class of stuck task — with the one exception recorded in section 1a, which it did
invent and does close.

## Persisted QUEUED runs, and where the gates are re-checked

Both checks run in the transaction that *inserts* the run, and nothing re-checks a
row already inserted. A QUEUED row therefore outlives the state it was validated
against: across an upgrade from a database written before these gates existed, and
across a crash. `recover_abandoned` settles RUNNING rows and says nothing about
QUEUED ones, so `ExecutionDispatcher.resume_queued` used to reschedule persisted
QUEUED runs unconditionally — a worker run starting while its predecessor's review
was queued or running, or while a human gate waited, which is exactly what
`create` refuses.

`ExecutionStore.revalidate_queued` re-asks the questions at resume, and
`resume_queued` schedules only what it admits. **Swept at resume, with the
conflicting rows settled there, rather than left for each row's own launch.** Two
reasons:

- Determinism. One sweep, oldest first, in one transaction, at startup before the
  API serves: which run survives a conflict between two persisted rows is decided
  by queue order, not by whichever coroutine the event loop reaches first.
- The record, in both paths. A refused run must not end FAILED — that reads as "the
  agent ran and did not succeed", a lie about a process that was never started.
  CANCELLED with a stated reason is what happened, and it is the only terminal
  state the store allows from QUEUED. The launch re-check below settles the same
  way, for the same reason.

### The questions asked, and of what

Four, in this order:

1. **Is the recorded role one this build can schedule?** A role that is neither
   `worker` nor `review` is refused rather than admitted: an unknown role cannot be
   gate-checked, and admitting it would walk past every check below.
2. **Does the task still permit a run?** Its lifecycle must still be `planned` or
   `running`, and its version must still be the one the run was validated against.
   This applies to a review run as much as to a worker run: the review exemption is
   from the *worker* gates, not from the task's own lifecycle — a review of a
   cancelled task is not evidence anybody is waiting for.
3. **Is a run of the same role already unsettled?** A RUNNING run holds its role's
   slot outright; between two QUEUED runs of one role the older wins.
4. **For a worker run only:** is this task's review unsettled, and is a human gate
   open? (Gate items scoped to the queued run *itself* are excluded: a permission
   prompt a run raised is not prior work that run must wait behind.)

Both re-validations ask all four; they differ only in what "already unsettled"
(question 3) is read from. The resume sweep decides a whole queue at once, so it
starts from the RUNNING rows and adds each review it admits. The launch re-check
judges one row, so it reads every *other* unsettled run on that task — QUEUED as
well as RUNNING, which is what `create` counts (`ACTIVE_RUN_STATES`) — and excludes
the row being judged, since a run cannot be the reason it may not launch.

Question 2 is the one that was missing. `create` refuses a request whose
`expected_task_version` is stale and whose task has left `planned`/`running`, and
nothing re-asked either of those of a row already inserted — so a worker queued
while the task was running was still rescheduled after the task had been
cancelled, or after the review gate had carried it to `verified`, or after an edit
had moved its version. `ExecutionStore.create` therefore records the version it
validated against on the run (`TASK_VERSION_KEY` in `result_metadata`, set to the
version that transaction *writes* when it bumps `planned -> running`, since that
bump is part of creating the run), and `_task_refusal` compares it.

The version test is deliberately the same one `create` uses rather than a narrower
"did anything material change" predicate: the version is the task's one
optimistic-concurrency token, and a second notion of materiality here would be an
untested rule that disagrees with `create`. One consequence worth stating: setting
the display-only `archived_at` flag bumps the version, so it too refuses a queued
run. That is recoverable by dispatching again, and it is named here rather than
discovered later.

**Residual:** a row that records no task version — a database written before this
key existed, or one edited by hand — can only be lifecycle-checked. Unknown is
reported by skipping the question it cannot answer, not by refusing every
persisted row from an older database: that would settle runs that are very likely
fine, which is a larger harm than the window it would close.

### Ordering: the review cycle outranks a queued worker

Conflicts resolve in favour of what is already unsettled and of the review cycle:
a RUNNING run outranks a QUEUED one, an older QUEUED run outranks a newer one *of
the same role*, and an unsettled review run outranks a queued worker run.

The last of those needs the queue walked in **role order, not purely in queue
order**, and originally was not. The sweep went oldest-first, so a queued worker
that happened to be older was admitted first; by the time it reached the newer
queued review, `_resume_refusal` returned `None` without consulting the worker slot
— reviews bypass the worker gates by design — and both were admitted. Which one
held authority came down to insertion order, while the method's own docstring
claimed the review cycle outranks a queued worker. So QUEUED reviews are now
settled first and the admitted ones recorded in `held`, and the workers are judged
against a review slot that is already decided. Queue order still decides *within* a
role, because the sort is stable over the SQL ordering, and the admitted list is
returned in queue order so the caller still schedules oldest-first. An unknown role
sorts with the workers; it is refused either way.

### The launch re-check

`revalidate_queued` closes the gap across a restart. Inside a running process
there is a second gap, and it is not the same one: a row can sit QUEUED for as
long as its worktree and the concurrency limits make it wait, and `execute` re-read
only the *run*'s state before starting a process. So `ExecutionStore.
revalidate_before_launch` re-asks the questions above at the last moment the run is
still QUEUED, settling it CANCELLED with a stated reason instead of launching an
agent against a task that no longer wants it, or on top of work a human is still
holding a gate on.

All four questions, not only question 2. It asked only question 2 at first, on the
stated ground that re-asking the gate questions here would cancel a run waiting
behind a bridge permission prompt raised in the meantime — a transient, run-scoped
fact. That ground does not hold: the gate question already excludes items scoped to
the run being judged (`exclude_run_id`), which *is* that fact, while still seeing
every gate raised by prior work. Leaving the questions out left a real hole instead:
a review run settles, a worker run is queued in the window before
`_raise_review_attention` writes the approval, the approval appears while that
worker waits for capacity, and at launch its task is still `running` at the version
it was validated against — so the worker started with an open approval on its task.
Review runs remain exempt from the worker gates here, exactly as at create time and
in the resume sweep; without that the review a gate is waiting on could never run.

A missing task row is refused, not admitted. A foreign key normally makes that
impossible, so it is only reachable through a database edited outside Agora, but
the answer has to be a refusal: the run's task, its version and its gates are all
unanswerable without that row. The one concession is that the refusal cannot be
written to the task's timeline, because a task event needs a task; the run row's
own `error_message` carries the reason, and the sweep is not aborted for the sake
of a record that cannot exist.

Residual: this closes the gap at create, at resume, and at launch — not at every
instant in between. A gate raised *after* the launch re-check and before the process
starts is still not seen by it; that window is now microseconds of one transaction
wide rather than the whole time a run spends queued, and the atomic marker in the
section above is what would close it properly.

## Wire contract

`POST /api/runs` still answers 409. For these two refusals only, `detail` is an
object rather than a string:

```json
{
  "code": "attention_gate_open",
  "message": "Task has 1 open gate item(s) waiting for you: approval attn_… \"…\". Respond to each one (approve/reject, or answer) or cancel it, then start the run again.",
  "items": [{"item_id": "attn_…", "kind": "approval", "title": "…"}]
}
```

`code` is one of `attention_gate_open` or `review_run_active`; for
`review_run_active`, `items` carries `{run_id, state}`. Every other 409 from this
route keeps its plain-string `detail`, so no existing client breaks.

Codes are defined once per side —
`GATE_CODE_ATTENTION_OPEN` / `GATE_CODE_REVIEW_ACTIVE` in
`backend/agora/execution/store.py`, `RUN_GATE_CODES` in
`frontend/src/lib/continue-run.ts` — and
`test_run_gate_conflict_codes_mirrored_by_the_frontend` in
`backend/tests/test_execution.py` is the alarm when they diverge.

## Consequences elsewhere

- `WorkflowOrchestrator.dispatch` already wraps `dispatcher.queue` and converts
  any exception into a recorded workflow blocker, so a gated step becomes a
  visible blocker rather than a crash. No change needed there.
- `ExecutionDispatcher._dispatch_review` passes `role=ROLE_REVIEW`, so the
  auto-review path is unaffected.
