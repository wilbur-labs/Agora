# AWS AI-DLC methodology completion approval v1

Status: implemented and independently reviewed

## Purpose and command

After both independent completion-review Runs have settled with exact passed
Evidence, the final Gate is `passed`, its Stage is `completed`, and the Task is
`needs_review`. Only then may an authenticated human complete the Task:

```powershell
agora task migration-completion-approve SUCCESSOR_TASK_ID `
  --request completion-approval.json `
  --credential-env AGORA_CONTROL_TOKEN
```

The request is `MethodologyCompletionApprovalRequest@1.0`. It binds the exact
Task/Plan/inventory and versions, execution contract, repository/ref/commit,
source graph, activation definition, final production dispatch and Handoff,
both reviewer claims/dispatch receipts/Handoffs/Evidence, every active passed
Evidence item, and all seven final managed Artifact versions and hashes.

The formal `Approval@1.0` additionally carries real path/hash bindings from the
sealed migration proposal and scope-seed artifacts. Managed final outputs have
no invented repository path; their exact version/hash bindings remain in the
request and receipt.

## Authentication and atomic authority

The asserted `approved_by` must equal a currently authenticated principal with
`control_plane.approve` for the Task project. Repository identity and all
path-bound file hashes are observed again inside the write transaction. The
transaction then:

1. revalidates both settled reviewer provenance chains and provider ledgers;
2. requires the immutable final Stage inventory to derive
   `needs_review/all_stages_passed`;
3. registers exactly one active, artifact-bound formal Approval;
4. transitions only the authoritative frozen Task from `needs_review` to
   `completed` with cause `user_action`;
5. seals `AuthenticatedMethodologyCompletionApproval@1.0` and
   `MethodologyCompletionApprovalReceipt@1.0`; and
6. writes mirrored audit events.

Any mismatch or audit failure rolls back the Approval, Task transition,
receipt, and events together. Exact concurrent/repeated requests return the
same receipt only after rechecking current repository, Artifact, Evidence,
Gate, Stage, Approval, and completed-Task authority.

Human approval chronology is also authoritative: the production settlement
and both independent reviewer settlements must precede `approved_at`, which
must not follow `requested_at` or authentication. Reviewer claims, dispatches,
Runs, Handoffs, and Evidence identities are pairwise distinct, and every
reviewer Evidence hash is bound to that reviewer's exact Run.

## Bypass and mutation boundaries

Legacy `agora task approve`, direct generic Control Plane Task completion, and
generic formal-Approval registration are rejected for methodology-bound Tasks.
Only the atomic completion transaction may register the exact authenticated
Approval. The command starts no runtime, registers no Artifact or Evidence,
does not reevaluate a Gate, does not modify a Stage, does not substitute a
provider, and does not mutate native AWS AI-DLC files. Later path/hash
invalidation marks the formal Approval and Gate stale, reopens the final Stage,
and moves the Task out of `completed`; reads and replay then fail closed.
