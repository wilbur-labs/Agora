# 0003. Environment 只有 run 和 probe 两个能力，agent 进程启动不在其中

- **Status**: accepted
- **Date**: 2026-09-04
- **Layer**: task:agora

## Context

W2 要把「跑在哪台机器上」变成可替换的东西（Local 现在、WSL/SSH 之后）。动手前把地形量了一遍
（2026-09-04 只读核对，非推测）：

**「跑一条命令」已经散成四处各写各的：**

| 位置 | 形态 |
|---|---|
| `workspaces/provisioner.py` | `_git`（async，`create_subprocess_exec`）+ `_git_sync`（`subprocess.run`），两个方法收敛了该文件里所有 git 调用 |
| `execution/deliverables.py` | `_rev_parse` 及另外两处 `subprocess.run`，各自 `shutil.which("git")` |
| `execution/handoff.py` | `_run_git` + 自己的 `shutil.which("git")` |
| `execution/dispatcher.py` | `create_subprocess_exec` 启动 **agent 进程**——见下，这一处形状不同 |

**「路径在不在」已经有两套口径，且是同一个问题的两个答案：**

- `agora/fsprobe.py` 存在（一次 `lstat`，跟随符号链接时再 stat 一次目标；**absent / unknown /
  present 分开**），
  `execution/dispatcher.py`、`execution/router.py`、`projects/registry.py` 都已改用它
- **`workspaces/provisioner.py` 完全没用它**：`_inspect` 里 `not workspace.exists()` 直接落进
  `WorkspaceState.MISSING`。一个因 ACL 读不到的 worktree 会被报成「还没建，去 provision 吧」，
  而正确的下一步是修权限。这就是 fsprobe 那篇 docstring 里写的、它存在的理由本身

**而 dispatcher 启动 agent 的那一处，形状和上面全都不一样**：它要 pid（`attach_pid`）、
要边跑边读两路流（`_read_tail`）、要能杀（`_stop`）、要 timeout 后仍拿到 tail。
它不是「跑完给我结果」，是「给我一个活的进程句柄」。

## Decision

**其一，`Environment` 协议只有两个能力：**

```python
class Environment(Protocol):
    def run(argv, cwd, timeout) -> CommandResult:   # 短命、有界、跑完收输出
        ...
    def probe(path) -> PathProbe:                    # absent/unknown/present 分开
        ...
```

`LocalEnvironment` 是唯一实现：`run` = `subprocess.run`，`probe` = `fsprobe.probe_path`。
`probe` 直接复用 `fsprobe` 的返回类型，**不另发明一套词汇**——`fsprobe` 的
absent / unknown / present 正好就是 W3 远程要表达的东西（「知道存在但读不到」≠「不存在」）。

**其二，`run` 的契约明写它不包括什么：它跑的是短命、有界、跑完才收输出的命令（git 这类）。
启动 agent 进程不走 `Environment`，dispatcher 那一处保持原样。**
这条写进 Protocol 的 docstring，不只是写在这里。理由是：远程启一个长跑进程要解决的是
流的转发、断连重连、远端 pid 的归属——那是 W0(a) 摸底出来的 resume 问题的另一面，
**现在没有远程实现可以校对，此刻定出来的接口只是猜测**。W3 真做 SSH 时按需要加第三个能力。

**其三，第一刀只落在 provisioner**：`_git` / `_git_sync` 改走 `Environment.run`，
`_inspect` 的 `exists()` / `is_dir()` 改走 `Environment.probe`，让读不到的 workspace
报成 `ERROR` + 原因，而不是 `MISSING`。`deliverables.py` 和 `handoff.py` 同批统一，
因为**它们是同一个问题的其他调用点**——「修完读取姿势不对的 bug，立刻搜同一个函数名的
其他调用点」是本项目已经付过学费的判据。

## Consequences

- `WorkspaceState.ERROR` 的含义变宽：原本是「上一次 provision 失败过」，现在还包括
  「这个 workspace 的状态查不出来」。两者共用 `error` 文本字段，前端已经在渲染它。
  代价是这两种 ERROR 在 UI 上暂时长得一样；W4 重做那层指标形态时再分。
