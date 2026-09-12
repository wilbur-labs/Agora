"use client";

/**
 * Project kickoff (W10): the agent reads the project once, asks what only the owner
 * can answer, and — after the answers — proposes the charter and the first tasks. The
 * owner edits and adopts; nothing is written to the charter or the board before that.
 *
 * Which step this panel is on is decided by lib/kickoff.ts from the backend's read of
 * `.agora/kickoff.json`; the three non-steps (read in flight, read failed, file there
 * but not a record) are their own sentences and never "start here". The flow itself is
 * one round: ask → answer → propose → adopt. "重新提问" starts the round again.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import {
  adoptKickoff,
  getKickoff,
  refineKickoff,
  saveKickoffAnswers,
  startKickoff,
  type KickoffPayload,
} from "@/lib/control-plane";
import {
  MOCKUP_TASK_KIND,
  adoptBlockedBy,
  kickoffStepOf,
  proposalCaveatsOf,
  type KickoffCharterLike,
  type KickoffRecordLike,
  type KickoffTaskLike,
} from "@/lib/kickoff";
import { cn } from "@/lib/utils";

export function KickoffPanel({ projectId, onAdopted }: { projectId: string; onAdopted: () => void }) {
  const { t } = useI18n();
  const [read, setRead] = useState<KickoffPayload | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [busy, setBusy] = useState<"start" | "answers" | "refine" | "adopt" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [brief, setBrief] = useState("");
  // Every request that may set `read` takes a ticket; only the newest ticket may apply its
  // answer. Without it a re-read started by a failed adopt could land *after* the resume
  // that succeeded and put the panel back on "adopting", offering the resume again.
  const ticket = useRef(0);

  const load = useCallback((signal?: AbortSignal): Promise<KickoffPayload | null> => {
    const mine = ++ticket.current;
    return getKickoff(projectId, signal)
      .then((payload) => { if (!signal?.aborted && mine === ticket.current) { setRead(payload); setReadFailed(false); } return payload; })
      .catch(() => { if (!signal?.aborted && mine === ticket.current) { setRead(null); setReadFailed(true); } return null; });
  }, [projectId]);

  useEffect(() => {
    const ctrl = new AbortController();
    const timeout = window.setTimeout(() => void load(ctrl.signal), 0);
    return () => { window.clearTimeout(timeout); ctrl.abort(); };
  }, [load]);

  const step = kickoffStepOf(read, readFailed);

  // Resumes (or re-applies, on a control plane that lacks what the record says) from what
  // was sealed at the first adopt; the server ignores the body past the schema, so what is
  // sent is the sealed payload itself.
  const adoptFromSeal = (record: KickoffRecordLike) => void run("adopt", async () => {
    const sealed = record.adopted;
    const payload = await adoptKickoff(projectId, {
      charter: sealed?.charter ?? record.charter_draft,
      tasks: (sealed?.approved ?? []).map(({ title, description, kind }) => ({ title, description, kind })),
      expected_revision: record.revision,
    });
    onAdopted();
    return payload;
  });

  const run = async (what: NonNullable<typeof busy>, action: () => Promise<KickoffPayload>) => {
    setBusy(what);
    setError(null);
    const mine = ++ticket.current;
    const revisionBefore = read?.record?.revision ?? null;
    try {
      const payload = await action();
      if (mine === ticket.current) {
        setRead(payload);
        setReadFailed(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      // The record may have moved without this tab hearing it — an adoption whose
      // answer was lost, a round replaced from another tab, a conflict the server named.
      // Re-read, so the panel shows the record as it is and not the step it was on. And
      // after a failed *adopt* the charter and the tasks above and below may have been
      // written all the same, so the page re-reads them too. The re-read is awaited:
      // the forms stay locked until the record that will remount them has landed, so
      // nothing typed in between is taken and then dropped.
      const after = await load();
      // A request whose answer was lost may have gone through: if the re-read shows the
      // record moved, the failure banner would sit over a state that contradicts it.
      // Withdrawn then; kept when nothing moved, since then the failure is the news.
      if (after !== null && (after.record?.revision ?? null) !== revisionBefore) setError(null);
      if (what === "adopt") onAdopted();
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border bg-muted/20 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-4 text-primary" />
        <h3 className="text-sm font-semibold">{t("portfolio.kickoff.title")}</h3>
        <span className="text-xs text-muted-foreground">{t(`portfolio.kickoff.step.${step.kind}`)}</span>
      </header>

      {error && <p className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-700 dark:text-red-400">{error}</p>}

      {step.kind === "loading" && <p className="text-xs text-muted-foreground">…</p>}
      {step.kind === "readFailed" && (
        <p className="text-xs text-muted-foreground">
          {t("portfolio.kickoff.readFailed")}{" "}
          <button className="text-primary hover:underline" onClick={() => load()}>{t("common.action.retry")}</button>
        </p>
      )}
      {step.kind === "unreadable" && (
        <p className="text-xs text-muted-foreground">{t(`portfolio.kickoff.unreadable.${step.reason}`)}</p>
      )}

      {step.kind === "start" && (
        <StartBlock
          brief={brief}
          onBrief={setBrief}
          busy={busy === "start"}
          onStart={() => void run("start", () => startKickoff(projectId, brief, null))}
          t={t}
        />
      )}

      {(step.kind === "questions" || step.kind === "answered") && (
        <>
          <ReadNotes record={step.record} t={t} />
          <DraftCharter charter={step.record.charter_draft} t={t} />
          <QuestionsBlock
            key={step.record.revision}
            record={step.record}
            busy={busy}
            onSave={(answers) => void run("answers", () => saveKickoffAnswers(projectId, answers, step.record.revision))}
            onRefine={() => void run("refine", () => refineKickoff(projectId, step.record.revision))}
            canRefine={step.answered > 0}
            t={t}
          />
          <RestartBlock key={step.record.revision} record={step.record} busy={busy === "start"} locked={busy !== null} onRestart={(b) => void run("start", () => startKickoff(projectId, b, step.record.revision))} t={t} />
        </>
      )}

      {step.kind === "proposed" && (
        <>
          <ReadNotes record={step.record} t={t} />
          <details className="rounded-lg border bg-background/60">
            <summary className="cursor-pointer px-3 py-2 text-xs font-medium">{t("portfolio.kickoff.answersReview")}</summary>
            <div className="border-t p-3">
              <QuestionsBlock
                key={step.record.revision}
                record={step.record}
                busy={busy}
                onSave={(answers) => void run("answers", () => saveKickoffAnswers(projectId, answers, step.record.revision))}
                onRefine={() => void run("refine", () => refineKickoff(projectId, step.record.revision))}
                canRefine
                t={t}
              />
            </div>
          </details>
          <ProposalBlock
            key={step.record.revision}
            record={step.record}
            busy={busy === "adopt"}
            locked={busy !== null}
            onAdopt={(charter, tasks) => void run("adopt", async () => {
              const payload = await adoptKickoff(projectId, { charter, tasks, expected_revision: step.record.revision });
              onAdopted();
              return payload;
            })}
            t={t}
          />
        </>
      )}

      {step.kind === "adopting" && (
        <div className="space-y-2">
          {step.scanFailed || step.keyConflict ? (
            // The counts would be placeholders and the resume refused: said, nothing offered.
            <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
              {t(step.scanFailed ? "portfolio.kickoff.hereScanFailed" : "portfolio.kickoff.hereKeyConflict")}
            </p>
          ) : (
            <>
              <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                {t("portfolio.kickoff.adoptingNote", { created: step.created, approved: step.approved })}
              </p>
              <Button size="sm" disabled={busy !== null} onClick={() => adoptFromSeal(step.record)}>
                {busy === "adopt" ? <RefreshCw className="animate-spin" /> : null} {t("portfolio.kickoff.resumeAdopt")}
              </Button>
            </>
          )}
        </div>
      )}

      {step.kind === "adopted" && (
        <div className="space-y-2 text-sm">
          {step.here !== null && (step.here.scan_failed || step.here.key_conflict) ? (
            // The store could not be scanned, or a stranger sits under one of the keys:
            // the counts would mislead and the re-apply would be refused. Said as what it
            // is, and nothing offered.
            <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-300">
              {t(step.here.scan_failed ? "portfolio.kickoff.hereScanFailed" : "portfolio.kickoff.hereKeyConflict")}
            </p>
          ) : step.here !== null && !step.here.complete ? (
            <>
              {/* The file says adopted; this control plane does not hold it (a clone, a
                  database not restored). Said with the counts, and re-applied on request. */}
              <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-300">
                {t("portfolio.kickoff.adoptedNotHere", {
                  charter: t(`portfolio.kickoff.hereCharter.${step.here.charter}`),
                  present: step.here.tasks_present, recorded: step.here.tasks_recorded,
                })}
              </p>
              <Button size="sm" disabled={busy !== null} onClick={() => adoptFromSeal(step.record)}>
                {busy === "adopt" ? <RefreshCw className="animate-spin" /> : null} {t("portfolio.kickoff.reapplyAdopt")}
              </Button>
            </>
          ) : (
            // Without the view, "the charter is set and the tasks exist" would be the file's
            // word, not this control plane's — said as unchecked instead.
            <p>{t(step.here === null ? "portfolio.kickoff.adoptedUnchecked" : step.charterKept ? "portfolio.kickoff.adoptedSummaryCharterKept" : "portfolio.kickoff.adoptedSummary", { count: step.created })}</p>
          )}
          <div className="flex flex-wrap gap-2">
            <Link href={`/workspace?project=${encodeURIComponent(projectId)}`} className="text-xs text-primary hover:underline">
              {t("portfolio.kickoff.openWorkbench")}
            </Link>
          </div>
          {/* No starting over while this control plane has not applied the seal: the seal
              is the only list of what to create here, and the server refuses it too. */}
          {(step.here === null || step.here.complete) && (
            <RestartBlock key={step.record.revision} record={step.record} busy={busy === "start"} locked={busy !== null} onRestart={(b) => void run("start", () => startKickoff(projectId, b, step.record.revision))} t={t} />
          )}
        </div>
      )}
    </section>
  );
}

