# 🏛 Agora

**English** | [中文](README_zh.md) | [日本語](README_ja.md)

**Multi-perspective AI council — discuss, design, execute, evolve.**

Agora is a **full-stack AI agent platform** where multiple AI advisors discuss your ideas from different perspectives, then execute the plan — and learn from every interaction.

You speak once. Multiple AI perspectives respond. Then they get to work.

```
You: "I want to add a caching layer to my Go project, QPS ~5000"

◆ scout (Researcher)
  Redis vs Memcached vs local cache comparison...
  At 5000 QPS + 2GB data, all three can handle it...

◆ architect (System Designer)
  Recommending two-tier: ristretto (L1) + Redis (L2)...
  Architecture: App → Local Cache → Redis → DB

◆ critic (Quality Reviewer)
  Cache consistency between L1 and L2 not addressed.
  Suggest adding pub/sub invalidation...

◆ synthesizer (Discussion Synthesizer)
  ## Action Items
  - [ ] Add ristretto and go-redis dependencies
  - [ ] Implement CacheManager interface
  - [ ] Configure Redis connection pool

Execute action items? [y/n] → y

◆ executor (Task Executor)
  🔧 shell(go get github.com/dgraph-io/ristretto)
  → Success
  🔧 write_file(internal/cache/manager.go)
  → Wrote CacheManager implementation
  ✅ Done

🧠 Learned skill: go_cache_layer_setup
```

## What Makes Agora Different

- 🏛 **Council, not chatbot** — Multiple AI agents discuss your problem from different angles (research, architecture, critique, security), then synthesize a unified conclusion.
- 🔧 **Discussion → Execution** — Agents don't just advise. After discussion, the executor calls real tools (file I/O, shell commands) to implement the plan.
- 🧠 **Self-improving** — Every discussion and execution is distilled into reusable skills. Agents get better at your specific workflows over time.
- ⚙️ **Fully customizable** — Create your own agents with custom prompts. Assign different LLM providers per agent. Configure via YAML, not code.
- 🔌 **Model agnostic** — Azure OpenAI, OpenAI, Kiro CLI, Claude CLI, Gemini CLI, or any OpenAI-compatible API. Mix and match per agent.
- 🐳 **Self-hosted** — One `docker compose up` and you own the whole stack. No vendor lock-in, no data leaving your network.

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd agora
cp .env.example .env
# Edit .env — add your API key
docker compose up -d    # Start API server
```

### Option 2: Local

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd agora
cp .env.example .env
# Edit .env — add your API key
make install
make cli      # Interactive CLI
# or
make dev      # API server at http://localhost:8000
```

### Configuration

Edit `config.yaml`:

```yaml
models:
  gpt4o:
    provider: azure-openai          # or openai-api
    api_key: ${AZURE_OPENAI_API_KEY}
    base_url: ${AZURE_OPENAI_BASE_URL}
    deployment: gpt-4o-0513

council:
  default_agents: [scout, architect, critic]
  model: gpt4o           # Discussion model
  executor_model: gpt4o  # Execution model (supports function calling)
  concurrent: false       # Parallel agent discussion
```

Supported model providers:
- **Azure OpenAI** — `azure-openai` (recommended)
- **OpenAI** — `openai-api`
- **Claude Code CLI** — `claude-cli`
- **Gemini CLI** — `gemini-cli`
- **Kiro CLI** — `kiro-cli`
- Any **OpenAI-compatible API** (DeepSeek, vLLM, OpenRouter, etc.)

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

### Council Agents

| Agent | Role | When |
|-------|------|------|
| **Moderator** | Routes requests | Always first |
| **Scout** | Research & evidence | Discussion |
| **Architect** | Design & solutions | Discussion |
| **Critic** | Review & challenge | Discussion |
| **Sentinel** | Security review | Optional |
| **Synthesizer** | Distill conclusions | End of discussion |
| **Executor** | Execute with tools | Execution phase |

### Built-in Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Create or overwrite files |
| `patch_file` | Replace specific strings in files |
| `list_dir` | List directory contents |
| `shell` | Execute shell commands (with timeout) |

### Self-Learning

Agora learns from every interaction:

