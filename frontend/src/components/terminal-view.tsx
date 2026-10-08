"use client";

/**
 * One terminal, drawn by xterm.js and connected over a WebSocket (CR-30). Output is
 * replayed on connect, so a closed tab reopens on the same screen. Input and window size
 * go back up the same socket. Loaded only in the browser: xterm touches the DOM at import.
 */
import { useEffect, useRef, useState } from "react";
import "@xterm/xterm/css/xterm.css";

import { getApiBase } from "@/lib/api";
import { readTerminalToken, terminalStreamUrl } from "@/lib/terminal";
import { readUiScale, terminalFontSize } from "@/lib/ui-scale";
import { useI18n } from "@/lib/i18n";

type Status = "connecting" | "open" | "exited" | "closed" | "tooSlow" | "refused";

export function TerminalView({ terminalId, onExit }: { terminalId: string; onExit?: (code: number | null) => void }) {
  const { t } = useI18n();
  const host = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [exitCode, setExitCode] = useState<number | null>(null);
  const onExitRef = useRef(onExit);
  useEffect(() => { onExitRef.current = onExit; }, [onExit]);

  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    void (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      if (disposed || !host.current) return;
      const term = new Terminal({
        cursorBlink: true,
        scrollback: 10000,
        fontFamily: "Cascadia Mono, Consolas, 'Courier New', monospace",
        fontSize: terminalFontSize(readUiScale()),
        theme: { background: "#0b0f14" },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(host.current);
      fit.fit();
      const socket = new WebSocket(terminalStreamUrl(terminalId, window.location, getApiBase()));
      let exited = false;
      const sendSize = () => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
      };
      // The token goes in the first message: not in the URL, not in a cookie.
      socket.onopen = () => {
        socket.send(JSON.stringify({ type: "auth", token: readTerminalToken() ?? "" }));
        setStatus("open"); sendSize(); term.focus();
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data));
        if (message.type === "output") term.write(message.data);
        else if (message.type === "exit") {
          exited = true;
          setStatus("exited");
          setExitCode(message.code ?? null);
          onExitRef.current?.(message.code ?? null);
        } else if (message.type === "overflow") setStatus("tooSlow");
      };
      socket.onclose = (event) => {
        if (exited) return;
        setStatus(event.code === 4401 || event.code === 4404 || event.code === 1008 ? "refused" : "closed");
      };
      const input = term.onData((data) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
      });
      const observer = new ResizeObserver(() => { try { fit.fit(); sendSize(); } catch { /* not laid out */ } });
      observer.observe(host.current);
      cleanup = () => {
        observer.disconnect();
        input.dispose();
        socket.close();
        term.dispose();
      };
    })();
    return () => { disposed = true; cleanup(); };
  }, [terminalId]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {status !== "open" && (
        <p className={status === "connecting" ? "px-3 py-1 text-xs text-muted-foreground" : "px-3 py-1 text-xs text-amber-700 dark:text-amber-400"}>
          {status === "exited"
            ? t("workspace.terminal.state.exitedWith", { code: exitCode === null ? "?" : String(exitCode) })
            : t(`workspace.terminal.stream.${status}`)}
        </p>
      )}
      <div ref={host} className="min-h-0 flex-1 bg-[#0b0f14] p-1" />
    </div>
  );
}
