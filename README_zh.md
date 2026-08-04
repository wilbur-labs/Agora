# Agora

[English](README.md) | **中文** | [日本語](README_ja.md)

Agora 是一个本地优先、面向持久 AI 辅助工作的交付控制平面。它通过同一条权威工作流协调 Codex、Claude Code 和 Kiro：

```text
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
```

## 重要：自动 AI 讨论已经移除

Agora 0.5 的 Scout / Architect / Critic / Synthesizer 自动讨论 Council 不属于 Agora 1.0。CLI、HTTP API 和 Web UI 都不会启动 AI 之间的自由讨论，也不会让模型生成的“共识”推进 Task、Stage 或 Gate。

`agora task consult` 是用户明确触发的一次有界咨询：它只调用当前 Stage 已固定的单个 runtime，结果只是候选建议，必须由人类选择 adopt 或 reject。

## 核心保证

- 只有 Agora 能写入跨 runtime 的 Task、Stage 和 Gate 状态。
- runtime 接收版本化 Context Pack，返回版本化 Handoff Pack。
- process、transport、schema 和 semantic result 分开记录。
- Approval 绑定 repository、ref、commit、Stage、Artifact 路径和 hash。
- Token 预算是准入与记账边界，不冒充 provider 的硬限制。
- resume 和 retry 保持幂等，并对过期权威信息 fail closed。

## Windows 快速启动

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

打开 `http://127.0.0.1:8000/control-plane`，填写项目 `agora` 和同一个 bearer token。控制台读取权威 Task 投影，只显示已经单独审查过的人类操作。

运行不调用任何模型的正式验收：

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

完整的安装、首次 Task、咨询 adopt/reject、恢复和停止步骤见 [Agora 1.0 实用教程](docs/usage/agora-1.0-tutorial.md)。

## 当前范围

Agora 1.0 是已审查的本地控制平面基线。Codex、Claude Code、Kiro 的账号和服务可用性属于外部条件；缺少已固定的 runtime 会阻塞对应 Stage，不会静默替换。动态角色、任意本地模型适配器和运行时替换是 1.0 之后的增强，现有 Kiro 配置与 `.kiro/` 数据会保留。

架构与进度来源：

- [`AGENTS.md`](AGENTS.md)
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md)
- [`docs/requirements/latest-transformation-requirements.md`](docs/requirements/latest-transformation-requirements.md)
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md)

## 许可证

MIT