type T = (key: string, vars?: Record<string, string | number>) => string;

/** Remount key for the answers block: the questions asked and the answers on record. */
// Every form below is keyed by the record's revision, the same way the restart block
// is: text typed against one record must not be saved against another. Keying by the
// question ids and the saved answers was not enough — another tab can start a round whose
// questions carry the same ids with different meanings, and a proposal can be repeated
// word for word — and the revision is the one thing that moves whenever the record does.

function StartBlock({ brief, onBrief, busy, onStart, t }: { brief: string; onBrief: (v: string) => void; busy: boolean; onStart: () => void; t: T }) {
  return (
    <div className="space-y-2">
      <p className="text-xs leading-relaxed text-muted-foreground">{t("portfolio.kickoff.startIntro")}</p>
      <textarea
        className="field min-h-24 w-full resize-y"
        value={brief}
        onChange={(event) => onBrief(event.target.value)}
        disabled={busy}
        placeholder={t("portfolio.kickoff.briefPlaceholder")}
      />
      <div className="flex items-center gap-3">
        {/* A brief is asked for always, not only for an empty repository: the questions
            are better for it, and the server refuses a blank one where there is nothing
            else to read. */}
        <Button size="sm" onClick={onStart} disabled={busy}>
          {busy ? <RefreshCw className="animate-spin" /> : <Sparkles />} {t("portfolio.kickoff.start")}
        </Button>
        {busy && <span className="text-xs text-muted-foreground">{t("portfolio.kickoff.running")}</span>}
      </div>
    </div>
  );
}

