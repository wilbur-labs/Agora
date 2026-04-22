# Changelog

All notable changes to Agora are documented here. This project follows [Semantic Versioning](https://semver.org/).

## [0.4.2] — 2026-04-22

### Fixed
- Broken Hermes Agent URL in English, Chinese, and Japanese READMEs. The previous link (`https://github.com/hermes-agent`) resolved to an empty GitHub user profile. Corrected to the real repository (`https://github.com/NousResearch/hermes-agent`) and added "Nous Research" as the author qualifier.

## [0.4.1] — 2026-04-21

### Fixed
- **Council sequential execution regression** — the `_parse_action_items` threshold was raised in 0.4.0, causing 2–5 item bullet lists to be lumped into a single executor call instead of running per-item. Restored the original behavior so any bulleted task list triggers sequential execution. Fixes 6 red tests in `tests/test_council.py`.
- **Makefile portability** — use `python3` / `pip3` so `make install`, `make cli`, and `make test` work on stock Ubuntu/Debian without a `python` symlink.
- **Docker port alignment** — `docker-compose.yaml` now maps host `8000:8000`, matching the port used in README examples, the frontend landing-page hint, and the dev-mode API client.
- **Version drift** — `backend/pyproject.toml` and `frontend/package.json` now reflect the tagged release version (were stuck at 0.1.0).

### Added
- `CHANGELOG.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1) for public-launch community hygiene.
- Concise "Agora vs. other tools" comparison table in the README, with a short paragraph crediting DeerFlow and Hermes Agent as the ideas Agora builds on.

### Changed
- `.env.example` rewritten with `CLAUDE_API_KEY` as the recommended default (matches `config.yaml`'s `executor_model: claude`) and providers reordered by typical use.
- `.gitignore` now excludes `nohup.out` and `*.log`.

### Removed
- Placeholder YouTube demo link from README (will be restored when a real recording is available).

## [0.4.0] — 2026-04

### Added
- **Anthropic Claude support** — native tool use via the Messages API. Claude is now the default executor in `config.yaml` for its strong tool-calling quality.
- `.dockerignore` for faster and cleaner Docker builds.
- Consolidated, rewritten README in English, Chinese, and Japanese with a live demo GIF.

### Changed
- **Executor resilience** — retry, timeout, and forced tool-usage handling for more reliable multi-step execution.
- README restructured; removed per-directory `AGENTS.md` / `CLAUDE.md` / `README.md` duplicates under `frontend/`.

### Fixed
- `api.ts` now passes `session_id` to `/reset` and `/restore` endpoints.

## [0.3.0]

### Added
- **Smart routing** (W6) — Moderator classifies each request as `QUICK`, `DISCUSS`, `EXECUTE`, or `CLARIFY` and picks the right path.
- **Deep search mode** (W10) — Scout performs multi-hop research when the task demands it.
- **Multi-session support** (W9) — multiple parallel sessions with isolated context, switchable from the UI.
- **Parallel discussion** (W8) — Scout, Architect, and Critic can run concurrently for faster turnaround.
- **Artifacts system** — file tracking, tree view, preview, and download of executor output.
- **Workspace path isolation** (W12) — file operations are confined to the task workspace.
- **Scout web search** integration.
- **Auto-approve** option for trusted workflows.
- Phase 2 test suite (32 new tests).

### Changed
- Default discussion model switched back to Azure `gpt-4o` after evaluating cost vs. quality.
- Multiple UI and language-consistency improvements across EN/CN/JP.

## [0.2.0]

### Added
- **Sequential executor** for multi-item action lists.
- **Configurable executor workspace** — files are created under `~/agora-workspace` by default.
- I1–I11 improvement batch: streaming, UI, bug fixes, and test expansions.

### Fixed
- Docker build failing when `skills/` directory is missing on a fresh clone.

## [0.1.0]

Initial public release.

### Added
- **Full Web UI** (Next.js + shadcn/ui).
- **Multi-agent council** — Moderator, Scout, Architect, Critic, Sentinel, Synthesizer, Executor.
- **Tool-calling execution** — `read_file`, `write_file`, `patch_file`, `list_dir`, `shell`.
- **Self-learning skill system** — SQLite store with embedding-based matching and three-tier fallback (embedding → LLM → keyword).
- **Docker sandbox** for isolated execution.
- **Multi-provider support** — OpenAI, Azure OpenAI, Claude CLI, Gemini CLI, Kiro CLI, and OpenAI-compatible APIs.
- **Multi-language** system prompts and language-consistency tests (EN/CN/JP) using LLM-as-Judge.
- MIT LICENSE.
