# 0005. charter 的权威记录也在项目仓库里，控制平面只读它

- **Status**: accepted
- **Date**: 2026-09-05
- **Layer**: task:agora
- **Supersedes**: 无。**延伸** [[0004]] 到 W4 标题里的第四件东西（Goal）

## Context

[[0004]] 定下「CR Log 与 ADR 的权威记录是项目仓库里的 markdown，控制平面数据库不持有它们」，
理由是这套机制的效力来自「agent 每次都被迫看见 OPEN 行」，搬进 DB 就只剩展示。

那条决定当时**没有动 charter**，于是留下 CR-15：`Project.charter`（goal / non_goals /
success_criteria）仍在控制平面 DB 里，而同属「项目定义」这一层的 CR / ADR 在仓库里。**同一层
出现两套口径，正是本项目反复栽的形状。**

不当场一起搬，理由记在 CR-15 里且成立：charter 不是一个字段。2026-09-05 实测它有

- `projects.charter TEXT` 列 + `projects/store.py` / `registry.py` / `models.py`
- `PUT /projects/{id}/charter`（`api/settings_router.py`）
- portfolio cockpit 的展示卡 + `CharterPanel` 编辑面板
- `projects.yaml` 导入器（`projects/importer.py`）

而且「该在 markdown 层的形状真的落地之后再决定」——W4 落地了那个形状（两个 reader：
`projects/cr_log.py`、`projects/decisions.py`；固定路径、不搜索、不 fallback、读不到就说读不到），
所以现在有东西可对着决定，不再是猜。

## Decision

**charter 的权威文本是项目仓库里的 markdown，控制平面读它，UI 展示为只读。**

即：charter 加入 [[0004]] 已经建好的那套 reader，成为第三个；`CharterPanel` 从编辑面板变成
展示面板；`projects.yaml` 导入器改为**生成/播种那个文件**而不是写 DB 列。

决定性的那句话不是存储整洁，而是 CR-15 自己设的那道测试：**选「留 DB」必须能说清「为什么
goal 不需要被 agent 看见，而 CR 需要」。说不清就是搬。**

那句话说不出来。ANCHOR 写的是把 claude-workspace 那套防偏离机制产品化，而在
claude-workspace 里 **goal 与 CR 正是同一个机制的两半**：`task.yaml` 的 goal 是文件、在仓库里，
SessionStart hook **每次会话都把 ANCHOR 注入给 agent 读**，CR Log 只是它的另一半（「现在做的事
还在 goal 里吗」）。把 CR 放进仓库、把 goal 留在 DB，等于把一把钳子的两片放在两个抽屉里。

## Consequences

- **项目定义整层一个口径**：goal / non_goals / success_criteria / CR Log / ADR 全在项目仓库里，
  一起 diff、一起 review、改 goal 是一个可见的 commit——这正是 [[0004]] 说的「效力来源」
- **agent 在它干活的地方读得到 goal**，不需要控制平面在跑
- **UI 暂时不能编辑 goal**。这是本决定**已知且接受的代价**：编辑要么改文件，要么等一个
  write-through 路径。选它而不是选全搬（见 Alternatives (a)），是因为写入路径立刻牵出
  「哪个环境、哪个 worktree 拥有那个文件」，而那是 W2 要设计的东西——现在做就是做两遍
- **公开仓库的问题要按 [[CR-16]] 的先例处理**：Agora 自己的仓库是 PUBLIC，charter 搬过去等于
  公开发布。CR-16 对 `.agora/decisions/` 的解法是**目录联结 + `.gitignore`**（一份文件、仍归
  data 仓追踪与备份、reader 读得到、git 看不见），charter 落地时沿用同一手
- **迁移**：现有 `projects.charter` 列里的内容要导出成文件。列本身可以先留着不读（比删列安全），
  但**不能两边都读**——那就又是两套口径
- **落地不在 W4 的 exit_criteria 里**。W4 第六条只要求「CR-15 有决定」，本 ADR 即是。实现是
  一块新工作，需要一个里程碑归属（建议与 W2 同批，因为写入路径与环境模型是同一个问题）

## Alternatives

### (a) charter 全搬进 markdown，包括编辑路径（被否）

口径最统一，也是最终形态。**被否的原因是顺序而不是方向**：界面编辑变成「往 git 工作树写文件」，
立刻要回答「哪个环境、哪个 worktree 拥有这个文件」——而这正是 W2（Environment 抽象）要设计的。
[[CR-7]] 已经因为同一个理由把「用哪个库取决于工作目录」推到 W2：现在按本地路径改一遍、W2 再按
环境模型改一遍，是白做两遍。本决定拿到 (a) 的读取侧全部好处，把写入侧留给 W2。

### (b) charter 留在 DB（被否）

零成本。**被否是因为它要求一句说不出来的话**——见 Decision。附带一点也值得记下：`charter` 的
模型注释写着 "Agents never write it"，这是对的，但「agent 不写」不等于「agent 不读」，而当时正是
把这两件事混成了一件。

### (c) 两边都存，DB 为缓存（被否）

听起来是折中，实际是**两套口径加一条同步规则**。本项目已经在同一形状上栽过：一个数字有两个来源
时，先漂移的那个决定界面显示什么。要缓存也该是 reader 层的缓存（可从文件重建），不是第二份权威。
