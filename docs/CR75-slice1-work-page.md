# CR-75 第一刀 — 工作页 + 重启恢复（设计说明）

> 承接 workspace **CR-75**（10-30 用户接受：Agora 改定位为三家 agent 的统一工作流平台）。
> 样张：https://claude.ai/artifact/NeG3BPvLtvhAK5QipyvTar 的 ② 工作页。
> 第 1 版送 codex 设计评审（10-30），CHANGES_REQUESTED 12 条，全部接受 → 第 2 版，改动标 `[R#]`。
> 第 2 版第二轮评审 CHANGES_REQUESTED 11 条，全部接受 → 本文第 3 版，改动标 `[S#]`。
> 设计评审到此为止，剩下的由代码评审门禁（`/codex-review`）把关。

## 用语（10-30 与用户统一）

- **项目** = 一条 workspace 任务记录（claude-workspace / kiro-workspace 的 `tasks/<name>/`），例如
  `agora-private-refactor`。工作页左栏一行 = 一个项目，右边一个终端 = 这个项目的一次 `cw open`。
- **任务** = 项目里的一件事（CR-71、CR-72……）。同一项目内任务一件做完再做下一件，不并行。
- **仓库** = 项目的属性（`task.yaml` 的 `primary_repo:` 等）。Agora 现有的「注册项目」在新用语里是仓库。

代码里的 `project_id`（Agora 注册的仓库）意思不变，本刀不改名；新东西一律叫 `work project`。

## 这一刀要让用户能做到的事（exit criteria，用户亲手验）

1. 打开 Agora →「工作」页：左栏列出我所有活跃项目；点一个，右边整块就是这个项目的终端
   （claude-workspace 项目 = 带 `CW_TASK` 的 claude；kiro-workspace 项目 = 它的 kiro agent）。
2. 点另一个项目，右边换过去；前一个在后台继续跑，切回来屏幕还在（现有的回放缓冲）。
3. **重启电脑后**再打开工作页：上次开着的 claude 项目自动接回各自**最后记录到的**那段对话
   （`claude --resume`），顶上横条说清楚每个项目接回了没有、没接上的为什么。
4. 中途在终端里 `/clear` 过的项目，接回的是 `/clear` 之后那段对话。

不在这一刀（Reach before polish 往后排；但**不能说假话**这一条现在就做）：
在干活/等你 状态灯、底部 agent 状态栏、任务下拉切换、右键菜单、远端项目的恢复、codex 项目、
概览页、项目详情页。

## 现状（能复用的）

- `agora/terminal/manager.py`：本机 PTY（Windows job object 兜底）、远端 SSH PTY、256K 回放缓冲、
  多窗口 attach。**服务器一停，终端全没了**——这是本刀要补的。
- `agora/terminal/targets.py`：按仓库找出指向它的 workspace 任务以及打开方式
  （claude：工具目录 + `CW_TASK`；kiro：`kiro-cli chat --agent <task>`）。
- `/terminal` 页：顶上一排标签，下面一个终端。工作页取代它的日常用途；`/terminal` 保留不删。
- 终端令牌：`terminal_router` 是普通 `APIRouter`，令牌依赖**逐条**挂在路由上，不是自动继承 [R11]。

## 设计

### 1. 左栏项目从哪来

新函数 `work_projects(sources)`：遍历已注册的 workspace 源（`registry.workspace_sources()`）下的
`tasks/*/`（`_` 开头的目录——含 `_archive`——照旧跳过）。每项：`key`、`source`、`task`、`title`、
`workspace`（claude/kiro）、能否打开以及不能的原因（复用 `claude_tool_dir_for` / `kiro_agent_for`）。

- `status: active` → 列出；`status` 缺失 → 也列出（没写 = 没归档）；其他值 → 不列，底部一行
  「另有 N 个项目不是 active」。
