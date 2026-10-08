"use client";

/**
 * The project workbench's left column: **project-level** navigation.
 *
 * What it replaced, and why. The column used to be a cross-project task rail with a
 * project filter on top — which made the task the workbench's first-class citizen. The
 * design has the project first: the column opens with the project's name and lists the
 * project's resources (overview, runs, goals, change requests, decisions, known defects,
 * skills, files), because the product's one real differentiator is that machinery, and
 * until now it could only be reached by typing a URL.
 *
 * Six constraints hold this shape in place. Break any one and this is a task-first column
 * with a new title:
 *
 *   1. the project's name and its resources come first;
 *   2. tasks are only ever this project's — and since W11 they are not here at all;
 *   3. the current task is context inside the project, not a global object;
 *   4. no filter over tasks (a switcher *between projects* is not one — see below);
 *   5. no cross-project attention — that belongs to Today;
 *   6. runs stay nested under tasks.
 *
 * **The task list has left this column (W11).** It was here because the middle column is
 * task-scoped and a task had to be switchable at all times. That is now the plan's job:
 * the page itself opens on the project's development order, which switches tasks *and*
 * answers how many steps there are and which one is live — questions a quiet rail could
 * never answer. What replaced it at the top is a project switcher, because the owner's
 * question at this level turned out to be "which project", not "which task".
 *
 * Constraint 4 above is therefore narrowed, not broken: this is a switcher between
 * projects, never a filter over tasks. Picking one enters that project; it does not
 * cross-filter anything.
 *
 * Counts are not numbers here — they are capabilities (see lib/nav-capability.ts). Three
 * of the eight resources have nothing behind them yet, and each says which kind of
 * nothing it is rather than all of them saying "unavailable".
 */
import Link from "next/link";
import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  FolderTree,
  GitPullRequest,
  LayoutDashboard,
  Library,
  Play,
  ScrollText,
  Target,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { CapabilitySlot } from "@/components/capability-slot";
import { useI18n } from "@/lib/i18n";
import type { NavCapability } from "@/lib/nav-capability";
import type { ProjectGate } from "@/lib/project-chat";

export interface ProjectNavProject {
  project_id: string;
  name: string;
  /** Who runs the work and who reviews it. Both come from the projects read, so both
   *  are facts rather than defaults this column invented. */
  worker_agent: string;
  reviewer_agent: string;
}

export interface ProjectNavProps {
  /** null when no project is in context yet: the column says so rather than guessing. */
  projectId: string | null;
  projectName: string | null;
  projectRoot: string | null;
  /** Resource capabilities. Each is a sentence about what can be said, not a number. */
  capabilities: {
    runs: NavCapability;
    changeRequests: NavCapability;
    decisions: NavCapability;
    defects: NavCapability;
    skills: NavCapability;
    files: NavCapability;
  };
  /** Every project this control plane holds, for the switcher. Empty when the read
   *  failed — the switcher then shows only the project in context rather than claiming
   *  this is the whole list. */
  projects: readonly ProjectNavProject[];
  /** Enters another project. Not a filter: it changes which project the page is inside. */
  onSwitchProject: (projectId: string) => void;
  /** Whether this project may be given work at all (CR-61). A project with no goal shows
   *  the way to settle one instead of a "new task" button; a project whose charter could
   *  not be read shows neither claim. Tasks are proposed in the conversation now, so
   *  there is nothing here to open a dialog with (CR-63). */
  gate: ProjectGate;
  /** Sends the person to the conversation that settles the goal. */
  onSettleGoal: () => void;
  /** The W7 read of other workspaces on disk. Folded and visually separated: it is not
   *  part of this project, and it predates this column's design. */
  otherWorkspaces: React.ReactNode;
  header: React.ReactNode;
  /**
   * Which of the project's views the centre is showing (W12). Rows open a view in the
   * centre of this window — not another page — and the open one is marked.
   */
  view?: string;
  onSelectView?: (view: string) => void;
  /** Where each executor runs, from the adapters read; `null` target = this machine.
   *  Absent or empty when that read has not landed or failed. */
  environments?: readonly { adapter: string; target: string | null }[];
}

/** Row → the centre view it opens. Rows without one state their count and stop. */
const ROW_VIEW: Partial<Record<keyof typeof RESOURCE_ICONS, string>> = {
  overview: "chat", runs: "runs", goals: "goal", changeRequests: "changeRequests",
  decisions: "decisions", defects: "defects", files: "files",
};

