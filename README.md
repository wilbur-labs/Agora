# 🏛 Agora

**English** | [中文](README_zh.md) | [日本語](README_ja.md)

**Multi-agent AI that discusses, decides, and executes your tasks.**

![Agora Demo](./docs/Demo1.gif)

Open-source AI system where multiple agents debate your problem from different perspectives, then actually build the solution.

## Why Agora?

- 🏛 **Council, not chatbot** — Multiple agents discuss your problem from different angles before acting.
- 🔧 **Discussion → Execution** — Agents don't stop at advice. They can write files, run commands, and implement the plan.
- 🧠 **Self-improving** — Discussions and executions are distilled into reusable skills over time.
- ⚙️ **Customizable** — Define your own agents, prompts, and models in YAML.
- 🔌 **Model agnostic** — OpenAI, Azure OpenAI, Claude CLI, Gemini CLI, Kiro CLI, and OpenAI-compatible APIs.
- 🐳 **Self-hosted** — Run it with Docker and keep control of your data.
- 🚦 **Delivery control plane** — Requirements, projects, isolated workspaces, runs, attention requests, and cross-project workflow DAGs for Codex, Claude Code, and Kiro CLI.

## Agora vs. other tools

|                                         | Agora | ChatGPT | AutoGPT | LangChain |
| --------------------------------------- | :---: | :-----: | :-----: | :-------: |
| Multi-agent debate before action        |   ✅   |    ❌    |    ❌    |    DIY    |
| Learns from discussion disagreements    |   ✅   |    ❌    |    ❌    |     ❌     |
| Execution skills learned from use       |   ✅   |    ❌    |   ⚠️    |     ❌     |
| Human-in-the-loop approval              |   ✅   |    ❌    |   ⚠️    |    DIY    |
| Self-hosted & open source               |   ✅   |    ❌    |    ✅    |     ✅     |

Agora builds on ideas from [DeerFlow](https://github.com/bytedance/deer-flow) (sandbox + memory) and [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (self-improving skills). Its original contribution is the **council discussion** model, which lets skills be learned not only from execution but from how multiple agents disagree and resolve.

## What it looks like

```text
You: "Add caching to my Go service (QPS ~5000)"

scout       → compares Redis vs Memcached
architect   → designs L1 + L2 cache strategy
critic      → points out consistency risks
synthesizer → generates action items

Execute? → yes

executor → writes code and runs commands
```

## Quick Start

### Docker

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd Agora
cp .env.example .env  # edit .env and add your API key
docker compose up -d
```

### Local

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd Agora
cp .env.example .env  # edit .env and add your API key
make install
make dev
```

## Configuration Example

```yaml
models:
  gpt4o:
    provider: azure-openai
    api_key: ${AZURE_OPENAI_API_KEY}
    base_url: ${AZURE_OPENAI_BASE_URL}
    deployment: gpt-4o-0513

council:
  default_agents: [scout, architect, critic]
  model: gpt4o
  executor_model: gpt4o
  concurrent: false
```

## Runtime Modes and API Requirements

Agora currently has two main runtime modes:

| Mode | Purpose | Invocation | Does Agora need model API credentials? |
|------|---------|------------|----------------------------------------|
| Chat / Council | Web UI chat, `/api/chat`, multi-agent discussion, QUICK / DISCUSS / EXECUTE routing | Agora calls the configured model providers directly | Yes. Configure `CLAUDE_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_BASE_URL`, or the provider credentials selected in `config.yaml` |
| Research Dispatch Worker | Background research tasks routed to `claude-research`, `codex-engineering`, and `kiro-spec` | Agora launches local CLIs: `claude`, `codex`, and `kiro-cli` | Not directly for Agora. The CLIs must be installed, available on `PATH`, and authenticated through their own login or environment setup |

Natural-language research dispatch is the default user experience. In the CLI, ask the question directly:

```text
Agora> 比较生产级 RAG 系统可用的开源 reranker，并推荐一个。
```

Agora classifies research-like prompts, selects the appropriate workers, dispatches them, and writes artifacts under `.agora/research/<task-id>/`. The explicit `/research` command remains available for advanced usage:

```text
/research <question>              # dispatch workers
/research --plan-only <question>  # create artifacts without dispatch
/research --worker codex-engineering <question>  # debug one worker
```

Current research worker configuration example:

```yaml
research:
  dispatch:
    enabled: true
  workers:
    claude-research:
      command: claude
      args: ["-p", "{prompt}", "--output-format", "text"]
    codex-engineering:
      command: codex
      args: ["exec", "--skip-git-repo-check"]
    kiro-spec:
      command: kiro-cli
      args: ["chat", "--no-interactive", "--trust-all-tools"]
```

In short: Agora own chat, council, and agent execution flows need model API credentials. Research dispatch only starts Claude Code, Codex, and Kiro CLI workers, so authentication is handled by those CLIs.

## How It Works

```
User Input
  → Moderator routes: QUICK / DISCUSS / EXECUTE / CLARIFY
    → QUICK: Single agent answers directly
    → DISCUSS:
        Scout → Architect → Critic → Synthesizer
        → User confirms action items
        → Executor runs tool-calling loop
        → Learn discussion + execution skills
    → EXECUTE:
        → Executor runs tool-calling loop directly
        → Learn execution skill
```

## Council Agents

| Agent | Role |
|-------|------|
| Moderator | Routes requests |
| Scout | Research and evidence gathering |
| Architect | System design and solution planning |
| Critic | Review and challenge assumptions |
| Sentinel | Security review |
| Synthesizer | Summarizes decisions and action items |
| Executor | Executes with tools |

## Built-in Tools

| Tool | Description |
|------|-------------|
| read_file | Read file contents |
| write_file | Create or overwrite files |
| patch_file | Update specific file content |
| list_dir | List directory contents |
| shell | Execute shell commands |

## Self-Learning

Agora learns from every interaction:

- **Discussion skills** — captures decision patterns and useful perspectives
- **Execution skills** — captures step-by-step implementation knowledge
- **Memory** — stores reusable user and project context
- **Success tracking** — records what works and what fails

## CLI Commands

| Command | Description |
|---------|-------------|
| `/ask <question>` | Quick answer |
| `/exec <task>` | Direct execution |
| `/agents` | List council agents |
| `/skills` | List learned skills |
| `/memory` | View memory |
| `/profile` | View/set user profile |
| `/reset` | Clear conversation context |
| `/quit` | Exit |

## API

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Design a CI/CD pipeline for my Go project"}'
```

## Testing

```bash
make test
make test-all
```

## Roadmap

- [x] Multi-agent discussion
- [x] Tool-calling execution
- [x] Self-learning skills
- [x] Docker sandbox
- [x] Multiple model backends
- [x] Web UI
- [x] Human-in-the-loop confirmation
- [x] Multi-project delivery control plane
- [x] Cross-project workflow DAGs and opt-in supervision
- [x] Codex, Claude Code, and Kiro CLI execution routing
- [ ] MCP server extensions
- [ ] Skill marketplace

## Philosophy

In ancient Athens, the Agora was where people gathered to discuss, debate, and decide. Agora brings this idea to AI: not one model doing everything, but multiple perspectives collaborating before taking action.

## License

MIT

## Acknowledgments

- [DeerFlow](https://github.com/bytedance/deer-flow) — inspiration for sandbox execution, memory systems, and orchestration
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research — inspiration for self-improving skills and persistent memory

## Contact

- 📧 wilbur.ai.dev@gmail.com
- 🐛 [GitHub Issues](https://github.com/wilbur-labs/Agora/issues)
