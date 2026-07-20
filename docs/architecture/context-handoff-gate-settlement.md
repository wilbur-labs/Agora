# Context/Handoff and Gate settlement boundary

Status: reviewed implementation increment; Kiro and Claude Code approve.

This increment connects the frozen Context Pack, Handoff Pack, Run protocol,
Artifact/Evidence Registry, Gate evaluator, Stage state machine, and Attention
ledger without routing through the provisional 0.5 `finish_run` semantics.

## Authority boundary

The runtime is allowed to return only terminal process/transport facts and a
candidate Handoff Pack. It cannot write Task, Stage, Gate, Artifact, Evidence,
or Attention state.

Agora performs two replay-safe operations:

1. `start_protocol_run` verifies the Task/project/Stage/Gate scope and every
   input Artifact version, persists the sealed Context Pack, and moves the
   authoritative Stage from `ready` to `running` in one transaction.
2. `settle_protocol_run` persists the four independent Run dimensions and the
   accepted Handoff, registers its Artifacts and Evidence, selects only exact
   Gate-scoped Evidence, evaluates the Gate, creates required Attention, and
   settles the Stage in one transaction.

Both operations use a caller-supplied operation key and canonical input
fingerprint. Identical replay returns the recorded result; changed input under
the same key fails closed.

The existing Registry operations for explicit active-Evidence management and
Gate evaluation remain available for review and reconciliation workflows. They
can change Gate state but never settle a Run or transition a Stage. A protocol
Run settlement therefore re-evaluates the current Gate before any possible
Stage completion; an earlier independently passed Gate cannot be used as a
completion shortcut.

## Immutable ledger

`protocol_runs` is additive and does not rewrite the legacy execution or
provisional orchestration tables. One row binds:

- Run, project, Task, Stage, and Gate identities;
- the sealed Context Pack identity, canonical payload, and content hash;
- the terminal `RunProtocolState` dimensions;
- at most one accepted sealed Handoff Pack;
- a stable adapter error code and required Attention reference;
- start and settlement timestamps.

Reads revalidate the sealed payloads and their ledger identity/hash bindings.
Input Artifact references must already exist in the same Task/project with the
exact version, hash, kind, and location.

## Formal Gate settlement

Accepted Handoff Artifacts and Evidence become formal Registry objects only
inside settlement. Evidence whose requirement id belongs to the configured
Gate must match its exact repository, ref, commit, and kind. Evidence for other
requirements remains historical and is not activated for the current Gate.
Current Handoff Evidence replaces prior active Evidence only for the same Gate
requirement. Active Evidence for requirements supplied by a prior Run, another
runtime, or a human-review workflow remains selected, so multi-source Gates can
be evaluated without forcing one runtime to reproduce independent Evidence.

Stage settlement is derived by Agora:

| Run semantic result | Gate result | Stage result |
| --- | --- | --- |
| `succeeded` | `passed` | `completed` |
| `succeeded` | not passed | `blocked` |
| `blocked` | any | `blocked` |
| `failed` | any | `failed` |
| `cancelled` | any | `cancelled` |

An Agent suggestion is never used as `next_safe_action`; only the formal Gate
evaluation can provide that value. A process exit code, including zero, is not
part of the Stage-completion decision.

If the adapter rejects the Handoff, settlement stores the protocol failure,
does not replace active Gate Evidence, blocks the Stage, and creates a durable
high-urgency Attention item. Any failure while registering a Handoff, evaluating
the Gate, settling the Stage, or creating Attention rolls back the entire
settlement.

A schema-valid Handoff with semantic `failed` is retained with its historical
Artifacts and Evidence, but it does not replace active Evidence or churn the
Gate because that Gate result cannot change the failed Stage outcome.

Approval invalidation also creates one deterministic, bounded impact-analysis
Attention item per affected Task in the same atomic invalidation transaction.
The item records affected counts and up to 200 stable identifiers per category;
larger impact sets are explicitly marked truncated while the authoritative
invalidation receipt remains complete.

## Subsequent integration

This boundary increment intentionally did not expose Run start/settlement over
HTTP, replace the provisional native-runtime dispatcher, or map the legacy Task
state to the frozen Task state machine. The subsequent explicit CLI integration
is documented in `formal-protocol-orchestration-v1.md`: it now generates a
Context Pack from a pinned concrete Task/Stage contract, invokes the fail-closed
Agent Adapter, and calls this reviewed boundary. The unified Task projection,
layered memory publication, full AI-DLC graph, and UI remain later work.

Infrastructure launch, timeout, interruption, and transport failures currently
settle the Stage without automatically creating human Attention. Retry versus
escalation policy for those non-protocol failures remains owned by the future
orchestrator; protocol failures always create Attention here.
