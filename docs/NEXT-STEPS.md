# 接下来做什么、怎么做

> 写给**另一台机器上的 agent**（以及在 Agora 里推进这个仓库的人）。2026-10-08 由公司那台
> （Windows，`E:\Work\2026\Projects\Agora-private`）写下，分支 `feature/w8-session-ui` = main = **v1.3.1**。
> 上一版（2026-09-11）的 W9 / W3-a 计划已全部落地或并入下面的计划，不再照它做。
>
> 原则（用户定的，先读）：**先打通再加固**——卡在哪修哪，加固顺序来自实际使用，不来自计划；
> **小刀**，每刀 codex 评审最多 3 轮（诚实性/安全问题不受限）；**「读不到」永远不说成「没有」**。

## 现在在哪

用户从 2026-10-07 起**每天用 Agora 干活**。所以现在有两份 Agora：

| | 目录 | 端口 | 数据 | 用途 |
|---|---|---|---|---|
| 稳定版 | `..\Agora-stable`（git worktree，固定在发布 tag 上） | 8000 | `Agora-stable\data\` | 用户每天用；桌面快捷方式指向它 |
| 开发版 | 本检出 | 8001（`.\agora.ps1 up -Dev`） | `data-dev\` + 沙盒项目 `.agora\dev\sandbox` | 改代码、验收界面；碰不到稳定版的数据、项目、密码 |

顶栏会显示「v1.3.1 · 稳定版」或琥珀色的「开发版」，两者不会混。

### 最近上线（10-07 ~ 10-08）

- **v1.2.0** 从 claude-workspace / kiro-workspace 的任务记录导入本机已有仓库为项目；稳定版/开发版分离、版本号单一来源、`scripts/release.ps1`、`agora.ps1 upgrade/rollback`
- **v1.2.1** 定目标时列出任务记录里已写的目标，一键采用；SQLite 等锁 10s → 60s
- **v1.2.2** 没有可派的步骤时，项目对话里说的一句话自动成为第一步并开始
- **v1.3.0** 网页不能再操控本机 Agora（CORS / Host / Origin 守卫）；开发版隔离；**嵌入终端**（项目对话标题栏「终端」→ 按任务打开 = `cw open <task>`，或在仓库里开 Claude / Codex / Kiro / PowerShell）
- **v1.3.1** 字号切换（默认「大」）、工作台左侧项目栏（每项标「等你处理 N / 运行中 / 未定目标 / 空闲」）、顶栏「等你处理 N」、去掉「尚未实现」类占位行

## 换一台电脑开工

1. `git fetch && git checkout feature/w8-session-ui && git pull`
2. 依赖：`cd backend; uv sync --locked --extra dev`；`cd frontend; corepack pnpm install --frozen-lockfile`
3. 开发版：`.\agora.ps1 build`，`.\agora.ps1 up -Dev`（Windows）。Linux 没有 agora.ps1：
   ```
   # the sandbox project config.dev.yaml points at must be its own git repo (agora.ps1 does this on Windows)
   mkdir -p .agora/dev/sandbox && cd .agora/dev/sandbox && [ -d .git ] || { git init -q && echo "# Agora development sandbox" > README.md \
     && git add README.md && git -c user.name=agora -c user.email=agora@localhost -c commit.gpgsign=false commit -q -m sandbox; }; cd -
   cd frontend && corepack pnpm build && cd ../backend
   AGORA_CONFIG_PATH=$PWD/../config.dev.yaml AGORA_INSTANCE=dev AGORA_KEYRING_SERVICE=agora-remote-env-dev \
     .venv/bin/uvicorn agora.api.app:app --host 127.0.0.1 --port 8001
   ```
4. 那台机器也要日常用：建自己的稳定版
   `git worktree add --detach ..\Agora-stable v1.3.1` → 在里面装依赖、`.\agora.ps1 build`、`.\agora.ps1 up`、`.\agora.ps1 shortcut`。
   **数据库是每台机器自己的**，不要拷别的机器的（里面是那台机器的路径）。
5. 终端第一次用之前，从桌面快捷方式（或 `agora.ps1 up`）打开一次 Agora——它把终端权限交给浏览器。

## 门禁与发布

- 每次改代码：前端 `corepack pnpm test:control-plane && corepack pnpm lint && npx tsc --noEmit && corepack pnpm build`（先清代理环境变量）；后端相关测试 + `python -m pyflakes`；然后 codex 评审（`claude-workspace/scripts/codex-review.sh`），过了才提交。
- 界面改动在开发版上看（截图可用 Edge headless：`msedge --headless=new --screenshot=... http://127.0.0.1:8001/...`）。
- **发布与升级每次都要用户明确说「发布」**。流程：feature 快进到 main 推 origin（**只推 origin，不推 public**）→
  `.\scripts\release-and-upgrade.ps1 -Version x.y.z`（全部门禁约 50 分钟 → tag → 升级稳定版，失败自动回滚）。发布期间不要跑别的测试（CR-33：负载高时一个测试会抢锁失败）。
