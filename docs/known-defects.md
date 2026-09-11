# 已知缺陷 D-1 ~ D-6

> 权威副本。2026-09-11 从 claude-workspace `tasks/agora/RESUME.md`「已知缺陷」一节原文搬入（那边自此只留指针）。
> 来源：codex review 2026-08-27，已确认成立；D-1 / D-6 后来已修，划掉的原文保留作记录。
> 状态变化直接改这个文件。W9 D-5 步骤 B 若建结构化来源（如 `.agora/defects.yaml`），以这里为输入，建成后本文件退为说明。
> 数字对照：mockup（08-27）与 PROJECT-STATUS 说 D-1~D-5，是因为 D-6 在 mockup 之后才自查发现；D-1..D-6 是全集。

- ~~**D-1 · 历史 Run 的「改了哪些文件」是假的**~~ → **已修并提交 2026-08-27**（commit `10e3140`，七个保证 + 六轮 review）。原文保留作记录： — `execution/deliverables.py` 的 docstring 自陈「Reports the worktree's **current** changes」：文件列表是**请求时现算 git diff**，不是 Run 完成时的快照。后果：handoff 提交后列表变空；同一 worktree 的后续改动被算到旧 Run 头上；两个 Run 显示同一份列表。**修法 = 在 Run 结束时持久化不可变文件清单 → 这就是 W0(b) 的第一项。**
- **D-2 · 「可信完成度」不绑定当前 spec** — `api/settings_router.py:361-365` 扫到任意历史成功 worker/review Run 就置标志。需求变更返工后，旧 Run 让新 criteria 显示成已产出/已评审。**暂不修**：这层指标属于 [[adr/0001]] 冻结的 AI-DLC 控制平面，等 W4 决定指标形态（很可能改用 RESUME 进度快照的 ✅/🔵/⬜ 计数）时一并处理。
- **D-3 · role 默认值前后不一致（codex 未报，自查发现）** — `dispatcher.py:148` 和 `handoff.py:56` 用 `.get(ROLE_KEY, ROLE_WORKER)`（缺 role = worker），而 `settings_router.py:361` 用 `.get(ROLE_KEY, "")`（缺 role = 忽略）。同一份数据两套解读。前端 F4 的修复已按前者（排除 review 而非只留 worker）。
- **D-4 · 「worker 成功过、但后续 review 失败」不进 Next Action** — `tasks/next_action.py` 的 RUNNING 分支用的是 `has_worker_success`（曾经成功过），不是「最近一次终止 Run 的结果」。所以 worker 成功→自动 review 失败 的场景仍回 `verify_review` 且 `attention=false`。**不是本次改动引入的**（改动前该场景同样回 verify_review），且失败并未被隐藏：`dispatcher._after_run` 会为 review 失败创建 **BLOCKER attention**，仍出现在 Attention Center 与 dashboard 的 attention 计数里。要真正修，得把「最近一次终止 Run」作为新信号从 settings_router 穿进引擎——那是 W4 要重做的那层，届时一并处理。
- **D-5 · 无证据 DONE 会同时显示 0% verified 和「Done — nothing to do」** — 这是 F1 修复带来的自相矛盾：指标诚实了，但 `task_next_action(DONE)` 仍返回 `none`，没有任何动作可以弥补这个证据缺口。**这是「指标形态本身不对」的症状，不是新 bug**——一个看得见的矛盾好过一个自洽的谎言。要消解它得引入新的 action 词汇 + 双语 i18n + gate 策略，同属 W4 范围。
- ~~**D-6 · 前端把「加载失败」显示成「尚未产出结果」**~~ → **已修（未提交）**，原文保留： — `workspace/page.tsx` 的 `fetchDelivery(...).catch(() => {})` 吞掉错误，`delivery` 保持 `null`，`fileCount` 经 `?? 0` 变成 0，界面渲染「尚未产出结果」。网络抖一下，一个已完成任务就会被说成什么都没做。**同类第七例**（见 `lessons/none-means-unknown-not-absent.md`）。**计划**：在给 `OutcomeSummary` 加 recap 渲染的那个 commit 里一起修，让它区分「未交付」和「读不到」。
