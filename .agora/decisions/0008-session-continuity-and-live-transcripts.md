# 0008. 会话续接与流式记录的机制

- **Status**: accepted
- **Date**: 2026-09-08
- **Layer**: task:agora
- **落地**: [[0007]] 定的原语（一个任务 = 一个持续会话）；本记录是它的机制选型
- **里程碑**: W8 第 1~3 条 exit_criteria

## Context

[[0007]] 定了「用户看到的原语是持续会话」，并列了三条边界。它没有定**怎么做**，
而这里有四个各自都有替代方案的决定：会话 id 存在哪、resume 命令怎么构造、
流式输出走什么传输、读不到会话时怎么办。

2026-09-08 凌晨对三家 CLI 做了实测（不是读文档），结论直接决定了下面的形状：

| | 起始 | 续接 | session id 来源 | 流式 |
|---|---|---|---|---|
| codex 0.149.1 | `exec --json --sandbox workspace-write` | `exec resume <id> --json` | `thread.started.thread_id` | 按 item |
| claude 2.1.246 | `-p --session-id <uuid> --output-format stream-json --verbose` | 同上但 `--resume <uuid>` | **调用方指定** | 按 token |
| kiro-cli 2.19.1 | `chat --agent-engine v2 --output-format stream-json` | 同上加 `--resume-id <id>` | `metadata.sessionId` | 按 chunk |

三个「按显然写法会失败」的点，都是被 CLI 当场拒绝才发现的：

1. **`codex exec resume` 完全不接受 `--sandbox`**（也不接受 `-C/--cd`）。
   sandbox 只能走 `-c sandbox_mode="workspace-write"` 配置覆盖。
2. **`claude -p --output-format stream-json` 必须带 `--verbose`**，否则 exit 1。
3. **`kiro --output-format stream-json` 必须带 `--agent-engine v2`**，v1 引擎拒绝，
   且这个拒绝本身是一条 `runError` 事件（所以配置错会显示成一行可读的错误，
   而不是一个空白的对话窗口）。

另外，`kiro-cli` 现在**有** `--output-format stream-json` 了 ——
`lessons/coding-agent-cli-headless-resume.md` 写的是「Kiro 没有结构化聊天输出」，
七个月不到就翻了。那条 lesson 自己的第一条 Gotcha 预言了这件事。

## Decision

**四条，逐条附被否方案。**

### 1. resume 命令是**独立的模板**，不是「起始命令 + 一个 id」

`ExecutionAdapter` 同时持有 `command_template` 与 `resume_template`，各自恰好一个
`{prompt}`，resume 的那个恰好一个 `{session_id}`。理由就是上面第 1 点：
codex 两个子命令的 flag 集合不同，按「起始命令追加 id」构造的 resume 会死在第一个 flag 上。

**连带发现的一个真缺陷**（不是设计，是既有行为）：配置只覆盖 `command`（测试里的 stub、
安装里的 wrapper）时，原本会继承**默认的** `resume_command`。也就是说，一个把 `claude`
换成 `echo` 的配置，第二轮会去跑**真正的 claude**。现在覆盖 `command` 而不给
`resume_command` 的适配器一律「不能续接」——真且安全。

### 2. session id 存**独立的表**，不进 `result_metadata`

`agent_sessions(task_id, adapter)` + `execution_run_sessions(run_id)`。
因为 `result_metadata` 这一列是**整体写入**的：run 结束时 `finish()` 用结果覆盖它，
而这正是 `models.py` 里 `IDENTITY_METADATA_KEYS` 那段事故的成因。
在启动时往那一列写会话事实，等于让 run 自己的结果把它擦掉。

会话表还带 `workspace`，因为**三家 CLI 的会话都按工作目录分域**
（claude 按 cwd slug 建目录、codex 与 kiro 默认按 cwd 过滤）。换了 worktree 就读不到原会话，
这是必须能判定的，不是细节。

### 3. 流式走**按字节偏移量轮询**的记录文件，不是 WebSocket / SSE

渲染后的文本按事件追加到 `<control-plane db 目录>/run-logs/<run_id>.log`，
API 是 `GET /api/runs/{id}/output?offset=N`，返回 `{text, next_offset, reachable}`。

**决定性理由**：这和远端环境用 `ssh tail -c +<offset>` 能提供的**是同一个契约**。
W3-a（[[CR-31]]）要把 run 改成「远端 detached 启动 + 靠读观察」，那时传输会变，
而这个端点的形状不变。一个 WebSocket 现在能用，等 run 搬到服务器上就得整个换掉。

