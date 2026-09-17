# 0009 — Remote execution is chosen per adapter, on its own path, with the review on the same host

- **Status**: accepted (2026-09-17)
- **Supersedes**: —
- **Related**: W3-a (`docs/W3a-remote-execution.md` in the Agora repo), CR-2 (environment layer, deferred), CR-89, CR-90

## Context

By 2026-09-17 the SSH remote-execution line (U1 `env-launch`, U2 `remote-env`, U3 `remote-worktree`, U4 `remote-deliverables`, U5's config schema, TOFU host-key pinning, a real-host round trip) was on the branch, written and verified from the office machine. The dispatcher, however, referenced none of it: `agora/execution/dispatcher.py` still spawned every run with `asyncio.create_subprocess_exec` on the machine Agora runs on, checked readiness with the local `is_ready_worktree`, and snapshotted with the local `snapshot_changes`. A remote adapter's run would have been launched locally in a path that does not exist here. The person asked for the two to be joined: "把 ssh 部分结合起来，那个公司已经跑通了".

Three questions had to be answered to join them.

## Decision

1. **Where a run executes is decided by its adapter's configuration, not by the project.** An adapter with `bridge_mode: ssh` carries a `RemoteAdapterConfig`; every run of that adapter — worker, review, question — executes on that host, in that adapter's `remote_workspace`, under that adapter's `remote_project_root`. `agora/execution/remote_wiring.py` is the one module that turns the config into the remote environment, provisioner and deliverables reader, and `is_remote(adapter)` is the one predicate everything else asks.

2. **The remote run is its own path in the dispatcher (`_execute_remote`), not branches threaded through the local one.** It asks the same questions in the same order — worktree ready, baseline, launch, stream, wait, stop, snapshot, settle — of the host. `_stop` accepts a `ProcessHandle` alongside a raw subprocess, which is the one seam the two paths share (and what the U2 smoke test was waiting for).

3. **A cross-review must run on the worker's host.** A review reads the worker's worktree; a local reviewer cannot see a remote one and vice versa. When the project's reviewer adapter is not on the same account of the same host as the worker adapter, the review is not silently substituted or skipped: the task is blocked with an attention item naming both places, through the same `_block_review_owed` path a missing reviewer uses.

4. **Remote paths come from the adapter config strings, never from the registry's resolved paths.** `ProjectRegistry._resolve_path` resolves every project path on the local machine (and creates `.agora/` under the root); on Windows a POSIX root becomes a path on the current drive. The dispatcher's remote path therefore reads `remote_workspace` / `remote_project_root` as written. The remote *provisioner* still reads the registry (CR-89) — that is recorded as a deferred defect, not papered over.

## Consequences

- Runs of a remote adapter execute remotely with no change to the control plane's model: a run still belongs to a task, still has a worktree path (now a POSIX path on the host), still snapshots deliverables and still gets reviewed. The UI shows `user@host` beside the executor wherever the executor is named.
- The workspaces router's status/provision and the execution router's three deliverables endpoints branch to the remote collaborators for remote adapters, each on a fresh environment per request (the remote environment holds one connection on one loop — CR-4).
- A run abandoned by a dispatcher stop records its delivery as "could not establish" for a remote run: the cancel path snapshots the local path named by the run, which for a remote run is not a worktree. Said in `_execute_remote`'s docstring and left as is.
- Verification from this machine stops at the fakes (`tests/test_remote_wiring.py`): there is no reachable SSH host from the home machine. The real-host round trip is the office machine's integration test.

## Alternatives

- **An environment layer keyed per project (CR-2, the mockup's "environment" tab).** Rejected for now: the adapter config already binds an adapter to a host, and the layer would be a second place to say the same thing until a project genuinely runs one adapter on two hosts. Deferred, not refused.
- **Thread `Environment.launch` through the existing local `execute`, replacing `create_subprocess_exec`.** Rejected: the local path is built on job objects, `taskkill`, `probe_path` and synchronous git helpers, each of which would need a branch, and the U1 design note already warned that rewriting that band twice turns the merge into a rewrite.
- **Let a local reviewer review a remote worker by reading the diff over SSH into the prompt.** Rejected: the prompt already carries file contents, but the review *run* itself executes in the reviewer's worktree and would be reviewing work it cannot see or test. Same-host is the condition under which the review means what it says.
- **Skip the review for remote runs.** Rejected outright: an unavailable review that ends as "this work needs no review" is the failure `_dispatch_review` exists to prevent.
