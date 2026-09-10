# Agora — 项目现状 / 未完成部分 / 目标

> **更新于 2026-09-11 深夜**
> 对应分支 `feature/w8-session-ui`，最新提交 `cbd2748`（其上游是 `8e65e0a` ← `4fb5a3a`）。
> 本机路径 `D:\Projects\Agora`。

## 0. 这份文档怎么用

这是**一页看懂「现在到哪了、还差什么、验收怎么做」**。它是摘要，**不是权威记录**。过期时以下面三份为准：

| 内容 | 权威文件 | 说明 |
|---|---|---|
| 目标 / 里程碑 / 每条验收线 | `<workspace>/tasks/agora/task.yaml` | `<workspace>` = `D:\AI\claude-workspace` |
| 逐日状态 / 变更记录（CR Log）/ 踩过的坑 | `<workspace>/tasks/agora/RESUME.md` | 每次开会话自动注入，OPEN 的 CR 会被强制看见 |
| 架构决策（为什么这么做、否掉了什么） | `<workspace>/tasks/agora/adr/0001..0008` | 见 §11 |
| 工程日志（最细，5600+ 行） | `.agora/development/PROGRESS.md` | 本仓库内 |

**§9「明早验收清单」是可以照着按的操作脚本**，包含每一步该看到什么、看到别的说明什么。

---

## 1. 一分钟版

- **产品是什么**：把已用真实项目验证半年的 claude-workspace 工作模型（RESUME 续接 / ANCHOR 目标 / CR 变更管理 / ADR 决策 / lessons 沉淀 / DoD 闭环）**产品化**成一个多 Agent × 多项目 × 多环境的开发工作台。
- **当前主线是 W8**：原语从「Run（有界调用）」换成「**一个任务 = 一段持续会话**」，界面按 mockup v2 的四屏重做。
- **W8 的代码已提交**（`4fb5a3a` 42 文件 / +11202 行，`cbd2748` 10 文件 / +1507 −192）。**代码这边没有待办了**，剩下的四条验收线里三条要人按、一条（改动/门禁进对话流）要人写。
- **今晚（2026-09-11）的新增事实**：服务器起来实测可用，前后端四屏全 200；使用者按了五分钟，问出 **5 条 OPEN 的 CR**（§7）。其中最重的一条是「**页面与 mockup 差距太大**」——见 §6，我今晚做了一份**逐条对照**，但要说清一件事：**mockup 的链接从这个会话读不到**（§6.0）。
- **最大的一块结构性缺口**：`/portfolio`（项目库存 + CR Log + ADR 的那一屏）**不在一级导航里**，而 Today 明确没有接管它的库存视图。也就是说，**本产品唯一真正的差异化（防偏离机制）现在只能靠手敲 URL 到达**。这是 §6.2 的第 1 条，我建议它排在最前。

---

## 2. 目标

### 2.1 goal（逐字来自 `task.yaml`）

> 把已用真实项目验证半年的 claude-workspace 工作模型（RESUME 续接 / ANCHOR 目标 / CR 变更管理 / ADR 决策 / lessons 技能沉淀 / DoD 闭环），产品化成一个多 Agent × 多项目 × 多环境的开发工作台：**打开就知道昨天停在哪、下一步是什么，一键在正确的环境里用正确的 agent 接着干；干完自动把状态写回去。**
> 并且**读得到那个模型已经积累的真实工作**：本机既有的 workspace 目录（claude-workspace / kiro-workspace 的 `tasks/<name>/{task.yaml,RESUME.md,WORKFLOW.md,adr/}`）以只读方式直接在 UI 里呈现，而不是要求把工作重录一遍。

### 2.2 唯一的成功标准

**作者自己每天愿意用它。** 不是功能条数，不是测试数，不是评审轮数。

由此派生两条贯穿全局的规则（两条都已写进 workspace 的 constitution，不只是这个项目的约定）：

| 规则 | 一句话判据 | 它挡住了什么 |
|---|---|---|
| **Reach before polish（打通 before 加固）** | 动手前问「这个界面**今天**、在使用者**真实的工作流**里，够得到吗？」够不到，就让「让它够得到」的那一刀优先——**哪怕被推迟的那一刀明显是更好的工程** | 2026-09-05/06 一个会话里 19 轮评审全砸在三块面板的诚实性上（全是真缺陷），而使用者真实的工作流（Windows → 内网 AI 服务器）被一个没开工的里程碑堵着——**那三块面板对他而言一块都够不到** |
| **产品不许声称它的读取不支持的事** | 「**读不到**」「**不知道**」「**没有**」是三种不同的话，永不合并。这一条**从不推迟**，它不属于「加固」 | 未完成的产品说「不知道」可以天天用；把「我读不到」说成「没有」会让人做错决定，而且他分不清自己看到的是哪一种答案 |

### 2.3 明确不做（non-goals）

- 不做通用助手入口（首页不是「有什么可以帮你的吗」）
- **不常驻一个活的 CLI 进程当会话**——笔记本合盖即断（`adr/0007` 备选 2）
- **不由 Agora 自己存全部历史每轮重发**——agent 的内部工具状态不在文本历史里（`adr/0007` 备选 3）
- 不做双向写回（在 Agora 里改别人 workspace 的 CR / 写 RESUME）——撞 `adr/0003`：`Environment` 只有 `run` + `probe`，**故意没有写文件能力**
- 不把别人 workspace 的任务存进 Agora 自己的库（会立刻产生同步问题）
- 不猜路径：workspace 目录由使用者显式登记，不搜索、不 fallback
- 冻结层（AI-DLC 形式化控制平面 / Requirements / Workflows，约 45k 行）不再新增功能，**且已撤出一级导航**（`adr/0001`）

