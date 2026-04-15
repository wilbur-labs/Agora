"use client";

import { useCallback, useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { fetchSessions, deleteSession } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface SidebarProps {
  onReset: () => void;
  currentSessionId?: string | null;
  onSelectSession?: (id: string) => void;
}

export function Sidebar({ onReset, currentSessionId, onSelectSession }: SidebarProps) {
  const [sessions, setSessions] = useState<{ id: string; title: string; created_at: string }[]>([]);

  useEffect(() => {
    fetchSessions().then(setSessions).catch(() => {});
  }, []);

  useEffect(() => {
    fetchSessions().then(setSessions).catch(() => {});
  }, [currentSessionId]);

  const handleDeleteSession = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await deleteSession(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
  }, []);

  return (
    <aside className="w-60 min-w-60 border-r border-border bg-sidebar flex flex-col h-full max-md:hidden">
      {/* Logo */}
      <div className="p-5 pb-2">
        <div className="flex items-center gap-2.5">
          <span className="text-2xl">🏛</span>
          <h1 className="text-xl font-bold tracking-tight">Agora</h1>
        </div>
        <p className="text-xs text-muted-foreground mt-1">Multi-perspective AI Council</p>
      </div>

      <Separator />

      {/* Session history — takes remaining space, scrolls internally */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="px-4 pt-3 pb-1">
          <h3 className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">History</h3>
        </div>
        <ScrollArea className="flex-1 px-2">
          <div className="space-y-0.5">
            {sessions.length === 0 && (
              <p className="text-xs text-muted-foreground px-3 py-2">No conversations yet</p>
            )}
            {sessions.map((s) => (
              <div
                key={s.id}
                onClick={() => onSelectSession?.(s.id)}
                className={cn(
                  "flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs cursor-pointer transition-colors group",
                  currentSessionId === s.id ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )}
              >
                <span className="truncate flex-1">{s.title || "Untitled"}</span>
                <button
                  onClick={(e) => handleDeleteSession(s.id, e)}
                  className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive text-[10px] shrink-0"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </ScrollArea>
      </div>

      <Separator />

      {/* Bottom menu — always visible, never pushed off screen */}
      <div className="p-3 space-y-1 shrink-0">
        <a href="/agents" className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
          <span>⚙️</span><span>Agents</span>
        </a>
        <a href="/skills" className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
          <span>🧠</span><span>Skills</span>
        </a>
        <a href="/settings" className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
          <span>👤</span><span>Settings</span>
        </a>
        <ThemeToggle />
        <Button variant="outline" size="sm" className="w-full mt-1" onClick={onReset}>
          ↻ New Session
        </Button>
      </div>
    </aside>
  );
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  const isDark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return (
    <button
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
    >
      <span>{isDark ? "☀️" : "🌙"}</span>
      <span>{isDark ? "Light mode" : "Dark mode"}</span>
    </button>
  );
}
