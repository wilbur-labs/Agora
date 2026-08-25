# Proposal: Kandev-style live terminal + switchable sessions in the Task Workspace

Status: DRAFT for review (do not implement yet)
Author: workspace iteration
Date: 2026-08-25

## 1. What we are borrowing from Kandev (and what we are NOT)

Kandev strengths the user called out:

1. **Switchable per-task chat/session** — click a task, the whole working
   context switches; the previous one keeps its state.
2. **Live run status** — you can see, at a glance and in real time, whether the
   agent is running, waiting, or done.
3. **The terminal is shown directly on the page** — the agent's raw terminal
   output streams live, not a post-hoc summary.

We are NOT adopting: arbitrary layout systems, Kandev's component code (AGPL-3.0
— reimplement on Agora's model), Docker/SSH remote execution, or a generic
multi-agent fleet UI. Those stay frozen.

Agora's differentiator must survive the merge: opening a task still answers
*goal / trustworthy completion / deviation / next step*, and the durable
**timeline** stays the record of what happened. The terminal is a **live lens on
the current run**, not a replacement for the timeline.

## 2. Where Agora is today (the gap)

- Runs are subprocesses (`execution/dispatcher.py`) spawning `codex` / `claude` /
  `kiro` CLIs, or the codex app-server bridge. Output is captured as a **static
  `stdout_tail` / `stderr_tail`** on the `ExecutionRun` and only becomes visible
  after the run reaches a terminal state (or on the next poll).
- The workspace shows that tail in a run card **after the fact**. There is no
  incremental/streaming output and no live run-state push.
- Run state is read by polling `GET /api/runs?...` / `GET /api/runs/{id}`.
- Adapter capability already distinguishes `attention_mode`:
  `bidirectional` (codex app-server) vs `capture_only` (claude CLI). This gates
  whether a human can send input *into* a running agent.
- Frontend is a **statically exported** Next.js app served by the FastAPI
  backend (same origin). The backend CAN serve SSE/WebSocket; the frontend
  cannot run server code but can open an EventSource/WebSocket to the backend.
- Platform is **Windows**; we have already hit `FileNotFoundError` launching the
  app-server, so PTY/stream handling must be Windows-first.

## 3. Design principles

1. **Terminal is a lens, timeline is the record.** The live terminal streams the
   active run; when the run ends, its captured output is folded into the existing
   run card in the timeline. No duplicate source of truth.
2. **Switching is cheap and stateful.** Selecting a task (re)subscribes to that
   task's active run stream; leaving it unsubscribes. Per-task view state
   (scroll, which tab) is preserved (already keyed by task_id).
