"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { MessageBubble } from "@/components/message-bubble";
import { Button } from "@/components/ui/button";
import { ChatMessage } from "@/lib/types";

const API = typeof window !== "undefined" && window.location.port === "3000"
  ? `${window.location.protocol}//${window.location.hostname}:8000` : "";

function SharedContent() {
  const params = useSearchParams();
  const id = params.get("id");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    fetch(`${API}/api/shared/${id}`)
      .then((r) => { if (!r.ok) throw new Error("Not found"); return r.json(); })
      .then((d) => setMessages(d.messages))
      .catch(() => setError("Shared conversation not found."));
  }, [id]);

  if (!id) return <div className="p-8 text-center text-muted-foreground">No share ID provided.</div>;
  if (error) return <div className="p-8 text-center text-muted-foreground">{error}</div>;

  return (
    <div className="min-h-screen">
      <nav className="flex items-center justify-between px-6 py-4 border-b border-border max-w-4xl mx-auto">
        <div className="flex items-center gap-2">
          <span className="text-2xl">🏛</span>
          <span className="text-lg font-bold">Agora</span>
          <span className="text-xs text-muted-foreground ml-2">Shared Conversation</span>
        </div>
        <a href="/chat"><Button size="sm">Try Agora →</Button></a>
      </nav>
      <div className="py-6 px-4 max-w-4xl mx-auto">
        {messages.map((m, i) => (
          <MessageBubble key={m.id ?? i} message={m} />
        ))}
      </div>
      <div className="text-center py-8 border-t border-border">
        <p className="text-muted-foreground text-sm mb-3">Want your own AI council?</p>
        <a href="/chat"><Button>Try Agora yourself →</Button></a>
      </div>
    </div>
  );
}

export default function SharedPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-muted-foreground">Loading...</div>}>
      <SharedContent />
    </Suspense>
  );
}
