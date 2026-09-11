# W3-a — 远端执行（SSH）

> 本仓库 private，所以这里可以写真实主机与凭据引用。**这些内容绝不能进 public `wilbur-labs/Agora`。**
> 与 `docs/W9-mockup-alignment.md` 并列：那份管界面信息架构，这份管「代码在另一台机器上跑」。
> 两台机器的 agent 都读这份，决策写在这里。

## 现状

**U1 + U2 已在本分支**（commit `253dd19`，从 public 仓库 `feat/ssh-remote` 移植）：

| 单元 | 内容 | 文件 |
|---|---|---|
| U1 `env-launch` | `Environment` 第三能力 async `launch()` + `ProcessHandle`/`_ByteReader`/`LocalProcessHandle` + `GRACE_SECONDS`；D5 `resolve_git_revision` 加 `env` 参数并对非本地环境 fail-closed | `agora/environments.py`、`agora/orchestration/protocol_context.py` |
| U2 `remote-env` | `RemoteEnvironment`（asyncssh，lazy connect + same-loop + 有界重试 + keepalive）、`RemoteProcessHandle`、`AsyncExecutionEnvironment` Protocol | `agora/environments_remote.py` |

安全边界（U2 已做实，别削弱）：host key **strict 做实**、`tofu` **fail-closed 拒绝**而非静默降级为 strict（
「一个没人能测的首连 pin 比诚实的拒绝更糟」）· 凭据从 config 指名的**环境变量**读、不存在 config 里 ·
`launch` 转发环境变量走**显式 allowlist**（本机 token 不过界）· 命令是 argv + 逐元素 quote ·
`probe` 走 SFTP `lstat`/`stat`，所以「不存在」来自 typed ENOENT 而不是解析 shell 输出（后者分不清
absent 与 unreadable）· 输出按 **bytes** 取回、由我们按 UTF-8 解码（spike 时 cp932 把日文变成了替换字符）。

依赖：`asyncssh>=2.14.2`（下限排除 Terrapin **CVE-2023-48795**；2.14.1 修的是消息注入
CVE-2023-46445/46446）。

**dispatcher 刻意保持 W8 的样子。** U1 原本对它只做一步最小收敛（不自己 spawn，改成走 environment），
而 W8 之后围绕 Windows job object 与进程树重写了那一带、文件几乎翻倍；而「按 adapter 选 Local/Remote」
本来就是 **U5** 的工作，会再次重写同一段。在刚变过的树上改两次，merge 就变成了 rewrite。

因此 `tests/smoke/test_remote_env_against_env_launch.py` 里驱动 dispatcher `_stop` 的那条 smoke 现在
**skip**，而且它自己判断：读 `_stop` 的签名，**一旦第一个参数接受 `ProcessHandle` 就自动解除**。等人记得
去删的 skip，会活得比它等的东西更久。

测试：移植的三个套件 **43 passed / 3 skipped**；改动周边回归（protocol / dispatch / execution / handoff /
deliverables / workspaces）**498 passed / 0 failed**。

## 未完成

### U3 `remote-worktree` —— 下一个。设计已分析完，未定稿（**别重做这段分析**）

目标：provisioner 远程化。D2 form-2 = **用户预置 `remote_project_root`，Agora 绝不自动创建 root**。

上游作者在 `agora/workspaces/provisioner.py` 的 `__init__` 里留了一段直接写给本单元的注释，指明「不是换个
实现就完事」。据此盘出 8 项本地假设：

