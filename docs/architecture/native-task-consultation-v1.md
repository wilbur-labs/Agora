# Native Task consultation v1

Status: implementation baseline pending independent review.

This increment lets the authoritative current Stage runtime answer one bounded
Task decision question without claiming the Stage or mutating formal delivery
state. The execution result is an advisory candidate, not a TaskDecision,
Artifact, Evidence item, Gate result, approval, memory entry, or route.

## Authority boundary

Agora derives the consultation runtime from the first incomplete Stage in the
sealed grouped inventory. A caller supplies only a decision key, question, and
admission reservations. It cannot select or substitute a runtime or model.

The consultation claim binds:

- Project, Task, Plan, and observed Plan version;
- grouped inventory identity/hash and current Stage/role/runtime;
- clean repository ID, canonical ref, and commit;
- normalized decision key and prompt hash;
- Token and optional cost reservations; and
- a stable exact-input operation key.

Consultation claim does not change the compatibility Plan cursor, Stage
attempt, Task lifecycle, Gate, Attention, Artifact, Evidence, Approval,
decision, or formal Run ledger.

## Admission and concurrency

Only an active/pending or blocked/blocked Plan/Stage pair may consult, and the
compatibility Stage must exactly equal the authoritative Control Plane route.
The claim fails before spawn when another formal Run or consultation is active.
A formal Run claim also fails while a consultation is active.

Every prior formal and consultation settlement is charged to admission.
Unavailable usage is conservatively charged at its reservation. Active
reservations are charged separately. All unfinished reviewer Stage Token and
cost allocations remain protected; consultation cannot consume them. A
cost-bounded Task requires an explicit consultation cost reservation.

## Native input and result

The runtime receives a bounded JSON context embedded in a read-only advisory
instruction. Context includes Task summary and acceptance, contract binding,
Plan/methodology identity, authoritative route, clean repository revision,
latest explicit human decisions, and the exact question. It never includes a
full previous transcript.

The only accepted native result is `ConsultationCandidateDraft@1.0`:

```json
{
  "schema_version": "1.0",
  "title": "concise title",
  "decision_key": "exact.requested_key",
  "decision_value": "bounded proposed value",
  "analysis": "tradeoffs and uncertainty",
  "source_refs": ["requirement:stable_id"]
}
```

Unknown fields, oversized output, surrounding prose, invalid JSON/schema, and
a different decision key are protocol failures. The sole format repair removes
one whole-document Markdown JSON fence. It cannot invent or alter content.
Process, transport, and schema status remain separate; exit code zero is not
semantic success.

## Settlement and recovery

Agora records terminal output hash, bounded/redacted error, repair count, and
hash-sealed provider usage. It rechecks the clean repository after a started
process and revalidates the exact Plan version and authoritative route inside
the settlement transaction. Repository drift, stale context, or a source
reference that would require secret redaction creates no candidate.

Only a valid draft is redacted and sealed as `ConsultationCandidate@1.0` in the
same transaction as consultation settlement. Its authority flags remain false.
Human `adopt` or `reject` is still required.

Recovery never repeats native dispatch. A persisted live or uninspectable PID
blocks `task resume`. A missing PID settles as a launch failure with provable
zero usage; a dead PID settles as interrupted with unavailable usage. The
separate consultation execution appears in unified projection schema `11.0`.

## Deferred work

This version returns at most one candidate. It does not add dynamic
runtime/model selection, provider serviceability routing, authenticated HTTP
routes, the complete authoritative AI-DLC graph, branch/rework/DAG scheduling,
or Task Workbench UI.
