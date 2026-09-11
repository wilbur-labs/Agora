# 0004. CR Log 与 ADR 的权威记录是项目仓库里的 markdown，不是控制平面数据库

- **Status**: accepted
- **Date**: 2026-09-05
- **Layer**: task:agora

## Context

W4 的标题是「Project Goal / Requirement / CR Log / ADR 进 UI」。动手前把这四件东西的现状量了
一遍（2026-09-05 只读核对，非推测）：

| W4 标题的四件 | 现状 |
|---|---|
| **Goal** | **已经在 UI 里**。`Project.charter`（goal / non_goals / success_criteria）有 DB 列（`projects.charter TEXT`）、`PUT /projects/{id}/charter`、portfolio cockpit 的展示卡 + `CharterPanel` 编辑面板、`projects.yaml` 导入器支持 |
| **Requirement** | **已经在 UI 里**。`backend/agora/requirements/` 1046 行（models/router/schema/store）+ `frontend/src/app/requirements/` 页 |
| **CR Log** | **不存在**。全库没有任何 change-request 概念 |
| **ADR** | **不存在**。`.agora/decisions/` 是个空目录——位置留好了，没有任何代码读写它。代码里所有 `decision` 命中都在 [[adr/0001]] 冻结的 `orchestration/` 树里（`orchestration_decisions` 表），与此无关 |

所以 W4 真正要造的是 **CR Log + ADR**。而它们该存在哪，本仓库有**两个互相打架的先例**：

- **W1-d 的先例**：进度条读项目自己的 `.agora/development/PROGRESS.md`。一条固定路径、不搜索、
  不 fallback；产品只数不评分；读不到就说读不到，绝不编数字（`projects/progress_snapshot.py`）
- **W1-a / [[adr/0002]] 的先例**：控制平面「DB 权威」，整个控制平面一个库。`charter` 就是这么存的

## Decision

**CR Log 与 ADR 的权威记录是项目仓库里的文件。控制平面数据库不持有它们。**

决定性的理由不是「哪种存储更整洁」，而是**这套机制的效力从哪来**。ANCHOR 写的是「把
claude-workspace 那套防偏离机制产品化」。在 claude-workspace 里，CR Log 拦得住 vision drift
**只因为 SessionStart hook 每次会话把它塞进 agent 的上下文**——OPEN 行就在眼前，checkpoint
自检对它阻塞。把这张表搬进数据库，任何 agent 都读不到它，剩下的只是一个好看的列表：
**产品化的是「显示」，机制本身没搬过来**。那正是 [[adr/0015]] 说的那种记录——写得越工整，
越像隐患已经被控制住了。

文件在仓库里则天然具备三件数据库给不了的事：随 `git clone` 走、agent 在工作树里直接读得到、
**Agora 没开着也照常工作**。最后一条要紧——唯一的成功标准是「我自己每天愿意用」，而 Agora
在很长一段时间里都不会是唯一的入口；一个只在 Agora 开着时才存在的 CR Log，会输给那个在终端里
一直好用的 markdown。

具体四条：

**① 固定路径，不搜索、不 fallback。** 沿用 `progress_snapshot` 的契约，理由相同：有 fallback
就没法证明「没有」，而「没有」正是界面必须能说出口、又不能编数字的那句话。

```
ADR      <project root>/.agora/decisions/NNNN-*.md
CR Log   <project root>/.agora/development/CR-LOG.md
```

**CR Log 单开一个文件，不做 PROGRESS.md 的一个章节**，尽管 claude-workspace 里它是 RESUME 的
一节。理由见 Alternatives 第三条——一句话：PROGRESS.md 在本仓库已经 5610 行，而 `too_large`
是 `progress_snapshot` 真实会返回的状态，把 CR Log 塞进去等于**让一个无关文件的增长静默关掉
防偏离机制**。

**② 解析走 CommonMark block token，不写行扫描。** `lessons/markdown-checklist-counting` 记的是
六轮 14 条 miscount 换来的教训，`progress_snapshot` 已经因此改用 markdown-it-py。CR Log 是
markdown 表格、ADR 要认 `## Status` 这类章节，两者都是 block 问题，不是行问题。

**③ reach 词汇沿用 `progress_snapshot`**：`absent` / `too_large` / `unreadable` / available，
读不到时字段是 `None`，不是 0、不是空数组。**「没有 CR」和「读不到 CR」必须是界面上两种东西**
——这是本项目反复栽的那个形状（`lessons/none-means-unknown-not-absent` 已记七例），不重演。