顺带：记录文件落盘，所以关掉浏览器再打开，之前的对话还在——这是 W8 第 2 条验收线。

**脱敏在渲染后、写入前，按事件为单位做**。因为一次 8 KiB 的 socket 读可能把一个密钥
劈成两半，而一个事件渲染出来的文本是完整的单位。原始 JSONL 从不落盘。

### 4. 读不到会话 → **拒绝这一轮并说明**，绝不静默新开

`AgentSessionStore.decide()` 只在「本来就没有会话」时返回 start。
有会话但续不上（工作目录变了 / 适配器没有 resume 命令）→ 带理由拒绝，
并把会话标成 `unreadable`，**id 保留**。id 保留是因为它是那段对话唯一的把手，
删掉就把「读不到」变成了真丢。

界面上有第四种状态 `unsupported`：codex app-server 桥在进程内自己持有对话、
Agora 没实测过它的 resume 路径，所以那条链路上每轮都是独立调用。
**写下来而不是留空**——一个每轮从零开始的链路配上一个显示着连续对话的窗口，
正是 [[0007]] 要防的那件事。这一条记为 CR-39（defer），触发物是
`test_agent_sessions.py::test_an_unsupported_bridge_says_so_rather_than_staying_blank`。

## Consequences

- **usage 计量表改为按「输出格式」索引，不再按适配器名。** 原本
  `_USAGE_RESULT_FORMATS = {"claude": CLAUDE_JSON_V1}`，而 argv 才是真正决定格式的地方，
  两处各说一套。它们**确实已经不一致了**：`provider_usage.py` 里 codex 的 JSONL 解析器
  一直存在、一直不可达，因为 codex 的 argv 从来没传 `--json`。现在三家都能出计量。
- **kiro 的 token 数从「按字节除以四估算」变成「不可用」，但换来了真实的 credits**
  （`meteringUsage`）。少一个编出来的数字，多一个量出来的数字。
- **`runFinished.finalTextTruncated` 被当成一等事实**：kiro 自己说最终文本被截断时，
  记录里会写明，不会把截断的答案当完整的呈现。
- codex 在 stdin 不是终端时会读 stdin（实测打印 "Reading additional input from stdin..."），
  所以子进程一律 `stdin=DEVNULL`。继承一个永不 EOF 的句柄 = 一个永远不开始干活的进程，
  在对话窗口里和「agent 在深思」长得一模一样。
- 事件解读器遇到**不认识的事件类型必须写进记录**（「未解读的事件：X」），不能丢。
  一份静默省略了自己读不懂的东西的记录，读起来像一份完整记录。

## Alternatives

1. **会话状态由 Agora 自己持有，每轮重发全部历史。** 已在 [[0007]] 备选 3 被否，
   这里只补一条实测理由：codex 的 `file_change` / `command_execution` 事件说明
   agent 的内部状态（读过哪些文件、批过什么命令）根本不在文本历史里。
2. **WebSocket / SSE 推流。** 体验最好、延迟最低。**被否**：它是「本地子进程」这一个
   前提专属的传输，而 W3-a 已经定了 run 要在远端 detached 跑。届时要么再做一遍，
   要么在服务器上常驻一个转发进程——那正是 [[0007]] 备选 2 被否的形状。
   偏移量轮询在两种拓扑下是同一个契约。
3. **只给一家（codex）做会话，另两家以后再说。** 今晚能省一半工作。**被否**：
   三家的差异恰好在**语义**上（谁指定 id、resume 的 flag 集合、事件词汇），
   只做一家会把这些差异写死进调用点，第二家来的时候要重构而不是扩展。
   而且实测这一步已经做完了，成本主要在测量而不在实现。
4. **续接失败就自动开新会话（带一条提示）。** 用户永远不会被「拒绝」挡住。
   **被否**：这就是 [[0007]] 点名禁止的那件事。提示会被忽略，而丢掉的上下文
   要到 agent 答得莫名其妙时才被发现——那时已经花了钱、也污染了 worktree。
5. **把渲染后的文本存进数据库而不是文件。** 一处状态，事务性也更好。**被否**：
   一行一次写入会把控制平面库变成日志库（WAL 膨胀、锁竞争），而且
   `tail -c +<offset>` 那条远端复用路径消失了——远端不会有 sqlite。
