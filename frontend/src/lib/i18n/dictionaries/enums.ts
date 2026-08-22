// Display labels for backend enum values. The raw value is always kept as the
// option value / tone key; only the shown text is localized. Anything not
// mapped falls back to the humanized raw value (underscores → spaces), so an
// unmapped or newly-added backend status still renders reasonably.

export const enums = {
  en: {
    runState: { queued: "queued", running: "running", succeeded: "succeeded", failed: "failed", timed_out: "timed out", cancelled: "cancelled", abandoned: "abandoned" },
    workflowState: { draft: "draft", active: "active", completed: "completed", failed: "failed", cancelled: "cancelled" },
    stepState: { pending: "pending", ready: "ready", running: "running", succeeded: "succeeded", failed: "failed", cancelled: "cancelled" },
    taskState: { backlog: "backlog", requirements: "requirements", design: "design", planned: "planned", running: "running", blocked: "blocked", review: "review", verified: "verified", done: "done", failed: "failed", cancelled: "cancelled" },
    risk: { low: "low", medium: "medium", high: "high", critical: "critical" },
    attentionState: { open: "open", responded: "responded", cancelled: "cancelled", expired: "expired", all: "all" },
    attentionKind: { question: "question", approval: "approval", blocker: "blocker", all: "all" },
    urgency: { low: "low", normal: "normal", high: "high", critical: "critical" },
    workspaceState: { missing: "missing", provisioning: "provisioning", ready: "ready", foreign: "foreign", error: "error", checking: "checking", unknown: "unknown" },
    status: { active: "active", ready: "ready", completed: "completed", passed: "passed", valid: "valid", invalid: "invalid", succeeded: "succeeded", failed: "failed", blocked: "blocked", stale: "stale", cancelled: "cancelled", reconciliation_required: "reconciliation required", unavailable: "unavailable", pending: "pending", running: "running", queued: "queued", timed_out: "timed out", abandoned: "abandoned", skipped: "skipped", not_initialized: "not initialized", awaiting_approval: "awaiting approval", approved: "approved", rejected: "rejected", superseded: "superseded", draft: "draft", waived: "waived", observed: "observed", recorded: "recorded", backlog: "backlog", needs_review: "needs review" },
  },
  zh: {
    runState: { queued: "排队中", running: "运行中", succeeded: "成功", failed: "失败", timed_out: "超时", cancelled: "已取消", abandoned: "已放弃" },
    workflowState: { draft: "草稿", active: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消" },
    stepState: { pending: "待处理", ready: "就绪", running: "运行中", succeeded: "成功", failed: "失败", cancelled: "已取消" },
    taskState: { backlog: "待办", requirements: "需求", design: "设计", planned: "已计划", running: "运行中", blocked: "受阻", review: "评审", verified: "已验证", done: "已完成", failed: "失败", cancelled: "已取消" },
    risk: { low: "低", medium: "中", high: "高", critical: "严重" },
    attentionState: { open: "待处理", responded: "已回应", cancelled: "已取消", expired: "已过期", all: "全部" },
    attentionKind: { question: "提问", approval: "审批", blocker: "阻塞", all: "全部" },
    urgency: { low: "低", normal: "普通", high: "高", critical: "紧急" },
    workspaceState: { missing: "缺失", provisioning: "置备中", ready: "就绪", foreign: "外来", error: "错误", checking: "检查中", unknown: "未知" },
    status: { active: "活跃", ready: "就绪", completed: "已完成", passed: "通过", valid: "有效", invalid: "无效", succeeded: "成功", failed: "失败", blocked: "受阻", stale: "已过期", cancelled: "已取消", reconciliation_required: "需要对账", unavailable: "不可用", pending: "待处理", running: "运行中", queued: "排队中", timed_out: "超时", abandoned: "已放弃", skipped: "已跳过", not_initialized: "未初始化", awaiting_approval: "等待审批", approved: "已批准", rejected: "已拒绝", superseded: "已被取代", draft: "草稿", waived: "已豁免", observed: "已观测", recorded: "已记录", backlog: "待办", needs_review: "待评审" },
  },
} as const;
