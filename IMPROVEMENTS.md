# Agora 改善任务清单

> 2026-04-16 基于代码审查的改善计划
> 分支: main (feat/sequential-executor 已合并)

## 当前状态

- feat/sequential-executor 已合并（多 action item 逐个执行 + timeout 300s）
- 前端: Thinking/Executing 指示器 + tool_call 动画（未提交）
- I1~I11 全部实现完毕（未提交）

---

## P0 — 核心体验（用户直接感知的问题）

### I1: generate_with_tools 流式化
- **状态**: ✅ 已实现
- **影响**: Executor 思考时用户什么都看不到（即使有 Thinking 指示器体验也差）
- **方案**: streaming 解析 `tool_calls` delta，逐步 yield text token
- **涉及**: `backend/agora/models/openai_provider.py`, `backend/agora/models/base.py`

### I2: Thinking/Executing 指示器 + tool_call 动画
- **状态**: ✅ 已实现（未提交）
- **内容**: streaming 时显示 bouncing dots + "Thinking…/Executing…"，tool_call running 状态旋转动画 + cyan 高亮
- **涉及**: `frontend/src/app/chat/page.tsx`, `frontend/src/components/message-bubble.tsx`

### I3: 执行进度 UI
- **状态**: ✅ 已实现
- **改善**: 前端识别 `[i/n]` 格式，渲染为进度条/步骤指示器
- **涉及**: `frontend/src/components/message-bubble.tsx`，可考虑新增 SSE event type `item_progress`

---

## P1 — 功能完善

### I4: onAgentDone 签名不匹配
- **状态**: ✅ 已修复
- **影响**: 功能正常但 TypeScript 类型不一致
- **涉及**: `frontend/src/hooks/use-chat.ts`

### I5: 会话恢复时后端 Context 丢失
- **状态**: ✅ 已实现（POST /api/chat/restore）
- **影响**: 切换到旧会话继续对话时，后端不知道之前聊了什么
- **涉及**: `frontend/src/hooks/use-chat.ts`, `backend/agora/api/chat.py`（需要 context restore API）

### I6: Error 事件显示改善
- **状态**: ✅ 已实现（error 状态图标 + 红色边框 + 内联错误消息）
- **涉及**: `frontend/src/hooks/use-chat.ts`（改善 onError 处理）

### I7: Web UI 版 Human-in-the-Loop 确认
- **状态**: ✅ 已实现（SSE confirm 事件 + 前端确认框 + POST /api/chat/confirm）
- **方案**: 后端暂停 → SSE 发确认事件 → 前端弹确认框 → 用户响应 → 后端继续
- **涉及**: `backend/agora/api/chat.py`, `frontend/src/hooks/use-chat.ts`, `frontend/src/components/message-bubble.tsx`

---

## P2 — 代码质量

### I8: API base URL 重复定义
- **状态**: ✅ 已修复（统一使用 getApiBase()）
- **修复**: 统一使用 `api.ts` 的 `getApiBase()`
- **涉及**: 上述 4 个文件

### I9: 页面缺少 loading/error 状态
- **状态**: ✅ 已实现（agents, skills, settings 三个页面）
- **涉及**: 上述 3 个页面

### I10: _tool_execute 的 context 重复添加 bug
- **状态**: ✅ 已修复（移除 _tool_execute 内的重复 add_agent 调用）
- **涉及**: `backend/agora/agents/council.py`

### I11: sequential executor 测试不足
- **状态**: ✅ 已补充（10 个新测试：_parse_action_items 5 个 + sequential execution 4 个 + I10 回归 1 个）
- **涉及**: `backend/tests/`

---

## P3 — 锦上添花

### I12: 移动端适配
- **现状**: sidebar 有 `max-md:hidden` 但没有移动端替代（汉堡菜单）

### I13: 键盘快捷键
- Esc 关闭、Cmd+Enter 发送等

### I14: tool_result 长输出的 UX
- **现状**: 后端截断到 2000 字符，前端 `max-h-48` 的 `<pre>`
- **改善**: 添加"展开全部"按钮

---

## 推荐实施顺序

1. **I2** 提交（已实现）
2. **I10** context 重复添加 bug 修复（小但重要）
3. **I1** streaming tool-calling（体验改善核心）
4. **I4 + I8** 类型修复 + API URL 统一（小重构）
5. **I5** 会话恢复的 context 同步
6. **I7** Web UI Human-in-the-Loop
7. **I11** 测试补充
8. 其余 P2/P3
