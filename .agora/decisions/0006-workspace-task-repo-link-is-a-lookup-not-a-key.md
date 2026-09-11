# 0006. workspace 任务 → 项目的连接：按 root 查，但不拿它当键

- **Status**: accepted
- **Date**: 2026-09-07
- **Layer**: task:agora
- **Supersedes**: 无。与 [[0003]] 相邻（那条定死 `Environment` 只有 `run`+`probe`），并直接受 `Project.root` 上写着的那条约束管辖

## Context

W7 把既有 workspace 的任务只读读进来之后，用户第一次真用就问了两句话：
**「我要看项目进行到哪里了、我要继续推进，怎么做？」**（[[CR-37]]）

第二句在当时的实现里没有答案：登记进来的 workspace 任务，与控制平面里的 project 之间
**没有任何连接**。Continue / Run 那条链路（W0(b) 打通的）只对 project 生效，而 workspace 任务
只是一堆被读出来的 markdown。

接口点其实一直在数据里：`task.yaml` 顶层就写着

```yaml
primary_repo: D:/Projects/Agora
repos:
  - name: agora
    path: D:/Projects/Agora
```

也就是说「这个任务的活在哪个仓库里干」这件事，工作模型**从来都记着**，只是没人读过。

**但 `Project.root` 上有一条写死的约束**（`projects/models.py`）：

> Nothing may key, join, or dedupe on `root`, or that migration turns into a rewrite.
> `project_id` is the only key.

理由写在同一段注释里：`root` 是项目**在本地环境里的位置**，不是它的身份；W3（SSH）落地时
root/workspaces 要迁进 `project_environments` 表，按环境解析。任何按 root 建立的连接，
到那天都会变成迁移的阻碍。

于是本条要回答的是：**在不违反那条约束的前提下，怎么把「读进来的任务」接到「能干活的项目」上。**

## Decision

**按 `primary_repo` 对 `Project.root` 做一次查表，产出一个「候选 project_id」，只用于给人一个
可以点的入口；不存、不建索引、不派生身份。**

具体边界：

- 匹配只发生在**一个函数**里（`projects/workspace_tasks.py` 的 `link_repo`），入参是
  `{project_id: root}` 的临时映射，由 API 层每请求现建
- 结果**不写库**、不进任何表、不参与任何判定；它只决定界面上显示哪一句话
- 产出是四态而不是布尔：`linked` / `not_registered` / `not_recorded` / `unknown`
  —— 三种「连不上」各自对应不同的下一步动作（去登记那个目录 / 去 task.yaml 里写 primary_repo /
  什么都别说），而 `unknown` 是「没看」或「manifest 读不到」时唯一有资格说的话
- 路径按 `Path` 比较而不是字符串（Windows 大小写与分隔符），这与登记路径刚修过的那个缺陷同源
- W3 落地、root 迁进环境表那天，**要改的只有这一个函数**

## Consequences

- 用户拿到了那句话的答案：任务行上直接给出「打开 agora」，点进 `/workspace?project=agora`
- 代价是这个连接**依赖 `primary_repo` 写得对**。写错、写的是相对路径、或者仓库在另一台机器上，
  都会显示成 `not_registered` —— 这是诚实的（我们确实没在已登记项目里找到它），但**它对
  「同一个仓库在服务器上」这种情况会一直说 not_registered**，而那正是 W3-a 要处理的世界。
  真到那时，这个函数要问的不是「哪个 project 的 root 等于这个路径」，而是「哪个 project 的
  **某个环境**的 root 等于这个路径」——形状不变，查的表变了
- 不会拖慢列表：映射每请求建一次，不是每任务查一次

## Alternatives

1. **在 task.yaml 里写 `agora_project: agora`，按 project_id 连接（不碰 root）。**
   最干净，完全绕开约束。**被否**：W7 的整个前提是「读既有的工作，不要求你重录一遍」，
   而这要求用户回头去改每一个 task.yaml —— 用户的原话正是「工作你不能直接导入吗」。
   将来若真需要精确指定，这条可以作为**可选的覆盖字段**加回来，不冲突。
2. **让用户在界面上手工把 workspace 任务和 project 配对，把配对存进控制平面库。**
   **被否**：这就是「按 root 建立持久关联」的更糟版本——它把一次查表变成了要维护的状态，
   还多出一套同步问题（项目改名、仓库搬家）。而且它需要用户为每个任务点一次，
   来换一个 `primary_repo` 已经写着的答案。
3. **不做连接，只显示 `primary_repo` 这个路径字符串。**
   诚实、零风险、也确实解决了「看得到」。**被否**：它没有解决用户问的第二句
   （「怎么继续推进」）——一个路径不能点，人还是得自己去别的页面找那个项目。
   [[adr/0020]]（框架层「Reach before polish」）说的就是这种：够不着的信息不算打通。
4. **在 `Project` 上加一个 `workspace_task` 反向字段，由 project 指向 workspace 任务。**
   **被否**：方向反了。workspace 是既有事实、project 是产品内的登记；让产品的表去记
   「我对应外面哪个 markdown」，等于把外部文件的路径变成控制平面的持久状态——
   `primary_repo` 改了就不一致，而没有任何机制会发现。
