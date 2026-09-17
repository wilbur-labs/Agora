# 接下来做什么、怎么做、要多久

> 写给**另一台机器的 agent**（家里 `D:\Projects\Agora`）。2026-09-11 由公司那台
> （`E:\Work\2026\Projects\Agora-private`）写下，当日 HEAD `822b80e`，分支 `feature/w8-session-ui`。
>
> 三份文档的分工：`PROJECT-STATUS.md` 是全局现状（§6 有逐屏差距清单）· `W9-mockup-alignment.md` 是界面
> 信息架构的决策与进度 · `W3a-remote-execution.md` 是远端执行 · **本文件是「下一步照着做」**。
> 决策一旦定下写进前两份，不要只留在某一台机器的 workspace 里。

## 现在在哪

两条线都在这个分支上：

| 线 | 位置 |
|---|---|
| **W9**（界面对齐 mockup） | 四处结构性落点里 **2/4 完成**：左栏项目级导航 ✅、「项目」页 ✅；Today 三档的**后端已通、前端未做**；中栏 Run 卡片流未开工 |
| **W3-a**（远端执行 SSH） | U1 `env-launch` + U2 `remote-env` **已移植到本分支**（`253dd19`）；U3/U4/U5 + Build&Test 未做 |

本机门禁基线（**以此为准，`PROJECT-STATUS.md` §3.6 的数字已过期**）：
后端 `-m "not integration"` = **1443 passed / 9 skipped**（耗时 **39m51s**）· 前端 = **340 tests / 339 pass /
1 skip** · tsc 0 · eslint 0 · `next build` 14 页。

---

## 一、W9 剩余（约 2~3 小时）

### 1. Today 三档前端 —— 下一刀，40~60 分钟

后端已经全部就绪，**不要再动后端**。契约见 `W9-mockup-alignment.md` 的「D-1 已落地（后端）」一节：
`GET /api/projects` 已带 `today_focus: {project_ids, unresolved_ids, limit, is_full}`，
`PUT/DELETE /api/projects/{id}/focus` 升降级、幂等、满员 409。

要做的（mockup 的 screen 1）：

- **今天主推**：≤3 张大卡。满员时显示「3/3 已满 —— 第 4 个要上来得先放下一个」。**上限 3 是故意的**，
  mockup 原话「4 张大卡等于没有重点，那正是这一屏要消灭的状态」。
- **在推进但不是今天的重点**：紧凑行 + 「提升为主推」按钮 → 打 `PUT .../focus`；收到 **409** 就显示满员那句话，
  **不要**自动挤掉一个（后端已经拒绝了，那是刻意的）。
- **休眠**：折叠成一行计数，点开查看。
- 底部「**卡住 / 需要你**」跨全部项目一张单子，右侧标来源 —— mockup 明确说**故意不按项目分组**：「阻塞要一次扫完」。
  这块正好承接第一刀从左栏删掉的跨项目 attention 分组（见下方「欠债」）。

**开工前先做一件事（10 分钟，能省掉返工）**：核实大卡字段的可读性。mockup 大卡有：项目名 · 当前里程碑 ·
环境 · **分支 + commit + 是否已推** · 「昨天/上次」· 「现在」下一步 · 「在册 / 拦路 / 发现」· 目标一句话 ·
进度快照条 · 「继续工作 →」「先看看这个项目」· resume 提示。
`PROJECT-STATUS.md` §6.2 说「后端大多已有」——**「大多」不是全部**。已知现成的：`recap`、next action、
`GET /api/projects/{id}/dashboard`、`lib/progress-snapshot.ts`、会话状态。**没核实过的：分支/commit/是否已推、
环境、里程碑。** 读不到的那些**必须走能力状态槽**（`lib/nav-capability.ts`，六态），不能留空也不能编 ——
mockup 自己也说无快照的项目「留空不编数字」。

其他 mockup 明写的规则，别违反：
- 进度**百分比只报 ✅**，🔵 不折算半分（「一折算就开始骗自己」），无快照留空。
- **砍掉「换个 agent」按钮**（与「Agent 在 Run 层选」自相矛盾），换成「先看看这个项目」= 进工作台但不起 Run。

### 2. 中栏 Run 卡片流 —— 最大一块，60~90 分钟

