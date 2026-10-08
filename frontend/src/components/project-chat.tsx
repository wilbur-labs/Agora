"use client";

/**
 * The project conversation — the workbench's middle column when no task is open (CR-62).
 *
 * Before the project has a goal this is the kickoff, as a conversation rather than a
 * form: the agent reads the repository once, asks one question at a time with candidates
 * to click, and proposes the charter and the first tasks. Adopting writes the goal and
 * creates the tasks *planned*, so they can be dispatched the moment they appear (CR-60).
 *
 * After adoption the same conversation stays open. Anything said in it is judged against
 * the charter, and when it is work the agent proposes a card and a place in the
 * development order (CR-63) — which is why there is no "new task" dialog any more.
 *
 * Two honesty rules live here as well as in lib/project-chat.ts:
 *  - what is echoed back after an answer is the answer *as recorded*, labelled as the
 *    record; the kickoff's two passes do not answer per question, and dressing a local
 *    restatement up as the agent's understanding would be a claim nothing supports.
 *  - where a placed card landed is read back from the board, not repeated from the
 *    position that was asked for.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { RecordedGoals } from "@/components/recorded-goals";
import { useI18n } from "@/lib/i18n";
import {
  adoptKickoff,
  getKickoff,
  placeIdeaTask,
  proposeFromIdea,
  refineKickoff,
  saveKickoffAnswers,
  startKickoff,
  type IdeaProposal,
  type KickoffPayload,
} from "@/lib/control-plane";
import {
  MOCKUP_TASK_KIND,
  adoptBlockedBy,
  kickoffStepOf,
  proposalCaveatsOf,
  type KickoffCharterLike,
  type KickoffQuestionLike,
  type KickoffRecordLike,
  type KickoffStep,
  type KickoffTaskLike,
} from "@/lib/kickoff";
import {
  DRAFT_BRIEF_KEY,
  YOU_DECIDE_ANSWER,
  answerChipsOf,
  composeModeOf,
  currentQuestionOf,
  draftFieldsOf,
  landingNoteOf,
  positionChipsOf,
  type AnswerChip,
} from "@/lib/project-chat";
import { cn } from "@/lib/utils";

/** One thing the owner said after adoption, and what came of it. */
interface IdeaTurn {
  key: string;
  idea: string;
  /** Null while the agent is still working. */
  proposal: IdeaProposal | null;
  error: string | null;
  /** Set once the card was placed, declined, or kept for later. */
  outcome:
    | { kind: "placed"; note: ReturnType<typeof landingNoteOf>; title: string }
    | { kind: "later" }
    | { kind: "no" }
    | null;
}

