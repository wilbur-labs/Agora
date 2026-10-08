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
  createTerminal, launchChoicesOf, TerminalAuthRequired, terminalTargets,
  type LaunchChoice, type TerminalTargets,
} from "@/lib/terminal";
import { useI18n } from "@/lib/i18n";

export function TerminalLauncher({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState<TerminalTargets | null | "failed">(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    terminalTargets(projectId).then((data) => { if (alive) setTargets(data); }).catch(() => { if (alive) setTargets("failed"); });
    const away = (event: MouseEvent) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => { alive = false; document.removeEventListener("mousedown", away); };
  }, [open, projectId]);

  async function launch(choice: LaunchChoice) {
    setBusy(true); setMessage(null);
    // Opened first, inside the click, so the browser does not treat it as a pop-up.
    const tab = window.open("about:blank", "_blank");
    try {
      const row = await createTerminal(choice.kind === "task"
        ? { project_id: projectId, task: { source: choice.target.source, task: choice.target.task } }
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
          {choices.map((choice) => choice.kind === "task" ? (
            <button key={`${choice.target.source}/${choice.target.task}`} type="button" disabled={busy || !choice.openable}
              onClick={() => void launch(choice)}
              className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent disabled:opacity-60">
              <span className="font-medium">{t("workspace.terminal.asTask", { task: choice.target.task })}</span>
              <span className="block text-xs text-muted-foreground">
                {choice.openable ? t("workspace.terminal.asTaskHint") : choice.target.reason}
              </span>
            </button>
          ) : (
            <button key={choice.command} type="button" disabled={busy} onClick={() => void launch(choice)}
              className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent disabled:opacity-60">
              {t(`workspace.terminal.inRepo.${choice.command}`)}
            </button>
          ))}
          {message && <p className="px-2 py-1 text-xs text-red-700 dark:text-red-400">{message}</p>}
        </div>
      )}
    </div>
  );
}
