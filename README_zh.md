# 🏛 Agora

[English](README.md) | **中文** | [日本語](README_ja.md)

**多视角 AI 议会 — 讨论、设计、执行、进化。**

Agora 是一个**全栈 AI Agent 平台**，多个 AI 顾问从不同视角讨论你的想法，然后执行计划——并从每次交互中学习。

你说一次。多个 AI 视角回应。然后它们开始工作。

## 特性

- 🏛 **议会，不是聊天机器人** — 多个 AI Agent 从不同角度（研究、架构、批评、安全）讨论你的问题，然后综合出统一结论。
- 🔧 **讨论 → 执行** — Agent 不只是建议。讨论结束后，执行器调用真实工具（文件读写、Shell 命令）来实现计划。
- 🧠 **自我进化** — 每次讨论和执行都被提炼为可复用的技能。Agent 会随着使用越来越擅长你的工作流。
- ⚙️ **完全可定制** — 用自定义 Prompt 创建你自己的 Agent。每个 Agent 可以指定不同的 LLM。通过 YAML 配置，无需改代码。
- 🔌 **模型无关** — Azure OpenAI、OpenAI、Kiro CLI、Claude CLI、Gemini CLI，或任何 OpenAI 兼容 API。可按 Agent 混合使用。
- 🐳 **自托管** — 一条 `docker compose up` 拥有整个技术栈。无厂商锁定，数据不出你的网络。

## 快速开始

### Docker（推荐）

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd agora
cp .env.example .env
# 编辑 .env — 添加你的 API key
docker compose up -d
# 打开 http://localhost:8000
```

### 本地开发

```bash
git clone https://github.com/wilbur-labs/Agora.git
cd agora
cp .env.example .env
make install
make dev      # API 服务 http://localhost:8000
make dev-ui   # 前端开发 http://localhost:3000
```

## Web UI

- **聊天界面** (`/chat`) — 多 Agent 流式讨论，Markdown 渲染，路由确认
- **Agent 配置** (`/agents`) — 查看/编辑/创建/测试 Agent
- **Skills** (`/skills`) — 查看已学习的技能
- **设置** (`/settings`) — Memory 和用户 Profile 管理
- **会话历史** — 自动保存，侧边栏切换
- **导出/分享** — Markdown 导出 + 分享链接

## 议会 Agent

| Agent | 角色 | 何时参与 |
|-------|------|---------|
| **Moderator** | 路由请求 | 始终第一个 |
| **Scout** | 研究与证据 | 讨论阶段 |
| **Architect** | 设计与方案 | 讨论阶段 |
| **Critic** | 审查与质疑 | 讨论阶段 |
| **Sentinel** | 安全审查 | 可选 |
| **Synthesizer** | 总结结论 | 讨论结束 |
| **Executor** | 工具执行 | 执行阶段 |

## 工作流程

```
用户输入
  → Moderator 路由: QUICK / DISCUSS / EXECUTE
    → DISCUSS:
        Scout → Architect → Critic → Synthesizer
        → 用户确认 Action Items
        → Executor 执行
        → 学习技能
    → EXECUTE:
        → Executor 工具调用循环
        → 学习技能
```

## 自我学习

Agora 从每次交互中学习：
- **讨论技能** — 决策模式，各视角发现
- **执行技能** — 步骤流程，经验教训
- **成功追踪** — 每个技能记录成功/失败次数
- **记忆** — 用户偏好，项目上下文

## 许可证

MIT

## 致谢

Agora 的诞生离不开以下优秀的开源项目，在此表示衷心感谢：

- **[DeerFlow](https://github.com/bytedance/deer-flow)** — ByteDance 的长周期 SuperAgent 框架，在沙箱执行、记忆系统和 Agent 编排方面给予了重要启发。
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — Nous Research 的自我进化 AI Agent。Skill 学习闭环的灵感来源——自主创建技能、使用中自我改进、跨会话持久记忆。

## 联系

如有问题、建议或合作意向，欢迎联系：

- 📧 Email: wilbur.ai.dev@gmail.com
- 🐛 Issues: [GitHub Issues](https://github.com/wilbur-labs/Agora/issues)
