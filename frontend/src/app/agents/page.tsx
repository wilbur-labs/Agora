"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AGENT_DOT_COLORS } from "@/lib/types";
import { cn } from "@/lib/utils";

import { getApiBase } from "@/lib/api";

const API = typeof window !== "undefined" ? getApiBase() : "";

interface AgentInfo {
  name: string;
  role: string;
  perspective?: string;
  active?: boolean;
  profile?: string;
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [selected, setSelected] = useState<AgentInfo | null>(null);
  const [activeNames, setActiveNames] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editRole, setEditRole] = useState("");
  const [editPrompt, setEditPrompt] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("");
  const [newPrompt, setNewPrompt] = useState("");
  const [testQ, setTestQ] = useState("");
  const [testResult, setTestResult] = useState("");
  const [testing, setTesting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [avail, active] = await Promise.all([
        fetch(`${API}/api/agents/available`).then((r) => r.json()),
        fetch(`${API}/api/agents`).then((r) => r.json()),
      ]);
      setAgents(avail.agents);
      setActiveNames(active.agents.map((a: AgentInfo) => a.name));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const selectAgent = useCallback(async (name: string) => {
    const res = await fetch(`${API}/api/agents/${name}`);
    const data = await res.json();
    setSelected(data);
    setEditRole(data.role);
    setEditPrompt(data.perspective);
    setCreating(false);
    setTestResult("");
  }, []);

  const saveAgent = useCallback(async () => {
    if (!selected) return;
    await fetch(`${API}/api/agents/${selected.name}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: editRole, perspective: editPrompt }),
    });
    await load();
    setSelected((s) => s ? { ...s, role: editRole, perspective: editPrompt } : s);
  }, [selected, editRole, editPrompt, load]);

  const deleteAgent = useCallback(async () => {
    if (!selected || !confirm(`Delete agent "${selected.name}"?`)) return;
    await fetch(`${API}/api/agents/${selected.name}`, { method: "DELETE" });
    setSelected(null);
    await load();
  }, [selected, load]);

  const toggleActive = useCallback(async (name: string) => {
    const next = activeNames.includes(name)
      ? activeNames.filter((n) => n !== name)
      : [...activeNames, name];
    if (next.length === 0) return;
    const res = await fetch(`${API}/api/agents/active`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agents: next }),
    });
    const data = await res.json();
    setActiveNames(data.agents.map((a: AgentInfo) => a.name));
  }, [activeNames]);

  const createAgent = useCallback(async () => {
    if (!newName.trim() || !newRole.trim()) return;
    await fetch(`${API}/api/agents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName.trim(), role: newRole.trim(), perspective: newPrompt }),
    });
    setCreating(false);
    setNewName("");
    setNewRole("");
    setNewPrompt("");
    await load();
  }, [newName, newRole, newPrompt, load]);

  const runTest = useCallback(async () => {
    if (!selected || !testQ.trim() || testing) return;
    setTesting(true);
    setTestResult("");
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await fetch(`${API}/api/agents/${selected.name}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: testQ }),
        signal: controller.signal,
      });
      if (!res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("data:")) {
            const raw = line.slice(5).trim();
            if (!raw) continue;
            try {
              const d = JSON.parse(raw);
              if (d.content) setTestResult((p) => p + d.content);
            } catch { /* skip */ }
          }
        }
      }
    } catch { /* abort */ }
    setTesting(false);
  }, [selected, testQ, testing]);

  return (
    <div className="flex h-screen">
      {/* Left: agent list */}
      <aside className="w-64 min-w-64 border-r border-border bg-sidebar flex flex-col h-full">
        <div className="p-5 pb-2">
          <div className="flex items-center gap-2.5">
            <a href="/" className="text-2xl">🏛</a>
            <h1 className="text-lg font-bold">Agents</h1>
          </div>
        </div>
        <Separator />
        <ScrollArea className="flex-1 p-2">
          <div className="space-y-0.5">
            {loading && <p className="text-xs text-muted-foreground p-3">Loading agents…</p>}
            {error && <p className="text-xs text-red-400 p-3">Error: {error}</p>}
            {!loading && !error && agents.map((a) => (
              <div
                key={a.name}
                onClick={() => selectAgent(a.name)}
                className={cn(
                  "flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm cursor-pointer transition-colors",
                  selected?.name === a.name ? "bg-accent" : "hover:bg-accent/50",
                )}
              >
                <span className={cn("w-2 h-2 rounded-full shrink-0", AGENT_DOT_COLORS[a.name] ?? "bg-muted-foreground")} />
                <span className="font-medium flex-1">{a.name}</span>
                {activeNames.includes(a.name) && <span className="text-[10px] text-primary">●</span>}
              </div>
            ))}
          </div>
        </ScrollArea>
        <div className="p-3 space-y-2">
          <Button size="sm" className="w-full" onClick={() => { setCreating(true); setSelected(null); }}>
            + New Agent
          </Button>
          <a href="/chat" className="flex items-center justify-center w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
            ← Back to Chat
          </a>
        </div>
      </aside>

      {/* Right: detail / create */}
      <main className="flex-1 flex flex-col min-w-0 p-6 overflow-y-auto">
        {creating ? (
          <div className="max-w-2xl space-y-4">
            <h2 className="text-xl font-bold">Create New Agent</h2>
            <div>
              <label className="text-sm font-medium">Name</label>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm"
                placeholder="e.g. devops"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Role</label>
              <input
                value={newRole}
                onChange={(e) => setNewRole(e.target.value)}
                className="mt-1 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm"
                placeholder="e.g. DevOps Engineer"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Prompt (perspective)</label>
              <Textarea
                value={newPrompt}
                onChange={(e) => setNewPrompt(e.target.value)}
                rows={10}
                className="mt-1 text-sm font-mono"
                placeholder="You are..."
              />
            </div>
            <div className="flex gap-2">
              <Button onClick={createAgent}>Create</Button>
              <Button variant="outline" onClick={() => setCreating(false)}>Cancel</Button>
            </div>
          </div>
        ) : selected ? (
          <div className="max-w-2xl space-y-4">
            <div className="flex items-center gap-3">
              <span className={cn("w-3 h-3 rounded-full", AGENT_DOT_COLORS[selected.name] ?? "bg-muted-foreground")} />
              <h2 className="text-xl font-bold">{selected.name}</h2>
              <button
                onClick={() => toggleActive(selected.name)}
                className={cn(
                  "ml-auto text-xs px-2.5 py-1 rounded-full border transition-colors",
                  activeNames.includes(selected.name)
                    ? "border-primary text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {activeNames.includes(selected.name) ? "Active" : "Inactive"}
              </button>
            </div>

            <div>
              <label className="text-sm font-medium">Role</label>
              <input
                value={editRole}
                onChange={(e) => setEditRole(e.target.value)}
                className="mt-1 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm"
              />
            </div>

            <div>
              <label className="text-sm font-medium">Prompt (perspective)</label>
              <Textarea
                value={editPrompt}
                onChange={(e) => setEditPrompt(e.target.value)}
                rows={12}
                className="mt-1 text-sm font-mono"
              />
            </div>

            <div className="flex gap-2">
              <Button onClick={saveAgent}>Save</Button>
              <Button variant="destructive" onClick={deleteAgent}>Delete</Button>
            </div>

            <Separator />

            {/* Test section */}
            <div className="space-y-2">
              <h3 className="text-sm font-semibold">Test this agent</h3>
              <div className="flex gap-2">
                <input
                  value={testQ}
                  onChange={(e) => setTestQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && runTest()}
                  className="flex-1 rounded-lg border border-input bg-transparent px-3 py-2 text-sm"
                  placeholder="Ask a test question..."
                />
                <Button size="sm" onClick={runTest} disabled={testing}>
                  {testing ? "..." : "Test"}
                </Button>
              </div>
              {testResult && (
                <div className="rounded-lg bg-muted/50 p-4 text-sm whitespace-pre-wrap max-h-80 overflow-y-auto">
                  {testResult}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
            Select an agent or create a new one
          </div>
        )}
      </main>
    </div>
  );
}
