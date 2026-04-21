# Contributing to Agora

Thanks for your interest. Agora is a young project and contributions are very welcome — bug reports, feature ideas, docs, and code.

## Getting set up

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd Agora
cp .env.example .env   # add at least one provider key
make install           # Python backend + Node frontend
make test              # run the full suite (unit + integration)
```

Prereqs: Python 3.10+, Node 18+, and Docker (optional, only needed for the sandbox and `docker compose up`).

## Before you open a PR

1. **Open an issue first** for anything larger than a small bug fix or typo — a quick chat saves rewrites later.
2. **Run the tests** — `make test` must pass. If you add behavior, add a test for it.
3. **Keep the diff focused** — one PR, one concern. Split unrelated changes into separate PRs.
4. **Match the style** — the repo uses standard tools (`ruff` for Python, `eslint` for TypeScript). The CI will flag anything off.

## Commit & PR format

- Commit subject: short, imperative, lowercase type prefix. Examples:
  - `feat: add retry to executor tool loop`
  - `fix: session_id missing on reset endpoint`
  - `docs: clarify Azure setup in README`
- PR title: same format as the commit subject.
- PR body: what changed, why, and how you tested it. Link the issue if there is one.

## Areas we'd love help with

- **Web UI polish** — the React front-end has room for UX refinements.
- **MCP server integration** — adding support for external MCP tool servers.
- **Skill examples** — contributing reusable skills to `skills/public/`.
- **Language support** — improving prompts and UI strings for non-English users.
- **Provider adapters** — new model providers beyond the current set.

## Questions

- 🐛 [GitHub Issues](https://github.com/wilbur-labs/Agora/issues) for bugs and feature requests.
- 💬 [GitHub Discussions](https://github.com/wilbur-labs/Agora/discussions) for ideas and questions.
- 📧 wilbur.ai.dev@gmail.com for anything else.

By contributing, you agree your work will be released under the project's [MIT License](LICENSE).
