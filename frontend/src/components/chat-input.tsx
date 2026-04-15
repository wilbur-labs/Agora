"use client";

import { useRef, KeyboardEvent } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

interface ChatInputProps {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
}

export function ChatInput({ onSend, onStop, streaming }: ChatInputProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  function handleSend() {
    const text = ref.current?.value.trim();
    if (!text) return;
    onSend(text);
    if (ref.current) {
      ref.current.value = "";
      ref.current.style.height = "auto";
    }
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (streaming) return;
      handleSend();
    }
  }

  function handleInput() {
    if (!ref.current) return;
    ref.current.style.height = "auto";
    ref.current.style.height = Math.min(ref.current.scrollHeight, 160) + "px";
  }

  return (
    <div className="px-6 pb-5 pt-3 max-w-3xl w-full mx-auto">
      <div className="flex items-end gap-2.5">
        <Textarea
          ref={ref}
          placeholder="Ask the council..."
          rows={1}
          className="resize-none min-h-[44px] max-h-[160px] text-sm"
          onKeyDown={handleKeyDown}
          onInput={handleInput}
        />
        {streaming ? (
          <Button size="icon" variant="outline" onClick={onStop} className="shrink-0 h-11 w-11">
            ■
          </Button>
        ) : (
          <Button size="icon" onClick={handleSend} className="shrink-0 h-11 w-11">
            ▶
          </Button>
        )}
      </div>
    </div>
  );
}