export function ProjectChat({
  projectId,
  onAdopted,
  onOpenTask,
  onDraftState,
}: {
  projectId: string;
  /** The charter and the board changed: the page re-reads both. */
  onAdopted: () => void;
  onOpenTask: (taskId: string) => void;
  /** The draft's inputs, lifted so the page can keep it in the one right-hand column
   *  the design has — it is drawn beside an open step too, and an aside owned by this
   *  component would vanish the moment the centre switched to one. */
  onDraftState?: (state: { step: KickoffStep; record: KickoffRecordLike | null; freshKey: string | null }) => void;
}) {
  const { t } = useI18n();
  const [read, setRead] = useState<KickoffPayload | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [busy, setBusy] = useState<null | "start" | "answer" | "refine" | "adopt" | "idea" | "place">(null);
  const [error, setError] = useState<string | null>(null);
  const [draftText, setDraftText] = useState("");
  const [writingOwn, setWritingOwn] = useState(false);
  const [ownText, setOwnText] = useState("");
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [ideas, setIdeas] = useState<IdeaTurn[]>([]);
  // What the person just sent, shown as their bubble the moment they send it (CR-119).
  // The kickoff's passes take a minute; the bubble used to appear only when the agent's
  // answer came back, so the message seemed to vanish while the agent thought.
  const [sent, setSent] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement | null>(null);
  // Every request that may set `read` takes a ticket; only the newest may apply its
  // answer, so a re-read started by a failure cannot land after the retry that worked.
  const ticket = useRef(0);

  const load = useCallback((signal?: AbortSignal): Promise<KickoffPayload | null> => {
    const mine = ++ticket.current;
    return getKickoff(projectId, signal)
      .then((payload) => {
        if (!signal?.aborted && mine === ticket.current) { setRead(payload); setReadFailed(false); }
        return payload;
      })
      .catch(() => {
        if (!signal?.aborted && mine === ticket.current) { setRead(null); setReadFailed(true); }
        return null;
      });
  }, [projectId]);

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  // Memoised on purpose: the effect below hands `step` up to the page, which stores it and
  // re-renders us. A fresh object per render would make that effect fire every render.
  const step = useMemo(() => kickoffStepOf(read, readFailed), [read, readFailed]);
  const mode = composeModeOf(step);
  const record = "record" in step ? step.record : null;

  // Handed up rather than rendered here: the right-hand column outlives this component.
  useEffect(() => {
    onDraftState?.({ step, record, freshKey });
  }, [onDraftState, step, record, freshKey]);

  useEffect(() => {
    // The newest turn is what the person is looking at.
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [step.kind, record?.revision, ideas.length, busy, sent]);

  const run = async (
    what: NonNullable<typeof busy>,
    action: () => Promise<KickoffPayload>,
    said?: { text: string; restore: () => void },
  ) => {
    setBusy(what);
    setError(null);
    if (said) setSent(said.text);
    const mine = ++ticket.current;
    const revisionBefore = read?.record?.revision ?? null;
    try {
      const payload = await action();
      if (mine === ticket.current) { setRead(payload); setReadFailed(false); }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      // The record may have moved without this tab hearing it. Re-read so the
      // conversation shows the record as it is rather than the step it was on; when it
      // did move, the failure banner would contradict what is now on screen.
      const after = await load();
      const moved = after !== null && (after.record?.revision ?? null) !== revisionBefore;
      if (moved) setError(null);
      // The box was emptied on send; a failure puts the words back to retry from — but
      // only when the record did not move. If it did, the send most likely landed (the
      // response was lost, or another tab got there first) and the box now belongs to
      // the next question; putting the old words there would save them as its answer
      // on the next send (kiro round 3 on CR-119).
      else said?.restore();
      if (what === "adopt") onAdopted();
    } finally {
      setBusy(null);
      setSent(null);
    }
  };

  const answerCurrent = (value: string) => {
    if (record === null) return;
    const current = currentQuestionOf(record);
    if (current === null) return;
    const text = value.trim();
    if (!text) return;
    setFreshKey(current.question.id);
    // The boxes empty on send, because the words are now on screen as the person's
    // bubble (CR-119). A failure restores them exactly — a paragraph of Japanese must
    // never be lost to a request that did not land.
    const before = { draftText, writingOwn, ownText };
    setDraftText("");
    setWritingOwn(false);
    setOwnText("");
    void run(
      "answer",
      () => saveKickoffAnswers(projectId, { ...record.answers, [current.question.id]: text }, record.revision),
      {
        text: value === YOU_DECIDE_ANSWER ? t("projectChat.question.youDecide") : text,
        restore: () => { setDraftText(before.draftText); setWritingOwn(before.writingOwn); setOwnText(before.ownText); },
      },
    );
  };

  const clearAnswer = (questionId: string) => {
    if (record === null) return;
    const next = { ...record.answers };
    delete next[questionId];
    setFreshKey(null);
    void run("answer", () => saveKickoffAnswers(projectId, next, record.revision));
  };

  const sendIdea = async (text: string) => {
    const key = `idea-${Date.now()}`;
    setIdeas((current) => [...current, { key, idea: text, proposal: null, error: null, outcome: null }]);
    setDraftText("");
    setBusy("idea");
    try {
      const proposal = await proposeFromIdea(projectId, text);
      setIdeas((current) => current.map((turn) => (turn.key === key ? { ...turn, proposal } : turn)));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setIdeas((current) => current.map((turn) => (turn.key === key ? { ...turn, error: message } : turn)));
    } finally {
      setBusy(null);
    }
  };

  const place = async (turn: IdeaTurn, position: number | null, kind: "later" | "no" | "place") => {
    if (kind === "later" || kind === "no") {
      setIdeas((current) => current.map((t2) => (
        t2.key === turn.key ? { ...t2, outcome: { kind } } : t2
      )));
      return;
    }
    // A place with nothing to place is not a decline; nothing is recorded either way.
    if (position === null || turn.proposal?.task == null) return;
    setBusy("place");
    setError(null);
    try {
      const placement = await placeIdeaTask(projectId, turn.proposal.task, position);
      setIdeas((current) => current.map((t2) => (
        t2.key === turn.key
          ? { ...t2, outcome: { kind: "placed", note: landingNoteOf(placement), title: placement.title } }
          : t2
      )));
      // A new planned task exists: the rail and the board are out of date.
      onAdopted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 flex-wrap items-center gap-2 border-b px-5 py-3 text-sm">
          <Sparkles className="size-4 text-primary" />
          <span className="font-medium">
            {t(step.kind === "adopted" ? "projectChat.crumb.project" : "projectChat.crumb.settling")}
          </span>
          <span className={cn(
            "ml-auto rounded-full border px-2.5 py-0.5 text-[0.75rem]",
            step.kind === "adopted"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
              : "border-primary/40 bg-primary/10 text-primary",
          )}>
            {t(step.kind === "adopted" ? "projectChat.pill.adopted" : "projectChat.pill.settling")}
          </span>
          {/* Off while a request is out. A refresh takes the newest ticket and usually
              reads the record from *before* the mutation; the mutation's own answer is
              then discarded as stale and nothing reads again, leaving the conversation
              on a step the server has already left. */}
          <Button
            variant="ghost"
            size="sm"
            disabled={busy !== null}
            onClick={() => void load()}
            aria-label={t("workspace.actions.refresh")}
          >
            <RefreshCw className={cn("size-3.5", busy !== null && "animate-spin")} />
          </Button>
        </header>

        {error && (
          <p className="mx-5 mt-3 shrink-0 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-700 dark:text-red-400">
            {error}
          </p>
        )}

        <div ref={streamRef} className="flex min-h-0 min-w-0 flex-1 flex-col gap-3.5 overflow-y-auto px-5 py-4">
          {step.kind === "loading" && <Muted>{t("projectChat.closed.loading")}</Muted>}
          {step.kind === "readFailed" && <Muted tone="warn">{t("projectChat.closed.readFailed")}</Muted>}
          {step.kind === "unreadable" && (
            <Muted tone="warn">{t(`portfolio.kickoff.unreadable.${step.reason}`)}</Muted>
          )}

          {step.kind === "start" && <RecordedGoals key={projectId} projectId={projectId} onAdopted={onAdopted} />}
          {step.kind === "start" && (
            <Bubble who="agent" t={t}>
              <p>{t("projectChat.brief.ask")}</p>
            </Bubble>
          )}

          {record !== null && <RecordStream
            step={step}
            record={record}
            t={t}
            busy={busy}
            writingOwn={writingOwn}
            ownText={ownText}
            onOwnText={setOwnText}
            onWriteOwn={() => { setWritingOwn(true); setOwnText(""); }}
            onCancelOwn={() => setWritingOwn(false)}
            onAnswer={answerCurrent}
            onYouDecide={() => answerCurrent(YOU_DECIDE_ANSWER)}
            onChange={clearAnswer}
            onRefine={() => void run("refine", () => refineKickoff(projectId, record.revision))}
            onAdopt={(charter, tasks) => void run("adopt", async () => {
              const payload = await adoptKickoff(projectId, { charter, tasks, expected_revision: record.revision });
              onAdopted();
              return payload;
            })}
            onRedo={() => void run("start", () => startKickoff(projectId, record.brief, record.revision))}
            onResume={() => void run("adopt", async () => {
              const sealed = record.adopted;
              const payload = await adoptKickoff(projectId, {
                charter: sealed?.charter ?? record.charter_draft,
                tasks: (sealed?.approved ?? []).map(({ title, description, kind }) => ({ title, description, kind })),
                expected_revision: record.revision,
              });
              onAdopted();
              return payload;
            })}
            onOpenTask={onOpenTask}
          />}

          {ideas.map((turn) => (
            <IdeaTurnView key={turn.key} turn={turn} t={t} busy={busy} onPlace={place} />
          ))}

          {sent !== null && (
            <Bubble who="you" t={t}>
              <p className="whitespace-pre-wrap">{sent}</p>
            </Bubble>
          )}

          {busy !== null && busy !== "place" && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              {t(
                busy === "start" ? "projectChat.brief.working"
                  : busy === "answer" ? "projectChat.question.working"
                    : busy === "refine" ? "projectChat.allAnswered.working"
                      : busy === "idea" ? "projectChat.idea.working"
                        : "projectChat.allAnswered.working",
              )}
            </p>
          )}
        </div>

        <Compose
          mode={mode}
          t={t}
          value={draftText}
          onChange={setDraftText}
          busy={busy !== null}
          onSend={(text) => {
            if (mode.kind === "brief") {
              setDraftText("");
              void run(
                "start",
                () => startKickoff(projectId, text, read?.record?.revision ?? null),
                { text, restore: () => setDraftText(text) },
              );
            } else if (mode.kind === "answer") {
              answerCurrent(text);
            } else if (mode.kind === "idea") {
              void sendIdea(text);
            } else if ((mode.kind === "reply" || mode.kind === "askForProposal") && record !== null) {
              // What they said about the draft goes into the pass that draws the next
              // one, with the draft it is about. The box is only offered because the
              // remark reaches the agent; text that went nowhere would be a lie told by
              // a textarea.
              setDraftText("");
              void run(
                "refine",
                () => refineKickoff(projectId, record.revision, text),
                { text, restore: () => setDraftText(text) },
              );
            }
          }}
        />
      </section>

    </div>
  );
}

function Muted({ children, tone = "plain" }: { children: React.ReactNode; tone?: "plain" | "warn" }) {
  return (
    <p className={cn(
      "rounded-lg border px-3 py-2 text-xs leading-relaxed",
      tone === "warn"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
        : "border-dashed text-muted-foreground",
    )}>
      {children}
    </p>
  );
}

function Bubble({
  who, children, t,
}: { who: "agent" | "you" | "agora"; children: React.ReactNode; t: (key: string, vars?: Record<string, string | number>) => string }) {
  const mine = who === "you";
  return (
    <div className={cn(
      "max-w-full break-words rounded-xl border px-3.5 py-3 text-sm leading-relaxed sm:max-w-[62ch]",
      mine ? "self-end border-transparent bg-primary/10" : "bg-muted/40",
    )}>
      <p className="mb-1.5 flex flex-wrap items-baseline gap-2 font-mono text-[0.75rem] text-muted-foreground">
        <span className={cn("font-medium", mine ? "text-primary" : "text-foreground")}>
          {t(`projectChat.who.${who}`)}
        </span>
        {who === "agent" && <span>{t("projectChat.who.agentNote")}</span>}
      </p>
      {children}
    </div>
  );
}

function Chip({
  label, onClick, disabled, tone = "plain", active = false,
}: { label: string; onClick: () => void; disabled?: boolean; tone?: "plain" | "alt"; active?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-full border px-3 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        tone === "alt" ? "border-dashed text-muted-foreground" : "border-border",
        active ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted",
      )}
    >
      {label}
    </button>
  );
}

