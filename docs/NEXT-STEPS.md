# 接下来做什么、怎么做

> 写给**另一台机器上的 agent**（以及在 Agora 里推进这个仓库的人）。2026-10-15 由公司那台
> （Windows，`E:\Work\2026\Projects\Agora-private`）更新。main = **v1.4.0**（`2a8bc48`）；
> 分支 `feature/w8-session-ui` 比 main 多 2 个**未发布**的提交（见下「已做未发布」）。
> 上一版（2026-10-08，v1.3.1）的计划里做完的已移出，没做的并入下面。
>
> 原则（用户定的，先读）：**先打通再加固**——卡在哪修哪，加固顺序来自实际使用，不来自计划；
> **小刀**，每刀评审最多 3 轮（诚实性/安全问题不受限）；**「读不到」永远不说成「没有」**。

## 现在在哪

用户从 2026-10-07 起**每天用 Agora 干活**。所以现在有两份 Agora：

| | 目录 | 端口 | 数据 | 用途 |
|---|---|---|---|---|
| 稳定版 | `..\Agora-stable`（git worktree，固定在发布 tag 上） | 8000 | `Agora-stable\data\` | 用户每天用；桌面快捷方式指向它 |
| 开发版 | 本检出 | 8001（`.\agora.ps1 up -Dev`） | `data-dev\` + 沙盒项目 `.agora\dev\sandbox` | 改代码、验收界面；碰不到稳定版的数据、项目、密码 |

顶栏会显示「v1.4.0 · 稳定版」或琥珀色的「开发版」。

### 最近上线

- **v1.2.0 ~ v1.3.1**（10-07 ~ 10-08）：从工作数据来源导入本机仓库为项目；稳定版/开发版分离与 `upgrade/rollback`；定目标一键采用记录里的目标；一句话开始；网页不能操控本机 Agora；**嵌入终端**（按任务打开 = `cw open <task>`）；字号切换、项目栏、「等你处理 N」
- **v1.4.0**（10-13）：自己的 logo（V7c）与 favicon；扁平化（1px 边框、小圆角、无阴影）；Inter + Noto Sans SC/JP 打包；lucide 图标；**移除项目**（只取消登记，不碰仓库文件；名下有任务/运行时拒绝）；导入候选排除 Agora 自身与两个 workspace 工具目录

### 已做未发布（在 feature 上，等用户说「发布」）

- `b92650f` **CR-60**：`agora.ps1 up -Dev` 一律报 dev（原来检出停在发布 tag 上时，8001 会自称稳定版）
- `a1bb693` **CR-59**：`release-and-upgrade.ps1` 在 PS 5.1 上 `-Stable` 默认值为空即退出、升级成功却报「FAILED (exit )」——都已修，退出码读不到时如实说「结果不明」。**CR-57**：导入面板空列表三分——没登记工作数据来源（链到设置）/ 有读不了的记录（可能还有）/ 真没有；设置页「工作区」栏改名「项目存放位置」

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
   `git worktree add --detach ..\Agora-stable v1.4.0` → 在里面装依赖、`.\agora.ps1 build`、`.\agora.ps1 up`、`.\agora.ps1 shortcut`。
   **数据库是每台机器自己的**，不要拷别的机器的（里面是那台机器的路径）。
5. 终端第一次用之前，从桌面快捷方式（或 `agora.ps1 up`）打开一次 Agora——它把终端权限交给浏览器。

## 门禁与发布

- 每次改代码：前端 `corepack pnpm run test:control-plane && corepack pnpm lint && npx tsc --noEmit && corepack pnpm build`（先清代理环境变量）；后端相关测试 + `python -m pyflakes`；然后 codex 评审（`claude-workspace/scripts/codex-review.sh`），过了才提交。**codex 不可用**（额度用完，或 Windows 沙盒失败 = 脚本 exit 4）时用 `kiro-review.sh` **替代**（不是并跑），并写进提交说明。
- 界面改动在开发版上看（截图可用 Edge headless：`msedge --headless=new --screenshot=... http://127.0.0.1:8001/...`）。
- **发布与升级每次都要用户明确说「发布」**。流程：feature 快进到 main 推 origin（**只推 origin，不推 public**）→
  `.\scripts\release-and-upgrade.ps1 -Version x.y.z`（全部门禁约 50 分钟 → tag → 升级稳定版，失败自动回滚）。发布期间不要跑别的测试（CR-33）。
  升级失败后**不要重跑同一条命令**（tag 已存在，会停在发布步）：修好环境后在稳定版目录直接 `.\agora.ps1 upgrade -To vx.y.z`。
- 回退：稳定版目录 `.\agora.ps1 rollback -Yes`（恢复升级前的数据库备份；升级后写入的数据和改过的远端密码会丢，会先提示）。

## 等用户回答（不答不做）

