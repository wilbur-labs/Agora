# Run → Gate Ordering v1

Status: frozen. Decided 2026-08-29. Amended 2026-08-30 by two maintainer
decisions, recorded here as **D1** (`question` joins the gating kinds) and **D2**
(the settle→review window is closed by a derived refusal), and by review findings
**K1** (§3c), **L1** (§3d), **L2** (§3e), **M1** (§3f), **M2** (§1b), **M3**
(§3c), **N1** (§3g), **N2** (persisted QUEUED runs), **N3** (§3h), **N4** (§3c),
**O1** (§3i), **O2** (§3c) and **O3** (§3j).
Both amendments and all thirteen findings reverse claims this document previously
made; the superseded reasoning is stated where it was wrong rather than deleted,
because the earlier arguments are the ones a future reader will otherwise re-derive.
Two of those findings (N1, N2) overturn something recorded here as an *accepted
residual*, so each states why the original reasoning was wrong and not only what the
new behaviour is — a residual whose argument is left standing is one the next reader
will re-accept. Three of them (O1, O2, O3) overturn a claim made by the *fix* for an
earlier finding rather than by the original design, which is the pattern §3i and §3j
are written to make visible: a bound that holds for one call was documented as a bound
on the system, and a nonexistent exit N1 was raised for naming was written back into
the message a human reads.

Changes **when an execution run may be created**. Read this before relaxing or
widening either refusal.

Every guard in this slice has produced a lockout — the same-role guard was
bypassed by a forged role, locking the role left QUEUED-cancelled runs refusing
all future workers, and the attention gate turned an unanswered Codex permission
prompt into a permanent task lockout. So each refusal below states three things:
**what raises it, what releases it, and what happens when the release condition
cannot be read.** A refusal whose release condition cannot be named does not
ship.

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
  heuristic; the ordering is a contract. (D2 replaced the guess with a refusal —
  §3 — and the timer stays only as the friendlier first line.)

## What is now refused, in the create transaction

Three checks run in `ExecutionStore.create`, inside the same `BEGIN IMMEDIATE`
transaction that inserts the run, and **only for `role == worker`**. A review
run is how a gate gets something to decide; gating it would deadlock the cycle it
belongs to.

### 1. An open human gate on the task — `attention_gate_open`

Refused when the task has an Attention item that is `open`, whose `expires_at`
has not passed, and whose `kind` gates. **D1 (2026-08-30): all three kinds gate.**

