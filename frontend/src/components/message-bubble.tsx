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
  onConfirmTool?: (approved: boolean) => void;
  onExecuteItems?: (items: string[]) => void;
  onApproveAll?: () => void;
  pendingRoute?: string | null;
}

/** Extract a short, readable summary from tool call content */
function summarizeToolCall(content: string): string {
  const writeMatch = content.match(/^(write_file)\s*\(\s*path\s*=\s*['"]([^'"]+)['"]/);
  if (writeMatch) {
    const path = writeMatch[2];
    const name = path.split("/").pop() ?? path;
    return `📝 Writing ${name}`;
  }
  const patchMatch = content.match(/^(patch_file)\s*\(\s*path\s*=\s*['"]([^'"]+)['"]/);
  if (patchMatch) {
    const path = patchMatch[2];
    const name = path.split("/").pop() ?? path;
    return `✏️ Patching ${name}`;
  }
  const shellMatch = content.match(/^(shell)\s*\(\s*command\s*=\s*['"](.{0,60})/);
  if (shellMatch) {
    const cmd = shellMatch[2].replace(/['"]\s*\)?\s*$/, "");
    return `💻 ${cmd}${cmd.length >= 60 ? "…" : ""}`;
  }
  const readMatch = content.match(/^(read_file)\s*\(\s*path\s*=\s*['"]([^'"]+)['"]/);
  if (readMatch) {
    const path = readMatch[2];
    const name = path.split("/").pop() ?? path;
    return `📖 Reading ${name}`;
  }
  const genericMatch = content.match(/^(\w+)\s*\(/);
  if (genericMatch) return `🔧 ${genericMatch[1]}`;
  if (content.length > 80) return content.slice(0, 80) + "…";
  return content;
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
                <input type="checkbox" checked={checked.has(i)} onChange={() => toggle(i)} className="mt-1 accent-emerald-500" />
                <span className={checked.has(i) ? "" : "text-muted-foreground"}>{item}</span>
              </label>
            ))}
          </div>
          {checked.size > 0 && onExecuteItems && (
            <Button size="sm" className="mt-3" onClick={() => onExecuteItems(actionLines.filter((_, i) => checked.has(i)))}>
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

function ItemProgress({ content }: { content: string }) {
  const match = content.match(/###\s*\[(\d+)\/(\d+)\]\s*(.*)/);
  if (!match) return null;
  const [, current, total, title] = match;
  const cur = parseInt(current), tot = parseInt(total);
  const pct = Math.round((cur / tot) * 100);
  return (
    <div className="max-w-3xl w-full mx-auto mt-3">
      <div className="flex items-center gap-3 px-4 py-2 rounded-lg border border-cyan-500/30 bg-cyan-500/5 text-sm">
        <span className="text-cyan-400 font-mono text-xs font-semibold">[{current}/{total}]</span>
        <span className="flex-1 truncate">{title}</span>
        <div className="w-24 h-1.5 rounded-full bg-muted overflow-hidden">
          <div className="h-full rounded-full bg-cyan-400 transition-all duration-300" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

export function MessageBubble({ message, onFeedback, onConfirmRoute, onConfirmTool, onExecuteItems, onApproveAll, pendingRoute }: MessageBubbleProps) {
  // --- User message ---
  if (message.type === "user") {
    return (
      <div className="max-w-3xl w-full mx-auto mt-4">
        <div className="bg-secondary rounded-2xl px-5 py-3.5 text-sm leading-relaxed whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  // --- Route badge ---
  if (message.type === "route") {
    const route = message.content;
    const label = ROUTE_LABELS[route] ?? route;
    const confirmed = message.confirmed;
    const isPending = !confirmed && pendingRoute === route;
    return (
      <div className="max-w-3xl w-full mx-auto mt-4">
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <div className="flex-1 h-px bg-border" />
          <span className="px-2.5 py-1 rounded-full bg-muted text-[11px] font-medium">{label}</span>
          <div className="flex-1 h-px bg-border" />
        </div>
        {isPending && (
          <div className="flex items-center justify-center gap-2 mt-3">
            <Button size="sm" onClick={() => onConfirmRoute?.(route)}>Proceed</Button>
            {route === "DISCUSS" && (
              <Button size="sm" variant="outline" onClick={() => onConfirmRoute?.("QUICK")}>⚡ Quick instead</Button>
            )}
            {route === "QUICK" && (
              <Button size="sm" variant="outline" onClick={() => onConfirmRoute?.("DISCUSS")}>💬 Discuss instead</Button>
            )}
          </div>
        )}
      </div>
    );
  }

  // --- System message ---
  if (message.type === "system") {
    const isError = message.content.startsWith("⚠");
    return (
      <div className={cn(
        "max-w-3xl w-full mx-auto mt-2 text-center text-xs py-2",
        isError ? "text-red-400 font-medium" : "text-muted-foreground",
      )}>
        {message.content}
      </div>
    );
  }

  // --- Tool call: compact single-line with status icon ---
  if (message.type === "tool_call") {
    const isRunning = message.toolStatus === "running";
    const isDone = message.toolStatus === "done";
    const isError = message.toolStatus === "error";
    const isSkipped = message.toolStatus === "skipped";
    const summary = summarizeToolCall(message.content);

    const icon = isRunning ? "⏳" : isDone ? "✓" : isError ? "✗" : isSkipped ? "⏭" : "•";
    const iconColor = isRunning ? "text-cyan-400" : isDone ? "text-emerald-400" : isError ? "text-red-400" : isSkipped ? "text-muted-foreground" : "text-muted-foreground";

    return (
      <div className="max-w-3xl w-full mx-auto mt-1 first:mt-3">
        <div className={cn(
          "flex items-center gap-2 px-4 py-1.5 text-xs font-mono transition-colors rounded",
          isError && "bg-red-500/5",
          isRunning && "bg-cyan-500/5",
        )}>
          <span className={cn("text-sm shrink-0", iconColor, isRunning && "animate-pulse")}>{icon}</span>
          <span className={cn(
            "truncate",
            isError ? "text-red-400" : isSkipped ? "text-muted-foreground line-through" : "text-muted-foreground",
            isRunning && "text-cyan-400",
          )}>
            {summary}
          </span>
          {isRunning && <span className="ml-auto text-[10px] text-cyan-400 animate-pulse shrink-0">running…</span>}
        </div>
      </div>
    );
  }

  // --- Tool result: collapsed by default, errors shown inline ---
  if (message.type === "tool_result") {
    const isError = message.content.startsWith("ERROR:");
    if (isError) {
      // Show errors prominently
      const errorMsg = message.content.replace(/^ERROR:\s*/, "").split("\n")[0];
      return (
        <div className="max-w-3xl w-full mx-auto mt-0.5">
          <div className="flex items-center gap-2 px-4 py-1.5 rounded bg-red-500/10 border border-red-500/20">
            <span className="text-red-400 text-xs">❌</span>
            <span className="text-xs text-red-400 font-mono truncate">{errorMsg}</span>
            {message.content.includes("\n") && (
              <details className="ml-auto">
                <summary className="text-[10px] text-red-400/60 cursor-pointer hover:text-red-400">details</summary>
                <pre className="mt-1 p-2 rounded bg-red-500/5 text-[11px] font-mono overflow-x-auto max-h-32 overflow-y-auto whitespace-pre-wrap text-red-300">
                  {message.content}
                </pre>
              </details>
            )}
          </div>
        </div>
      );
    }
    // Non-error results: fully collapsed
    return (
      <div className="max-w-3xl w-full mx-auto mt-0.5">
        <details className="group">
          <summary className="text-[11px] text-muted-foreground/50 cursor-pointer hover:text-muted-foreground px-4 py-0.5">
            ▸ output ({message.content.length > 1000 ? `${(message.content.length / 1024).toFixed(1)}KB` : `${message.content.length} chars`})
          </summary>
          <pre className="mx-4 mt-1 p-3 rounded-lg bg-muted/30 text-xs font-mono overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
            {message.content}
          </pre>
        </details>
      </div>
    );
  }

  // --- Confirm: compact with "Approve All" option ---
  if (message.type === "confirm") {
    const summary = summarizeToolCall(message.content);
    if (message.confirmed) {
      // Already confirmed — show as a simple inline note
      return (
        <div className="max-w-3xl w-full mx-auto mt-0.5">
          <div className="flex items-center gap-2 px-4 py-1 text-[11px] text-muted-foreground/60">
            <span>✓ approved</span>
          </div>
        </div>
      );
    }
    return (
      <div className="max-w-3xl w-full mx-auto mt-2">
        <div className={cn(
          "px-4 py-3 rounded-lg border text-sm",
          message.dangerous ? "border-red-500/40 bg-red-500/5" : "border-amber-500/30 bg-amber-500/5",
        )}>
          <div className="flex items-center gap-2 mb-2">
            <span>{message.dangerous ? "🚨" : "⚠️"}</span>
            <span className={cn("font-semibold text-xs", message.dangerous ? "text-red-400" : "text-amber-400")}>
              {summary}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => onConfirmTool?.(true)}>✓ Approve</Button>
            <Button size="sm" variant="outline" onClick={() => onConfirmTool?.(false)}>✕ Reject</Button>
            <Button size="sm" variant="ghost" className="ml-auto text-xs text-muted-foreground" onClick={() => onApproveAll?.()}>
              ✓✓ Approve All
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // --- Agent message ---
  const name = message.agent ?? "agent";
  const colorClass = AGENT_COLORS[name] ?? "text-muted-foreground";
  const dotClass = AGENT_DOT_COLORS[name] ?? "bg-muted-foreground";
  const borderClass = AGENT_BORDER_COLORS[name] ?? "border-l-muted-foreground";

  const progressMatch = name === "executor" ? message.content.match(/###\s*\[(\d+)\/(\d+)\]\s*(.*)/) : null;
  const displayContent = progressMatch
    ? message.content.replace(/###\s*\[\d+\/\d+\]\s*.*\n?/, "").trim()
    : message.content;

  return (
    <div className="max-w-3xl w-full mx-auto mt-4 animate-in fade-in slide-in-from-bottom-1 duration-200">
      {progressMatch && <ItemProgress content={message.content} />}
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
        <MarkdownContent content={displayContent} />
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