- **读不了的都要说出来** [R12][S11]：没有 `task.yaml`、读文件失败、YAML 语法错、解析出来不是映射
  （列表 / 标量 / 空文档）——各列在「读不了的项目」，带原因。工作页用**自己的扫描**（`agora/work/projects.py`），
  不改 `_task_documents`：导入页「没有 task.yaml 就是没指向仓库」的语义保持不变。

`key` = `sha256(normcase(source) + "\0" + task)` 前 16 位十六进制。服务器内部用来命名记录和文件，
请求只带 `key`，服务器查回 source/task，**从不从请求里拿路径**。

### 2. 打开 / 关闭，以及「意图」先落盘 [R3][R4][R5][R10]

记录：控制面 SQLite 新表

```
work_sessions(key PK, source, task, workspace,
              want_open INTEGER,      -- 用户的意图：1 开着 / 0 关了
              -- 当前这次启动（每次启动换新）
              launch_id TEXT, launch_mode TEXT,     -- fresh / resume
              expect_session TEXT,                  -- resume 时期望接回的会话 id
              shell_pid INTEGER, shell_created REAL,-- 这次启动的 shell 进程与其创建时间 [S3]
              launched_at REAL,
              -- 最后一个合格的恢复点，与「当前启动」分开存 [S2]
              ck_session TEXT, ck_transcript TEXT, ck_launch TEXT, ck_at REAL,
              ck_stale INTEGER,                     -- 恢复点之后有报不上来的会话切换 [S9]
              updated_at REAL)
```

**恢复点（checkpoint）只由服务器写**：服务器读到合格的上报（§3）就把它推进到 `ck_*`，并且**先提交恢复点，
再改写 launch 相关列** [S2]。所以一次中断的恢复（新 launch 已写、还没上报、服务器就死了）不会冲掉上一次能用的恢复点。

**所有** work 操作（open / close / restore）走同一个服务（`agora/work/service.py`），
服务内一把 `asyncio.Lock`（单进程控制面；整个服务一把锁，够用且简单），锁内**重新检查**意图与
活终端，再动作。所以自动恢复和左栏点击不会给同一个项目开出两个终端，恢复也不会把用户刚关掉的项目又打开。

顺序（意图先落盘，进程后动）：
- **启动是服务自己持有的操作** [S4]：每个 key 至多一个「启动中」的 `asyncio.Task`，由服务创建、
  调用方 `await asyncio.shield(...)` 等它；请求被取消不影响启动继续、也不释放「启动中」标记，
  并发的 open/restore 看到「启动中」就等同一个结果。锁只保护检查与登记，不跨线程里的 PTY 创建。
- **open**：锁内 → 已有活终端或启动中就返回/等它 → 否则写 `want_open=1`、新 `launch_id` → 起终端 →
  失败则把这次结果记为 `failed`（`want_open` 保持 1，下次恢复再试；横条照实说）。
- **close**：锁内 → 先写 `want_open=0` → 再关终端。服务器在两步之间被杀：下次不会恢复它
  （终端本来也随服务器没了）。
- **只有用户亲手关才写 `want_open=0`**：程序自己退出、服务器停止（`close_all`）都不写。
- **入口统一** [R5]：现有 `DELETE /terminals/{id}`（`/terminal` 页的关闭按钮）遇到
  `project_id` 以 `work:` 开头的终端，转给 work 服务的 close；`TerminalManager.close()` 本身不碰
  `work_sessions`（`close_all` 也走它）。
- **项目已不在活跃列表里** [R10]：`want_open=1` 的记录照样出现在恢复结果里，结果为
  `unavailable`（「这个项目已不是 active / 读不了，没有打开」）；按 `key` 关闭已有记录不要求它还在活跃列表里。

