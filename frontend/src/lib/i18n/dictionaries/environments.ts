// Environments and Skills — mockup v2's screens 3 and 4, restricted to what
// Agora can actually read. Both screens' main job is to keep "this is what is
// here" apart from "this is what I could not find out", and Skills' main job is
// to admit it reads nothing yet rather than showing a plausible inventory.

export const environments = {
  en: {
    title: "Environments",
    subtitle: "Where runs can happen, what is installed there, and what could not be read.",
    unknownShort: "unknown",
    local: {
      title: "This machine",
      current: "Current",
    },
    agents: {
      title: "Agent CLIs",
      installed: "installed",
      missing: "not installed",
      uninspectable: "could not be inspected",
      signedIn: "signed in",
      signedOut: "signed out",
      authUnknown: "sign-in not established ({status})",
      // About **token counts**, which is what the field behind it reports. Said
      // as "usage" it was wrong for kiro: that link reports no token counts and
      // its credits are read and stored, so the page denied a number it holds.
      usageRead: "token counts read per run",
      usageNotRead: "no token counts (other usage may still be read)",
      usageUnknown: "whether token counts are read is not known",
      unreadable: "The runtime probe failed, so which agents are installed here is not known — that is not the same as none being installed.",
    },
    projects: {
      title: "Projects on this machine",
      git: "git",
      notGit: "not a git repo",
      gitUnknown: "git status not established",
      worktrees: "{ready} of {total} worktrees present · {unknown} not established",
      none: "No projects registered yet.",
      unreadable: "The project list could not be read, so this may be incomplete.",
      importFailed: "The project import source was reached but not understood ({error}), so this list may be shorter than what is configured.",
    },
    capacity: "Capacity {global} runs at once, {project} per project · {running} running, {queued} queued",
    remote: {
      title: "Remote environments cannot be registered yet",
      body: "Agora has one environment implementation — this machine. The seam a remote one plugs into exists (an environment answers `run` and `probe`), but nothing implements it over ssh, so there is no way to tell Agora about another host, and no host to show as offline.",
      why: "That work is W3-a, whose harder half is not the ssh connection: a run has to start detached on the far side and be observed by reading, or closing the laptop kills it. Until then a project has to be on this machine for a run to reach it.",
      settingsLink: "Where projects and limits are configured →",
    },
  },
  zh: {
    title: "环境",
    subtitle: "run 能在哪里跑、那里装了什么，以及有什么是读不到的。",
    unknownShort: "未知",
    local: {
      title: "本机",
      current: "当前",
    },
    agents: {
      title: "Agent CLI",
      installed: "已安装",
      missing: "未安装",
      uninspectable: "查不了",
      signedIn: "已登录",
      signedOut: "未登录",
      authUnknown: "没确认登录状态（{status}）",
      usageRead: "每轮 token 数可读",
      usageNotRead: "不报 token 数（其他用量可能仍在采集）",
      usageUnknown: "能不能读到 token 数，不知道",
      unreadable: "运行时探测失败，所以本机装了哪些 agent 不知道——这和「一个都没装」不是一回事。",
    },
    projects: {
      title: "本机上的项目",
      git: "git 仓库",
      notGit: "不是 git 仓库",
      gitUnknown: "git 状态没确认",
      worktrees: "worktree {ready}/{total} 在位 · {unknown} 个没确认",
      none: "还没有登记任何项目。",
      unreadable: "项目列表读不到，所以这里可能不全。",
      importFailed: "项目导入源读到了但看不懂（{error}），所以这个列表可能比配置里的短。",
    },
    capacity: "并发上限 全局 {global} / 单项目 {project} · 正在跑 {running}，排队 {queued}",
    remote: {
      title: "远端环境还不能登记",
      body: "Agora 只有一个环境实现——本机。远端要插进来的那道接缝已经在了（环境只回答 `run` 和 `probe`），但没有任何东西用 ssh 实现它，所以既没法把另一台主机告诉 Agora，也就没有「离线的那台」可显示。",
      why: "那是 W3-a，而它更难的一半不是 ssh 连接：run 必须在对面 detached 启动、靠读来观察，否则合上笔记本它就死了。在那之前，项目得在本机上，run 才够得到它。",
      settingsLink: "项目和并发上限在这里配 →",
    },
  },
} as const;

