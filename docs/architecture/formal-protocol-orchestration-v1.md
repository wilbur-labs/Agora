# Formal protocol orchestration v1

Status: reviewed implementation baseline.

This increment adds an explicit CLI path from the provisional task scheduler to
the reviewed Context/Handoff and Gate settlement boundary. It does not replace
the missing authoritative AI-DLC graph and does not start UI work.

## Entry point

Formal dispatch is opt-in so existing 0.5 planning Tasks are not silently
reinterpreted:

```text
agora task start --contract PATH --run --protocol-v1
agora task next TASK_ID --protocol-v1
agora task run TASK_ID --protocol-v1
agora task retry TASK_ID STAGE_KEY --protocol-v1
agora task resume TASK_ID
```

A formal Run requires a pinned concrete Task contract whose persisted canonical
hash still matches its content. The project root must resolve to an exact Git
ref and commit, and its worktree must be clean so a runtime cannot inspect
content outside that commit binding. Each immutable Gate is scoped to that
project/repository, ref, commit, Stage, and the contract's Evidence requirements.

## Dispatch and authority flow

```text
pinned Task Contract + current Git revision + formal prior Artifacts
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
Artifact content is carried only as a hash-verified materialization with an
authoritative Artifact source reference; the Artifact reference remains in
`input_artifacts` and the materialization is not authoritative state or new
Evidence. Full transcripts are never supplied.

Required output identities are unique to the Task and Run. Contract Artifact
IDs remain templates, avoiding cross-Task collisions in the global immutable
Artifact registry.

The prompt contains the canonical sealed Context Pack and exact Gate Evidence
bindings. The runtime may return only an exact Handoff Pack or the one permitted
whole-document fence repair. A process exit code, Agent suggestion, or legacy
semantic JSON cannot advance the Stage. Evidence that claims a configured Gate
requirement with the wrong repository, ref, commit, or kind is converted to a
protocol failure and Attention before Registry mutation.

Only the Control Plane settlement decides whether the authoritative Stage is
completed, blocked, failed, or cancelled. The provisional Plan advances only
after it receives a completed authoritative Stage receipt. This projection
exists temporarily to preserve the reviewed Token reservation/settlement ledger
and existing CLI status while the unified Task projection is still missing.

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
