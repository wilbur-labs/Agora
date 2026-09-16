"use client";

/**
 * One entry of a task's history that is not an agent turn: a note the person wrote,
 * a stage reached, a spec drafted or approved, a gate raised or answered, a handoff.
 *
 * Moved here from the workbench page when the timeline stopped being its own tab and
 * became part of the conversation stream (W9, mockup v2 screen 2): the stream is one
 * column ordered by time, and these are the entries between the run cards. The
 * wording keys stayed under `workspace.timeline.*`, where both locales already agree
 * on them.
 */

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import type { Translate, TranslateEnum } from "@/lib/i18n";
import type { RequirementSpec, TaskEvent } from "@/lib/control-plane";
import type { AttentionItem } from "@/lib/attention";
import type { InlineGate } from "@/lib/inline-gate";
import { InlineGateBlock, type GateOutcome } from "@/components/inline-gate";
import { ATTENTION_TIMELINE_EVENT_KEY } from "@/lib/timeline";
import { cn } from "@/lib/utils";

/** "just now / N min ago / N h ago / N d ago", from the rail's own vocabulary. */
export function relTime(iso: string | null, t: Translate): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (mins < 1) return t("workspace.rail.relative.justNow");
  if (mins < 60) return t("workspace.rail.relative.minAgo", { n: mins });
  const hours = Math.floor(mins / 60);
  if (hours < 24) return t("workspace.rail.relative.hourAgo", { n: hours });
  return t("workspace.rail.relative.dayAgo", { n: Math.floor(hours / 24) });
}

const MILESTONE_STATES = new Set([
  "backlog", "requirements", "design", "planned", "running", "review", "verified", "done", "blocked", "failed", "cancelled",
]);

function lookupMilestone(state: string, t: Translate): string | null {
  return MILESTONE_STATES.has(state) ? t(`workspace.timeline.milestone.${state}`) : null;
}

export function timelineView(
  event: TaskEvent, t: Translate, te: TranslateEnum,
): { who: "you" | "agent" | "system"; title: string; detail: string | null } {
  const p = event.payload as Record<string, unknown>;
  const adapter = String(p.adapter ?? "");
  const type = event.event_type;
  if (type === "user_message") return { who: "you", title: t("workspace.timeline.you"), detail: String(p.text ?? "") };
  if (type === "task_created") return { who: "system", title: t("workspace.timeline.events.taskCreated"), detail: null };
  if (type === "state_changed") {
    const to = String(p.to ?? "");
    // Plain-language milestone by the state reached; fall back to the localized
    // state name for anything unmapped. A human actor reads as "you".
    const milestone = lookupMilestone(to, t) ?? te("taskState", to);
    return { who: event.actor === "user" ? "you" : "system", title: milestone, detail: p.reason ? String(p.reason) : null };
  }
  if (type === "spec.created") return { who: "system", title: t("workspace.timeline.events.specCreated"), detail: null };
  if (type === "spec.approved") return { who: "you", title: t("workspace.timeline.events.specApproved"), detail: p.reason ? String(p.reason) : null };
  if (type === "spec.rejected") return { who: "you", title: t("workspace.timeline.events.specRejected"), detail: p.reason ? String(p.reason) : null };
  if (type === "run.queued") return { who: "agent", title: t("workspace.timeline.events.runQueued", { adapter }), detail: null };
  if (type === "run.started") return { who: "agent", title: t("workspace.timeline.events.runStarted", { adapter }), detail: null };
  if (type === "run.succeeded") return { who: "agent", title: t("workspace.timeline.events.runSucceeded", { adapter }), detail: null };
  if (type === "run.failed") return { who: "agent", title: t("workspace.timeline.events.runFailed", { adapter }), detail: p.error_message ? String(p.error_message) : null };
  if (type.startsWith("attention.")) {
    // One sentence per event (CR-27). An unmapped `attention.*` name falls through to
    // the humanised-type fallback below rather than borrowing "Needs you" — a name this
    // build does not know is a fact about this build, not a thing the user must do.
    const key = ATTENTION_TIMELINE_EVENT_KEY[type];
    if (key !== undefined) return { who: "system", title: t(key), detail: p.title ? String(p.title) : null };
  }
  if (type.startsWith("handoff.")) return { who: "system", title: t("workspace.timeline.events.handoff"), detail: p.commit ? String(p.commit).slice(0, 8) : null };
  // Fallback: humanized type.
  return { who: "system", title: type.replaceAll(".", " ").replaceAll("_", " "), detail: null };
}

