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

## Deferred boundaries

Native `agora task consult` dispatch and response parsing, consultation-specific
usage settlement, multiple candidate generation, authenticated HTTP exposure,
dynamic runtime/model routing, the missing authoritative AI-DLC graph,
parallel/DAG routing, and Task Workbench UI remain separate reviewed
increments. The persistence and disposition boundary is intentionally usable
by that later provider-dispatch increment without making provider output
authoritative.