同时结清 **W8 验收线 ④**。现状是「对话 / 时间线」两个页签，改动文件在右栏；mockup 要一条 Run 卡片流：
执行者 · run id · `exec resume …` · exit · 时刻+时长 · `› prompt` · `$ 命令` · `›` 步骤 · 测试输出 ·
**改动文件（M/A + 查看）** · **缩进挂着的「复核」「重试」子卡** · 脚注（worktree · 分支 · 未提交 · 子 Run 数）
+ 底部消息框 + 执行者/环境下拉。

零件都在，是**重新组合不是重写**：`components/conversation.tsx`(815) · `RunColumnPane` 全家 ·
`RunWorkUnit`（已实现嵌套）· `lib/timeline.ts`(411) · `lib/run-column.ts`(571) · `lib/run-tree.ts`(176)。

两条 mockup 明写的规则：
- **Run 之间不画连线、用嵌套**（连线会变面条图）；关系数据本来就有（`reviews_run_id`）。
- **按工作单元排，不按进程启动次数排** —— 顶层只排 worker run，复核与重试缩进挂在所属 worker 下。

### 3. 壳 / 顶栏 —— 15~25 分钟

mockup：顶栏横排四个 tab（Today / 项目工作台 / 环境 / 技能库）+ 右侧当前环境与三家 CLI 版本。
现状：左侧 sidebar 六项（多 Settings 与「项目」）。

⚠️ 做这一刀时**顺手把「项目」页移到设计给它的位置**。它现在在一级导航里是**临时的**，`delivery-shell.tsx`
的注释已写明理由（Reach before polish：Today 还是任务行时，一个只存在于「重做后的 Today」上的入口等于没人
够得到）。另外那里还有一个**留后**的陷阱：`links` 的 `label` 字段看似展示文本、实际当 active 匹配的内部 ID
（展示文本走 i18n），独立评审建议改成按 `key`/`href` 匹配或重命名 `activeId` —— 但明确说不要为小改动扩大重构。

### W9 的两笔欠债（记录在案，别当没发生）

第一刀从左栏删掉了三样东西，见 `W9-mockup-alignment.md`「这一刀删掉的能力」：

1. **跨项目 attention 分组** —— 按约束属于 Today，**但 Today 还没重做，所以这是迁移未完成，不是纯迁移。**
   做 Today 那刀时把它接上（就是底部「卡住 / 需要你」那张单子）。
2. **搜索框** —— 约束只禁止跨项目筛选器，**并不要求删掉「当前项目内搜索」**。可随时按需恢复。
3. 按活跃度排序 + 相对时间 —— 符合「扁平、安静」的取舍，但确实是信息与排序能力的减少。

---

## 二、W3-a 剩余（约 5~6 小时）

完整设计与安全条件在 **`W3a-remote-execution.md`**，那份是权威，这里只给顺序与估时。

| 单元 | 估时 | 开工前必须先做的事 |
|---|---|---|
| U3 `remote-worktree` | 1.5~2 小时 | ① 定 **TOCTOU 威胁模型**（我推荐 A：只防误操作与配置错误，把「防恶意 agent」明确记为 out of scope，与本地版当前水平一致；B 需 OS 级隔离，超出 MVP 且无真机验不了）② 为新增的 `AsyncFilesystem` 协议**提一个 CR**（那是契约新增，不能静默改）③ **读 `adr/0002`/`0003`/`0004` 原文**复核 |
| U4 `remote-deliverables` | 约 1 小时 | 依赖 U3 的 `AsyncFilesystem.read_file` |
| U5 `config-and-tests` | 约 1.5 小时 | 含 **dispatcher 接线**（`_stop` 接受 `ProcessHandle`）—— `tests/smoke/test_remote_env_against_env_launch.py` 里那条 smoke 会**自动解除 skip**，它读 `_stop` 的签名 |
| Build & Test | 约 1 小时 | 需真机 `10.36.94.35`；其中后端全量本身 **40 分钟** |

⚠️ U3 的 8 项本地假设、`AsyncFilesystem` 的最小能力集、以及评审补的 **5 条安全条件**（含「递归 `mkdir` 会
悄悄重建设计禁止 Agora 创建的 project root」这个真漏洞）都在 `W3a-remote-execution.md` 里 —— **别重新推导一遍**，
那页分析花了不少时间。