---

## 3. 系统现状（架构地图）

### 3.1 三次身份沿革（别再考古一遍，`adr/0001`）

| 时期 | 主张 | 代码现状 |
|---|---|---|
| v0.1–v0.4（~2026-04） | 议会讨论 + 全栈执行 + 自学习 | **已删除**（council 退役；`skills/` `memory/` `context/` `tools/` `sandbox/` 五个包现在是空的） |
| v0.5–v1.1（2026-07~08） | AWS AI-DLC 形式化方法论控制平面 | **存活约 45k 行，冻结**（`orchestration/` 32k + `protocol/` 6.7k + `control_plane/` 6.2k） |
| 当前（2026-08-27 起） | **AI Agent 开发工作台** | 复用 `execution/adapters` + `workspaces/provisioner` + 前端 Task Workspace |

### 3.2 后端模块（`backend/agora/`，行数为本次实测）

| 模块 | 行数 | 角色 |
|---|---|---|
| `execution/` | 9208 | **W8 的主战场**：三个适配器、派发器、会话记录（`sessions.py`）、三家事件解析（`transcript.py`）、按字节偏移的转写（`runlog.py`） |
| `projects/` | 3822 | 项目注册表 / 项目导入器（`.agora/projects.yaml`）/ CR Log 与进度快照的 reader / **W7 的 workspace 任务 reader**（`workspace_tasks.py`） |
| `tasks/` | 1722 | 任务、状态机、next action、进度 |
| `attention/` | 1843 | 「有什么在等你」 |
| `api/` | 1116 | `app.py` + `settings_router.py` |
| `requirements/` | 1046 | 冻结层 |
| `workflows/` | 864 | 冻结层 |
| `workspaces/` | 523 | git worktree 隔离（**仍假定本地文件系统之外的部分已抽象，但只有 `LocalEnvironment` 一个实现**） |
| `control_plane/` `protocol/` `orchestration/` | 6157 / 6752 / 32069 | **冻结层**，UI 已不暴露 |
| `agents/` `context/` `memory/` `models/` `sandbox/` `skills/` `tools/` | 0 | **空包**（v0.4 遗迹）——注意 `skills/` 是空的，所以「技能库」那屏读不到任何东西，这不是 bug 而是尚未建造 |

### 3.3 API 出口（`/api` 前缀，9 个 router）

会话与运行（W8 新增/改动的那几个）：

```
GET    /api/tasks/{task_id}/sessions           # 这个任务每个适配器的会话状态
DELETE /api/tasks/{task_id}/sessions/{adapter}  # 清除（显式新开一段会话）
GET    /api/runs/{run_id}/output?offset=        # 按字节偏移增量取输出（流式的底座）
POST   /api/runs                                # 发一条消息 = 起一轮
POST   /api/runs/{run_id}/cancel
GET    /api/tasks/{task_id}/recap
POST   /api/tasks/{task_id}/handoff
```

项目 / 设置 / W7 只读读取：

```
GET  /api/projects                    # 含 current 指针、projects_base、导入状态
POST /api/projects                    # 新建（root 由 projects_base/<id> 推出，见 CR-52）
PUT  /api/projects/{id}/agents        # 只能改 worker/reviewer
PUT  /api/projects/{id}/charter
GET  /api/projects/{id}/dashboard     # 项目驾驶舱投影（charter + 每任务验收条 + CR Log + 汇总）
GET  /api/projects/{id}/decisions     # ADR
GET  /api/workspace-sources           # W7：登记的 workspace 目录里的任务（活读）
POST /api/workspace-sources[/probe]  DELETE /api/workspace-sources
GET  /api/settings/overview   PUT /api/settings/projects-base   POST /api/settings/pick-folder
```

### 3.4 前端路由与导航

一级导航（`src/components/delivery-shell.tsx`，5 项）：

| 导航 | 路由 | 状态 |
|---|---|---|
| Today | `/` | ✅ 已按 mockup screen 1 重做，**但缺三档聚焦与库存**（§6.1） |
| Workbench（项目工作台） | `/workspace` | ✅ 主战场，3132 行 |
| Environments | `/environments` | ✅ 但**只有一个环境可读**（§6.3） |
| Skills | `/skills` | ✅ 但**清单完全没读**（§6.4） |
| Settings | `/settings` | ✅ |

**仍然存在但已不在导航里**的页面：`/portfolio`、`/attention`、`/runs`、`/control-plane`、`/requirements`、`/workflows`。前三个是「Today 应该接管但还没接管」的东西，后三个是冻结层（按 `adr/0001` 故意撤出）。

⚠️ **`/portfolio` 被撤出导航是目前最大的一处结构性问题**，见 §6.2 第 1 条。

### 3.5 已经做到的能力