进程归属 [R4][S3]：Windows 上 `_start()` 先起进程再放进 job object，放不进去（返回 None）或服务器
在两步之间死掉时，旧进程可能还活着。本刀**不自动开第二个**：每次启动记下 shell 的 pid 与创建时间；
恢复前查这个 pid 还在且创建时间对得上（Windows：`OpenProcess` + `GetProcessTimes`；POSIX：
`/proc/<pid>/stat` 的 starttime；不需要新依赖）→ 结果 `maybe_running`（「上次的进程可能还在跑（pid N），
没有自动接回」），不起新的；用户在左栏点开 = 明确要求新开，照常起。查不了（权限 / 平台）→ 也按
`maybe_running` 处理，宁可不开。job object 本身的缺口仍记 `defer`（CR-76）。

### 3. 会话 id 从哪来：Agora 注入的 hook（借鉴 Orca，已实测）

`/clear`、`/compact` 后 claude 会换会话 id，所以用 hook 持续上报，而不是启动时钉一次。

- Agora 在数据目录写 `<data>/work-hooks/settings.json`，含 `SessionStart` 与 `SessionEnd` 两个 hook，
  命令是 Agora 自带的上报脚本（`agora/work/report_hook.py`，复制到 `<data>/work-hooks/` 下，用服务器
  自己的 Python 绝对路径调用）。
- **hook 命令的第二层 shell** [S1]：hook 命令由 claude 交给 shell（Windows 上是 Git Bash）执行。
  Python 路径与脚本路径都只接受字符白名单 `[A-Za-z0-9 :/._-]`（统一写成正斜杠），不在白名单里就不装 hook，
  项目照常打开，恢复结果说「这个安装路径里有特殊字符，不能记录会话」。白名单之内放进双引号
  在 bash 与 PowerShell 里都是字面量，不靠转义。测试里放空格、`'`、`$`、反引号、中文，断言被拒或逐字到达。
- 起 claude 时加 `--settings <该文件>`；PTY 环境里设 `AGORA_WORK_DIR=<data>/work-sessions/<key>`、
  `AGORA_WORK_KEY=<key>`、`AGORA_WORK_LAUNCH=<launch_id>`。`CW_TASK` 仍走环境变量。
- **目录只给本人** [S5]：`<data>/work-hooks/` 与 `<data>/work-sessions/` 建好后用 `terminal/auth.py` 已有的
  `_restrict_to_owner` 同样做法（Windows `icacls /inheritance:r /grant:r <user>:(OI)(CI)F`；POSIX `0700`）；
  限不住就**不装 hook**并照实说，与终端令牌「限不住宁可失败」一致。上报是**本机运行时的声明，不是防篡改证据**：
  界面用语是「记录到的」，不是「已验证的」。
- **已实测（10-30，claude 2.1.314，Windows，工具目录 + `CW_TASK=work-chat`）**：
  ① `--settings` 的 hook 与工具目录 `.claude/settings.json` 的 hook **合并执行**（任务照常注入，
  上报文件也写出了）；② hook 命令 `"<python 绝对路径>" "<脚本绝对路径>"`（正斜杠、双引号）能跑；
  ③ `--resume <id>` 后 SessionStart 以 `source=resume` 触发，报同一 id；④ hook 输入带 `cwd`。
  未实测：交互模式 `/clear` 后再报一次（真机验收那一行覆盖）。

上报 = **每次启动一个只追加的日志** `<data>/work-sessions/<key>/<launch_id>.jsonl` [R6][S6]，脚本只追加、
从不改写或截断；一次启动的事件很少，不需要轮转。每行
`{v:1, key, launch_id, event: SessionStart|SessionEnd, source, reason, session_id, transcript_path, cwd, at}`
（`reason` 是 SessionEnd 的 clear / logout / prompt_input_exit / other [S7]）。拿不到 `session_id` 也写一行
（`session_id: null`），「上报失败」本身可见。服务器推进恢复点后，删掉既不是当前 launch、也不是恢复点
来源 launch 的旧日志。

读取时校验 [R2][R9]（服务器读，`agora/work/reports.py`）：
- 每行独立解析，坏行跳过并计数；
- `session_id` 必须是规范小写 UUID（正则）；`key` 必须等于本项目；`launch_id` 必须是本记录**曾经**发出的
  （只保留最近一次；旧 launch 的行只用于「最后记录到的会话」，不用于确认本次启动）；