const WHO_DOT: Record<"you" | "agent" | "system", string> = { you: "bg-primary", agent: "bg-blue-500", system: "bg-zinc-400" };

/** The spec a spec.* event refers to (by spec_id), so the step shows what was specified. */
export function specForEvent(event: TaskEvent, specsById: ReadonlyMap<string, RequirementSpec>): RequirementSpec | null {
  if (!event.event_type.startsWith("spec.")) return null;
  const id = String((event.payload as Record<string, unknown>).spec_id ?? "");
  return specsById.get(id) ?? null;
}

export function TimelineEntry({ event, spec, gate, onGateAnswered, t, te }: {
  event: TaskEvent;
  spec: RequirementSpec | null;
  /**
   * For an event that raised an attention item: the item and whether it still waits
   * (CR-74). The conversation decides this from its task-scoped attention read; a caller
   * without that read passes nothing and gets the two-word entry it always had.
   */
  gate?: InlineGate<AttentionItem>;
  onGateAnswered?: (outcome: GateOutcome) => void;
  t: Translate;
  te: TranslateEnum;
}) {
  const view = timelineView(event, t, te);
  const isYou = view.who === "you";
  const open = gate?.kind === "open";
  return (
    <div className={cn("flex gap-2.5", isYou && "flex-row-reverse")}>
      <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", WHO_DOT[view.who])} />
      {/* An open item carries a verdict and a form, so it takes the width a turn does. */}
      <div className={cn("rounded-xl border px-3 py-2", open ? "min-w-0 flex-1" : "max-w-[85%]", isYou ? "bg-primary/5" : "bg-card")}>
        <div className={cn("flex items-baseline gap-2", isYou && "flex-row-reverse")}>
          <span className="text-xs font-semibold">{view.title}</span>
          <span className="text-[10px] text-muted-foreground">{relTime(event.created_at, t)}</span>
        </div>
        {view.detail && <p className={cn("mt-0.5 whitespace-pre-wrap text-sm leading-relaxed", isYou && "text-right")}>{view.detail}</p>}
        {spec && <SpecBlock spec={spec} t={t} />}
        {gate && onGateAnswered && <InlineGateBlock gate={gate} t={t} te={te} onAnswered={onGateAnswered} />}
      </div>
    </div>
  );
}

// What the requirements step actually produced: summary, requirements, and the
// given/when/then acceptance scenarios — collapsed by default.
function SpecBlock({ spec, t }: { spec: RequirementSpec; t: Translate }) {
  const [open, setOpen] = useState(false);
  const reqs = [...spec.functional, ...spec.non_functional];
  return (
    <div className="mt-1.5">
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1 text-[11px] font-medium text-primary hover:underline">
        <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} /> {open ? t("workspace.timeline.spec.hide") : t("workspace.timeline.spec.view")}
      </button>
      {open && (
        <div className="mt-1.5 space-y-2 rounded-lg bg-muted/40 p-2 text-xs leading-relaxed">
          {spec.summary && (
            <div>
              <p className="font-medium text-muted-foreground">{t("workspace.timeline.spec.summary")}</p>
              <p className="mt-0.5">{spec.summary}</p>
            </div>
          )}
          {reqs.length > 0 && (
            <div>
              <p className="font-medium text-muted-foreground">{t("workspace.timeline.spec.requirements")} ({reqs.length})</p>
              <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
                {reqs.map((r) => <li key={r.requirement_id}>{r.statement}</li>)}
              </ul>
            </div>
          )}
          {spec.acceptance_scenarios.length > 0 && (
            <div>
              <p className="font-medium text-muted-foreground">{t("workspace.timeline.spec.scenarios")} ({spec.acceptance_scenarios.length})</p>
              <ul className="mt-0.5 space-y-0.5">
                {spec.acceptance_scenarios.map((s) => (
                  <li key={s.scenario_id} className="rounded bg-background/60 px-2 py-1">
                    <span className="font-medium">{s.given}</span> → {s.when} → <span className="font-medium">{s.then}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
