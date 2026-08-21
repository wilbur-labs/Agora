# Agora

[English](README.md) | **中文** | [日本語](README_ja.md)

**Agora 是一个本地优先的控制台，让你一个人同时监工多个 coding agent。**
给项目配一个文件夹、一个**开发** agent 和一个**不同的评审** agent：开发 agent 干活，评审 agent 独立交叉评审它的产出，在你批准之前什么都不会推进。Agora 是跨 Codex、Claude Code、Kiro 记录每个任务、每次运行和每个决策的唯一权威。

```text
项目（文件夹 + 开发 agent + 评审 agent）
   -> 任务
   -> 开发运行（隔离的 git worktree）
   -> 由另一个 agent 自动交叉评审
   -> 你批准
   -> 已验证
```

核心是**分诊**：一个人盯着多个 agent，一眼看清哪些需要回复、哪些等批准、哪些受阻——而不是在各自的终端里一个个盯。

## 你能得到什么

- **关注优先的 Portfolio。** 任务分成「需要你处理 / 开发中 / 已完成」三组，按最要紧的信号着色——待回答的提问、待批准的审批、或受阻——让需要人介入的工作最先浮上来。
- **项目级 开发 + 评审 agent。** 每个项目固定一个默认实现者和一个**不同的**评审 agent（如 Codex 开发、Claude 评审）。自审毫无价值，所以评审永远换另一个 agent。
- **自动交叉评审。** 开发运行成功后，Agora 自动派项目的评审 agent 跑一次独立评审，把开发产出喂给它，再把结果作为待批准项交给你。批准后任务推进到「已验证」；评审**有真牙齿**——给出具体缺陷发现，不是橡皮章。
- **隔离的交付物。** 每个 agent 工作区都是它自己的 git 仓库，所以一次运行的产出恰好就是它生成的文件——在 UI 里干净呈现，评审时不被外层仓库的噪声干扰。
- **应用内需求访谈。** agent 在页面上向你提澄清问题，你的回答汇成 spec——澄清是产品该有的一步，而不是在旁边的聊天窗口里代劳。
- **说实话的设置页。** 设置页探测每个 runtime 的安装、版本和登录状态（不耗 token），按 agent 报告 Claude 的 token 用量，并让你设置项目根目录和执行上限。
- **底座是持久的控制平面。** 只有 Agora 能写入跨 agent 的任务与 gate 状态；审批绑定 repo、ref、commit 和 artifact；resume 与 retry 幂等，并对过期权威 fail closed。已固定的 runtime 绝不静默替换——缺失时阻塞对应工作，而非顶替。

> Agora 0.5 的 Scout / Architect / Critic / Synthesizer 自动讨论 Council 已退役，不属于本产品；模型输出永远不会自行推进状态。

## 快速开始（Windows）

```powershell
cd backend
uv sync --locked --extra dev

cd ..\frontend
corepack pnpm install --frozen-lockfile
corepack pnpm build

cd ..\backend
$env:AGORA_CONTROL_PLANE_TOKEN = "请替换为足够长的随机密钥"
uv run uvicorn agora.api.app:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/portfolio` 看关注优先看板，或 `http://127.0.0.1:8000/control-plane` 看已鉴权的任务控制台。填项目 `agora` 和同一个 bearer token。从这里你可以新建项目（它会得到自己的文件夹和隔离的 agent 工作区）、指定它的开发与评审 agent、并运行任务。

不调用任何 AI provider、也不碰配置的数据库，验证完整的正式控制路径：

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

它在临时 Git 项目里启动确定性的本地子进程，跑完每个 stage 和 gate，记录一次明确的人工批准，重新打开 SQLite，打印一份 JSON 收据，然后删除工作区。这是控制平面验收，不是 Codex/Claude/Kiro 原生产出质量的证据。

## 运行时

Codex、Claude Code、Kiro 是明确固定的 CLI 适配器。它们的订阅和可用性属于外部条件：缺失或未登录的 runtime 会阻塞对应工作，绝不静默替换。探测与登录状态在设置页呈现。

## 了解更多

- [`AGENTS.md`](AGENTS.md) —— 架构与约定
- [Agora 实用教程](docs/usage/agora-1.0-tutorial.md) —— 安装、首个任务、批准、恢复、停止
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md) —— 控制平面协议
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md) —— 实现进度

## 许可证

MIT