- **CR-54 整体 UI 重设计**：草稿与 codex 设计评审已完成（8 条收下）；剩一个决定——**终端放中间还是右侧**。定稿前不动代码；定稿后 CR-53 的「右栏收起」逻辑随刀 1 撤掉。
- 「等你处理」要不要算上 claude-workspace 任务里 OPEN 的 CR？（现在只算 Agora 自己的待处理事项）
- Orca 编排借鉴的小改动——派活提示用五段格式（改哪 / 改成什么 / 约束 / 能动什么 / 怎么算完成）+ agent 收尾给明确的成功/失败结论——排在哪？没答 = 计划第 5 位。
- **终端里的工作要不要也自动评审**？现状：Agora 派出的 run 跑完会自动派评审（`auto_review: true`，codex 额度用完换 kiro）；但「终端」里交互式 claude 做的改动 Agora 看不到，只靠 claude-workspace 的 `/codex-review` 规则手动跑。候选做法：git pre-commit hook，或终端关闭时检查未评审的改动。是新功能，要先记 CR。
- CR-2 / CR-3 / CR-13（读 kiro-workspace 的 task.yaml、导入任务后在 Agora 里继续开发、读三家 agent 的原生会话记录）：仍未分类，含义和排序待用户定。
- 旧的方向问题：Agora 自用 vs 对外公开；公开仓库 `wilbur-labs/Agora` 的描述还写「AI council」要不要改（对外动作）。

## 计划（没有新反馈时的默认顺序）

1. **收 v1.4.0 的使用反馈**，卡在哪修哪；顺带问用户要不要发布 feature 上的 CR-60 / CR-59 / CR-57。
2. **终端画面实测**：只做过脚本端到端和截图，没在真浏览器里看过终端本身；首次使用的显示问题（字体、颜色、resize、中文宽度）优先修。
3. **运行活跃度**：每个 run 记最后输出时间，界面显示「最后输出 N 分钟前」，超阈值如实说「可能卡住了」。只显示，不动作。
4. 「按任务打开」支持 kiro-workspace 的任务（现在只认 claude-workspace；kiro 是 `kiro-cli chat --agent`）。
5. Orca 借鉴小改动（见上）。
6. **远端终端**（CR-30 刀 2）：asyncssh PTY，沿用注册环境的凭据与 TOFU；断线即断、用 `claude --resume` 接着来；**不用 tmux**（滚轮会坏），经常断线再考虑 dtach。
7. 用专用账户在真机上走一遍远端派活（`adr/0002`：远端环境只注册到只有用户能进的账户）。
8. 欠账（都有 OPEN 的 CR 行，「何时变急」写在行里）：
   - CR-33 根治：methodology 认领评审在写事务里重验全历史，持锁 9–10s（已缓解：busy timeout 60s）
   - CR-12 ④ diff 逐行批注发回 worker、⑤ 通知降噪、⑥ 订阅限额显示（只走官方路径）
   - 环境的「测试连接 + TOFU 指纹确认」UI（CR-4 刀二后半）
   - 远端环境注册表的 defer：CR-10 keyring 孤儿、CR-11 keyring 命名空间按 OS 账户、CR-14 一台主机挂多个项目、CR-15 换执行者看不到前一个的改动、CR-17 并发编辑、CR-18 可删除仍有改动的环境、CR-19 读回失败误回滚、CR-20 远端初始化不可重试、CR-21 位置检查两处空白
   - CR-58：移除项目与并发写入之间的孤儿行（单用户下罕见）
   - CR-5 / CR-6 / CR-7（空闲优先派活、agent 自主标记、skills/hooks 包管理）：用户说「先记录暂不对应」

## 坑（踩过的）

- 从 PowerShell 用管道调 `agora.ps1 up`（`| Where-Object` 等）会一直挂住：后台服务继承了管道句柄。用 `Start-Process -RedirectStandardOutput` 写文件。
- `Start-Process -PassThru` 之后要先读一次 `$p.Handle` 再等，否则 `ExitCode` 是 `$null`（CR-59）。
- Windows PowerShell 5.1：param() 默认值里 `$PSScriptRoot` 是空串（CR-59）；传给原生程序的参数里双引号会被吃掉；`$null` 传给 .NET 字符串参数会变成空串（用 `[NullString]::Value`）。
- uvicorn 没装 `websockets` 就拒绝所有 WebSocket 升级，TestClient 看不出来——终端问题要对真实服务器端到端测。
- Windows 上 `python3` 是商店存根；用 `python` 或 `.venv` 里的解释器。Git Bash 的 heredoc 喂中文给 Python 会按 cp932 解码出错：脚本写成文件再跑。Bash 工具里两个反斜杠会塌成一个——含反斜杠的内容用文件编辑工具写。
- ps1 文件只写 ASCII：Windows PowerShell 5.1 按系统码页读无 BOM 文件，中文会乱。
- 终端/等宽字体栈要带中日文字体，否则中文退回 MS Gothic；等宽字体排在 Noto 前，防终端错位。