| # | 位置 | 本地假设 | 远程需要 |
|---|---|---|---|
| 1 | `ensure_source_repo` | `root.mkdir(parents=True, exist_ok=True)` | **不能建 root**，不存在则 raise |
| 2 | `ensure_source_repo` | `gitignore.write_text(".agora/\n")` | 远端写文件 |
| 3 | `provision` | `workspace.parent.mkdir(parents=True)` | 远端建目录（⚠️ 递归 mkdir 会**间接重建 root**，违反 D2 form-2） |
| 4 | `provision` | `workspace.rmdir()` | 远端删空目录 |
| 5 | `provision`、`_inspect` | `any(workspace.iterdir())` | 远端列目录判空 |
| 6 | `_assert_allowed`、`_resolve`、`_same_path` | `expanduser().resolve()` 在**本机**解析 symlink 并比较 root | 远端规范化（**安全关键**，上游作者标注 "the one that matters for safety"） |
| 7 | `status`、`_inspect`、`_source_is_own_repo`、`_git_sync` | 全同步 | 远程往返须 async |
| 8 | — | 上游无 `teardown_worktree` | 新增（Q5=C：默认保留，可配清理） |

**能力缺口（U3 的核心问题）**：`Environment` = sync `run` + sync `probe` + async `launch`；
`AsyncExecutionEnvironment` = async `run`/`probe`。**都没有**列目录 / 写文件 / mkdir / rmdir / realpath，
而 U4 的 `read_file` 也要读远程文件内容。

**四个决策（我的推荐 + 独立评审的修正）**

- **Q1 缺口怎么补** → **新增独立的 `AsyncFilesystem` 协议**，由远程环境通过 typed `env.fs` accessor 暴露
  （**不返回裸 `SFTPClient`**），U3/U4 构造时接收。**不改已冻结的 `Environment` 契约**（评审的修正：比扩充
  Environment 更小，U1/U2 一行不动；若只有远程路径消费，`LocalEnvironment` 不必立即实现）。
  最小能力集：`realpath` / `is_empty_dir` / `read_file` / `write_file` / `mkdir` / `rmdir`，`probe` 复用。
  `is_empty_dir` 比 `list_dir` 更窄（除判空无别的枚举需求）。契约里放操作语义（错误分类、覆盖行为、
  symlink、大小上限）；SFTP 会话 / 文件句柄 / 分块传输留实现内部。
  **成本提醒**（评审）：主要成本不是 6 个包装方法，而是错误分类、测试替身、路径类型、安全语义的一致性。
  ⚠️ 这是**契约新增**，动手前先提 CR。
- **Q2 远端路径 confinement** → **SFTP `realpath` 规范化后比较**。评审补充：这只是必要条件，还须
  ① 两侧都规范化、用 POSIX **路径分量**判包含（不能字符串前缀）② 处理目标尚不存在的情况
  ③ 确认 SFTP 与 SSH exec 看到**同一棵文件系统**（chroot/映射可能不同）④ 认识到规范化后仍有被换成
  symlink 的竞态。
- **Q3 同步/异步边界** → **双轨**（同 CR-4 精神）：同步 `status`/`is_ready_worktree` 只服务本地、遇远程
  环境**显式报错**而非静默答本地；远程走 async 新方法。评审补充：保留同步兼容**不免除集成工作** —— 远程
  流程最终经过的 4 个调用点仍要接异步路径（属 U4/U5）。另纠正：`run_coroutine_threadsafe` 本身不违反
  same-loop 约束，反对该方案的真正理由是阻塞 / 死锁 / 生命周期复杂度。
- **Q4 `is_ready_worktree` 归属** → 留在 `agora/execution/deliverables.py`（它是**同步模块级函数**，被
  `execution/router.py:81`、`dispatcher.py:515`、`dispatcher.py:1237`、`handoff.py:170` 共 4 处同步调用），
  U3 加 async 远程版并由 provisioner 契约方法委托，单一实现、无语义重复。

**评审提出的 5 条安全条件（实现时必须落）**

1. **判空必须是完整证明**：只忽略精确的 `.` / `..`，隐藏文件算内容；只有枚举正常结束且无条目才返回
   True；权限错误 / 断线 / 解码失败一律抛错。`rmdir` 报非空必须停手，**绝不升级为递归删除**。
