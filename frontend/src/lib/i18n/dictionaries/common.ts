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
  },
} as const;