| 能力 | 状态 | 证据在哪 |
|---|---|---|
| 一个任务 = 一段**持续会话**，连着说多轮、agent 记得上一句 | ✅ | `backend/tests/test_session_continuity_live.py`（见下） |
| **流式**输出（不出现「等三分钟没有任何字」） | ✅ | `GET /runs/{id}/output?offset=` + 前端轮询全部未结算轮次 |
| 会话读不到时**明说读不到**，不静默新开上下文 | ✅ | 这是 W8 的核心不变量；`decide()` 现在看会话 state |
| 改动文件 / 门禁 / 复核重试可见 | ⚠️ 位置不对 | 在右栏与「时间线」页签，mockup 要求缩进挂在对话流里（验收线 ④） |
| review / retry 轮次**缩进挂在所属 worker 轮次下** | ✅ | `lib/timeline.ts` + `RunWorkUnit`；不能挂的还会说明原因（4 种） |
| 右栏两态（跑着 = 实时输出 / 结束 = 改动 + 门禁） | ✅ | `RunColumnPane` / `LiveRun` / `SettledRunChanges` / `GateBlock` |
| 执行者（适配器）在**发消息那一刻**选 | ✅ | `components/conversation.tsx` 的适配器下拉，不进导航 |
| 进度 = 进度快照驱动，**百分比只报 ✅**，🔵 不折算半分，无快照留空不编数字 | ✅ | `lib/progress-snapshot.ts` + `donePct` |
| 只读读取既有 workspace 的任务（goal / 当前状态 / 下一步 / CR / ADR / 进度） | ✅ | `projects/workspace_tasks.py` + `lib/workspace-sources.ts` |
| 远端项目（Agora 在 Windows，代码在另一台机器） | ❌ 未开工 | W3-a |
| Skill Registry / Desktop 套壳 | ❌ 未开工 | W5 / W6 |

**唯一算数的续接证据**：`backend/tests/test_session_continuity_live.py` **跑在每一次全量门禁里**（约 100 秒），对着**真实的 codex CLI** 连说三轮——
① 第一轮让它记住一个词；② 第二轮它答出来（转写里出现 `››`，说明真的走了 resume）；③ 第三轮在同一段会话里把那个词写进文件（转写里有 `✎ add remembered.txt`）。
**续接是被反复实测的，不是从命令行参数推出来的。**

### 3.6 门禁数字（上次全量实测：`cbd2748`）

| 门禁 | 数字 |
|---|---|
| 后端 pytest | **1435 passed / 13 skipped**（W8 开工前基线 1289 → **+146**） |
| 前端 node 测试 | **298**（297 + 1 skipped） |
| `tsc --noEmit` / `eslint` | 0 / 0 |
| `next build` | 14 页 |

**全量门禁需 20~29 分钟**，不要按「几分钟」估；后台跑时不要接 `| tail`（会全程 0 字节看不到进度）。

命令：

```bash
make test            # 后端（跳过 integration）
make test-all        # 后端全量（含真 CLI 的续接实测，需要 codex 已登录）
cd frontend && corepack pnpm test:control-plane && corepack pnpm lint && corepack pnpm build
```

### 3.7 本地起服务（`uv` 不在本机 PATH 上，所以不走 `make dev`）

```bash
# 后端：启动日志会打 control-plane database D:\Projects\Agora\data\agora.db
cd backend && ./.venv/Scripts/uvicorn.exe agora.api.app:app --host 127.0.0.1 --port 8000
# 前端
cd frontend && corepack pnpm dev --hostname 127.0.0.1     # → http://127.0.0.1:3000
```

**今晚实测**：后端 `/api/projects` `/api/tasks` `/api/workspace-sources` 全 200；前端 `/` `/workspace` `/environments` `/skills` 全 200；Next 16.2.3 三秒就绪。
**控制平面当前内容**：project 2 个（`agora` → root 就是 `D:\Projects\Agora` 本身、`demo` → `D:\Projects\Agora\projects\demo`）、**task 0 个**。

---

## 4. 里程碑总表

| ID | 标题 | 状态 | 备注 |
|---|---|---|---|
| M1–M4 | 议会讨论 / Docker 沙箱 / 智能匹配 / Embedding 检索 | `retired` | 代码已删 |
| M5–M8（旧） | 原计划 Web UI / MCP / Skill 版本 / Sub-agent | `superseded` | 被重定位取代 |
| 1.0–1.1 | AI-DLC 形式化控制平面 | `frozen` | 45k 行，UI 不暴露 |
| **W0** | 证伪切片——跨厂商 session resume 可行性 + 单项目续接闭环 | ✅ done | 结论沉淀到 `lessons/coding-agent-cli-headless-resume.md` |
| **W1** | Project + Run 持久化 + Codex 式三栏信息架构 | ✅ done | |
| **W2** | Environment 抽象（Local / WSL） | ✅ done | `run` + `probe` 两个能力，**只有 `LocalEnvironment` 一个实现** |
| **W4** | Project Goal / Requirement / **CR Log / ADR 进 UI** | ✅ done | 六条验收线全满足；但**落点 `/portfolio` 已被撤出导航**（§6.2 第 1 条） |
| **W7** | 导入既有 workspace 的工作（只读） | ✅ 实际完成（`task.yaml` 里仍写 `planned`，见 §8） | 两半分别在 2026-09-06 / 09-07 完成 |
| **W8** | 界面按 mockup v2 重做 + 原语换成「一个任务 = 一段持续会话」 | 🔵 **主体已提交，剩 4 条验收线**（§5） | `task.yaml` 里仍写 `planned`，见 §8 |
| **W8-b** | 会话状态机从 7 个状态收窄到 4 个 | ⬜ planned（依赖 W8） | 承接 CR-45 的拍板 |
| **W3-a** | **打通**——远端项目能用起来（Agora 在 Windows，项目在 AI 服务器） | ⬜ planned | **「能不能每天用」的分界线**；被环境前提堵着 |
| **W3-b** | 加固——按实际卡住的地方排，不按计划排 | ⬜ planned | 顺序由 W3-a 上线后的真实卡点决定 |
| **W5** | Skill Registry（三级晋升 + Claude 格式为源、单向编译到 Codex/Kiro） | ⬜ planned | |
| **W6** | Desktop 套壳 | ⬜ planned | |

