"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

import { getApiBase } from "@/lib/api";

const API = typeof window !== "undefined" ? getApiBase() : "";

interface Skill {
  name: string; type: string; trigger: string;
  steps: string[]; lessons: string[]; success_count: number; fail_count: number;
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Skill | null>(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/skills`).then((r) => r.json()).then((d) => setSkills(d.skills)).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  const filtered = skills.filter((s) =>
    !filter || s.name.includes(filter) || s.trigger.includes(filter) || s.type.includes(filter),
  );

  return (
    <div className="flex h-screen">
      <aside className="w-72 min-w-72 border-r border-border bg-sidebar flex flex-col h-full">
        <div className="p-5 pb-2">
          <div className="flex items-center gap-2.5">
            <a href="/" className="text-2xl">🏛</a>
            <h1 className="text-lg font-bold">Skills</h1>
          </div>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search skills..."
            className="mt-3 w-full rounded-lg border border-input bg-transparent px-3 py-1.5 text-sm"
          />
        </div>
        <Separator />
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {loading && <p className="text-xs text-muted-foreground p-3">Loading skills…</p>}
          {error && <p className="text-xs text-red-400 p-3">Error: {error}</p>}
          {!loading && !error && filtered.length === 0 && (
            <p className="text-xs text-muted-foreground p-3">No skills learned yet.</p>
          )}
          {!loading && !error && filtered.map((s) => (
            <div
              key={s.name}
              onClick={() => setSelected(s)}
              className={cn(
                "px-3 py-2 rounded-lg cursor-pointer transition-colors text-sm",
                selected?.name === s.name ? "bg-accent" : "hover:bg-accent/50",
              )}
            >
              <div className="flex items-center gap-2">
                <span className="font-medium truncate flex-1">{s.name}</span>
                <Badge variant="secondary" className="text-[10px]">{s.type}</Badge>
              </div>
              <p className="text-xs text-muted-foreground truncate mt-0.5">{s.trigger}</p>
            </div>
          ))}
        </div>
        <div className="p-3">
          <a href="/chat" className="flex items-center justify-center w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
            ← Back to Chat
          </a>
        </div>
      </aside>

      <main className="flex-1 p-6 overflow-y-auto">
        {selected ? (
          <div className="max-w-2xl space-y-4">
            <h2 className="text-xl font-bold">{selected.name}</h2>
            <div className="flex gap-2">
              <Badge>{selected.type}</Badge>
              <Badge variant="outline">✅ {selected.success_count} successes</Badge>
              <Badge variant="outline">❌ {selected.fail_count} failures</Badge>
            </div>
            <div>
              <h3 className="text-sm font-semibold mb-1">Trigger</h3>
              <p className="text-sm text-muted-foreground">{selected.trigger}</p>
            </div>
            {selected.steps.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold mb-1">Steps</h3>
                <ol className="list-decimal pl-5 text-sm space-y-1">
                  {selected.steps.map((s, i) => <li key={i}>{s}</li>)}
                </ol>
              </div>
            )}
            {selected.lessons.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold mb-1">Lessons</h3>
                <ul className="list-disc pl-5 text-sm space-y-1">
                  {selected.lessons.map((l, i) => <li key={i}>{l}</li>)}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm h-full">
            {skills.length === 0 ? "No skills learned yet. Skills are created after successful discussions and executions." : "Select a skill to view details"}
          </div>
        )}
      </main>
    </div>
  );
}
