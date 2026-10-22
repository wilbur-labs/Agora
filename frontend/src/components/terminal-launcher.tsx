"use client";

/**
 * "Open a terminal" for one project (CR-30): as one of its claude-workspace tasks (what
 * `cw open <task>` does — the task's RESUME, CRs and review gate come with it), or in the
 * repository with an agent or a plain shell. The terminal opens in its own browser tab.
 */
import { useEffect, useRef, useState } from "react";
import { SquareTerminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  createTerminal, launchChoicesOf, remoteTaskNoteOf, remoteTaskTargets, repoFactsOf, TerminalAuthRequired, terminalTargets,
  type LaunchChoice, type RemoteTaskTargets, type TerminalTargets,
} from "@/lib/terminal";
import { useI18n } from "@/lib/i18n";

export function TerminalLauncher({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState<TerminalTargets | null | "failed">(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // CR-30 slice 3: each remote host's tasks, read when the menu opens. "failed" is the
  // request itself not coming back; a host it reached but could not read is in the scan.
  const [remoteTasks, setRemoteTasks] = useState<Record<string, RemoteTaskTargets | "loading" | "failed">>({});
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    terminalTargets(projectId).then((data) => {
      if (!alive) return;
      setTargets(data);
      for (const environment of data.environments ?? []) {
        if (!environment.workspace_tool_dir) continue;
        setRemoteTasks((held) => ({ ...held, [environment.environment_id]: "loading" }));
        remoteTaskTargets(projectId, environment.environment_id)
          .then((scan) => { if (alive) setRemoteTasks((held) => ({ ...held, [environment.environment_id]: scan })); })
          .catch(() => { if (alive) setRemoteTasks((held) => ({ ...held, [environment.environment_id]: "failed" })); });
      }
    }).catch(() => { if (alive) setTargets("failed"); });
    const away = (event: MouseEvent) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => { alive = false; document.removeEventListener("mousedown", away); };
  }, [open, projectId]);

  async function launch(choice: LaunchChoice | { kind: "remoteTask"; environmentId: string; task: string }) {
    setBusy(true); setMessage(null);
    // Opened first, inside the click, so the browser does not treat it as a pop-up.
    const tab = window.open("about:blank", "_blank");
    try {
      const row = await createTerminal(choice.kind === "remoteTask"
        ? { project_id: projectId, environment_id: choice.environmentId, remote_task: choice.task }
        : choice.kind === "task"
        ? { project_id: projectId, task: { source: choice.target.source, task: choice.target.task } }
        : choice.kind === "remote"
          ? { project_id: projectId, command: choice.command, environment_id: choice.environment.environment_id }
          : { project_id: projectId, command: choice.command });
      const url = `/terminal?id=${encodeURIComponent(row.terminal_id)}`;
      if (tab) tab.location.href = url; else window.location.href = url;
      setOpen(false);
    } catch (err) {
      tab?.close();
      setMessage(err instanceof TerminalAuthRequired ? t("workspace.terminal.authRequired") : t("workspace.terminal.startFailed", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  }

  const choices = launchChoicesOf(typeof targets === "object" ? targets : null);
  return (
    <div ref={box} className="relative">
      <Button variant="outline" size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <SquareTerminal className="size-3.5" /> {t("workspace.terminal.open")}
      </Button>
      {open && (
        <div className="absolute right-0 z-40 mt-1 w-80 space-y-1 rounded-lg border bg-background p-2 text-sm shadow-lg">
          {targets === null && <p className="px-2 py-1 text-xs text-muted-foreground">{t("workspace.terminal.loading")}</p>}
          {targets === "failed" && <p className="px-2 py-1 text-xs text-amber-700 dark:text-amber-400">{t("workspace.terminal.targetsFailed")}</p>}
          {typeof targets === "object" && targets?.environments_error && (
            <p className="px-2 py-1 text-xs text-amber-700 dark:text-amber-400">{t("workspace.terminal.environmentsFailed")}</p>
          )}
          {choices.map((choice) => choice.kind === "remote" ? (
            <button key={`${choice.environment.environment_id}/${choice.command}`} type="button" disabled={busy} onClick={() => void launch(choice)}
              className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent disabled:opacity-60">
              {t(`workspace.terminal.onRemote.${choice.command}`, { target: choice.environment.target })}
              {choice.command === "claude" && <span className="block text-xs text-muted-foreground">{t("workspace.terminal.onRemoteHint", { root: choice.environment.root })}</span>}
            </button>
          ) : choice.kind === "task" ? (
            <button key={`${choice.target.source}/${choice.target.task}`} type="button" disabled={busy || !choice.openable}
              onClick={() => void launch(choice)}
              className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent disabled:opacity-60">
              <span className="font-medium">
                {t(choice.target.workspace === "kiro" ? "workspace.terminal.asKiroTask" : "workspace.terminal.asTask", { task: choice.target.task })}
              </span>
              <span className="block text-xs text-muted-foreground">
                {!choice.openable ? choice.target.reason
                  : choice.target.workspace === "kiro" ? t("workspace.terminal.asKiroTaskHint", { agent: choice.target.agent ?? choice.target.task })
                  : t("workspace.terminal.asTaskHint")}
              </span>
            </button>
          ) : (
            <button key={choice.command} type="button" disabled={busy} onClick={() => void launch(choice)}
              className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent disabled:opacity-60">
              {t(`workspace.terminal.inRepo.${choice.command}`)}
            </button>
          ))}
          {(typeof targets === "object" && targets ? targets.environments : []).filter((e) => e.workspace_tool_dir).map((environment) => {
            const read = remoteTasks[environment.environment_id];
            return (
              <div key={`tasks-${environment.environment_id}`} className="border-t pt-1">
                <p className="px-2 py-1 text-xs font-medium text-muted-foreground">{t("workspace.terminal.remoteTasks.heading", { target: environment.target })}</p>
                {(read === undefined || read === "loading") && <p className="px-2 py-1 text-xs text-muted-foreground">{t("workspace.terminal.remoteTasks.loading")}</p>}
                {read === "failed" && <p className="px-2 py-1 text-xs text-amber-700 dark:text-amber-400">{t("workspace.terminal.remoteTasks.requestFailed")}</p>}
                {typeof read === "object" && (() => {
                  const note = remoteTaskNoteOf(read);
                  const remote = repoFactsOf(read.remote_repo);
                  return (
                    <>
                      {read.tasks.map((task) => (
                        <button key={task.task} type="button" disabled={busy}
                          onClick={() => void launch({ kind: "remoteTask", environmentId: environment.environment_id, task: task.task })}
                          className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent disabled:opacity-60">
                          <span className="font-medium">{t("workspace.terminal.remoteTasks.open", { task: task.task })}</span>
                          {task.title && <span className="block text-xs text-muted-foreground">{task.title}</span>}
                        </button>
                      ))}
                      {note.kind === "unavailable" && <p className="px-2 py-1 text-xs text-amber-700 dark:text-amber-400">{t("workspace.terminal.remoteTasks.unavailable", { reason: note.reason })}</p>}
                      {note.kind === "partial" && <p className="px-2 py-1 text-xs text-amber-700 dark:text-amber-400">{t(note.truncated ? "workspace.terminal.remoteTasks.truncated" : "workspace.terminal.remoteTasks.partial", { count: note.unreadable })}</p>}
                      {note.kind === "none" && <p className="px-2 py-1 text-xs text-muted-foreground">{t("workspace.terminal.remoteTasks.none", { root: environment.root })}</p>}
                      {read.state !== "unavailable" && (
                        <p className="px-2 py-1 text-[0.6875rem] text-muted-foreground">
                          {t("workspace.terminal.remoteTasks.repoRemote", { head: remote.head, dirty: t(`workspace.terminal.remoteTasks.dirty.${remote.dirty}`) })}
                          {read.local_repos.map((repo) => {
                            const local = repoFactsOf(repo);
                            return <span key={repo.source} className="block">{t("workspace.terminal.remoteTasks.repoLocal", { source: repo.source, head: local.head, dirty: t(`workspace.terminal.remoteTasks.dirty.${local.dirty}`) })}</span>;
                          })}
                        </p>
                      )}
                    </>
                  );
                })()}
              </div>
            );
          })}
          {message && <p className="px-2 py-1 text-xs text-red-700 dark:text-red-400">{message}</p>}
        </div>
      )}
    </div>
  );
}
