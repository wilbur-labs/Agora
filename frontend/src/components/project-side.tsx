"use client";

/**
 * The workbench's right-hand column (W12, mockup v4): four short cards that answer
 * two questions — what is the goal, and where does it stand — plus what is running
 * now and whether anything waits on the person. Everything longer than a line is
 * behind the left column's rows, which open in the centre.
 *
 * While the goal is still being settled the column shows the kickoff draft instead
 * (the `draft` slot, owned by the conversation that grows it).
 *
 * What it must not do: claim a count it cannot support. The headline carries that
 * verdict (`PlanHeadline.totalKnown`); a run read that failed says so instead of
 * "nothing running"; an attention read that failed says so instead of "nothing".
 */
import { useEffect, useState } from "react";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { foldedWaitingMark, readSideCollapsed, writeSideCollapsed } from "@/lib/side-collapse";
import type { PlanHeadline, PlanRow } from "@/lib/task-plan";

export interface SideRunLike { adapter: string; task_id: string; remote?: { host: string; port: number; user: string } | null }

export interface ProjectSideProps {
  /** `settling`: the draft slot; `adopted`: the four cards. */
  mode: "settling" | "adopted";
  rows: readonly PlanRow[];
  headline: PlanHeadline;
  /** The goal's first lines, or why there is none to show. */
  goal: { kind: "text"; text: string } | { kind: "none" } | { kind: "unreadable" } | { kind: "loading" };
  /** Unsettled runs in this project; `null` when that read failed. */
  activeRuns: readonly SideRunLike[] | null;
  /** Open items waiting on the person; `null` when that read failed. */
  openGates: number | null;
  onSelectView: (view: string) => void;
  /** The kickoff draft, lifted out of the conversation so it survives view changes. */
  draft: (order: React.ReactNode) => React.ReactNode;
  /** Where an adapter runs, for the "now" card; absent when the adapters read has not landed. */
  envOf?: (adapter: string) => string;
}

