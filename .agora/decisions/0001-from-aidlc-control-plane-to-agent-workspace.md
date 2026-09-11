# 0001. 从 AI-DLC 形式化控制平面转向 AI Agent 开发工作台，旧机器冻结而非删除

- **Status**: accepted
- **Date**: 2026-08-27
- **Layer**: task:agora

## Context

Agora 到 2026-08-27 为止已经换过两次身份，而 workspace 的 L3 记录停在第一次：

| 阶段 | 产品论点 | 代码现状 |
|---|---|---|
| v0.1–v0.4（~2026-04） | 议会讨论 + 全栈执行 + 自学习 | **已删除**。`legacy-council-retirement-v1.md` 明写 "Agora 1.0 is a Task delivery control plane, **not** an autonomous AI council"；`skills/` `memory/` `context/` `tools/` `sandbox/` 全部变成空目录 |
| v0.5–v1.1（2026-07~08） | AWS AI-DLC 形式化方法论控制平面 | **存活约 45k 行**：`orchestration/` 31,871 + `protocol/` 6,724 + `control_plane/` 6,157 |
| 现在 | ？ | —— |

驱动这次决策的三个 force：

1. **实际使用已经脱离 AI-DLC 主线**。`.agora/development/PROGRESS.md` 从 2026-08-24 起，全部增量都在 Task Workspace 前端（让每一步显示真实产出），没有一条再碰 orchestration。用户对当时界面的原话是「显示的是屁」——说明形式化的 stage/gate/receipt 模型没有解决他真正的问题。

2. **用户提出的新构想，其领域模型已经在 claude-workspace 里跑了半年**。RESUME 续接、task.yaml goal 锚定、CR Log 五分类防漂移、ADR 决策留痕、lessons 三级技能沉淀、DoD 闭环——这些不是待设计的功能，是**已经用 agora / kimono / pleasanter-workflow 等真实任务验证过、失败模式都写进 CLAUDE.md 的机制**。所以本次转向的性质是「移植一个验证过的模型」，不是「设计一个新产品」。

3. **45k 行的处置必须先定，否则新模型只会变成第三层沉积岩**。两次转身都留下了没人用的代码；第三次不能重蹈。

同时有一个未解的技术前提：三个执行适配器目前是 `codex exec --ephemeral` / `claude -p --no-session-persistence` / `kiro-cli chat --no-interactive`，**会话持久化被主动关掉**，而新定位的核心承诺是「点一下接着昨天干」。厂商侧能力（见 `phase-6e-adapter-capabilities.md`）：Codex 可双向、Claude Code capture-only、Kiro 无双向协议。

## Decision

**其一，Agora 重新定义为：把 claude-workspace 的工作模型产品化成多 Agent × 多项目 × 多环境的开发工作台。** 单一成功标准是「我自己每天愿意用它干活」。

**其二，45k 行 AI-DLC 机器采取「隔离」策略**：冻结、不删、UI 不暴露、新代码一律不依赖它。只复用其中两块直接服务新定位的资产——`execution/adapters.py`（三个 agent 适配器，约 2k 行）和 `workspaces/provisioner.py`（git worktree 隔离）。

**其三，先证伪再建设。** 在写任何 UI 之前先做 W0 切片，回答「跨厂商 session resume 到底能不能做、代价是什么」。这个答案决定 Agora 是「多 agent 工作台」还是「Codex 工作台 + 两个一次性调用」，是两个不同的产品。

## Consequences

**变容易**：
- 领域模型不用赌——它已被半年真实使用验证，且踩过的坑有文字记录
- 差异化从「功能」变成「经验」：别人要撞三次墙才能拿到的工作流约束，这里是现成的
- 复用 adapters + provisioner，三个 agent 的接入和工作区隔离不用重写

**变难 / 代价**：
- 一个 repo 里两套东西并存，心理负担持续存在，且新人（含未来的会话）容易误读
- 45k 行仍在 CI 与测试范围内，维护成本不为零
- `promotion-drafts.md` 里 15 篇发布文案描述的是**已不存在的产品**，重发前必须全部重写
- v1.1.0 已打 tag 并公开，对外叙事需要一次显式的重定位说明

**已知缺口 / 未决**：
- W0(a) 的答案未知，可能反过来削减产品范围（见 RESUME「下一步」）
- `feature/control-plane-1.2` 上 16 个未 push 的 commit 去向未定（建议合 main）
- 「隔离」与「删除」的差别只是时机：跑通新主线后若半年无人触碰冻结区，届时另写 ADR 决定删除

## Alternatives considered

- **A 继承：把新模型架在现有 protocol 之上** — 否决。每加一个功能都要过 stage / gate / receipt / 授权路由那一整套形式化流程，开发速度会被拖到不可接受。而且这套流程解决的是「多人组织的交付合规」问题，本产品的用户是一个人。

- **B 清算：直接删掉 orchestration + protocol，Agora 2.0 重新开始** — 否决（暂缓）。心智负担能一次性结清，但会连带删掉 `execution/adapters` 与 `workspaces/provisioner` 的上下游依赖，而这两块是唯一直接服务新定位的资产。且 W0 尚未跑通，此刻删除等于在没有替代品时先拆掉唯一能跑的执行路径。保留「跑通后再删」的选项。

- **C 沿用旧 goal，只在 AI-DLC 框架内加 UI** — 否决。这是记录与现实继续背离的做法：用户的六条需求（Codex 式界面 / Agent × 项目切分 / SSH / 项目目标管理 / workspace 与 skill 统一 / skill 上 GitHub）没有一条能从「形式化交付控制平面」这个论点里推导出来。

- **D 先定 9 个核心对象的关系图再动手**（外部建议的做法） — 否决。该建议默认这是绿地项目。实际情况是已有三套互相打架的领域模型（council 遗骸 / AI-DLC 协议 / 新构想），再定第四套只会变成第三层沉积岩。改为「先移植已验证的模型 + 先证伪技术前提」。

- **E 先做 Desktop 套壳（外部建议排 P0）** — 否决。Desktop 化只服务六条需求中的第一条，而 Codex 式布局本质是信息架构问题，与外壳无关，在 Web 里今天就能做。降到 W6。

- **F 做 LLM 驱动的 Goal Drift 检测**（外部建议） — 否决。高频 LLM 判官必然误报，必然被静音。claude-workspace 的 CR Log 用「拦截」而非「检测」解决同一问题：新想法出现即分类为 bug/clarify/refine/creep/cut，creep 必须先改 goal 再动手——确定性、人在回路、零误报。

- **G 做 Skill 自动提取 / 自动进化**（外部建议） — 否决，有实证。v0.1-0.4 的 `skills/extractor.py` 正是此物，现已随 council 一并删除。`lessons/` 机制之所以活得好，恰恰因为它有一道**强制的人工适用性测试**（「业务流程不是跨任务片段」等 4 条判据）。没有这道闸，产出的是垃圾场。

- **H 统一 Skill 格式 + 三厂商双向同步**（外部建议） — 否决其「双向」部分。三家执行语义不同：Claude skill 由模型自主决定加载（渐进披露），Codex 的 AGENTS.md 无条件注入，Kiro 用 steering/specs 另一套触发。最小公约数格式三边都不好用。改为 **Claude 格式作 source of truth，Codex / Kiro 为单向编译产物**——理由是现成 skill 全是 Claude 格式，且 claude-workspace 刚完成 Codex overlay 移植，这条路已有实践经验。