const RESOURCE_ICONS = {
  overview: LayoutDashboard,
  runs: Play,
  goals: Target,
  changeRequests: GitPullRequest,
  decisions: ScrollText,
  defects: FileText,
  skills: Library,
  files: FolderTree,
} as const;

function NavRow({
  itemKey,
  capability,
  href,
  onClick,
  active = false,
}: {
  itemKey: keyof typeof RESOURCE_ICONS;
  capability?: NavCapability;
  href?: string;
  onClick?: () => void;
  active?: boolean;
}) {
  const { t } = useI18n();
  const Icon = RESOURCE_ICONS[itemKey];
  const body = (
    <>
      <Icon className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{t(`workspace.nav.items.${itemKey}`)}</span>
      {capability && <CapabilitySlot capability={capability} />}
    </>
  );
  const className = cn(
    "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[0.875rem] hover:bg-accent",
    active && "bg-accent font-medium",
  );

  if (href) {
    return (
      <Link href={href} className={className}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className}>
        {body}
      </button>
    );
  }
  // Neither: a row that states a count and does nothing else. Rendered as text rather
  // than a dead button, so nothing invites a click that goes nowhere.
  return <div className={cn(className, "cursor-default")}>{body}</div>;
}

export function ProjectNav({
  projectId,
  projectName,
  projectRoot,
  capabilities,
  projects,
  onSwitchProject,
  gate,
  onSettleGoal,
  otherWorkspaces,
  header,
  view,
  onSelectView,
  environments,
}: ProjectNavProps) {
  const { t } = useI18n();
  const [otherOpen, setOtherOpen] = useState(false);
  const current = projects.find((project) => project.project_id === projectId);

  // `h-full`, not `h-screen`: this row's height is the viewport minus the top bar, and a
  // full-viewport child stretches the row past it. The parent clips, and what it clips is
  // the bottom of the centre column — the message box.
  return (
    <aside className="flex h-full min-h-0 w-72 shrink-0 flex-col border-r bg-sidebar lg:w-80">
      {header}

      {/* 1. The projects. The one in context is named first and stays named; the others
             are a way in, not a filter. When the projects read failed the list is empty
             and only the project in context appears — silence about the rest, rather
             than a short list that reads as the whole. */}
      <div className="border-b px-4 pb-3">
        <div className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
          {t("workspace.nav.projectsLabel")}
        </div>
        {projectId ? (
          <>
            {/* One box, as the design draws it: the project's name with "換項目" as the
                caret. A native select — the list is only there while you are choosing,
                and the person's rule is fewer things on screen. When the projects read
                failed the list holds only the project in context, so the box still names
                it and offers nothing it cannot vouch for. */}
            <div className="relative mt-1">
              <select
                id="project-switcher"
                value={projectId}
                onChange={(e) => { if (e.target.value !== projectId) onSwitchProject(e.target.value); }}
                aria-label={t("workspace.nav.switchProject")}
                className="w-full appearance-none truncate rounded-lg border bg-background py-1.5 pl-2.5 pr-20 text-sm font-semibold outline-none focus:border-ring"
                title={projectName ?? projectId}
              >
                {(projects.some((project) => project.project_id === projectId)
                  ? projects
                  : [{ project_id: projectId, name: projectName ?? projectId, worker_agent: "", reviewer_agent: "" }, ...projects]
                ).map((project) => (
                  <option key={project.project_id} value={project.project_id}>{project.name}</option>
                ))}
              </select>
              <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-[0.75rem] text-muted-foreground">
                ▾ {t("workspace.nav.switchProject")}
              </span>
            </div>
            {projectRoot && (
              <div className="mt-1 truncate text-[0.75rem] text-muted-foreground" title={projectRoot}>
                {projectRoot}
              </div>
            )}
          </>
        ) : (
          <p className="text-[0.75rem] leading-snug text-muted-foreground">
            {t("workspace.nav.noProject")}
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {/* 2. The project's resources. This is what could only be reached by URL before. */}
        {projectId && (
          <nav className="space-y-0.5">
            {/* Counts, not doors. In the design these rows say how much of each the
                project has; they do not throw you onto another page — which is what
                they did, and what "到处跳转" was about. The panels they will open live
                in this window, and until they exist a row states its count and stops. */}
            {(["overview", "runs", "goals", "changeRequests", "decisions", "defects", "skills", "files"] as const).map((key) => {
              const target = ROW_VIEW[key];
              const capability = key === "overview" || key === "goals" ? undefined : capabilities[key];
              return (
                <NavRow
                  key={key}
                  itemKey={key}
                  capability={capability}
                  onClick={target !== undefined && onSelectView ? () => onSelectView(target) : undefined}
                  active={target !== undefined && view === target}
                />
              );
            })}
          </nav>
        )}

        {/* 3. Who runs it and where. The design keeps both beside the project's
               resources, because they are the two facts that decide what "continue"
               actually does. The executor pair is read from the control plane; the
               environment has no per-project source yet (there is no environment
               registry — W3-a/W5), so the row says that rather than naming this
               machine and implying the project is pinned to it. */}
        {current !== undefined && (
          <>
            <div className="px-4 pb-1 pt-4 text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
              {t("workspace.nav.executor")}
            </div>
            <div className="flex justify-between px-4 py-1 text-[0.8125rem] text-muted-foreground">
              <span>{current.worker_agent}</span>
              <b className="font-medium text-foreground">{t("workspace.nav.asWorker")}</b>
            </div>
            <div className="flex justify-between px-4 py-1 text-[0.8125rem] text-muted-foreground">
              <span>{current.reviewer_agent}</span>
              <b className="font-medium text-foreground">{t("workspace.nav.asReviewer")}</b>
            </div>
            <div className="px-4 pb-1 pt-3 text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
              {t("workspace.nav.environment")}
            </div>
            {/* Where each executor runs, read from its configuration (W3-a U5): a remote
                adapter names its host, a local one says this machine. No registry of
                environments exists yet, and when the list could not be read the column
                says that rather than "local". */}
            {environments && environments.length > 0 ? (
              <>
                <p className="px-4 pb-1 text-[0.75rem] leading-snug text-muted-foreground">{t("workspace.nav.environmentByAdapter")}</p>
                {environments.map((row) => (
                  <div key={row.adapter} className="flex justify-between gap-2 px-4 py-1 text-[0.8125rem] text-muted-foreground">
                    <span>{row.adapter}</span>
                    <b className="min-w-0 truncate font-mono text-[0.75rem] font-medium text-foreground" title={row.target ?? undefined}>
                      {row.target ?? t("workspace.nav.environmentLocal")}
                    </b>
                  </div>
                ))}
              </>
            ) : (
              <p className="px-4 pb-1 text-[0.75rem] leading-snug text-muted-foreground">
                {t("workspace.nav.environmentUnknown")}
              </p>
            )}
          </>
        )}

        {/* 4. What stands in the way of work, said once. It is project-level — a project
               with no settled goal may not be given work at all (CR-61) — so it outlived
               the task list it used to sit above. "This project has no goal" is a claim,
               so the unknown case says the charter could not be read rather than making
               it. The way onward is the conversation, which is where a goal is settled. */}
        {gate.kind !== "open" && (
          <div className="mx-2 mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[0.75rem] leading-relaxed text-amber-700 dark:text-amber-400">
            <p>{t(gate.kind === "noCharter" ? "projectChat.gate.noCharter" : "projectChat.gate.unknown")}</p>
            {gate.kind === "noCharter" && (
              <button type="button" onClick={onSettleGoal} className="mt-1 font-medium underline">
                {t("projectChat.gate.settle")}
              </button>
            )}
          </div>
        )}
      </div>

      {/* 4. Other workspaces (W7). Folded, named, and separated by a rule: it is a read of
             other machines' work, not part of this project. It postdates this column's
             design, which is why it is here at all rather than dropped. */}
      <div className="border-t">
        <button
          type="button"
          onClick={() => setOtherOpen((v) => !v)}
          className="flex w-full items-center gap-1.5 px-3 py-2 text-[0.75rem] text-muted-foreground hover:bg-accent"
          aria-expanded={otherOpen}
        >
          {otherOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
          {t("workspace.nav.otherWorkspaces")}
        </button>
        {otherOpen && <div className="max-h-64 overflow-y-auto px-2 pb-2">{otherWorkspaces}</div>}
      </div>
    </aside>
  );
}
