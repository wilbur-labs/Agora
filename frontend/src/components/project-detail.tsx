"use client";

/**
 * A left-column row, opened in the centre (W12): the project's details one view at a
 * time, with the way back at the top. Nothing here navigates; the URL does not move.
 *
 * Every view says what it could not read rather than showing an empty table as if it
 * were the answer: a decisions read that failed is "could not be read (reason)", a CR
 * log the server did not report is "could not be read", a project with no defect
 * source says so.
 */
import { Button } from "@/components/ui/button";
import { relTime } from "@/components/timeline-entry";
import { crLogViewOf } from "@/lib/cr-log";
import type { ProjectDashboard, ProjectDecisions, ProjectDefects, TaskManifest } from "@/lib/control-plane";
import type { RunSummary } from "@/lib/execution";
import type { PlanHeadline, PlanRow } from "@/lib/task-plan";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type ProjectView = "chat" | "goal" | "order" | "runs" | "decisions" | "changeRequests" | "defects" | "files";

export function ProjectDetail({
  view, dashboard, dashboardFailed, decisions, decisionsFailed, defects, defectsFailed, tasks, rows, headline, runs, runsFailed, runsTruncated, onBack,
}: {
  view: Exclude<ProjectView, "chat">;
  dashboard: ProjectDashboard | null;
  dashboardFailed: boolean;
  decisions: ProjectDecisions | null;
  /** The decisions read itself failed — told apart from "not landed yet". */
  decisionsFailed: boolean;
  defects: ProjectDefects | null;
  /** The defects read itself failed — told apart from "not landed yet". */
  defectsFailed: boolean;
  tasks: readonly TaskManifest[];
  rows: readonly PlanRow[];
  headline: PlanHeadline;
  runs: readonly RunSummary[] | null;
  runsFailed: boolean;
  /** The runs page was full, so what is shown is the latest N, not the history. */
  runsTruncated: boolean;
  onBack: () => void;
}) {
  const { t, te } = useI18n();
  const stepOf = (taskId: string) => rows.find((row) => row.task_id === taskId)?.step ?? null;

  let body: React.ReactNode;
  if (view === "goal") {
    const charter = dashboard?.charter ?? null;
    body = (
      <>
        {dashboardFailed && <p className="text-[13px] text-muted-foreground">{t("workspace.detail.goal.unreadable")}</p>}
        {!dashboardFailed && charter && (
          <dl className="grid grid-cols-[8em_1fr] gap-x-4 gap-y-2 text-[13px]">
            <dt className="text-muted-foreground">{t("workspace.detail.goal.goal")}</dt>
            <dd className="whitespace-pre-wrap">{charter.goal.trim() === "" ? t("workspace.detail.goal.none") : charter.goal}</dd>
            <dt className="text-muted-foreground">{t("workspace.detail.goal.nonGoals")}</dt>
            <dd>{charter.non_goals.length === 0 ? "—" : <ul className="list-disc pl-4">{charter.non_goals.map((line) => <li key={line}>{line}</li>)}</ul>}</dd>
            <dt className="text-muted-foreground">{t("workspace.detail.goal.criteria")}</dt>
            <dd>{charter.success_criteria.length === 0 ? "—" : <ul className="list-disc pl-4">{charter.success_criteria.map((line) => <li key={line}>{line}</li>)}</ul>}</dd>
          </dl>
        )}
        {!dashboardFailed && charter === null && <p className="text-[13px] text-muted-foreground">{t("workspace.stream.loading")}</p>}
        <h3 className="mt-6 text-[14px] font-semibold">{t("workspace.detail.goal.tasks")}</h3>
        <div className="mt-2 space-y-2">
          {rows.map((row) => {
            const task = tasks.find((candidate) => candidate.task_id === row.task_id);
            return (
              <div key={row.task_id} className="rounded-lg border bg-muted/30 px-3 py-2 text-[13px]">
                <p><span className="mr-2 font-mono text-[11px] text-muted-foreground">{row.step}</span><b className="font-medium">{row.title}</b> <span className="ml-1 rounded-full border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{te("taskState", row.state)}</span></p>
                {task?.description && <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{task.description}</p>}
                <p className="mt-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("workspace.detail.goal.acceptance")}</p>
                {task && task.acceptance.length > 0
                  ? <ul className="list-disc pl-4 text-[12px]">{task.acceptance.map((line) => <li key={line}>{line}</li>)}</ul>
                  : <p className="text-[12px] text-muted-foreground">{t("workspace.detail.goal.noAcceptance")}</p>}
              </div>
            );
          })}
        </div>
      </>
    );
  } else if (view === "order") {
    body = rows.length === 0 ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.order.none")}</p> : (
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead><tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground"><th className="px-2 py-1.5">{t("workspace.detail.order.step")}</th><th className="px-2 py-1.5">{t("workspace.detail.order.task")}</th><th className="px-2 py-1.5">{t("workspace.detail.order.state")}</th><th className="px-2 py-1.5">{t("workspace.detail.order.waiting")}</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.task_id} className="border-t">
                <td className="px-2 py-2 font-mono text-[11px] text-muted-foreground">{row.step}</td>
                <td className="px-2 py-2">{row.title}</td>
                <td className="px-2 py-2"><span className="rounded-full border px-2 py-0.5 font-mono text-[10px]">{te("taskState", row.state)}</span>{row.running && <span className="ml-1 text-[11px] text-primary">{t("workspace.plan.running")}</span>}</td>
                <td className="px-2 py-2 text-[12px] text-muted-foreground">{row.waiting.kind === "open" ? t("workspace.side.needsYou") : row.waiting.kind === "none" ? "—" : t("workspace.panels.unavailable")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!headline.totalKnown && <p className="mt-2 text-[11px] text-muted-foreground">{t("workspace.side.orderNotWhole", { count: headline.total })}</p>}
      </div>
    );
  } else if (view === "runs") {
    body = runsFailed ? <p className="text-[13px] text-muted-foreground">{t("workspace.stream.runsUnreadable")}</p>
      : runs === null ? <p className="text-[13px] text-muted-foreground">{t("workspace.stream.loading")}</p>
      : runs.length === 0 ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.runs.none")}</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead><tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground"><th className="px-2 py-1.5">{t("workspace.detail.runs.run")}</th><th className="px-2 py-1.5">{t("workspace.detail.runs.who")}</th><th className="px-2 py-1.5">{t("workspace.detail.runs.step")}</th><th className="px-2 py-1.5">{t("workspace.detail.runs.when")}</th><th className="px-2 py-1.5">{t("workspace.detail.runs.outcome")}</th></tr></thead>
            <tbody>
              {[...runs].sort((a, b) => (a.queued_at < b.queued_at ? 1 : -1)).map((run) => (
                <tr key={run.run_id} className="border-t">
                  <td className="px-2 py-2 font-mono text-[11px]">{run.run_id.slice(0, 12)}</td>
                  <td className="px-2 py-2">{te("adapter", run.adapter)}{run.role === "review" ? ` · ${t("workspace.stream.run.review")}` : ""}</td>
                  <td className="px-2 py-2 text-muted-foreground">{stepOf(run.task_id) === null ? t("workspace.stream.run.stepUnknown") : t("workspace.stream.run.step", { step: stepOf(run.task_id)! })}</td>
                  <td className="px-2 py-2 text-[12px] text-muted-foreground">{relTime(run.started_at ?? run.queued_at, t)}</td>
                  <td className="px-2 py-2"><span className="rounded-full border px-2 py-0.5 font-mono text-[10px]">{te("runState", run.state)}</span>{run.exit_code !== null && <span className="ml-1 font-mono text-[11px] text-muted-foreground">{t("workspace.stream.run.exit", { code: run.exit_code })}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {runsTruncated && <p className="mt-2 text-[11px] text-muted-foreground">{t("workspace.stream.runsCapped", { limit: runs.length })}</p>}
        </div>
      );
  } else if (view === "decisions") {
    body = decisionsFailed ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.decisions.unavailable", { reason: t("workspace.panels.unavailable") })}</p>
      : decisions === null ? <p className="text-[13px] text-muted-foreground">{t("workspace.stream.loading")}</p>
      : !decisions.available || decisions.records === null ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.decisions.unavailable", { reason: decisions.reason ?? "?" })}</p>
      : decisions.records.length === 0 ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.decisions.none")}</p> : (
        <div className="space-y-2">
          {decisions.records.map((record) => (
            <div key={record.filename} className="rounded-lg border bg-muted/30 px-3 py-2 text-[13px]">
              <p><span className="mr-2 font-mono text-[11px] text-muted-foreground">{String(record.number).padStart(4, "0")}</span><b className="font-medium">{record.title ?? record.filename}</b>{record.status && <span className="ml-2 rounded-full border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{record.status}</span>}</p>
              <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{record.filename}{record.date ? ` · ${record.date}` : ""}{record.superseded_by ? ` · → ${record.superseded_by}` : ""}{!record.readable ? ` · ${t("workspace.panels.unavailable")}` : ""}</p>
            </div>
          ))}
          {(decisions.unreadable_count ?? 0) > 0 && <p className="text-[11px] text-muted-foreground">{t("workspace.detail.decisions.unreadableCount", { count: decisions.unreadable_count ?? 0 })}</p>}
        </div>
      );
  } else if (view === "changeRequests") {
    const log = crLogViewOf(dashboard?.cr_log);
    body = log.kind === "unknown" ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.changeRequests.unknown", { reason: log.reason })}</p>
      : log.kind === "none" || log.rows.length === 0 ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.changeRequests.none")}</p> : (
        <div className="space-y-2">
          {log.rows.map((row) => (
            <div key={row.id} className="rounded-lg border bg-muted/30 px-3 py-2 text-[13px]">
              <p><span className="mr-2 font-mono text-[11px] text-muted-foreground">{row.id}</span><b className="font-medium">{row.description}</b></p>
              <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{row.date} · {row.kind} · {row.status}{row.decision ? ` · ${row.decision}` : ""}</p>
            </div>
          ))}
        </div>
      );
  } else if (view === "defects") {
    // The three answers are kept apart the same way the reader and navFromDefects keep
    // them: "absent" is a settled nothing (no source registered), any other unavailable
    // is a read that did not land, and an available payload whose required counts are
    // null contradicts its own contract — that is a failed read, never rendered as zero.
    // `ready` is the payload once every required field is known non-null; only it may
    // render the list and the counted footer.
    const ready = defects !== null && defects.available && defects.records !== null
      && defects.open_count !== null && defects.unrecognised_count !== null
      && defects.unreadable_count !== null
      ? { ...defects, records: defects.records, open_count: defects.open_count,
          unrecognised_count: defects.unrecognised_count, unreadable_count: defects.unreadable_count }
      : null;
    body = defectsFailed ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.defects.unavailable", { reason: t("workspace.panels.unavailable") })}</p>
      : defects === null ? <p className="text-[13px] text-muted-foreground">{t("workspace.stream.loading")}</p>
      : (!defects.available && defects.reason === "absent") ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.defects.noSource")}</p>
      : ready === null ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.defects.unavailable", { reason: defects.reason ?? t("workspace.panels.unavailable") })}</p>
      : ready.records.length === 0 ? <p className="text-[13px] text-muted-foreground">{t("workspace.detail.defects.none")}</p> : (
        <div className="space-y-2">
          {ready.records.map((record, index) => {
            const fixed = record.status_token === "fixed";
            return (
              <div key={record.id ?? `#${index}`} className="rounded-lg border bg-muted/30 px-3 py-2 text-[13px]">
                <p>
                  <span className="mr-2 font-mono text-[11px] text-muted-foreground">{record.id ?? "—"}</span>
                  <b className={cn("font-medium", fixed && "text-muted-foreground line-through")}>{record.title ?? t("workspace.panels.unavailable")}</b>
                  {record.status && <span className="ml-2 rounded-full border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{record.status}</span>}
                </p>
                {record.summary && <p className={cn("mt-0.5 text-[12px]", fixed ? "text-muted-foreground" : "text-foreground/80")}>{record.summary}</p>}
                <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{record.milestone ? record.milestone : ""}{!record.readable ? `${record.milestone ? " · " : ""}${t("workspace.panels.unavailable")}` : ""}</p>
              </div>
            );
          })}
          <p className="text-[11px] text-muted-foreground">
            {t("workspace.detail.defects.openCount", { count: ready.open_count })}
            {ready.unrecognised_count > 0 ? ` · ${t("workspace.detail.defects.unrecognisedCount", { count: ready.unrecognised_count })}` : ""}
            {ready.unreadable_count > 0 ? ` · ${t("workspace.detail.defects.unreadableCount", { count: ready.unreadable_count })}` : ""}
          </p>
        </div>
      );
  } else {
    body = <p className="text-[13px] text-muted-foreground">{t("workspace.detail.files.noView")}</p>;
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
      <Button size="sm" variant="ghost" onClick={onBack}>{t("workspace.detail.back")}</Button>
      <h2 className="mt-2 text-[16px] font-semibold">{t(`workspace.detail.${view}.title`)}</h2>
      <div className="mt-3">{body}</div>
    </div>
  );
}