- `transcript_path` 必须在 `~/.claude/projects/` 之下（规范化后做包含检查），否则视为不可信；
- 合格的 `SessionStart` 行推进恢复点 `ck_*`；
- **恢复点之后出现的任何不合格的会话切换**——`session_id: null`、非 UUID、`transcript_path` 越界、
  解析不了的尾行——都把 `ck_stale` 置 1 [S9]：恢复照样用 `ck_*`，但横条加「这之后可能还换过会话，
  没报上来」；下一条合格的 `SessionStart` 推进恢复点时清掉。

文件权限：数据目录在用户自己的 profile 下（Windows `%LOCALAPPDATA%` 或仓库 data 目录），本刀不另设
ACL；上报内容不被当成命令执行——id 过正则才会进命令行。

### 4. Windows 命令行的转义 [R1]

`command_argv()` 生成 `pwsh -NoLogo -NoExit -Command "<line>"`，pywinpty 再用 `list2cmdline` 拼成命令行。
新加的两段值：
- 会话 id：只接受 §3 的 UUID 正则，不合格就不 resume（结果 `fresh_untrusted`）。
- settings 路径：服务器自己的数据目录，用 PowerShell **单引号字面量**（内部 `'` 写成 `''`，单引号里
  `$`、反引号都不展开）；路径含 `"` 或控制字符就拒绝起这个 hook（结果照实说），Windows 路径本也不能含 `"`。
- POSIX 侧照现有 `-ic` 走 `shlex.quote`。
- 测试：真用 pywinpty 起一个探针命令（`pwsh` 与 Windows PowerShell 各一次，环境里有哪个测哪个），
  settings 路径里放空格、`'`、`$`、反引号、中文，断言探针收到的参数逐字相同。hook 命令的引号另测。

### 5. 恢复，以及每种结果怎么说 [R7][R8][R9]

触发：**工作页第一次打开时**前端调 `POST /api/work/restore`（服务器启动时不开——Agora 可能随开机
启动而用户当天不开工作页）。服务器内一次只跑一个，重复调用对已处理的项目是空操作。

对每个 `want_open=1` 且没有活终端的项目：

| 启动前判断 | 动作 |
|---|---|
| claude 项目，有「最后记录到的会话」，`transcript_path` 存在 | `--resume <id>`，`launch_mode=resume` |
| claude 项目，有 id 但 `transcript_path` 不在 | 新会话；结果 `fresh_missing` |
| claude 项目，`transcript_path` 不可信 / 位置判断不了 | 新会话；结果 `fresh_untrusted` |
| claude 项目，没有任何记录 | 新会话；结果 `fresh_no_record` |
| kiro 项目 | 新会话；结果 `fresh_unsupported` |
| 项目已不在活跃列表 | 不开；结果 `unavailable` |
| 起终端失败（上限 8 个、PTY、目录没了） | 结果 `failed` + 原因 |

**「接回了」要有证据，不是推断** [R7][S8]：起了终端只代表 shell 起来了。服务器看本次 `launch_id` 日志里的
**第一条** `SessionStart`，确认结果一旦定下就**锁定**（之后的 `/clear` 不改写它，只改左栏的当前会话）：
- resume 启动：第一条是 `source=resume` 且 `session_id == expect_session` → `resumed`（已接回）；
  第一条是 `source=resume` 但 id 不同 → `resumed_other`（「接回的不是记录里那段」）；
  第一条不是 `source=resume` → `not_resumed`（「没有接回，claude 开了一段新对话」）。
- 新会话启动：第一条是 `source=startup` → `fresh_*` 的「已开新会话」才成立。
- 60 秒内没有第一条 → `unconfirmed`（「终端开了，还没确认 claude 起来没有」）——这是状态，不是终点：
  之后来的第一条照样把它改成上面某一种 [S10]。
- `ck_stale` 的项目横条另加一句「这之后可能还换过会话，没报上来」。

