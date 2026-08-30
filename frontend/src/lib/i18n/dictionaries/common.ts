// Shared UI copy: navigation, shell chrome, and cross-page primitives.
// Protocol terms keep their English original in parentheses on first/section
// use so the domain stays legible to operators familiar with the API.

export const common = {
  en: {
    brand: {
      name: "Agora",
      tagline: "Delivery Control Plane",
    },
    nav: {
      portfolio: "Portfolio",
      controlPlane: "Control Plane",
      requirements: "Requirements",
      runs: "Runs",
      workflows: "Workflows",
      attention: "Attention",
      settings: "Settings",
    },
    action: {
      toggleTheme: "Toggle theme",
      toggleLanguage: "Switch language",
      refresh: "Refresh",
      retry: "Retry",
      dismiss: "Dismiss",
    },
    language: {
      en: "English",
      zh: "中文",
    },
    state: {
      loading: "Loading",
      empty: "Nothing to show yet.",
    },
    // The project progress bar (lib/progress-snapshot.ts). Shared, because the same
    // bar is the answer on the workspace strip and in the portfolio cockpit, and two
    // copies of these sentences would let the two surfaces disagree about what a
    // missing snapshot means. Every sentence names the read it came from: none of
    // them may be readable as "no progress has been made".
    progress: {
      label: "Progress",
      // The counted items behind the percentage, so the number is checkable.
      counts: "{done} done · {inProgress} in progress · {todo} to do",
      // Present and tracking nothing yet — not a missing snapshot, and not 0%.
      noItems: "The progress snapshot tracks no items yet.",
      // No snapshot at all. Says where one would be read from, so it reads as a
      // thing that can be created rather than as a failure.
      noSnapshot: "No progress snapshot for this project ({source}), so no progress figure is shown.",
      // Read fine, and it does track items — in markers this reader does not count.
      // The honest sentence names the reader, not the project: saying "no items" about
      // a file full of checkboxes would be false.
      markersNotCounted: "The progress snapshot ({source}) uses checklist markers this reader does not count, so no progress figure is shown.",
      unreadable: "The progress snapshot could not be read, so progress is not known.",
      notReported: "This server does not report a progress snapshot, so progress is not known.",
    },
  },
  zh: {
    brand: {
      name: "Agora",
      tagline: "交付控制平面",
    },
    nav: {
      portfolio: "项目全景",
      controlPlane: "控制平面",
      requirements: "需求",
      runs: "运行记录",
      workflows: "工作流",
      attention: "关注事项",
      settings: "设置",
    },
    action: {
      toggleTheme: "切换主题",
      toggleLanguage: "切换语言",
      refresh: "刷新",
      retry: "重试",
      dismiss: "关闭",
    },
    language: {
      en: "English",
      zh: "中文",
    },
    state: {
      loading: "加载中",
      empty: "暂无内容。",
    },
    progress: {
      label: "进度",
      counts: "已完成 {done} · 进行中 {inProgress} · 待办 {todo}",
      noItems: "进度快照中还没有登记任何条目。",
      noSnapshot: "这个项目没有进度快照（{source}），因此不显示任何进度数字。",
      markersNotCounted: "进度快照（{source}）使用的清单标记不在本读取器的统计范围内,因此不显示任何进度数字。",
      unreadable: "进度快照无法读取,因此进度未知。",
      notReported: "这个服务端不提供进度快照,因此进度未知。",
    },
  },
} as const;