### 4.1 W3-a 的验收线（下一个大件，逐字）

1. 在 Windows 上把 AI 服务器上的一个目录注册成项目 → 起一个 Codex run → 看到它的输出和它改了哪些文件 → 发一条笔记纠正方向，**全程不用打开服务器的 shell**
2. Run 在远端 **detached** 启动（不是 ssh 连接的子进程）：**合上笔记本、过一会儿再打开，run 还在跑或已经跑完**，输出与交付都在。**这一条不是优化，是「能不能每天用」的分界**
3. 远端读取失败一律呈现为「不知道」而非「没有」——**包括「run 还活着吗」这个问题**

**当前的技术障碍（已认领，不是意外）**：派发器现在是「握着子进程」的模型（`execution/dispatcher.py` 的 `self._active: dict[str, asyncio.subprocess.Process]` + `stdout=PIPE`），agent 若跑在从笔记本发起的 ssh 连接里，**笔记本一睡 run 就死**。
**当前的环境障碍**：目标服务器上一个 agent CLI 都没装，装 + 登录只有使用者到公司才能做（清单在 `<workspace>/tasks/agora/COMPANY-CHECKS.md`）。所以这一刀**不是写不出来，是最后一步验不了**。

---

## 5. W8 剩下的验收线（逐条：怎么验、谁能验）

W8 的六条验收线里**三条已满足**——流式输出、四屏导航、W7 任务进工作台（`cbd2748`）。剩下三条，其中两条只有人按得出来、一条要写代码：

### ① 连发三条消息（**只有使用者能证**）

> 打开 Agora 进到一个任务，**像用本地 gpt/codex 一样连着说三轮**：说一句它接着做，再说一句它记得上一句，中途看得到它在改哪些文件；全程不需要先想清楚一整件事再「派活」。

- **为什么我证不了**：浏览器路径这边没有可用的 Chrome 扩展连接。所以所有判断都被抽到 `frontend/src/lib/conversation.ts`（392 行）用 node 测试覆盖——**组件本身在本仓库跑不起来测试**，那是 CR-18 记的坑。
- **「窗口里真的能连着说三轮」只有人按得出来。** 操作脚本见 §9。
- 附带的三条**浏览器债**（node runner 挂不起 next/link 的客户端导航，只有 `shouldInterceptClick` 谓词被抽出来测了）：
  1. 点 workspace 任务行的「打开项目」，筛选是否**真的**跳过去
  2. Ctrl/Cmd + 点，是否**真的**开新标签页
  3. 点完之后 URL 里是否**残留了别的项目的 `task=`**

### ④ 改动文件 / 门禁 / 复核重试的嵌套要显示在**对话流里面**（要写）

> 现状：它们在**右栏**（`RunColumnPane`：跑着 = 实时输出，结束 = 改动 + 门禁）与**中栏的「时间线」页签**（`TimelineColumn`，含 `RunWorkUnit` 的缩进嵌套）。
> mockup 要的是**一条 Run 卡片流**：`› prompt` + 终端输出 + 改动文件 + **缩进挂着的复核/重试**，底部消息框，右栏只放两态。
> 也就是说**东西都在、位置不对**——CR-17~CR-30 那一整批诚实性工作没有丢，它们在右栏。

这是唯一一条还要写代码的验收线，也是 §6.2 里那条结构性差异的另一半。

### ⑤ claude 与 kiro 各跑一次三轮实测（要跑）

只对 **codex** 证过端到端。另两家的 argv 与事件解析器**有单测**（用实测抓下来的真实事件行喂），但**端到端没证**。

### ⑥ ~~W7 的 workspace 任务进「项目工作台」~~ ✅ 已提交 `cbd2748`

不再待在设置页的一张卡片上。

---

## 6. 与 mockup v2 的差距（CR-53 的清单）

### 6.0 先说清一件事：**mockup 我读不到**

- 链接：`https://claude.ai/code/artifact/e8babf4a-084f-48b8-bf8b-10bcbf91023a`
- **2026-09-10 与 2026-09-11 两次尝试都失败**。今晚拿到的是更明确的原因：**这个 artifact 属于你的另一个组织**，当前会话登录的不是那个组织，**在你运行 `/login` 切到那个组织之前，本会话任何工具都到不了它**。
- 本仓库里唯一一个 `mockup.html`（`.agora/workspaces/claude/mockup.html`）**不是它**——那是一个酒店预订的演示页面（agent 跑出来的产物）。
- **所以下面这份对照，比的不是 mockup 本身，而是 2026-08-27 mockup 评审的定案记录**（`RESUME.md`「UI / 交互决策」一节，那是目前唯一的权威记录）。**凡是只存在于图上、没被写进那一节的差异，这份清单一定漏掉了。**
- **要我按图逐屏对，需要你二选一**：(a) 运行 `/login` 切到那个组织；(b) 把 mockup 导出/另存一份放进仓库（建议 `docs/mockup-v2.html`，这样它就不会再丢）。

