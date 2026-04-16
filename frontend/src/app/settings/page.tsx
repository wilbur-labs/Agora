"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";

import { getApiBase } from "@/lib/api";

const API = typeof window !== "undefined" ? getApiBase() : "";

export default function SettingsPage() {
  const [memory, setMemory] = useState("");
  const [profile, setProfile] = useState<Record<string, string>>({});
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetch(`${API}/api/memory`).then((r) => r.json()).then((d) => setMemory(d.memory || "(empty)")),
      fetch(`${API}/api/profile`).then((r) => r.json()).then((d) => setProfile(d.profile || {})),
    ]).catch((e) => setError((e as Error).message)).finally(() => setLoading(false));
  }, []);

  const saveProfile = useCallback(async () => {
    await fetch(`${API}/api/profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile }),
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }, [profile]);

  const addField = useCallback(() => {
    if (!newKey.trim()) return;
    setProfile((p) => ({ ...p, [newKey.trim()]: newVal }));
    setNewKey("");
    setNewVal("");
  }, [newKey, newVal]);

  return (
    <div className="min-h-screen p-8 max-w-2xl mx-auto space-y-8">
      <div className="flex items-center gap-3">
        <a href="/" className="text-2xl">🏛</a>
        <h1 className="text-2xl font-bold">Settings</h1>
      </div>

      {/* Profile */}
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading settings…</p>
      ) : error ? (
        <p className="text-sm text-red-400">Error: {error}</p>
      ) : (<>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">User Profile</h2>
        <p className="text-sm text-muted-foreground">Agents use this to personalize responses.</p>
        <div className="space-y-2">
          {Object.entries(profile).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2">
              <span className="text-sm font-medium w-32 shrink-0">{k}</span>
              <input
                value={v}
                onChange={(e) => setProfile((p) => ({ ...p, [k]: e.target.value }))}
                className="flex-1 rounded-lg border border-input bg-transparent px-3 py-1.5 text-sm"
              />
              <button
                onClick={() => setProfile((p) => { const n = { ...p }; delete n[k]; return n; })}
                className="text-xs text-muted-foreground hover:text-destructive"
              >✕</button>
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="Key" className="w-32 rounded-lg border border-input bg-transparent px-3 py-1.5 text-sm" />
          <input value={newVal} onChange={(e) => setNewVal(e.target.value)} placeholder="Value" className="flex-1 rounded-lg border border-input bg-transparent px-3 py-1.5 text-sm" />
          <Button size="sm" variant="outline" onClick={addField}>Add</Button>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={saveProfile}>Save Profile</Button>
          {saved && <span className="text-sm text-emerald-400">✓ Saved</span>}
        </div>
      </section>

      <Separator />

      {/* Memory */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Memory</h2>
        <p className="text-sm text-muted-foreground">Persistent memory from past conversations (read-only).</p>
        <Textarea value={memory} readOnly rows={12} className="font-mono text-xs" />
      </section>
      </>)}

      <div className="pt-4">
        <a href="/chat" className="text-sm text-muted-foreground hover:text-foreground">← Back to Chat</a>
      </div>
    </div>
  );
}