// Keyed by the record's revision where it is used: the brief typed here belongs to the
// record it was typed against. When the record moves — another tab started a round, an
// answer landed, a 409 made this tab re-read — the block remounts and the old brief is
// dropped rather than being sent with the new revision, which the server would accept.
function RestartBlock({ record, busy, locked, onRestart, t }: { record: KickoffRecordLike; busy: boolean; locked: boolean; onRestart: (brief: string) => void; t: T }) {
  const [open, setOpen] = useState(false);
  const [brief, setBrief] = useState(record.brief);
  return (
    <div className="text-xs">
      <button className="text-muted-foreground hover:text-foreground hover:underline" onClick={() => setOpen((v) => !v)}>
        {t("portfolio.kickoff.restart")}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <p className="text-muted-foreground">{t("portfolio.kickoff.restartNote")}</p>
          {/* Locked while ANY request is out, not only a restart: a save or a second pass
              moves the record and this block remounts on the new revision. And the brief
              may be empty — a repository with a README, a tree or a history is enough for
              the server, which refuses only an empty brief over an empty repository. */}
          <textarea className="field min-h-20 w-full resize-y" value={brief} onChange={(event) => setBrief(event.target.value)} disabled={locked} />
          <Button size="sm" variant="outline" onClick={() => onRestart(brief)} disabled={locked}>
            {busy ? <RefreshCw className="animate-spin" /> : null} {t("portfolio.kickoff.restartConfirm")}
          </Button>
        </div>
      )}
    </div>
  );
}

