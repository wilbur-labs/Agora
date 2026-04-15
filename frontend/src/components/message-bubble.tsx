"use client";

import { useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  ChatMessage,
  AGENT_COLORS,
  AGENT_DOT_COLORS,
  AGENT_BORDER_COLORS,
  ROUTE_LABELS,
} from "@/lib/types";

interface MessageBubbleProps {
  message: ChatMessage;
  onFeedback?: (id: string, rating: "up" | "down") => void;
  onConfirmRoute?: (route: string) => void;
  onExecuteItems?: (items: string[]) => void;
  pendingRoute?: string | null;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [text]);
  return (
    <button
      onClick={copy}
      className="absolute top-2 right-2 text-[10px] px-1.5 py-0.5 rounded bg-muted/80 text-muted-foreground hover:text-foreground transition-colors opacity-0 group-hover:opacity-100"
    >
      {copied ? "✓" : "Copy"}
    </button>
  );
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        pre({ children, ...props }) {
          const codeEl = children as React.ReactElement<{ children?: string }>;
          const code = String(codeEl?.props?.children ?? "");
          return (
            <div className="relative group my-3">
              <CopyButton text={code} />
              <pre className="overflow-x-auto rounded-lg bg-muted/50 p-4 text-[13px] leading-relaxed" {...props}>
                {children}
              </pre>
            </div>
          );
        },
        code({ className, children, ...props }) {
          const isBlock = className?.startsWith("hljs") || className?.startsWith("language-");
          if (isBlock) {
            return <code className={className} {...props}>{children}</code>;
          }
          return (
            <code className="rounded bg-muted px-1.5 py-0.5 text-[13px] font-mono" {...props}>
              {children}
            </code>
          );
        },
        table({ children, ...props }) {
          return (
            <div className="overflow-x-auto my-3">
              <table className="min-w-full text-sm border-collapse" {...props}>{children}</table>
            </div>
          );
        },
        th({ children, ...props }) {
          return <th className="border border-border px-3 py-1.5 bg-muted/50 text-left font-medium" {...props}>{children}</th>;
        },
        td({ children, ...props }) {
          return <td className="border border-border px-3 py-1.5" {...props}>{children}</td>;
        },
        a({ children, href, ...props }) {
          return <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2" {...props}>{children}</a>;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function SynthesizerHighlights({ content, onExecuteItems }: { content: string; onExecuteItems?: (items: string[]) => void }) {
  const questionsMatch = content.match(/(?:^|\n)#+\s*Open Questions?\s*\n([\s\S]*?)(?=\n#+\s|\n*$)/i);
  const actionsMatch = content.match(/(?:^|\n)#+\s*Action Items?\s*\n([\s\S]*?)(?=\n#+\s|\n*$)/i);

  const questions = questionsMatch?.[1]?.trim();
  const actionsRaw = actionsMatch?.[1]?.trim();

  // Parse action items: lines matching - [ ] or - [x]
  const actionLines = actionsRaw
    ? actionsRaw.split("\n").filter((l) => /^\s*-\s*\[[ x]\]/.test(l)).map((l) => l.replace(/^\s*-\s*\[[ x]\]\s*/, "").trim())
    : [];

  const [checked, setChecked] = useState<Set<number>>(new Set());

  const toggle = useCallback((i: number) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  }, []);

  if (!questions && actionLines.length === 0) return null;

  return (
    <div className="mt-3 space-y-2">
      {actionLines.length > 0 && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-4 py-3">
          <div className="text-xs font-semibold text-emerald-400 mb-2">📋 Action Items</div>
          <div className="space-y-1.5">
            {actionLines.map((item, i) => (
              <label key={i} className="flex items-start gap-2 text-sm cursor-pointer hover:bg-emerald-500/5 rounded px-1 py-0.5">
                <input
                  type="checkbox"
                  checked={checked.has(i)}
                  onChange={() => toggle(i)}
                  className="mt-1 accent-emerald-500"
                />
                <span className={checked.has(i) ? "" : "text-muted-foreground"}>{item}</span>
              </label>
            ))}
          </div>
          {checked.size > 0 && onExecuteItems && (
            <Button
              size="sm"
              className="mt-3"
              onClick={() => onExecuteItems(actionLines.filter((_, i) => checked.has(i)))}
            >
              ▶ Execute Selected ({checked.size})
            </Button>
          )}
        </div>
      )}
      {questions && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3">
          <div className="text-xs font-semibold text-amber-400 mb-2">❓ Questions for You</div>
          <div className="text-sm leading-relaxed whitespace-pre-wrap">{questions}</div>
        </div>
      )}
    </div>
  );
}

