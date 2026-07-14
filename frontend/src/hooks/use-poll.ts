"use client";

import { useCallback, useEffect, useRef } from "react";

export function usePoll(task: () => void | Promise<void>, intervalMs: number, enabled = true) {
  const taskRef = useRef(task);
  const busyRef = useRef(false);
  taskRef.current = task;

  const refresh = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try { await taskRef.current(); }
    finally { busyRef.current = false; }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let timer: number | undefined;
    const start = () => {
      if (timer === undefined) timer = window.setInterval(() => void refresh(), intervalMs);
    };
    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer);
      timer = undefined;
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else { void refresh(); start(); }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [enabled, intervalMs, refresh]);

  return refresh;
}
