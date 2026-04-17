"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useChat } from "@/hooks/use-chat";
import { Sidebar } from "@/components/sidebar";
import { MessageBubble } from "@/components/message-bubble";
import { ChatInput } from "@/components/chat-input";
import { Welcome } from "@/components/welcome";
import { ArtifactsPanel } from "@/components/artifacts-panel";
import { ChatMessage } from "@/lib/types";

import { getApiBase, setAutoApprove, getAutoApprove } from "@/lib/api";

const API = typeof window !== "undefined" ? getApiBase() : "";

function exportMarkdown(messages: ChatMessage[]) {
  const lines = messages.map((m) => {
    if (m.type === "user") return `**You:**\n${m.content}\n`;
    if (m.type === "agent") return `**${m.agent}** (${m.role}):\n${m.content}\n`;
    if (m.type === "route") return `---\n*Route: ${m.content}*\n---`;
    return "";
  });
  const md = `# Agora Conversation\n\n${lines.join("\n")}`;
  const blob = new Blob([md], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "agora-conversation.md";
  a.click();
  URL.revokeObjectURL(url);
}

export default function ChatPage() {
  const { messages, streaming, pendingRoute, sessionId, artifacts, send, confirmRoute, confirmTool, stop, reset, feedback, executeItems, selectSession } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [artifactsOpen, setArtifactsOpen] = useState(false);

  // Auto-open artifacts panel when first artifact is created
  useEffect(() => {
    if (artifacts.length > 0 && !artifactsOpen) setArtifactsOpen(true);
  }, [artifacts.length]);
  const [autoApproveOn, setAutoApproveOn] = useState(false);

  useEffect(() => {
    getAutoApprove().then(setAutoApproveOn).catch(() => {});
  }, []);

  const handleApproveAll = useCallback(async () => {
    // First approve the current pending confirmation, then enable auto-approve
    confirmTool(true);
    await setAutoApprove(true);
    setAutoApproveOn(true);
  }, [confirmTool]);

  const handleToggleAutoApprove = useCallback(async () => {
    const next = !autoApproveOn;
    await setAutoApprove(next);
    setAutoApproveOn(next);
  }, [autoApproveOn]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleShare = useCallback(async () => {
    if (messages.length === 0) return;
    const res = await fetch(`${API}/api/chat/share`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    const data = await res.json();
    const url = `${window.location.origin}/shared?id=${data.id}`;
    setShareUrl(url);
    navigator.clipboard.writeText(url).catch(() => {});
    setTimeout(() => setShareUrl(null), 4000);
  }, [messages]);

  return (
    <div className="flex h-screen">
      <Sidebar onReset={reset} currentSessionId={sessionId} onSelectSession={selectSession} />

      <main className="flex-1 flex flex-col min-w-0 h-screen">
        {/* Header with export/share/auto-approve */}
        {messages.length > 0 && (
          <div className="flex items-center justify-end gap-3 px-4 py-2 border-b border-border text-xs">
            <button
              onClick={handleToggleAutoApprove}
              className={`flex items-center gap-1.5 transition-colors ${autoApproveOn ? "text-emerald-400" : "text-muted-foreground hover:text-foreground"}`}
            >
              <span className={`inline-block w-7 h-4 rounded-full relative transition-colors ${autoApproveOn ? "bg-emerald-500" : "bg-muted"}`}>
                <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${autoApproveOn ? "left-3.5" : "left-0.5"}`} />
              </span>
              Auto-approve
            </button>
            <span className="w-px h-3 bg-border" />
            <button onClick={() => exportMarkdown(messages)} className="text-muted-foreground hover:text-foreground transition-colors">
              📥 Export
            </button>
            <button onClick={handleShare} className="text-muted-foreground hover:text-foreground transition-colors">
              🔗 Share
            </button>
            {artifacts.length > 0 && (
              <button onClick={() => setArtifactsOpen(!artifactsOpen)} className="text-muted-foreground hover:text-foreground transition-colors">
                📁 Files ({artifacts.length})
              </button>
            )}
            {shareUrl && <span className="text-emerald-400">✓ Link copied!</span>}
          </div>
        )}

        {messages.length === 0 ? (
          <Welcome onExample={send} />
        ) : (
          <div className="flex-1 overflow-y-auto">
            <div className="py-6 px-4">
              {messages.map((m) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  onFeedback={feedback}
                  onConfirmRoute={confirmRoute}
                  onConfirmTool={confirmTool}
                  onExecuteItems={executeItems}
                  onApproveAll={handleApproveAll}
                  pendingRoute={pendingRoute}
                />
              ))}
              {streaming && (() => {
                const last = messages[messages.length - 1];
                const hasActiveAgent = last?.type === "agent" && last.streaming;
                if (hasActiveAgent) return null;
                const isExecuting = last?.type === "tool_call" || last?.type === "tool_result" || (last?.type === "route" && last.confirmed);
                return (
                  <div className="max-w-3xl w-full mx-auto mt-4">
                    <div className="flex items-center gap-2 px-4 py-3 text-sm text-muted-foreground">
                      <span className="flex gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:0ms]" />
                        <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:150ms]" />
                        <span className="w-1.5 h-1.5 rounded-full bg-current animate-bounce [animation-delay:300ms]" />
                      </span>
                      <span className="text-xs">{isExecuting ? "Executing…" : "Thinking…"}</span>
                    </div>
                  </div>
                );
              })()}
              <div ref={bottomRef} />
            </div>
          </div>
        )}

        <ChatInput onSend={send} onStop={stop} streaming={streaming} />
      </main>

      <ArtifactsPanel artifacts={artifacts} open={artifactsOpen} onClose={() => setArtifactsOpen(false)} />
    </div>
  );
}