下面每条都标了 **[结构性]**（信息架构不同，要动代码结构）/ **[布局]**（东西都在、位置不同）/ **[缺件]**（功能尚未建造）/ **[措辞]**。

### 6.1 Today（screen 1，`src/app/page.tsx`）

| # | 定案要求 | 现状 | 差距 |
|---|---|---|---|
| 1 | **三档撑住多项目**：今天主推（**最多 3 张大卡**）/ 在推进但非重点（紧凑行，**一键提升**）/ 休眠（**折叠计数**） | **没有三档聚焦选择器** | **[缺件]** 页面自己的注释写明了原因：这是**使用者做的选择**，Agora 得有地方存它，而**现在没有存的地方**；「一个刷新就蒸发的选择器比没有更糟」 |
| 2 | 全部项目的库存视图另开「项目」页；Today 回答「今天推什么」，项目页回答「我都有什么」 | Today **读的是 task，不是磁盘**，看不到没人登记的项目；库存仍在 `/portfolio`，而 `/portfolio` **不在导航里** | **[结构性]** 见 §6.2 第 1 条 |
| 3 | 进度 = 进度快照驱动，**百分比只报 ✅**，🔵 不折半，无快照留空 | ✅ 已实现（`lib/progress-snapshot.ts`） | — |
| 4 | 已砍：Today 的「换个 agent」按钮 → 换成「先看看这个项目」 | ✅ 已按定案 | — |
| 5 | 「知道存在但读不到」必须和「不存在」显示成两回事 | ✅ 一次失败或被截断的读取一律进 `unknown`，永不进「没有事在等你」（`lib/portfolio-bucket.ts`） | — |

### 6.2 项目工作台（screen 2，`src/app/workspace/page.tsx`）

| # | 定案要求 | 现状 | 差距 |
|---|---|---|---|
| **1** | **CR / ADR 回左栏**（项目级信息不该常驻任务级右栏，左栏已有入口）；W4 的验收线更强：「**OPEN 的 CR 在项目视图里显眼到不用去找——那是防偏离机制在产品里唯一的执行点**」 | 工作台左栏显示的是**别人 workspace 任务**的 CR/ADR 计数（W7 的只读读取）。**Agora 项目自己的** CR Log（`.agora/development/CR-LOG.md`）与 ADR（`.agora/decisions/`）只画在 **`/portfolio`**，而它**已被撤出一级导航** | **[结构性] ⚠️ 我建议这条排第一**。W4 造出来的差异化机制，现在**只能靠手敲 URL 到达**。这正是本项目自己的规则在说的事：*够不到的质量等于没有* |
| 2 | 中栏是**一条 Run 卡片流**：`› prompt` + 终端输出 + 改动文件 + **缩进挂着的复核/重试**，底部消息框 | 中栏是**两个页签**：「对话」与「时间线」。嵌套在时间线里，改动/门禁在右栏 | **[结构性]** = 验收线 ④ |
| 3 | 右栏**两态**：Run 跑着 → 实时终端；Run 结束 → 本次改动 + 提交门禁 | ✅ 已实现（`LiveRun` / `SettledRunChanges` / `GateBlock`） | — |
| 4 | Run **之间不画连线，用嵌套**：review/retry 缩进挂在所属 worker Run 下，顶层只排 worker | ✅ 已实现，且**挂不上时会说明原因**（父级未加载 / 指向的 run 不存在 / 指向自己 / 链未落到根，四种各一句话） | — |
| 5 | **执行者与环境在派活那一刻选**（消息框旁下拉），不进导航、不进 Today | ✅ 适配器下拉在消息框旁；**「环境」还没有可选项**（只有 `LocalEnvironment`） | **[缺件]** 环境那一半要等 W3-a |
| 6 | W7 的 workspace 任务出现在工作台里 | ✅ `cbd2748` | 但**只读**，没有「接着干」的动作 → CR-49 |

### 6.3 环境（screen 3，`src/app/environments/page.tsx`）

- **定案/图上**：三台机器——本机、一台 VPS、一台「知道存在但连不上」的。
- **现状**：**三台里两台没有数据源**。没有环境注册表；`backend/agora/environments.py` 只定义了 `run` + `probe` 的接缝（W2）与**唯一一个实现 `LocalEnvironment`**。注册远端主机是 W3-a，而 W3-a 没建。
- **这一屏的做法是刻意的**：只画真实读到的那一个环境，并**明说另外两台还不能注册**——不画一张灰掉的卡片假装那台机器存在。**[缺件]**，等 W3-a。
- 同批定案里**已降级 backlog** 的：GPU / 内存实时占用（要常驻探针，且目前没有「哪台机器现在空」的真实判断需求）。

### 6.4 技能库（screen 4，`src/app/skills/page.tsx`）