横条和左栏只读 `GET /api/work/projects` 返回的这些状态，不自己推断。

左栏状态说的是**终端**，不假装知道 claude 的状态 [R8][S7]：
`终端开着` / `终端已结束` / `没打开`。本次 launch 的事件**按顺序归并**：最后一条是 `SessionEnd` 且
`reason != clear`、**且终端确实还开着**时，才加「claude 已退出，终端还开着」；`/clear` 产生的
`SessionEnd` 后面紧跟新的 `SessionStart`，不算退出。
点一个终端开着的项目 = 切过去看它（不重开 claude）。

### 6. 路由与令牌 [R11]

新 `agora/api/work_router.py`，`APIRouter(dependencies=[Depends(require_terminal_session)])`——**包括 GET**：
`GET /api/work/projects`、`POST /api/work/projects/{key}/open`、`POST /api/work/projects/{key}/close`、
`POST /api/work/restore`。前端请求带终端令牌头（沿用 `lib/terminal.ts` 的 `call`）。
测试：无令牌 / 错令牌各一条，断言不扫描、不写文件、不起进程。

### 7. 前端

- 顶栏导航加「工作」（`/work`）。
- `/work`：左栏 280px 项目列表（名字、标题一行、§5 的终端状态）；右边一个终端（复用 `TerminalView`）占满。
  点左栏 = 打开或切过去。右边顶条：项目名、执行者 · 本机、关闭按钮。
- 横条：恢复结果逐项列出；有没接上或未确认的项目时不自动消失。
- 页面可见时每 5 秒轮询 `GET /api/work/projects`（不可见时停，回到可见立即拉一次）[S10]：
  确认超时之后的状态变化、后台终端结束、claude 退出都能显示出来。
- 选中的项目记在 `localStorage`（读写包 try）。
- 中文 / 日本語 / English 三套文案，键对齐检查。

### 8. 不做 / 明确拒绝的

- 不改 claude-workspace 的 `session-start.sh`，不改用户的 `~/.claude/settings.json`：上报 hook 只活在
  Agora 起的那个 claude 进程里。
- 不读 claude 的会话内容，只看 `transcript_path` 在不在。
- 不在服务器端常驻扫描 `~/.claude/projects`。

## 测试计划

- 后端单测：`work_projects`（active / 缺 status / 非 active 计数 / `_archive` 跳过 / 缺 task.yaml、
  语法错、非映射各列为读不了）；key 稳定；open 幂等；open 与 restore 并发只开一个；
  restore 与 close 并发不复活；close 先落盘；`/terminals/{id}` 关 work 终端走 work close；
  `close_all` 不改意图；restore 每种结果各一条（含 `maybe_running`、`not_resumed`）；`unconfirmed` 超时后再被改写；
  `resumed` 锁定后 `/clear` 不改写；启动中请求被取消 → 不重复启动；中断的恢复不冲掉恢复点；
  上报日志：坏行、非 UUID、错 key、`transcript_path` 越界、`session_id: null` → `ck_stale`；
  `SessionEnd(reason=clear)` 后跟 `SessionStart` 不算退出；pid 检查（活着 / 已死 / 创建时间不符）；
  hook 路径白名单（拒绝 `$`、反引号、`'`）；目录限权失败 → 不装 hook；
  令牌：无/错令牌两条。
- Windows 转义：§4 的 pywinpty 探针测试。
- 真机（用户亲手验那一行）：开两个项目 → 其中一个 `/clear` 后再说一句 → **重启电脑** → 打开工作页 →
  两个都显示「已接回」、`/clear` 的那个接回的是后一段。
- 前端：lint、build、三语键对齐；`tests/test_web_ui.py` 全量。

## 本刀产生的 defer

- CR-76：Windows 上进程放不进 job object 时服务器死后子进程可能活着。本刀已用 §2 的 pid 检查挡住
  「自动再开一个」，剩下的是那个孤儿进程本身没人管；何时变急：真实使用中看到 `maybe_running`。