2. **不存在的路径不能「规范化后放行」**：验证最近的已存在父目录，再按受限分量创建；拒绝 `..`、悬空链接、
   不确定状态。**显式确保 project root 仍存在，避免递归 mkdir 间接重建 root**（对应 #3）。
3. **SFTP 与 git 必须看到同一棵文件系统**：验证，或写成受支持部署的硬前提。
4. **保护现有内容与仓库归属**：`.gitignore` 不能无条件覆盖；防止经 symlink 写到 root 外；seed commit 只
   提交预期文件；readiness 要验 worktree 注册 + 仓库归属 + 预期分支；**teardown 必须验证归属，不能只凭
   路径或分支名删**。
5. **TOCTOU** —— 评审称为最大遗漏。规范化后、真正写入或执行 git 前，路径可能被替换。

**唯一悬空的决策**：TOCTOU 威胁模型。A = 只防误操作与配置错误，把「恶意 agent 并发改目录」明确记为 out of
scope（与本地版当前保证水平一致，vision 要求的是「与本地同标准」）；B = 也防恶意 agent（需服务端受限操作
或 OS 隔离，超出 MVP 且无真机无法验证）。**推荐 A + 在 CR 留痕**，尚未确认。

### U4 `remote-deliverables`

`list_changes` / `snapshot_changes` / `read_file` / `handoff_commit`，全 async，操作远程 worktree。复用上游
`MAX_FILES` / `MAX_FILE_BYTES`；git 失败返回空列表（与上游 `list_changes` 一致，不抛异常）；`read_file`
对单个不可读文件抛 `FileNotFoundError`/`PermissionError`/`OSError`（调用方跳过）；输出过 redaction。
依赖 U3 的 `AsyncFilesystem.read_file`。

### U5 `config-and-tests`

`RemoteAdapterConfig`（`ssh_host`/`ssh_port`/`ssh_user`/`remote_workspace`/`remote_project_root`/
`secret_ref`/`host_key_mode`/`known_hosts_path`/`cleanup_worktree`，`extra="forbid"`）+ config loader 接入 +
**dispatcher 按 adapter 选 Local/Remote**（没有它远程功能串不起来；那条自动解除的 smoke 也在等它）+ 三层测试。

### Build & Test

必须**真跑**。真连测试机 **`10.36.94.35`**（公司网，Ubuntu 22 / git 2.34.1 / claude + kiro-cli，密码认证
已验证）。测试从 `AGORA_SSH_TEST_HOST` 读地址，凭据从 `AGORA_SSH_KEY_<name>` / `AGORA_SSH_PASSWORD_<name>`，
需 known_hosts 条目。只有这一步能验证本机验不了的部分：channel signal、真实 known_hosts、真 SFTP。

## 未闭合 CR（沿用 SSH 任务的编号）

| CR | 类型 | 内容 | 归属 |
|---|---|---|---|
| CR-2 | clarify | 配置引入「环境」层，host/凭据跨项目复用（mockup 把环境作为一等公民 tab） | U5 |
| CR-6 | clarify | 完整 TOFU（首连 `get_server_host_key` + 原子写 known_hosts + 重连 strict）推迟；现状 tofu fail-closed 报错 | 有真机可验证时 |
| CR-9 | clarify | `uv lock` 解析成功 ≠ 依赖声明完整。要在**干净环境** `uv sync` 后跑全套测试 | Build & Test |
| CR-10 | refine | 非法 UTF-8 的 `errors="replace"` 覆盖只打到同步本地 `run()`；异步本地与 `environments_remote.py` 的远程 decode 站点仍无覆盖 | U5 或 Build & Test |

## public 仓库那份怎么办

`wilbur-labs/Agora` 的 `feat/ssh-remote` 分支（commit `5287077`）仍在，内容与这里移植的相同（除
dispatcher）。它是公开可见的实现，**不含任何内网信息**。后续开发在本仓库进行；若要回馈 public，需逐条
确认不含主机/凭据/内网流程。
