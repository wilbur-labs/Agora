"use client";

import { useCallback, useEffect, useRef } from "react";
import { TERMINAL_RUN_STATES, type RunSummary } from "@/lib/execution";

export function useRunNotifications(enabled: boolean) {
  const previousRef = useRef<Map<string, RunSummary> | null>(null);
  const seenRef = useRef(new Set<string>());
  const enabledRef = useRef(enabled);
  useEffect(() => { enabledRef.current = enabled; }, [enabled]);

  return useCallback((runs: RunSummary[], emit = true) => {
    const next = new Map(runs.map((run) => [run.run_id, run]));
    const previous = previousRef.current;
    previousRef.current = next;
    if (!previous || !emit || !enabledRef.current || typeof Notification === "undefined" || Notification.permission !== "granted") return;

    for (const run of runs) {
      const before = previous.get(run.run_id);
      if (!before || TERMINAL_RUN_STATES.has(before.state) || !TERMINAL_RUN_STATES.has(run.state)) continue;
      const key = `${run.run_id}:${run.version}`;
      if (seenRef.current.has(key)) continue;
      seenRef.current.add(key);
      try {
        new Notification(`Agora run ${run.state.replace("_", " ")}`, {
          body: `${run.adapter} · ${run.project_id} · ${run.task_id}`,
          tag: run.run_id,
        });
      } catch {
        // Notification delivery is best-effort and must never break polling.
      }
    }
  }, []);
}