- **接缝覆盖到哪里，是数出来的**（第六轮评审逼出来的；我先前写的「四处里的三处」自己就是错的
  ——而那句话恰恰是写来标注边界的）：
  - 冻结区**之外**的 git 调用点**全部**在接缝上：provisioner、deliverables、handoff。
  - `orchestration/protocol_context.resolve_git_revision` 是**第四处 git 调用点，不在接缝上**。
    它在 [[adr/0001]] 冻结的那棵树里、被 `orchestration/service.py` 调用，会拿**本机** git
    去跑一个远程 Environment 认为在别处的路径 → 记为 RESUME 的 `defer` CR。
  - 冻结区**外**另有五个模块出于别的目的直接起进程：`execution/dispatcher.py`（启动 agent，
    本 ADR 明示不覆盖）、`attention/bridges/codex_app_server.py`、`research/workers.py`、
    `tasks/clarify.py`、`api/settings_router.py`。
  - 冻结区**内**还有四个：`orchestration/protocol_context.py`（即上面那处 git）、
    `orchestration/runtime.py`、`orchestration/runtime_auth.py`、
    `orchestration/runtime_capabilities.py`。
  - **`agora/` 下一共 10 个模块能起进程**（在 AST 上数、不排除任何目录），其中一个是
    `environments.py` 自己——它是实现。所以「其余九个」才是上面那两行。
    **这个数错过两次**：先是「四」（排除了 `orchestration/`），后是「九」（默默把实现本身排除了）。
    两次的排除都成立、都没写出来——**没写出来才是缺陷，写出来就只是简写**。
  - **错的根源值得记，而且我犯了两次**：grep 时排除 `orchestration/`，因为它是冻结区——
    可问题问的是「哪里在跑 git」，不是「哪里在跑 git 且我可以改」。**用一个与问题无关的类别
    去过滤搜索范围，等于让答案漏掉一整棵树。** 第二次就发生在写下这条判据的同一轮里：
    我把上面那句「另有五个模块」也算漏了，因为用的还是那个带排除的搜索。
    **判据写下来不等于判据被执行；下一次搜索要先问「这个 --exclude 是问题的一部分吗」。**
  每次说到「W2 做了 Environment 抽象」都要带上这段，否则又是一次「声明的范围 > 检查的范围」。
- W3 接 SSH 时，`run` 和 `probe` 可以直接换实现；`dispatcher` 的 agent 启动**要单独设计**，
  它是 W3 真正的工作量所在，不是「顺手换个 Environment」。这个预期现在就写下来，
  免得 W3 开工时把它当成已解决。
- provisioner 的 `mkdir` / `iterdir` / `rmdir` / 写 `.gitignore` 这次不进 Environment。它们是**写**
  操作和目录枚举，和 `run`/`probe` 不是一类；远程要不要支持写，等 W3 有真实需求时再定。
- **还有一条比「写操作」更要紧，评审第一轮 P1-5 才点出来**：`_assert_allowed` 的越界防护
  （`expanduser().resolve()` + `is_relative_to`）解析的是**本机**的符号链接和根目录。给这个类塞一个
  远程 Environment，它会一边在本机建目录、一边让远端 git 在别处建 worktree，而且**拿本机的文件系统
  去约束远端的路径**——一个在本机 allowed root 内、但在远端穿过 junction 指到外面的路径会通过。
  所以 provisioner 里那句「W3 换实现即可，不用动这个文件」是错的，已改掉。**这不是留给 W3 的一个
  小尾巴，是 W3 的主要工作量之一**：远程要的是「谁来判断路径边界」这个问题的答案，而不是多一个方法。

## Alternatives considered

- **一步到位定义完整的 `Environment`（run / spawn / read / write / mkdir / glob …）** —— 否决。
  没有远程实现可以校对的接口是猜测；W0(a) 已经证明过一次「跨厂商能力矩阵必须做成可重跑探测，
  不能靠文档推断」。同一条教训适用于自己设计的接口：**先让本地这两个能力真的被两处以上用起来，
  再谈第三个。**
- **不做协议，直接把 `subprocess` 调用集中成一个 `git_runner` 模块** —— 否决。
  它解决了「四处各写各的」，但没解决「换机器」——而换机器正是 W2 存在的理由。
  不过它的一半是对的：本次确实顺手把四处 git 调用收成一处，只是收在 `Environment.run` 后面。
- **让 `Environment.run` 也负责启动 agent（返回一个 `Process` 句柄）** —— 否决，理由见 Decision 其二。
  一个方法同时表达「跑完给我结果」和「给我一个活句柄」，会让本地实现看起来支持远程，
  而远程实现真写的时候才发现流的语义对不上。
- **provisioner 保持 `Path.exists()`，只在 dispatcher 用 fsprobe** —— 否决。
  这就是当前状态，而当前状态的问题不是「有个地方没改」，是**同一个问题在同一个代码库里有两个答案**。
  留着它，下一个读代码的人有 50% 概率抄错那一个。
