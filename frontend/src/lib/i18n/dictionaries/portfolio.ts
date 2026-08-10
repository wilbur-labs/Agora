// Portfolio page copy.

export const portfolio = {
  en: {
    header: {
      eyebrow: "All projects",
      title: "Delivery Portfolio",
    },
    filter: {
      label: "Filter by project",
      allProjects: "All projects",
    },
    actions: {
      refresh: "Refresh tasks",
      newTask: "New task",
    },
    errors: {
      loadFailed: "Could not load control plane: {error}",
      conflict: "Task changed elsewhere. The board was refreshed; try again.",
    },
    attention: {
      heading: "Needs attention ({count})",
    },
    columns: {
      intake: "Intake",
      define: "Define",
      build: "Build",
      verify: "Verify",
      complete: "Complete",
    },
    empty: "No tasks",
    card: {
      define: "Define",
      requirements: "Requirements",
      authority: "Authority",
      dispatchRun: "Dispatch run",
      advance: {
        toPlanned: "Start build",
        toReview: "Submit for review",
        toVerified: "Mark verified",
        toDone: "Mark done",
      },
      confirm: {
        question: "Advance this task from “{from}” to “{to}”?",
        cancel: "Cancel",
        confirm: "Confirm",
        viewRuns: "View run details",
        hint: {
          requirements: "Begin capturing requirements for this task.",
          planned: "Enter the build stage; you can then dispatch an agent on the Runs page.",
          review: "Mark this stage's deliverable complete and move it to review. Check the output first (open the run details on the Runs page).",
          verified: "Confirm the review passed.",
          done: "Mark the task complete. This is final — a done task cannot be reopened.",
        },
      },
      archive: "Remove",
      restore: "Restore",
      archivedBadge: "Archived",
      confirmArchive: {
        question: "Remove “{title}” from the board?",
        hint: "The task is hidden from the board. Its lifecycle state is unchanged and it can be restored from “Show archived”.",
        keep: "Keep",
        confirm: "Remove",
      },
    },
    archived: {
      show: "Show archived ({count})",
      hide: "Hide archived",
    },
    create: {
      eyebrow: "Control plane",
      title: "Create task",
      close: "Close create task dialog",
      fields: {
        projectId: "Project ID",
        projectIdPlaceholder: "my-project",
        title: "Title",
        titlePlaceholder: "What should the agents deliver?",
        description: "Description",
        descriptionPlaceholder: "Context, desired outcome, and important constraints",
        risk: "Risk",
      },
      submit: "Create in backlog",
      submitting: "Creating…",
    },
  },
  zh: {
    header: {
      eyebrow: "全部项目",
      title: "交付项目全景（Portfolio）",
    },
    filter: {
      label: "按项目筛选",
      allProjects: "全部项目",
    },
    actions: {
      refresh: "刷新任务",
      newTask: "新建任务",
    },
    errors: {
      loadFailed: "无法加载控制平面：{error}",
      conflict: "任务已在别处变更。看板已刷新，请重试。",
    },
    attention: {
      heading: "关注事项（{count}）",
    },
    columns: {
      intake: "接收",
      define: "定义",
      build: "构建",
      verify: "验证",
      complete: "完成",
    },
    empty: "暂无任务",
    card: {
      define: "定义",
      requirements: "需求",
      authority: "权威",
      dispatchRun: "派发运行",
      advance: {
        toPlanned: "开始构建",
        toReview: "提交评审",
        toVerified: "验证通过",
        toDone: "标记完成",
      },
      confirm: {
        question: "把任务从「{from}」推进到「{to}」?",
        cancel: "取消",
        confirm: "确认推进",
        viewRuns: "查看运行详情",
        hint: {
          requirements: "开始为该任务录入需求。",
          planned: "进入构建阶段,之后可在 Runs 页派发 agent 执行。",
          review: "标记这一阶段交付完成并进入评审。请先确认产出(在 Runs 页打开运行详情查看)。",
          verified: "确认评审通过。",
          done: "标记任务完成。此操作最终生效——done 状态无法重开。",
        },
      },
      archive: "移除",
      restore: "恢复",
      archivedBadge: "已归档",
      confirmArchive: {
        question: "把「{title}」从看板移除?",
        hint: "任务将从看板隐藏。其生命周期状态不变,可通过「显示已归档」恢复。",
        keep: "保留",
        confirm: "移除",
      },
    },
    archived: {
      show: "显示已归档（{count}）",
      hide: "隐藏已归档",
    },
    create: {
      eyebrow: "控制平面（Control Plane）",
      title: "创建任务",
      close: "关闭创建任务对话框",
      fields: {
        projectId: "项目 ID",
        projectIdPlaceholder: "my-project",
        title: "标题",
        titlePlaceholder: "希望智能体交付什么？",
        description: "描述",
        descriptionPlaceholder: "背景、期望结果与重要约束",
        risk: "风险",
      },
      submit: "创建至待办",
      submitting: "创建中…",
    },
  },
} as const;
