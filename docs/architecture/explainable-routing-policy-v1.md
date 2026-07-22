# Explainable routing policy v1

Status: reviewed implementation baseline.

This increment explains and enforces one already pinned formal Stage route. It
does not select or substitute a runtime, change the sealed grouped Stage
inventory, or claim to supply the missing authoritative AI-DLC graph. The
authoritative route remains the decision defined in
`authoritative-stage-routing-v1.md`; this policy records why dispatching that
route is allowed under the current provisional foundation.

## Versioned policy boundary

`agora-foundation-routing-policy@1.0` is deliberately bounded to the pinned
`agora-aidlc-foundation@0.1` roles and the current concrete Task contract. Its
hash-covered definition records:

- capabilities required by `engineering_planner`, `independent_reviewer`, and
  `methodology_steward`;
- the corresponding declared Codex, Claude, and Kiro capability sets;
- which roles are independent reviewers;
- the minimum independent reviewer count for each Task risk; and
- that unfinished required reviewer Stage allocations are protected and
  runtime substitution is disabled.

The capability declarations are local policy facts derived from the checked-in
product requirements. They are not live provider discovery and do not authorize
a different model, runtime, role, or methodology Stage. An unknown role,
runtime, capability binding, reviewer, or contract relationship fails closed.

## Dispatch checks

Before a formal process starts, Agora derives exactly five checks:

1. `stage_assignment`: the Task, sealed inventory route, pinned methodology,
   concrete contract, and compatibility dispatch ledger agree on Stage, role,
   and runtime;
2. `runtime_capability`: the pinned runtime's declared capabilities cover every
   capability required by the Stage role;
3. `reviewer_coverage`: the Task reviewer declaration exactly matches the
   sealed reviewer Stages, each reviewer has one capability-complete contract
   role, and every `independent_from` role is assigned to another runtime;
4. `risk_coverage`: low and medium risk require at least one independent
   reviewer, while high and critical risk require at least two; and
5. `protected_budget`: the current Run reservation plus every unfinished later
   required reviewer Stage allocation fits inside the remaining Task Token and,
   when configured, cost envelope.

The provisional sealed method currently requires both Claude correctness review
and Kiro methodology review for every Task. That is an explicit expansion above
the one-reviewer minimum for low and medium risk, not an inference from process
output. High and critical risk require both and therefore cannot reduce the set.
Extra, missing, duplicate, or differently assigned reviewers require a future
methodology/policy change and are rejected by this bounded version.

## Protected budget

The protection calculation occurs before dispatch:

```text
available = Task allocation - conservative settlements - active reservations
required  = current Run reservation + unfinished future reviewer allocations
dispatch  = available >= required
```

An unavailable Token or cost settlement is conservatively debited at its Run
reservation. A process that provably never started settles both Tokens and cost
at exact zero. Budget pressure cannot remove Claude or Kiro, reduce their Stage
allocations, or turn a missing review into a passing Gate. A retry that would
consume protected review capacity fails before process spawn and directs the
caller to increase the Task budget or reduce scope without weakening the
reviewer set.

This increment does not add a budget-amendment command. The existing Stage
allocator remains unchanged; the new guard makes its protected-review
consequence explicit.

## Persistence, Context, and recovery

Each formal Run carries one immutable `RoutingPolicyDecision` in the additive
`routing_policy_payload` column. The decision includes the policy hash,
inventory and methodology bindings, pinned Stage/runtime, Task risk, required
capabilities and reviewers, reviewer assignments, conservative budget inputs,
all five check results, and bounded rationale. Its canonical content hash is
verified whenever the Run is read.

Agora first derives a read-only preview so the sealed Context Pack can include
the full decision and use its exact Run reservation. `claim_current_stage` then
re-derives the decision inside the same SQLite write transaction as the Run and
usage reservation. Any Task, contract, methodology, route, reviewer, or budget
change between preview and claim rejects the claim without a Run or charge.

Unified Task projection schema `6.0` exposes the persisted decision on each
formal operational Run. Historical legacy Runs may have no decision. Reads do
not synthesize one or mutate state; malformed or hash-tampered policy payloads
fail closed.

## Deferred boundaries

Dynamic runtime/model substitution, provider capability discovery, reviewer-set
changes, policy migration, budget amendment, authenticated HTTP lifecycle
commands, parallel/DAG routing, exact provider usage, the missing authoritative
AI-DLC graph, and Task Workbench UI remain separate reviewed increments.
