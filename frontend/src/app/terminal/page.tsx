"use client";

/**
 * The terminal page (CR-30): `/terminal?id=<terminal>`. A static page (the build is a
 * static export), so the terminal is chosen by the query string, read in the browser.
 * Tabs across the top are every terminal this server holds; the body is the chosen one.
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { TerminalView } from "@/components/terminal-view";
import { closeTerminal, listTerminals, TerminalAuthRequired, terminalStateOf, type TerminalRow } from "@/lib/terminal";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export default function TerminalPage() {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string | null>(null);
  const [rows, setRows] = useState<TerminalRow[] | "auth" | "failed" | null>(null);

  const load = useCallback(() => {
    listTerminals()
      .then((data) => setRows(data.terminals))
      .catch((err) => setRows(err instanceof TerminalAuthRequired ? "auth" : "failed"));
  }, []);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    // Read once from the address the tab was opened with; the tabs change it afterwards.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelected(id);
    load();
  }, [load]);

  const choose = (id: string) => {
    setSelected(id);
    window.history.replaceState(null, "", `/terminal?id=${encodeURIComponent(id)}`);
  };

  const current = Array.isArray(rows) ? rows.find((row) => row.terminal_id === selected) ?? null : null;

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2 text-sm">
        <Link href="/" className="font-semibold">{t("common.brand.name")}</Link>
        <span className="text-muted-foreground">· {t("workspace.terminal.title")}</span>
        <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {Array.isArray(rows) && rows.map((row) => (
            <button key={row.terminal_id} type="button" onClick={() => choose(row.terminal_id)}
              className={cn("shrink-0 rounded-md px-2 py-1 text-xs", row.terminal_id === selected ? "bg-primary text-primary-foreground" : "hover:bg-accent",
                !row.alive && "opacity-60")}>
              {row.label}{row.alive ? "" : ` · ${t("workspace.terminal.state.ended")}`}
            </button>
          ))}
        </nav>
        {current?.alive && (
          <Button variant="outline" size="sm" onClick={() => void closeTerminal(current.terminal_id).then(load)}>
            {t("workspace.terminal.close")}
          </Button>
        )}
      </header>
      {rows === "auth" && <p className="p-4 text-sm text-amber-700 dark:text-amber-400">{t("workspace.terminal.authRequired")}</p>}
      {rows === "failed" && <p className="p-4 text-sm text-red-700 dark:text-red-400">{t("workspace.terminal.listFailed")}</p>}
      {Array.isArray(rows) && selected && !current && <p className="p-4 text-sm text-muted-foreground">{t("workspace.terminal.gone")}</p>}
      {current && (
        <>
          <p className="shrink-0 truncate border-b px-3 py-1 font-mono text-[0.75rem] text-muted-foreground" title={current.cwd}>
            {current.cwd} · {t(`workspace.terminal.state.${terminalStateOf(current)}`, { code: String(current.exit_code ?? "?") })}
          </p>
          <div className="min-h-0 flex-1">
            <TerminalView key={current.terminal_id} terminalId={current.terminal_id} onExit={load} />
          </div>
        </>
      )}
    </div>
  );
}
