// Today — mockup v2's screen 1: three tiers of projects, and one cross-project list of
// what is stuck. Every string here is written so that an absence ("dormant", "nothing
// needs you", "no other project moved") is only ever said when the read behind it was
// complete; the alternative wording for each case says which read fell short, because
// "the list hit its limit" and "the list could not be read" send a person to different
// places. Fullness of the focus tier is the server's sentence, repeated verbatim.

export const today = {
  en: {
    title: "Today",
    subtitle: "Which projects to push today. What you have, in full, is the projects screen.",
    header: {
      countsExact: "{projects} projects · {advancing} advancing",
      countsAtLeast: "{projects} projects · at least {advancing} advancing",
      // The task read failed or has not landed: the project count stands on its own.
      projectsOnly: "{projects} projects",
      projectsUnreadable: "The project list could not be read, so no tier below is complete.",
    },
    reach: {
      tasksUnreadable: "The task list could not be read. Which projects are advancing and which are dormant cannot be told apart below; a project's open items may also be missing.",
      tasksCapped: "The task list came back at its {limit}-task limit, so a project shown without a recently moved open task may have one that was not read.",
      attentionUnreadable: "Open items could not be read, so whether a task needs you is not known for any task without one already on screen.",
      attentionCapped: "Open items came back at their read limit, so a task shown as clear may have an item that was not read.",
    },
    focus: {
      title: "Today's focus",
      capacityOpen: "{used} / {limit}",
      capacityFull: "{used} / {limit} full — to bring a fourth up, put one down first",
      unreported: "This server does not report a focus tier, so nothing can be promoted from here. Advancing and dormant projects are still placed below.",
      // The tier is a list of project ids; without the project list nothing can be said
      // about which of them resolve, so no card, no seat count and no release button.
      projectsUnreadable: "The project list could not be read, so the focus tier cannot be shown or changed until a refresh succeeds.",
      empty: "Nothing is promoted yet. Promote a project from the rows below; the tier holds up to {limit}.",
      unresolved: "{count} promoted entries no longer resolve to a registered project and still hold a seat: {ids}",
      release: "Release seat",
      refusal: {
        full: "{limit} / {limit} full — to bring a fourth up, put one down first",
        unknownProject: "That project is no longer registered, so it cannot be promoted.",
        // A lost answer is not a refusal: the server may have promoted. Shown until the
        // tier has been re-read; the re-read retires it.
        failed: "No answer to promoting it. The tier is being re-read from the server; until that lands, what is shown here is unconfirmed.",
      },
      // Shown until a re-read of the tier succeeds; a lost answer is not a refusal, and
      // until the server has been asked again the tier on screen is unconfirmed.
      demoteFailed: "No answer to putting it down ({error}). The tier is being re-read from the server; until that lands, what is shown here is unconfirmed.",
    },
    card: {
      fields: {
        milestone: "Milestone",
        environment: "Environment",
        gitState: "Branch · commit · pushed",
        lastTime: "Last time",
        now: "Now",
        blocking: "Blocking",
        defects: "On record",
        discovery: "Found",
        goal: "Goal",
        progress: "Progress",
      },
      goalMore: "Show all",
      goalLess: "Show less",
      goalNone: "No goal recorded yet.",
      dashboardLoading: "Reading the project…",
      dashboardUnreadable: "The project dashboard could not be read, so the next step, what blocks it and its progress are not known.",
      dashboardStale: "The dashboard refresh failed; the next step and blocking count are withheld and the progress shown may be out of date.",
      blockingExact: "{count} open items need you",
      blockingAtLeast: "at least {count} open items need you",
      actions: {
        continue: "Continue work →",
        continueTitle: "Opens the most recently updated open task: {title}",
        look: "Look at the project first",
        lookTitle: "Opens the workbench inside this project without starting anything",
        // The one action a project with no goal offers. It is the card's own button, not
        // a few words at the end of the goal line: that link was looked past repeatedly.
        settleGoal: "Settle the goal with the agent →",
        settleGoalHint: "A minute or two: the agent reads the repository, then asks one question at a time — mostly clicking.",
        demote: "Put down",
      },
    },
    advancing: {
      title: "Advancing, but not today's focus",
      note: "Has an open task that moved in the last {days} days",
      openExact: "{count} open tasks",
      openAtLeast: "at least {count} open tasks",
      openUnknown: "open tasks not read",
      promote: "Promote to focus",
      none: "No other project moved in the last {days} days.",
    },
    dormant: {
      // "Open task", not "task": a task that closed yesterday is activity of a kind, and it
      // is not what this tier measures. The tier asks whether anything still open moved.
      summary: "{count} dormant projects · no open task moved in {days} days · open to see",
      note: "No open task of this project moved in the last {days} days.",
    },
    unplaced: {
      title: "Advancing or dormant — cannot say",
      note: "Whether these moved in the last {days} days is not known — each row says why. They are listed here rather than filed as dormant.",
      // The task read was complete; the project itself carries the doubt.
      undated: "An open task's update time could not be read, so recent activity cannot be ruled out",
      // The task read was not complete; the doubt is the read's.
      readShort: "The task read fell short, so a task that moved may not have been read",
    },
    needsYou: {
      title: "Stuck / needs you",
      note: "Across every project — deliberately not grouped by project, so it can be scanned in one pass",
      clear: "Nothing is waiting on you right now.",
      // Said instead of the line above whenever either read fell short. It is the
      // difference between "you are clear" and "I cannot tell you that".
      unsure: "Nothing waiting was found — but the reads above did not fully land, so this is not \"you are clear\".",
      atLeast: "at least {count}",
      // An open item whose task is not on the task page read alongside it. Presence is
      // provable from the item itself; the task's title and state are not.
      orphanState: "task not in the task read",
      unknownTitle: "Not established",
      unknownNote: "A read fell short, so whether these need you is unknown",
    },
  },
  zh: {
    title: "今天",
    subtitle: "今天推什么。「我都有什么」在「项目」页。",
    header: {
      countsExact: "{projects} 个项目 · {advancing} 个在推进",
      countsAtLeast: "{projects} 个项目 · 至少 {advancing} 个在推进",
      projectsOnly: "{projects} 个项目",
      projectsUnreadable: "项目列表读不到，所以下面每一档都不完整。",
    },
    reach: {
      tasksUnreadable: "任务列表读不到。下面分不出哪些项目在推进、哪些休眠；项目的待处理项也可能缺。",
      tasksCapped: "任务列表是按 {limit} 条上限返回的，所以显示为「近期没有未完成任务动过」的项目，可能有一条没被读到的任务动过。",
      attentionUnreadable: "待处理项读不到，所以对任何屏幕上没有待处理项的任务，「它是否需要你」都是不知道。",
      attentionCapped: "待处理项是按读取上限返回的，所以显示为「没事」的任务，可能有一条没被读到。",
    },
    focus: {
      title: "今天主推",
      capacityOpen: "{used} / {limit}",
      capacityFull: "{used} / {limit} 已满 —— 第 4 个要上来，得先放下一个",
      unreported: "这台服务器没有报告主推这一档，所以这里无法提升任何项目。在推进 / 休眠仍照常归位。",
      projectsUnreadable: "项目列表读不到，主推这一档在刷新成功前无法显示、也不能改。",
      empty: "还没有提升任何项目。从下面的行里提升；这一档最多 {limit} 个。",
      unresolved: "{count} 条已提升的记录已经对不上任何在册项目，但仍占着位子：{ids}",
      release: "释放位子",
      refusal: {
        full: "{limit} / {limit} 已满 —— 第 4 个要上来，得先放下一个",
        unknownProject: "这个项目已不在册，无法提升。",
        failed: "提升时没收到回应。正在向服务器重新读取主推状态；读回来之前，这一档显示的未经确认。",
      },
      demoteFailed: "放下时没收到回应（{error}）。正在向服务器重新读取主推状态；读回来之前，这一档显示的未经确认。",
    },
    card: {
      fields: {
        milestone: "里程碑",
        environment: "环境",
        gitState: "分支 · commit · 已推",
        lastTime: "上次",
        now: "现在",
        blocking: "拦路",
        defects: "在册",
        discovery: "发现",
        goal: "目标",
        progress: "进度",
      },
      goalMore: "展开全文",
      goalLess: "收起",
      goalNone: "这个项目还没有目标记录。",
      dashboardLoading: "正在读这个项目……",
      dashboardUnreadable: "项目驾驶舱读不到，所以下一步、拦路项和进度都不知道。",
      dashboardStale: "刷新失败；下一步和拦路数先不显示，下面的进度可能已过期。",
      blockingExact: "{count} 项待处理需要你",
      blockingAtLeast: "至少 {count} 项待处理需要你",
      actions: {
        continue: "继续工作 →",
        continueTitle: "打开最近更新的未完成任务：{title}",
        look: "先看看这个项目",
        settleGoal: "和 agent 一起定目标 →",
        settleGoalHint: "大约 1~2 分钟：agent 先读一遍仓库，然后一次问你一条，点选就行。",
        lookTitle: "进这个项目的工作台，不启动任何东西",
        demote: "放下",
      },
    },
    advancing: {
      title: "在推进，但不是今天的重点",
      note: "有未完成任务、近 {days} 天有活动",
      openExact: "{count} 个未完成任务",
      openAtLeast: "至少 {count} 个未完成任务",
      openUnknown: "未完成任务数没读到",
      promote: "提升为主推",
      none: "近 {days} 天没有其他项目动过。",
    },
    dormant: {
      summary: "{count} 个休眠项目 · 近 {days} 天没有未完成任务动过 · 点开查看",
      note: "这个项目近 {days} 天没有未完成任务动过。",
    },
    unplaced: {
      title: "在推进还是休眠 —— 说不了",
      note: "这些项目近 {days} 天动没动，不知道 —— 每行写明原因。放在这里，而不是当成休眠。",
      undated: "有一条未完成任务的更新时间读不出来，所以不能排除近期有活动",
      readShort: "任务读取没读全，动过的任务可能没被读到",
    },
    needsYou: {
      title: "卡住 / 需要你",
      note: "跨全部项目 —— 故意不按项目分组，阻塞要一次扫完",
      clear: "现在没有东西在等你。",
      unsure: "没找到在等你的东西——但上面的读取没有完整落地，所以这不等于「你没事了」。",
      atLeast: "至少 {count}",
      orphanState: "任务不在这次任务读取里",
      unknownTitle: "没确认",
      unknownNote: "有一次读取没读全，所以这些是否需要你，不知道",
    },
  },
  ja: {
    title: "今日",
    subtitle: "今日どれを進めるか。「手持ちのすべて」は「プロジェクト」画面にあります。",
    header: {
      countsExact: "プロジェクト {projects} 件 · 推進中 {advancing} 件",
      countsAtLeast: "プロジェクト {projects} 件 · 推進中 少なくとも {advancing} 件",
      projectsOnly: "プロジェクト {projects} 件",
      projectsUnreadable: "プロジェクト一覧を読めないため、以下のどの段も完全ではありません。",
    },
    reach: {
      tasksUnreadable: "タスク一覧を読めません。以下では推進中と休眠中のプロジェクトを区別できず、プロジェクトの未対応事項も欠けている可能性があります。",
      tasksCapped: "タスク一覧は上限の {limit} 件で返ってきました。「最近動いた未完了タスクがない」と表示されたプロジェクトにも、読めなかったタスクで動いたものがあるかもしれません。",
      attentionUnreadable: "未対応事項を読めないため、画面に未対応事項が出ていないタスクについては、あなたの対応が必要かどうかは不明です。",
      attentionCapped: "未対応事項は読み取り上限で返ってきました。「問題なし」と表示されたタスクにも、読めなかった事項があるかもしれません。",
    },
    focus: {
      title: "今日の注力",
      capacityOpen: "{used} / {limit}",
      capacityFull: "{used} / {limit} 満杯 —— 4つ目を上げるには、先に1つ下ろしてください",
      unreported: "このサーバーは注力の段を報告していないため、ここからは何も昇格できません。推進中 / 休眠中の振り分けはこれまでどおり行います。",
      projectsUnreadable: "プロジェクト一覧を読めないため、更新が成功するまで注力の段は表示も変更もできません。",
      empty: "まだ何も昇格していません。下の行から昇格させてください。この段には最大 {limit} 件まで入ります。",
      unresolved: "昇格済みの {count} 件は登録済みのどのプロジェクトにも対応しなくなっていますが、まだ枠を占めています：{ids}",
      release: "枠を解放",
      refusal: {
        full: "{limit} / {limit} 満杯 —— 4つ目を上げるには、先に1つ下ろしてください",
        unknownProject: "このプロジェクトはもう登録されていないため、昇格できません。",
        failed: "昇格の応答がありませんでした。サーバーから注力の状態を読み直しています。読み直しが終わるまで、この段の表示は未確認です。",
      },
      demoteFailed: "下ろす操作の応答がありませんでした（{error}）。サーバーから注力の状態を読み直しています。読み直しが終わるまで、この段の表示は未確認です。",
    },
    card: {
      fields: {
        milestone: "マイルストーン",
        environment: "環境",
        gitState: "ブランチ · コミット · プッシュ済み",
        lastTime: "前回",
        now: "現在",
        blocking: "ブロック",
        defects: "記録済み",
        discovery: "発見",
        goal: "目標",
        progress: "進捗",
      },
      goalMore: "すべて表示",
      goalLess: "折りたたむ",
      goalNone: "このプロジェクトにはまだ目標の記録がありません。",
      dashboardLoading: "このプロジェクトを読み込み中……",
      dashboardUnreadable: "プロジェクトのダッシュボードを読めないため、次の一手、ブロック要因、進捗はいずれも不明です。",
      dashboardStale: "更新に失敗しました。次の一手とブロック数は表示を控えています。下の進捗は古い可能性があります。",
      blockingExact: "あなたの対応が必要な未対応事項 {count} 件",
      blockingAtLeast: "あなたの対応が必要な未対応事項 少なくとも {count} 件",
      actions: {
        continue: "作業を続ける →",
        continueTitle: "最近更新された未完了タスクを開きます：{title}",
        look: "まずプロジェクトを見る",
        settleGoal: "エージェントと目標を決める →",
        settleGoalHint: "1〜2 分ほど：エージェントがまずリポジトリを読み、その後1問ずつ質問します。ほぼクリックで答えられます。",
        lookTitle: "このプロジェクトのワークベンチを開きます。何も開始しません",
        demote: "下ろす",
      },
    },
    advancing: {
      title: "推進中だが、今日の注力ではない",
      note: "未完了タスクがあり、直近 {days} 日に動きあり",
      openExact: "未完了タスク {count} 件",
      openAtLeast: "未完了タスク 少なくとも {count} 件",
      openUnknown: "未完了タスク数を読めていません",
      promote: "注力に昇格",
      none: "直近 {days} 日に動いたプロジェクトはほかにありません。",
    },
    dormant: {
      summary: "休眠中のプロジェクト {count} 件 · 直近 {days} 日に動いた未完了タスクなし · クリックで表示",
      note: "このプロジェクトでは直近 {days} 日に動いた未完了タスクはありません。",
    },
    unplaced: {
      title: "推進中か休眠中か —— 判断できない",
      note: "これらのプロジェクトが直近 {days} 日に動いたかどうかは不明です —— 理由は各行に書いてあります。休眠扱いにはせず、ここに並べています。",
      undated: "未完了タスクの1つで更新日時を読めないため、最近の動きがなかったとは言い切れません",
      readShort: "タスクの読み取りが途中までだったため、動いたタスクが読めていない可能性があります",
    },
    needsYou: {
      title: "停滞 / 要対応",
      note: "全プロジェクト横断 —— あえてプロジェクトごとにまとめず、1回で見渡せるようにしています",
      clear: "今あなたを待っているものはありません。",
      unsure: "あなたを待っているものは見つかりませんでした。ただし上の読み取りが完全には終わっていないため、「対応不要」という意味ではありません。",
      atLeast: "少なくとも {count}",
      orphanState: "今回のタスク読み取りに含まれないタスク",
      unknownTitle: "未確認",
      unknownNote: "読み取りが一部欠けたため、これらに対応が必要かどうかは不明です",
    },
  },
} as const;