3. **Read-only by default; interactive only where the adapter supports it.**
   Sending keystrokes to a live agent is allowed ONLY for
   `attention_mode = bidirectional`. For `capture_only` agents the terminal is
   read-only and course-correction stays asynchronous (Agora's message/attention).
4. **Incremental, resumable, bounded.** The stream is cursor-based so a reconnect
   resumes without gaps or replaying everything; server memory per run is capped.
5. **Fits the frozen scope.** Ship in phases; each phase is independently useful
   and reviewable.

## 4. Proposed architecture

### 4.1 Backend — live output capture

- The dispatcher already reads child stdout/stderr to build the tail. Change it
  to append each chunk to an **in-memory per-run ring buffer** (bounded, e.g.
  256–512 KB) keyed by `run_id`, alongside the existing tail persistence.
- Each appended chunk gets a monotonically increasing **byte offset / sequence
  cursor**, so consumers can request "everything after cursor N".
- On run completion the ring buffer is flushed; the persisted `stdout_tail`
  remains the durable record for the timeline. The live buffer is evicted after
  a short grace period.
- Concurrency: capture runs in the existing reader path (thread or asyncio task);
  the ring buffer is guarded so the SSE endpoint can read while the reader writes.

### 4.2 Backend — streaming endpoints

- `GET /api/runs/{run_id}/stream?cursor=N` → **SSE** (`text/event-stream`).
  Emits `output` events (`{cursor, chunk}`) from the ring buffer starting at N,
  then live chunks as they arrive; emits `state` events on run-state changes;
  emits `done` and closes when the run reaches a terminal state.
  - SSE (not WebSocket) because output is server→client only for read-only mode,
    it survives proxies, and reconnect/`Last-Event-ID` is built in.
- `GET /api/tasks/{task_id}/active-run` → the current running/queued run id (if
  any) so the workspace knows what to subscribe to when a task is selected.
- Interactive input (Phase 3, bidirectional adapters only):
  `POST /api/runs/{run_id}/input {text}` → writes to the child's stdin / the
  app-server session. Rejected (409) for `capture_only` adapters.

### 4.3 Frontend — terminal in the workspace

- Add a **Terminal view** in the workspace center column, as a mode toggle
  alongside the Timeline (e.g. "Timeline | Terminal"), NOT a separate page —
  switching tasks keeps switching the whole workspace.
- Render with a bundled terminal emulator (**xterm.js**, bundled into the app —
  the frontend is served same-origin by the backend, so no artifact-style CSP
  limit applies). A minimal `<pre>` + ANSI-to-HTML fallback is the low-risk
  option if xterm.js proves heavy; decide during Phase 2.
- On task select: call `active-run`; if a run is live, open an `EventSource` to
  `/stream`; append chunks; show a **live run-status pill** (queued / running /
  waiting / done) driven by the `state` events. On task switch, close the
  EventSource.
- When the run ends, stop streaming and let the timeline's run card (existing)
  show the final captured output — the terminal view can also just show the
  last buffer.
- Live status also updates the TaskRail dot (running → blue, pulsing) so the
  switchable list reflects real-time state — this is the Kandev "see run status
  while switching" behavior.

### 4.4 Windows / PTY

- Phase 1–2 can stream the plain piped stdout/stderr we already capture (no PTY)
  — simplest and avoids ANSI/ConPTY complexity. Many CLIs still emit useful
  progress this way.
- If a true TTY is needed (color, progress bars, interactive prompts), use
  **ConPTY** via `pywinpty`/`winpty` on Windows. This is a Phase 3 concern tied
  to interactivity; keep it out of Phases 1–2.
- Keep the existing `resolve_runtime_command` (PATHEXT/`.cmd`) path; the
  app-server `FileNotFoundError` is an existing launcher issue, orthogonal to
  streaming, but streaming must fail gracefully when the child never starts.

### 4.5 Security & privacy

- The `/api/runs/*` endpoints are currently **unauthenticated** (non-control-
  plane). Live output can contain secrets/paths. Options: (a) reuse the existing
  redaction that already scrubs the persisted tail, applied to live chunks too;
  (b) gate the stream behind the same bearer as the control-plane once the
  1.3 auth pass lands. At minimum, apply the existing redaction to live chunks.
- Cap concurrent SSE connections and per-run buffer size to avoid resource
  exhaustion; drop the oldest bytes past the cap (the timeline tail is still the
  record).

## 5. Data model impact

- **Phase 1–2: none.** The ring buffer is in-memory; the durable record is the
  existing `stdout_tail`. No schema change.
- **Phase 3 (optional):** if we want full (non-truncated) run logs replayable
  after restart, add a `run_output` append log (run_id, cursor, chunk) — but only
  if the tail proves insufficient.

## 6. Phasing (each independently shippable, review-gated)

- **P0 (cheap, near-live):** while a run is `running`, the workspace polls
  `GET /api/runs/{id}` every ~2s and re-renders the tail. No backend change.
  Delivers "see it progress" immediately; validates the UX before investing in
  streaming.
- **P1 (streaming read-only):** ring buffer + SSE `/stream` + `active-run`;
  terminal view in the workspace; live status pill + TaskRail live dot.
  Read-only for all adapters.
- **P2 (real terminal fidelity):** xterm.js rendering, ANSI handling; decide
  PTY-vs-pipe.
- **P3 (interactive):** stdin/input for `bidirectional` adapters only; ConPTY on
  Windows if a TTY is required; keep async message/attention for `capture_only`.

## 7. How this fits Agora's identity

- The workspace still leads with the **project strip + outcome summary** (goal /
  verified completion / next step). The terminal is an additional lens, not the
  headline.
- The **timeline** remains the durable, human-readable record; the live terminal
  is the real-time view of the one active run.
- Kandev gives you "switch fast + watch it run"; Agora adds "…and always know
  the goal, whether the result is trustworthy, and what to decide next."

## 8. Open questions for review

1. SSE vs WebSocket given a future need for bidirectional input — is a single
   WebSocket per active run simpler than SSE + a POST input channel?
2. Is streaming plain piped stdout (no PTY) good enough for codex/claude/kiro
   CLIs on Windows, or is ConPTY required earlier than Phase 3?
3. Ring-buffer location: in the FastAPI process memory — does the dispatcher run
   in the same process/loop, or a worker, i.e. can the SSE endpoint read the same
   buffer? (If runs are spawned in a separate process, we need IPC or a shared
   store.)
4. Redaction on live chunks: reuse the tail redaction as-is, or is line-buffering
   needed so secrets split across chunks are still caught?
5. Auth: ship the stream unauthenticated (matching current run endpoints) or
   block on the 1.3 auth pass?
6. Does adding a Terminal mode dilute the "one clear next step" focus? Should the
   terminal be a collapsed panel under the timeline rather than a co-equal tab?

## 9. Exit criteria for the merged feature

- Switching tasks shows the selected task's live run status within ~1s.
- A running task streams its output to the page as it happens (P1+).
- Switching away and back resumes the stream without gaps or full replay.
- No regression to the goal / verified-completion / next-step framing.
- Read-only for capture_only adapters; input only where bidirectional.