---

## 三、两个阻塞（只有你那边能解）

1. **`tasks/agora/RESUME.md`** —— 「已知缺陷 D-1..D-6」的权威内容只在那里。W9 左栏的「已知缺陷」入口现在
   显示「此仓库尚无来源」（诚实但不可用），要建真实读取就缺这个。`.agora/development/PROGRESS.md:5688` 自己
   写着缺陷记在 workspace RESUME 里，而且数字还与 mockup（5）和 PROJECT-STATUS（D-1~D-5）**打架**（PROGRESS
   说 D-1..**D-6**）。
   落地建议：按 `adr/0004`（CR Log 与 ADR 住在项目仓库）推断缺陷也该住仓库，如 `.agora/defects.yaml` —— **但先确认 `adr/0004` 原文**。
2. **`tasks/agora/adr/0001-0008`** —— D-1（主推存控制平面库）与 D-5 都标注了「需确认 ADR 原文」。mockup 里
   到处是「砍掉 X 因为 Y」，不读 ADR 会重新提出已被否决的方案。

放进本仓库 `docs/` 即可（本仓库 private）。

**✅ 2026-09-11 已解**：缺陷 → `docs/known-defects.md`（权威副本）；ADR 0001-0008 → `.agora/decisions/`（已入库，
UI 直接读得到）；验收线按用户决定不复制，见 `W9-mockup-alignment.md`「给另一台机器的 agent」。

---

## 四、今天得到的经验

### 门禁与环境

1. **门禁有顺序依赖：先 `pnpm build`，再跑后端全量。** 否则 `tests/test_web_ui.py` 的 `GET /` 是 404
   （`frontend/.next` 不存在），报 1 failed 而看起来像代码问题。
2. **后端全量 40 分钟**（不是文档说的 20~29）。改造期间别每刀都跑全量 —— 用 `pytest tests/<file>` 或
   `-k "<关键词>"` 做窄反馈，收口时再跑一次全量。后台跑时**不要接 `| tail`**（会全程 0 字节）。
3. **`.gitattributes` 已加（`7b307cb`）**，Windows 上 clone 不会再踩 CRLF。它解决的是：这个仓库有一批
   **源码字符串断言测试**（断言 `page.tsx` 里出现某段代码，期望值含 `\n`），`core.autocrlf=true` 下必挂 ——
   而 **`git checkout-index -f` 修不动**（git 认为归一化后内容一致，不重写），`git ls-files --eol` 才看得出
   （`i/lf w/crlf`）。
4. 基线数字与 `PROJECT-STATUS.md` §3.6 记录不一致（后端 +8 passed、前端 +25），而其间只有 docs commit。
   **以实测为准**，这属于该项目 §8 自己承认的「记录与实现不一致」。

### 独立评审（今天最值钱的一条）

5. **每一刀都过了独立评审，四刀四次都抓到真问题。** 具体是什么，值得看：
   - 能力状态槽里 `unreadable_count ?? 0` **把「字段缺失」读成「确认的零」** —— 而这正是那个模块存在的目的
     所要防的事（`cr-log.ts` 明写过「the safe reading of a missing number is not zero」）。修法不是加判断，
     是把输入类型字段全改必填，让「缺失值」根本不存在。
   - 「今天主推」的**容量检查含 unresolved id 而 `is_full` 不含** → 「2 个有效 + 1 个 unresolved」会报
     `is_full=false` 然后下一次升级 409，**屏幕会提供一个它没有的位子**。
   - 我改过的一条源码断言测试**实际被削弱了**：正则只证明「表达式在文件里出现过」，连死代码都拦不住。
   - 「给已有逻辑找挂载点」不能反过来决定产品 IA —— 我当时用「能复用 `rail-group.ts`」当作加任务区的理由，
     方向错了。判断规则可复用，**展示结构不是必须保留的资产**。
   这些修正大约占了三分之一时间。**不要为赶进度省掉这一步**，上面四条都是会上线的 bug 或错误论证。
6. **codex 限额时的备选路径**：`Get-Content -Raw prompt.txt | kiro-cli chat --no-interactive --model
   gpt-5.6-sol --effort max`。**长 prompt 必须走 stdin 管道**，作命令行参数传会被截断（模型只收到开头）。

