# Agora 升级执行计划

## 项目定位

Agora = 多角色 AI 议会系统，面向开发者。
一个模型扮演多个专业角色，像团队一样从不同视角审视问题，讨论后可直接执行。

核心能力 = DeerFlow（任务编排） + Hermes（自学习） 的融合。

## 当前状态（已完成）

- [x] 多角色讨论（Scout / Architect / Critic / Sentinel）
- [x] Moderator 澄清机制
- [x] Synthesizer 结论收敛（Goal / Key Decisions / Action Items / Open Questions）
- [x] 基础记忆（MEMORY.md / USER.md）
- [x] 多模型后端（claude-cli / gemini-cli / kiro-cli）
- [x] CLI + API 双入口
- [x] 流式输出

---

## Phase 2：任务编排（DeerFlow 能力）

### 2.1 Moderator 升级 — 路由判断

Moderator 从"澄清者"升级为"路由器"，判断用户输入走哪条路：

- **快速回答** — 简单问题，单 agent 直接回答
- **深度讨论** — 复杂问题，召集议会讨论 → Synthesizer 收敛
- **直接执行** — 明确的执行指令，跳过讨论直接交给 Executor

Moderator 判断后向用户确认："要深入讨论还是直接执行？"用户也可通过命令手动指定（如 `/ask` 快速问答）。

### 2.2 Executor Agent — 执行者

新增 Executor 角色，专门负责执行任务：

- 不参与讨论，只在执行阶段出场
- 接收 Synthesizer 的 Action Items 或用户的直接指令
- 通过底层 CLI 工具（kiro-cli / claude / gemini）的能力来读写文件、跑命令
- Executor 自身不实现工具，而是把任务描述清楚传给 CLI 工具执行

### 2.3 执行流程

```
用户输入
  → Moderator 判断路由 + 用户确认
    → 快速回答：单 agent 直接回复
    → 深度讨论：
        Scout / Architect / Critic 讨论
        → Synthesizer 产出 Action Items
        → 用户确认要执行哪些
        → Executor 逐项执行
    → 直接执行：
        → Executor 直接执行
```

### 2.4 用户确认环节

执行前必须经过用户确认（人在回路）：
- Synthesizer 输出 Action Items 后，用户选择执行哪些
- Executor 执行过程中，危险操作（删除文件、跑未知命令）需二次确认

---

## Phase 3：自学习（Hermes 能力）

### 3.1 Skill 提取

每次任务执行成功后，LLM 回顾执行过程，提取 skill 文件：

```yaml
name: add_fastapi_endpoint
trigger: "用户要求添加新的 API 端点"
steps:
  - 在 router 文件中添加路由
  - 创建对应的 Pydantic schema
  - 添加业务逻辑
  - 更新导出
lessons:
  - 记得加 error handling
  - 要同步更新 OpenAPI docs
```

### 3.2 Skill 注入

下次遇到类似需求时，匹配的 skill 注入到 agent prompt 中作为参考。agent 带着经验思考，不是机械执行模板。

### 3.3 三层记忆

| 层级 | 内容 | 文件 |
|------|------|------|
| Episodic | 历史任务、对话上下文 | MEMORY.md |
| Semantic | 用户偏好、项目知识 | USER.md |
| Procedural | 可复用的执行技能 | skills/*.yaml |

---

## Phase 4：自定义角色

- 用户通过手动编写 YAML 文件创建自定义角色
- 放到 profiles 目录，在 config.yaml 中配置即可启用
- 现有架构已天然支持，无需大改

---

## 未来改善计划（Phase 5+）

以下功能在核心闭环跑通后再迭代：

- [ ] Skill 成功率追踪（记录每个 skill 的执行成功/失败）
- [ ] Skill 版本管理（skill 更新时保留历史版本）
- [ ] Skill 匹配优化（从关键词匹配升级为语义匹配）
- [ ] CLI 命令创建角色（`/role create name "description"`）
- [ ] Agents 并发执行（讨论阶段多 agent 同时响应，不再顺序等待）
- [ ] Web UI
- [ ] 商业化层（多租户、权限、计费、数据隔离）

---

## 技术决策摘要

| 决策 | 选择 | 理由 |
|------|------|------|
| 模型后端 | 支持 claude-cli / gemini-cli / kiro-cli / API / 本地模型 | 开源项目需要灵活性 |
| Executor 工具实现 | 利用底层 CLI 工具能力，不自己造轮子 | kiro-cli/claude 已有成熟的文件操作和命令执行 |
| 执行确认 | 人在回路，用户确认后才执行 | 安全，避免 agent 乱改代码 |
| 路由判断 | Moderator 自动判断 + 用户可手动指定 | 兼顾效率和控制感 |
| 自定义角色 | 手动写 YAML | 先简单实现，后续加 CLI 命令 |
| 自学习水平 | 先做到 Hermes 基础水平 | 跑通闭环再迭代 |
