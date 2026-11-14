# Agora

[English](README.md) | **中文** | [日本語](README_ja.md)

**Agora 是一个本地优先的开发工作台，面向同时用多个编程 Agent（Claude Code、Codex、Kiro）推进多个项目的人。**

早上打开它，每个项目昨天停在哪、有什么在等你、下一步是什么，一眼可见。点开一个任务，就回到同一个 Agent 的同一段对话里，并且在正确的机器上继续。活干完后，状态写回它该在的地方——项目自己的仓库，而不是只有 Agora 读得懂的数据库。

```text
概览（所有项目，谁在等你）
   -> 工作页（一个任务 = 与 Agent 的一段持续对话）
   -> 评审关卡（提交前由另一个 Agent 评审）
   -> 状态写回：目标、变更记录（CR）、架构决策（ADR）、进度
```

当前版本：**1.10.1**。界面支持 English / 中文 / 日本語。

## 功能

- **概览就是首页。** 每个项目一张卡片，从你登记的 workspace 目录导入。卡片显示进行中的事、等你处理的事、提交评审关卡的记录，以及项目来自哪个 workspace。项目可以暂停、归档、恢复；任务分为「待办 / 进行中」，可拖动排序。
- **一个任务 = 一段持续对话。** 工作页续接 Agent 自己的会话（Claude Code / Codex / Kiro），而不是重放历史；输出实时流式显示，正在运行的一轮已经多久没有新输出也会如实标出。在 Agora 之外继续过的对话会被找回，重启后正在运行的工作会被恢复。
- **执行者与另一个评审者。** 每个项目固定一个执行 Agent 和一个*不同的*评审 Agent——自己审自己没有意义。评审关卡真正读过改动之前，什么都不会提交。
- **原地读取你已有的工作。** workspace 里已有的任务记录（`tasks/<name>/{task.yaml, RESUME.md, WORKFLOW.md, adr/}`）实时读取、原样呈现——目标、变更记录、决策、进度快照。不复制进 Agora 的数据库，所以也不存在同步问题。
- **通过 SSH 的远程环境。** 登记一台主机（可直接从 `~/.ssh/config` 里选），测试连接，确认主机密钥后才信任它，然后在上面跑任务或开终端。凭据可用本机 SSH 密钥，或存进系统钥匙串的密码。
- **内置终端。** Agora 里的交互式终端，本地或已登记的远程主机都可以，直接打开到某个任务。
- **技能库。** 一个遵循 Agent Skills 标准的技能库，按项目安装给 Claude、Codex、Kiro。可以拉取、阅读并导入公开的技能仓库——安装前先读内容。
- **用量一目了然。** 每个页面底部显示各 Agent 的剩余额度——Claude 取自它自己的 `/usage`（含 Team 计划），Kiro 显示 credits。
- **桌面应用。** 可安装的 Windows 应用：窗口、托盘图标、一键升级，底层仍走同一个启动脚本。
- **稳定版与开发版互不干扰。** 稳定版严格对应一个发布 tag；升级前先备份数据库，任何一步失败都会回滚。开发检出跑在单独的端口、单独的数据库和钥匙串命名空间上，碰不到稳定版的数据。

### 贯穿全局的一条规则

**Agora 从不声称它的读取不支持的事。**「读不到」「不知道」「没有」是三种不同的回答，永不合并。一个说「不知道」的半成品界面可以天天放心用；把「我读不到」说成「没有」，会让你做出错误的决定。

## 环境要求

- Windows 10/11，PowerShell 5.1 以上（启动脚本和桌面应用仅限 Windows；后端本身是普通 Python）
- Python 3.10+ 与 [uv](https://docs.astral.sh/uv/)
- Node.js 20+ 与 `corepack`（pnpm）
- Git
- 至少一个已安装并登录的 Agent CLI：`claude`、`codex` 或 `kiro-cli`。Agent 是显式固定的适配器——缺失或未登录时，对应的工作会被阻塞，绝不会被悄悄替换。

## 快速开始

```powershell
git clone https://github.com/wilbur-labs/Agora.git
cd Agora

cd backend
uv sync --locked --extra dev
cd ..\frontend
corepack pnpm install --frozen-lockfile
corepack pnpm build
cd ..

.\agora.ps1 up          # 启动后端，等待 /health，打开 Agora
```

后端直接提供构建好的前端，日常使用只有一个进程，地址 `http://127.0.0.1:8000`。其他命令：

```powershell
.\agora.ps1 down        # 停止（不会误杀端口上的非 Agora 进程）
.\agora.ps1 status      # 是否在运行、pid、前端构建是否过期
.\agora.ps1 logs        # 后端日志最后 40 行
.\agora.ps1 up -Build   # 先重新构建前端再启动
.\agora.ps1 shortcut    # 在桌面创建「Agora」快捷方式
.\agora.ps1 up -Dev     # 把当前检出作为开发版跑在 8001 端口
```

### 可自我升级的稳定版

在单独的目录检出一个发布 tag，日常就用它：

```powershell
git clone https://github.com/wilbur-labs/Agora.git Agora-stable
cd Agora-stable
git checkout v1.10.1
# 按上面的步骤安装依赖并构建，然后：
.\agora.ps1 up
.\agora.ps1 upgrade -To v1.11.0   # 以后升级：备份、检出、构建、启动、验证——失败则回滚
.\agora.ps1 rollback -Yes          # 回到上一次升级前的版本
```

### 桌面应用

```powershell
cd desktop
npm install
npm run build           # 在 desktop\dist 生成 NSIS 安装包
```

桌面应用通过 `agora.ps1` 启动和升级 Agora，所有安全检查都集中在一处。

## 第一次使用

1. **设置 → 工作数据来源**：登记已经存放任务记录的目录。Agora 存储前会先探测，并显示每个目录能读到什么——包括读不到的部分。
2. 从这些 workspace **导入项目**，或新建一个。为它选择执行 Agent 和评审 Agent。
3. **概览**：选一个任务，在工作页继续。
4. 可选：在**环境**里添加远程主机，在**技能库**里给项目安装技能。

## 架构

| 部分 | 路径 | 说明 |
|---|---|---|
| 后端 | `backend/agora/` | FastAPI + SQLite。Claude Code / Codex / Kiro 适配器、会话与实时转写、项目注册表、workspace 读取、SSH 环境、终端、技能 |
| 前端 | `frontend/` | Next.js，构建为静态文件由后端提供 |
| 桌面 | `desktop/` | 包裹 `agora.ps1` 的 Electron 外壳 |
| 启动脚本 | `agora.ps1` | 启动 / 停止 / 状态 / 升级 / 回滚 |
| 决策记录 | `.agora/decisions/` | 架构决策记录（ADR） |

Agora 1.0 的形式化控制平面（需求、工作流、阶段关卡）仍在代码库里、仍有测试覆盖，但已冻结，不再出现在主导航中。

## 开发

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -m "not integration"
cd ..\frontend
corepack pnpm test:control-plane
corepack pnpm exec tsc --noEmit
cd ..
git diff --check
```

不调用任何 AI 服务、也不碰你的数据库，完整走一遍控制路径：

```powershell
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

更多：[`AGENTS.md`](AGENTS.md)（架构与约定）、[`CONTRIBUTING.md`](CONTRIBUTING.md)、[`CHANGELOG.md`](CHANGELOG.md)、[`.agora/development/PROGRESS.md`](.agora/development/PROGRESS.md)（工程日志）。

## 许可证

MIT