function RecordStream(props: {
  step: KickoffStep;
  record: KickoffRecordLike;
  t: (key: string, vars?: Record<string, string | number>) => string;
  busy: string | null;
  writingOwn: boolean;
  ownText: string;
  onOwnText: (value: string) => void;
  onWriteOwn: () => void;
  onCancelOwn: () => void;
  onAnswer: (value: string) => void;
  onYouDecide: () => void;
  onChange: (questionId: string) => void;
  onRefine: () => void;
  onAdopt: (charter: KickoffCharterLike, tasks: KickoffTaskLike[]) => void;
  onRedo: () => void;
  onResume: () => void;
  onOpenTask: (taskId: string) => void;
}) {
  const { step, record, t, busy } = props;
  const current = currentQuestionOf(record);
  const asked = current === null ? record.questions.length : current.index + 1;
  const locked = busy !== null || step.kind === "adopting" || step.kind === "adopted";

  return (
    <>
      <Bubble who="agent" t={t}>
        <p>{t("projectChat.read.lead")}</p>
        <div className="mt-2 rounded-lg bg-background/70 px-2.5 py-2 font-mono text-[0.75rem] leading-relaxed text-muted-foreground">
          <div>{record.context.readme ? t("projectChat.read.readme") : t("projectChat.read.readmeNone")}</div>
          <div>{t("projectChat.read.tree", { count: record.context.tree_entries })}</div>
          <div>{t("projectChat.read.git", { count: record.context.git_log_entries })}</div>
          {record.context.unread.length > 0 && (
            <div className="text-amber-600 dark:text-amber-400">
              {t("projectChat.read.unread", { items: record.context.unread.join("、") })}
            </div>
          )}
          {record.read_notes.map((note, index) => <div key={index}>{note}</div>)}
          {!record.context.readme && record.context.tree_entries === 0 && record.context.git_log_entries === 0 && (
            <div>{t("projectChat.read.nothingElse")}</div>
          )}
        </div>
      </Bubble>

      {record.brief.trim().length > 0 && (
        <Bubble who="you" t={t}><p className="whitespace-pre-wrap">{record.brief}</p></Bubble>
      )}

      {record.questions.slice(0, asked).map((question, index) => {
        const answer = (record.answers[question.id] ?? "").trim();
        const isCurrent = current !== null && current.index === index;
        return (
          <div key={question.id} className="contents">
            <Bubble who="agent" t={t}>
              <p>
                <span className="mr-2 font-mono text-[0.75rem] text-primary">
                  {t("projectChat.question.counter", { index: index + 1, total: record.questions.length })}
                </span>
                {question.question}
              </p>
              {question.why && <p className="mt-1 text-xs text-muted-foreground">{question.why}</p>}
              <QuestionChips
                question={question}
                answer={answer}
                disabled={locked || !isCurrent}
                t={t}
                writingOwn={props.writingOwn}
                ownText={props.ownText}
                onOwnText={props.onOwnText}
                onWriteOwn={props.onWriteOwn}
                onCancelOwn={props.onCancelOwn}
                onAnswer={props.onAnswer}
                onYouDecide={props.onYouDecide}
              />
              {isCurrent && !props.writingOwn && (
                <p className="mt-2 text-[0.75rem] text-muted-foreground">{t("projectChat.question.hint")}</p>
              )}
            </Bubble>
            {answer.length > 0 && (
              <>
                <Bubble who="you" t={t}><p className="whitespace-pre-wrap">{answer}</p></Bubble>
                <div className="max-w-full break-words border-l-2 border-primary/60 pl-3 text-xs text-muted-foreground sm:max-w-[62ch]">
                  <p>
                    <span className="font-medium text-foreground">
                      {t("projectChat.recorded.line", { answer })}
                    </span>
                  </p>
                  <p className="mt-1">
                    {answer === YOU_DECIDE_ANSWER ? t("projectChat.recorded.youDecide") : t("projectChat.recorded.note")}
                  </p>
                  {step.kind !== "adopted" && step.kind !== "adopting" && (
                    <button
                      type="button"
                      className="mt-1 text-primary hover:underline disabled:opacity-50"
                      disabled={busy !== null}
                      onClick={() => props.onChange(question.id)}
                    >
                      {t("projectChat.question.change")}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        );
      })}

      {current === null && step.kind !== "proposed" && step.kind !== "adopting" && step.kind !== "adopted" && (
        <Bubble who="agent" t={t}>
          <p>{t("projectChat.allAnswered.lead")}</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <Button size="sm" onClick={props.onRefine} disabled={busy !== null}>
              {t("projectChat.allAnswered.action")}
            </Button>
            <RedoButton t={t} busy={busy !== null} onRedo={props.onRedo} />
          </div>
        </Bubble>
      )}

      {/* What the person said that produced this draft. Shown from the record, so the
          bubble that appeared on send (CR-119) does not vanish when the answer lands. */}
      {step.kind === "proposed" && typeof record.refine_note === "string" && record.refine_note.trim() !== "" && (
        <Bubble who="you" t={t}>
          <p className="whitespace-pre-wrap">{record.refine_note}</p>
        </Bubble>
      )}

      {step.kind === "proposed" && (
        // Keyed on the revision: a redraw leaves `step.kind` at "proposed", so React
        // would reuse this component and keep the previous draft's goal, criteria and
        // tick boxes — and adopting would then write the old charter beside the new
        // tasks. The key remounts it on the record the proposal came with.
        <ProposalCard
          key={step.record.revision}
          step={step}
          t={t}
          busy={busy !== null}
          onAdopt={props.onAdopt}
          onRedo={props.onRedo}
        />
      )}

      {step.kind === "adopting" && (
        <Bubble who="agora" t={t}>
          {/* The two states in which resuming is certain to be refused get the reason
              instead of a button: the store could not be scanned, or a task that is not
              the approved one sits under one of its keys. Offering the resume anyway
              would be offering a click that can only produce a 409. */}
          {step.scanFailed || step.keyConflict ? (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              {t(step.scanFailed ? "portfolio.kickoff.hereScanFailed" : "portfolio.kickoff.hereKeyConflict")}
            </p>
          ) : (
            <>
              <p>{t("projectChat.closed.adopting")}</p>
              <Button className="mt-2.5" size="sm" onClick={props.onResume} disabled={busy !== null}>
                {t("projectChat.closed.resume", { created: step.created, approved: step.approved })}
              </Button>
            </>
          )}
        </Bubble>
      )}

      {step.kind === "adopted" && (
        <Bubble who="agora" t={t}>
          {/* The record travels with the repository; the charter and the tasks do not. A
              clone, or a database that was not restored, gives an *adopted* record this
              control plane never applied — the seal is then the only list of what to
              create here, and saying "adopted, here is your first task" over it would
              point at tasks that do not exist. Each of the three cases is its own
              sentence, and only the complete one opens the conversation. */}
          {step.here !== null && (step.here.scan_failed || step.here.key_conflict) ? (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              {t(step.here.scan_failed ? "portfolio.kickoff.hereScanFailed" : "portfolio.kickoff.hereKeyConflict")}
            </p>
          ) : step.here !== null && !step.here.complete ? (
            <>
              <p className="text-xs text-amber-700 dark:text-amber-400">
                {t("portfolio.kickoff.adoptedNotHere", {
                  charter: t(`portfolio.kickoff.hereCharter.${step.here.charter}`),
                  present: step.here.tasks_present, recorded: step.here.tasks_recorded,
                })}
              </p>
              <Button className="mt-2.5" size="sm" disabled={busy !== null} onClick={props.onResume}>
                {t("portfolio.kickoff.reapplyAdopt")}
              </Button>
            </>
          ) : (
            <>
              <p>
                {step.here === null
                  // No view of this control plane: "the charter is set and the tasks are
                  // here" would be the file's word, not this machine's.
                  ? t("portfolio.kickoff.adoptedUnchecked")
                  : t("projectChat.adopted.done", { count: step.created })}
              </p>
              {step.charterKept && (
                <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-400">{t("projectChat.adopted.charterKept")}</p>
              )}
              {(record.adopted?.tasks.length ?? 0) > 0 && (
                <Button
                  className="mt-2.5"
                  size="sm"
                  onClick={() => props.onOpenTask(record.adopted!.tasks[0].task_id)}
                >
                  {t("projectChat.adopted.openFirst", { title: record.adopted!.tasks[0].title })}
                </Button>
              )}
              <p className="mt-2 text-xs text-muted-foreground">{t("projectChat.adopted.continues")}</p>
            </>
          )}
        </Bubble>
      )}
    </>
  );
}

function QuestionChips({
  question, answer, disabled, t, writingOwn, ownText, onOwnText, onWriteOwn, onCancelOwn, onAnswer, onYouDecide,
}: {
  question: KickoffQuestionLike;
  answer: string;
  disabled: boolean;
  t: (key: string, vars?: Record<string, string | number>) => string;
  writingOwn: boolean;
  ownText: string;
  onOwnText: (value: string) => void;
  onWriteOwn: () => void;
  onCancelOwn: () => void;
  onAnswer: (value: string) => void;
  onYouDecide: () => void;
}) {
  const chips = answerChipsOf(question);
  const label = (chip: AnswerChip) =>
    chip.kind === "own" ? t("projectChat.question.own")
      : chip.kind === "youDecide" ? t("projectChat.question.youDecide")
        : chip.value ?? "";
  return (
    <>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {chips.map((chip) => (
          <Chip
            key={chip.key}
            label={label(chip)}
            tone={chip.kind === "suggestion" ? "plain" : "alt"}
            active={chip.value !== null && chip.value === answer}
            disabled={disabled}
            onClick={() => {
              if (chip.kind === "own") onWriteOwn();
              else if (chip.kind === "youDecide") onYouDecide();
              else if (chip.value !== null) onAnswer(chip.value);
            }}
          />
        ))}
      </div>
      {writingOwn && !disabled && (
        <div className="mt-2 flex items-center gap-2">
          <input
            autoFocus
            value={ownText}
            onChange={(event) => onOwnText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") { event.preventDefault(); onAnswer(ownText); }
              if (event.key === "Escape") onCancelOwn();
            }}
            placeholder={t("projectChat.question.ownPlaceholder")}
            className="min-w-0 flex-1 rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/40"
          />
          <Button size="sm" variant="outline" onClick={() => onAnswer(ownText)} disabled={!ownText.trim()}>
            {t("projectChat.question.ownConfirm")}
          </Button>
        </div>
      )}
    </>
  );
}

function ProposalCard({
  step, t, busy, onAdopt, onRedo,
}: {
  step: Extract<KickoffStep, { kind: "proposed" }>;
  t: (key: string, vars?: Record<string, string | number>) => string;
  busy: boolean;
  onAdopt: (charter: KickoffCharterLike, tasks: KickoffTaskLike[]) => void;
  onRedo: () => void;
}) {
  const { proposal } = step;
  const [goal, setGoal] = useState(proposal.charter.goal);
  const [nonGoals, setNonGoals] = useState(proposal.charter.non_goals.join("\n"));
  const [criteria, setCriteria] = useState(proposal.charter.success_criteria.join("\n"));
  const [ticked, setTicked] = useState<boolean[]>(() => proposal.tasks.map(() => true));

  const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter((line) => line.length > 0);
  const chosen = proposal.tasks.filter((_, index) => ticked[index]);
  const blocked = adoptBlockedBy(goal, lines(criteria));
  const caveats = proposalCaveatsOf(proposal, chosen);

  return (
    <Bubble who="agent" t={t}>
      <p>{t("projectChat.proposal.lead")}</p>
      <div className="mt-3 space-y-3">
        <Field label={t("projectChat.proposal.goal")}>
          <textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            rows={3}
            className="w-full resize-y rounded-md border bg-background px-2.5 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
          />
        </Field>
        <Field label={t("projectChat.proposal.nonGoals")}>
          <textarea
            value={nonGoals}
            onChange={(event) => setNonGoals(event.target.value)}
            rows={3}
            className="w-full resize-y rounded-md border bg-background px-2.5 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
          />
        </Field>
        <Field label={t("projectChat.proposal.criteria")}>
          <textarea
            value={criteria}
            onChange={(event) => setCriteria(event.target.value)}
            rows={3}
            className="w-full resize-y rounded-md border bg-background px-2.5 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
          />
        </Field>
        <Field label={t("projectChat.proposal.tasks")}>
          <ul className="space-y-2">
            {proposal.tasks.map((task, index) => (
              <li key={index} className="flex gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={ticked[index]}
                  onChange={() => setTicked((current) => current.map((v, i) => (i === index ? !v : v)))}
                  className="mt-1"
                />
                <span>
                  <span className={cn(
                    "mr-1.5 rounded px-1.5 py-0.5 font-mono text-[0.6875rem] uppercase",
                    task.kind === MOCKUP_TASK_KIND ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground",
                  )}>
                    {task.kind}
                  </span>
                  <span className="font-medium">{task.title}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{task.description}</span>
                </span>
              </li>
            ))}
          </ul>
        </Field>
      </div>
      {caveats.map((caveat) => (
        <p key={caveat} className="mt-2.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-700 dark:text-amber-400">
          {t(caveat === "noTasks" ? "projectChat.proposal.caveatNoTasks" : "projectChat.proposal.caveatMockupTaskMissing")}
        </p>
      ))}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={busy || blocked !== null}
          onClick={() => onAdopt(
            { goal: goal.trim(), non_goals: lines(nonGoals), success_criteria: lines(criteria) },
            chosen.map(({ title, description, kind }) => ({ title, description, kind })),
          )}
        >
          {t("projectChat.proposal.adopt", { count: chosen.length })}
        </Button>
        <RedoButton t={t} busy={busy} onRedo={onRedo} />
        <span className="text-xs text-muted-foreground">
          {blocked === "goal" ? t("projectChat.proposal.blockedGoal")
            : blocked === "criteria" ? t("projectChat.proposal.blockedCriteria")
              : t("projectChat.proposal.adoptNote")}
        </span>
      </div>
    </Bubble>
  );
}

/**
 * "Ask the questions again", behind a confirmation.
 *
 * Starting a new round writes a fresh questions record over the current one: the answers
 * given so far and the proposal on the table are gone, and they do not go into
 * `previous_adoptions` — only an *adopted* round leaves a receipt. One misclick beside
 * "Adopt" used to be enough. The old panel guarded this by making a restart mean typing
 * the brief again; this asks once instead, which is the same protection and less typing.
 */
function RedoButton({
  t, busy, onRedo,
}: {
  t: (key: string, vars?: Record<string, string | number>) => string;
  busy: boolean;
  onRedo: () => void;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setAsking(true)} disabled={busy}>
        {t("projectChat.proposal.redo")}
      </Button>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted-foreground">{t("projectChat.proposal.redoConfirm")}</span>
      <Button size="sm" variant="outline" onClick={() => { setAsking(false); onRedo(); }} disabled={busy}>
        {t("projectChat.proposal.redoYes")}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setAsking(false)} disabled={busy}>
        {t("projectChat.proposal.redoNo")}
      </Button>
    </span>
  );
}


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-[0.75rem] uppercase tracking-wide text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function IdeaTurnView({
  turn, t, busy, onPlace,
}: {
  turn: IdeaTurn;
  t: (key: string, vars?: Record<string, string | number>) => string;
  busy: string | null;
  onPlace: (turn: IdeaTurn, position: number | null, kind: "later" | "no" | "place") => void;
}) {
  const proposal = turn.proposal;
  return (
    <>
      <Bubble who="you" t={t}><p className="whitespace-pre-wrap">{turn.idea}</p></Bubble>
      {turn.error !== null && <Muted tone="warn">{turn.error}</Muted>}
      {proposal !== null && (
        <Bubble who="agent" t={t}>
          <p className="border-l-2 border-primary/60 pl-2.5 text-sm">
            <span className="font-medium">{t("projectChat.idea.echo", { echo: proposal.echo })}</span>
          </p>
          {proposal.why && <p className="mt-1.5 text-xs text-muted-foreground">{proposal.why}</p>}

          {proposal.verdict === "later" && <p className="mt-2">{t("projectChat.idea.verdictLater")}</p>}
          {proposal.verdict === "note" && <p className="mt-2">{t("projectChat.idea.verdictNote")}</p>}

          {proposal.verdict === "task" && proposal.task !== null && (
            <>
              <p className="mt-2.5">{t("projectChat.idea.cardLead")}</p>
              <div className="mt-1.5 rounded-lg border bg-background/70 px-2.5 py-2 text-sm">
                <span className="mr-1.5 rounded bg-muted px-1.5 py-0.5 font-mono text-[0.6875rem] uppercase text-muted-foreground">
                  {proposal.task.kind}
                </span>
                <span className="font-medium">{proposal.task.title}</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">{proposal.task.description}</span>
              </div>
              {turn.outcome === null ? (
                <>
                  {proposal.position !== null && (
                    <p className="mt-2.5 text-sm">{t("projectChat.idea.positionLead", { position: proposal.position })}</p>
                  )}
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {positionChipsOf(proposal.position, proposal.order.length).map((chip) => (
                      <Chip
                        key={chip.key}
                        tone={chip.kind === "suggested" || chip.kind === "last" ? "plain" : "alt"}
                        disabled={busy !== null}
                        label={
                          chip.kind === "suggested" ? t("projectChat.idea.positionSuggested", { position: chip.position ?? 0 })
                            : chip.kind === "last" ? t("projectChat.idea.positionLast", { position: chip.position ?? 0 })
                              : chip.kind === "later" ? t("projectChat.idea.positionLater")
                                : t("projectChat.idea.positionNo")
                        }
                        onClick={() => onPlace(
                          turn,
                          chip.position,
                          chip.kind === "later" ? "later" : chip.kind === "no" ? "no" : "place",
                        )}
                      />
                    ))}
                  </div>
                  {proposal.order_truncated && (
                    <p className="mt-1.5 text-[0.75rem] text-muted-foreground">{t("projectChat.idea.orderTruncated")}</p>
                  )}
                </>
              ) : (
                <p className="mt-2.5 text-sm">
                  {turn.outcome.kind === "later" ? t("projectChat.idea.declinedLater")
                    : turn.outcome.kind === "no" ? t("projectChat.idea.declinedNo")
                      : turn.outcome.note.kind === "exact" ? t("projectChat.idea.placedExact", { position: turn.outcome.note.position })
                        : turn.outcome.note.kind === "moved" ? t("projectChat.idea.placedMoved", { position: turn.outcome.note.position, asked: turn.outcome.note.asked })
                          : t("projectChat.idea.placedUnknown")}
                </p>
              )}
            </>
          )}
        </Bubble>
      )}
    </>
  );
}

function Compose({
  mode, t, value, onChange, onSend, busy,
}: {
  mode: ReturnType<typeof composeModeOf>;
  t: (key: string, vars?: Record<string, string | number>) => string;
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  busy: boolean;
}) {
  if (mode.kind === "closed") {
    return (
      <div className="shrink-0 border-t bg-muted/30 px-5 py-3 text-xs text-muted-foreground">
        {t(`projectChat.closed.${mode.reason}`)}
      </div>
    );
  }
  const placeholder = mode.kind === "brief" ? t("projectChat.brief.placeholder")
    : mode.kind === "idea" ? t("projectChat.idea.placeholder")
      : mode.kind === "reply" ? t("projectChat.proposal.replyPlaceholder")
        : t("projectChat.question.ownPlaceholder");
  const action = mode.kind === "brief" ? t("projectChat.brief.send")
    : mode.kind === "idea" ? t("projectChat.idea.send")
      : mode.kind === "askForProposal" ? t("projectChat.allAnswered.action")
        : t("projectChat.proposal.redraw");
  return (
    <div className="shrink-0 border-t bg-muted/30 px-5 py-3">
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={2}
        placeholder={placeholder}
        className="w-full resize-y rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
      />
      <div className="mt-2 flex items-center gap-2">
        <span className="text-[0.75rem] text-muted-foreground">{t("projectChat.who.agent")}</span>
        <Button
          className="ml-auto"
          size="sm"
          disabled={busy || !value.trim()}
          onClick={() => onSend(value.trim())}
        >
          {action}
        </Button>
      </div>
    </div>
  );
}

export function DraftPanel({
  step, record, freshKey, t, middle,
}: {
  step: KickoffStep;
  record: KickoffRecordLike | null;
  freshKey: string | null;
  t: (key: string, vars?: Record<string, string | number>) => string;
  /** Rendered between the draft and what it will write — where the design puts the
   *  development order. A slot rather than a second "what this writes" block: that one
   *  already exists here, and two of them is what the page showed for one round. */
  middle?: React.ReactNode;
}) {
  const fields = useMemo(
    () => (record === null ? [] : draftFieldsOf(record, freshKey)),
    [record, freshKey],
  );
  const adopted = step.kind === "adopted";
  const created = adopted ? (record?.adopted?.tasks.length ?? 0) : 0;
  return (
    <>
      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("projectChat.draft.title")}
        </h4>
        <div className="space-y-2.5 rounded-lg border p-3">
          {fields.length === 0 && <p className="text-xs text-muted-foreground">{t("projectChat.draft.blank")}</p>}
          {fields.map((field) => (
            <div key={field.key}>
              <p className="text-[0.75rem] text-muted-foreground">
                {field.key === DRAFT_BRIEF_KEY ? t("projectChat.draft.brief") : field.label}
              </p>
              <p className={cn(
                "text-xs leading-relaxed",
                field.value === null && "italic text-muted-foreground",
                field.fresh && "text-primary",
              )}>
                {field.value ?? t("projectChat.draft.blank")}
              </p>
            </div>
          ))}
        </div>
      </section>

      {middle}

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("projectChat.draft.writes")}
        </h4>
        <div className="space-y-1.5 rounded-lg border p-3 text-xs">
          {adopted ? (
            <>
              <Line label={t("projectChat.draft.writesGoal")} value={t("projectChat.draft.written")} />
              <Line label={t("projectChat.draft.writesTasks")} value={t("projectChat.draft.writesTasksValue", { count: created })} />
              <Line label={t("projectChat.draft.writesRecord")} value=".agora/kickoff.json" />
            </>
          ) : (
            <>
              <p className="text-muted-foreground">{t("projectChat.draft.writesNothing")}</p>
              <Line label={t("projectChat.draft.writesRecord")} value=".agora/kickoff.json" />
            </>
          )}
        </div>
      </section>
    </>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-[0.75rem]">{value}</span>
    </div>
  );
}
