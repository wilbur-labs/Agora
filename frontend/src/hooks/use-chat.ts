"use client";

import { useCallback, useRef, useState } from "react";
import { ChatMessage } from "@/lib/types";
import { streamChat, streamContinue, resetChat, sendFeedback, createSession, saveSession, loadSession, SSECallbacks } from "@/lib/api";

let msgId = 0;
function nextId() {
  return `msg-${++msgId}`;
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [pendingRoute, setPendingRoute] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bufferRef = useRef<Map<string, string>>(new Map());
  const rafRef = useRef<number>(0);
  const lastConfirmedRouteRef = useRef<string | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-save messages to session (debounced)
  const autoSave = useCallback((msgs: ChatMessage[], sid: string | null) => {
    if (!sid) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      saveSession(sid, msgs).catch(() => {});
    }, 1000);
  }, []);

  const flushBuffer = useCallback(() => {
    rafRef.current = 0;
    const buf = bufferRef.current;
    if (buf.size === 0) return;
    const updates = new Map(buf);
    buf.clear();
    setMessages((prev) => {
      let next = prev;
      for (const [agent, content] of updates) {
        const idx = next.findLastIndex(
          (m) => m.type === "agent" && m.agent === agent && m.streaming,
        );
        if (idx >= 0) {
          next = next === prev ? [...next] : next;
          next[idx] = { ...next[idx], content: next[idx].content + content };
        }
      }
      return next;
    });
  }, []);

  const makeCallbacks = useCallback(
    (opts?: { onRoute?: (route: string) => void }): SSECallbacks => ({
      onToken(agent, role, content) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.type === "agent" && last.agent === agent && last.streaming) {
            const buf = bufferRef.current;
            buf.set(agent, (buf.get(agent) ?? "") + content);
            if (!rafRef.current) rafRef.current = requestAnimationFrame(flushBuffer);
            return prev;
          }
          return [...prev, { id: nextId(), type: "agent", agent, role, content, streaming: true }];
        });
      },
      onAgentDone(agent) {
        const pending = bufferRef.current.get(agent);
        bufferRef.current.delete(agent);
        setMessages((prev) =>
          prev.map((m) =>
            m.type === "agent" && m.agent === agent && m.streaming
              ? { ...m, content: m.content + (pending ?? ""), streaming: false }
              : m,
          ),
        );
      },
      onToolCall(content) {
        setMessages((prev) => [
          ...prev,
          { id: nextId(), type: "tool_call", content, toolStatus: "running" },
        ]);
      },
      onToolResult(content) {
        setMessages((prev) => {
          // Update the last tool_call with result
          const idx = prev.findLastIndex((m) => m.type === "tool_call" && m.toolStatus === "running");
          if (idx >= 0) {
            const next = [...prev];
            next.splice(idx + 1, 0, { id: nextId(), type: "tool_result", content, toolStatus: "done" });
            next[idx] = { ...next[idx], toolStatus: "done" };
            return next;
          }
          return [...prev, { id: nextId(), type: "tool_result", content, toolStatus: "done" }];
        });
      },
      onToolSkipped(content) {
        setMessages((prev) => {
          const idx = prev.findLastIndex((m) => m.type === "tool_call" && m.toolStatus === "running");
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = { ...next[idx], toolStatus: "skipped" };
            return next;
          }
          return prev;
        });
      },
      onRoute: opts?.onRoute,
      onDone() {
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        bufferRef.current.clear();
        setMessages((prev) => {
          const updated = prev.map((m) => (m.streaming ? { ...m, streaming: false } : m));
          // Auto-save after streaming completes
          autoSave(updated, sessionIdRef.current);
          return updated;
        });
        setStreaming(false);
      },
      onError(error) {
        setMessages((prev) => [...prev, { id: nextId(), type: "system", content: `Error: ${error}` }]);
        setStreaming(false);
      },
    }),
    [flushBuffer],
  );

  /** Phase 1: send user message → moderator routes */
  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || streaming) return;

      // Create session on first message
      if (!sessionIdRef.current) {
        const title = text.slice(0, 60);
        const sid = await createSession(title, []);
        sessionIdRef.current = sid;
        setSessionId(sid);
      }

      setMessages((prev) => [...prev, { id: nextId(), type: "user", content: text }]);
      setStreaming(true);
      setPendingRoute(null);

      // If we already confirmed a route before, auto-continue with same route
      const prevRoute = lastConfirmedRouteRef.current;

      const cb = makeCallbacks({
        onRoute(route) {
          if (prevRoute) {
            // Auto-continue: show route badge as confirmed, then immediately continue
            setMessages((prev) => [...prev, { id: nextId(), type: "route", content: route, confirmed: true }]);
            // Continue with the same route type
            abortRef.current = streamContinue(route, makeCallbacks());
            lastConfirmedRouteRef.current = route;
          } else {
            // First time: pause for user confirmation
            setMessages((prev) => [...prev, { id: nextId(), type: "route", content: route }]);
            setPendingRoute(route);
            setStreaming(false);
          }
        },
      });
      cb.onDone = () => {
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        bufferRef.current.clear();
        setMessages((prev) => prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)));
        if (!prevRoute) setStreaming(false);
      };

      abortRef.current = streamChat(text, cb);
    },
    [streaming, makeCallbacks],
  );

  /** Phase 2: user confirms or overrides route */
  const confirmRoute = useCallback(
    (route: string) => {
      setPendingRoute(null);
      setStreaming(true);
      lastConfirmedRouteRef.current = route;
      setMessages((prev) =>
        prev.map((m) => (m.type === "route" && !m.confirmed ? { ...m, confirmed: true, content: route } : m)),
      );
      abortRef.current = streamContinue(route, makeCallbacks());
    },
    [makeCallbacks],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    bufferRef.current.clear();
    setMessages((prev) => prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)));
    setStreaming(false);
    setPendingRoute(null);
  }, []);

  const reset = useCallback(async () => {
    abortRef.current?.abort();
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    bufferRef.current.clear();
    await resetChat();
    setMessages([]);
    setStreaming(false);
    setPendingRoute(null);
    lastConfirmedRouteRef.current = null;
    sessionIdRef.current = null;
    setSessionId(null);
  }, []);

  const feedback = useCallback(async (messageId: string, rating: "up" | "down") => {
    await sendFeedback(messageId, rating);
    setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, feedback: rating } : m)));
  }, []);

  const executeItems = useCallback(
    (items: string[]) => {
      if (streaming || items.length === 0) return;
      const task = "Execute the following action items:\n" + items.map((it) => `- ${it}`).join("\n");
      send(task);
    },
    [streaming, send],
  );

  const selectSession = useCallback(async (sid: string) => {
    abortRef.current?.abort();
    await resetChat();
    const data = await loadSession(sid);
    setMessages(data.messages as ChatMessage[]);
    sessionIdRef.current = sid;
    setSessionId(sid);
    setStreaming(false);
    setPendingRoute(null);
    lastConfirmedRouteRef.current = null;
  }, []);

  return { messages, streaming, pendingRoute, sessionId, send, confirmRoute, stop, reset, feedback, executeItems, selectSession };
}
