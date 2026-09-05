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
      newProject: "New project",
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
    groups: {
      action: "Needs you",
      // Not "Needs you": the whole content of this group is that the read
      // could not say whether these need you.
      unsorted: "Could not be sorted",
      inProgress: "In progress",
      completed: "Completed",
    },
    buckets: {
      needsReply: "Needs reply",
      needsApproval: "Needs approval",
      blocked: "Blocked",
      // Not a kind of work — the read's own limit. These tasks may or may not need
      // you; the one thing known is that the attention read could not say.
      unknown: "Could not tell",
      running: "Running",
      verified: "Verified",
      shaping: "Shaping",
      done: "Done",
      closed: "Closed",
    },
    // Why some tasks are in "Could not tell". Three separate sentences on purpose:
    // saying "the cap was reached" to someone whose request failed is a specific
    // falsehood, and the distinction is the whole point of this area. Named
    // `attentionRead` because `attention` is already taken by the panel heading —
    // a second `attention:` key here is a duplicate the compiler rejects, and the
    // YAML equivalent earlier tonight was accepted in silence.
    attentionRead: {
      pending: "Still loading the board, so tasks are not sorted yet.",
      // No recovery step, because there is none to offer: the project filter narrows
      // the tasks already loaded on the client, it does not rescope or page the
      // open-attention read (`listOpenAttention` takes no arguments), so following
      // that advice would change nothing and read as though it had. A sentence that
      // states the limit and stops is the honest version of this notice.
      //
      // And it states the *read's* limit, not a count: `truncated` is set when the
      // page comes back full, which a complete list of exactly that size also does,
      // so "there are more open items than this page reads" was a fact this code
      // cannot have. The same correction applies to the other two — with no tasks on
      // the board at all, "some tasks could not be sorted" is about tasks that do not
      // exist. Every one of these sentences is about the read.
      //
      // `pending` says "the board", not "this read", and that is not vagueness: the
      // page settles all four reads and publishes them together, so this state lasts
      // until the whole first load has been applied. "Still reading open items" would
      // go on saying it after the open-items read had in fact finished, held up by a
      // sibling.
      capped: "Open items reached this page's read limit, so sorting may be incomplete.",
      unreadable: "Open items could not be read, so sorting could not be done. The columns below do not mean nothing is waiting.",
    },
    // The task list is a page too, and only this notice says so. Suppressing the
    // reassurance without it leaves an empty action group explaining nothing — the
    // uncertainty kept internally and dropped at the display boundary.
    //
    // Each sentence states this read's limit and stops. "There are more tasks than
    // this page reads" would be a fact the board does not have: a page filled to the
    // cap is *indistinguishable* from a complete list of exactly that size, and the
    // reach is deliberately pessimistic there. Nor does the unreadable line say the
    // board is missing tasks — a failed refresh keeps the previous snapshot, which
    // may well have been complete; what is known is that it could not be re-read.
    // These are notices about a read, not about the inventory.
    taskInventory: {
      pending: "Still loading the board.",
      capped: "The task list reached this page's limit, so this board may not be showing every task.",
      unreadable: "The task list could not be read, so the tasks shown here may be out of date or incomplete.",
    },
    allClear: "Nothing needs you right now.",
    cockpit: {
      charter: {
        title: "Charter",
        goal: "Goal",
        nonGoals: "Non-goals",
        successCriteria: "Success criteria",
        empty: "No charter yet — define the goal so progress has meaning.",
        edit: "Edit charter",
        eyebrow: "Project",
        editTitle: "Project charter",
        close: "Close charter editor",
        goalLabel: "Goal",
        goalPlaceholder: "What should this project achieve?",
        nonGoalsLabel: "Non-goals (one per line)",
        nonGoalsPlaceholder: "What this project explicitly will not do",
        successLabel: "Success criteria (one per line)",
        successPlaceholder: "How you'll know it's done",
        save: "Save charter",
        saving: "Saving…",
      },
      crLog: {
        title: "Change log",
        open: "{count} open",
        // "Read, and every row classified." The only state in which this UI is
        // allowed to say nothing is open.
        allClear: "No open change requests.",
        // Read, but some rows carry a status this reader does not understand. The
        // count above is then a floor, and saying so is the whole point.
        unclassified: "{count} with a status this reader cannot read — the count above may be short.",
        empty: "This project keeps a change log and has raised nothing in it.",
        // Absent is a fact about the project.
        none: "This project keeps no change log ({path}).",
        // Everything else is a fact about the read, and never a number.
        unreadable: "The change log could not be read — no count is shown.",
        tooLarge: "The change log is too large to read whole — no count is shown.",
        malformed: "The change log's table does not have the expected columns, so no status can be trusted. Rows to fix: {ids}",
        unreported: "This server does not report the change log.",
        openBadge: "OPEN",
        unknownBadge: "?",
      },
      metrics: {
        progress: "Progress",
        verified: "Verified",
        health: "Health",
        weight: "{done}/{total} criteria",
      },
      nextAction: {
        label: "Next action",
        forTask: "Task: {title}",
        allClear: "Nothing needs you — the project is on track.",
        attention: "{count} need attention",
        pending: "{count} awaiting you",
        partial: "Large project — counts cover the first page of tasks.",
      },
      card: {
        next: "Next: {action}",
        verified: "{pct}% verified",
      },
      loadFailed: "Could not load the project cockpit: {error}",
    },
    card: {
      define: "Define",
      requirements: "Requirements",
      authority: "Authority",
      dispatchRun: "Dispatch run",
      run: {
        status: "{adapter} · {state}",
        viewResult: "View result",
        none: "No runs yet",
      },
      delivery: "Delivered {count} file(s)",
      advance: {
        toPlanned: "Start build",
        toReview: "Submit for review",
        toVerified: "Mark verified",
        toDone: "Hand off & complete",
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
          done: "Commit the deliverable in the worker's workspace, bind the commit to the task, and complete it. This is final — a done task cannot be reopened.",
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
    newProject: {
      eyebrow: "Projects",
      title: "Create project",
      close: "Close create project dialog",
      idLabel: "Project ID",
      idPlaceholder: "hotel-booking",
      nameLabel: "Name (optional)",
      namePlaceholder: "Hotel Booking",
      workerLabel: "Developer agent",
      reviewerLabel: "Reviewer agent",
      agentsHint: "The developer builds; a different reviewer cross-checks its work before you approve.",
      hint: "A folder is created under the projects root set in Settings.",
      submit: "Create project",
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
      newProject: "新建项目",
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
    groups: {
      action: "需要你处理",
      unsorted: "无法归类",
      inProgress: "开发中",
      completed: "已完成",
    },
    buckets: {
      needsReply: "需回复",
      needsApproval: "需审批",
      blocked: "受阻",
      unknown: "无法判断",
      running: "开发中",
      verified: "已验收",
      shaping: "筹备中",
      done: "完成",
      closed: "已关闭",
    },
    attentionRead: {
      pending: "看板还在加载，任务尚未归类。",
      capped: "待处理事项已达本页读取上限，归类可能不完整。",
      unreadable: "待处理事项读取失败，无法完成归类。下面各列并不代表没有事情在等你。",
    },
    taskInventory: {
      pending: "看板还在加载。",
      capped: "任务列表已达本页读取上限，本面板可能没有显示全部任务。",
      unreadable: "任务列表读取失败，此处显示的任务可能已过时或不完整。",
    },
    allClear: "当前没有需要你处理的事项。",
    cockpit: {
      charter: {
        title: "项目章程",
        goal: "目标",
        nonGoals: "非目标",
        successCriteria: "成功标准",
        empty: "尚未定义章程——先明确目标,进度才有意义。",
        edit: "编辑章程",
        eyebrow: "项目",
        editTitle: "项目章程",
        close: "关闭章程编辑器",
        goalLabel: "目标",
        goalPlaceholder: "这个项目要达成什么?",
        nonGoalsLabel: "非目标(每行一条)",
        nonGoalsPlaceholder: "这个项目明确不做的事",
        successLabel: "成功标准(每行一条)",
        successPlaceholder: "怎样算完成",
        save: "保存章程",
        saving: "保存中…",
      },
      crLog: {
        title: "变更记录",
        open: "{count} 项未决",
        allClear: "没有未决的变更请求。",
        unclassified: "{count} 项的状态本读取器读不懂——上面的数字可能偏少。",
        empty: "这个项目有变更记录,但还没提出过任何一条。",
        none: "这个项目没有变更记录({path})。",
        unreadable: "变更记录读不到——不显示任何条数。",
        tooLarge: "变更记录太大,无法完整读取——不显示任何条数。",
        malformed: "变更记录的表格列数不对,任何状态都不可信。需要修的行:{ids}",
        unreported: "此服务器不提供变更记录。",
        openBadge: "未决",
        unknownBadge: "?",
      },
      metrics: {
        progress: "进度",
        verified: "已验证",
        health: "健康度",
        weight: "{done}/{total} 项标准",
      },
      nextAction: {
        label: "下一步",
        forTask: "任务:{title}",
        allClear: "没有待你处理的事——项目进展正常。",
        attention: "{count} 项需要关注",
        pending: "{count} 项待你处理",
        partial: "项目较大——统计仅覆盖第一页任务。",
      },
      card: {
        next: "下一步:{action}",
        verified: "已验证 {pct}%",
      },
      loadFailed: "无法加载项目驾驶舱:{error}",
    },
    card: {
      define: "定义",
      requirements: "需求",
      authority: "权威",
      dispatchRun: "派发运行",
      run: {
        status: "{adapter} · {state}",
        viewResult: "查看结果",
        none: "尚无运行",
      },
      delivery: "已交付 {count} 个文件",
      advance: {
        toPlanned: "开始构建",
        toReview: "提交评审",
        toVerified: "验证通过",
        toDone: "交付并完成",
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
          done: "把交付物在 worker 工作区里 git 提交、把 commit 绑定到任务,并完成它。此操作最终生效——done 状态无法重开。",
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
    newProject: {
      eyebrow: "项目",
      title: "新建项目",
      close: "关闭新建项目对话框",
      idLabel: "项目 ID",
      idPlaceholder: "hotel-booking",
      nameLabel: "名称(可选)",
      namePlaceholder: "宾馆预订",
      workerLabel: "开发 agent",
      reviewerLabel: "评审 agent",
      agentsHint: "开发 agent 负责实现;另一个评审 agent 交叉检查它的产出,再由你批准。",
      hint: "会在设置页配置的项目根目录下创建一个文件夹。",
      submit: "创建项目",
      submitting: "创建中…",
    },
  },
} as const;