- **Discussion skills** — Decision patterns, what each perspective found
- **Execution skills** — Step-by-step procedures, lessons learned
- **Success tracking** — Each skill tracks success/failure counts
- **Memory** — User preferences, project context (MEMORY.md / USER.md)

Skills are injected into future conversations, so agents improve over time.

## CLI Commands

| Command | Description |
|---------|-------------|
| `/ask <question>` | Quick answer (skip discussion) |
| `/exec <task>` | Direct execution (skip discussion) |
| `/agents` | List council agents |
| `/skills` | List learned skills |
| `/memory` | View persistent memory |
| `/profile` | View/set user profile |
| `/reset` | Clear conversation context |
| `/quit` | Exit |

## API

```bash
# SSE streaming chat
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Design a CI/CD pipeline for my Go project"}'

# Synchronous chat
curl -X POST http://localhost:8000/api/chat/sync \
  -H "Content-Type: application/json" \
  -d '{"message": "What is Python GIL?"}'

# List agents
curl http://localhost:8000/api/agents

# Health check
curl http://localhost:8000/health
```

## Docker Sandbox

Enable isolated command execution:

```yaml
# config.yaml
sandbox:
  enabled: true
  image: python:3.12-slim
  timeout: 120
  memory_limit: 512m
```

When enabled, `shell` commands run inside ephemeral Docker containers that are automatically removed after execution.

## Testing

```bash
make test          # Unit tests only (70 tests, ~1s, no API calls)
make test-all      # Unit + integration tests (79 tests, uses LLM-as-Judge)
```

Integration tests use **LLM-as-Judge** — GPT evaluates whether agent outputs match the user's question, fit the agent's role, and meet quality standards.

## Project Structure

```
Agora/
├── .env.example          # Environment template
├── config.yaml           # Main configuration
├── Dockerfile
├── docker-compose.yaml
├── Makefile
├── skills/               # Learned and custom skills
│   ├── public/
│   ├── learned/
│   └── custom/
└── backend/
    ├── agora/
    │   ├── agents/       # Agent definitions & profiles
    │   ├── api/          # FastAPI routes
    │   ├── config/       # Configuration loader
    │   ├── context/      # Shared conversation context
    │   ├── embeddings/   # Vector search (optional)
    │   ├── memory/       # Persistent memory (MEMORY.md/USER.md)
    │   ├── models/       # LLM providers (Azure/OpenAI/CLI)
    │   ├── sandbox/      # Docker sandbox
    │   ├── skills/       # Skill extraction & matching
    │   └── tools/        # Built-in tools (file, shell)
    └── tests/
```

## Roadmap

- [x] Multi-perspective council discussion
- [x] Full-stack tool-calling execution
- [x] Self-learning (discussion + execution skills)
- [x] Docker sandbox isolation
- [x] Multiple model backends
- [x] LLM-as-Judge test suite
- [x] Docker Compose deployment
- [x] Embedding-based semantic search
- [x] Web UI (Next.js + SSE streaming + session management)
- [x] Streaming tool-calling with real-time output
- [x] Human-in-the-Loop confirmation for dangerous operations
- [x] Executor workspace configuration
- [ ] MCP server extensions
- [ ] Skill marketplace

## Philosophy

> In ancient Athens, the Agora was where citizens gathered to discuss, debate, and decide.
> Different perspectives. Shared context. Better decisions.
>
> Agora brings this to AI — not one model doing everything, but multiple perspectives collaborating on your behalf. Then actually doing the work. And learning from it.

## License

MIT

## Acknowledgments

Agora stands on the shoulders of these excellent open-source projects. We are deeply grateful:

- **[DeerFlow](https://github.com/bytedance/deer-flow)** — ByteDance's long-horizon SuperAgent harness. Key inspiration for sandbox execution, memory systems, and agent orchestration.
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — Nous Research's self-improving AI agent. Inspiration for the skill learning loop — autonomous skill creation, self-improvement during use, and persistent memory across sessions.

## Contact

Questions, suggestions, or collaboration inquiries are welcome:

- 📧 Email: wilbur.ai.dev@gmail.com
- 🐛 Issues: [GitHub Issues](https://github.com/wilbur-labs/Agora/issues)
