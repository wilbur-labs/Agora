# Agora 1.0 实用教程

这份教程面向 Windows PowerShell，目标是从干净检出开始，把 Agora 的 API、静态 Control Plane、确定性正式验收和 Task CLI 全部跑起来。

## 1. 先理解 Agora 在做什么

Agora 管理的是一条权威交付链：

```text
Project -> Task -> Stage -> Run -> Artifact/Evidence -> Gate -> Handoff/Done
```

它不是让多个 AI 自由聊天的聊天室。旧 0.5 Council 已经删除。只有你明确执行 `agora task consult` 时，Agora 才会调用当前 Stage 已固定的一个 runtime；返回内容仍是候选建议，必须由人类 adopt 或 reject。

Agora 1.0 有三层使用方式：

1. `scripts/run_task_acceptance.py`：不调用任何 AI，验证正式控制链、Gate、人工批准和持久化。
2. Web Control Plane：查看 Task 权威状态，并处理 Attention、咨询候选和 Plan approval。
3. `agora task` CLI：创建、执行、检查、恢复真实 Task；只有这层可能调用 Codex、Claude Code 或 Kiro。

## 2. 前置条件

安装并放入 `PATH`：

- Git；
- Python 3.10 或更高版本；
- `uv`；
- Node.js 22 和 Corepack；
- 只在需要真实 AI Stage 时安装并登录相应的 `codex`、`claude`、`kiro-cli`。

Kiro 当前不可用也不影响 API、UI 和确定性验收。它仍保留在配置中；如果一个真实 Stage 明确固定到 Kiro，而 Kiro 不可用，该 Stage 会如实阻塞，不会自动换成 Codex 或 Claude。

## 3. 锁定安装

在仓库根目录执行：

```powershell
cd D:\Projects\Agora\backend
uv sync --locked --extra dev

cd ..\frontend
corepack pnpm install --frozen-lockfile
corepack pnpm build
```

`pnpm build` 会生成 `frontend/out/`。FastAPI 直接提供这个静态目录，因此正常使用时不需要单独长期运行 Next.js dev server。

## 4. 设置本地认证

生成一个只保存在本机进程环境中的长随机值。例如：

```powershell
$env:AGORA_CONTROL_PLANE_TOKEN = [Convert]::ToHexString(
  [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
)
```

`config.yaml` 把这个 secret reference 映射到 `local-owner`。变量为空或未设置时，任何人都不会获得 Control Plane 权限。

如需使用单独数据库或隔离实例，可以创建自己的 YAML，然后设置：

```powershell
$env:AGORA_CONFIG_PATH = 'D:\path\to\isolated-config.yaml'
```

显式配置路径优先于默认仓库配置。不要把 bearer token 提交进 Git。

## 5. 启动 Agora

在新的 PowerShell 中保留同一个 token，然后启动：

```powershell
cd D:\Projects\Agora\backend
$env:AGORA_CONTROL_PLANE_TOKEN = '上一节生成的值'
uv run uvicorn agora.api.app:app --host 127.0.0.1 --port 8000
```

检查健康状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

应看到 `status = ok` 和 `version = 1.0.0`。

浏览器打开：

```text
http://127.0.0.1:8000/control-plane
```

在连接区域填写：

- Project：`agora`
- Bearer token：`AGORA_CONTROL_PLANE_TOKEN` 的值

点击 Discover/Connect 后选择 Task。token 只放在当前浏览器 tab 的 `sessionStorage`；点击 Forget 会清除受保护状态。

## 6. 先跑无 AI 正式验收

打开另一个 PowerShell，在仓库根目录执行：

```powershell
cd D:\Projects\Agora
.\backend\.venv\Scripts\python.exe scripts\run_task_acceptance.py
```

成功收据必须包含：

- `acceptance_mode: deterministic_non_ai`
- `provider_or_model_called: false`
- 三个 Stage 和三个 Gate 全部 `passed`
- 人工批准前存在 `plan_approval`
- SQLite 重开后 Task 为 `completed`
- `temporary_workspace_removed: true`

这个命令证明 Agora 控制逻辑可运行，不代表真实 Codex/Claude/Kiro 的输出质量。

## 7. 创建第一个 Task

先只创建，不立即调用 AI：

```powershell
cd D:\Projects\Agora\backend
uv run agora task start `
  --contract ..\docs\examples\bounded-control-plane-api-task-contract.json `
  --tokens 30000
```

