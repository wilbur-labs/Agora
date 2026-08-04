# Agora

[English](README.md) | **中文** | [日本語](README_ja.md)

Agora 是一个本地优先、可持久恢复的 AI 交付控制平面。它通过唯一的权威工作流协调 Codex、Claude Code 和 Kiro：

```text
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
```

## 重要：自主 AI 议会已经退役

Agora 0.5 的 Scout / Architect / Critic / Synthesizer 多 AI 讨论模式不再属于 Agora 1.0。默认 CLI、HTTP API 和网页入口都不会再启动 AI 之间的自主讨论，也不会把模型生成的“共识”写成权威工作流状态。

`agora task consult` 不是 AI 互聊。它是用户显式发起、范围受限的一次咨询，只调用当前 Stage 已经固定的单个 runtime。咨询结果只是候选，必须由人明确采纳或拒绝。

## 核心保证

- 只有 Agora 能写入跨 runtime 的 Task、Stage 和 Gate 状态。
- 每次 Run 接收版本化 Context Pack，并返回版本化 Handoff Pack。
- 进程、传输、Schema 与语义结果分别记录；退出码 0 不等于成功。
- Approval 绑定仓库、ref、commit、Stage、Artifact 路径和哈希。
- 实现或咨询不能挪用独立评审所需预算。
- resume 和 retry 必须幂等，并在权威信息过期时封闭失败。

## 当前入口

```powershell
# 查看权威 Task 命令
cd backend
.\.venv\Scripts\agora.exe task --help

# 开发模式启动 API 与静态 Control Plane UI
cd ..
make dev
```

打开 `http://localhost:8000/control-plane` 使用经过认证的 Task 控制台。

架构与当前进度以这些文件为准：

- [`AGENTS.md`](AGENTS.md)
- [`docs/architecture/protocol-domain-freeze-v1.md`](docs/architecture/protocol-domain-freeze-v1.md)
- [`docs/requirements/latest-transformation-requirements.md`](docs/requirements/latest-transformation-requirements.md)
- [`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md)

Agora 1.0 仍在迁移中。历史 0.5 版本号或等待审计删除的旧文件，不代表当前产品语义。

## 许可证

MIT