/** What the agent read, and what it could not — beside its conclusions, never behind them. */
function ReadNotes({ record, t }: { record: KickoffRecordLike; t: T }) {
  const unread = record.context.unread;
  if (record.read_notes.length === 0 && unread.length === 0) return null;
  return (
    <div className="rounded-lg border border-dashed px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
      {record.read_notes.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4">
          {record.read_notes.map((note, i) => <li key={i}>{note}</li>)}
        </ul>
      )}
      {unread.length > 0 && (
        <p className={cn(record.read_notes.length > 0 && "mt-1")}>
          {t("portfolio.kickoff.unread", { items: unread.join("；") })}
        </p>
      )}
    </div>
  );
}

function DraftCharter({ charter, t }: { charter: KickoffCharterLike; t: T }) {
  return (
    <div className="rounded-lg border bg-background/60 p-3 text-xs">
      <p className="font-medium text-muted-foreground">{t("portfolio.kickoff.draftTitle")}</p>
      <p className="mt-1 text-sm font-semibold leading-snug">{charter.goal}</p>
      <CharterLists charter={charter} t={t} />
    </div>
  );
}

function CharterLists({ charter, t }: { charter: KickoffCharterLike; t: T }) {
  return (
    <div className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
      {charter.success_criteria.length > 0 && (
        <div>
          <p className="font-medium text-muted-foreground">{t("portfolio.cockpit.charter.successCriteria")}</p>
          <ul className="mt-0.5 list-disc pl-4">{charter.success_criteria.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </div>
      )}
      {charter.non_goals.length > 0 && (
        <div>
          <p className="font-medium text-muted-foreground">{t("portfolio.cockpit.charter.nonGoals")}</p>
          <ul className="mt-0.5 list-disc pl-4 text-muted-foreground">{charter.non_goals.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function QuestionsBlock({
  record, busy, onSave, onRefine, canRefine, t,
}: {
  record: KickoffRecordLike;
  busy: string | null;
  onSave: (answers: Record<string, string>) => void;
  onRefine: () => void;
  canRefine: boolean;
  t: T;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({ ...record.answers });
  // Seeded once per mount: the parent remounts this block (by key) whenever the
  // record's answers change, so the seed is never stale and no effect writes state.
  const dirty = useMemo(
    () => record.questions.some((q) => (answers[q.id] ?? "") !== (record.answers[q.id] ?? "")),
    [answers, record],
  );
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">{t("portfolio.kickoff.questionsIntro")}</p>
      {record.questions.map((question, index) => (
        <div key={question.id} className="rounded-lg border bg-background/60 p-3">
          <p className="text-sm font-medium">{index + 1}. {question.question}</p>
          {question.why && <p className="mt-0.5 text-[11px] text-muted-foreground">{question.why}</p>}
          {question.suggestions.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {question.suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  disabled={busy !== null}
                  onClick={() => setAnswers((prev) => ({ ...prev, [question.id]: suggestion }))}
                  className="rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-primary hover:text-primary"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          )}
          {/* Read-only while a request is out: the answer lands as a new revision and this
              block remounts on it, so anything typed meanwhile would be dropped without
              a word. Better not to take it than to lose it. */}
          <textarea
            className="field mt-2 min-h-16 w-full resize-y"
            value={answers[question.id] ?? ""}
            onChange={(event) => setAnswers((prev) => ({ ...prev, [question.id]: event.target.value }))}
            placeholder={t("portfolio.kickoff.answerPlaceholder")}
            disabled={busy !== null}
          />
        </div>
      ))}
      {record.needs_mockup && record.mockup_note && (
        <p className="text-[11px] text-muted-foreground">{t("portfolio.kickoff.mockupNote", { note: record.mockup_note })}</p>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button size="sm" variant="outline" onClick={() => onSave(answers)} disabled={busy !== null || !dirty}>
          {busy === "answers" ? <RefreshCw className="animate-spin" /> : null} {t("portfolio.kickoff.saveAnswers")}
        </Button>
        {/* Refine reads the saved answers, so unsaved edits are saved first by the person,
            not silently by this button — a proposal built on answers the record does not
            hold would be a proposal nobody can trace. */}
        <Button size="sm" onClick={onRefine} disabled={busy !== null || !canRefine || dirty} title={dirty ? t("portfolio.kickoff.saveFirst") : undefined}>
          {busy === "refine" ? <RefreshCw className="animate-spin" /> : <Sparkles />} {t("portfolio.kickoff.refine")}
        </Button>
        {busy === "refine" && <span className="text-xs text-muted-foreground">{t("portfolio.kickoff.running")}</span>}
      </div>
    </div>
  );
}

function ProposalBlock({
  record, busy, locked, onAdopt, t,
}: {
  record: KickoffRecordLike;
  /** This block's own request is out (shows the spinner). */
  busy: boolean;
  /** Any request that may move the record is out — a refine from the answers above
   *  included: the answer lands as a new revision and this block remounts on it, so
   *  nothing typed meanwhile may be accepted. */
  locked: boolean;
  onAdopt: (charter: KickoffCharterLike, tasks: KickoffTaskLike[]) => void;
  t: T;
}) {
  const proposal = record.proposal;
  const [goal, setGoal] = useState(proposal?.charter.goal ?? "");
  const [nonGoals, setNonGoals] = useState((proposal?.charter.non_goals ?? []).join("\n"));
  const [success, setSuccess] = useState((proposal?.charter.success_criteria ?? []).join("\n"));
  const [picked, setPicked] = useState<boolean[]>(() => (proposal?.tasks ?? []).map(() => true));
  // Seeded once per mount; the parent remounts this block (by key) per proposal.
  if (proposal === null) return null;
  const toLines = (value: string) => value.split("\n").map((line) => line.trim()).filter(Boolean);
  const chosen = proposal.tasks.filter((_, i) => picked[i]);
  // Judged on what is ticked: unticking the mockup is exactly what this warns about.
  const caveats = proposalCaveatsOf(proposal, chosen);
  const blocked = adoptBlockedBy(goal, toLines(success));

  return (
    <div className="space-y-3 rounded-lg border bg-background/60 p-3">
      <p className="text-xs font-medium text-muted-foreground">{t("portfolio.kickoff.proposalTitle")}</p>
      <label className="block text-xs">
        <span className="font-medium text-muted-foreground">{t("portfolio.cockpit.charter.goal")}</span>
        <textarea className="field mt-1 min-h-16 w-full resize-y" value={goal} onChange={(e) => setGoal(e.target.value)} disabled={locked} />
      </label>
      <div className="grid gap-3 sm:grid-cols-2 text-xs">
        <label className="block">
          <span className="font-medium text-muted-foreground">{t("portfolio.cockpit.charter.successCriteria")}</span>
          <textarea className="field mt-1 min-h-24 w-full resize-y" value={success} onChange={(e) => setSuccess(e.target.value)} disabled={locked} />
        </label>
        <label className="block">
          <span className="font-medium text-muted-foreground">{t("portfolio.cockpit.charter.nonGoals")}</span>
          <textarea className="field mt-1 min-h-24 w-full resize-y" value={nonGoals} onChange={(e) => setNonGoals(e.target.value)} disabled={locked} />
        </label>
      </div>

      <p className="text-xs font-medium text-muted-foreground">{t("portfolio.kickoff.tasksTitle", { count: proposal.tasks.length })}</p>
      <ul className="space-y-1.5">
        {proposal.tasks.map((task, i) => (
          <li key={`${i}-${task.title}`} className="flex items-start gap-2 rounded border bg-card px-2 py-1.5 text-xs">
            <input type="checkbox" className="mt-0.5" checked={picked[i] ?? true} disabled={locked} onChange={(e) => setPicked((prev) => prev.map((v, j) => (j === i ? e.target.checked : v)))} />
            <span className="min-w-0 flex-1">
              <span className="font-medium">{task.title}</span>
              <span className={cn("ml-2 rounded px-1.5 py-0.5 text-[10px] uppercase", task.kind === MOCKUP_TASK_KIND ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>{task.kind}</span>
              {task.description && <span className="mt-0.5 block text-muted-foreground">{task.description}</span>}
            </span>
          </li>
        ))}
      </ul>
      {caveats.map((caveat) => (
        <p key={caveat} className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-300">
          {t(`portfolio.kickoff.caveat.${caveat}`)}
        </p>
      ))}
      <div className="flex items-center justify-end gap-3">
        <span className="text-[11px] text-muted-foreground">
          {blocked ? t(`portfolio.kickoff.adoptBlocked.${blocked}`) : t("portfolio.kickoff.adoptNote")}
        </span>
        <Button size="sm" onClick={() => onAdopt({ goal: goal.trim(), non_goals: toLines(nonGoals), success_criteria: toLines(success) }, chosen)} disabled={locked || blocked !== null}>
          {busy ? <RefreshCw className="animate-spin" /> : null} {t("portfolio.kickoff.adopt", { count: chosen.length })}
        </Button>
      </div>
    </div>
  );
}
