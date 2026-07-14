"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bell, Check, RefreshCw, ShieldAlert, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAttentionNotifications } from "@/hooks/use-attention-notifications";
import { usePoll } from "@/hooks/use-poll";
import { ApiError } from "@/lib/control-plane";
import {
  cancelAttention, listAttention, respondAttention,
  type AttentionItem, type AttentionKind, type AttentionState, type ResponseAction,
} from "@/lib/attention";
import { cn } from "@/lib/utils";

const states: Array<AttentionState | "all"> = ["open", "responded", "cancelled", "expired", "all"];
const kinds: Array<AttentionKind | "all"> = ["all", "question", "approval", "blocker"];

export default function AttentionPage() {
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [stateFilter, setStateFilter] = useState<AttentionState | "all">("open");
  const [kindFilter, setKindFilter] = useState<AttentionKind | "all">("all");
  const [projectFilter, setProjectFilter] = useState("all");
  const [response, setResponse] = useState("");
  const [action, setAction] = useState<ResponseAction>("answer");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const mounted = useRef(true);
  const requestId = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const notify = useAttentionNotifications(notificationsEnabled);

  const load = useCallback(async (initial = false) => {
    const id = ++requestId.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    if (initial) setLoading(true);
    try {
      const loaded = await listAttention({ limit: 200 }, controller.signal);
      if (!mounted.current || id !== requestId.current) return;
      setItems((current) => loaded.map((item) => {
        const local = current.find((candidate) => candidate.item_id === item.item_id);
        return local && local.version > item.version ? local : item;
      }));
      notify(loaded);
      setError(null);
    } catch (err) {
      if ((err as Error).name !== "AbortError" && mounted.current && id === requestId.current) setError((err as Error).message);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (mounted.current && initial && id === requestId.current) setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    mounted.current = true;
    const query = new URLSearchParams(window.location.search);
    const queryState = query.get("state");
    const queryKind = query.get("kind");
    if (states.includes(queryState as AttentionState)) setStateFilter(queryState as AttentionState);
    if (kinds.includes(queryKind as AttentionKind)) setKindFilter(queryKind as AttentionKind);
    setProjectFilter(query.get("project") || "all");
    const enabled = window.localStorage.getItem("agora.attentionNotifications") === "enabled";
    setNotificationsEnabled(enabled && typeof Notification !== "undefined" && Notification.permission === "granted");
    const timeout = window.setTimeout(() => void load(true), 0);
    return () => { mounted.current = false; requestId.current += 1; abortRef.current?.abort(); window.clearTimeout(timeout); };
  }, [load]);
  usePoll(() => load(false), 5_000, true);

  const projects = useMemo(() => Array.from(new Set(items.map((item) => item.project_id))).sort(), [items]);
  const visible = items.filter((item) =>
    (stateFilter === "all" || item.state === stateFilter)
    && (kindFilter === "all" || item.kind === kindFilter)
    && (projectFilter === "all" || item.project_id === projectFilter));
  const selected = items.find((item) => item.item_id === selectedId) ?? null;
  const openCount = items.filter((item) => item.state === "open").length;

  useEffect(() => {
    if (selected && selected.kind === "approval") setAction("approve");
    else setAction("answer");
    setResponse(selected?.options[0] ?? "");
  }, [selected?.item_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const updateItem = useCallback((item: AttentionItem) => {
    setItems((current) => current.map((candidate) => candidate.item_id === item.item_id ? item : candidate));
  }, []);

  async function submitResponse() {
    if (!selected || selected.state !== "open") return;
    setActing(true); setMessage("");
    try {
      const updated = await respondAttention(selected.item_id, {
        action, response, actor: "user", expected_version: selected.version,
      });
      if (!mounted.current) return;
      updateItem(updated); setMessage("Response recorded. The bridge can now deliver it to the waiting agent.");
    } catch (err) {
      if (!mounted.current) return;
      if (err instanceof ApiError && err.status === 409) { setMessage("This item changed in another session; refreshed the authoritative state."); void load(false); }
      else setError((err as Error).message);
    } finally { if (mounted.current) setActing(false); }
  }

  async function cancelSelected() {
    if (!selected || selected.state !== "open") return;
    setActing(true); setMessage("");
    try {
      const updated = await cancelAttention(selected.item_id, { actor: "user", reason: "Cancelled from Attention Center", expected_version: selected.version });
      if (mounted.current) { updateItem(updated); setMessage("Attention item cancelled."); }
    } catch (err) {
      if (mounted.current && err instanceof ApiError && err.status === 409) { setMessage("This item changed in another session; refreshed."); void load(false); }
      else if (mounted.current) setError((err as Error).message);
    } finally { if (mounted.current) setActing(false); }
  }

  async function enableNotifications() {
    if (typeof Notification === "undefined") return;
    const permission = await Notification.requestPermission();
    const enabled = permission === "granted";
    setNotificationsEnabled(enabled);
    window.localStorage.setItem("agora.attentionNotifications", enabled ? "enabled" : "disabled");
  }

  return (
    <DeliveryShell active="Attention">
      <header className="border-b bg-background/85 px-5 py-5 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4">
          <div><p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">Human intervention</p><h1 className="mt-1 text-2xl font-bold">Attention Center</h1></div>
          <div className="flex items-center gap-2"><Badge variant={openCount ? "destructive" : "secondary"}>{openCount} open</Badge><Button variant="outline" onClick={() => void load(false)}><RefreshCw />Refresh</Button></div>
        </div>
      </header>
      <main className="mx-auto max-w-[1500px] space-y-5 p-5 md:p-8">
        {!notificationsEnabled && typeof window !== "undefined" && typeof Notification !== "undefined" && Notification.permission === "default" && (
          <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4"><div className="flex items-center gap-3"><Bell className="size-5 text-primary" /><div><p className="font-medium">Get alerted when an agent is waiting</p><p className="text-sm text-muted-foreground">Permission is requested only after you click.</p></div></div><Button onClick={() => void enableNotifications()}>Enable notifications</Button></section>
        )}
        {error && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        <p aria-live="polite" className="text-sm text-muted-foreground">{message}</p>
        <section className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(380px,.85fr)]">
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2 rounded-xl border bg-card p-3">
              <label className="text-sm">State <select className="ml-2 rounded-md border bg-background px-2 py-1" value={stateFilter} onChange={(e) => setStateFilter(e.target.value as AttentionState | "all")}>{states.map((value) => <option key={value}>{value}</option>)}</select></label>
              <label className="text-sm">Kind <select className="ml-2 rounded-md border bg-background px-2 py-1" value={kindFilter} onChange={(e) => setKindFilter(e.target.value as AttentionKind | "all")}>{kinds.map((value) => <option key={value}>{value}</option>)}</select></label>
              <label className="text-sm">Project <select className="ml-2 rounded-md border bg-background px-2 py-1" value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)}><option value="all">all</option>{projects.map((value) => <option key={value}>{value}</option>)}</select></label>
            </div>
            {loading ? <p className="p-8 text-center text-muted-foreground">Loading attention inbox…</p> : visible.length === 0 ? <p className="rounded-xl border border-dashed p-10 text-center text-muted-foreground">No matching attention items.</p> : visible.map((item) => (
              <button key={item.item_id} onClick={() => setSelectedId(item.item_id)} className={cn("w-full rounded-xl border bg-card p-4 text-left transition hover:border-primary/50", selectedId === item.item_id && "border-primary ring-2 ring-primary/15")}>
                <div className="flex flex-wrap items-start justify-between gap-2"><div className="flex items-center gap-2"><Badge variant={item.state === "open" ? "default" : "secondary"}>{item.state}</Badge><Badge variant="outline">{item.kind}</Badge><Badge variant={item.urgency === "critical" ? "destructive" : "outline"}>{item.urgency}</Badge></div><time className="text-xs text-muted-foreground">{new Date(item.created_at).toLocaleString()}</time></div>
                <h2 className="mt-3 font-semibold">{item.title}</h2><p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{item.body || "No additional details"}</p><p className="mt-3 text-xs text-muted-foreground">{item.project_id} · {item.task_id} · requested by {item.requester}</p>
              </button>
            ))}
          </div>
          <aside className="rounded-xl border bg-card p-5 xl:sticky xl:top-5 xl:self-start">
            {!selected ? <div className="grid min-h-64 place-items-center text-center text-muted-foreground"><div><ShieldAlert className="mx-auto mb-3 size-8" /><p>Select an item to inspect and respond.</p></div></div> : <div className="space-y-5">
              <div><div className="flex gap-2"><Badge>{selected.kind}</Badge><Badge variant="outline">v{selected.version}</Badge></div><h2 className="mt-3 text-xl font-semibold">{selected.title}</h2><p className="mt-2 whitespace-pre-wrap text-sm">{selected.body}</p></div>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm"><dt className="text-muted-foreground">Project</dt><dd>{selected.project_id}</dd><dt className="text-muted-foreground">Task</dt><dd className="break-all">{selected.task_id}</dd>{selected.run_id && <><dt className="text-muted-foreground">Run</dt><dd className="break-all">{selected.run_id}</dd></>}<dt className="text-muted-foreground">Requester</dt><dd>{selected.requester}</dd></dl>
              {selected.state === "open" ? <div className="space-y-3 border-t pt-4">
                {selected.kind === "approval" && <div className="flex gap-2"><Button variant={action === "approve" ? "default" : "outline"} onClick={() => setAction("approve")}><Check />Approve</Button><Button variant={action === "reject" ? "destructive" : "outline"} onClick={() => setAction("reject")}><X />Reject</Button></div>}
                {selected.kind !== "approval" && <input type="hidden" value="answer" />}
                {selected.options.length > 0 && <label className="block text-sm">Suggested answer<select className="mt-1 block w-full rounded-md border bg-background p-2" value={response} onChange={(e) => { setResponse(e.target.value); setAction("answer"); }}>{selected.options.map((value) => <option key={value}>{value}</option>)}</select></label>}
                <label className="block text-sm">Response<textarea className="mt-1 min-h-28 w-full rounded-md border bg-background p-3" value={response} onChange={(e) => setResponse(e.target.value)} placeholder="Provide context for the waiting agent" /></label>
                <div className="flex flex-wrap gap-2"><Button disabled={acting || (action === "answer" && !response.trim())} onClick={() => void submitResponse()}>Submit response</Button><Button variant="outline" disabled={acting} onClick={() => void cancelSelected()}>Cancel item</Button></div>
              </div> : <div className="border-t pt-4"><p className="text-sm font-medium">Resolution</p><p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">{selected.response_action ? `${selected.response_action}: ` : ""}{selected.response || selected.cancellation_reason || selected.state}</p></div>}
            </div>}
          </aside>
        </section>
      </main>
    </DeliveryShell>
  );
}
