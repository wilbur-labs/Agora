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
 *   2. tasks are only ever this project's;
 *   3. the current task is context inside the project, not a global object;
 *   4. no project filter;
 *   5. no cross-project attention — that belongs to Today;
 *   6. runs stay nested under tasks.
 *
 * The task section exists because the middle column is a task-scoped conversation, so a
 * task has to be switchable at all times — the mockup depicts one task mid-flight and
 * simply never draws the switcher. It is deliberately quiet: a flat list, no attention
 * grouping, no banners competing with the project's own resources. The test of whether
 * this stayed project-first is whether the task list is once again the largest, busiest,
 * most organising thing on the page.
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
import { useI18n } from "@/lib/i18n";
import {
  countSayable,
  navCapabilityKey,
  type NavCapability,
} from "@/lib/nav-capability";

export interface ProjectNavTask {
  task_id: string;
  title: string;
  state: string;
  /** A run in flight, if any — a dot, not a section. Runs stay nested under tasks. */
  running: boolean;
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
  tasks: ProjectNavTask[];
  selectedTaskId: string | null;
  onSelectTask: (taskId: string) => void;
  onNewTask: () => void;
  /** The W7 read of other workspaces on disk. Folded and visually separated: it is not
   *  part of this project, and it predates this column's design. */
  otherWorkspaces: React.ReactNode;
  header: React.ReactNode;
}

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

/**
 * The trailing slot on a nav row.
 *
 * A number only when a read produced one. Otherwise the short form of whichever sentence
 * applies, with the long form on `title` — never a bare grey dash, which would be the
 * three sentences merged into one again.
 */
function CapabilitySlot({ capability }: { capability: NavCapability }) {
  const { t } = useI18n();
  const key = navCapabilityKey(capability);

  if (countSayable(capability)) {
    const caveat = capability.caveat;
    return (
      <span
        className={cn(
          "shrink-0 text-[11px] tabular-nums",
          caveat ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground",
        )}
        title={
          caveat
            ? t(`workspace.nav.why.caveat.${caveat.kind}`, { caveat: caveat.count })
            : undefined
        }
      >
        {t(key, { value: capability.value, caveat: caveat?.count ?? 0 })}
      </span>
    );
  }

  // Not a count. The row still renders and stays clickable — an item whose read is
  // missing is not a tombstone, it is an item that can explain itself.
  const why =
    capability.kind === "unbuilt"
      ? t("workspace.nav.why.unbuiltWithMilestone", { milestone: capability.milestone })
      : capability.kind === "noReader"
        ? t(`workspace.nav.why.noReader.${capability.reason}`)
        : capability.kind === "unreadable"
          ? t(`workspace.nav.why.unreadable.${capability.reason}`)
          : capability.kind === "unreported"
            ? t("workspace.nav.why.unreported")
            : t("workspace.nav.why.none");

  return (
    <span
      className="shrink-0 truncate text-[10px] italic text-muted-foreground/80"
      title={why}
    >
      {t(key, {
        milestone: capability.kind === "unbuilt" ? capability.milestone : "",
      })}
    </span>
  );
}

function NavRow({
  itemKey,
  capability,
  href,
  onClick,
}: {
  itemKey: keyof typeof RESOURCE_ICONS;
  capability?: NavCapability;
  href?: string;
  onClick?: () => void;
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
  const className =
    "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-accent";

  if (href) {
    return (
      <Link href={href} className={className}>
        {body}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onClick} className={className}>
      {body}
    </button>
  );
}

export function ProjectNav({
  projectId,
  projectName,
  projectRoot,
  capabilities,
  tasks,
  selectedTaskId,
  onSelectTask,
  onNewTask,
  otherWorkspaces,
  header,
}: ProjectNavProps) {
  const { t } = useI18n();
  const [otherOpen, setOtherOpen] = useState(false);

  return (
    <aside className="flex h-screen w-72 shrink-0 flex-col border-r bg-sidebar lg:w-80">
      {header}

      {/* 1. The project comes first. Not a filter — the column is inside one project. */}
      <div className="border-b px-4 pb-3">
        {projectId ? (
          <>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {t("workspace.nav.projectLabel")}
            </div>
            <div className="truncate text-sm font-semibold" title={projectName ?? projectId}>
              {projectName ?? projectId}
            </div>
            {projectRoot && (
              <div className="truncate text-[11px] text-muted-foreground" title={projectRoot}>
                {projectRoot}
              </div>
            )}
          </>
        ) : (
          <p className="text-[11px] leading-snug text-muted-foreground">
            {t("workspace.nav.noProject")}
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {/* 2. The project's resources. This is what could only be reached by URL before. */}
        {projectId && (
          <nav className="space-y-0.5">
            <NavRow itemKey="overview" href={`/workspace?project=${projectId}`} />
            <NavRow itemKey="runs" capability={capabilities.runs} href="/runs" />
            <NavRow itemKey="goals" href={`/portfolio?project=${projectId}`} />
            <NavRow
              itemKey="changeRequests"
              capability={capabilities.changeRequests}
              href={`/portfolio?project=${projectId}#cr-log`}
            />
            <NavRow
              itemKey="decisions"
              capability={capabilities.decisions}
              href={`/portfolio?project=${projectId}#decisions`}
            />
            <NavRow itemKey="defects" capability={capabilities.defects} href="/portfolio" />
            <NavRow itemKey="skills" capability={capabilities.skills} href="/skills" />
            <NavRow itemKey="files" capability={capabilities.files} href="/environments" />
          </nav>
        )}

        {/* 3. Tasks: this project's only, flat and quiet. Switchable at all times because
               the middle column is task-scoped, but never the organising element. */}
        <div className="mt-4">
          <div className="flex items-center justify-between px-2 py-1">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {t("workspace.nav.tasks")}
            </span>
            <button
              type="button"
              onClick={onNewTask}
              className="rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              + {t("workspace.rail.newTask")}
            </button>
          </div>
          {tasks.length === 0 ? (
            <p className="px-2 py-3 text-[11px] text-muted-foreground">
              {t("workspace.rail.empty")}
            </p>
          ) : (
            <ul className="space-y-0.5">
              {tasks.map((task) => (
                <li key={task.task_id}>
                  <button
                    type="button"
                    onClick={() => onSelectTask(task.task_id)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors",
                      task.task_id === selectedTaskId
                        ? "bg-primary/10 ring-1 ring-primary/30"
                        : "hover:bg-accent",
                    )}
                  >
                    <span
                      className={cn(
                        "size-1.5 shrink-0 rounded-full",
                        task.running ? "bg-emerald-500" : "bg-muted-foreground/40",
                      )}
                    />
                    <span className="min-w-0 flex-1 truncate text-[13px]">{task.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* 4. Other workspaces (W7). Folded, named, and separated by a rule: it is a read of
             other machines' work, not part of this project. It postdates this column's
             design, which is why it is here at all rather than dropped. */}
      <div className="border-t">
        <button
          type="button"
          onClick={() => setOtherOpen((v) => !v)}
          className="flex w-full items-center gap-1.5 px-3 py-2 text-[11px] text-muted-foreground hover:bg-accent"
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
