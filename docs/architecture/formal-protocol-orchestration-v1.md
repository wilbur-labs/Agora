# Formal protocol orchestration v1

Status: reviewed implementation baseline.

This increment adds an explicit CLI path from the provisional task scheduler to
the reviewed Context/Handoff and Gate settlement boundary. It does not replace
the missing authoritative AI-DLC graph and does not start UI work.

## Entry point

Formal dispatch is opt-in so existing 0.5 planning Tasks are not silently
reinterpreted:

```text
agora task start --contract PATH --run --protocol-v1 --allow-unbounded-native-usage
agora task next TASK_ID --protocol-v1 --allow-unbounded-native-usage
agora task run TASK_ID --protocol-v1 --allow-unbounded-native-usage
agora task retry TASK_ID STAGE_KEY --protocol-v1
agora task resume TASK_ID
agora task preflight TASK_ID
```

A formal Run requires a pinned concrete Task contract whose persisted canonical
hash still matches its content. The project root must resolve to an exact Git
ref and commit, and its worktree must be clean so a runtime cannot inspect
content outside that commit binding. Each immutable Gate is scoped to that
project/repository, ref, commit, Stage, and the contract's Evidence requirements.

Every CLI command that can start a provider-backed Run requires the explicit
`--allow-unbounded-native-usage` acknowledgement. Without it, the CLI fails
before service construction, capability collection, Task creation, Run claim,
or process spawn. The acknowledgement is a local dispatch safety interlock; it
does not claim or create a provider-native limit.

Both the formal and compatibility `next`/`run` paths invoke native runtime
adapters, so the interlock applies to both. `retry` is intentionally different:
it only moves the blocked Stage projections back to ready/pending and never
claims a Run or starts a process. Any later provider execution still enters
through an acknowledged `next` or `run`.

## Dispatch and authority flow

```text
pinned Task Contract + current Git revision + formal prior Artifacts
    -> fresh pinned-runtime observation and allow/block preflight
    -> bounded sealed Context Pack
    -> operational Run reservation
    -> ControlPlaneStore.start_protocol_run
    -> native read-only runtime
    -> fail-closed Agent Adapter
    -> ControlPlaneStore.settle_protocol_run
    -> formal Artifact/Evidence/Gate/Stage result
    -> compatibility projection into the 0.5 usage/Plan ledger
```

The Context builder maps the current Stage contract, role, acceptance criteria,
latest explicit Task decisions, prior versioned Artifact references, forbidden
constraints, and Run budget into the frozen Context Pack. Managed prior
Artifact content is carried only as an exact hash-verified materialization with
an authoritative Artifact source reference; the Artifact reference always
remains in `input_artifacts` and the materialization is not authoritative state
or new Evidence. To preserve the Windows 24 KiB argv bound, policy entries are
bounded projections whose source references retain the full sealed decision
hashes. Prior Artifact contents are then considered deterministically from the
newest Stage backward and materialized only when the complete prompt still
fits. A candidate that does not fit remains reference-only, while smaller
older candidates may still be considered in the same deterministic pass.
Reference-only entries retain version, hash, byte count, and omission reason;
Agora never invents a summary or changes the authoritative Artifact. Full
transcripts are never supplied.

Required output identities are unique to the Task and Run. Contract Artifact
IDs remain templates, avoiding cross-Task collisions in the global immutable
Artifact registry.

The prompt contains the canonical sealed Context Pack and exact Gate Evidence
bindings. A bounded exact-key guide names the Handoff, Artifact, Evidence,
Artifact-version, and producer fields and rejects common non-protocol aliases;
it also states the frozen Artifact storage values and requires raw JSON without
prose or Markdown fences. Optional NativeStateSnapshot and MemoryCandidate
values use their frozen distinction: a NativeStateSnapshot remains null unless
the Context supplies a complete frozen object, while generated memory
candidates remain allowed only as exact MemoryCandidate objects rather than
strings. Every nested producer remains the exact ProducerRef object, and
Evidence details remains a JSON object rather than free-form transport text.
The guide does not map aliases, fill missing fields, or synthesize Evidence.
The parser still permits only the existing
whole-document fence repair; prefixed prose plus a fenced object remains a
protocol failure. A process exit code, Agent suggestion, or legacy semantic
JSON cannot advance the Stage. Evidence that claims a configured Gate
requirement with the wrong repository, ref, commit, or kind is converted to a
protocol failure and Attention before Registry mutation.

