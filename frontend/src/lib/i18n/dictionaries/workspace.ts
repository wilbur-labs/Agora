// Task Workspace: the task-centric three-column workbench. One task = one work
// session you can switch to, see where it stands, and know the next step —
// without hopping between the Portfolio / Requirements / Runs / Attention pages.

export const workspace = {
  en: {
    title: "Workspace",
    rail: {
      newTask: "New task",
      search: "Search tasks",
      allProjects: "All projects",
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
    continue: {
      action: "Continue with Codex",
      close: "Close the Continue panel",
      panelTitle: "What Codex will be asked to do",
      panelHint: "Seeded from what this task recorded. Edit it — nothing is sent until you press start.",
      reseed: "Reset to the seeded prompt",
      start: "Start Codex run",
      starting: "Starting…",
      provisioning: "Creating the Codex worktree…",
      cancel: "Cancel",
      count: "{n} / {limit}",
      adapterNote: "Runs as Codex, in this project's Codex worktree.",
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
    create: {
      eyebrow: "New task",
      title: "What do you want to accomplish?",
      close: "Close new task dialog",
      project: "Project",
      goalLabel: "1 · What do you want to accomplish?",
      goalPlaceholder: "e.g. Add email login to the app",
      doneLabel: "2 · What counts as done? (one per line)",
      donePlaceholder: "A user can sign in with email\nInvalid credentials show an error",
      avoidLabel: "3 · Anything it must NOT do? (one per line)",
      avoidPlaceholder: "No social login\nDon't touch the billing code",
      advanced: "Advanced settings",
      advancedHint: "Agent, model, reviewer, permissions and timeout use project defaults. Change them on the full task page after creating.",
      submit: "Create task",
      submitting: "Creating…",
    },
    errors: {
      loadFailed: "Could not load the workspace: {error}",
    },
  },
  zh: {
    title: "工作台",
    rail: {
      newTask: "新建任务",
      search: "搜索任务",
      allProjects: "全部项目",
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
    continue: {
      action: "用 Codex 继续",
      close: "关闭继续面板",
      panelTitle: "将要交给 Codex 的内容",
      panelHint: "内容取自这个任务已记录的信息。可以修改——在你点击开始之前不会发送任何东西。",
      reseed: "恢复为自动生成的内容",
      start: "开始 Codex 运行",
      starting: "启动中…",
      provisioning: "正在创建 Codex 工作树…",
      cancel: "取消",
      count: "{n} / {limit}",
      adapterNote: "以 Codex 运行，在本项目的 Codex 工作树中。",
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
    create: {
      eyebrow: "新建任务",
      title: "你想完成什么?",
      close: "关闭新建任务对话框",
      project: "项目",
      goalLabel: "1 · 你想完成什么?",
      goalPlaceholder: "例如:给应用增加邮箱登录",
      doneLabel: "2 · 什么算完成?(每行一条)",
      donePlaceholder: "用户能用邮箱登录\n凭证错误时显示提示",
      avoidLabel: "3 · 有哪些不能做?(每行一条)",
      avoidPlaceholder: "不做社交登录\n不要改动计费相关代码",
      advanced: "高级设置",
      advancedHint: "Agent、模型、Reviewer、权限与超时使用项目默认值。创建后可在完整任务页修改。",
      submit: "创建任务",
      submitting: "创建中…",
    },
    errors: {
      loadFailed: "无法加载工作台:{error}",
    },
  },
} as const;