export function ProjectSide({ mode, rows, headline, goal, activeRuns, openGates, onSelectView, draft, envOf }: ProjectSideProps) {
  const { t, te } = useI18n();
  const done = rows.filter((row) => row.standing === "done").length;
  const running = rows.filter((row) => row.running).length;
  const stepOf = (taskId: string) => rows.find((row) => row.task_id === taskId)?.step ?? null;
  // Folding is for the adopted cards only: while the goal is being settled the column
  // holds the draft of what will be written, which is not something to tuck away.
  const [collapsed, setCollapsed] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- read the stored choice once in the browser
  useEffect(() => { setCollapsed(readSideCollapsed()); }, []);
  const fold = (next: boolean) => { setCollapsed(next); writeSideCollapsed(next); };

  const order = rows.length === 0 ? null : (
    <ol className="grid gap-1 rounded-lg border p-3">
      {rows.map((row) => (
        <li key={row.task_id} className={cn("grid grid-cols-[14px_1.4em_1fr] items-baseline gap-1.5 text-[0.8125rem]",
          row.standing === "done" ? "text-muted-foreground line-through decoration-border" : row.running ? "font-medium text-foreground" : "text-muted-foreground")}>
          <span className={cn("font-mono text-[0.75rem]", row.standing === "done" ? "text-emerald-600 dark:text-emerald-400" : row.running ? "text-primary" : "")}>{row.standing === "done" ? "✓" : row.running ? "▶" : "○"}</span>
          <span className="font-mono text-[0.75rem] text-muted-foreground">{row.step}</span>
          <span className="min-w-0 truncate">{row.title}</span>
        </li>
      ))}
    </ol>
  );

  const heading = (label: string, action?: { label: string; view: string }) => (
    <h5 className="mb-1.5 flex items-baseline justify-between text-[0.75rem] font-semibold uppercase tracking-wide text-muted-foreground">
      <span>{label}</span>
      {action && <button type="button" onClick={() => onSelectView(action.view)} className="text-[0.75rem] font-medium normal-case tracking-normal text-primary hover:underline">{action.label}</button>}
    </h5>
  );

  if (mode === "adopted" && collapsed) {
    const waitingMark = foldedWaitingMark(openGates);
    const runningNow = activeRuns === null ? "?" : activeRuns.length > 0 ? String(activeRuns.length) : null;
    return (
      <aside className="hidden h-full min-h-0 w-11 shrink-0 flex-col items-center gap-3 border-l py-4 lg:flex">
        <button type="button" onClick={() => fold(false)} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={t("workspace.side.expand")} title={t("workspace.side.expand")}><PanelRightOpen className="size-4" /></button>
        {waitingMark !== null && (
          <button type="button" onClick={() => fold(false)} title={openGates === null ? `${t("workspace.side.waiting")}: ${t("workspace.side.waitingUnknown")}` : t("workspace.side.waitingCount", { count: openGates })}
            className={cn("rounded-full border px-1.5 py-0.5 font-mono text-[0.6875rem]", openGates === null ? "text-muted-foreground" : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400")}>{waitingMark}</button>
        )}
        {runningNow !== null && (
          <button type="button" onClick={() => fold(false)} title={activeRuns === null ? t("workspace.side.nowUnknown") : t("workspace.side.foldedRunning", { count: activeRuns.length })}
            className="font-mono text-[0.6875rem] text-primary">▶{runningNow}</button>
        )}
      </aside>
    );
  }

  return (
    <aside className="hidden h-full min-h-0 w-80 min-w-0 shrink-0 flex-col gap-4 overflow-y-auto border-l px-4 py-4 lg:flex">
      {mode === "settling" ? draft(order) : (
        <>
          <div className="-mb-2 flex justify-end">
            <button type="button" onClick={() => fold(true)} className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={t("workspace.side.collapse")} title={t("workspace.side.collapse")}><PanelRightClose className="size-4" /></button>
          </div>
          <section className="min-w-0">
            {heading(t("workspace.side.goal"), { label: t("workspace.side.goalFull"), view: "goal" })}
            <div className="rounded-lg border p-3 text-[0.8125rem] leading-relaxed">
              {goal.kind === "text" && <p className="line-clamp-4 whitespace-pre-wrap">{goal.text}</p>}
              {goal.kind === "none" && <p className="text-muted-foreground">{t("workspace.side.goalNone")}</p>}
              {goal.kind === "unreadable" && <p className="text-muted-foreground">{t("workspace.side.goalUnreadable")}</p>}
              {goal.kind === "loading" && <p className="text-muted-foreground">—</p>}
            </div>
          </section>

          <section className="min-w-0">
            {heading(t("workspace.side.progress"), { label: t("workspace.side.progressDetail"), view: "order" })}
            <div className="rounded-lg border p-3">
              <div className="flex items-baseline justify-between text-[0.8125rem]">
                <span>{t(headline.totalKnown ? "workspace.side.progressLine" : "workspace.side.progressLineNotWhole", { total: headline.total, done })}{running > 0 ? t("workspace.side.progressRunning", { running }) : ""}</span>
                {headline.totalKnown && headline.total > 0 && <span className="font-mono text-[0.75rem] text-muted-foreground">{Math.round((done / headline.total) * 100)}%</span>}
              </div>
              {headline.totalKnown && headline.total > 0 && (
                <div className="my-2 flex h-1.5 overflow-hidden rounded-full bg-muted">
                  <i className="block h-full bg-emerald-500" style={{ width: `${(done / headline.total) * 100}%` }} />
                  <i className="block h-full bg-primary/50" style={{ width: `${(running / headline.total) * 100}%` }} />
                </div>
              )}
              {order ?? <p className="text-[0.8125rem] text-muted-foreground">{t("workspace.detail.order.none")}</p>}
            </div>
          </section>

          <section className="min-w-0">
            {heading(t("workspace.side.now"))}
            <div className="rounded-lg border p-3 text-[0.8125rem]">
              {activeRuns === null && <p className="text-muted-foreground">{t("workspace.side.nowUnknown")}</p>}
              {activeRuns !== null && activeRuns.length === 0 && <p className="text-muted-foreground">{t("workspace.side.nowNone")}</p>}
              {activeRuns !== null && activeRuns.map((run, index) => (
                <div key={`${run.task_id}-${index}`} className="grid gap-0.5">
                  <div className="flex justify-between gap-2 border-b border-dashed py-0.5"><span>{te("adapter", run.adapter)}</span><span className="font-mono text-[0.75rem] text-muted-foreground">{stepOf(run.task_id) === null ? t("workspace.stream.run.stepUnknown") : t("workspace.stream.run.step", { step: stepOf(run.task_id)! })}</span></div>
                  <div className="flex justify-between gap-2 border-b border-dashed py-0.5"><span>{t("workspace.side.nowEnv")}</span><span className="min-w-0 truncate font-mono text-[0.75rem] text-muted-foreground">{run.remote ? `${run.remote.user}@${run.remote.host}${run.remote.port !== 22 ? `:${run.remote.port}` : ""}` : envOf ? envOf(run.adapter) : "—"}</span></div>
                  <div className="flex justify-between gap-2 py-0.5"><span>{t("workspace.side.nowAfter")}</span><span className="font-mono text-[0.75rem] text-muted-foreground">{t("workspace.side.nowAfterAuto")}</span></div>
                </div>
              ))}
            </div>
          </section>

          <section className="min-w-0">
            {heading(t("workspace.side.waiting"))}
            <div className="rounded-lg border p-3 text-[0.8125rem]">
              {openGates === null && <p className="text-muted-foreground">{t("workspace.side.waitingUnknown")}</p>}
              {openGates === 0 && <p className="text-muted-foreground">{t("workspace.side.waitingNone")}</p>}
              {openGates !== null && openGates > 0 && <p><span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 font-mono text-[0.6875rem] text-amber-700 dark:text-amber-400">{openGates}</span> <button type="button" onClick={() => onSelectView("chat")} className="text-primary hover:underline">{t("workspace.side.waitingCount", { count: openGates })}</button></p>}
            </div>
          </section>
        </>
      )}
    </aside>
  );
}
