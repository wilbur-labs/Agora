# Phase 5: Git Worktree Workspace Provisioning

Status: implemented and independently reviewed
Specification owner: Kiro CLI
Implementation owner: Codex
Independent reviewer: Claude Code
Date: 2026-07-14

## Goal

Explicitly provision registered Codex, Claude, and Kiro workspaces as linked Git
worktrees so parallel agents operate on real project code without sharing a checkout.

## Invariants

1. Provisioning is an explicit API/user action and never occurs while queueing a run.
2. The API accepts only project and adapter identifiers; the registry owns the target path.
3. Targets must be inside the project root or an explicit execution workspace allowlist.
4. A non-empty unmanaged directory is never overwritten, removed, reset, or cleaned.
5. Git commands use argv without a shell and have bounded timeouts and sanitized errors.
6. Provision is idempotent; concurrent requests for one workspace serialize in-process.
7. Existing branches are reused only when not checked out in another registered worktree.
8. No operation removes worktrees, deletes branches, resets files, or automatically prunes Git metadata.

## API

- `GET /api/workspaces/{project_id}`
- `GET /api/workspaces/{project_id}/{adapter}`
- `POST /api/workspaces/provision`

## Create-project provisioning (2026-08-22)

`POST /api/projects` provisions the new project's adapter workspaces as linked
worktrees at creation — the explicit action of Invariant 1. It first calls
`WorkspaceProvisioner.ensure_source_repo`, which makes the project root its own
Git repository *only when it is its own `--show-toplevel`*: a root nested inside
an outer checkout (e.g. `<agora>/projects/<id>`) is `git init`ed so worktrees are
never sourced from the outer repository. It also guarantees a HEAD (seeding an
unborn one) and ignores `.agora/`. A pre-existing non-empty project directory is
rejected before any Git operation (Invariant 4). Per-workspace provisioning is
best-effort and reported in the response; a failure never rolls back the created
project. `source_is_git` requires the project root's own top-level, so the
provisioner never `worktree add`s against an ancestor repository.

Deliverable discovery, file content/raw serving, and handoff commit all fail
closed unless the run's workspace is a linked worktree whose own top-level is
exactly the workspace (a bare repo root — including a project root or the outer
Agora checkout — is rejected), so a run can never list, read, or commit the
surrounding repository's files.

## Deferred

Worktree removal, update/rebase/pull, branch merge/push, cross-process distributed
locking, stale metadata pruning, and task-specific worktrees are out of scope.
Workspace paths are resolved and checked again immediately before filesystem mutation;
OS-level directory-handle confinement for the remaining local symlink swap window is deferred.
