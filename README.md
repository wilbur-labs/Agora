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

🎬 **Full Demo**

[Watch the full demo video](https://youtu.be/placeholder)

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
- [ ] MCP server extensions
- [ ] Skill marketplace

## Philosophy

In ancient Athens, the Agora was where people gathered to discuss, debate, and decide. Agora brings this idea to AI: not one model doing everything, but multiple perspectives collaborating before taking action.

## License

MIT

## Acknowledgments

- [DeerFlow](https://github.com/bytedance/deer-flow) — inspiration for sandbox execution, memory systems, and orchestration
- [Hermes Agent](https://github.com/hermes-agent) — inspiration for self-improving skills and persistent memory

## Contact

- 📧 wilbur.ai.dev@gmail.com
- 🐛 [GitHub Issues](https://github.com/wilbur-labs/Agora/issues)
