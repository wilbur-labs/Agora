# Agora 升级执行计划

## 项目定位

Agora = 全栈 AI agent 平台，面向开发者。

核心差异化：
- **议会讨论**（护城河）— 多角色从不同视角审视问题，这是 DeerFlow 没有的
- **全栈执行** — 自建工具层，不依赖外部 CLI 的黑盒能力
- **自学习** — Hermes 风格，从讨论和执行中都提取经验

一句话：**先讨论再动手，做完了还会总结经验。**

---

## 完成进度总览

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 多角色议会讨论 + 基础设施 | ✅ 完成 |
| Phase 2 | 自建工具层 + Tool-calling 执行 | ✅ 完成 |
| Phase 3 | 自学习系统（讨论+执行 skill） | ✅ 完成 |
| Phase 4 | 执行流程完整闭环 | ✅ 完成 |
| Phase 5 | 自定义角色 | ✅ 架构已支持 |
| Phase 6 | Docker 沙箱 | ✅ 完成 |
| Phase 7 | 测试体系 + 项目结构 + 部署 | ✅ 完成 |
| Phase 8 | Embedding 向量检索 | ✅ 完成 |
| Phase 9 | Web UI | 📋 计划中 |
| Phase 10 | MCP Server 扩展 | 📋 计划中 |
| 商业化 | API 计费 / Skill 市场 / SaaS | 🔒 保留 |

---

## ✅ 已完成的功能明细

### 议会讨论系统
- [x] 多角色讨论（Scout / Architect / Critic / Synthesizer）
- [x] Moderator 路由（QUICK / DISCUSS / EXECUTE / CLARIFY）
- [x] Agent 并发执行（config 开关 `concurrent: true`）
- [x] Sentinel 安全审查（可选角色）
- [x] 自定义角色（写 YAML profile 即可）

### 全栈执行
- [x] 自建工具层（read_file / write_file / patch_file / list_dir / shell）
- [x] OpenAI-compatible API provider（Azure / OpenAI / DeepSeek / 任何兼容 API）
- [x] Executor tool-calling 循环（LLM 发 function call → 执行 → 返回结果 → 循环）
- [x] 失败自动重试（Executor prompt 改进）
- [x] CLI fallback（无 API 时退回 CLI 子进程模式）

### 自学习系统
- [x] 执行 Skill 提取（从执行过程中学习操作步骤）
- [x] 讨论 Skill 提取（从讨论中学习决策模式）
- [x] Skill 成功/失败追踪（success_count / fail_count）
- [x] Skill 合并（同名 skill 保存时累加计数）
- [x] Skill 关键词匹配
- [x] Skill LLM 语义匹配（fallback 关键词）
- [x] 持久化记忆（MEMORY.md / USER.md，有界存储）
- [x] 对话后自动提取记忆

### 模型支持
- [x] Azure OpenAI（function calling）
- [x] OpenAI API
- [x] Claude Code CLI
- [x] Gemini CLI
- [x] Kiro CLI
- [x] 按角色配不同模型（讨论用 A，执行用 B）

### 基础设施
- [x] Docker 沙箱（ephemeral container，执行完自动删除）
- [x] Docker Compose 部署（API + CLI 双模式）
- [x] 项目结构规范化（配置在根目录）
- [x] CLI 交互（prompt-toolkit，命令补全，历史记录）
- [x] FastAPI SSE 流式 API
- [x] Makefile（install / dev / cli / test / up / down）

### 测试体系
- [x] 70 个单元测试（工具 / 记忆 / Skill / Council / 执行循环 / 配置）
- [x] 9 个集成测试（LLM-as-Judge 质量评估）
- [x] Judge 评估器（评估角色匹配度 / 讨论多视角 / 执行完成度 / 事实准确性）

---

## 📋 下一步计划

### Phase 8: Embedding 向量检索
- Skill 匹配：embedding 替代 LLM 调用，毫秒级 + 零成本
- Memory 检索：只检索相关记忆注入 prompt，不全量注入
- 技术方案：SQLite + numpy 余弦相似度（轻量，无额外依赖）
- 后续可升级：PostgreSQL + pgvector

### Phase 9: Web UI
- 前端框架待定（React / Vue / Svelte）
- 实时显示多 agent 讨论流
- 执行过程可视化（tool call 时间线）
- Skill 管理界面

### Phase 10: MCP Server 扩展
- 支持外部 MCP server 接入
- 扩展工具能力（数据库查询、API 调用等）

---

## 🔒 商业化（保留，等定制需求）

### 变现路径
1. **API 按量计费**（最快）— 加 API key + 用量统计
2. **Skill 市场**（最有壁垒）— skill 评分/审核/分发
3. **托管 SaaS** — Web UI + 多租户 + 计费
4. **企业定制部署** — 私有化 + 对接内部系统
5. **IDE 插件** — VS Code / JetBrains

---

## 技术决策摘要

| 决策 | 选择 | 理由 |
|------|------|------|
| 工具层 | 自建 Python 工具函数 | 全栈平台必须控制执行过程 |
| 执行模型 | OpenAI-compatible API | CLI 子进程无法做 tool calling |
| 讨论模型 | 保留 CLI provider 选项 | 讨论不需要 tool calling |
| 沙箱 | Docker ephemeral container | 安全隔离，用完即删 |
| 学习范围 | 讨论 + 执行 + 失败 | 全方位学习 |
| Skill 匹配 | 关键词 → LLM → embedding（渐进） | 平衡成本和效果 |
| 测试 | 单元(mock) + 集成(LLM-as-Judge) | AI 输出需要 AI 评判 |
| 部署 | Docker Compose | 用户一键启动 |
| 向量检索 | SQLite + numpy（计划） | 轻量，无额外依赖 |
