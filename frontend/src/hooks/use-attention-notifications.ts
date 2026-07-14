"use client";

import { useCallback, useRef } from "react";
import type { AttentionItem } from "@/lib/attention";

export function useAttentionNotifications(enabled: boolean) {
  const known = useRef<Set<string> | null>(null);
  return useCallback((items: AttentionItem[]) => {
    const open = items.filter((item) => item.state === "open");
    const next = new Set(open.map((item) => item.item_id));
    if (known.current && enabled && typeof Notification !== "undefined" && Notification.permission === "granted") {
      for (const item of open) {
        if (!known.current.has(item.item_id)) {
          new Notification(`Agora: ${item.kind} — ${item.title}`, {
            body: `${item.requester} · ${item.project_id}`, tag: item.item_id,
          });
        }
      }
    }
    known.current = next;
  }, [enabled]);
}
