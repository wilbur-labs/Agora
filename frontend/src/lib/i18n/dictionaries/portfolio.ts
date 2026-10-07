// Portfolio page copy.

export const portfolio = {
  en: {
    header: {
      eyebrow: "All projects",
      // The screen answers "what do I have" -- Today answers "what am I pushing today".
      // Named after the question rather than after a section of a product tour, because
      // the split between the two screens is the point (W9).
      title: "Projects",
    },
    filter: {
      label: "Filter by project",
      allProjects: "All projects",
    },
    actions: {
      refresh: "Refresh tasks",
      newTask: "New task",
      newProject: "New project",
      importProjects: "Import from workspaces",
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
    // Project kickoff (W10): one round — the agent reads and asks, the owner answers, the
    // agent proposes, the owner edits and adopts. Every step's sentence is here; the
    // three non-steps (read in flight, read failed, file not a record) are separate and
    // none of them is worded as "start".
    kickoff: {
      title: "Kickoff",
      step: {
        loading: "reading the record…",
        readFailed: "record could not be requested",
        unreadable: "record present but not readable",
        start: "not started",
        questions: "questions to answer",
        answered: "answered — ready to propose",
        proposed: "proposal ready to adopt",
        adopting: "adoption stopped halfway",
        adopted: "adopted",
      },
      readFailed: "The kickoff record could not be requested from the server.",
      unreadable: {
        unreadable: "A kickoff record exists in the repository (.agora/kickoff.json) but could not be read. This is not \"not started\".",
        malformed: "A kickoff record exists in the repository (.agora/kickoff.json) but is not a record this build understands. It was not started over.",
        foreign: "The kickoff record in this repository (.agora/kickoff.json) belongs to another project registered on the same directory. This project cannot start over it or adopt it; register this project on its own directory, or continue the kickoff from that project.",
        linked: "The record's path in this repository (.agora, or kickoff.json inside it) is a link pointing elsewhere. It is neither read nor written: a kickoff record belongs in the project itself. Replace the link with a real directory or file to continue.",
        root_missing: "The project's root directory does not exist (moved, deleted, or registered at the wrong path). Nothing can be started or read until it is back where the project says it is.",
      },
      startIntro: "The agent reads the README, the directory and the git history, drafts a charter and asks the questions only you can answer. For an empty repository, write down what this project is in a few sentences first. This step runs the Claude CLI once as an analyst, whichever agent the project's runs use.",
      adoptingNote: "A previous adoption stopped halfway: {created} of the {approved} approved tasks exist. Resume to finish from what was approved then — nothing already created is created twice, and nothing unticked comes back. The charter: if the project's charter still reads as it did before adopting, resuming sets it to the approved one; if it has been changed since, it is left as it is and the record says so.",
      resumeAdopt: "Resume the adoption",
      briefPlaceholder: "What is this project, for whom, and what would make it a success?",
      start: "Have the agent read it once",
      running: "Reading and thinking — this takes a minute or so.",
      restart: "Ask again from the start",
      restartNote: "Starts a new round over the same brief (edit it below). Nothing already adopted is undone.",
      restartConfirm: "Start again",
      unread: "Not readable and not guessed about: {items}",
      draftTitle: "Charter draft (from the first read)",
      questionsIntro: "Answer what you can. A skipped question is a decision the proposal will have to make without you.",
      answerPlaceholder: "Your answer…",
      mockupNote: "This project has a user interface. The proposal will open with a mockup task: {note}",
      saveAnswers: "Save answers",
      saveFirst: "Save the answers first — the proposal is built from what is saved.",
      refine: "Propose the charter and first tasks",
      answersReview: "Review or change the answers",
      proposalTitle: "Proposal — edit anything, then adopt",
      tasksTitle: "First tasks ({count}) — untick what you do not want",
      caveat: {
        mockupTaskMissing: "This project has a user interface and the proposal does not open with a mockup task. The mockup comes before any UI code — every later acceptance line points at it — so put one first after adopting, or ask again.",
        noTasks: "The proposal has no tasks; adopting sets the charter only.",
      },
      adoptNote: "Adopting writes the charter and creates the ticked tasks; nothing before this touched the charter or the task list. The kickoff record itself lives in the repository as .agora/kickoff.json from the first step on.",
      adopt: "Adopt: charter + {count} tasks",
      adoptBlocked: {
        goal: "Write a goal before adopting.",
        criteria: "At least one success criterion is needed before adopting — a goal with none cannot be checked.",
      },
      adoptedSummary: "Adopted. The charter is set and {count} tasks were created — open the workbench to dispatch the first one.",
      adoptedUnchecked: "The record says adopted ({count} tasks recorded). What this control plane holds of it was not checked in this read — reload to check.",
      adoptedSummaryCharterKept: "Adopted: {count} tasks were created. The charter was NOT overwritten — it had been edited by hand while the adoption sat half done, and that edit stands; the sealed charter is in the record if you want it.",
      openWorkbench: "Open the workbench →",
      adoptedNotHere: "The record in the repository says adopted, but this control plane has no record of applying it in full: charter {charter}; {present} of {recorded} recorded tasks present. The repository may have been cloned here, or the database not restored. Re-apply to create the missing tasks from what was approved — and, if this control plane never applied this adoption, to set the charter to the approved one. Nothing present is created twice.",
      hereCharter: {
        matches: "is the approved one",
        differs: "differs from the approved one",
        absent: "absent",
      },
      reapplyAdopt: "Re-apply the adoption here",
      hereScanFailed: "The record says adopted, but this project has more tasks than the adoption can scan, so nothing can be said about what this control plane holds of it — and re-applying is refused for the same reason.",
      hereKeyConflict: "A task in this project already carries one of this adoption's keys but is not the approved task. Adopting is refused while that task is active — cancel or archive it in the workbench (the two things the product can do to a task), then come back.",
    },
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
      group: {
        // "at least", because the board holds one page of tasks and a full page is
        // indistinguishable from "there is more".
        atLeast: "≥{count}",
      },
      metrics: {
        progress: "Progress",
        verified: "Verified",
        health: "Health",
        weight: "{done}/{total} criteria",
        // Shown instead of the number when the dashboard's task page was capped: a
        // percentage of an unstated subset is not a percentage of the project.
        partial: "Not shown — the dashboard's task read reached its limit",
      },
      nextAction: {
        label: "Next action",
        forTask: "Task: {title}",
        allClear: "Nothing needs you — the project is on track.",
        // Shown instead of any action when the dashboard's task read reached its
        // limit: a candidate found on one page is not the project's next action, and
        // finding none on one page is not "nothing to do".
        unknown: "Next action not determined — the dashboard's task read reached its limit",
        attention: "{count} need attention",
        pending: "{count} awaiting you",
        // Floors, for a rollup over a page that was not the project.
        attentionAtLeast: "≥{count} need attention",
        pendingAtLeast: "≥{count} awaiting you",
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
        // Said instead of "No runs yet" when the board's one page of runs did not
        // cover this task. Absent from a page it did not fully read is not "none".
        unknown: "Runs not fully read — this card cannot say",
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
      // Without the number, for when the task read was capped or failed and the count
      // is not one this board can vouch for.
      showSome: "Show archived",
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
    import: {
      title: "Import projects from your workspaces",
      body: "The repositories your claude-workspace and kiro-workspace task records name. Importing registers each one as a project where it is: nothing in it is created, initialised or committed. Agora adds only its own empty .agora/ folders and a local .git/info/exclude line.",
      loading: "Reading your workspaces' task records...",
      failed: "Could not read the candidates. Nothing was imported.",
      importable: "Ready to import ({count})",
      none: "No repository on this machine is waiting to be imported.",
      registered: "Already projects ({count})",
      registeredAs: "already project {id}",
      blocked: "On this machine, but not importable as is ({count})",
      elsewhere: "Not on this machine ({count})",
      unreadable: "These task files could not be read: {tasks}",
      from: "from {tasks}",
      submit: "Import {count} selected",
      importing: "Importing...",
      done: "Imported",
      error: "Not imported: {message}",
      close: "Close",
      status: {
        ok: "ready",
        registered: "already a project",
        missing: "the path does not exist here",
        not_git: "not a git repository (Agora never git-inits one)",
        unreadable: "could not be inspected",
        not_this_machine: "another machine's path",
        placeholder: "template placeholder, never filled in",
      },
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
      title: "项目",
    },
    filter: {
      label: "按项目筛选",
      allProjects: "全部项目",
    },
    actions: {
      refresh: "刷新任务",
      newTask: "新建任务",
      newProject: "新建项目",
      importProjects: "从工作区导入项目",
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
    kickoff: {
      title: "项目启动",
      step: {
        loading: "正在读记录…",
        readFailed: "记录请求失败",
        unreadable: "记录在但读不了",
        start: "尚未开始",
        questions: "有问题要答",
        answered: "已作答 —— 可以生成定稿",
        proposed: "定稿待采用",
        adopting: "采用中途停了",
        adopted: "已采用",
      },
      readFailed: "启动记录没能从服务器请求到。",
      unreadable: {
        unreadable: "仓库里有启动记录（.agora/kickoff.json）但读不出来。这不是「尚未开始」。",
        malformed: "仓库里有启动记录（.agora/kickoff.json）但不是这个版本认得的格式。没有替你重新开始。",
        foreign: "仓库里的启动记录（.agora/kickoff.json）属于注册在同一目录上的另一个项目。这个项目不能覆盖它、也不能采用它；给这个项目一个自己的目录，或者到那个项目里继续。",
        linked: "仓库里记录所在的路径（.agora，或里面的 kickoff.json）是一个指向别处的链接。既不读也不写：启动记录只能在项目自己里面。把链接换成真实目录或文件再继续。",
        root_missing: "项目的根目录不存在（被移动、删除，或注册的路径不对）。在它回到项目登记的位置之前，什么都启动不了、也读不到。",
      },
      startIntro: "agent 会读 README、目录和 git 历史，起草 charter，并提出只有你能回答的问题。空仓库请先用几句话写下这个项目是什么。这一步固定由 Claude CLI 以分析者身份执行一次，与项目 run 用哪个 agent 无关。",
      adoptingNote: "上一次采用中途停了：当时批准的 {approved} 个任务已建 {created} 个。按当时批准的内容继续补完 —— 已建的不会重复建，取消勾选的也不会回来。。charter：如果项目的 charter 还是采用前的那版，续跑会把它写成批准的那版；如果这期间改过，就保留改过的，记录里会写明",
      resumeAdopt: "继续完成采用",
      briefPlaceholder: "这个项目是什么、给谁用、做成什么样才算成？",
      start: "让 agent 读一遍",
      running: "正在读和想 —— 大约一分钟。",
      restart: "从头重新提问",
      restartNote: "用同一段想法（可在下面改）重新来一轮。已经采用的东西不会被撤销。",
      restartConfirm: "重新开始",
      unread: "没读到、也没有编：{items}",
      draftTitle: "charter 初稿（第一遍读完）",
      questionsIntro: "能答的都答。跳过的问题，定稿就得替你做决定。",
      answerPlaceholder: "你的回答…",
      mockupNote: "这个项目有界面。定稿会以一条 mockup 任务开头：{note}",
      saveAnswers: "保存回答",
      saveFirst: "先保存回答 —— 定稿是从已保存的回答生成的。",
      refine: "生成 charter 定稿与首批任务",
      answersReview: "查看或修改回答",
      proposalTitle: "定稿 —— 可以随便改，然后采用",
      tasksTitle: "首批任务（{count}）—— 不要的取消勾选",
      caveat: {
        mockupTaskMissing: "这个项目有界面，但定稿没有以 mockup 任务开头。mockup 要在任何界面代码之前 —— 之后每条验收线都指向它 —— 采用后把它排到第一，或者重新提问。",
        noTasks: "定稿里没有任务；采用只会写入 charter。",
      },
      adoptNote: "采用会写入 charter 并创建勾选的任务；在此之前 charter 和任务列表都没动过。启动记录本身从第一步起就存在项目仓的 .agora/kickoff.json 里。",
      adopt: "采用：charter + {count} 个任务",
      adoptBlocked: {
        goal: "先写目标，再采用。",
        criteria: "至少要有一条成功标准才能采用 —— 没有标准的目标无法验收。",
      },
      adoptedSummary: "已采用。charter 已写入，创建了 {count} 个任务 —— 去工作台派第一个。",
      adoptedUnchecked: "记录说已采用（记了 {count} 个任务）。这次读取没有核对这台控制面上到底有没有——刷新一下再看。",
      adoptedSummaryCharterKept: "已采用：创建了 {count} 个任务。charter 没有被覆盖 —— 采用中途停着的时候有人手改过它，改动保留；封存的那版 charter 还在记录里，要用可以自己搬。",
      openWorkbench: "打开工作台 →",
      adoptedNotHere: "仓库里的记录说已采用，但这台控制面上没有完整落地过它：charter {charter}；记录的 {recorded} 个任务里这里有 {present} 个。可能是仓库克隆到了这台机器，或者数据库没有恢复。「在这里重新落地」会按当时批准的内容把缺的任务建出来；如果这台控制面从没落地过这次采用，charter 也会写成批准的那版。已有的任务不会重复建。",
      hereCharter: {
        matches: "就是批准的那版",
        differs: "和批准的那版不同",
        absent: "不在",
      },
      reapplyAdopt: "在这里重新落地",
      hereScanFailed: "记录说已采用，但这个项目的任务多到采用流程扫不过来，所以这台控制面上到底有没有落地说不了——「重新落地」也会因为同一个原因被拒绝。",
      hereKeyConflict: "项目里已经有一个任务带着这次采用的 key，但它不是批准的那个任务。它还活着的时候采用会被拒绝——到工作台把它取消或归档（产品对任务只能做这两件事），再回来采用。",
    },
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
      group: {
        atLeast: "≥{count}",
      },
      metrics: {
        progress: "进度",
        verified: "已验证",
        health: "健康度",
        weight: "{done}/{total} 项标准",
        partial: "不显示——驾驶舱这次任务读取到达了上限",
      },
      nextAction: {
        label: "下一步",
        forTask: "任务:{title}",
        allClear: "没有待你处理的事——项目进展正常。",
        unknown: "无法判定下一步——驾驶舱这次任务读取到达了上限",
        attention: "{count} 项需要关注",
        pending: "{count} 项待你处理",
        attentionAtLeast: "≥{count} 项需要关注",
        pendingAtLeast: "≥{count} 项待你处理",
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
        unknown: "运行记录未读全——这张卡说不准",
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
      showSome: "显示已归档",
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
    import: {
      title: "从工作区导入项目",
      body: "这里列出你在 claude-workspace 和 kiro-workspace 的任务记录里写过的仓库。导入就是把它们原地登记成项目：不会在仓库里新建、初始化或提交任何东西。Agora 只加它自己的空 .agora/ 目录，并在本地 .git/info/exclude 里加一行。",
      loading: "正在读取工作区的任务记录…",
      failed: "没能读取候选列表，什么都没有导入。",
      importable: "可以导入（{count}）",
      none: "这台机器上没有待导入的仓库。",
      registered: "已经是项目（{count}）",
      registeredAs: "已是项目 {id}",
      blocked: "在本机，但不能原样导入（{count}）",
      elsewhere: "不在这台机器上（{count}）",
      unreadable: "以下任务文件读不了：{tasks}",
      from: "来自 {tasks}",
      submit: "导入所选 {count} 个",
      importing: "正在导入…",
      done: "已导入",
      error: "未导入：{message}",
      close: "关闭",
      status: {
        ok: "可以导入",
        registered: "已经是项目",
        missing: "这台机器上没有这个路径",
        not_git: "不是 git 仓库（Agora 不会替你 git init）",
        unreadable: "无法检查",
        not_this_machine: "别的机器上的路径",
        placeholder: "模板占位，从没填过",
      },
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