**④ 第一刀只读。** 理由见 Consequences 第一条，那是架构后果不是取舍。

## Consequences

- **写入路径今天不存在，而且不是偷懒。** [[adr/0003]] 定死 `Environment` 只有 `run` + `probe`
  两个能力，**没有写文件**。所以「从 UI 新建一条 CR」对远端项目根本没有落点；只给本地项目实现
  又会造出第二套口径，正是要躲的那个形状。写入能力要么等 W3 给 `Environment` 加第三个能力
  （那需要一条自己的 ADR），要么走「Agora 生成文本、agent 在工作树里写」这条已经存在的路。
- **goal 仍在 DB 里 → 同一件事两套口径。** 已记为 **CR-15（OPEN）**，W4 收口前必须给出决定。
  这里不顺手一起搬：charter 已有 DB 列 + 端点 + 编辑面板 + 导入器支持，迁移是独立一件事，而且
  **该在 markdown 层的形状落地之后再决定**——现在决定就是对着还没写出来的东西猜。
- **项目必须自己维护那两个路径**，Agora 不代建、不改写项目的 `AGENTS.md`（该文件明文
  「Never silently modify, move, or rename native Codex, Claude, Kiro, or AI-DLC files」）。
  没有文件 = `absent`，界面说「这个项目没有 CR Log」，**不是「0 条 CR」**。
- Agora 自己的 `.agora/decisions/` 空着——**dogfood 的第一个动作就是往里放东西**，否则 W4 做完，
  在本仓库上看到的是一片 absent，等于没有验收样本。

## Alternatives

- **控制平面数据库持有 CR / ADR**（与 charter、requirements、tasks 一致）。**被否**：口径统一、
  UI 编辑简单、无需解析器，这些好处都真实；但记录不在仓库里，`git clone` 带不走，**agent 看不见**。
  防偏离机制的执行力恰恰全部来自「agent 每次都被迫看见 OPEN 行」，搬进 DB 就只剩展示。
  另注一条容易被误用的理由：[[adr/0002]] 否掉「每项目一个库」，依据是**控制平面的行跨项目互相
  引用**（任务引用项目、Run 引用任务、gate 引用 Run），必须能在单个事务里写。**CR 与 ADR 不跨
  项目引用**——它们是项目本地的叙事。那条理由在这里不成立，不能拿来支持 DB 权威。
- **markdown 权威 + DB 做派生索引**（文件是真源，解析进 DB 供跨项目查询，写入仍落回文件）。
  **被否**：两边好处都要，代价是双向同步和陈旧问题，三个方案里工作量最大、失败模式最多。
  而它买到的唯一东西是**跨项目查询 CR / ADR**——这个需求今天不存在：Today 页回答「今天推什么」、
  项目页回答「我都有什么」，两者都没有「所有项目的 CR 汇总」这一格。为一个假想需求先付双向同步
  的账，正是这个仓库刚花 14 轮评审清掉的那类债。真需要时再加索引也不迟，那时文件已经是真源，
  加索引是单向的。
- **CR Log 作为 `.agora/development/PROGRESS.md` 的一个章节**（最贴近 claude-workspace 的原形，
  且 `AGENTS.md` 的 resume 步骤第 1 条已经要求 agent 读那个文件，上下文注入属性免费拿到）。
  **被否，两条理由**：
  ① PROGRESS.md 在本仓库已经 5610 行，而 `too_large` 是 `progress_snapshot` 真实会返回的状态。
  CR Log 寄生在它里面，意味着**某天有人多写了几百行开发日志，防偏离机制就静默失效了**。一个
  机制的可用性不该由一个与它无关的文件的体积决定。
  ② 按标题定位章节 = 把那行标题变成标识符，改名或翻译即断。claude-workspace 自己为此在
  CLAUDE.md 里立了「Never translate — identifiers」一条，那是它已经踩过的坑，没有理由在新产品
  里重踩一遍。
  **代价认领**：单开文件不在 `AGENTS.md` 的 resume 清单里，上下文注入属性要项目自己补一行。
  这是项目的选择，不是 Agora 偷偷替它做的决定——而 Agora 能做的是在界面上说「这个项目有 CR Log
  但它没被列进 resume 清单」，那是提示，不是代劳。