- 回退：稳定版目录 `.\agora.ps1 rollback -Yes`（恢复升级前的数据库备份；升级后写入的数据和改过的远端密码会丢，会先提示）。

## 等用户回答（不答不做）

- 「等你处理」要不要算上 claude-workspace 任务里 OPEN 的 CR？（现在只算 Agora 自己的待处理事项）
- Orca 编排借鉴的小改动——派活提示用五段格式（改哪 / 改成什么 / 约束 / 能动什么 / 怎么算完成）+ agent 收尾给明确的成功/失败结论——排在终端之前还是之后？没答 = 之后。

## 计划（没有新反馈时的默认顺序）

1. **收 v1.3.1 的使用反馈**，卡在哪修哪。
2. **终端画面实测**：只做过脚本端到端和截图，没在真浏览器里看过终端本身；首次使用的显示问题（字体、颜色、resize、中文宽度）优先修。
3. **运行活跃度**：每个 run 记最后输出时间，界面显示「最后输出 N 分钟前」，超阈值如实说「可能卡住了」。只显示，不动作。
4. 「按任务打开」支持 kiro-workspace 的任务（现在只认 claude-workspace；kiro 是 `kiro-cli chat --agent`）。
5. Orca 借鉴小改动（见上）。
6. **远端终端**（CR-30 刀 2）：asyncssh PTY，沿用注册环境的凭据与 TOFU；断线即断、用 `claude --resume` 接着来；**不用 tmux**（滚轮会坏），经常断线再考虑 dtach。
7. 用专用账户在真机上走一遍远端派活。
8. 欠账：CR-33 根治（methodology 认领评审在写事务里重验全历史，持锁 9–10s）；diff 逐行批注发回 worker；通知降噪；订阅限额显示；环境的「测试连接 + TOFU 确认」UI；远端环境注册表的一组 defer（keyring 孤儿、并发编辑、删除仍有改动的环境等）。

## 坑（这两天踩过的）

- 从 PowerShell 用管道调 `agora.ps1 up`（`| Where-Object` 等）会一直挂住：后台服务继承了管道句柄。用 `Start-Process -RedirectStandardOutput` 写文件。
- Windows PowerShell 5.1 传给原生程序的参数里，双引号会被吃掉；`$null` 传给 .NET 字符串参数会变成空串（用 `[NullString]::Value`）。
- uvicorn 没装 `websockets` 就拒绝所有 WebSocket 升级，TestClient 看不出来——终端问题要对真实服务器端到端测。
- Windows 上 `python3` 是商店存根；用 `python` 或 `.venv` 里的解释器。Git Bash 的 heredoc 喂中文给 Python 会按 cp932 解码出错：脚本写成文件再跑。
- ps1 文件只写 ASCII：Windows PowerShell 5.1 按系统码页读无 BOM 文件，中文会乱。