- **定案/图上**：18 条技能分三级，按「能不能公开」分堆。
- **现状**：**Agora 一条都没读。** `backend/agora/skills/` 是**空包**（v0.1–0.4 的抽取器随 council 时代代码一起删了，没有 reader 接替）。图上那 18 条其实是使用者 claude-workspace 里的 `lessons/*.md`，**没有任何 Agora 出口打开过它们**。
- 这一屏现在展示**三级模型本身**（那是真实的、被记录的决策），并明说：清单尚未读取、文件实际在哪、建它要做什么。**一页看起来很像真的行，是本项目唯一严禁的东西。** **[缺件]**，等 W5。
- 同批定案里已定的两条规则（W5 要遵守）：
  - **技能公开：先分类，别指望脱敏**——机械扫描抓不到最危险的那类：**业务流程本身**（一段流程描述里可以一个 IP 都没有）；且一条需要脱敏才能公开的 lesson，脱敏后往往没剩下价值。判据：**能不能给一个完全不知道你公司在做什么的人看，他读完仍然不知道**。
  - **「被用 N 个项目」不做**，换「安装在 N 个」+「最后更新」——Claude 的 skill 由模型自主决定加载，Agora 在进程外拿不到真信号；**假精度比没有更糟**。

### 6.5 小结：差距分成三堆

| 堆 | 条目 | 该怎么排 |
|---|---|---|
| **[结构性]**（要动信息架构） | ① `/portfolio` 的 CR/ADR + 库存不在导航里 ② 中栏两页签 vs 一条 Run 卡片流 | **这两条就是「差距太大」的真身**。我建议 ① 先做（它让已经造好的差异化重新够得到，成本最低）、② 紧随（它是验收线 ④） |
| **[缺件]**（尚未建造，有里程碑归属） | 三档聚焦（缺存储）、环境注册（W3-a）、技能清单（W5） | 按里程碑走，**不要在这一刀里假装补上** |
| **[未量]** | **只存在于图上、没写进定案记录的一切** | 需要你先解决 §6.0 的读取问题 |

---

## 7. OPEN 的 CR（5 条，全部等拍板）

CR = 变更请求。规则：**接受一个 creep，必须先改 `task.yaml` 的 goal + RESUME 的 ANCHOR，然后才动手**——否则记录说 A 而工作是 A+B。

| CR | 提出 | 问题 | 类别 | 我的建议 |
|---|---|---|---|---|
| **CR-49** | 09-10 使用者 | **workspace 里的任务是只读镜像，没有「拿过来接着干」的动作**。原话：「那 claude-workspace 里面的任务 不能直接拿过来继续开发阿玛？」已核实代码里没有任何收编入口（`adopt` / `import_task` / `from_workspace` 全空） | creep | 三个形态：①写回 RESUME.md（撞 `adr/0003`，最大一刀）②**「以这个 workspace 任务为起点新建 Agora 任务」**——预填 goal / 下一步 / 关联仓库，两边各自演进、不假装同步 ③只把话说清楚。**倾向 ②，今天就能做** |
| **CR-50** | 09-10 使用者 | **新建任务必须先懂 `project` 这个概念，而入口没解释它**。原话：「我就随便新建一个任务不行吗？」已核实**不行且是硬校验**：`project_id` 是 `Field(min_length=1)`，路由再挡一层 `422 Unknown project_id` | refine | **约束本身有理由**：任务不携带「在哪儿跑」——root、worker/reviewer、worktree 往哪 provision，三者都挂在 project 上（ANCHOR 的「Project-first, Agent-second」）。**所以缺的不是功能而是解释**：在选择器旁写清「project 决定 agent 在哪个目录里跑」。⚠️ **别顺手把 project 变成可选**——那会让产品无法回答「这活在哪台机器的哪个目录里跑」 |
| **CR-51** | 09-11 使用者 | **新建任务的 project 默认值是「字母序第一个」，于是默认指向 `agora`——也就是 Agora 自己这棵源码树**。原话：「新建任务的时候 项目为什么是agora?」`/api/projects` 用 `sorted(...)` 返回，而 `workspace/page.tsx:499` 取 `projectIds[0]`，**不是**后端同一响应里已给出的 `current` 指针 | bug | ①改用 `current`（本机也是 `agora`，**治不了症**）②**默认留空，强制选一次**（最诚实）③选择器旁把 root 路径显示出来。**倾向 ②+③**：问题不是选错了谁，而是**它无声地替你选了一个会动到源码的目录** |
| **CR-52** | 09-11 使用者 | **设置页只能改「新项目的父目录」，改不了某个项目的 root，也没法把一个已存在的仓库注册成项目**。原话：「那我不能自己在设定页面设置项目放哪个目录吗？」`create_project` 里 root = `projects_base/<id>` 写死，`CreateProjectRequest` 没有 `root` 字段，且**明确拒绝接管已存在的非空目录**；能指定任意 root 的路径只有手写 `.agora/projects.yaml` | creep | **约束是有意的**：控制平面自己持有目录布局才敢往里 provision worktree。①设置页把 `projects.yaml` 做成可编辑 ②加「注册已有仓库为项目」入口（要求已是 git 仓库、worktree 放它自己的 `.agora/` 下、明说 Agora 不改它的文件）③**先把话说清楚**（那个输入框现在的文案会误导）。**倾向 ③ 立刻做 + ② 单独排一刀** |
| **CR-53** | 09-11 使用者 | **页面与 mockup v2 差距太大** | UNCLASSIFIED（**还没量完，所以不分类**） | 见 §6。**下一步不是改界面，是先把差距量准**：§6.0 的读取问题解决 → 按图逐屏补齐清单 → 你挑哪几条算数。⚠️ 「差距太大」是一句判断，不是一份清单 |

