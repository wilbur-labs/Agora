// Attention page copy. Filled during migration.
// Protocol terms keep their English original in parentheses on first/section
// use so the domain stays legible to operators familiar with the API.
export const attention = {
  en: {
    header: {
      eyebrow: "Human intervention",
      title: "Attention Center",
      openCount: "{count} open",
      refresh: "Refresh",
    },
    notifications: {
      title: "Get alerted when an agent is waiting",
      hint: "Permission is requested only after you click.",
      enable: "Enable notifications",
    },
    filters: {
      state: "State",
      kind: "Kind",
      project: "Project",
    },
    list: {
      loading: "Loading attention inbox…",
      empty: "No matching attention items.",
      noBody: "No additional details",
      requestedBy: "requested by {requester}",
    },
    detail: {
      emptyState: "Select an item to inspect and respond.",
      project: "Project",
      task: "Task",
      run: "Run",
      requester: "Requester",
      resolution: "Resolution",
    },
    bridge: {
      heading: "{vendor} bridge · {mode}",
      captureOnly: "Agora captured this wait, but cannot send your answer back automatically. Resolve it in the vendor UI or cancel the run.",
      bidirectional: "Responses can be delivered over the verified structured channel.",
    },
    form: {
      approve: "Approve",
      reject: "Reject",
      suggestedAnswer: "Suggested answer",
      response: "Response",
      responsePlaceholder: "Provide context for the waiting agent",
      submit: "Submit response",
      cancel: "Cancel item",
    },
    messages: {
      recordedCaptureOnly: "Response recorded in Agora. This capture-only bridge cannot deliver it to the vendor process.",
      recordedBidirectional: "Response recorded. The bidirectional bridge can deliver it to the waiting agent.",
      conflictRefreshed: "This item changed in another session; refreshed the authoritative state.",
      cancelled: "Attention item cancelled.",
      conflictRefreshedShort: "This item changed in another session; refreshed.",
      cancelReason: "Cancelled from Attention Center",
    },
  },
  zh: {
    header: {
      eyebrow: "人工介入",
      title: "关注事项中心（Attention Center）",
      openCount: "{count} 个待处理",
      refresh: "刷新",
    },
    notifications: {
      title: "有智能体在等待时提醒我",
      hint: "仅在你点击后才会请求权限。",
      enable: "启用通知",
    },
    filters: {
      state: "状态",
      kind: "类型",
      project: "项目",
    },
    list: {
      loading: "正在加载关注事项收件箱…",
      empty: "没有匹配的关注事项。",
      noBody: "无更多详情",
      requestedBy: "由 {requester} 请求",
    },
    detail: {
      emptyState: "选择一个事项以查看并处理。",
      project: "项目",
      task: "任务",
      run: "运行",
      requester: "请求者",
      resolution: "处理结果",
    },
    bridge: {
      heading: "{vendor} 桥接 · {mode}",
      captureOnly: "Agora 已捕获此次等待，但无法自动将你的回复发送回去。请在供应商界面中处理，或取消该运行。",
      bidirectional: "回复可通过已验证的结构化通道送达。",
    },
    form: {
      approve: "批准",
      reject: "拒绝",
      suggestedAnswer: "建议答复",
      response: "回复",
      responsePlaceholder: "为正在等待的智能体提供背景信息",
      submit: "提交回复",
      cancel: "取消事项",
    },
    messages: {
      recordedCaptureOnly: "回复已记录在 Agora 中。此仅捕获（capture-only）桥接无法将其送达供应商进程。",
      recordedBidirectional: "回复已记录。双向桥接可将其送达正在等待的智能体。",
      conflictRefreshed: "该事项已在另一会话中变更；已刷新为权威状态。",
      cancelled: "关注事项已取消。",
      conflictRefreshedShort: "该事项已在另一会话中变更；已刷新。",
      cancelReason: "从关注事项中心取消",
    },
  },
} as const;