### 这个项目的既有模式（照着走，别另起一套）

7. **判断进 `lib/*.ts` + node 测试，组件只渲染。** 可被 `node --test` 直接跑的 lib 文件**不能用 `@/` alias**
   （node 解析不了）—— 这就是 `cr-log.ts` 明写「deliberately free of React and of `@/` path aliases」的原因，
   也是为什么 `control-plane.ts`（用了 alias）没有任何测试 import 它。需要它的类型时，**照 `cr-log.ts` 的做法
   重新声明一个最小 `*Like` 接口并注明为什么**，而不是 import。
8. **诚实性判断已经有三处同族**：`cr-log.ts` 的 `CRLogView`、`portfolio-bucket.ts` 的 `DashboardClaim`、
   `rail-group.ts` 的 `AttentionReach`。新增的 `nav-capability.ts` 是**上层映射**，只把它们的输出投影成导航
   能显示什么，**不重新判断**。再加新的同类东西时先问：是不是该吸收现有的。
9. **源码断言测试的写法**（这个仓库有好几条）：锚定**赋值本身**而不是「表达式出现过」，并尽量用**结构性**
   断言。例：证明渲染不会绕过陈旧-run 门控，最强的写法不是禁止某一种写法，而是断言**组件根本拿不到 runs**
   （`RunSummary` / `runs.some(` / `runs.filter(` 在组件里零出现）—— 别名和 helper 绕不过去。
10. **mockup 早于部分功能。** 它画于 2026-08-27，而 W7 是 09-06/07 才完成 —— 所以 mockup 左栏没有「其他工作区」
    面板是**时间顺序，不是否决**。遇到「mockup 里没有」先判断是**空白**还是**否决**：mockup 每屏底部的
    「这一屏」说明会明确写出砍掉过什么、为什么，那才是否决的证据。

### 工具坑（都是今天真踩的）

11. **python 的 `write_text` 编码失败会先截断文件。** 我用它写文档时一个 emoji 转义写错（`\ud83d\udd35` 是
    孤立代理对），结果**文件被清空 211 行并推了上去**（`a74644c`，已由 `822b80e` 恢复）。写文档用编辑工具，
    或先写临时文件再原子替换；emoji 用 `\U0001F535` 这种八位形式，或直接写字符。
12. **PowerShell 里单元素结果不是数组**：`$ids[0]` 在只有一个项目时取到的是**字符串的第一个字符**（拿到 `"a"`
    而不是 `"agora"`）。用 `@($ids)[0]`。
13. **PowerShell 传 python 单行命令时引号会被吃**，表现是「命令成功但没有输出」。用 here-string（`@" ... "@`）
    或写成脚本文件。
14. 字符串替换类编辑要带**足够上下文**：我两次把 `oldStr` 写得太短，误删了相邻一行（一次删掉
    `search: "Search tasks"`，一次造成 `const [projectFilter...]` 重复声明）。改完立刻 `tsc` / import 验一次。

---

## 五、怎么接手

1. 读本文件 → `W9-mockup-alignment.md`（界面决策）→ `W3a-remote-execution.md`（远端执行）。
   `PROJECT-STATUS.md` 仍是全局现状，但它的门禁数字已过期。
2. 装依赖（都不入库）：`cd backend && uv sync --frozen --all-extras`；
   `cd frontend && corepack pnpm install --frozen-lockfile`。
3. 起服务：`backend/.venv/Scripts/uvicorn.exe agora.api.app:app --host 127.0.0.1 --port 8000`
   —— 日常直接 `.\agora.ps1 up`（同一件事，外加等 /health 与开浏览器；`down` / `status` / `logs` / `shortcut` 见脚本头注释）
   （后端已挂载前端 build 产物，所以先 `pnpm build` 后直接开 `http://127.0.0.1:8000` 就能看界面）。
4. 从「一、W9 剩余」的第 1 项开始 —— 先花 10 分钟核实 Today 大卡的字段可读性，再动手。
5. 每一刀收口：窄范围测试 → `pnpm lint` + `tsc` + `build` → **独立评审** → triage → commit → push →
   更新对应文档的进度表。
