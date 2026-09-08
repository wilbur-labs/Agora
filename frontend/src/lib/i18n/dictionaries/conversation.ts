// The conversation window: one task = one continuing session with an agent
// (tasks/agora/adr/0007). Every string that describes the session's state has to
// keep three answers apart — continuing, not started, and could not be read —
// because the third one drawn as either of the first two silently loses the
// user's context.

export const conversation = {
  en: {
    title: "Conversation",
    empty: "Pick a task on the left to open its conversation.",
    placeholder: "Say what to do next — this continues the same session.",
    placeholderNew: "Say what to do next — this opens a new session for this task.",
    placeholderUnsure: "Say what to do next — whether this continues the existing session is not known.",
    send: "Send",
    sending: "Sending…",
    stop: "Stop",
    turnsLabel: "{count} turns in this session",
    firstTurn: "No turn yet",
    session: {
      active: "Session live · {turns} turns",
      none: "New session on the next message",
      pending: "Session not confirmed — the next message starts one",
      unidentified: "Last turn's session could not be named",
      unreadable: "Session could not be read",
      lost: "Session did not resume",
      forked: "Conversation restarted",
      unsupported: "This link does not continue sessions",
      unknown: "Session state not read",
      // The three that are not "live" each need their own next action, so none of
      // them may share wording with another.
      unreadableDetail:
        "A session is on record for this task, and this configuration could not continue it. The context is probably still there. Nothing has been started in its place — Agora never opens a new conversation to work around one it could not reach.",
      lostDetail:
        "The last attempt to continue this session ran and the agent never named a session in it, so it did not pick the conversation up. Sending is blocked until you act: this is a recorded observation, not a state that clears itself. If you have fixed the cause — signed in again, restored the CLI — press “Try continuing again” and the next message resumes the same session. Starting a new one drops the context for good.",
      forkedDetail:
        "The agent answered in a different session than the one it was asked to continue, so the earlier context did not come across. Work since then is in the new session; the old id is kept.",
      unknownDetail:
        "Whether this task has a session was not read, so whether your next message continues one is not known. Sending is still allowed; the control plane decides.",
      pendingDetail:
        "The first turn ended before the agent named its session, so there is nothing confirmed to continue. Nothing was established, so nothing is lost by starting again.",
      unidentifiedDetail:
        "A turn ran and no session id could be read from its output. One may exist on the agent's side; Agora cannot name it, so the next message starts a new conversation and that turn's context does not come with it.",
      unsupportedDetail:
        "Every turn on this link is an independent call, so nothing carries over between them. A session recorded earlier is kept, not dropped: point this task at a link that can resume and it continues that conversation rather than starting a new one.",
      orphan: "Session recorded before roles existed",
      orphanDetail:
        "This task has a session on record from a build that did not separate your turns from review turns, so Agora cannot tell whose conversation it is. Sending is refused rather than resuming what may be a review's context. Start a new session to unblock it — the old id is kept and not deleted from the agent's own store.",
      retry: "Try continuing again",
      reviewMoved: "The review conversation changed",
      reviewMovedDetail:
        "Automatic review runs in its own session on this agent, and something about that session changed — it moved, or this configuration cannot reach it right now. Reviews continue: the control plane resumes whichever session the agent answered in, and re-checks a reachability problem every turn.",
      reviewStuck: "The review conversation is stuck",
      reviewStuckDetail:
        "Automatic review runs in its own session on this agent, and that one cannot be continued. Every later review of this task is refused until it is retried or started fresh — the work itself is unaffected.",
      forget: "Start a new session",
      forgetConfirm:
        "Drop the recorded session? The next message starts a new conversation with no earlier context. The old session is not deleted from the agent's own store.",
      idLabel: "Session",
    },
    transcript: {
      waiting: "Started — waiting for the first output…",
      unreadable: "The last read of this transcript could not reach it ({reason}). Nothing is known to be missing — it was not read past, so the next read that lands returns everything.",
      unreadableNoReason: "The last read of this transcript could not reach it. Nothing is known to be missing; the next read that lands returns everything.",
      unreachable: "This turn's transcript could not be read. That is not the same as the agent having said nothing.",
      settledEmpty: "This turn finished without writing any transcript.",
      truncatedNotice: "Reading the newest part first — earlier output is above.",
      load: "Load this turn's transcript",
      stalled: "The last read of this transcript did not come back. Nothing is lost — the next one picks up where this stopped.",
      lossy: "Some of what the agent produced could not be saved to this transcript, so output is missing from it. The run itself was unaffected.",
      fromRecord: "Rebuilt from this run's own record: it has no live transcript (it ran before transcripts existed, or ended before writing one).",
    },
    turn: {
      you: "You",
      running: "Running",
      queued: "Queued",
      review: "Review",
      retry: "Retry",
      exit: "exit {code}",
      files: "Files changed",
      filesUnknown: "Which files this turn changed was not recorded.",
      duration: "{seconds}s",
      refused: "Refused before it started",
    },
    pickers: {
      adapter: "Agent",
      environment: "Environment",
      environmentLocal: "Local",
    },
    errors: {
      projectUnread:
        "This project's agent could not be read, so which agent your message would go to is not known — and the wrong one runs in its own worktree, where the work already done is not visible. Pick an agent above to send anyway.",
      sendFailed: "The message was not accepted: {reason}",
      sessionsUnreadable: "The session list could not be read.",
      turnsUnreadable: "This task's turns could not be read, so the conversation may be incomplete.",
      olderTurns: "Showing the most recent {shown} turns; earlier ones exist.",
      loadOlder: "Load earlier turns",
      // Says what this view knows, which is not that more turns exist: at the
      // ceiling it has no probe left to spend, so claiming they do was a guess
      // it could not make. What it can say is where it stops.
      olderCeiling: "This view loads at most {limit} turns. If the task has more, the earlier ones are not shown here.",
    },
  },
  zh: {
    title: "对话",
    empty: "在左边挑一个任务，打开它的对话。",
    placeholder: "说下一步做什么——这条会接着同一个会话。",
    placeholderNew: "说下一步做什么——这条会为这个任务开一个新会话。",
    placeholderUnsure: "说下一步做什么——这条会不会接着现有会话，现在不知道。",
    send: "发送",
    sending: "发送中…",
    stop: "停止",
    turnsLabel: "这个会话已经说了 {count} 轮",
    firstTurn: "还没有任何一轮",
    session: {
      active: "会话在线 · 第 {turns} 轮",
      none: "下一条消息会开新会话",
      pending: "会话未被确认——下一条消息会开一个新的",
      unidentified: "上一轮的会话叫不出名字",
      unreadable: "会话读不到",
      lost: "会话没接上",
      forked: "对话被重开了",
      unsupported: "这条链路不续接会话",
      unknown: "没读到会话状态",
      unreadableDetail:
        "这个任务记录着一个会话，而当前配置续不上它。上下文很可能还在。Agora 没有替你另开一个——它绝不会因为读不到就悄悄新起一段对话。",
      lostDetail:
        "上一次续接这段会话时，agent 从头到尾没有报出会话 id，说明它没有接上这段对话。在你做出选择之前不能发送——这是一次真实观测的结论，不会自己变回来。如果失败的原因已经解决（重新登录、CLI 修好了），点「再试一次续接」，下一条消息会按原来的会话 id 再试；点「另起一个会话」则会永久丢掉这段上下文。",
      forkedDetail:
        "agent 在一个与请求不同的会话里作答，所以之前那段上下文没有被带过来。此后的工作在新会话里；旧的 id 仍然保留着。",
      unknownDetail:
        "没读到这个任务有没有会话，所以你下一条消息会不会接着上下文，现在不知道。仍然可以发，由控制平面判定。",
      pendingDetail:
        "第一轮在 agent 报出会话 id 之前就结束了，所以没有被确认的东西可以续。什么都没建立过，所以重开不丢东西。",
      unidentifiedDetail:
        "有一轮跑过了，但它的输出里读不到会话 id。agent 那边可能真有一个会话，Agora 叫不出它的名字——所以下一条消息会开一段新的对话，那一轮的上下文不会被带过来。",
      unsupportedDetail:
        "这条链路上每一轮都是独立调用，轮与轮之间什么都不带。之前记下的会话不会被丢弃：把这个任务换回能续接的链路，它会接着那段对话，而不是新开一段。",
      orphan: "会话记录于角色区分之前",
      orphanDetail:
        "这个任务有一条会话记录来自还没区分「你的轮次」和「评审轮次」的版本，Agora 判定不出它属于哪一方。与其把可能是评审的上下文接给你，这里直接拒绝发送。点「开新会话」即可解除——旧 id 会保留，也不会从 agent 自己的存储里删掉。",
      retry: "再试一次续接",
      reviewMoved: "评审那段对话变了",
      reviewMovedDetail:
        "自动评审在这个 agent 上用的是它自己的会话，而那段会话发生了变化——它换了一段，或者当前配置暂时够不到它。评审会继续跑：控制面会接上 agent 实际回答的那一段，够不到的情况每一轮都会重新判断。",
      reviewStuck: "评审那段对话卡住了",
      reviewStuckDetail:
        "自动评审在这个 agent 上用的是它自己的会话，而那一段现在续接不了。在你重试或新开之前，这个任务后面的每一次评审都会被拒绝——工作本身不受影响。",
      forget: "另起一个会话",
      forgetConfirm:
        "丢掉记录的会话？下一条消息将开始一段没有先前上下文的新对话。agent 自己存的那个会话不会被删。",
      idLabel: "会话",
    },
    transcript: {
      waiting: "已启动——还没有第一段输出…",
      unreadable: "上一次读这份记录没读到（{reason}）。没有已知的缺失——偏移量没往前走，下一次读到就会把这些字节全部取回。",
      unreadableNoReason: "上一次读这份记录没读到。没有已知的缺失，下一次读到就会把内容全部取回。",
      unreachable: "这一轮的记录读不到。这和「agent 什么都没说」不是一回事。",
      settledEmpty: "这一轮结束了，没有写下任何记录。",
      truncatedNotice: "先显示最新的一段，更早的输出在上面。",
      load: "读取这一轮的记录",
      stalled: "上一次读取这份记录没有回来。没有东西丢——下一次会从停下的地方接着取。",
      lossy: "agent 产出的一部分没能写进这份记录，所以这里缺了输出。run 本身没受影响。",
      fromRecord: "这是用这个 run 自己的记录重建的：它没有实时转写（跑在转写机制之前，或者还没写下第一行就结束了）。",
    },
    turn: {
      you: "你",
      running: "进行中",
      queued: "排队中",
      review: "复核",
      retry: "重试",
      exit: "退出码 {code}",
      files: "改动文件",
      filesUnknown: "这一轮改了哪些文件没有被记录下来。",
      duration: "{seconds} 秒",
      refused: "还没开始就被拒绝",
    },
    pickers: {
      adapter: "执行者",
      environment: "环境",
      environmentLocal: "本机",
    },
    errors: {
      projectUnread:
        "读不到这个项目配置的 agent，所以不知道你这条消息会发给谁——发错 agent 会跑在它自己的 worktree 里，看不到已经做过的工作。要照样发送，请在上面自己选一个 agent。",
      sendFailed: "这条消息没有被接受：{reason}",
      sessionsUnreadable: "会话列表读不到。",
      turnsUnreadable: "这个任务的轮次读不到，所以对话可能不完整。",
      olderTurns: "只显示最近 {shown} 轮；更早的轮次存在。",
      loadOlder: "加载更早的轮次",
      olderCeiling: "这个视图最多加载 {limit} 轮。如果这个任务的轮次超过这个数，更早的在这里看不到。",
    },
  },
} as const;