**CR-49 ~ CR-52 指向同一件事**：产品在你想干活之前，**先要求你把它的世界观填完整**。

---

## 8. 记录本身的一处不一致（读的人请注意）

`task.yaml` 里 **W7 与 W8 的 `status` 都还写着 `planned`**，而 `RESUME.md` 记的是 W7 两半都已完成（2026-09-06 / 09-07）、W8 主体已提交（`4fb5a3a` + `cbd2748`）。

**以 RESUME 为准**；`task.yaml` 的 status 待回填。

这类不一致本身就是要修的东西——本项目的规则是「**记录为真**」：记录说 A 而实现是 A+B 时，要么修实现，要么修记录，**不允许两者并存**。（我今晚没有顺手改它：里程碑状态的回填该和 W8 收口一起做，避免把「还剩四条验收线」的 W8 标成 done。）

### 8.1 已被明确接受的缺口（不是遗漏，是拍过板的）

| 缺口 | 决定 | 会怎么回来找我们 |
|---|---|---|
| **停止一轮的进程树，POSIX 上没做** | 接受为已知平台差异（Windows 已用 job object 覆盖并实测，含父进程已退出的情形；POSIX 要改每个 run 的信号投递方式，本机没环境可量） | 一条 **skip 的测试**作为触发物，W8 收口时移交 Agora 的 tracker |
| `role=unknown` 的旧会话没有「认领归属」的动作 | 进 backlog；拒绝信息如实说明了这一点 | CR-47 行 |
| codex 的 `bridge_mode` 保持 `cli` | 换回 app-server 能拿双向审批，但**每轮从零开始**，与「持续会话」冲突。双向审批另开一刀 | CR-44 行 |
| 会话状态机 7 个状态 × 2 条写入路径 | 收窄到 4 个，但**排在 W8 验收线做完之后** | 已挂成里程碑 **W8-b**（不再是一句没有承接的决定） |
| agent 沙箱写出的文件读不到内容 | 真做时**先量** `snapshot_changes` 记的是「不知道」还是「缺失」——若是后者，那才是真正危险的地方 | CR-43 行（第 31 轮已顺手改了一半：判断文件在不在改用 `lstat`，「读不到」不再被记成「已删除」） |

---

## 9. 明早验收清单（照着按）

### 9.0 起服务（两条命令，见 §3.7）

期望：后端日志打出 `control-plane database D:\Projects\Agora\data\agora.db`；前端 `Ready in ~3s`。

### 9.1 先建一个任务

1. 打开 <http://127.0.0.1:3000/workspace> → 点「新建任务」。
2. ⚠️ **把「项目」从 `agora` 改成 `demo`。** 这不是洁癖：project `agora` 的 root 就是 `D:\Projects\Agora`——**我们正在改的这棵树**，agent 在里面跑会动到它。（这就是 CR-51。）
3. 三个问题随便填一个能验证的小活，例如：
   - 1 你想完成什么：`在 demo 里建一个 hello.txt，写一行字`
   - 2 什么算完成：`hello.txt 存在且有内容`
   - 3 有哪些不能做：`不要改动别的文件`
4. **顺便验一件事**：「3 有哪些不能做」目前**只被拼成一段描述文字**（`description`），**不是**结构化的约束字段——门禁与复核都不会拿它当判据。界面把三个问题并列问，是否让你误以为三条同等生效？（这一条我还没记成 CR，等你说算不算。）

### 9.2 验收线 ①：连着说三轮

| 步 | 你做什么 | 期望看到 | 看到别的说明什么 |
|---|---|---|---|
| 1 | 在对话框发第一条：`先看一下这个目录里有什么，别改东西` | **几秒内开始出字**（流式），不是空等三分钟 | 长时间空白 = 流式路径断了（`GET /runs/{id}/output?offset=` 或前端轮询） |
| 2 | 发第二条：`刚才你看到了什么？照着建 hello.txt` | 它**记得上一句**（真的走了 resume），中途看得到它在改哪些文件 | 它重新问「什么目录？」= 会话没续上，而**这时产品必须明说「读不到会话」，绝不能静默新开** |
| 3 | 发第三条：`在 hello.txt 里再加一行` | 仍在同一段会话里 | 同上 |
| 4 | **关掉浏览器标签，重开** `/workspace`，进同一个任务 | 上下文还在，接着上次继续 | 若变成一段新对话且**没有说明**，那是 W8 的核心不变量破了——这是最严重的一类失败 |

### 9.3 三条浏览器债（一分钟）

1. 在左栏 workspace 任务块里点某行的「打开项目」→ **筛选是否真的跳过去**（不是链接看起来变了但列表没动）。
2. **Ctrl/Cmd + 点**同一个入口 → 是否**真的**开新标签页。
3. 点完看地址栏：**URL 里是否残留了别的项目的 `task=`**（应该没有）。

### 9.4 看四屏，对着 §6 挑刺