第一行会打印 `task_...`。把它保存下来：

```powershell
$taskId = 'task_把这里替换为实际ID'
uv run agora task status $taskId --json
uv run agora task status $taskId --protocol-v1 --json
```

`status` 是权威状态入口。重点看：

- 当前 Task/Plan/Stage 状态；
- `next_safe_action`；
- Run 的 process、transport、schema、semantic 四个维度；
- Token reservation/settlement；
- Attention、咨询候选和需要的人类操作。

回到 Web Control Plane 并重新 Discover，即可看到新 Task。

## 8. 运行真实 Stage

先观察本机 runtime，不会改变路由：

```powershell
uv run agora task capabilities
uv run agora task preflight $taskId
```

每次只推进一个 Stage，便于检查成本与结果：

```powershell
uv run agora task next $taskId `
  --protocol-v1 `
  --allow-unbounded-native-usage
```

`--allow-unbounded-native-usage` 是必须的安全确认：Task Token envelope 是 Agora 的准入/记账边界，不是 provider 的硬上限。它不会放宽 Gate、Evidence、repository hash 或语义校验。

每次执行后先检查：

```powershell
uv run agora task status $taskId --protocol-v1 --json
```

确认当前 Stage/Gate、实际 usage、Attention 和 next safe action 后，再决定是否执行下一次 `next`。不要在 Kiro 不可用时盲目使用 `task run` 连续推进到固定为 Kiro 的 Stage。

## 9. 明确的人类决策与咨询

Task 因缺少人类决策而阻塞时，可直接记录决定：

```powershell
uv run agora task decide $taskId architecture_choice `
  --value '采用方案A' `
  --reason '人工确认了兼容性和回滚边界'
```

如果确实需要当前 Stage 的单个 runtime 给建议：

```powershell
uv run agora task consult $taskId architecture_choice `
  --question '比较方案A和方案B，只给出风险与证据来源' `
  --tokens 2000 `
  --allow-unbounded-native-usage
```

咨询不会启动 AI 之间的讨论，也不会修改 Stage/Gate。使用 `status --json` 或 Web Control Plane 找到 `candidate_id` 和当前 `plan.version`，然后由人类二选一：

```powershell
uv run agora task adopt $taskId candidate_实际ID `
  --expected-plan-version 3 `
  --reason '人工核对后采纳该候选'

# 或者
uv run agora task reject $taskId candidate_实际ID `
  --expected-plan-version 3 `
  --reason '证据不足，不采纳'
```

adopt 只绑定一个 TaskDecision 并增加 Plan version；reject 只记录 disposition。两者都不会调用 runtime，也不会直接推进 Stage/Gate。

## 10. 人工批准

只有所有必需 Stage 和 Gate 都通过后才能批准：

```powershell
uv run agora task approve $taskId `
  --reason '已核对所有正式 Artifact、Evidence 和独立审查结果'
```

也可以在 Web Control Plane 的 Human checkpoint 中操作。按钮只会在权威投影表明这是唯一合法的人类动作时出现，并绑定可见的 Task/Plan version。

## 11. 失败、恢复和重试

进程退出 0 不等于语义成功。遇到阻塞先查看：

```powershell
uv run agora task status $taskId --protocol-v1 --json
uv run agora task resume $taskId
```

`resume` 负责中断修复，不会重复启动仍存活或无法确认状态的进程。只有修复明确 blocker 后才重置某个 Stage：

```powershell
uv run agora task retry $taskId STAGE_KEY --protocol-v1
```

`retry` 只改变可重试状态，不启动模型。后续真正 dispatch 仍需再次执行带安全确认的 `next` 或 `run`。

## 12. 停止与日常维护

直接启动的服务用 `Ctrl+C` 正常停止。Docker daemon 可用时也可以：

```powershell
docker compose up -d agora-api
docker compose down
```

Docker 不是 Windows 直接启动的前提。不要用 `docker compose down -v`，除非明确要删除容器卷；不要手工删除 `.agora/` 或 `data/` 中的权威状态。

升级代码后重复锁定同步和静态构建：

```powershell
cd backend
uv sync --locked --extra dev
cd ..\frontend
corepack pnpm install --frozen-lockfile
corepack pnpm build
```

开发者完整验证命令见 `AGENTS.md`。动态角色、任意本地模型和运行时替换会作为 1.0 之后的独立增量实现；当前不要通过修改持久化状态或伪造 runtime 名称绕过固定路由。