| kind | raised by | released by |
| --- | --- | --- |
| `approval` | `_raise_review_attention` on a successful review run; a vendor permission prompt (`bridges/codex_protocol.decode_approval`, and `bridges/normalize` for a hook `permissionrequest`) | a human approving, rejecting or cancelling it; `expires_at` passing; or — for a bridge prompt — its run settling (§1a) |
| `blocker` | `_block_review_owed`; `_raise_review_attention` when the review run itself failed | a human answering or cancelling it; expiry; a bridge-origin blocker settles with its run |
| `question` | `bridges/normalize.normalize_hook_event`, for a non-permission hook event delivered during a run (the only *runtime* producer); the generic `POST /api/attention`, which takes `kind` and `requester` from the client (any requester except the control plane's own — §3d) | a human answering or cancelling it; expiry; **and, for a bridge-origin question only**, the run that asked settling (§1a) |

Why each kind gates:

- `approval` **is** the gate. `AGENTS.md` binds an approval to repository, ref,
  commit, stage, artifact path and artifact hash. Creating the next run while the
  approval is open makes that binding meaningless.
- `blocker`: the task is stuck on something only a human can clear. Another worker
  run does not clear it — it spends tokens on the same wall — and the auto
  cross-review raises this kind both when the review run failed and when no review
  could be dispatched at all.
- `question`: **D1.** An agent that asked the human something, got no answer, and
  is followed by a new run means the new run proceeds on an *unanswered
  assumption* — which makes asking pointless. This reverses the rule this document
  carried until 2026-08-30, which argued that a question binds to no artifact and
  settles no decision, so answering it later (or by starting a better-prompted run,
  which "in practice *is* the answer") was legitimate. The answer to "later" turned
  out to be "by a run that already guessed". The client has declined to offer
  Continue on an open question since the Continue action shipped, so this makes the
  client's own rule authoritative rather than inventing one.

**Why D1 is safe, and what the safety argument rests on.** The dangerous shape is
an item that gates and can never be *acted on* — §1a, where an unanswered
permission prompt locked a task permanently. No `question` has that shape, because
**a question is always human-clearable**: `respond` (answer) and `cancel` are open
to a human for as long as the item exists, whatever raised it, and neither depends
on the run that asked still being alive.

That — not automatic settling — is the load-bearing property. **Correction
(2026-08-30, K2):** this document, the `GATING_ATTENTION_KINDS` comment in
`backend/agora/execution/store.py` and the test docstring beside it previously
argued D1 was safe because the only producer of `kind=question` is
`bridges/normalize.normalize_hook_event`, so *every* question was bridge-origin,
run-scoped, and cancelled with its run. That absolute was false. `POST /api/attention`
→ `AttentionStore.create` accepts `kind=question` with an arbitrary `requester`
(other than the control plane's own, since §3d) and writes **no**
`attention_bridge_events` row, and
`settle_bridge_attention_for_run` JOINs that table — so a generically created
question is never settled with any run, no matter which run it names. D1's
conclusion stands on the human-clearable argument above; the auto-settling
argument holds for bridge-origin questions **only**, and is recorded that way
because the next person relaxing a rule here will rely on the sentence that is
written down, and the false version reopens the stale-lockout reasoning.

What is true about auto-settling, stated precisely: an item raised through
`AttentionStore.create_bridge_event` — which is how the hook ingress and the Codex
bridge raise theirs, and the only way that bridge row is written — is cancelled by
`settle_bridge_attention_for_run` in the same transaction that settles its run.
That is the mechanism §1a describes for `approval`, and bridge-origin `question`
items get it for free. `agora.tasks.clarify` raises no Attention at all — the
requirements interview stores its questions as requirements state, not as inbox
items — so it is not a producer of anything that gates.

The residual is unchanged and is not a new exposure: the same generic endpoint has
been able to create the `approval` and `blocker` that have gated since 2026-08-29,
and no in-repo caller of `AttentionStore.create` passes `QUESTION`. If a *new*
caller raises a `question` that is genuinely run-scoped and unanswerable once its
run is gone, it reintroduces the §1a lockout, and nothing detects that — the same
residual already recorded for `approval` at the end of §1a.

`responded`, `cancelled` and `expired` items never gate. An item whose
`expires_at` has passed but whose row has not been swept by
`AttentionStore.expire_overdue` yet is treated as expired, so the refusal does
not depend on when that sweep last ran.

**When the release condition cannot be read.** A database with no
`attention_items` table cannot hold an Attention item, so there is nothing to gate
on. That is a fact about the schema, not an unreadable state: `ExecutionStore`
initializes only the execution schema, and a deployment that never constructs an
`AttentionStore` has never raised an item. This is the one place the check returns
"no gate" without reading rows, and it is not a fail-open on an error path.
Nothing else about a gate item is derived from outside this database.

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
the first and is checked anyway — and since §3d it is redundant *by rule* rather
than by accident: `create_bridge_event` refuses the control plane's requesters
outright, so a bridge-origin item cannot carry one.

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

### 1b. The re-validation exclusion was too broad once `question` gated (M2, 2026-08-30)

`_open_gate_items` takes an exclusion used by the two re-validations of an
already-inserted row (`revalidate_queued`, `revalidate_before_launch`): drop the
items the queued run raised *itself*, so a run does not wait behind its own vendor
permission prompt. That is the right exclusion. It was implemented as the wrong
predicate — `run_id IS NULL OR run_id <> ?`, i.e. **everything carrying that run's
id**.

The two are equivalent only while every gating item that names a run came from that
run's bridge. D1 ended that. `POST /api/attention` takes `kind`, `requester` and
`run_id` from the client, so a generic `question` scoped to the queued run — an
operator or a script asking something *about* that run — was excluded by the same
rule. The run launched with an unanswered question about it and proceeded on a
guess, which is precisely the outcome D1 exists to prevent, reintroduced by the
exclusion rather than by the gate.

**The cut is by provable origin, the same rule §1a states**: an item is excluded only
when a row in `attention_bridge_events` links it to this `run_id`, which exists if
and only if it came from `AttentionStore.create_bridge_event`. Everything else keeps
gating, whatever `run_id` it carries. The redundant `requester <> REVIEW_REQUESTER`
condition `run_scope` checks is checked inside the exclusion here too, for the same
reason: redundant by rule since §3d, and the cost of excluding a review gate at this
point is admitting a worker run onto work a human is still holding.

**When the origin cannot be read.** A database with no `attention_bridge_events`
table cannot prove any item is bridge-origin, so nothing is excluded and every open
item gates. That is the fail-closed direction: it can cost a queued run that was
genuinely waiting behind its own prompt, and can never admit one that should have
waited. (Contrast the `attention_items` table being absent, which is "there are no
items at all" — a fact about the schema, §1.)

Regression: `test_a_generic_question_scoped_to_a_queued_run_still_gates_it` — a
generic question naming the queued run refuses it at launch and in the resume sweep,
and the run's own bridge prompt still refuses it in neither. Verified by mutation:
restoring the `run_id <>` predicate makes the run launch and the test fail.

### 2. An unsettled review run on the task — `review_run_active`


Refused when the task has a `queued` or `running` run tagged `role = review`.
This is the half of the ordering enforceable from a row that already exists: the
review run is in this database, readable in this transaction. Without it, a worker
run created while its predecessor's review executes is refused by nothing — the
same-role guard only compares worker against worker.

Raised by: a review row existing and unsettled. Released by: that run settling, or
a human cancelling it. It cannot become a permanent lock — `recover_abandoned`
settles a RUNNING one on restart and the dispatcher settles a QUEUED one on
shutdown.

### 3. A review that is owed but does not exist yet — `review_owed`

**D2 (2026-08-30).** Check 2 reads a review *row*, so it says nothing in the
window between a worker run settling and `_dispatch_review` inserting one:
`ExecutionDispatcher._after_run` publishes the worker run's terminal state, *then*
`_dispatch_review` shells out to git to build the review prompt (readiness check,
`list_changes`, file reads), *then* inserts the review run. In that window no run
is active, no gate exists, and checks 1 and 2 both pass. A reviewer reproduced a
worker run being accepted inside it through `POST /api/runs`.

It is now closed by **deriving** the fact rather than storing it
(`ExecutionStore._reviews_owed`, `_reject_review_owed`).

**What raises it.** All of:

- the task's most recent *terminal worker* run is `succeeded`;
- no review run **accounts for** reviewing that run — a run recording
  `reviews_run_id` for it (the linkage already existed) that is unsettled. A review
  run that ended `failed`, `timed_out`, `abandoned`, `succeeded` or `cancelled` does
  not account for it; see §3c, §3e and §3g;
- the review path has recorded no `blocker` for that run;
- the task is still `planned` or `running`;
- `auto_review` is on.

**What releases it:**

| release | how |
| --- | --- |
| the review run is inserted | the predicate goes false on its own — no write, no sweep, nothing to reconcile. Check 2 takes over while it is unsettled. |
| the dispatch failed outright | `_block_review_owed` recorded a `blocker`, which gates as check 1 and names how to clear it. The derived predicate **stands down** for that run once such an item exists in *any* state, so the two cannot both gate and leave no exit. |
| a review run is cancelled | **not a release** (N1, §3g). Whoever cancelled it, the work was not reviewed and no gate item was written by the cancel itself — so every path that cancels a review run records the review-owed blocker for the worker run, and *that item* is the release. |
| a later worker run settles in any non-`succeeded` state | only the one terminal worker run this predicate *nominates* is examined. When the runs can be ordered that is the newest terminal row, whatever state it is in. When they cannot (§3h, §3i) a non-`succeeded` row cannot displace a `succeeded` one, so this release does not apply there. |
| the task leaves `planned`/`running` | `create` refuses any run for such a task anyway, so a review could not have been dispatched for it — and this keeps the startup reconciliation from writing blockers onto tasks that are already done. |
| `auto_review` is off | the flag is passed into the create transaction; with it off the question is not asked. |
| the process died in the window | **cannot release itself.** See §3a. |
| the review run ended `failed`, `timed_out` or `abandoned` | **not a release** (K1, §3c). What accounts for it is the blocker its failure raises, which is the row above. A review that ended with neither a result nor a blocker leaves the review owed. |

**When the release condition cannot be read.** Three cases, all decided
deliberately:

- No `attention_items` table: the "has a blocker been recorded?" question answers
  False, so the refusal *stands*. That is the fail-closed direction — the same
  schema fact check 1 relies on.
- `auto_review` is dispatcher configuration the store cannot read. The caller
  passes the **flag**, not the answer, and the store asks the derived question
  inside its own transaction. A caller that evaluated the predicate beforehand and
  handed over a verdict would be the exact TOCTOU hole this closes. The parameter
  defaults to `False` because the config flag defaults off and a caller that never
  dispatches reviews can never owe one; `ExecutionDispatcher.queue` is the only
  production caller and always passes its own value.
- A terminal run row with no `finished_at` (only reachable in a hand-edited
  database, or one written before Agora recorded the field) makes the whole task's
  chronology unusable, and the predicate stops claiming to know which run is latest:
  it nominates the newest `succeeded` row it can still *identify* — the last in
  `rowid` order — and no other. It used to coalesce the missing value to `''`, which
  is an *ordering* claim and not an absence of one (§3h); the first fix then made
  every succeeded row a candidate, which bounded one call rather than the task
  (§3i).

**Why derived and not a durable marker.** A "review owed" flag written in the same
transaction that settles the worker run was costed and rejected: it leaks on a
crash, so it needs its own reconciliation, and it would have to be threaded
through `finish`, `record_cancelled_output` and `abandon`, any one of which could
forget it. A derived fact cannot leak, because nothing is written.

**Why only the most recent terminal worker run.** Two reasons. It matches
`_dispatch_review`'s own trigger — `_after_run` dispatches for the run that just
settled, so an older unreviewed run is not one a review "would have been
dispatched" for, and the predicate must never claim a review is owed that would
never have been dispatched. And it bounds what turning `auto_review` on over an
existing database can raise to one item per task rather than one per historical
run (see the residuals below).

### 3a. The one release condition that cannot resolve itself

If the process dies between the worker run settling and `_dispatch_review`
running, nothing will ever insert the review and nothing recorded a blocker — so
the predicate stays true for as long as the task exists, and no worker run could
ever start on that task again. That is the lockout shape this slice keeps
producing, so it is reconciled at startup:
`ExecutionDispatcher.reconcile_reviews_owed` asks `ExecutionStore.reviews_owed`
for every run that owes a review with no review row and no blocker, and **records
the blocker** for each one.

**Blocker, not re-dispatch — and this was a choice between two offered options.**
Re-dispatching at startup was rejected for three reasons:

- `_dispatch_review`'s `_review_prompt` reads the worker run's **live worktree**,
  not the frozen deliverables snapshot. Worktrees are keyed by project+adapter and
  are reused, so after a crash and a restart that directory is no longer
  guaranteed to hold what the run delivered — an automatic review at boot can
  review the wrong content and report a verdict on it, which is worse than saying
  nothing.
- It spends a reviewer's tokens at startup on work nobody asked to review again.
- It can fail for the same reason the original dispatch never happened, which
  lands on `_block_review_owed` anyway.

The blocker is deterministic, costs nothing, gates by itself, names what happened
(`REVIEW_OWED_AFTER_RESTART_REASON`: Agora stopped after the run finished and
before the review was created), and leaves the human the exits that **exist** —
clear the item and accept the work unreviewed, or clear it and dispatch another
*worker* run, whose own automatic cross-review then follows (§3j; there is no
endpoint that dispatches a review). Because the derived
predicate stands down once that item exists, clearing it releases *everything*
rather than one of two gates.

It runs **before the API serves and before `resume_queued`**: before the API, so
no client can observe the unreleasable state (which is why a `review_owed` refusal
in practice always means the live, seconds-long window — every start of the
process reconciles first); before the resume sweep, so a blocker it raises is
visible to the sweep that refuses queued worker runs on a gated task. It runs only
when `auto_review` is on, exactly matching the predicate. Its read is outside a
transaction, which is safe *here specifically* because nothing else is running
yet; the create-time refusal asks the same question inside its own transaction,
which is where the race actually lives.

**Where that ordering lives (2026-08-30, K3).** It used to live only in the order
of two statements in `agora.api.app.lifespan`, pinned by one test — which catches a
reordering in that file and nothing else, and tells a second startup path nothing.
It is now `ExecutionDispatcher.start_recovery`: one function that calls
`reconcile_reviews_owed()` then `resume_queued()` and returns a receipt
(`{reviews_owed_reconciled, reviews_owed_unreadable, reviews_owed_unrecorded,
queued_runs_refused}`), and
`resume_queued` raises `RuntimeError` if it is reached before the reconciliation
has run in this process. The guard is scoped to `auto_review` being on, which is
exactly when the reconciliation has anything to raise; with the flag off there is
no order to get wrong. `lifespan` makes one call. **Order is necessary and not
sufficient** — see §3f for what continuing after an *incomplete* reconciliation did,
and why the resume sweep now derives the review-owed refusal itself instead of
trusting that the gates it reads were all raised.

**The read can fail, and that is now recorded rather than fatal (2026-08-30, K3).**
`self.store.reviews_owed()` was the one unwrapped call on this path while the
per-item blocker write was already wrapped, so a database that could not be read
propagated out of `lifespan`: the API never served and `resume_queued` never ran —
the opposite of the promise two paragraphs above. It is wrapped now. The failure is
recorded on `ExecutionDispatcher.reviews_owed_unreadable`, returned in
`start_recovery`'s receipt, and printed to stderr by `lifespan` (uvicorn's console
is the operator's channel at startup, and the failure *is* a database read, so
there is nowhere durable to write it). A reconciliation that could not run is a
fact worth surfacing, not a silent skip: the tasks it would have recorded a blocker
for stay refused with `review_owed` until a later start reads them.

`test_a_review_lost_to_a_crash_is_reconciled_into_a_blocker_at_startup` and
`test_startup_reconciles_reviews_owed_before_resuming_queued_runs` in
`backend/tests/test_execution.py` are the regressions for the recovery and for the
ordering.

### 3b. What D2 does not cover

- **Turning `auto_review` on over an existing database is retroactive.** The
  predicate cannot know when the flag flipped — that is inherent to deriving rather
  than marking — so a task that is still `planned`/`running` and whose last worker
  run succeeded before auto-review was enabled owes a review the moment it is
  enabled, and the first startup after that records a blocker for it. Bounded to one
  item per task by the most-recent-run rule, bounded to live tasks by the task-state
  rule, and cleared by one cancel each. Stated here rather than discovered later.
- **A blocker the reconciliation cannot write** (an unavailable Attention store at
  startup) is skipped rather than raised, because one unrecordable task must not
  stop the rest or the API from serving. That task stays refused with `review_owed`
  until a later start records it, and since §3f the skip is *reported* — per task, in
  `reviews_owed_unrecorded` and on the operator's console — instead of being silent.
  The same applies to `_block_review_owed` failing inside `_record_review_failure`,
  which already swallows. The *read* that finds the owed runs is wrapped for the same
  reason since K3, and its failure is reported (§3a) rather than skipped silently.
- **A review run a human cancels does not release the predicate** — it releases by
  raising the review-owed blocker for the worker run, at the moment of the cancel. It
  used to release the predicate outright, leaving the worker run unreviewed with no
  gate; that was called deliberate ("the human decided") and it was reachable with no
  human at all. **§3g** is the finding and the reversal.
- **The derived question is asked at create *and* in both re-validations.** It was
  asked at create only, on the ground that the arrangement it would otherwise catch
  is unreachable through the API: a QUEUED worker run can only exist after every
  earlier worker run of that task has settled (`_reject_second_active_run`), and if
  one of those settled `succeeded` and owed a review, this refusal would have refused
  the queued run at create time. That reasoning was sound about the API and wrong
  about a restart — see **§3f**, which reverses it.

### 3c. The predicate depends on a field another path could erase (K1, 2026-08-30)
Every check in §1–§3 reads a run's **identity** — `agora_role`, `reviews_run_id`,
`task_version` — out of the `result_metadata` JSON column. That column is written
wholesale, and `ExecutionStore.finish` used to write `result_metadata or {}`. So a
settle that carried no result wrote `{}` over the identity:

1. `auto_review` dispatches a review run; it is QUEUED, `agora_role=review`,
   `reviews_run_id=<worker run>`.
2. It ends **before it starts** — adapter disabled, workspace gone, launch refused.
   Every one of those paths in `ExecutionDispatcher.execute` calls `finish(...)`
   with no `result_metadata`, which wrote `{}`.
3. The row now has no role. `_reviews_owed`'s
   `COALESCE(json_extract(result_metadata, '$.agora_role'), 'worker')` reads it as
   the task's most recent **worker** run — state `failed`, so nothing is owed.
4. The next worker run passes the review gate. The whole of §3, defeated by a field
   cleared on an unrelated path.

**The fix is to state that identity is not a result, and to enforce it at the one
place that writes the column.** `IDENTITY_METADATA_KEYS`
(`backend/agora/execution/models.py`) is the set, and it is the same object as
`RESERVED_METADATA_KEYS` by construction, not by coincidence: a key is refused from
a client exactly because the server owns it. Identity now has one protection per
direction — a client may not *set* it (`CreateRunRequest`), and a result write may
not *clear or change* it (`ExecutionStore._identity_preserved`).

Every terminal path is covered, and by construction rather than by enumeration:

| path | how identity survives |
| --- | --- |
| `finish` (any target, with or without a result) | `_transition` delegates the column to `_write_result_metadata`, which merges the stored identity back over the value being written; a falsy `result_metadata` is passed as `None`, and `_transition` then does not call the writer at all |
| `cancel` | goes through `_transition` and passes no `result_metadata` — the writer is not called |
| `abandon`, and `recover_abandoned` through it | same |
| `record_cancelled_output` | hand-written UPDATE naming `stdout_tail`, `stderr_tail`, `exit_code` and optionally `deliverable_snapshot` — not this column |
| the queued-cancel settle (`_cancel_locked`, used by `revalidate_queued` and `revalidate_before_launch`) | same: state, version, `finished_at`, `pid`, `error_message` only |
| the state UPDATE itself (`_write_transition_columns`, called by `_transition`) | its columns come from the fixed `_TRANSITION_COLUMNS` list, which does not include this one; a key outside the list raises (N4) |

**One writer, and the test is a backstop on that (M3, 2026-08-30).** The column write
used to be one entry in `_transition`'s dynamically assembled SET clause, so "only
`_transition` writes it" was a property of today's statements that
`test_no_raw_sql_write_of_result_metadata` had to *prove* by deciding, from SQL text,
whether any other statement assigned the column. Three revisions of that parser were
each defeated by a form of SQL they could not read; the last cut the statement at the
first `WHERE`, so

```sql
UPDATE execution_runs SET x = (SELECT ... WHERE ...), result_metadata = ? WHERE ...
```

hid the assignment behind a subquery's own `WHERE` and passed. The structure was
changed instead of the parser: `ExecutionStore._write_result_metadata` is a private
method owning one static statement and calling `_identity_preserved`, `_transition`
delegates to it, and the test now asserts that no other `UPDATE execution_runs` in the
module so much as *names* the column. That is a property of a whole statement, so
clause order, nesting and subqueries cannot hide anything from it — there is no clause
to truncate.

**The exemption that structure still needed, and its removal (N4, 2026-08-30).** One
gap survived M3 and was written down in the test's docstring rather than closed: a
statement whose text comes from an f-string cannot be read at all, so the guard refused
such a statement only in functions that do *not* delegate to the writer. `_transition`
both builds its SET list dynamically and delegates, so it was exempt — and adding one
line, `updates["result_metadata"] = ...`, made it a second writer of the identity
column with the guard reporting no offenders and every structural assertion still
satisfied. A reviewer made exactly that edit.

Nothing about the guard was changed in response; it was the fifth revision, and each
previous one had been a smarter reader of SQL that a new form of SQL defeated. The
*need* for the exemption was removed instead. The dynamic statement moved to
`ExecutionStore._write_transition_columns`, whose column vocabulary is the fixed
`_TRANSITION_COLUMNS` tuple — `result_metadata` is not on it, and a key outside it
raises `RunValidationError` rather than being dropped, because silently discarding a
write the caller believed it made is this slice's recurring failure. That function's
body never names the column (its reasoning is in comments, which `ast.unparse` drops,
precisely so the function stays one the guard has nothing to say about). The guard's
dynamic-statement clause is therefore gone, with no exemption in its place: an
unreadable `UPDATE execution_runs` in any function that names the column is an
offender, full stop, and both remaining interpolated statements
(`_write_transition_columns` and `record_cancelled_output`'s `{snapshot_sql}`) pass
because neither function mentions it. `test_a_transition_may_not_write_result_metadata`
pins the allowlist behaviourally.

**The verification this section first recorded was not one (O2, 2026-08-30).** It said:
the reviewer's edit, reapplied, "now fails 57 tests in `test_execution.py`". That is a
true observation and the wrong thing to record as a guard. Reapplied, the mutation left
*both* named guards passing — `test_no_raw_sql_write_of_result_metadata` cannot read a
dynamic SET list, and `test_a_transition_may_not_write_result_metadata` called
`_write_transition_columns` directly with a dict the test itself wrote, so neither
exercised `_transition`. The property was held up by 57 tests that are about something
else and happen to perform a transition; nothing named it, so nothing would have kept
it — the next refactor that stopped those tests from transitioning would have removed
the protection silently, and no failing test would have said why. The named guard now
performs one real transition (`store.cancel`) and asserts the run settles with its
identity intact, so the mutation fails a test that states the property. Re-verified in a
copy outside the repository: with the mutation applied and that one addition removed,
both named guards pass; with it present, `test_a_transition_may_not_write_result_metadata`
fails naming the column and the writer.

What the test still cannot see is recorded in its docstring.

**Why not a separate column.** It was the first candidate — a column cannot be
cleared by a JSON blob being replaced. Rejected because the JSON key is *already*
the read surface in five SQL predicates in `ExecutionStore`, in
`agora/api/settings_router.py`, in `agora/execution/handoff.py` and in
`frontend/src/app/workspace/page.tsx` (`result_metadata.agora_role`). Adding a
column without removing the key would leave two homes for one fact and a
reconciliation between them — the shape this document rejects elsewhere — and
removing the key is an API change to a field the frontend reads. One home, one
writer, and a write path that cannot drop it is the smaller correct change.

**Refused, not merged over.** `_identity_preserved` raises `RunValidationError` if
`result_metadata` carries an identity key at all, even the value the row already
has. The dispatcher used to re-send `agora_role` with every successful settle,
which is what made the role *look* like something a result write carries — so the
paths that had no result looked like they were merely omitting an optional field
rather than erasing identity. Both dispatcher call sites now pass results only.

**The same scenario needed a second fix, and this is why the two are in one
section.** With the identity restored, the failed review row is correctly a
*review* run again — and the old rule `_has_review_run` ("any state, deliberately")
then read its preserved `reviews_run_id` link as "a review exists", so the
obligation was still discharged and the next worker run still passed the gate. That
rule rested on each state accounting for itself: unsettled is held by check 2, and
a settled one raises the approval or blocker its outcome produces. The second half
is only true of a review run that reached `_after_run` — and every pre-launch
failure returns from `execute` without ever calling it.

So the predicate is now `_has_accounting_review_run`: a review run accounts for the
obligation when it is unsettled or `cancelled`, and **not** when it ended `failed`,
`timed_out` or `abandoned` — nor, since §3e below, when it `succeeded`. For all of
those, the thing that accounts for it is the gate item `_raise_review_attention` (or
`_block_review_owed`) records against the worker run, which `_has_review_gate_item`
checks separately — so the two never gate at once, and the human still has one item
with one exit. A review that ended with no gate item anywhere is owed again, and the
startup reconciliation turns it into that same blocker (§3a).

Residual (removed by §3g): `cancelled` still accounts for the review, and the resume
sweep and the shutdown sweep cancel a queued review run without a human deciding
anything. Telling those apart from a human's cancel means parsing a recorded reason
string, which is a worse rule than this one, and re-dispatching the review is the
exit in both cases. Recorded here rather than closed. **This reasoning was wrong; see
§3g.**

Regressions: `test_a_review_that_fails_before_it_starts_still_owes_a_review`
(the exact scenario: queue a review, fail it pre-launch, assert a review is still
owed and the next worker run is refused),
`test_a_failed_review_that_raised_its_blocker_does_not_gate_twice`,
`test_every_terminal_path_preserves_a_runs_identity`,
`test_a_result_write_may_not_carry_identity` and
`test_no_raw_sql_write_of_result_metadata` — the last one fails if any other UPDATE
in `store.py` so much as *names* the column outside `_write_result_metadata`, which
is the one way the table above can silently stop being true.

Residual, stated rather than hidden: a result write still replaces any *non*-identity
key an earlier write put in `result_metadata` (a client's own `metadata` from
`create`, for instance). Nothing in the control plane reads those keys after a run
settles, and preserving arbitrary client keys across a settle would be a different
decision about what that column means. Identity is what gates read, and identity is
what is preserved.

### 3d. The release valve was a field the API let a client set (L1, 2026-08-30)

The valve above is read from one row: an Attention item with
`requester = REVIEW_REQUESTER`, `kind = blocker`, and the worker run's `run_id`.
`POST /api/attention` takes `requester` from the request body. So the release was
forgeable end to end:

1. A worker run succeeds. §3 holds: a review is owed and the next worker run is
   refused.
2. The caller POSTs that exact item itself — the requester is a string, the kind and
   the `run_id` are its own choice.
3. `_reviews_owed` sees the valve and returns nothing. The caller cancels the item,
   so `_reject_open_gate` sees no gate either.
4. A worker run is accepted. No review run was ever created, no review ever ran, and
   nothing is left anywhere that says so.

The same row also drives `agora.attention.review_gate.settle_review_approval`, which
advances a task to `verified` when a human approves a `REVIEW_REQUESTER` *approval* —
so the forgery reached the task's terminal state too, not only the run gate.

**This was the third authority marker in this slice that turned out to be
client-settable**, after `agora_role` in run metadata (§3c, forged through
`POST /api/runs`) and the locking role before it. The fix is the same shape as
`RESERVED_METADATA_KEYS`, deliberately: `RESERVED_REQUESTERS`
(`agora/attention/models.py`) is the set of requesters the control plane treats as
its own, and `AttentionStore._reject_reserved_requester` refuses them in public
input. Only Agora's internal paths may write one, through an explicit
`internal=True` argument on `create`/`create_once` that **defaults to closed** — the
same shape as `role=ROLE_REVIEW` being an argument only the internal review dispatch
passes to `ExecutionStore.create`. `create_bridge_event` has no opt-out at all: a
vendor event is never the control plane. Refused, not rewritten, for the reason
`RESERVED_METADATA_KEYS` gives: a caller sending one is confused or hostile, and
quietly substituting a requester would hide which.

**Not fixed by making the predicate cleverer.** The alternative was to distrust
blockers at the read — match the title too, or require a marker in `context`, or
correlate with a review run. All of them are the same mistake: a gate that has to
guess which of its own markers are genuine has no marker at all, and every
distinguishing field a client could also set would have to be reserved anyway. The
value is refused where it enters.

**Residual, and it cannot be closed here.** A forged row **already stored** by a
build that predates this check is indistinguishable from a genuine one. Every column
a client could set (`requester`, `kind`, `run_id`, `title`, `body`, `context`) is one
the internal path sets the same way, and the `attention.created` event records the
same requester as its actor, so there is no field left to tell them apart. What the
fix delivers is that no sequence of public API calls can produce a releasing row from
now on; a database written before it carries whatever it carries. Stated rather than
papered over with a heuristic.

Regression: `test_the_control_plane_requester_cannot_be_claimed_through_the_public_api`
— the API refuses the blocker and the approval, the store refuses them from any
caller that has not declared itself internal, a bridge event cannot carry the
requester at all, nothing is written, the obligation still stands and the next worker
run is still refused; and the internal path still writes the item that releases it.

### 3e. A succeeded review run was counted as a gate that never existed (L2, 2026-08-30)

`_has_accounting_review_run` used to treat `succeeded` as accounting for the review,
on the argument that "the review happened; its approval is the gate". That conflates
two facts. The run is **evidence** that a review ran. The approval is the **gate**
where a human decides. `AGENTS.md`'s
`Run -> Artifact/Evidence -> Gate -> Handoff/Done` line only means anything while
they stay separate, and here they came apart:

1. A review run succeeds. `_after_run` calls `_raise_review_attention` to write the
   approval, and it fails — the Attention store is a different transaction on the
   same database.
2. `_after_run`'s handler calls `_record_review_failure`, whose fallback blocker write
   fails too. It swallows, by design: the run's own recorded outcome must survive.
3. There is now a SUCCEEDED review run, no approval, and no blocker. The accounting
   concluded the review was satisfied **from the run's state alone**.
4. Nothing is pending for the human. After a restart the startup reconciliation saw
   nothing owed either, so it skipped that worker run, and the next worker run built
   on work nobody had ever been asked to accept.

**Decided: a review run that produced no gate item satisfies nothing.** It is worse
than no review at all, because it *looks* finished — the run table says `succeeded`,
the inbox is empty, and every reading of that arrangement is that the work was
reviewed and accepted. So `succeeded` joins `failed`/`timed_out`/`abandoned` as a
state that does not account for the obligation, and what releases it is
`_has_review_gate_item`: the `approval` a successful review raises, or the `blocker`
every path without a review records, scoped to the worker run, in any state. The
gate, not the evidence.

The costs were weighed. This makes the derived refusal fire in one more window — the
moment between a review run's `finish` and its approval insert, two separate
transactions — so a worker run POSTed inside it is refused with `review_owed`. That
window is seconds long, the refusal names the run and how to clear it, and failing
closed there is the whole point of the check. And a review whose approval genuinely
cannot be written no longer looks reviewed; it becomes a blocker naming what
happened.

**The startup reconciliation covers it, and says which arrangement it found.** It is
the only thing that can notice this after a restart, so `_reviews_owed` now carries a
`cause` with each record: `REVIEW_OWED_NO_REVIEW_RUN` (no review run is linked at
all — the §3a crash window) or `REVIEW_OWED_NO_GATE_ITEM` (one is, and it left no
gate item). `ExecutionDispatcher.REVIEW_OWED_REASONS` maps each to its own blocker
reason, with **no default**: an unrecognised cause is skipped, leaving the derived
refusal standing, rather than recorded with a reason that describes something that
did not happen. A blocker that misdescribes its own cause is the defect L4 was, one
layer down.

**What still does not hold.** The two gate writes failing is still *silent in the
moment* — nothing is recorded when both fail, and the arrangement is only detected by
the next create attempt or the next startup, not at the time it happens. Making the
settle transaction itself write the gate would close that, and it is the
durable-marker design §3 rejected for other reasons; it is not attempted here.
(`cancelled` accounting for the review by itself was listed here as the other gap; it
is closed by §3g.)

Regressions: `test_a_succeeded_review_that_recorded_no_gate_item_still_owes_a_review`
(both writes fail through the real `_after_run` path, then the reconciliation records
the blocker with this cause's reason),
`test_a_cause_with_no_recorded_reason_is_skipped_rather_than_mislabelled`, and
`test_a_worker_run_is_refused_while_its_predecessor_owes_a_review`, which now asserts
that the *gate item* is what releases the obligation.

### 3f. A reconciliation that did not finish still resumed the queue (M1, 2026-08-30)

§3a puts `reconcile_reviews_owed()` before `resume_queued()` because the blocker the
first raises is the gate the second judges the queue against. §3b then argued the
resume sweep need not ask the derived review-owed question itself, because a QUEUED
worker coexisting with an owed review is unreachable through the API. Both statements
were true. Together they were not sufficient, and this is the interesting part of the
finding: **the defect is in the continuation after an incomplete first step, not in
any single step.**

`reconcile_reviews_owed` swallows its own failures deliberately (§3a, §3b): the
`reviews_owed` read is wrapped so an unreadable database does not stop the API from
serving, and a per-task blocker write that fails is skipped so one unrecordable task
does not cost the rest theirs. In both cases the method returns normally, and
`start_recovery` called `resume_queued()` next regardless. Correct order, gates that
were never raised: `revalidate_queued` computed no `review_owed` of its own, so a
persisted QUEUED worker run on a task whose last worker succeeded unreviewed was
**admitted and scheduled**. Reproduced with `auto_review` on, a succeeded unreviewed
worker run and a queued worker run in the database, and the blocker write failing.

Note what the previous round asked and what this one found, because the difference is
the lesson. The earlier question was whether the empty list returned by an unreadable
`reviews_owed` is read downstream as "nothing owes a review". It is not — nothing
consumes that return value as a judgement, and that check was clean. The unsafe thing
is one step further on: continuing into a step that *depends* on the first having
completed.

**Chosen: the resume path derives the refusal itself.** Both options were available
— refuse to resume (all of the queue, or the affected part) when the reconciliation
was incomplete, or have the resume path ask the question. The second is what shipped,
for three reasons:

- It asks the hazard rather than a proxy for it. "Is a review owed on this task?" is
  answerable in the sweep's own transaction; "was the reconciliation complete?" is a
  bookkeeping fact that would have to be mapped back onto tasks — and an unreadable
  read knows *no* task ids, so the affected part of the queue is not even
  identifiable in the case that matters most.
- Blast radius. Refusing on the flag stalls or cancels queued runs on tasks that owe
  nothing, including queued *review* runs — the very runs a gate is waiting for.
  Deriving the refusal touches exactly the runs that are unsafe.
- It removes the asymmetry that made this reachable at all. The sweep now asks the
  same four-plus-one questions `create` asks, so "unreachable through the API" stops
  being load-bearing anywhere.

`auto_review` is passed into `ExecutionStore.revalidate_queued` and
`revalidate_before_launch` as the **flag**, never as a precomputed verdict, for the
reason §3 gives; `_resume_refusal` asks `_reviews_owed` last, in `create`'s own order,
so an existing gate item is still reported as the gate it is.

**In the ordinary path this changes nothing.** When the reconciliation succeeds, its
blocker is a gate item, and once that item exists the derived question stands down
(`_has_review_gate_item`, §3e) — so the open-gate answer comes first and still names
the item and its exit. The derived reason only ever appears when no gate was written.

**What the operator sees when it fires**, because "the queue just did not resume" would
be this document's own unknown-as-absence:

- the queued run is settled `CANCELLED` with the reason on its own row
  (`Not resumed after restart: worker run … succeeded and no independent cross-review
  accounts for it yet …`) and as a `run.cancelled` event on the task's timeline;
- it is listed in `start_recovery`'s `queued_runs_refused`;
- the reconciliation's own incompleteness is reported separately and per task:
  `reviews_owed_unreadable` for a failed read (unchanged), and a new
  `reviews_owed_unrecorded` list carrying `{task_id, run_id, cause, error}` for every
  owed run it read but could not record a blocker for — including a cause it has no
  true sentence for (§3e). `lifespan` prints both to stderr. Those per-task write
  failures were previously *entirely* silent, which is the part of §3b's "skipped
  rather than raised" that had no channel.

Safety no longer depends on any of that reporting; it is there so a human knows their
inbox is missing an item, not so the gate holds.

Regression: `test_an_incomplete_reconciliation_does_not_resume_a_worker_onto_unreviewed_work`
— the blocker write failing, the read failing, the reconciliation succeeding (the
blocker is what refuses, by item id), and `auto_review` off (the question is not asked
and the queued worker resumes). Verified by mutation: disabling the derived question
makes the queued worker be scheduled and the test fail.

### 3g. A cancelled review satisfied the obligation, and the sweep cancels automatically (N1, 2026-08-30)

`_has_accounting_review_run` counted `cancelled` alongside `queued`/`running` as a
state that accounts for the review. **This was recorded as an accepted residual twice**
— in §3c when the method replaced `_has_review_run`, and again in §3e's "what still
does not hold" — on this reasoning:

> Cancelling a review is a human decision the control plane does not override. The
> resume and shutdown sweeps also cancel a queued review, and those are not human
> decisions, but telling them apart means parsing a recorded reason string, which is a
> worse rule than this one; re-dispatching the review is the exit in both cases.

**Why that reasoning was wrong, which is the part worth recording.** It has two halves
and both fail, in ways the words themselves conceal.

1. *It assumed a human was involved.* The sentence "cancelling a review is a human
   decision" was written as the rule and the sweeps were then filed as an exception to
   it — an odd shape, because the sweeps are not a rare corner. `ExecutionDispatcher.
   shutdown` cancels **every** scheduled run through `_settle_unstarted`, so an
   ordinary stop of the process between a worker run succeeding and its review
   starting cancels the review with nobody deciding anything. The residual was
   filed as a human's decision that the control plane respects; it is reachable by
   *restarting Agora*. Something reachable by an ordinary restart is not a residual,
   it is the common case, and calling it "not a human decision, but…" is where the
   reasoning stopped looking.
2. *It framed the fix as needing information it did not need.* "Parsing a reason
   string" was true of one particular way to distinguish the cancels — and the exit
   never depended on distinguishing them. What a cancelled review has in common with a
   failed one is the fact §3e had already made decisive: **it produced no gate item**.
   That is a row in this database, not a sentence to parse. Having just moved
   `succeeded` onto the gate-item rule for exactly this reason, §3e left `cancelled`
   on the state rule and described the leftover as a string-parsing problem. The
   argument for the residual was an argument about an implementation nobody had to
   choose.
3. *"Re-dispatching the review is the exit" was not an exit a human had.* There is no
   endpoint that dispatches a review run — `ROLE_REVIEW` is an internal argument to
   `queue`, and `_dispatch_review` runs off a worker run settling. The stated escape
   hatch did not exist.

Reproduced: worker run succeeds, the auto-review is queued, Agora shuts down before
the review starts, the next start reads `reviews_owed() == []` and admits a new worker
run. No review ran, no gate item existed, and nothing anywhere said so.

**Decided: a review run that was cancelled accounts for nothing.** `cancelled` joins
`succeeded`, `failed`, `timed_out` and `abandoned`, so the *only* states that account
for the review by themselves are the unsettled ones that check 2 already holds, and
everything else is released by `_has_review_gate_item`. The state rule and the gate
rule no longer disagree about which review outcomes are special.

**A human's cancel is still honoured — by being recorded rather than by vanishing.**
Cancelling a run writes no gate item, so on its own this change would leave a human
who cancelled a review inside a live process with a task refusing worker runs
(`review_owed`) and nothing in their inbox until the next restart's reconciliation —
a worse door than the one being closed. So every path that cancels a review run now
raises the review-owed blocker for the worker run it was going to review, at the
moment of the cancel (`ExecutionDispatcher._block_reviews_owed_for`, called from
`cancel`, from `_settle_unstarted`, from the resume sweep's refusals and from the
launch re-check). The human gets one item, and it states the exits that exist:
clear the item and take the work unreviewed, or clear it and dispatch another
*worker* run, whose own automatic review follows. It is
`create_once`, so covering the same run from two paths records one item.

The first version of that item did **not** say this. It told the human to "dispatch
another review", and two of the three `REVIEW_OWED_REASONS` said the same — the exact
nonexistent exit point 3 above was written to retire, reintroduced by the fix for the
finding that retired it, in the one place a human actually reads. §3j is that finding
and the correction.

**The startup reconciliation notices the shape, and says which shape it is.** A third
cause joins the two from §3e: `REVIEW_OWED_REVIEW_CANCELLED`, with its own sentence
(`REVIEW_OWED_CANCELLED_REASON`). It has to be its own cause because the existing
`REVIEW_OWED_NO_GATE_ITEM` sentence says *both gate writes failed*, which is a
different fact about the database and points at a different thing to check; recording
a stopped review under it would be the L4 defect one layer down, which §3e already
refused to do by giving `REVIEW_OWED_REASONS` no default. The sweep is the backstop
for the cancels whose own write had no chance to run — a hard kill, or a failed write.

Cost: a review a human deliberately cancels now produces an inbox item. That is the
intended trade. The alternative is a review obligation that disappears when a process
restarts, and this project's rule is that Agora is the only writer of that state.

Regressions: `test_a_shutdown_cancelled_review_still_owes_a_review` (the reproduction,
through the real `shutdown`/`_settle_unstarted` path),
`test_the_startup_reconciliation_notices_a_cancelled_review` (the hard-kill shape, and
the cause is the cancelled one rather than the no-gate-item one),
`test_a_human_cancelling_a_review_gets_the_blocker_that_releases_it`, and
`test_cancelling_a_worker_run_raises_no_review_blocker` (the scope: a cancelled worker
run owes nothing, and must not have the retry it is about to get gated).

### 3h. Unknown chronology was manufactured into an ordering (N3, 2026-08-30)

`_reviews_owed` picked a task's "most recent terminal worker run" with
`ORDER BY COALESCE(r.finished_at, ''), r.rowid`, and the note next to it said a row
with no `finished_at` "sorts oldest, which is the reading that cannot invent a newer
run". Coalescing to `''` does not mean *unknown*, though — it means *older than every
real timestamp*, which is a claim about order, not an absence of one. The direction it
happens to be safe in is the direction with one succeeded run; with two rows it
inverts:

1. An older **failed** worker run has a real `finished_at`.
2. A newer **succeeded** worker run has none.
3. The failed row wins the sort, becomes the "latest terminal worker", and the
   `state != succeeded` branch concludes no review is owed.
4. Another worker run is admitted on unreviewed work — decided by a sort order chosen
   because it looked safe in the case that was being thought about.

**Decided: being unable to order the terminal runs fails closed rather than choosing
an order.** A terminal worker row with no usable `finished_at` has **no rank**: it
cannot be ranked against anything, so it can neither be the latest nor be overridden.
When a task has such a row, this method stops claiming to know which run is latest and
nominates the newest `succeeded` terminal worker row it can still *identify* — the last
one in `rowid` order — and no other. A row in any other state cannot displace it, which
is what closes the inversion above. When the chronology *is* readable nothing changes:
exactly one run is examined, ranked by `finished_at` then `rowid`.

The first version of this fix nominated differently, and the difference is the whole of
§3i: it made **every** succeeded row a candidate and returned the first one that was
still owed. Read against a single call that is one record per task, which is what this
section originally claimed; read against a task's lifetime it walks back through
history one blocker per start. Fixed there, and this paragraph states the rule that
actually ships.

Cost: a hand-edited database can raise a blocker for a run that was in fact reviewed
before the row was edited. One cancel clears it — see §3i for why that sentence was
false as first written and is true now — and that is the direction to be wrong in.

Regression: `test_terminal_worker_runs_that_cannot_be_ordered_fail_closed` — the
inverted pair above owes a review and refuses the next worker run, and giving the
succeeded run a real, older timestamp restores the single-run reading.

### 3i. The unorderable branch bounded one call, not the task (O1, 2026-08-30)

§3h's fix said "still one record per task, so §3b's blast-radius bound is unchanged".
It returned one record per **call**. Every `succeeded` terminal worker row was a
candidate, the loop skipped a candidate that already had an accounting review run or a
gate item, and it `break`s after recording one — so the `break` bounds a single
invocation and the *next* invocation starts again from the next-oldest candidate.
Reproduced across successive starts on a task with two unreviewed successes and no
usable chronology:

```text
INITIAL     [run A]
RECONCILE_1 [run A]     -> blocker recorded for A
AFTER_1     [run B]     -> A now has a gate item, so the walk continues to B
RECONCILE_2 [run B]     -> a second blocker, for B
AFTER_2     []
```

With N historical successes that is N blockers over N starts, several of which can be
open at the same time. It also falsified §3h's own closing sentence, "one cancel clears
it": cancelling A's item released the obligation for A only, and the next worker attempt
was refused for B, which had no item yet — so the operator clears items and restarts,
one historical run at a time, to get a task moving again.

**What the honest bound is, stated as the code now delivers it.** We cannot tell which
terminal worker run is latest, so we owe a review on the newest one we can still
*identify* and no more: the last `succeeded` row in `rowid` order. The nominee is chosen
from the rows **before** any release question is asked, so it does not depend on what
has already been recorded — the same row is nominated on every call until a new terminal
worker row appears, one gate item settles it for good, and one cancel really is the
whole exit.

`rowid` is used with the weak claim it can carry, and this is the part worth being
explicit about. `_reject_second_active_run` refuses a second unsettled worker run on a
task, so under Agora's own writes worker runs on one task are strictly serialized and
insertion order *is* their settle order. But this branch is only reachable when a row
was written by something that is not Agora, so insertion order is treated as the best
available identification of "newest", not as proof of chronology. That is exactly why
the branch nominates one run and refuses to walk any further back: a guess about which
row is newest is a fail-closed guess when it raises one item a human can cancel, and an
unbounded one when it walks history.

Cost, unchanged in direction and now bounded in size: one blocker for a run that may in
fact have been reviewed before the row was edited. One item, one cancel.

Note how this was found, because it is a property of the review and not of the code. One
reviewer checked the per-invocation bound, confirmed it holds — it does — and called the
branch clean. The other asked what bounded it over the task's lifetime. The claim in the
document was ambiguous between the two, and the reading that made it true was the one
nobody needed.

Regression: `test_unorderable_terminal_runs_owe_one_review_for_the_task_lifetime` — the
sequence above, asserting that the nominee is the newest identifiable success, that a
second reconciliation records nothing, that exactly one blocker is open, and that
cancelling it lets a worker run start. Verified by mutation in a copy outside the
repository: restoring the walk-back loop makes it fail, naming the older run.

### 3j. The fix for N1 reintroduced the exit N1 was raised for (O3, 2026-08-30)

§3g point 3 took apart a residual whose stated escape hatch — "re-dispatching the
review is the exit" — did not exist: `ROLE_REVIEW` is a server-side argument to
`ExecutionStore.create` and `ExecutionDispatcher._dispatch_review` is its only
production caller, so no endpoint starts a review run. The fix for N1 then wrote that
same nonexistent exit into the human-facing message it added:

- the review-owed blocker's body said "then dispatch the run again" after listing a
  cause about the *review* runtime, and two of the three `REVIEW_OWED_REASONS`
  (`REVIEW_OWED_WITHOUT_GATE_REASON`, `REVIEW_OWED_CANCELLED_REASON`) said "dispatch a
  review again" outright;
- `ExecutionStore._task_refusal` applied one remediation — "dispatch it again to
  re-validate it" — to worker and review runs alike. For a worker run that is a real
  instruction (`POST /api/runs`) and is the correct contrast; for a review run it names
  something nobody can do.

The lesson is not "check the message". It is that a document can retire a claim in the
section that analyses it and keep shipping it in the text a human reads, because those
are different artifacts and only one of them was under review.

**Corrected in every place that offered it**, and the exits are now written once rather
than in each cause's sentence:

- the three `REVIEW_OWED_REASONS` state what happened and what to look at, and say
  nothing about how to get out;
- `_block_review_owed`'s body states both exits, in one place, together with the fact
  that a blocker gates the task while it is open (so both exits begin by clearing it):
  **(1)** clear the item and add no review — the work is accepted with no review
  decision on record; **(2)** fix the recorded cause, clear the item, and dispatch
  another worker run — Agora automatically cross-reviews *that* run when it succeeds,
  within the bound stated in §3k. The body says explicitly that dispatching a review on
  its own is not an option and why;
- `_task_refusal` takes the run's `role` for the sole purpose of wording that
  remediation truthfully: for a review run it points at the review-owed blocker the
  cancel raises for the worker run, which is the item that actually carries the
  decision;
- `_resume_refusal`'s derived review-owed reason no longer says "dispatch one". It fires
  only when no gate item exists (§3f), so telling the operator to clear a blocker they
  do not have would be the same defect again; it says that a start whose reconciliation
  completes records the blocker, and that clearing it accepts the work unreviewed.

What each exit costs is stated rather than implied, because that is the consequence the
human is choosing and the whole reason this gate exists.

### 3k. The human-facing text was the unreviewed artifact (P1/P2/P3, 2026-08-30)

§3j ends on a lesson it did not then apply: "a document can retire a claim in the
section that analyses it and keep shipping it in the text a human reads, because those
are different artifacts and only one of them was under review." The next round found
three more defects, all of them in text and none in behaviour.

**P1 — the body contradicted the reason printed inside it.** The blocker's generic body
opened with "this work has not been seen by any review" and described exit 1 as leaving
the work "as nothing has reviewed it". `REVIEW_OWED_NO_GATE_ITEM`'s own `reason`, two
lines above in the same item, says the review *ran* and may have reached a conclusion
that failed to save. `REVIEW_OWED_REVIEW_CANCELLED` is not safe for that claim either:
`cancel` settles a RUNNING review, and a reviewer stopped partway had been given the
prompt. The fact that actually holds across all five causes — three `_dispatch_review`
failures, `REVIEW_OWED_NO_REVIEW_RUN`, `REVIEW_OWED_NO_GATE_ITEM`,
`REVIEW_OWED_REVIEW_CANCELLED` — is that **no review decision is on record**, which is
also the exact fact `_has_review_gate_item` releases on. The body now says that, and
whether any review looked at the work is left to the cause-specific `reason`, which is
the only place it can be said truthfully.

**P2 — the honest caveat was itself an overstatement.** §3j's own sentence, added as the
correction to exit 2, said "the review reads the worktree as it then stands, [so] the
earlier run's output is covered only insofar as it is still there". `_review_prompt`
does not read the worktree; it *samples* it. It takes only what `list_changes` reports
as changed against HEAD (`[]` on any git failure, indistinguishable from clean, and
capped at `MAX_FILES` = 200), truncates each file to `_REVIEW_FILE_CHARS` = 1,500
characters, stops adding files once `_REVIEW_PROMPT_BUDGET` = 12,000 characters are
spent (appending "[additional files omitted for length]"), skips any file it cannot
read, and finally truncates the whole prompt to 15,900 characters. So an unchanged
2,000-character file is covered to its first 1,500 characters, and a file past the
aggregate budget is not covered at all. Both the body and this document now state that
bound; in the body the three numbers are interpolated from the constants that enforce
them, so the sentence cannot drift from the code.

**P3 — the nonexistent exit survived in two more docstrings.** "dispatch another review,
or cancel the item and take the work as unreviewed" was still in
`ExecutionStore._has_accounting_review_run` and in
`test_a_human_cancelling_a_review_gets_the_blocker_that_releases_it` — the third and
fourth copies of a sentence O3 removed from the strings a user sees but not from the
instructions a maintainer follows.

**P4, found by the sweep P1–P3 prompted — the blocker's *title* was false.** It read
`跨评审未能启动` , "the cross-review failed to start". That was true of the only causes
that existed when it was written, the three `_dispatch_review` failures. This slice then
attached the same item — same title, because the title is its idempotency key — to
`REVIEW_OWED_NO_GATE_ITEM`, where the review started, ran and settled and lost only its
gate write, and to `REVIEW_OWED_REVIEW_CANCELLED`, where it may have run and been stopped
partway. The most prominent string in the item was the last one checked, because it was
not touched by the diff that changed what it means. It now reads `跨评审没有留下结论`,
"the cross-review left no conclusion", which holds for all five causes;
`REVIEW_FAILED_TITLE` keeps its name only because callers import it.

Four more sentences in the same family were corrected for the P1 reason — they described
clearing the blocker as "accepting the work unreviewed", which is the claim P1 retired:
`_resume_refusal`'s derived reason, `_has_review_gate_item`'s and
`reconcile_reviews_owed`'s docstrings, and `REVIEW_OWED_CANCELLED_REASON`, which had also
asserted that no review had seen the work. `_task_refusal`'s review-run remediation said
"the review-owed blocker this cancel records"; `_block_reviews_owed_for` never raises, so
the write can fail, and it now names the next start's reconciliation as the fallback. The
English `workspace.continue.errors.reviewOwed` said "the last run" where the Chinese said
实现运行; the refusal is about the last succeeded *worker* run, and a review run is often
the more recent row, so the English now says so too.

**What changed in the process, not just the text.** Every round of this slice found a
defect in human-facing text and none of them was found by the tests, because the tests
assert that a constant appears in an item's body, never what the sentence claims. So
each string is now treated as a claim with a named referent in code:

- a sentence that describes what the system *did* names the cause or state it is true
  of, and a sentence attached to more than one cause states only the intersection —
  which for this gate is "no review decision is on record", the same predicate
  `_has_review_gate_item` implements;
- a sentence that names an action states an action with a caller a human can reach; the
  test for that is a production caller, not the existence of a code path;
- a sentence that states a limit interpolates the constant that enforces it rather than
  restating the number;
- a sentence that asserts a write names what happens when the write fails, if the caller
  swallows the failure;
- a caveat is a claim and gets the same treatment. P2 exists because the correction
  written in the previous round was reviewed as a fix rather than as a new assertion;
- **the strings the diff does not touch are in scope.** P4 was reachable only by
  re-reading a constant this change never edited, against the causes this change added to
  it. Adding a cause to an existing item is a change to every sentence that item already
  carried.

A caveat is a claim too, and the review that clears the analysis does not clear the
prose beside it.

### The other half of the same problem: a dispatch that *failed*


§3 is about the *window*. A separate and larger hole was that a cross-review
dispatch which simply **failed** left no trace at all.
`_dispatch_review` returned silently when the project had left the registry or its
`reviewer_agent` was not in the adapter registry, and `_after_run` wrapped
everything else in `except Exception: pass` — so a failed prompt build, a request
the control plane rejected, a queue conflict, and a failed gate write all ended as
"no review exists". Both the Continue action and `create` then accepted another
worker run, because nothing was active and no gate was open. An unavailable
reviewer read as "this work needs no review", which is a silent substitution of
the kind `AGENTS.md` forbids.

So every path that ends without a review run records a durable `blocker`
Attention item on the task (`ExecutionDispatcher._block_review_owed`), scoped to
the run whose review is missing, naming what failed in its body. `blocker` is one
of `GATING_ATTENTION_KINDS`, so while it is open the create transaction refuses
another worker run and the Continue action declines to offer one: the failure
blocks its work instead of deleting the review stage.

**This item is also the release valve on §3, which is why the two do not
double-gate.** Its body promises that cancelling it lets the task accept another
implementation run. If the derived refusal also fired, cancelling would clear one
gate and leave the other standing with nothing left to act on — the shape of every
lockout this slice has produced. So `_reviews_owed` treats a run for which a
`REVIEW_REQUESTER` **`blocker` or `approval`** exists as already accounted for, in
*any* state (`cancelled` and `responded` included: the human's disposition of it is
the decision). The two kinds are one rule because they are the two shapes of the
same gate: the approval a review that ran raises, and the blocker a review that did
not raises (§3e). Matched on `(task_id, run_id, requester, kind)` rather than on the
item's title, because titles are redacted on insert and comparing against a
redacted copy of `REVIEW_OWED_BLOCKER_TITLE` would be a second thing to keep in
step; the constant lives in `agora/execution/models.py` so the store and the
dispatcher still name one item rather than two.
`test_a_failed_review_dispatch_does_not_double_gate_with_the_derived_refusal` is
the regression.

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

One thing this still does **not** do: a task whose row is gone gets nothing. An
Attention item hangs on a task, so there is nowhere to record it. The run's own
record is the only trace, and that is the honest end of that path. (It is also the
only `_dispatch_review` exit that ends without either a review or a blocker — and
it cannot make §3 claim a review is owed, because `create` reads the task row in
the same transaction and refuses a missing task first.)

## Alternatives rejected

- **Bump the task version when an Attention item is raised**, so
  `expected_task_version` refuses the dispatch. Rejected: it makes every attention
  item a task mutation, invalidates unrelated in-flight optimistic writes, and
  reports "the task changed" for what is actually "a gate is waiting" — the same
  loss of meaning this change exists to fix.
- **Refuse on any open Attention regardless of kind** (matching the client
  exactly). Rejected on 2026-08-29, **reversed by D1 on 2026-08-30** — all three
  kinds now gate. The original rejection argued that `question` binds to no
  artifact and settles no decision, so a task-level refusal on one asserts a gate
  that does not exist, and that unlike a permission prompt a question is answerable
  after its run is gone (the answer is information, not a reply to a blocked
  process), so it has no natural point at which it can be settled automatically.
  Both halves were wrong in the way that mattered: an unanswered question followed
  by a new run means the run proceeded on an unanswered assumption, and a question
  is always human-clearable, so it is never a gate nobody can act on (a
  bridge-origin one is also cancelled with its run, which a generically created one
  is not — see §1). The part of the old note that still stands: Codex
  permission prompts are `approval`, not `question`, so they gated either way. See
  §1.
- **A durable "review owed" marker** written in the transaction that settles the
  worker run. Rejected, and D2 chose derivation instead: a marker leaks on a crash,
  needs its own reconciliation, and has to be threaded through every terminal path
  (`finish`, `record_cancelled_output`, `abandon`), any one of which could forget
  it. Nothing is written for a derived fact, so nothing can leak. See §3.
- **Computing "does this task owe a review?" in the caller** and passing the answer
  into `create`. Rejected: that is the TOCTOU hole §3 exists to close. The caller
  passes the `auto_review` *flag*; the store asks the question inside its own
  transaction.
- **Re-dispatching the missing review at startup** instead of recording a blocker.
  Rejected; the three reasons are in §3a. Chiefly: the review prompt reads the live
  worktree, which after a restart is no longer guaranteed to hold what the run
  delivered.
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

A stale, unanswered `approval`, `blocker` or `question` blocks every new worker run
on its task, and so does a review that is owed and has not appeared. This is
deliberate — that is what a gate is — and it is why each refusal is narrow and
reversible:

- Only genuinely open items gate, and every kind has a named release (§1).
- The 409 body names each blocking item by `item_id`, `kind` and title, and states
  the two ways out: respond to it (approve/reject, or answer) or cancel it. Both
  are existing API operations, so no restart and no database surgery is needed.
- Review runs are exempt, so the review cycle can always complete.
- A prompt that has become *unanswerable* is not left as a gate: a bridge-origin
  item is settled with its run (§1a). A `question` or `approval` created any other
  way is not run-scoped in the first place — it stays open until a human answers or
  cancels it, which is the exit every gate item has.
- An unsettled *review run* cannot become a permanent lock either:
  `recover_abandoned` settles a RUNNING one on restart, and the dispatcher settles
  a QUEUED one on shutdown, and a human can cancel it. (The shutdown half is a
  same-change fix: a run whose coroutine was cancelled before the event loop ever
  entered it used to stay QUEUED forever.) Since §3g none of those cancels *discharges*
  the review — each records the review-owed blocker for the worker run instead, so the
  work does not silently become reviewed, and the human has one item with two exits.
- An *owed* review releases itself when the review appears, and the one case that
  cannot is reconciled into a human-clearable blocker at every startup (§3a). The
  derived refusal and that blocker never gate at once. If that reconciliation cannot
  finish, a persisted queued worker run on the affected task is refused rather than
  resumed, with the reason on its row and on the task's timeline (§3f) — a run
  refused, not a task locked, and dispatching again after the cause is cleared is the
  exit.

Note that this lockout already existed in the UI, which has refused to offer
Continue on any open Attention since the Continue action shipped — including a
`question`, which is what D1 makes authoritative. The client's `followUpArmed`
timer likewise guessed at the window §3 now refuses for real; the timer is a
heuristic, the refusal is the contract, and the client still shows the timer
because it is the friendlier of the two.

## Persisted QUEUED runs, and where the gates are re-checked

Both row-reading checks run in the transaction that *inserts* the run, and nothing
re-checks a row already inserted. A QUEUED row therefore outlives the state it was
validated against: across an upgrade from a database written before these gates
existed, and across a crash. `recover_abandoned` settles RUNNING rows and says
nothing about QUEUED ones, so `ExecutionDispatcher.resume_queued` used to reschedule
persisted QUEUED runs unconditionally — a worker run starting while its
predecessor's review was queued or running, or while a human gate waited, which is
exactly what `create` refuses.

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

Five, in this order:

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
   open? (Only the gate items this run's own **bridge** raised are excluded: a
   permission prompt a run raised is not prior work that run must wait behind. Cut by
   provable origin, not by `run_id` — §1b.)
5. **For a worker run only, and only with `auto_review` on:** does this task owe a
   cross-review that does not exist yet (§3)? Asked last, in `create`'s order, so an
   existing gate item is reported as the gate it is. This question was added by §3f;
   before it, both re-validations trusted that the startup reconciliation had raised
   every gate they read.

Both re-validations ask all five; they differ only in what "already unsettled"
(question 3) is read from. The resume sweep decides a whole queue at once, so it
starts from the RUNNING rows and adds each review it admits. The launch re-check
judges one row, so it reads every *other* unsettled run on that task — QUEUED as
well as RUNNING, which is what `create` counts (`ACTIVE_RUN_STATES`) — and excludes
the row being judged, since a run cannot be the reason it may not launch.

Both ask §3's derived "does this task owe a review?" question, and neither did until
§3f. The reason for leaving it out — a QUEUED worker run coexisting with an owed
review is not reachable through the API, because the run would have been refused at
create time — was true of the API and false of a restart whose review-owed
reconciliation did not finish. §3f is the finding and the choice between the two
available fixes.

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

**A row that records no task version fails closed (N2, 2026-08-30).** This was the
opposite until N2, and the old text is worth quoting because the shape of its error
recurs:

> **Residual:** a row that records no task version — a database written before this
> key existed, or one edited by hand — can only be lifecycle-checked. Unknown is
> reported by skipping the question it cannot answer, not by refusing every persisted
> row from an older database: that would settle runs that are very likely fine, which
> is a larger harm than the window it would close.

"Skipping the question it cannot answer" reads as modest, and it is not: skipping a
*refusal* check is granting permission. The version question has two possible answers
and the code returned the permissive one for a third state — unknown — while the
sentence describes it as declining to answer. And the harm it weighed was the wrong
way round: "runs that are very likely fine" is not a property this transaction can
read, whereas the window it left open is ordinary. A queued run persisted by an older
Agora records no version; the task is then edited, or moves
`running -> blocked -> running`, and its version bumps; nothing can say which version
that run was validated against, and it is scheduled anyway — launching an agent
against a prompt and a worktree built for state that no longer exists, which is
exactly what recording the version exists to prevent.

So unknown refuses, with a reason a human can act on ("it records no task version, so
it cannot be checked against its task's current version (N)"), and the run is settled
`CANCELLED` like every other refusal here. Dispatching it again re-validates it
against the task that exists now and records the version this time. `create` has
written `TASK_VERSION_KEY` on every run since the key existed, so no run this build
creates can reach that branch — the population affected is exactly the rows nobody can
vouch for.

Regression: `test_resume_re_asks_the_task_questions_of_a_queued_run`, whose case 3
**asserted the old admission** and now asserts the refusal. The test docstring says so
explicitly, because a test that pins a defect is harder to find than the defect. Its
control case was extended at the same time: a hand-inserted row that *does* record the
current version still resumes, so the refusal is about the version being unknown and
not about the row being persisted.

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

All five questions, not only question 2. It asked only question 2 at first, on the
stated ground that re-asking the gate questions here would cancel a run waiting
behind a bridge permission prompt raised in the meantime — a transient, run-scoped
fact. That ground does not hold: the gate question already excludes the items this
run's own bridge raised (`exclude_bridge_run_id`, §1b), which *is* that fact, while
still seeing every gate raised by prior work. Leaving the questions out left a real
hole instead:
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
wide rather than the whole time a run spends queued. Nothing short of holding the
create transaction open across the process launch would close it, and that is not
on offer.

## Wire contract

`POST /api/runs` still answers 409. For these three refusals only, `detail` is an
object rather than a string:

```json
{
  "code": "attention_gate_open",
  "message": "Task has 1 open gate item(s) waiting for you: approval attn_… \"…\". Respond to each one (approve/reject, or answer) or cancel it, then start the run again.",
  "items": [{"item_id": "attn_…", "kind": "approval", "title": "…"}]
}
```

`code` is one of `attention_gate_open`, `review_run_active` or `review_owed`. For
`review_run_active` and `review_owed`, `items` carries `{run_id, state}` — for
`review_owed` that is the succeeded worker run whose review is missing, and the
message states both exits (wait for the review to appear; or, if Agora stopped
inside the window, restart it and clear the blocker it records). Every other 409
from this route keeps its plain-string `detail`, so no existing client breaks.

Codes are defined once per side —
`GATE_CODE_ATTENTION_OPEN` / `GATE_CODE_REVIEW_ACTIVE` / `GATE_CODE_REVIEW_OWED` in
`backend/agora/execution/store.py`, `RUN_GATE_CODES` in
`frontend/src/lib/continue-run.ts` — and
`test_run_gate_conflict_codes_mirrored_by_the_frontend` in
`backend/tests/test_execution.py` is the alarm when they diverge. The client maps
each code to its own sentence (`DispatchFailure`), and `review_owed` counts as
"nothing was started", because the create transaction refused before inserting
anything and what is owed is a run that does not exist yet.

## Consequences elsewhere

- `WorkflowOrchestrator.dispatch` already wraps `dispatcher.queue` and converts
  any exception into a recorded workflow blocker, so a gated step becomes a
  visible blocker rather than a crash. No change needed there — including for
  `review_owed`, which arrives as the same `RunGateError`.
- `ExecutionDispatcher._dispatch_review` passes `role=ROLE_REVIEW`, so the
  auto-review path is unaffected by all three checks: they are worker-only.
- `agora.api.app.lifespan` calls `ExecutionDispatcher.start_recovery()`, which runs
  `reconcile_reviews_owed()` and then `resume_queued()`. Both are startup-only and
  both are idempotent; the order is enforced by `resume_queued`'s guard rather than
  by the order of statements in `lifespan` (§3a), and the resume sweep no longer
  depends on the reconciliation having *completed* (§3f). `lifespan` prints both
  reconciliation failure kinds from the receipt — the unreadable read and each owed
  run whose blocker could not be recorded — to stderr.