export function MessageBubble({ message, onFeedback, onConfirmRoute, onExecuteItems, pendingRoute }: MessageBubbleProps) {
  if (message.type === "user") {
    return (
      <div className="max-w-3xl w-full mx-auto mt-4">
        <div className="bg-secondary rounded-2xl px-5 py-3.5 text-sm leading-relaxed whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  if (message.type === "route") {
    const route = message.content;
    const label = ROUTE_LABELS[route] ?? route;
    const confirmed = message.confirmed;
    const isPending = !confirmed && pendingRoute === route;

    return (
      <div className="max-w-3xl w-full mx-auto mt-4">
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <div className="flex-1 h-px bg-border" />
          <span className="px-2.5 py-1 rounded-full bg-muted text-[11px] font-medium">
            {label}
          </span>
          <div className="flex-1 h-px bg-border" />
        </div>
        {isPending && (
          <div className="flex items-center justify-center gap-2 mt-3">
            <Button size="sm" onClick={() => onConfirmRoute?.(route)}>
              Proceed
            </Button>
            {route === "DISCUSS" && (
              <Button size="sm" variant="outline" onClick={() => onConfirmRoute?.("QUICK")}>
                ⚡ Quick instead
              </Button>
            )}
            {route === "QUICK" && (
              <Button size="sm" variant="outline" onClick={() => onConfirmRoute?.("DISCUSS")}>
                💬 Discuss instead
              </Button>
            )}
          </div>
        )}
      </div>
    );
  }

  if (message.type === "system") {
    return (
      <div className="max-w-3xl w-full mx-auto mt-2 text-center text-xs text-muted-foreground py-2">
        {message.content}
      </div>
    );
  }

  if (message.type === "tool_call") {
    const isDangerous = message.content.startsWith("shell(");
    return (
      <div className="max-w-3xl w-full mx-auto mt-2">
        <div className={cn(
          "flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-mono border",
          isDangerous ? "border-amber-500/30 bg-amber-500/5" : "border-border bg-muted/30",
          message.toolStatus === "skipped" && "opacity-50 line-through",
        )}>
          <span>{message.toolStatus === "running" ? "⏳" : message.toolStatus === "done" ? "✅" : message.toolStatus === "skipped" ? "⏭️" : "🔧"}</span>
          <span className={isDangerous ? "text-amber-400" : "text-muted-foreground"}>
            🔧 {message.content}
          </span>
          {isDangerous && <span className="text-amber-400 text-[10px] ml-auto">⚠ shell</span>}
        </div>
      </div>
    );
  }

  if (message.type === "tool_result") {
    return (
      <div className="max-w-3xl w-full mx-auto mt-1">
        <details className="group">
          <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground px-4 py-1">
            ▸ Output ({message.content.length} chars)
          </summary>
          <pre className="mx-4 mt-1 p-3 rounded-lg bg-muted/30 text-xs font-mono overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
            {message.content}
          </pre>
        </details>
      </div>
    );
  }

  const name = message.agent ?? "agent";
  const colorClass = AGENT_COLORS[name] ?? "text-muted-foreground";
  const dotClass = AGENT_DOT_COLORS[name] ?? "bg-muted-foreground";
  const borderClass = AGENT_BORDER_COLORS[name] ?? "border-l-muted-foreground";

  return (
    <div className="max-w-3xl w-full mx-auto mt-4 animate-in fade-in slide-in-from-bottom-1 duration-200">
      <div className="flex items-center gap-2 px-1 mb-1.5">
        <span className={cn("w-2 h-2 rounded-full", dotClass)} />
        <span className={cn("font-semibold text-[13px]", colorClass)}>{name}</span>
        <span className="text-[11px] text-muted-foreground">{message.role}</span>
      </div>
      <div
        className={cn(
          "bg-card rounded-xl px-5 py-3.5 text-sm leading-[1.7] break-words border-l-[3px]",
          "[&_h1]:text-lg [&_h1]:font-bold [&_h1]:mt-4 [&_h1]:mb-2",
          "[&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1.5",
          "[&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1",
          "[&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5",
          "[&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5",
          "[&_hr]:my-3 [&_hr]:border-border",
          "[&_blockquote]:my-2 [&_blockquote]:pl-3 [&_blockquote]:border-l-2 [&_blockquote]:border-primary/50 [&_blockquote]:text-muted-foreground",
          "[&_strong]:font-semibold",
          borderClass,
        )}
      >
        <MarkdownContent content={message.content} />
        {message.streaming && (
          <span className="inline-block w-[2px] h-4 bg-primary ml-0.5 animate-pulse align-text-bottom" />
        )}
      </div>
      {!message.streaming && name === "synthesizer" && (
        <SynthesizerHighlights content={message.content} onExecuteItems={onExecuteItems} />
      )}
      {!message.streaming && name !== "moderator" && (
        <div className="flex items-center gap-1 mt-1 px-1">
          <button
            onClick={() => onFeedback?.(message.id, "up")}
            className={cn(
              "text-xs px-1.5 py-0.5 rounded hover:bg-muted transition-colors",
              message.feedback === "up" ? "text-emerald-400" : "text-muted-foreground/50 hover:text-muted-foreground",
            )}
            aria-label="Helpful"
          >
            👍
          </button>
          <button
            onClick={() => onFeedback?.(message.id, "down")}
            className={cn(
              "text-xs px-1.5 py-0.5 rounded hover:bg-muted transition-colors",
              message.feedback === "down" ? "text-red-400" : "text-muted-foreground/50 hover:text-muted-foreground",
            )}
            aria-label="Not helpful"
          >
            👎
          </button>
        </div>
      )}
    </div>
  );
}
