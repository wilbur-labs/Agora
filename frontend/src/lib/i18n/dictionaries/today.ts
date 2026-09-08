// Today — mockup v2's screen 1. Every string here is written so that "nothing is
// waiting on you" is only ever said when both reads behind it were complete; the
// alternative wording for each case says which read fell short, because "the list
// hit its limit" and "the list could not be read" send a person to different
// places.

export const today = {
  en: {
    title: "Today",
    subtitle: "What is waiting on you, and what is moving, across every task Agora can see.",
    reach: {
      tasksUnreadable: "The task list could not be read, so nothing below is a complete picture — tasks may be missing entirely.",
      tasksCapped: "The task list came back at its {limit}-task limit, so there may be tasks beyond the ones placed below.",
      attentionUnreadable: "Open items could not be read, so whether a task needs you is not known for any task without one already on screen.",
      attentionCapped: "Open items came back at their read limit, so a task shown as clear may have an item that was not read.",
    },
    groups: {
      needsYou: "Needs you",
      needsYouNote: "Across every project",
      needsYouClear: "Nothing is waiting on you right now.",
      // Said instead of the line above whenever either read fell short. It is the
      // difference between "you are clear" and "I cannot tell you that".
      needsYouUnsure: "Nothing waiting was found — but the reads above did not fully land, so this is not \"you are clear\".",
      running: "Moving",
      runningNone: "Nothing is running.",
      unknown: "Not established",
      unknownNote: "A read fell short, so whether these need you is unknown",
      rest: "{count} other tasks",
      atLeast: "at least {count}",
    },
    missing: {
      focus: "Today's focus picker (pick up to three) is not built: the choice would have to be stored somewhere, and there is nowhere yet — a choice that vanishes on reload is worse than none.",
      inventory: "Projects with no tasks registered do not appear here, because this screen reads tasks rather than the disk. The full board:",
    },
  },
  zh: {
    title: "今天",
    subtitle: "有什么在等你、有什么在动——覆盖 Agora 看得到的全部任务。",
    reach: {
      tasksUnreadable: "任务列表读不到，所以下面任何一处都不是完整图景——可能有任务整个没出现。",
      tasksCapped: "任务列表是按 {limit} 条上限返回的，所以下面归好位的之外可能还有任务。",
      attentionUnreadable: "待处理项读不到，所以对任何屏幕上没有待处理项的任务，「它是否需要你」都是不知道。",
      attentionCapped: "待处理项是按读取上限返回的，所以显示为「没事」的任务，可能有一条没被读到。",
    },
    groups: {
      needsYou: "需要你",
      needsYouNote: "跨全部项目",
      needsYouClear: "现在没有东西在等你。",
      needsYouUnsure: "没找到在等你的东西——但上面的读取没有完整落地，所以这不等于「你没事了」。",
      running: "在动",
      runningNone: "没有正在跑的。",
      unknown: "没确认",
      unknownNote: "有一次读取没读全，所以这些是否需要你，不知道",
      rest: "另外 {count} 个任务",
      atLeast: "至少 {count}",
    },
    missing: {
      focus: "「今天主推（最多三个）」这个选择器没做：那个选择得存在什么地方，而现在还没有地方存——一个刷新就消失的选择比没有更糟。",
      inventory: "还没有登记任何任务的项目不会出现在这里，因为这一屏读的是任务而不是磁盘。完整的板子在：",
    },
  },
} as const;
