import { Agent } from "@/lib/types";

export function getApiBase(): string {
  if (typeof window === "undefined") return "";
  if (window.location.port === "3000") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "";
}

export async function fetchAgents(): Promise<Agent[]> {
  const res = await fetch(`${getApiBase()}/api/agents`);
  return (await res.json()).agents;
}

export async function fetchAvailableAgents(): Promise<Agent[]> {
  const res = await fetch(`${getApiBase()}/api/agents/available`);
  return (await res.json()).agents;
}

export async function setActiveAgents(names: string[]): Promise<Agent[]> {
  const res = await fetch(`${getApiBase()}/api/agents/active`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agents: names }),
  });
  return (await res.json()).agents;
}

export async function resetChat(): Promise<void> {
  await fetch(`${getApiBase()}/api/chat/reset`, { method: "POST" });
}

export async function restoreContext(messages: object[]): Promise<void> {
  await fetch(`${getApiBase()}/api/chat/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
}

// Sessions API
export async function fetchSessions(): Promise<{ id: string; title: string; created_at: string }[]> {
  const res = await fetch(`${getApiBase()}/api/sessions`);
  return (await res.json()).sessions;
}

export async function createSession(title: string, messages: object[]): Promise<string> {
  const res = await fetch(`${getApiBase()}/api/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, messages }),
  });
  return (await res.json()).id;
}

export async function loadSession(sid: string): Promise<{ id: string; title: string; messages: object[] }> {
  const res = await fetch(`${getApiBase()}/api/sessions/${sid}`);
  return res.json();
}

export async function saveSession(sid: string, messages: object[], title?: string): Promise<void> {
  await fetch(`${getApiBase()}/api/sessions/${sid}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, title }),
  });
}

export async function deleteSession(sid: string): Promise<void> {
  await fetch(`${getApiBase()}/api/sessions/${sid}`, { method: "DELETE" });
}

export async function sendFeedback(messageId: string, rating: "up" | "down"): Promise<void> {
  await fetch(`${getApiBase()}/api/chat/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId, rating }),
  });
}

export async function respondConfirm(approved: boolean): Promise<void> {
  await fetch(`${getApiBase()}/api/chat/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  });
}

export async function setAutoApprove(enabled: boolean): Promise<void> {
  await fetch(`${getApiBase()}/api/chat/auto-approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export async function getAutoApprove(): Promise<boolean> {
  const res = await fetch(`${getApiBase()}/api/chat/auto-approve`);
  const data = await res.json();
  return data.auto_approve;
}

export type SSECallbacks = {
  onToken: (agent: string, role: string, content: string) => void;
  onAgentDone: (agent: string, role: string) => void;
  onRoute?: (route: string) => void;
  onDone?: (route: string) => void;
  onError: (error: string) => void;
  onToolCall?: (content: string) => void;
  onToolResult?: (content: string) => void;
  onToolSkipped?: (content: string) => void;
  onConfirm?: (content: string) => void;
};

function parseSSEStream(
  url: string,
  body: object,
  cb: SSECallbacks,
  signal: AbortSignal,
) {
  (async () => {
    try {
      const res = await fetch(`${getApiBase()}${url}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
      });
      if (!res.ok || !res.body) { cb.onError(`HTTP ${res.status}`); return; }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventType = "message";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const raw = line.slice(5).trim();
            if (!raw) continue;
            try {
              const data = JSON.parse(raw);
              switch (eventType) {
                case "token": cb.onToken(data.agent, data.role, data.content); break;
                case "agent_done": cb.onAgentDone(data.agent, data.role); break;
                case "route": cb.onRoute?.(data.route); break;
                case "done": cb.onDone?.(data.route ?? ""); break;
                case "tool_call": cb.onToolCall?.(data.content); break;
                case "tool_result": cb.onToolResult?.(data.content); break;
                case "tool_skipped": cb.onToolSkipped?.(data.content); break;
                case "confirm": cb.onConfirm?.(data.content); break;
                case "error": cb.onError(data.content ?? "Unknown error"); break;
              }
            } catch { /* skip */ }
            eventType = "message";
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") cb.onError((err as Error).message);
    }
  })();
}

/** Phase 1: send message, get moderator routing only */
export function streamChat(message: string, cb: SSECallbacks): AbortController {
  const controller = new AbortController();
  parseSSEStream("/api/chat", { message }, cb, controller.signal);
  return controller;
}

/** Phase 2: user confirmed route, execute it */
export function streamContinue(route: string, cb: SSECallbacks): AbortController {
  const controller = new AbortController();
  parseSSEStream("/api/chat/continue", { route }, cb, controller.signal);
  return controller;
}
