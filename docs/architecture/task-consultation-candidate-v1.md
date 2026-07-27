# Task consultation candidate v1

Status: reviewed implementation baseline.

This increment separates native-runtime advice from Agora-owned Task decisions.
A consultation result is durable and inspectable, but it has no authority to
change a Task, Stage, Run, Artifact, Evidence, Gate, or Approval. Only an
explicit user disposition may adopt its bounded decision fields into the
authoritative Task decision ledger.

## Versioned contracts

`ConsultationCandidate@1.0` is hash sealed and binds:

- candidate, consultation, operation, Project, Task, Plan, and Stage identity;
- the observed Plan version and pinned producer runtime;
- one bounded decision key/value, title, analysis, and stable source refs;
- the actor that registered the candidate;
- `advisory_authority=false` and `formal_artifact=false`; and
- a timezone-aware creation time and canonical content hash.

`ConsultationCandidateDisposition@1.0` is a separate hash-sealed receipt. It
binds the exact candidate hash, action, actor, reason, Plan version boundary,
and optional TaskDecision identity/hash/version.

`ConsultationCandidateDraft@1.0` is the strict, untrusted provider result
shape. It contains only the bounded title, decision key/value, analysis, and
stable source references. It contains no Project, Task, Plan, Stage, runtime,
repository, authority, Artifact, Evidence, or Gate fields; Agora supplies and
validates those bindings after the native process returns.

The executable models and checked-in JSON Schemas reject unknown fields and
verify canonical content hashes on every read.

## Registration boundary

Candidate registration is allowed only for the current pending or blocked
Stage, with no active Run, at an exact expected Plan version. The producing
runtime must equal the runtime already pinned to that Stage; consultation
cannot dynamically substitute another runtime.

Before sealing, Agora redacts bounded human/native text. The append-only Task
event contains identities, hashes, and explicit non-authority markers, not the
candidate body. Registration is idempotent by operation key. Reusing an
operation key with different inputs fails closed. When a caller omits the key,
Agora deterministically derives it from the complete normalized request; it
never generates a random retry identity.

Registration does not:

- increment the Plan version;
- create a TaskDecision or formal Artifact;
- change Stage, Gate, Task, Run, Approval, or Evidence state; or
- copy candidate content into memory or a provider prompt.

## Explicit disposition

`agora task adopt` and `agora task reject` require the candidate ID, exact
expected Plan version, actor, and reason.

Adoption is atomic. It creates or reuses the exact versioned TaskDecision for
the candidate key/value and explicit adoption reason, records a hash-bound
disposition, and increments the Plan version once. The version increment
invalidates stale claims and sibling candidates observed against the old Plan.
It does not pass a Gate, advance a Stage, or create an Artifact.

Rejection records a hash-bound disposition without creating a TaskDecision or
changing the Plan version. This permits multiple candidates from the same
consultation boundary to be rejected independently. A candidate may receive
only one disposition, and operation-key replay is exact and idempotent.

Both actions fail closed for stale candidates, a different Task or current
Stage, terminal Plan state, an active Run, or a reused operation key with
different inputs. Omitted disposition keys are deterministically derived from
the complete normalized action request, so an identical CLI retry returns the
original receipt.

## Projection

Unified Task projection schema `10.0` adds paginated,
hash-verified `consultation_candidates` and
`consultation_candidate_dispositions`, with true collection totals and pages.
The CLI projection labels each visible candidate `pending`, `adopted`, or
`rejected`. Existing formal Artifacts, Evidence, approvals, Gates, and
authoritative next-safe-action derivation remain unchanged.

Unified Task projection schema `11.0` adds paginated
`consultation_runs`. Each execution exposes its exact Plan/inventory/Stage/
runtime/repository binding, independent process/transport/schema dimensions,
repair count, terminal candidate identity, and hash-sealed usage observation.
Active and settled consultation reservations are included in aggregate budget
truth; unavailable usage is never recorded as zero.

## Native dispatch and settlement

`agora task consult TASK_ID DECISION_KEY --question ... --tokens ...` dispatches
only the runtime already pinned by the authoritative current Stage route. It
requires the same explicit `--allow-unbounded-native-usage` acknowledgement as
other provider-backed commands because the reservation is admission control,
not a provider hard cap. A cost-bounded Task also requires a bounded
consultation cost reservation.

The claim transaction:

- requires an active/pending or blocked/blocked compatibility Stage that
  exactly matches the authoritative route;
- binds the current Plan version and clean repository ID/ref/commit;
- refuses a concurrent formal Run or consultation;
- debits earlier formal and consultation settlements conservatively;
- protects every unfinished required reviewer Stage allocation; and
- records a deterministic exact-input operation key before process spawn.

The native runtime receives a bounded Task/Plan/route/repository/decision
context, never a prior transcript. It is explicitly prohibited from changing
files or native AI-DLC state and from claiming formal Artifact, Evidence,
Stage, Gate, or Task authority. Its output must be exact draft JSON; one
whole-document `json` fence removal is the only format repair.

Settlement records process, transport, schema, output hash, error, and native
usage independently. Before candidate creation, Agora rechecks the clean
repository revision and transactionally revalidates the exact Plan version and
authoritative route. A valid candidate is redacted, hash sealed, and registered
in the same transaction as terminal consultation settlement. Failed,
interrupted, malformed, stale, sensitive, or repository-drifting results still
settle usage but create no candidate.

`task resume` never redispatches a running consultation. A live or
uninspectable persisted PID blocks recovery. A missing PID settles as a
provable launch failure; a dead persisted PID settles as interrupted with
usage unavailable.

## Deferred boundaries

Multiple candidates from one native execution, authenticated HTTP exposure,
dynamic runtime/model routing, the missing authoritative AI-DLC graph,
parallel/DAG routing, and Task Workbench UI remain separate reviewed
increments. Consultation execution does not turn machine-local capability
observation into routing authority or substitute another runtime/model.