Only the Control Plane settlement decides whether the authoritative Stage is
completed, blocked, failed, or cancelled. The provisional Plan advances only
after it receives a completed authoritative Stage receipt. This projection
exists temporarily to preserve the reviewed Token reservation/settlement ledger
and existing CLI status while the unified Task projection is still missing.

The native capability observation is collected outside the operational Run
claim transaction. Its sealed preflight decision is included in the Context
Pack and persisted with the Run. After formal Run start, the Runner rechecks
the exact observation, registry, command, resolved launch target, and policy
hashes immediately before process creation. Expiry or drift fails before spawn
and settles exact-zero process-not-started usage; no alternate runtime is tried.

Structured native output is drained into a bounded byte tail. When that bound
is known to have removed the stream prefix, the Codex JSONL normalizer may
discard exactly one partial leading transport frame. Every later non-empty line
must still be valid JSON, and exactly one final usage event plus a valid agent
message remain mandatory. This is format-only recovery; it cannot change or
invent Handoff semantics, Evidence, blockers, or usage.

The default Claude process also starts with the native `--safe-mode` boundary
in addition to plan permissions and disabled session persistence. This prevents
unbound user or project customizations such as hooks, skills, plugins, MCP
servers, output styles, and session-memory handlers from replacing or
post-processing the Handoff stdout. Agora does not modify those native files;
it isolates the formal subprocess and still validates the returned Handoff
fail-closed.

The Task-scoped `preflight` command is a read-only preview over an already
initialized formal route. It returns the exact sealed decision used by the
dispatch derivation together with explicit no-claim/no-persistence/no-spawn
markers and bounded pinned-route remediation. It does not call `resume` or
initialize missing state, and its synthetic preview Run identity is never
persisted or supplied to the claim path. Real dispatch always derives a fresh
decision and rechecks it at claim and immediately before spawn.

## Recovery

The operational reservation and Control Plane use separate durable SQLite
transactions, with fail-closed recovery around the only two interruption
windows:

- If the operational reservation succeeds but formal Run start fails, no native
  process is launched, usage settles at exact zero, and the provisional Stage
  blocks. Formal retry accepts the still-ready authoritative Stage and repairs
  only the blocked provisional dispatch projection before a new attempt.
- If formal settlement commits before the compatibility projection, `task
  resume` reconstructs the projection from the sealed protocol Run and does not
  redispatch or duplicate usage settlement.
- If a started process disappears before settlement, `task resume` records an
  unavailable-use interruption, settles the formal Run as failed, and does not
  duplicate dispatch.
- A protocol retry explicitly moves both the authoritative Stage and the
  provisional dispatch projection back to ready/pending. A previously passed
  Gate becomes stale before reevaluation.
- Gate configuration is immutable in this bounded increment. Retry therefore
  resolves the repository again and requires the same repository, ref, and
  commit as the configured Gate. A changed revision is rejected before either
  projection is mutated; the caller must start a new Task bound to that
  revision. Gate rebinding remains a later explicit persistence design.

Cancellation remains distinct in the frozen Run/Stage dimensions and is
projected as a cancelled operational Run. Unknown live process state always
refuses redispatch.

## Deferred boundaries

This increment does not expose formal Run start/settlement over HTTP, map the
legacy Task state to the frozen Task state machine, publish new long-term memory,
implement dynamic risk/capability routing, recover the full AI-DLC graph, or add
the Task Workbench UI. The unified authoritative Task projection and real
provider-specific usage remain subsequent bounded increments.