`/`（Today）、`/workspace`、`/environments`、`/skills`。
**重点看两处结构性差距**是否就是你说的「差距太大」：
- 「CR / ADR / 项目库存」要靠手敲 `/portfolio` 才到得了（§6.2 第 1 条）；
- 中栏是「对话 / 时间线」两个页签，而不是一条带缩进的 Run 卡片流（§6.2 第 2 条）。

若你说的差距**主要不是这两条**，那就说明 §6 的清单确实漏了图上的东西 → 回到 §6.0，二选一让我读到 mockup。

### 9.5 要你拍板的（按顺序）

1. **CR-53** 的下一步：先量清差距（`/login` 或把 mockup 存进仓库），还是直接按 §6 已量到的两条结构性差异开工？
2. **CR-51 + CR-50 + CR-52③** 能不能打成一刀（默认值不替你选 + project 说清楚 + 那个目录输入框改文案）？我倾向可以，都是小改动、同一个入口。
3. **CR-49** 选哪个形态（我倾向「以 workspace 任务为起点新建 Agora 任务」）。
4. **CR-52②**（注册已有仓库为项目）要不要单独排一刀。
5. **验收线 ④**（改动/门禁进对话流）现在做，还是等 §6 的清单齐了一起动信息架构？——我建议**一起动**，否则这块要改两遍。

---

## 10. 走到今天的代价（给「为什么慢」一个可核对的答案）

**W8 这一刀：32 轮评审、154 条 finding —— 153 条成立并修完，1 条误报。**
第 1~4 轮由 codex 做，**第 5 轮起换另一个 reviewer 顶上**（codex 配额耗尽；这是「替代」而不是「第二意见」，提交信息里写明了）。

三条最贵的教训（都已沉淀成跨任务的知识）：

1. **「我加了守卫」和「守卫在那条路径上会被执行」是两件事。** 至少两次：双 session id 的 fail-closed 加在了**流式阶段永远进不去的分支**里；`mark_lost` 只更新不插入，**行不存在时静默失败**。
2. **「加了超时」不等于「超时真的会到」。** Windows 上挂起的管道读取**不响应取消**，`wait_for` 会先取消再等它结束——实测**仍等满 90 秒**。正确做法是**放弃而不是取消**。
3. **量，不要推。** 曾推理过「保留的尾巴比密钥长，所以切口不可能落在密钥内部」；写脚本把密钥在切点附近移动 161 次，**53 个偏移量下密钥被切成两半明文落盘**。

同一刀里最贵的**设计**教训（评审给出、已修）：

- **声明范围 > 检查范围，是这个仓库的头号罪。** 例：`decide()` 不看会话 state，已标 unreadable 的会话仍会被再次派发，而前端是 block 的。修法不是加一个 state 检查（那会造成新陷阱），而是**把状态拆成两类**：条件类（每轮重新推导、会自愈）与结论类（供应商没认这个 id，必须显式新开）。
- **独立评审会继承被评审者的上下文**：会话键原本是 `(task_id, adapter)`，而 worker 与 reviewer 用同一适配器是正常配置——于是自动评审会 resume 实现者那段会话，「不自己审自己」被会话机制从背后拆掉。修法：**role 进主键**。
- **凭据泄露两层**：claude/kiro 按 token 分片流出文本，`sk-…` 被拆成多个事件、每片单独无害、日志再拼回原文。修法：写入前按行缓冲（带 512 字符重叠）+ API 返回切片时再脱敏一次，**两层各自写明覆盖范围**。

---

## 11. 文件地图

| 内容 | 位置 |
|---|---|
| 目标 / 里程碑 / 验收线（权威） | `<workspace>/tasks/agora/task.yaml` |
| 逐日状态 / CR Log / 坑（权威） | `<workspace>/tasks/agora/RESUME.md` |
| 到公司才能做的事 | `<workspace>/tasks/agora/COMPANY-CHECKS.md` |
| 本项目的架构决策 | `<workspace>/tasks/agora/adr/`：0001 重定位 · 0002 控制平面库锚定 · 0003 **Environment = run+probe（故意没有写文件能力）** · 0004 CR Log/ADR 住在项目仓库 · 0005 charter 权威在仓库、UI 只读 · 0006 workspace 任务与仓库的关联是查表不是主键 · 0007 **原语变成对话** · 0008 **会话续接与流式记录的机制** |
| 工程日志（最细） | `.agora/development/PROGRESS.md` |
| 产品要读的那份 CR Log / 决策 | `.agora/development/CR-LOG.md` / `.agora/decisions/` |
| 项目注册表（**唯一能给项目指定任意 root 的地方**） | `.agora/projects.yaml` |
| 三个适配器 / worktree 隔离 | `backend/agora/execution/adapters.py` / `backend/agora/workspaces/provisioner.py` |
| 会话 / 转写 / 增量输出（W8 新增） | `backend/agora/execution/{sessions,transcript,runlog}.py` |
| W7 的 workspace 任务 reader | `backend/agora/projects/workspace_tasks.py` |
| 对话窗口的全部判断（可测，392 行） | `frontend/src/lib/conversation.ts` |
| 唯一算数的续接证据 | `backend/tests/test_session_continuity_live.py` |
| 一级导航的定义与「为什么撤掉那几屏」 | `frontend/src/components/delivery-shell.tsx` |
