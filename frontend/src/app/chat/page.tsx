"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useChat } from "@/hooks/use-chat";
import { Sidebar } from "@/components/sidebar";
import { MessageBubble } from "@/components/message-bubble";
import { ChatInput } from "@/components/chat-input";
import { Welcome } from "@/components/welcome";
import { ChatMessage } from "@/lib/types";

const API = typeof window !== "undefined" && window.location.port === "3000"
  ? `${window.location.protocol}//${window.location.hostname}:8000` : "";

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
  const { messages, streaming, pendingRoute, sessionId, send, confirmRoute, stop, reset, feedback, executeItems, selectSession } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null);

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
        {/* Header with export/share */}
        {messages.length > 0 && (
          <div className="flex items-center justify-end gap-2 px-4 py-2 border-b border-border text-xs">
            <button onClick={() => exportMarkdown(messages)} className="text-muted-foreground hover:text-foreground transition-colors">
              📥 Export
            </button>
            <button onClick={handleShare} className="text-muted-foreground hover:text-foreground transition-colors">
              🔗 Share
            </button>
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
                  onExecuteItems={executeItems}
                  pendingRoute={pendingRoute}
                />
              ))}
              <div ref={bottomRef} />
            </div>
          </div>
        )}

        <ChatInput onSend={send} onStop={stop} streaming={streaming} />
      </main>
    </div>
  );
}
