// Task Workspace: the task-centric three-column workbench. One task = one work
// session you can switch to, see where it stands, and know the next step —
// without hopping between the Portfolio / Requirements / Runs / Attention pages.

export const workspace = {
  en: {
    title: "Workspace",
    // Project-level navigation (mockup v2's left column). A nav item does not carry a
    // number — it carries a capability (see lib/nav-capability.ts), and each of these
    // is a different sentence. Merging them is what the constitution forbids.
    nav: {
      projectLabel: "Project",
      items: {
        overview: "Overview",
        runs: "Runs",
        goals: "Goals & requirements",
        changeRequests: "Change requests",
        decisions: "Decisions (ADR)",
        defects: "Known defects",
        skills: "Skills",
        files: "Files",
      },
      capability: {
        count: "{value}",
        countWithCaveat: {
          unclassified: "{value} (+{caveat} unclassified)",
          unopenable: "{value} (+{caveat} unreadable)",
        },
        none: "none",
        unbuilt: "not built yet",
        unbuiltWithMilestone: "not built yet ({milestone})",
        noReader: {
          reader_unbuilt: "no read yet",
          no_structured_source: "no source here yet",
        },
        unreadable: {
          too_large: "too large to read",
          unreadable: "could not be read",
          malformed: "unreadable format",
        },
        unreported: "not reported",
      },
      // Long form, shown on hover/focus. Each says which of the sentences it is, because
      // "unavailable" for all of them is the merge this product refuses to make.
      why: {
        none: "Read successfully; the project keeps none.",
        unbuilt: "This feature does not exist yet, so there is nothing to read.",
        unbuiltWithMilestone: "This feature does not exist yet — planned for {milestone}.",
        noReader: {
          reader_unbuilt: "These exist, but Agora has no read for them yet.",
          no_structured_source: "These are real, but no structured source exists in this repository yet, so they cannot be listed here.",
        },
        unreadable: {
          too_large: "A read was attempted and the source was too large.",
          unreadable: "A read was attempted and failed.",
          malformed: "A read succeeded but the contents could not be parsed.",
        },
        unreported: "This server did not report it — a fact about the server, not about the project.",
        caveat: {
          unclassified: "{caveat} row(s) could not be classified, so this count may not be the whole story.",
          unopenable: "{caveat} record(s) could not be opened, so this count is not complete.",
        },
      },
      tasks: "Tasks",
      otherWorkspaces: "Other workspaces",
      noProject: "No project selected. Open a task, or pick a project from Today.",
      projectsLabel: "Projects",
      executor: "Executor",
      asWorker: "worker",
      asReviewer: "reviewer",
      environment: "Environment",
      environmentUnknown: "No environment is recorded for this project — there is no environment registry yet, so where a run happens is decided by the adapter, not named here.",
      environmentByAdapter: "Where each executor runs, from its configuration:",
      environmentLocal: "this machine",
      switchProject: "Switch project",
    },
    // The right-hand column, which the design keeps beside every centre state.
    crumb: { backToChat: "Project conversation" },
    side: {
      order: "Development order · {count}",
      orderNotWhole: "Development order · at least {count}",
      goal: "Goal",
      goalNone: "This project has no goal yet.",
      goalFull: "Full text →",
      goalUnreadable: "The goal could not be read.",
      progress: "Where it stands",
      progressDetail: "Details →",
      progressLine: "{total} steps · {done} done",
      progressLineNotWhole: "at least {total} steps · {done} done",
      progressRunning: " · {running} running",
      now: "Now",
      nowNone: "Nothing running.",
      nowUnknown: "Cannot tell what is running.",
      nowEnv: "Environment",
      nowAfter: "When it finishes",
      nowAfterAuto: "reviewed automatically",
      waiting: "Waiting on you",
      waitingNone: "Nothing.",
      waitingUnknown: "Could not be read.",
      waitingCount: "{count} · in the conversation",
      collapse: "Hide this column",
      expand: "Show the goal, progress and what waits on you",
      foldedRunning: "{count} running",
      rowDone: "Done",
      rowRunning: "Running",
      rowPending: "Not started",
      needsYou: "needs you",
      written: "What this step has written",
      done: "written",
      tasksValue: "{count} · planned",
      tasksValueNotWhole: "at least {count} · planned",
      nothingWritten: "Nothing has been written into the project yet. That happens the moment you adopt: the goal, the bounds, the acceptance line, the first tasks.",
      lines: {
        goal: "Project goal",
        bounds: "Out of scope / acceptance",
        tasks: "Tasks",
        record: "Conversation record",
      },
    },
    // The plan: the project's tasks as an ordered whole, in the page rather than the
    // column. Every sentence here is about what the reads can support — a step whose
    // gate could not be read says so, and no bar is drawn for a progress nobody has.
    plan: {
      title: "Development order",
      subtitle: "Set when the plan was adopted. New ideas are placed into it from the conversation below.",
      headline: "{total} steps · {done} done · {running} in flight",
      headlineNotWhole: "at least {total} steps · {done} done · {running} in flight",
      totalNotKnown: "The task list read did not cover the whole board, so this is how much of the plan could be seen — not how long it is.",
      headlineWithAbandoned: "{total} steps · {done} done · {running} in flight · {abandoned} dropped",
      percent: "{percent}% done",
      percentRule: "Only finished steps count — work in flight is not half a step.",
      waitingNotKnown: "At least one step's gate could not be read, so this is not the whole picture of what needs you.",
      empty: "This project has no tasks yet. Settle a goal in the conversation below and the first ones are proposed there.",
      emptyUnknown: "No tasks of this project came back in this read, and the read did not cover the whole board — so this is not yet \"none\".",
      step: "Step {step} of {total}",
      stepUnknownTotal: "Step {step}. How many steps in all is not known from this read.",
      stepShort: "{step}/{total}",
      columns: {
        step: "Step",
        task: "Task",
        state: "State",
        progress: "Progress",
        continue: "Continue",
        waiting: "Waiting on you",
      },
      progressUnreadable: {
        no_task_snapshot_source: "not readable",
      },
      progressUnreadableWhy: "A single step's progress has no source yet: the snapshot reader reads a workspace task's RESUME.md, and this project's tasks have none.",
      waiting: {
        none: "nothing",
        unknown: "could not be read",
        open: "{count} open",
        openAtLeast: "at least {count} open",
        kinds: {
          question: "question",
          approval: "approval",
          blocker: "blocker",
        },
      },
      running: "Running",
      standing: {
        done: "Done",
        abandoned: "Dropped",
      },
      continue: "Continue",
      open: "Open",
      backToPlan: "Back to the plan",
    },
    rail: {
      search: "Search tasks",
      groups: {
        needsYou: "Needs you",
        unknown: "Attention unknown",
        running: "Running",
        review: "Review",
        done: "Done",
      },
      // Shown when the global open-attention page did not come back as a complete
      // answer. Each must read as a limit of the read, not as a property of the
      // tasks — and must name the outcome that actually occurred: a failed read is
      // not a reached cap, and saying so was a concrete, wrong explanation.
      attentionPending: "Open attention items have not been read yet, so whether these tasks need you is not known. Open a task to see its own gates.",
      attentionCapped: "Open attention items reached the {limit}-item read limit, so there are more than were read and whether these tasks need you is not known. Open a task to see its own gates.",
      attentionUnreadable: "Open attention items could not be read, so whether these tasks need you is not known. Open a task to see its own gates.",
      // The other three reads of the same refresh, each speaking only for itself.
      // A failed sibling costs the rail freshness, not knowledge of the attention
      // gates — so none of these borrows the wording above, which was written
      // about a different read and was once printed about a read that succeeded.
      tasksUnreadable: "The task list could not be read, so tasks may be missing from this list or out of date.",
      projectsUnreadable: "The project list could not be read, so the project filter may be out of date.",
      // Rewritten with CR-17. It used to say the dots and times *shown* might be out
      // of date, which described exactly the presentation CR-17 removed: on a failed
      // runs read the rail now drops the run it is holding, so there is no dot and the
      // activity time comes from the task manifest. A notice that outlives the thing it
      // describes is the same defect one level up — the fix made its own disclosure false.
      runsUnreadable: "Run status could not be read, so it is not shown for these tasks.",
      unknownSubtitle: "{state} · attention not known",
      empty: "No tasks yet — create one to begin.",
      noMatch: "No tasks match your search.",
      relative: {
        justNow: "just now",
        minAgo: "{n}m ago",
        hourAgo: "{n}h ago",
        dayAgo: "{n}d ago",
      },
    },
    header: {
      goal: "Goal",
      stage: "Stage",
      completion: "Verified completion",
      completionValue: "{done}/{total} criteria",
      noCriteria: "No approved criteria yet",
      // Replaces the verified fraction, which was derived from a worker success
      // not bound to the revision the criteria belong to.
      completionUnbound: "Not shown — no evidence bound to the current criteria",
      unavailable: "Unavailable",
      blocker: "Blocker",
      noBlocker: "None",
      blockedNoDetail: "Blocked — reason not recorded",
      nextStep: "Next step",
      noGoal: "No project goal set — define the charter.",
      selectPrompt: "Select a task on the left to open its workspace.",
      noTasksPrompt: "No tasks yet. Create one to open a workspace.",
    },
    project: {
      // S3: no `{verified}%` any more. The verified percentage came from
      // `metrics.verified_pct`, which counts a criterion as verified from any
      // historical successful run of its task — so a requirements change makes new
      // criteria read as already verified by a run that predates them. The strip now
      // carries the task tally (a count of task states, which is what it says) and
      // the snapshot-driven progress bar beside it.
      strip: "{done}/{total} done",
      // A page's length is not the project's task count, so a truncated read reports
      // the floor it actually saw and no denominator at all.
      stripPartial: "≥{done} done",
      nextPartial: "Next action not determined — this dashboard read one page",
      nextStale: "Next action not shown — this dashboard is from before a failed refresh",
      attentionAtLeast: "≥{count} need attention",
      next: "Project next",
      forTask: "· {title}",
      allDone: "All tasks done or cancelled",
      attention: "{count} need you",
      unavailable: "Project status unavailable",
    },
    recap: {
      // Wording follows days_ago, so a task untouched for weeks never claims
      // "yesterday".
      today: "Today so far",
      yesterday: "Yesterday",
      daysAgo: "{n} days ago",
      onDate: "Last moved {date}",
      never: "Nothing has happened on this task yet.",
      noSummary: "That day had activity, but nothing to summarize.",
      more: "+{n} more",
      partial: "More happened that day than is shown.",
      items: {
        created: "Task created",
        // Three separate keys, not one template with a condition: a known
        // count, an unknown count, and a floor are three different claims,
        // and collapsing them would discard the distinction the backend keeps.
        deliveredCount: "{adapter} delivered {files} file(s)",
        deliveredAtLeast: "{adapter} delivered at least {files} file(s)",
        deliveredUnknown: "{adapter} delivered work",
        ranNoChanges: "{adapter} ran and changed nothing",
        runFailed: "{adapter} run did not finish",
        reviewed: "{adapter} reviewed the work",
        specApproved: "Spec approved",
        specRejected: "Spec rejected",
        stageReached: "Reached: {stage}",
        needsYou: "{count} raised for you",
        // CR-29: not "you". `tasks/recap.py` counts every `attention.responded`
        // event without looking at its actor, and `RespondAttentionRequest.actor`
        // accepts any non-empty string (the authenticated path passes a principal id),
        // so an automation's answer was being reported as the reader's own. Naming the
        // actor instead would be better than dropping the attribution, but the recap
        // publishes counts rather than actors, and the frontend has no notion of "me"
        // at all -- there is no whoami surface, only a count of principals in
        // Settings. So this says what the record establishes and no more.
        answered: "{count} answered",
        handedOff: "Work handed off",
        noted: "{count} note(s) added",
      },
    },
    // The Continue action that sits with the Resume Brief: it acts on what that
    // panel says, so its wording lives next to the recap's.
    // The project conversation (W12): one stream per project, one box.
    projectRail: {
      waiting: "waiting for you {count}",
      running: "running",
      noGoal: "no goal yet",
      idle: "idle",
      unknown: "status unknown",
    },
    terminal: {
      open: "Terminal",
      title: "Terminal",
      loading: "Reading where this project can open…",
      targetsFailed: "Could not read this project's task records; the repository choices still work.",
      asTask: "As task {task} (claude-workspace)",
      asTaskHint: "Like `cw open`: Claude starts with this task's RESUME, CRs and review gate.",
      inRepo: {
        claude: "Claude Code in the repository",
        codex: "Codex in the repository",
        kiro: "Kiro in the repository",
        shell: "PowerShell in the repository",
      },
      authRequired: "Terminals open only for whoever started this Agora. Open it from the desktop shortcut (or `agora.ps1 up`) once, then try again.",
      startFailed: "The terminal did not start: {reason}",
      listFailed: "Could not read the terminals.",
      gone: "That terminal is no longer held by this server (it ended, or the server restarted).",
      close: "Close terminal",
      state: {
        running: "running",
        exited: "ended (exit {code})",
        exitedUnknown: "ended (exit code unknown)",
        exitedWith: "The program ended (exit {code}). Scroll back to read it; open a new terminal to continue.",
        ended: "ended",
      },
      stream: {
        connecting: "Connecting…",
        closed: "Disconnected from the terminal. It may still be running: reload this tab to reconnect.",
        tooSlow: "This window fell too far behind and was disconnected; reload to catch up from the saved output.",
        refused: "The server refused this terminal (not signed in for terminals, or it no longer exists).",
      },
    },
    stream: {
      title: "Project conversation",
      you: "You",
      auto: "Agora · automatic",
      loading: "Reading…",
      empty: "No runs yet. Say what to do and it starts.",
      runsUnreadable: "This project's runs could not be read.",
      gatesUnreadable: "The items waiting on you could not be read.",
      runsCapped: "Only the last {limit} runs are shown.",
      gatesCapped: "More than {limit} items exist; the ones beyond that are not shown here — check under Needs you.",
      noteTag: "note",
      reply: "{adapter} replied",
      // The agent's turn as the terminal shows it (CR-99); what the feed cannot vouch for.
      stop: "stop",
      stopping: "stopping…",
      stopFailed: "Could not stop this turn: {reason}",
      transcriptWaiting: "Waiting for the first line…",
      // Shown under a live turn that has already written something. The pause
      // between lines is usually thinking, whose words claude does not serve in
      // this mode — so the window would otherwise sit perfectly still and look
      // finished (user, 2026-09-22: "正在输出的时候也没什么显示").
      transcriptWorking: "still working…",
      transcriptStalled: "last read did not come back — retrying",
      transcriptUnreadable: "transcript could not be read",
      transcriptLossy: "some output was lost before it could be saved",
      // A question's run is not a card of its own: while it runs, one quiet line under
      // the question; once answered, the run's facts sit in the reply's footer.
      answering: "{adapter} is answering…",
      answerMeta: "{run}{state} · exit {exit} · {elapsed}",
      answerFailed: "{adapter} did not answer — {state}{detail}",
      answerRead: "Read this exchange",
      answerReadFailed: "This exchange could not be read — try again",
      answerChanged: "This question's run changed files, though it was told not to:",
      reviewReply: "{adapter} reviewed",
      promptSummary: "The prompt for step {step} (open to read it)",
      seedVerify: "When done, end with: which files changed, how to start or verify it (commands, URL), and where any screenshots are. The person verifies from the conversation, not from a log.",
      replyFolded: "Showing the end of a long reply; the whole is under \"expand\" on the card.",
      reviewStoodIn: "standing in for {adapter} (out of quota)",
      reviewQuota: "{adapter} is out of quota, so this review did not finish.",
      reviewQuotaUntil: "{adapter} is out of quota (it says it is usable again after {when}), so this review did not finish.",
      reviewFailedBecause: "{adapter}'s review did not finish: {reason}",
      status: {
        running: "{adapter} · step {step} running",
        waiting: "{count} waiting on you",
        idle: "Nothing running",
        unknown: "Cannot tell what is running",
      },
      run: {
        step: "step {step}",
        stepUnknown: "step unknown",
        review: "review",
        chat: "question",
        exit: "exit {code}",
        nested: "{count} review(s)",
        expand: "expand",
        collapse: "collapse",
        prompt: "Prompt",
        output: "Output",
        noOutput: "No output to show.",
        readFailed: "This run's details could not be read.",
        unplaced: {
          parentMissing: "the run it reviews is not in this page",
          parentUnknown: "the run it reviews is unknown",
          reviewOfReview: "a review of a review",
        },
      },
      compose: {
        placeholder: "Talk to the agent as in its terminal — it answers or acts. \"note:\" only records a line; \"idea:\" has the agent judge whether it becomes a task. Enter sends, Shift+Enter breaks the line.",
        askHint: "A question about step {step} — answered, not reviewed; the step does not move",
        noteHint: "Recorded on the timeline only",
        ideaHint: "The agent judges whether this becomes a task",
        askPreamble: "This is a question from the owner, not work. Do not modify any file; answer in the conversation from what is in the worktree and the task.",
        asked: "Asked {adapter}. The answer appears below when it is done.",
        adapter: "Executor",
        envLocal: "this machine",
        envRemote: "remote · {target}",
        envTitle: "Where the run executes. By default it follows the task: a task that has run stays where its work is; a new task uses the executor's own place. Or pick a registered remote environment for a new task.",
        environment: "Environment",
        envFollowTask: "Where the task's work is (a new task: {own})",
        envRegistered: "{label} · {target}",
        envListFailed: "Could not read the registered environments; only the executor's own place is offered",
        envGone: "The chosen environment is no longer registered. Pick another before sending.",
        fixPlaceUnknown: "Could not read the reviewed run, so where its work lives is unknown; the fix was not sent.",
        send: "Send",
        to: "Goes to step {step} \"{title}\"",
        busyShort: "Step {step} is running — the line waits until it finishes",
        busy: "{adapter} is running step {step}. Send again once it finishes.",
        allDone: "Every step is done.",
        noneStartable: "No step in the order can be started.",
        toNewStep: "No step can take this, so it becomes a new step and starts.",
        sentNew: "Sent to {adapter} as a new step: \"{title}\". If it changes files, a review follows automatically.",
        newStepNotStartable: "The new step \"{title}\" came back as {state} and cannot take work; nothing was sent.",
        unknown: "The order could not be read whole, so which step this goes to cannot be told.",
        noteNoTask: "There is no step to record this on yet.",
        noted: "Recorded on the timeline; nothing was dispatched.",
        sent: "Sent to {adapter} for step {step}. If it changes files, a review follows automatically.",
        sendFailed: "Not sent: {reason}",
        conflict: "The control plane refused it: {detail}",
        worktreeNotReady: "The worktree is not ready: {detail}",
      },
      idea: {
        thinking: "Judging whether this is a task, a later version, or a note…",
        failed: "The agent gave no judgement: {reason}",
        echo: "Read as: {echo}",
        verdict: {
          task: "This is a new task. Suggested position in the order: {position}.",
          later: "This belongs to a later version.",
          note: "This is a note, not a task.",
        },
        placeHere: "Add at {position}",
        placeLast: "Add last",
        skip: "Don't add",
        skipped: "Not added.",
        placed: "Added to the order at position {position}.",
        placedUnknown: "Added; where it landed could not be read back.",
      },
      advance: {
        done: "Step {step} \"{title}\" is done and committed ({commit}).",
        doneNothing: "Step {step} \"{title}\" is done; the worktree had nothing left to commit.",
        next: "Next is step {step} \"{title}\". The prompt is drafted from its description:",
        seed: "Prompt for the next step",
        start: "Start step {step}",
        edit: "Edit first",
        editDone: "Done editing",
        started: "Started.",
        with: "runs as {adapter}",
        noNext: "Nothing planned is left in the order.",
        noNextUncertain: "No planned step in what could be read; the list may be cut.",
        taskUnknown: "Approved, but the task behind this item is not in the list, so it was not handed off.",
      },
    },
    // A left-column row opened in the centre (W12): the details, one view at a time.
    detail: {
      back: "Back to the conversation",
      goal: {
        title: "Goal & requirements",
        goal: "Goal",
        nonGoals: "Not doing",
        criteria: "What done means",
        tasks: "Steps and their definition of done",
        unreadable: "The goal could not be read.",
        none: "This project has no goal yet.",
        acceptance: "Acceptance",
        noAcceptance: "No acceptance lines recorded.",
      },
      order: {
        title: "Development order",
        step: "#",
        task: "Step",
        state: "State",
        waiting: "Waiting on you",
        none: "No steps.",
      },
      runs: {
        title: "Runs",
        run: "Run",
        who: "Who",
        step: "Step",
        when: "Started",
        outcome: "Outcome",
        none: "No runs yet.",
      },
      decisions: {
        title: "Decisions",
        unavailable: "Decision records could not be read ({reason}).",
        none: "No decision records yet.",
        unreadableCount: "{count} record(s) could not be opened.",
      },
      changeRequests: {
        title: "Change requests",
        none: "The CR log has no rows.",
        unknown: "The CR log could not be read ({reason}).",
      },
      defects: {
        title: "Known defects",
        unavailable: "The defect list could not be read ({reason}).",
        noSource: "This project has no defect source registered; nothing is guessed.",
        none: "This project keeps no known defects.",
        openCount: "{count} open",
        unrecognisedCount: "{count} unclassified",
        unreadableCount: "{count} could not be read",
      },
      files: {
        title: "Files",
        noView: "There is no file view yet. The worktrees live under the project's .agora/workspaces.",
      },
    },
    continue: {
      action: "Continue",
      close: "Close the Continue panel",
      panelTitle: "What the executor will be asked to do",
      panelHint: "Seeded from what this task recorded. Edit it — nothing is sent until you press start.",
      reseed: "Reset to the seeded prompt",
      start: "Start Codex run",
      starting: "Starting…",
      provisioning: "Creating the Codex worktree…",
      cancel: "Cancel",
      count: "{n} / {limit}",
      adapterNote: "Runs as Codex, in this project's Codex worktree.",
      // A blocked task can go back to planned (the control plane allows it); nothing on
      // any page offered that after a rejected review, so the conversation was a dead end.
      unblock: "Lift the block — back to planned",
      unblockHint: "The rejected review's notes stay under Needs you. Once planned, the next message here is the next turn.",
      unblockFailed: "Could not lift the block: {reason}",
      markDone: "Mark done",
      markDoneHint: "Verified. Marking done commits what the worker's worktree still holds and binds the commit to this task.",
      markDoneCommitted: "Done. Committed {commit} ({count} files).",
      markDoneNothingToCommit: "Done. The worktree had nothing left to commit; the existing commit is bound.",
      markDoneFailed: "Could not mark done: {reason}",
      // What happens on its own after a review is answered (CR-70 / CR-83).
      advance: {
        completed: "Step {step} \"{title}\" is done and committed ({commit}).",
        completedNothingToCommit: "Step {step} \"{title}\" is done; the worktree had nothing left to commit.",
        nextHere: "This is the next step. The prompt is below — one click starts it.",
        noNext: "Nothing planned is left in this project's order.",
        noNextUncertain: "No planned step was found in what could be read; the list may be cut.",
        completeFailed: "Approved, but marking the step done failed: {reason}. It is still verified — use \"Mark done\" above.",
        fixSent: "Rejected. The reviewer's notes and your line went back to the worker as the next turn.",
        fixFailed: "Rejected, but the fix turn could not be sent: {reason}",
      },
      blocked: {
        dispatching: "Starting a run…",
        // The control plane only accepts a run for a planned or running task, so
        // saying why beats firing a request that cannot succeed.
        taskNotRunnable: "Only a planned or running task can be continued — this one is {state}.",
        runActive: "Already running — a run for this task hasn't finished.",
        runsUnknown: "This task's runs couldn't be read, so whether one is already running is unknown. Refresh before starting another.",
        // A task stays "running" while a review's approval is waiting, so state
        // and runs alone would say "go ahead" right at a human gate.
        attentionOpen: "Something on this task is waiting for you — answer it in Attention before starting another run.",
        attentionUnknown: "What this task is waiting on couldn't be read, so whether something needs you is unknown. Refresh before starting another run.",
        // The auto-review handoff: the worker run is already terminal, but the
        // review it triggers is not queued yet, so nothing is active and no gate
        // exists. Starting a run here would jump the review gate.
        followUpPending: "That run just finished — waiting to see whether a review starts. Give it a moment before starting another run.",
      },
      worktree: {
        checking: "Checking the Codex worktree…",
        ready: "Codex worktree ready on {branch}.",
        willProvision: "The Codex worktree doesn't exist yet — it will be created before the run starts.",
        provisioning: "The Codex worktree is still being created. Wait for it to finish.",
        // A failed read is not an absence: offering to "create" a worktree that
        // may already hold work is exactly the wrong move here.
        unreadable: "The Codex worktree's status couldn't be read, so whether it exists is unknown — that is not the same as missing. {detail}",
        foreign: "The Codex worktree path holds something Agora didn't create, so it can't be used.",
        notGit: "The project folder isn't a git repository, so no worktree can be created.",
        unprovisionable: "The Codex worktree can't be prepared: {detail}",
      },
      errors: {
        alreadyRunning: "A run for this task is already active, so this one was not started.",
        // The control plane's Run -> Gate refusal. Its message names the exact
        // item(s) and how to clear them, so it is shown rather than paraphrased.
        gateOpen: "This task has a gate waiting for you, so nothing was started. {detail}",
        reviewActive: "This task's review hasn't finished, so nothing was started. {detail}",
        // The auto-review handoff, refused by the control plane rather than
        // guessed at by a timer: the last worker run succeeded and no independent
        // review accounts for it — the review may not exist yet, or one may have
        // ended without reviewing anything, or without leaving a gate to settle.
        // Says "worker run", not "run": the refusal is about the last succeeded
        // *worker* run, and a review run may well be the more recent row.
        reviewOwed:
          "No independent review accounts for the last worker run yet, so nothing was started. {detail}",
        taskChanged: "The task changed underneath this page, so nothing was started. Refresh and try again.",
        // A 409 whose re-read failed: something refused the dispatch and we cannot
        // say what. Notably it must not borrow taskChanged's "nothing was started"
        // certainty — the refusal may well have been a run that is executing.
        conflictUnknown: "The control plane refused to start the run, and re-reading this task's runs failed too — so why it refused, and whether a run is executing, are both unknown. Refresh before trying again. ({detail})",
        taskGone: "The task or its project no longer exists, so nothing was started. ({detail})",
        rejected: "The control plane rejected the run, so nothing was started: {detail}",
        provisionFailed: "The Codex worktree couldn't be created, so nothing was started: {detail}",
        worktreeNotReady: "The Codex worktree still isn't ready, so nothing was started: {detail}",
        // The three uncertain outcomes: the request got no usable answer, so what
        // happened on the server is established only by re-reading the runs.
        startedUnconfirmed: "The reply never came back, but a run for this task is now active — treat it as started and watch it below rather than starting another. ({detail})",
        startUnknown: "The reply never came back, and re-reading this task's runs failed too — so whether a run started is unknown, not \"no\". Refresh before trying again. ({detail})",
        transportFailed: "The request didn't get an answer, and no run for this task is active now. It may never have reached the control plane, or the run it created may have already finished — check the timeline before retrying. ({detail})",
        unknown: "The run couldn't be started: {detail}",
        // Said alongside the failure above when this click did create the
        // worktree: the worktree change stuck, the run did not.
        afterProvision: "The Codex worktree was created and is ready — only the run failed to start, and nothing is running now.",
        // Same, for an outcome that does not establish that. Claiming "nothing is
        // running" here is exactly the sentence that would be false while a run
        // executes.
        afterProvisionUnsure: "The Codex worktree was created and is ready. Whether a run started could not be confirmed — check the runs below before starting another.",
      },
      seed: {
        goalLabel: "Task goal:",
        descriptionLabel: "Task notes:",
        acceptanceLabel: "Done means:",
        recapUnavailable: "Where this task was left off could not be read, so it is not summarized here. Check the task timeline before assuming anything about it.",
        recapNever: "Nothing has been recorded on this task yet.",
        recapNoItems: "That day had activity, but nothing that could be summarized.",
        // The recap was trimmed. Said in the prompt because the agent has no other
        // way to learn that the history it was handed is incomplete.
        recapMore: "This summary is incomplete: {n} more recorded item(s) from that day are not listed here.",
        recapPartial: "More happened that day than these records cover, so treat the summary above as partial.",
        nextLabel: "Recorded next step:",
        nextUnknown: "The recorded next step could not be read.",
        instruction: "Continue this task from that point, in this worktree. If anything above is ambiguous or contradicts what you find, stop and say what you need instead of guessing.",
        truncated: "\n\n[Seed cut to fit the prompt limit.]",
        // Used when text below the cut was kept: the marker sits at the cut point,
        // so the prompt states that its middle is missing instead of reading as if
        // the body and the closing instruction were continuous.
        truncatedMiddle: "\n\n[Part of this seed was cut to fit the prompt limit — the task notes, the acceptance criteria, or the recap above may be incomplete or stop mid-sentence. What follows was kept in full. Check the task in Agora before relying on anything above.]\n",
      },
    },
    summary: {
      did: "What it did",
      nothingYet: "No successful run is on record for this task yet.",
      // The capped/partial run history reaching the header too: "nothing was
      // produced" is a claim about the whole history, and this page holds one page
      // of it. R3 — `runsTruncated` stopped the right column claiming "never ran"
      // while the header still printed "Nothing produced yet" from the same read.
      // CR-25. This is reached from `latestDeliveredOf` whenever the run read cannot
      // support "no success exists" — a failed read *or* a page with more behind it.
      // It used to say "couldn't be read in full", which named a failure that had not
      // happened: the cap was inferred from `summaries.length >= RUN_HISTORY_LIMIT`,
      // and a history of exactly that many runs looks identical to a truncated one.
      // CR-28 then made the cap a fact rather than an inference (the read asks for one
      // more than it keeps), so the remaining reason to hedge is the *failed read* half
      // — which this sentence has to cover too, and cannot call a cap.
      producedUnknown: "Whether anything was produced can't be said — this task's run history can't be confirmed as complete.",
      // S1. A run succeeded and left nothing behind that this header can show: no
      // printable output and no files in the task's handoff. It used to share
      // `nothingYet` with "no successful run exists", which is a different result —
      // this one means a run *ran*, which is either a verified no-op or an agent that
      // did nothing, and both are things to act on rather than to read as "not
      // started yet". The wording is bounded by what is read here: the run's own
      // deliverable snapshot lives on the run card, so this does not claim the run
      // wrote no files anywhere.
      producedNothing: "A run succeeded but printed nothing, and this task's handoff lists no files. What it wrote, if anything, is on the run in the timeline.",
      // The same successful, silent run when no handoff has been read. Delivery is
      // fetched only for a task that is done, so a zero count is usually "not asked"
      // — claiming "lists no files" from it would be the same false absence one step
      // over.
      producedFilesNotRead: "A run succeeded but printed nothing. No handoff has been read for this task, so files are not counted here — see the run in the timeline.",
      producedFilesUnreadable: "A run succeeded but printed nothing, and this task's handoff could not be read.",
      // No answer yet about this task's runs. Deliberately a sentence, not the em
      // dash it used to be: under a heading that asks "what it did", a lone dash is
      // indistinguishable from a rendered empty value, and this block is where an
      // unknown must not look like an absence. The numeric cells on the right keep
      // their dash — a labelled figure that has not arrived reads as pending there.
      producedLoading: "Reading this task's runs…",
      files: "delivered {count} file(s)",
      trust: "Trustworthy completion",
      blocker: "Blocker",
      next: "Next step",
      done: "Done — nothing to do.",
      deliveryUnknown: "delivered files unavailable",
    },
    timeline: {
      title: "Timeline",
      empty: "No activity yet. Say what you want, or make the first move.",
      // Said instead of `empty` whenever a read did not fully land: an empty list
      // after a failed read is not an empty history.
      unknown: "Activity could not be read — this is not \"nothing happened yet\".",
      // …and above the entries when there are some, because a partial list shown as
      // the history is the same false completeness where nobody looks for it.
      //
      // One hedged clause and nothing else. Two earlier wordings were each wider than
      // what the page knows: "some activity could not be read" states a cause that is
      // wrong on the capped path, and "some of the history was not read" states it as
      // fact — `runsTruncated` is set whenever the page comes back at exactly the cap,
      // which is also what a history of exactly that many runs looks like, so the
      // whole history may well have been read. What is true in every case that reaches
      // this string is only that the page cannot vouch for the list being complete.
      partial: "This may not be the whole history.",
      // The in-flight half of the same caveat, kept as its own sentence. Entries show
      // as soon as one read lands, so the other read still being in flight would
      // otherwise raise the notice above on every ordinary load — and a warning that
      // appears every time is one nobody reads when it finally means something.
      //
      // "Checking for more", not "reading the rest": the read still in flight may come
      // back with nothing, so there need not be any rest. And it may never come back —
      // the loaders carry no timeout — so this line has to stay true if it persists.
      // A "needs you" event answered where it is read (CR-74): the item's words, and the
      // buttons, in the conversation. Six states of the read, each its own sentence.
      gate: {
        loading: "Reading this item…",
        unreadable: "This item's current state could not be read.",
        unknown: "Whether this item is still waiting could not be told — the open-items page was cut. Check under Needs you.",
        showBody: "Show the whole note",
        hideBody: "Hide",
        approvalHint: "Approve sends at once. Reject asks for a line first.",
        // Where the typed words go, per item (lib/inline-gate.ts gateReplyReachOf, CR-62).
        answerHint: {
          reviewFix: "Write what to change; it is sent to the developing agent as the fix request.",
          reviewOwed: "Submitting only marks this item handled so the task can go on: the review is not re-run, and this work counts as accepted without review. What you write stays on this item and is sent to no agent.",
          recordOnly: "Your answer is recorded on this item and sent to no agent.",
        },
        rejectPlaceholder: {
          reviewFix: "Why it is rejected — sent to the developing agent as the fix request (optional)",
          reviewOwed: "Why it is rejected (kept on this item only, optional)",
          recordOnly: "Why it is rejected (kept on this item only, not sent to the agent, optional)",
        },
        confirmReject: "Confirm rejection",
        answerPlaceholder: "Your answer",
        recorded: "Recorded.",
        conflict: "This item was changed elsewhere; it has been re-read.",
        failed: "Could not submit: {reason}",
        openFull: "Open under Needs you",
      },
      stillChecking: "Still checking for more history…",
      // The third case, and the reason it is not the one above: the page can know a
      // record exists that this list lacks — a note just posted, a gate raised
      // mid-run — while the read that would fetch it is not the one the reader is
      // waiting on. Saying "still checking" there described an activity that was not
      // happening. This says only what is known.
      notShownYet: "Something recorded on this task is not shown here yet.",
      inputPlaceholder: "Add a note or correct the direction…",
      send: "Send",
      sending: "Sending…",
      you: "You",
      system: "Agora",
      // Plain-language milestone for reaching a state — what actually happened,
      // not "running -> review" state-machine jargon.
      milestone: {
        backlog: "Moved back to backlog",
        requirements: "Requirements started",
        design: "Moved to design",
        planned: "Plan ready — ready to build",
        running: "Development started",
        review: "Development done — in review",
        verified: "Review passed — verified",
        done: "Delivered & completed",
        blocked: "Blocked",
        failed: "Run failed",
        cancelled: "Cancelled",
      },
      events: {
        taskCreated: "Task created",
        stageChanged: "Stage: {from} → {to}",
        specCreated: "Requirement spec drafted",
        specApproved: "Spec approved",
        specRejected: "Spec rejected",
        runQueued: "Run queued · {adapter}",
        runStarted: "Run started · {adapter}",
        runSucceeded: "Run succeeded · {adapter}",
        runFailed: "Run failed · {adapter}",
        // CR-27. These used to be the only two: everything that was not
        // `attention.responded` rendered as "Needs you", so a cancelled item, an
        // expired one, and a *successful* bridge delivery all announced that the user
        // had something to do. The event names are enumerated in
        // `ATTENTION_TIMELINE_EVENT_KEY` (lib/timeline.ts) against the backend's
        // writers, including the two that are built with an f-string and so cannot be
        // found by searching for a literal name.
        attentionRaised: "Needs you",
        // "Responded", not "You responded": `RespondAttentionRequest.actor` accepts any
        // non-empty string and the authenticated path passes the principal id, so an
        // automation or service principal would otherwise be rendered as the reader.
        attentionResponded: "Responded",
        // The event establishes cancellation and closure. A caller may cancel with no
        // reason at all, so "no longer needed" is an explanation the record does not
        // carry.
        attentionCancelled: "Cancelled",
        attentionExpired: "Expired unanswered",
        attentionDelivered: "Answer delivered",
        // Not "the answer could not be delivered": `_fail_undelivered_bridge`
        // (attention/store.py) also writes this when a bidirectional item is cancelled
        // or expires *unanswered*, and run_scope.py does the same for an orphaned
        // prompt. In those cases there was no answer, so naming one is a claim about
        // something that never existed.
        attentionDeliveryFailed: "Delivery failed",
        handoff: "Handed off",
        handoffRefused: "Handoff refused — no commit bound, task still verified",
        note: "Note",
      },
      spec: {
        view: "View the spec",
        hide: "Hide the spec",
        summary: "Summary",
        requirements: "Requirements",
        scenarios: "Acceptance scenarios",
      },
      // S2: runs are nested rather than joined by connector lines — a review run is
      // indented under the worker run it reviews, and the top level lists worker runs
      // only. These sentences exist because nesting can hide two things, and neither
      // may be hidden: a nested run's own failure, and a run this page cannot place.
      nested: {
        label: "On this run",
        // Indentation de-emphasises; a failed review under a succeeded worker run is
        // the case a person most needs to see, so the unit says it out loud.
        failed: "{count} nested run(s) failed — read them below.",
        // A run whose parent is not in this page. Not dropped, and not presented as a
        // work unit of its own: the run is real, its place is unknown.
        unplacedTitle: "Runs that could not be placed",
        unplacedParentNotLoaded: "This review's run is not in the loaded history (the newest {limit} runs), so it is shown on its own rather than under the run it reviews.",
        unplacedNamesNoRun: "This review run records no run that it reviews, so there is nothing to nest it under.",
        unplacedNamesItself: "This review run records itself as the run it reviews, which cannot be nested.",
        unplacedChainNotRooted: "This review run reviews another review, and following that chain never reaches a run this page can place (it loops, or it leads outside the loaded history), so it is shown on its own.",
      },
      run: {
        title: "{adapter} run",
        titleReview: "{adapter} review",
        exit: "exit {code}",
        prompt: "Prompt",
        showPrompt: "Show prompt",
        output: "Output",
        noOutput: "(no output captured)",
        stderr: "Errors",
        errorMessage: "Failure",
        expand: "Show full output",
        collapse: "Collapse",
        files: "Files changed ({count})",
        noFiles: "No files changed.",
        noSnapshot: "This run's file list was not recorded, so it cannot be shown.",
        gone: "The worktree these files lived in is gone, so they can no longer be opened.",
        changedSince: "Can't show this: the file no longer holds what this run delivered.",
        partial: "More files changed than were recorded — this list is partial.",
        liveFiles: "Changing now — this run is still active.",
        showFiles: "Show files changed",
        retryFiles: "Try again",
        previewTruncated: "Preview cut at the server's limit — this is not the whole file. Use \"open\" for all of it.",
        previewUnavailable: "The preview could not be read.",
        loadingFiles: "Loading files…",
        viewFile: "view",
        hideFile: "hide",
        openRaw: "open",
        loadingFile: "loading…",
      },
    },
    // The requirements strip in the header stack: the task's contract, moved off the
    // right column (which now belongs to the run) without losing anything.
    //
    // There is no `tabs` block any more, and no `panels.run` block: the right column
    // is two derived states rather than four selectable tabs, so the tab labels and
    // the "latest run" summary they fronted no longer name anything on screen. A
    // dictionary that still carries them invites the next reader to put them back.
    requirements: {
      label: "Requirements",
      loading: "Loading the spec…",
      scenarioCount: "{count} acceptance scenario(s)",
    },
    // The right column, in its two states. Every sentence here has to be true of the
    // read it came from: "no run" and "couldn't tell" are different lines on purpose.
    column: {
      openRuns: "All runs",
      title: {
        loading: "Run",
        unreadable: "Run",
        none: "Run",
        live: "Running now",
        settled: "This run's changes",
      },
      loading: "Checking whether a run is in progress…",
      unreadable: "Couldn't read this task's runs, so whether one is in progress can't be said. Nothing below is a claim that it isn't.",
      none: "This task has no runs yet. Use Continue above to start one.",
      liveWithoutRecord: "{count} run(s) of this task have not finished, and their records haven't arrived yet. Output will appear here as it is read.",
      // CR-25, same shape: reached from the same read, so it may not name a failure.
      maybeNotLatest: "This task's run history can't be confirmed as complete, so this may not be the most recent one.",
      live: {
        header: "{adapter} is running",
        alsoActive: "{count} other run(s) of this task are also unfinished.",
        noOutputYet: "No output captured yet.",
      },
      changes: {
        header: "{adapter} delivered",
      },
      gate: {
        label: "Before this is done",
        actionRequired: "{count} item(s) need an answer from you.",
        reviewRunning: "{adapter} is cross-reviewing this run.",
        unknown: "Whether anything is waiting on this run can't be said yet.",
        // Deliberately not "no review exists" and not a prediction. Nothing on
        // record accounts for the review of this run, and whether automatic review
        // is even on is not something this page can see.
        reviewNotAccounted: "Nothing on record accounts for the independent review of this run.",
        clear: "No gate is open on this task.",
        answerThem: "Answer in Attention",
        duringRun: "{count} item(s) need an answer from you while this runs.",
        unreadableDuringRun: "Couldn't read whether anything needs you on this task.",
      },
      review: {
        notWorker: "This run is not an implementation run, so no cross-review is expected for it.",
        runDidNotSucceed: "This run didn't succeed, so no cross-review was started for it.",
        // CR-25's third consumer, missed on the first pass: `reviewStandingOf` reaches
        // this from the same `runsTruncated`, which is pessimistic by one, so "was
        // incomplete" states something the read never established.
        unknown: "Whether a cross-review accounts for this run can't be said: this task's run list can't be confirmed as complete.",
        // Reached from three different situations (`reviewStandingOf`): the gate read
        // failed, it was truncated, or it returned nothing matching this run. Only the
        // first is a read failure, so naming one was false on the other two — and the
        // cap is now established by a probe rather than guessed, which makes the
        // distinction sharper rather than softer.
        gateUnread: "Whether a cross-review accounts for this run can't be said: its approvals and blockers could not be confirmed.",
        active: "The cross-review by {adapter} hasn't finished yet.",
        gateOpen: "The cross-review raised {count} item(s) for you to settle on this run.",
        // CR-29, same reason: `reviewStandingOf` treats any non-open matching item as
        // settled, including another principal's response and a cancellation, neither
        // of which the reader did.
        gateSettled: "The cross-review's {count} item(s) on this run have been settled.",
        // The two arrangements the control plane's `cause` labels distinguish. A
        // review run that finished is not itself the review being accounted for:
        // the approval or blocker it was supposed to raise is.
        noGateAfterReview: "A cross-review by {adapter} ended {state} but recorded no approval or blocker on this run, so nothing accounts for it.",
        noGateNoReview: "No cross-review run and no approval or blocker are recorded against this run.",
      },
    },
    panels: {
      openFull: "Open full page",
      unavailable: "Couldn't load this — the data is unavailable, not necessarily empty.",
      requirements: {
        none: "No requirement spec yet.",
        state: "Spec {state} · rev {rev}",
        scenarios: "Acceptance scenarios",
        summary: "Summary",
      },
      attention: {
        none: "Nothing needs you on this task.",
        loading: "Checking what needs you…",
        partial: "There may be more waiting than shown — this list hit its limit.",
        partialEmpty: "This list hit its limit, so whether anything needs you can't be said.",
      },
      changes: {
        none: "No deliverable yet.",
        delivered: "Delivered {count} file(s)",
        commit: "Commit {sha}",
        uncommitted: "Not committed",
        // The handoff's own file paths. They had no home after the changes tab was
        // removed: the count and the commit were rendered and the list they came
        // from was fetched and dropped (R4).
        files: "Files delivered",
        moreFiles: "…and {count} more.",
      },
    },
    errors: {
      loadFailed: "Could not load the workspace: {error}",
    },
    // The tasks a registered workspace directory already holds (W7), read live and
    // shown here in the workbench — not tucked away on a Settings card. Registration
    // itself stays in Settings; what lives here is the task list. The three-answer
    // honesty is the whole point (a reason is not a count), so every "nothing here"
    // sentence names which nothing it is.
    workspaceTasks: {
      title: "Workspace tasks",
      // The board as a whole. "Could not read the list" is not "none registered".
      loading: "Reading workspace sources…",
      unreadable: "The workspace source list could not be read, so registered sources may be missing or out of date.",
      none: "No workspace sources registered. Add one in Settings to see the tasks it already holds.",
      manage: "Manage in Settings",
      // Per-source, when the directory itself yields no task list. The specific reason
      // comes from settings.sources.reason.* via SOURCE_REASON_KEY.
      emptyTasksDir: "Has a tasks/ folder with nothing in it.",
      skipped: "Skipped: {names}",
      taskCount: "{count} task(s)",
      taskCountAtLeast: "At least {count} task(s) — some could not be read.",
      unreadableEntries: "Could not read: {names}",
      goalEmpty: "No goal recorded yet.",
      // Progress from the RESUME 「进度快照」. done-only percentage; the non-bar
      // answers are kept apart from a real "0 of N".
      progress: {
        counts: "{done} done · {inProgress} in progress · {todo} to do",
        // The checkbox convention has no form for work in flight, so it says done /
        // not-done and never an in-progress figure — a 0 there would assert nothing is
        // in flight, which the file never said.
        countsCheckbox: "{done} done · {notDone} not done",
        // The section exists and lists nothing — not a missing snapshot.
        empty: "Progress snapshot tracks nothing yet.",
        // Read fine; the task records no 进度快照 section at all. Deliberately says
        // nothing about a percentage.
        none: "No progress snapshot recorded.",
        // The RESUME file could not be read — not the same as "no snapshot".
        unreadable: "Progress could not be read.",
        // Read fine, but the section cannot be counted — a different fact from both
        // "empty" and "unreadable". The two reasons name themselves; this is the
        // fallback for a reason the UI has no specific sentence for.
        uncountable: "Progress snapshot was read but can't be counted.",
        // Both a marker checklist and a task-list in one section: neither reading is
        // the file's, so counting either would report a floor.
        mixedConventions: "Progress snapshot mixes ✅ markers and [ ] checkboxes, so it can't be counted.",
        // Boxes holding marks this reader has no meaning for ([/], [-], …): counting the
        // rest would be a floor, so nothing is counted.
        uncountedMarkers: "Progress snapshot uses checklist marks this reader can't count.",
      },
      // Where onward goes; sentences shared with Settings live under settings.sources.*.
      openProject: "Open {project}",
      // The CR/ADR standing and the way onward, moved here with the task list (W8). An
      // open CR never travels without the rows whose status could not be read; a read
      // failure is its own amber sentence, not a blank that reads as "none".
      crOpen: "{open} open CR",
      crUnrecognised: "{n} row(s) whose status could not be read",
      crUnavailable: "CR log could not be read",
      adrCount: "{n} decision record(s)",
      adrUnavailable: "decision records could not be read",
      // The five non-`open` onward states, each a different next move.
      repoNotRegistered: "its repository is not registered here: {repo}",
      noRepoRecorded: "no primary_repo recorded",
      repoMalformed: "primary_repo is not a usable path",
      projectsUnreadable: "the project list could not be read, so this is unknown",
      currentState: "Where it stopped",
      nextStep: "What's next",
      sectionMissing: "Not recorded.",
      resumeUnreadable: "Could not read RESUME.",
      expand: "Show more",
      collapse: "Show less",
    },
  },
  zh: {
    title: "工作台",
    // 项目级导航（mockup v2 的左栏）。导航项不携带数字，它携带一个「能力」
    // （见 lib/nav-capability.ts），下面每一条都是**不同的一句话** —— 把它们
    // 合并成「不可用」正是 constitution 禁止的那件事。
    nav: {
      projectLabel: "项目",
      items: {
        overview: "概览",
        runs: "运行",
        goals: "目标与需求",
        changeRequests: "变更 CR",
        decisions: "决策 ADR",
        defects: "已知缺陷",
        skills: "技能",
        files: "文件",
      },
      capability: {
        count: "{value}",
        countWithCaveat: {
          unclassified: "{value}（另有 {caveat} 条无法分类）",
          unopenable: "{value}（另有 {caveat} 条读不到）",
        },
        none: "无",
        unbuilt: "尚未实现",
        unbuiltWithMilestone: "尚未实现（{milestone}）",
        noReader: {
          reader_unbuilt: "尚无读取能力",
          no_structured_source: "此仓库尚无来源",
        },
        unreadable: {
          too_large: "太大读不了",
          unreadable: "读取失败",
          malformed: "格式无法解析",
        },
        unreported: "未报告",
      },
      // 悬停/聚焦时显示的完整说法。每一条都说明自己是哪一种，因为把它们统称
      // 「不可用」正是本产品拒绝做的合并。
      why: {
        none: "已成功读取，这个项目确实没有。",
        unbuilt: "这个功能还不存在，所以无从读取。",
        unbuiltWithMilestone: "这个功能还不存在 —— 计划在 {milestone}。",
        noReader: {
          reader_unbuilt: "它们存在，但 Agora 还没有读取它们的能力。",
          no_structured_source: "它们是真实存在的，但本仓库还没有结构化的来源，所以这里列不出来。",
        },
        unreadable: {
          too_large: "尝试读取了，来源太大。",
          unreadable: "尝试读取了，失败。",
          malformed: "读到了，但内容无法解析。",
        },
        unreported: "这台服务器没有报告它 —— 这是关于服务器的事实，不是关于项目的事实。",
        caveat: {
          unclassified: "有 {caveat} 条的状态无法分类，所以这个数字可能不是全貌。",
          unopenable: "有 {caveat} 条记录打不开，所以这个数字不完整。",
        },
      },
      tasks: "任务",
      otherWorkspaces: "其他工作区",
      noProject: "尚未选定项目。打开一个任务，或从 Today 进入某个项目。",
      projectsLabel: "项目",
      executor: "执行者",
      asWorker: "默认",
      asReviewer: "复核",
      environment: "环境",
      environmentUnknown: "这个项目没有登记环境——还没有环境注册表，所以「在哪儿跑」由适配器决定，这里不替它说。",
      environmentByAdapter: "各执行者在哪儿跑（来自它的配置）：",
      environmentLocal: "本机",
      switchProject: "切换项目",
    },
    crumb: { backToChat: "项目对话" },
    side: {
      order: "开发顺序 · {count} 个",
      orderNotWhole: "开发顺序 · 至少 {count} 个",
      goal: "目标",
      goalNone: "这个项目还没有目标。",
      goalFull: "全文 →",
      goalUnreadable: "目标读不到。",
      progress: "走到哪了",
      progressDetail: "明细 →",
      progressLine: "{total} 步 · 完成 {done}",
      progressLineNotWhole: "至少 {total} 步 · 完成 {done}",
      progressRunning: " · 进行中 {running}",
      now: "现在",
      nowNone: "没有在跑的。",
      nowUnknown: "说不了现在在跑什么。",
      nowEnv: "环境",
      nowAfter: "结束后",
      nowAfterAuto: "自动复核",
      waiting: "等你",
      waitingNone: "没有。",
      waitingUnknown: "读不到。",
      waitingCount: "{count} 条 · 在对话里",
      collapse: "收起右边栏",
      expand: "展开：目标、进度、等你",
      foldedRunning: "{count} 个在跑",
      rowDone: "已完成",
      rowRunning: "运行中",
      rowPending: "未开始",
      needsYou: "等你",
      written: "这一步会写什么",
      done: "已写入",
      tasksValue: "{count} 个 · planned",
      tasksValueNotWhole: "至少 {count} 个 · planned",
      nothingWritten: "目前：什么都还没写进项目。采用那一刻才写：目标、不做、验收、第一批任务。",
      lines: {
        goal: "项目目标",
        bounds: "不做 / 验收",
        tasks: "任务",
        record: "对话记录",
      },
    },
    plan: {
      title: "开发顺序",
      subtitle: "采用计划时定下的顺序。新点子在下面的对话里提出，并放进这个顺序。",
      headline: "共 {total} 步 · 完成 {done} · 进行中 {running}",
      headlineNotWhole: "至少 {total} 步 · 完成 {done} · 进行中 {running}",
      totalNotKnown: "任务列表这一次没读全整块板，所以这里是「看得见多少」，不是「一共多少」。",
      headlineWithAbandoned: "共 {total} 步 · 完成 {done} · 进行中 {running} · 已放弃 {abandoned}",
      percent: "完成 {percent}%",
      percentRule: "只算做完的步骤——进行中的不折算成半步。",
      waitingNotKnown: "至少有一步的状态读不出来，所以这里说的「在等你什么」不是全貌。",
      empty: "这个项目还没有任务。在下面的对话里把目标定下来，第一批任务就在那里提出。",
      emptyUnknown: "这一次读取里没有这个项目的任务，而且这次没读全整块板——所以还不能说「没有」。",
      step: "第 {step} 步，共 {total} 步",
      stepUnknownTotal: "第 {step} 步。一共几步，这次读取答不出来。",
      stepShort: "{step}/{total}",
      columns: {
        step: "第几步",
        task: "任务",
        state: "状态",
        progress: "进度",
        continue: "继续",
        waiting: "在等你什么",
      },
      progressUnreadable: {
        no_task_snapshot_source: "读不出来",
      },
      progressUnreadableWhy: "单条任务的进度目前没有数据源：进度快照读的是 workspace 任务的 RESUME.md，这个项目的任务没有那个文件。",
      waiting: {
        none: "没有",
        unknown: "读不出来",
        open: "{count} 条待处理",
        openAtLeast: "至少 {count} 条待处理",
        kinds: {
          question: "提问",
          approval: "待批准",
          blocker: "阻塞",
        },
      },
      running: "运行中",
      standing: {
        done: "已完成",
        abandoned: "已放弃",
      },
      continue: "继续",
      open: "打开",
      backToPlan: "返回计划",
    },
    rail: {
      search: "搜索任务",
      groups: {
        needsYou: "需你处理",
        unknown: "待确认",
        running: "运行中",
        review: "待评审",
        done: "已完成",
      },
      attentionPending: "尚未读取未处理事项，因此无法判断这些任务是否需你处理。打开任务可查看它自己的关卡。",
      attentionCapped: "未处理事项已达到 {limit} 条读取上限，还有未读到的事项，因此无法判断这些任务是否需你处理。打开任务可查看它自己的关卡。",
      attentionUnreadable: "未处理事项读取失败，因此无法判断这些任务是否需你处理。打开任务可查看它自己的关卡。",
      tasksUnreadable: "任务列表读取失败，此列表可能缺少任务或已过时。",
      projectsUnreadable: "项目列表读取失败，项目筛选可能已过时。",
      runsUnreadable: "运行状态读取失败，因此这些任务不显示运行状态。",
      unknownSubtitle: "{state} · 是否需你处理未知",
      empty: "还没有任务——创建一个开始。",
      noMatch: "没有匹配的任务。",
      relative: {
        justNow: "刚刚",
        minAgo: "{n} 分钟前",
        hourAgo: "{n} 小时前",
        dayAgo: "{n} 天前",
      },
    },
    header: {
      goal: "项目目标",
      stage: "当前阶段",
      completion: "可信完成度",
      completionValue: "{done}/{total} 项验收",
      noCriteria: "尚无已批准的验收标准",
      completionUnbound: "不显示——没有与当前验收标准绑定的证据",
      unavailable: "不可用",
      blocker: "阻塞",
      noBlocker: "无",
      blockedNoDetail: "受阻——未记录原因",
      nextStep: "下一步",
      noGoal: "尚未设定项目目标——去定义章程。",
      selectPrompt: "在左侧选择一个任务,打开它的工作台。",
      noTasksPrompt: "还没有任务。创建一个以打开工作台。",
    },
    project: {
      strip: "{done}/{total} 完成",
      stripPartial: "≥{done} 完成",
      nextPartial: "无法判定下一步——这次驾驶舱只读到一页",
      nextStale: "不显示下一步——这份驾驶舱数据来自一次失败刷新之前",
      attentionAtLeast: "≥{count} 项需要关注",
      next: "项目下一步",
      forTask: "· {title}",
      allDone: "所有任务已完成或取消",
      attention: "{count} 项需你处理",
      unavailable: "项目状态不可用",
    },
    recap: {
      today: "今天到目前",
      yesterday: "昨天",
      daysAgo: "{n} 天前",
      onDate: "最后一次进展在 {date}",
      never: "这个任务还没有发生过任何事。",
      noSummary: "那天有活动，但没有可总结的内容。",
      more: "还有 {n} 项",
      partial: "那天发生的比这里显示的多。",
      items: {
        created: "任务已创建",
        deliveredCount: "{adapter} 交付了 {files} 个文件",
        deliveredAtLeast: "{adapter} 至少交付了 {files} 个文件",
        deliveredUnknown: "{adapter} 交付了工作",
        ranNoChanges: "{adapter} 跑完了，没有改动文件",
        runFailed: "{adapter} 运行未完成",
        reviewed: "{adapter} 复核了这些工作",
        specApproved: "规格已批准",
        specRejected: "规格已拒绝",
        stageReached: "进入：{stage}",
        needsYou: "{count} 项需你处理",
        answered: "已回应 {count} 项",
        handedOff: "工作已交付",
        noted: "新增 {count} 条说明",
      },
    },
    projectRail: {
      waiting: "等你处理 {count}",
      running: "运行中",
      noGoal: "未定目标",
      idle: "空闲",
      unknown: "状态读不全",
    },
    terminal: {
      open: "终端",
      title: "终端",
      loading: "正在读这个项目能在哪儿打开……",
      targetsFailed: "读不到这个项目的任务记录；下面在仓库里打开的几项照常可用。",
      asTask: "按任务打开：{task}（claude-workspace）",
      asTaskHint: "等于 `cw open`：Claude 带着这个任务的 RESUME、CR 和评审关卡启动。",
      inRepo: {
        claude: "在仓库里开 Claude Code",
        codex: "在仓库里开 Codex",
        kiro: "在仓库里开 Kiro",
        shell: "在仓库里开 PowerShell",
      },
      authRequired: "终端只对启动这个 Agora 的人开放。请从桌面快捷方式（或 `agora.ps1 up`）打开一次 Agora，再试。",
      startFailed: "终端没起来：{reason}",
      listFailed: "读不到终端列表。",
      gone: "这个终端已经不在了（程序结束了，或服务重启过）。",
      close: "关闭终端",
      state: {
        running: "运行中",
        exited: "已结束（退出码 {code}）",
        exitedUnknown: "已结束（退出码不明）",
        exitedWith: "程序已结束（退出码 {code}）。可以往上翻看输出；要继续请新开一个终端。",
        ended: "已结束",
      },
      stream: {
        connecting: "正在连接……",
        closed: "和终端的连接断了。终端可能还在运行：刷新这个标签页即可重连。",
        tooSlow: "这个窗口落后太多被断开了；刷新后会从保存的输出接上。",
        refused: "服务端拒绝了这个终端（没有终端权限，或终端已不存在）。",
      },
    },
    stream: {
      title: "项目对话",
      you: "你",
      auto: "Agora · 自动",
      loading: "正在读……",
      empty: "还没有任何运行。说一句要做什么就开始。",
      runsUnreadable: "读不到这个项目的运行记录。",
      gatesUnreadable: "读不到等你处理的事项。",
      runsCapped: "只显示最近 {limit} 次运行。",
      gatesCapped: "事项超过 {limit} 条，多出的这里没显示——去「待你处理」里确认。",
      noteTag: "备注",
      reply: "{adapter} 的回复",
      stop: "停止",
      stopping: "正在停止…",
      stopFailed: "停不下来：{reason}",
      transcriptWaiting: "等第一行输出…",
      transcriptWorking: "还在跑…",
      transcriptStalled: "上一次读取没回来——重试中",
      transcriptUnreadable: "读不到 transcript",
      transcriptLossy: "有一部分输出没来得及保存就丢了",
      answering: "{adapter} 回答中…",
      answerMeta: "{run}{state} · 退出码 {exit} · {elapsed}",
      answerFailed: "{adapter} 没有回答——{state}{detail}",
      answerRead: "读取这次问答",
      answerReadFailed: "读不到这次问答——再试一次",
      answerChanged: "这次提问的 run 改了文件（本不该改）：",
      reviewReply: "{adapter} 的复核",
      promptSummary: "开始第 {step} 步的提示词（点开看全文）",
      seedVerify: "做完后请在最后写清：改了哪些文件；怎么启动或验证（命令、地址）；截图放在哪。我在对话里看结果，不看日志。",
      replyFolded: "回复较长，只显示末尾；全文在卡片「展开」里。",
      reviewStoodIn: "顶替 {adapter}（额度用完）",
      reviewQuota: "{adapter} 额度用完，这次复核没做完。",
      reviewQuotaUntil: "{adapter} 额度用完（它说 {when} 之后可以再用），这次复核没做完。",
      reviewFailedBecause: "{adapter} 的复核没做完：{reason}",
      status: {
        running: "{adapter} · 第 {step} 步 运行中",
        waiting: "{count} 条等你",
        idle: "没有在跑的",
        unknown: "说不了现在在跑什么",
      },
      run: {
        step: "第 {step} 步",
        stepUnknown: "步骤未知",
        review: "复核",
        chat: "提问",
        exit: "退出码 {code}",
        nested: "{count} 次复核",
        expand: "展开",
        collapse: "收起",
        prompt: "提示词",
        output: "输出",
        noOutput: "没有可显示的输出。",
        readFailed: "读不到这次运行的详情。",
        unplaced: {
          parentMissing: "它复核的那次运行不在这一页里",
          parentUnknown: "它复核的那次运行未知",
          reviewOfReview: "复核的复核",
        },
      },
      compose: {
        placeholder: "像在终端里一样跟 agent 说话，它自己决定是回答还是动手。「备注：」只记时间线；「点子：」让 agent 判断要不要加任务。回车发送，Shift+回车换行。",
        askHint: "对第 {step} 步的提问——只回答，不复核，步骤不动",
        noteHint: "只记进时间线",
        ideaHint: "让 agent 判断要不要加任务",
        askPreamble: "这是 owner 的提问，不是派活。不要修改任何文件；根据工作树和任务里的内容，在对话里直接回答。",
        asked: "已问 {adapter}，答完会出现在下面。",
        adapter: "执行者",
        envLocal: "本机",
        envRemote: "远端 · {target}",
        envTitle: "run 在哪儿执行。默认跟随任务：跑过的任务留在它的改动所在处；新任务用执行者自己的位置。新任务也可以选一个已注册的远端环境。",
        environment: "环境",
        envFollowTask: "跟随任务所在位置（新任务用：{own}）",
        envRegistered: "{label} · {target}",
        envListFailed: "已注册环境读取失败，暂时只能选执行者自己的位置",
        envGone: "所选环境已不在注册表里，换一个再发。",
        fixPlaceUnknown: "读不到被评审的那次 run，不知道它的改动在哪儿，修正没有发出。",
        send: "发送",
        to: "会派到第 {step} 步「{title}」",
        busyShort: "第 {step} 步正在跑——发出去的话要等它结束",
        busy: "{adapter} 正在跑第 {step} 步，等它结束后再发。",
        allDone: "全部步骤已完成。",
        noneStartable: "开发顺序里没有可开始的步骤。",
        toNewStep: "现在没有可接手的步骤，这句话会成为新的一步并开始。",
        sentNew: "已作为新的一步「{title}」发给 {adapter}。它要是改了文件，跑完会自动复核。",
        newStepNotStartable: "新步骤「{title}」的状态是 {state}，不能接活，什么都没发。",
        unknown: "读不全开发顺序，说不了该派给哪一步。",
        noteNoTask: "还没有可以记在上面的步骤。",
        noted: "记进时间线了，没派活。",
        sent: "已发给 {adapter}，第 {step} 步。它要是改了文件，跑完会自动复核。",
        sendFailed: "没发出去：{reason}",
        conflict: "控制平面拒绝了：{detail}",
        worktreeNotReady: "工作树没准备好：{detail}",
      },
      idea: {
        thinking: "正在判断这是任务、第二版、还是备注……",
        failed: "agent 没给出判断：{reason}",
        echo: "我记成：{echo}",
        verdict: {
          task: "这是一个新任务。建议放到开发顺序第 {position} 位。",
          later: "这属于第二版。",
          note: "这是一条备注，不是任务。",
        },
        placeHere: "加在第 {position} 位",
        placeLast: "放最后",
        skip: "不加",
        skipped: "没加。",
        placed: "已加入开发顺序，第 {position} 位。",
        placedUnknown: "已加入，但位置没读回来。",
      },
      advance: {
        done: "第 {step} 步「{title}」已完成并提交（{commit}）。",
        doneNothing: "第 {step} 步「{title}」已完成；工作树里没有待提交的改动。",
        next: "下一步是第 {step} 步「{title}」。提示词按任务描述写好了：",
        seed: "下一步的提示词",
        start: "开始第 {step} 步",
        edit: "改一句再开始",
        editDone: "改好了",
        started: "已开始。",
        with: "由 {adapter} 执行",
        noNext: "开发顺序里没有待开始的步骤了。",
        noNextUncertain: "读到的范围内没有待开始的步骤，列表可能被截断。",
        taskUnknown: "已批准，但这条事项对应的任务不在列表里，没有做交付。",
      },
    },
    detail: {
      back: "回到对话",
      goal: {
        title: "目标与需求",
        goal: "目标",
        nonGoals: "先不做",
        criteria: "怎么算做完",
        tasks: "步骤与各自的「完成的定义」",
        unreadable: "目标读不到。",
        none: "这个项目还没有目标。",
        acceptance: "验收",
        noAcceptance: "没有记录验收条目。",
      },
      order: {
        title: "开发顺序",
        step: "#",
        task: "步骤",
        state: "状态",
        waiting: "在等你什么",
        none: "没有步骤。",
      },
      runs: {
        title: "运行记录",
        run: "run",
        who: "谁",
        step: "步骤",
        when: "开始",
        outcome: "结果",
        none: "还没有运行。",
      },
      decisions: {
        title: "决策",
        unavailable: "决策记录读不到（{reason}）。",
        none: "还没有决策记录。",
        unreadableCount: "有 {count} 条记录打不开。",
      },
      changeRequests: {
        title: "变更",
        none: "CR Log 里没有记录。",
        unknown: "CR Log 读不到（{reason}）。",
      },
      defects: {
        title: "已知缺陷",
        unavailable: "读不到缺陷列表（{reason}）。",
        noSource: "这个项目还没有登记缺陷记录的来源；产品不猜。",
        none: "这个项目没有已知缺陷。",
        openCount: "{count} 个未修",
        unrecognisedCount: "{count} 个状态无法识别",
        unreadableCount: "{count} 个读不到",
      },
      files: {
        title: "文件",
        noView: "文件视图还没接上。工作树在项目目录的 .agora/workspaces 下。",
      },
    },
    continue: {
      action: "继续",
      close: "关闭继续面板",
      panelTitle: "将要交给执行者的内容",
      panelHint: "内容取自这个任务已记录的信息。可以修改——在你点击开始之前不会发送任何东西。",
      reseed: "恢复为自动生成的内容",
      start: "开始 Codex 运行",
      starting: "启动中…",
      provisioning: "正在创建 Codex 工作树…",
      cancel: "取消",
      count: "{n} / {limit}",
      adapterNote: "以 Codex 运行，在本项目的 Codex 工作树中。",
      unblock: "解除受阻，回到「计划就绪」",
      unblockHint: "你拒掉的那份复核意见还在「待你处理」里。回到计划就绪后，这里的下一条消息就是下一轮。",
      unblockFailed: "没能解除受阻：{reason}",
      markDone: "标记完成",
      markDoneHint: "已验证。标记完成会把 worker 工作树里还没提交的东西提交掉，并把这个提交绑到任务上。",
      markDoneCommitted: "已完成。提交了 {commit}（{count} 个文件）。",
      markDoneNothingToCommit: "已完成。工作树里没有待提交的改动，绑定的是已有提交。",
      markDoneFailed: "没能标记完成：{reason}",
      advance: {
        completed: "第 {step} 步「{title}」已完成并提交（{commit}）。",
        completedNothingToCommit: "第 {step} 步「{title}」已完成；工作树里没有待提交的改动。",
        nextHere: "这是下一步。提示词在下面，看一眼，点一下就开始。",
        noNext: "这个项目的开发顺序里没有待开始的步骤了。",
        noNextUncertain: "在读到的范围内没找到待开始的步骤——列表可能被截断了。",
        completeFailed: "已批准，但标记完成没成功：{reason}。任务仍是「已验证」，用上面的「标记完成」。",
        fixSent: "已拒绝。复核意见和你的话已作为下一轮派回给 worker。",
        fixFailed: "已拒绝，但派回修改没成功：{reason}",
      },
      blocked: {
        dispatching: "正在启动运行…",
        taskNotRunnable: "只有处于「计划就绪」或「运行中」的任务才能继续——当前是{state}。",
        runActive: "已在运行——这个任务还有未结束的运行。",
        runsUnknown: "读取这个任务的运行记录失败，因此无法判断是否已有运行在进行。请先刷新再启动新的运行。",
        attentionOpen: "这个任务上有事项在等你处理——请先在「待你处理」中回应，再启动新的运行。",
        attentionUnknown: "读取这个任务的待处理事项失败，因此无法判断是否有事项在等你。请先刷新再启动新的运行。",
        followUpPending: "刚结束的运行正在等待评审是否启动。请稍候再启动新的运行。",
      },
      worktree: {
        checking: "正在检查 Codex 工作树…",
        ready: "Codex 工作树就绪，分支 {branch}。",
        willProvision: "Codex 工作树尚不存在——将在运行开始前创建。",
        provisioning: "Codex 工作树正在创建中，请等待完成。",
        unreadable: "读取 Codex 工作树状态失败，因此它是否存在属于未知——这与「不存在」不是一回事。{detail}",
        foreign: "Codex 工作树路径上是 Agora 之外创建的内容，无法使用。",
        notGit: "项目目录不是 git 仓库，无法创建工作树。",
        unprovisionable: "无法准备 Codex 工作树：{detail}",
      },
      errors: {
        alreadyRunning: "这个任务已有运行在进行，因此本次没有启动。",
        gateOpen: "这个任务上有一道门在等你处理，因此没有启动任何运行。{detail}",
        reviewActive: "这个任务的评审还没有结束，因此没有启动任何运行。{detail}",
        reviewOwed: "上一次实现运行还没有任何独立评审算数，因此没有启动任何运行。{detail}",
        taskChanged: "任务在本页面之外发生了变化，因此没有启动任何运行。请刷新后重试。",
        conflictUnknown: "控制平面拒绝了这次运行，而重新读取这个任务的运行记录也失败了——因此拒绝的原因，以及现在是否有运行在执行，都属于未知。请先刷新再重试。（{detail}）",
        taskGone: "任务或其所属项目已不存在，因此没有启动任何运行。（{detail}）",
        rejected: "控制平面拒绝了这次运行，没有启动：{detail}",
        provisionFailed: "无法创建 Codex 工作树，因此没有启动任何运行：{detail}",
        worktreeNotReady: "Codex 工作树仍未就绪，因此没有启动任何运行：{detail}",
        startedUnconfirmed: "没有收到回复，但这个任务现在有运行在进行——请按「已启动」处理并在下方观察，不要再启动一次。（{detail}）",
        startUnknown: "没有收到回复，重新读取这个任务的运行记录也失败了——因此运行是否已启动属于未知，而不是「没有启动」。请先刷新再重试。（{detail}）",
        transportFailed: "这次请求没有收到回复，而现在这个任务没有运行在进行。可能请求根本没有到达控制平面，也可能它创建的运行已经结束——请先查看时间线再重试。（{detail}）",
        unknown: "无法启动运行：{detail}",
        afterProvision: "Codex 工作树已创建并就绪——只是运行没能启动，现在没有任何运行在进行。",
        afterProvisionUnsure: "Codex 工作树已创建并就绪。运行是否已启动无法确认——请先查看下方的运行记录，再决定是否重新启动。",
      },
      seed: {
        goalLabel: "任务目标：",
        descriptionLabel: "任务说明：",
        acceptanceLabel: "完成的标准：",
        recapUnavailable: "无法读取这个任务上次停在哪里，因此这里没有总结。在做任何假设之前，请先查看任务时间线。",
        recapNever: "这个任务还没有任何记录。",
        recapNoItems: "那天有活动，但没有可总结的内容。",
        recapMore: "这份总结不完整：那天还有 {n} 项已记录的内容没有列在这里。",
        recapPartial: "那天发生的比这些记录覆盖的更多，请把上面的总结当作不完整的。",
        nextLabel: "已记录的下一步：",
        nextUnknown: "无法读取已记录的下一步。",
        instruction: "请在这个工作树中，从上面所述的位置继续这个任务。如果上面有含糊之处，或与你实际看到的情况矛盾，请停下并说明你需要什么，不要猜测。",
        truncated: "\n\n[为符合长度上限，内容已被截断。]",
        truncatedMiddle: "\n\n[为符合长度上限，这份内容的一部分被截去了——上面的任务说明、完成标准或工作回顾可能不完整，也可能在句子中间断开。下面的内容是完整保留的。在依据上面的内容行动之前，请先在 Agora 中核对这个任务。]\n",
      },
    },
    summary: {
      did: "做了什么",
      nothingYet: "这个任务还没有成功的运行记录。",
      producedUnknown: "无法判断是否有产出——无法确认此任务的运行历史是完整的。",
      producedNothing: "有一次运行成功了,但没有任何输出,而这个任务的交接记录里也没有文件。它究竟写了什么,请看时间线中的那次运行。",
      producedFilesNotRead: "有一次运行成功了,但没有任何输出。这个任务还没有读取到交接记录,因此这里不统计文件——请看时间线中的那次运行。",
      producedFilesUnreadable: "有一次运行成功了,但没有任何输出,而这个任务的交接记录无法读取。",
      producedLoading: "正在读取这个任务的运行记录……",
      files: "交付 {count} 个文件",
      trust: "可信完成度",
      blocker: "阻塞",
      next: "下一步",
      done: "已完成,无需操作。",
      deliveryUnknown: "交付文件读取失败",
    },
    timeline: {
      title: "时间线",
      empty: "还没有活动。说出你想要什么,或者先迈出第一步。",
      unknown: "活动记录读不到——这不等于「还没有发生过任何事」。",
      partial: "下面这些可能不是全部的历史。",
      gate: {
        loading: "正在读这条事项……",
        unreadable: "读不到这条事项现在的状态。",
        unknown: "这条事项是否还在等你，这次读取说不了——待处理列表被截断了。去「待你处理」里确认。",
        showBody: "展开全文",
        hideBody: "收起",
        approvalHint: "点「批准」即提交。「拒绝」会先让你写一句原因。",
        // Where the typed words go, per item (lib/inline-gate.ts gateReplyReachOf, CR-62).
        answerHint: {
          reviewFix: "写下要改什么，会作为修正要求发给开发的 agent。",
          reviewOwed: "提交只会把这条标成已处理，让任务能继续：不会重新复核，这次实现就算没经过复核被接受。写的内容只记在这条事项上，不会发给任何 agent。",
          recordOnly: "你的回答会记在这条事项上，不会发给任何 agent。",
        },
        rejectPlaceholder: {
          reviewFix: "为什么拒——会作为修正要求发给开发的 agent（可选）",
          reviewOwed: "为什么拒（只记在这条事项上，可选）",
          recordOnly: "为什么拒（只记在这条事项上，不会发给 agent，可选）",
        },
        confirmReject: "确认拒绝",
        answerPlaceholder: "你的回答",
        recorded: "已记录。",
        conflict: "这条事项在别处已被改过，已重新读取。",
        failed: "没提交成功：{reason}",
        openFull: "在「待你处理」里打开",
      },
      stillChecking: "还在确认是否有更多历史……",
      notShownYet: "此任务上有一条记录还没显示出来。",
      inputPlaceholder: "添加一条说明,或纠正方向……",
      send: "发送",
      sending: "发送中…",
      you: "你",
      system: "Agora",
      milestone: {
        backlog: "退回待办",
        requirements: "开始梳理需求",
        design: "进入设计",
        planned: "计划就绪,准备开发",
        running: "开始开发",
        review: "开发完成,进入评审",
        verified: "评审通过,已验证",
        done: "已交付完成",
        blocked: "受阻",
        failed: "运行失败",
        cancelled: "已取消",
      },
      events: {
        taskCreated: "任务已创建",
        stageChanged: "阶段:{from} → {to}",
        specCreated: "已起草需求规格",
        specApproved: "规格已批准",
        specRejected: "规格已拒绝",
        runQueued: "运行已排队 · {adapter}",
        runStarted: "运行已开始 · {adapter}",
        runSucceeded: "运行成功 · {adapter}",
        runFailed: "运行失败 · {adapter}",
        attentionRaised: "需要你处理",
        attentionResponded: "已回应",
        attentionCancelled: "已取消",
        attentionExpired: "已过期,未回应",
        attentionDelivered: "回应已送达",
        attentionDeliveryFailed: "送达失败",
        handoff: "已交付",
        handoffRefused: "交付被拒——没有绑定到提交，任务仍是已验证",
        note: "说明",
      },
      spec: {
        view: "查看规格",
        hide: "收起规格",
        summary: "摘要",
        requirements: "需求",
        scenarios: "验收场景",
      },
      nested: {
        label: "属于这次运行",
        failed: "有 {count} 次嵌套运行失败——请在下方查看。",
        unplacedTitle: "无法归位的运行",
        unplacedParentNotLoaded: "这次评审所对应的运行不在已加载的历史里(最新 {limit} 条),因此它单独显示,而不是挂在被评审的运行下面。",
        unplacedNamesNoRun: "这次评审运行没有记录它评审的是哪次运行,因此无法归位。",
        unplacedNamesItself: "这次评审运行把自己记为被评审的运行,无法归位。",
        unplacedChainNotRooted: "这次评审运行评审的是另一次评审,沿这条链一直追下去也找不到本页能够归位的运行(出现环,或者链指向已加载历史之外),因此它单独显示。",
      },
      run: {
        title: "{adapter} 运行",
        titleReview: "{adapter} 评审",
        exit: "退出码 {code}",
        prompt: "指令(Prompt)",
        showPrompt: "查看指令",
        output: "输出",
        noOutput: "(未捕获到输出)",
        stderr: "错误输出",
        errorMessage: "失败原因",
        expand: "展开完整输出",
        collapse: "收起",
        files: "改动文件({count})",
        noFiles: "无文件改动。",
        noSnapshot: "这次运行没有记录改动文件清单，无法显示。",
        gone: "这些文件所在的工作树已不存在，无法再打开。",
        changedSince: "无法显示：该文件已不再是这次运行交付的内容。",
        partial: "改动的文件数超过记录上限——此列表不完整。",
        liveFiles: "正在变化 —— 这次运行还没结束。",
        showFiles: "查看改动文件",
        retryFiles: "重试",
        previewTruncated: "预览在服务器上限处被截断 —— 这不是整个文件。要看全文用「打开预览」。",
        previewUnavailable: "预览读不到。",
        loadingFiles: "加载文件中…",
        viewFile: "查看",
        hideFile: "收起",
        openRaw: "打开预览",
        loadingFile: "加载中…",
      },
    },
    requirements: {
      label: "需求",
      loading: "正在加载需求规格……",
      scenarioCount: "{count} 条验收场景",
    },
    column: {
      openRuns: "全部运行",
      title: {
        loading: "运行",
        unreadable: "运行",
        none: "运行",
        live: "正在运行",
        settled: "本次运行的改动",
      },
      loading: "正在确认是否有运行在进行中……",
      unreadable: "无法读取此任务的运行记录,因此无法判断是否有运行正在进行。下面的内容不代表没有运行。",
      none: "此任务尚无任何运行。可用上方的“继续”发起一次。",
      liveWithoutRecord: "此任务有 {count} 次运行尚未结束,但记录还没读到。输出会在读到后显示在这里。",
      maybeNotLatest: "无法确认此任务的运行历史是完整的,因此这可能不是最近的一次。",
      live: {
        header: "{adapter} 正在运行",
        alsoActive: "此任务还有 {count} 次运行尚未结束。",
        noOutputYet: "尚未捕获到任何输出。",
      },
      changes: {
        header: "{adapter} 的交付",
      },
      gate: {
        label: "在完成之前",
        actionRequired: "有 {count} 项待你处理。",
        reviewRunning: "{adapter} 正在对这次运行做交叉评审。",
        unknown: "暂时无法判断这次运行是否还有待处理的事项。",
        reviewNotAccounted: "没有任何记录能说明这次运行已经过独立评审。",
        clear: "此任务当前没有未处理的门禁。",
        answerThem: "去“关注”处理",
        duringRun: "运行期间有 {count} 项待你处理。",
        unreadableDuringRun: "无法读取此任务是否有待你处理的事项。",
      },
      review: {
        notWorker: "这次运行不是实现运行,因此不会有针对它的交叉评审。",
        runDidNotSucceed: "这次运行没有成功,因此没有为它启动交叉评审。",
        unknown: "无法判断这次运行是否已由交叉评审承接:无法确认此任务的运行列表是完整的。",
        gateUnread: "无法判断这次运行是否已由交叉评审承接:无法确认它的审批与阻塞项。",
        active: "{adapter} 的交叉评审尚未结束。",
        gateOpen: "交叉评审针对这次运行提出了 {count} 项待你处理的事项。",
        gateSettled: "交叉评审针对这次运行提出的 {count} 项事项已处理完毕。",
        noGateAfterReview: "{adapter} 的交叉评审以 {state} 结束,但没有针对这次运行记录任何审批或阻塞项,因此这次评审并未被承接。",
        noGateNoReview: "没有记录到针对这次运行的交叉评审运行,也没有任何审批或阻塞项。",
      },
    },
    panels: {
      openFull: "打开完整页面",
      unavailable: "加载失败——数据不可用,不代表为空。",
      requirements: {
        none: "尚无需求规格。",
        state: "规格 {state} · 修订 {rev}",
        scenarios: "验收场景",
        summary: "摘要",
      },
      attention: {
        none: "此任务当前没有待你处理的事项。",
        loading: "正在确认是否有待你处理的事项……",
        partial: "可能还有未显示的事项——这份列表已达上限。",
        partialEmpty: "这份列表已达上限,因此无法判断是否有待你处理的事项。",
      },
      changes: {
        none: "尚无交付物。",
        delivered: "已交付 {count} 个文件",
        commit: "提交 {sha}",
        uncommitted: "未提交",
        files: "交付的文件",
        moreFiles: "……另有 {count} 个。",
      },
    },
    errors: {
      loadFailed: "无法加载工作台:{error}",
    },
    workspaceTasks: {
      title: "工作区任务",
      loading: "正在读取工作区来源……",
      unreadable: "无法读取工作区来源列表,已注册的来源可能缺失或已过时。",
      none: "尚未注册任何工作区来源。请在“设置”中添加,以查看它已有的任务。",
      manage: "在设置中管理",
      emptyTasksDir: "有 tasks/ 目录,但里面什么都没有。",
      skipped: "已跳过:{names}",
      taskCount: "{count} 个任务",
      taskCountAtLeast: "至少 {count} 个任务——有些无法读取。",
      unreadableEntries: "无法读取:{names}",
      goalEmpty: "尚未记录目标。",
      progress: {
        counts: "{done} 已完成 · {inProgress} 进行中 · {todo} 待办",
        countsCheckbox: "{done} 已完成 · {notDone} 未完成",
        empty: "进度快照尚未记录任何条目。",
        none: "没有记录进度快照。",
        unreadable: "无法读取进度。",
        uncountable: "进度快照已读取，但无法计数。",
        mixedConventions: "进度快照同时使用了 ✅ 标记和 [ ] 复选框，因此无法计数。",
        uncountedMarkers: "进度快照使用了本读取器无法识别的清单标记。",
      },
      openProject: "打开 {project}",
      crOpen: "{open} 项未决 CR",
      crUnrecognised: "{n} 行状态无法读取",
      crUnavailable: "无法读取 CR 日志",
      adrCount: "{n} 条决策记录",
      adrUnavailable: "无法读取决策记录",
      repoNotRegistered: "它的仓库尚未在此注册：{repo}",
      noRepoRecorded: "未记录 primary_repo",
      repoMalformed: "primary_repo 不是可用的路径",
      projectsUnreadable: "项目列表读取失败，因此这一项属于未知",
      currentState: "停在哪里",
      nextStep: "下一步",
      sectionMissing: "未记录。",
      resumeUnreadable: "无法读取 RESUME。",
      expand: "展开",
      collapse: "收起",
    },
  },
} as const;
