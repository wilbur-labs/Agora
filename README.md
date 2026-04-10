# 🏛 Agora

**Multi-perspective AI council — discuss, design, execute, evolve.**

Agora is not another AI chatbot. It's a **council of AI advisors** that discuss your ideas from different perspectives before taking action.

You speak once. Multiple AI perspectives respond — a researcher finds evidence, a designer proposes solutions, a critic finds flaws. All sharing the same context. No copy-pasting between tools.

## Why Agora?

Every AI tool today is a **single brain**. You talk to ChatGPT, then copy the result to Claude for review, then ask Gemini to search for references. Each time you lose context, miss nuance, and waste time.

Agora fixes this:

```
You: "I want to do OPCD distillation on top of LoRA training"

◆ Scout (Researcher)
  Found the OPCD paper (arXiv:2602.12275), key mechanism is...
  Here's how it combines with LoRA...
  Question: What's your terminology dataset size?

◆ Architect (Designer)  
  Two-stage pipeline: Stage 1 LoRA SFT → Stage 2 OPCD + LoRA...
  Hardware allocation for your GPU...
  Trade-offs: [table]

◆ Critic (Reviewer)
  Reverse KL direction has mode-seeking risk for translation...
  Teacher quality is the ceiling — validate before distilling...
  Missing: no evaluation metric defined for terminology accuracy.
```

One input. Three perspectives. Shared context. Real discussion.

## Quick Start

```bash
git clone https://github.com/user/agora.git
cd agora/backend
pip install -e .
python -m agora
```

Requires one of: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Gemini CLI](https://github.com/google-gemini/gemini-cli), or [Kiro CLI](https://kiro.dev).

## How It Works

```
You → Agora Council → ┬─ Scout (research & evidence)
                       ├─ Architect (design & structure)
                       └─ Critic (review & challenge)
                          ↓
          All agents share the same conversation context
          Results displayed in real-time, streaming
```

### Council Agents

| Agent | Role | Focus |
|-------|------|-------|
| **Scout** | Researcher | Find information, papers, related projects |
| **Architect** | Designer | Propose technical solutions and architecture |
| **Critic** | Reviewer | Challenge assumptions, find gaps and risks |
| **Sentinel** | Security *(optional)* | Security review, only activated when relevant |

### Supported Backends

| Backend | Type | Cost |
|---------|------|------|
| Claude Code CLI | `claude -p` | Subscription |
| Gemini CLI | `gemini -p` | Free / Subscription |
| Kiro CLI | `kiro-cli chat` | Subscription |

All agents can use any backend. Configure in `config.yaml`.

## CLI Commands

| Command | Description |
|---------|-------------|
| `/agents` | List active council agents |
| `/reset` | Clear conversation context |
| `/memory` | View persistent memory |
| `/profile` | View user profile |
| `/quit` | Exit |

## API

```bash
# Start API server
make dev

# Chat (SSE streaming)
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Design a CI/CD pipeline for my Go project"}'

# List agents
curl http://localhost:8000/api/agents
```

## Configuration

Edit `config.yaml`:

```yaml
council:
  default_agents: [scout, architect, critic]
  model: kiro  # All agents use this model

# Add sentinel for security-sensitive tasks
# default_agents: [scout, architect, critic, sentinel]
```

Edit `agora/agents/profiles/user.yaml` with your background — agents will tailor responses to you.

## Memory

Agora remembers across sessions using bounded Markdown files (inspired by [Hermes Agent](https://github.com/NousResearch/hermes-agent)):

- **MEMORY.md** — Agent notes, lessons learned, project context
- **USER.md** — Your preferences and communication style

Memory is injected into every conversation, keeping agents informed without you repeating yourself.

## Roadmap

- [x] Multi-perspective council discussion
- [x] Streaming CLI with real-time output
- [x] Persistent memory (Hermes-style)
- [x] Multiple model backends (Claude/Gemini/Kiro)
- [ ] Task decomposition & execution after discussion
- [ ] Skill auto-learning from successful tasks
- [ ] Web UI
- [ ] Gemini as utility agent for lightweight tasks

## Philosophy

> In ancient Athens, the Agora was where citizens gathered to discuss, debate, and decide.
> Different perspectives. Shared context. Better decisions.
>
> Agora brings this to AI — not one model doing everything, but multiple perspectives collaborating on your behalf.

## License

MIT