export const skills = {
  en: {
    title: "Skills",
    subtitle: "The tier model is decided. The inventory is not read yet.",
    notRead: {
      title: "Agora reads no skills yet",
      body: "There is no skill reader in this build. `backend/agora/skills/` is an empty package — the v0.1–0.4 extractor was deleted along with the council-era code, and nothing replaced it. So this screen has no inventory to show, and it shows none rather than a list that would look like one.",
      where: "The skills themselves are real, and they are files: the workspace's `lessons/*.md`, plus each agent CLI's own skill directories. Nothing here has opened any of them.",
      whatItWouldTake: "Building it is the same shape as the workspace-task reader already in this build (W7): a fixed path, no searching, no fallback, and \"could not be read\" reported as itself. The publish gate below is the part that needs care, not the listing.",
    },
    decisions: {
      title: "Decided at the mockup review (2026-08-27), kept here so they are not re-argued",
      tiers: {
        title: "Three tiers: project, workbench, registry",
        body: "Project-only, available to every project, and published. Most skills stop at the first tier, and that is the right outcome. Promotion is a deliberate act with an applicability test; nothing is promoted automatically.",
      },
      publish: {
        title: "Publishing is per-skill, and classification comes before redaction",
        body: "Default is that nothing is uploaded. A mechanical gate (IP, hostname, email, .local/.corp, company and person names) is the last check, not the first, because it cannot catch the dangerous kind: a business process with no identifier in it at all. The test is whether someone who knows nothing about your employer could read it and still know nothing.",
      },
      usage: {
        title: "\"Used by N projects\" is not shown; \"installed in N\" and \"last updated\" are",
        body: "Claude decides on its own whether to load a skill, in-process, so usage is not observable from outside. A \"used by 5 projects\" figure would be precision Agora cannot have — worse than no figure, because it would guide decisions. What it can honestly report is what you installed and when it last changed.",
      },
      noAutoExtract: {
        title: "No \"repeated solution detected → skill generated\"",
        body: "That was `skills/extractor.py` in v0.1–0.4. It was deleted. This is evidence, not a prediction.",
      },
    },
    workbenchHint: "Skills are attached to work, so the place they will first appear is a task's own conversation:",
  },
  zh: {
    title: "技能库",
    subtitle: "分层模型已经定了。清单还没有被读。",
    notRead: {
      title: "Agora 还读不到任何技能",
      body: "这个版本里没有技能读取器。`backend/agora/skills/` 是个空包——v0.1–0.4 的自动提取器随议会时代的代码一起被删了，没有东西接替它。所以这一屏没有清单可显示，而它选择什么都不显示，而不是摆一份看起来像清单的东西。",
      where: "技能本身是真的，而且就是文件：workspace 的 `lessons/*.md`，加上三家 agent CLI 各自的技能目录。这里没有打开过其中任何一个。",
      whatItWouldTake: "要做的话，形状和这个版本里已有的 workspace 任务读取器（W7）一样：固定路径、不搜索、不 fallback、读不到就说读不到。需要小心的是下面那道公开闸，不是列清单。",
    },
    decisions: {
      title: "2026-08-27 mockup 评审定下来的，记在这里以免重新吵一遍",
      tiers: {
        title: "三层：项目技能 / 工作台技能 / 公开 registry",
        body: "只在本项目可见、所有项目可用、已公开。多数技能会停在第一层，这是对的。晋升是个要过适用性测试的显式动作，不自动提取。",
      },
      publish: {
        title: "公开是逐条的，而且先分类、再谈脱敏",
        body: "默认一条都不上传。机械闸（IP / 主机名 / 邮箱 / .local·.corp / 公司名 / 人名）是最后一道而不是第一道，因为它抓不到真正危险的那类：一段里面一个标识符都没有的业务流程。判据是——给一个完全不知道你公司在做什么的人看，他读完仍然不知道。",
      },
      usage: {
        title: "不显示「被 N 个项目使用」，显示「安装在 N 个」和「最后更新」",
        body: "Claude 在进程内自主决定要不要加载某个技能，所以使用率在外面观测不到。一个「被 5 个项目使用」的数字是 Agora 拿不到的精度——比没有数字更糟，因为它会引导判断。它能诚实报的是：你装了哪些，以及它上次变动是什么时候。",
      },
      noAutoExtract: {
        title: "不做「检测到重复解法 → 自动生成技能」",
        body: "v0.1–0.4 的 `skills/extractor.py` 就是这个，已经被删了。这是实证，不是预测。",
      },
    },
    workbenchHint: "技能是挂在工作上的，所以它第一次出现的地方会是某个任务自己的对话里：",
  },
} as const;
