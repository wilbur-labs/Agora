# Legacy council retirement v1

Status: implementation contract

## Product decision

Agora 1.0 is a Task delivery control plane, not an autonomous AI council.
Codex, Claude Code, and Kiro may perform different routed Stages and independent
reviews, but they do not hold an open-ended conversation with one another or
manufacture a consensus that advances authoritative workflow state.

Runtime choice is separate from Council behavior. Kiro remains a supported
runtime, and the architecture must retain a bounded path for future Task/Stage
role assignment to choose among Codex, Claude Code, Kiro, and explicitly
configured local runtimes. Adding freely configurable roles or local-model
adapters is deferred until the authoritative end-to-end delivery mainline is
running; this retirement increment neither implements that expansion nor
removes the runtime boundary needed by it.

The only advisory path is an explicit, user-triggered Task consultation. It
dispatches the runtime already pinned to the authoritative Stage route, returns
one bounded `ConsultationCandidateDraft@1.0`, and remains non-authoritative
until a human explicitly adopts or rejects it. Consultation cannot mutate
Task, Stage, Gate, formal Run, Artifact, Evidence, Approval, or completion
state.

## Retired 0.5 surfaces

The following 0.5 surfaces must not construct a Council, call a model provider,
run tools, learn from a discussion, or write discussion/session state:

- the default interactive `agora` CLI and its QUICK / DISCUSS / EXECUTE router;
- `/api/chat` and every `/api/chat/*` endpoint;
- the mutable `/api/agents` surface;
- chat-only `/api/sessions`, `/api/shared`, `/api/skills`, `/api/memory`, and
  `/api/profile` surfaces;
- the chat-only, caller-path `/api/artifacts` preview/download surface;
- the `/chat`, `/agents`, `/skills`, `/settings`, and `/shared` pages;
- public product copy that presents Scout / Architect / Critic / Synthesizer
  debate as the current Agora workflow.

HTTP retirement is fail closed. Retired API paths return `410 Gone` with the
stable code `legacy_council_retired` and do not initialize legacy state. Retired
browser paths redirect permanently to `/control-plane`. Unknown `/api/*` GET
paths return `404`, never the frontend fallback document or a successful null
response.

The `agora` executable accepts only the versioned `agora task ...` command
family. Bare or help invocation prints migration guidance without importing
legacy Council modules; any other top-level command fails with exit code 2.

## Compatibility and data boundary

This increment disconnects the active product surfaces. It does not rewrite or
delete existing user databases, learned files, native runtime configuration,
or native Codex / Claude / Kiro files. Historical chat data remains inert on
disk and is not interpreted as Task state, Evidence, Approval, or memory.

Council retirement must not remove or disable Control Plane execution adapters,
orchestration runtime definitions, runtime capability/preflight observation,
usage normalizers, workspaces, credentials, or native-runtime configuration.
In particular, temporary unavailability or substitution of a development
review tool does not remove that runtime from Agora's product architecture.

The now-unreachable 0.5 Council implementation is eligible for a separate,
reference-audited cleanup only after the authoritative end-to-end mainline is
running. That cleanup must distinguish Council-only model/provider code from
active and future runtime adapters. Git history remains the recovery path; the
1.0 runtime must not carry a compatibility switch that can silently reactivate
autonomous discussion.

## Acceptance checks

- Importing the FastAPI application does not import `agora.agents.council` or
  initialize Council/provider state.
- Every retired API prefix returns the same `410` code and stable detail.
- Every retired UI path redirects to `/control-plane`.
- `agora --help` and bare `agora` describe the Task control plane; unsupported
  legacy commands fail without provider activity.
- `/health`, authenticated Control Plane routes, execution operations, and the
  static `/control-plane` export remain available.
- The landing page and primary navigation describe the authoritative
  Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate workflow and do
  not advertise autonomous debate.
