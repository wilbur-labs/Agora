"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Cpu,
  FolderGit2,
  FolderOpen,
  Gauge,
  GitFork,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  getRuntimeCapabilities,
  getSettingsOverview,
  pickProjectsFolder,
  setProjectsBase,
  type RuntimeCapabilities,
  type RuntimeCapability,
  type SettingsOverview,
} from "@/lib/control-plane";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

export default function SettingsPage() {
  const { t } = useI18n();
  const [runtimes, setRuntimes] = useState<RuntimeCapabilities | null>(null);
  const [overview, setOverview] = useState<SettingsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [caps, ov] = await Promise.all([getRuntimeCapabilities(), getSettingsOverview()]);
      setRuntimes(caps);
      setOverview(ov);
    } catch (err) {
      setError(t("settings.error.load", { message: (err as Error).message }));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  return (
    <DeliveryShell active="Settings">
      <header className="border-b bg-background px-5 py-5 md:px-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">{t("settings.header.eyebrow")}</p>
            <h1 className="mt-1 text-2xl font-bold">{t("settings.header.title")}</h1>
            <p className="mt-1 text-sm text-muted-foreground">{t("settings.header.subtitle")}</p>
          </div>
          <Button variant="outline" size="lg" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={cn(loading && "animate-spin")} /> {t("common.action.refresh")}
          </Button>
        </div>
      </header>

      <div className="mx-auto max-w-6xl space-y-6 p-5 md:p-8">
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>
        )}

        <RuntimesCard runtimes={runtimes} loading={loading} />
        <div className="grid gap-6 lg:grid-cols-2">
          <WorkspacesCard overview={overview} onReload={load} onError={setError} />
          <div className="space-y-6">
            <LimitsCard overview={overview} />
            <ActivityCard overview={overview} />
          </div>
        </div>
        <CodeHostCard />
      </div>
    </DeliveryShell>
  );
}

function Section({ icon: Icon, title, intro, children }: { icon: React.ElementType; title: string; intro: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border bg-card p-5 md:p-6">
      <div className="mb-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><Icon className="size-4" /> {title}</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{intro}</p>
      </div>
      {children}
    </section>
  );
}

function RuntimesCard({ runtimes, loading }: { runtimes: RuntimeCapabilities | null; loading: boolean }) {
  const { t } = useI18n();
  return (
    <Section icon={Cpu} title={t("settings.runtimes.title")} intro={t("settings.runtimes.intro")}>
      <div className="grid gap-3 md:grid-cols-3">
        {runtimes?.adapters.map((adapter) => <RuntimeTile key={adapter.adapter} adapter={adapter} />)}
        {!runtimes && loading && [0, 1, 2].map((i) => <div key={i} className="h-32 animate-pulse rounded-lg border bg-muted/40" />)}
      </div>
      <p className="mt-4 rounded-lg bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-700 dark:text-amber-300">
        {t("settings.runtimes.authNote")}
      </p>
      <p className="mt-2 rounded-lg bg-muted/50 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        {t("settings.activity.tokenNote")}
      </p>
    </Section>
  );
}

function RuntimeTile({ adapter }: { adapter: RuntimeCapability }) {
  const { t } = useI18n();
  const installed = adapter.installation_status === "installed";
  const bidirectional = adapter.attention_mode === "bidirectional";
  return (
    <div className={cn("rounded-lg border p-4", installed ? "bg-card" : "border-destructive/40 bg-destructive/5")}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold capitalize">{adapter.adapter}</span>
        {installed ? (
          <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"><CheckCircle2 className="size-3.5" /> {t("settings.runtimes.installed")}</span>
        ) : (
          <span className="flex items-center gap-1 text-xs text-destructive"><XCircle className="size-3.5" /> {adapter.installation_status === "not_found" ? t("settings.runtimes.notFound") : t("settings.runtimes.uninspectable")}</span>
        )}
      </div>
      <p className="mt-1 font-mono text-xs text-muted-foreground">{adapter.version ?? t("settings.runtimes.versionUnknown")}</p>
      <p className="mt-0.5 text-[11px] text-muted-foreground">{adapter.provider}</p>
      {installed ? (
        <>
          <AuthLine adapter={adapter} />
          <TokenLine adapter={adapter} />
          <Badge variant={bidirectional ? "default" : "secondary"} className="mt-2">
            {bidirectional ? t("settings.runtimes.bidirectional") : t("settings.runtimes.captureOnly")}
          </Badge>
        </>
      ) : (
        <p className="mt-3 text-[11px] text-destructive">{t("settings.runtimes.notInstalledHint")}</p>
      )}
    </div>
  );
}

function AuthLine({ adapter }: { adapter: RuntimeCapability }) {
  const { t } = useI18n();
  const authed = adapter.authenticated;
  const label = authed === true
    ? t("settings.runtimes.signedIn")
    : authed === false
      ? t("settings.runtimes.signedOut")
      : t("settings.runtimes.authUnknown");
  const tone = authed === true
    ? "text-emerald-600 dark:text-emerald-400"
    : authed === false
      ? "text-destructive"
      : "text-muted-foreground";
  return (
    <p className={cn("mt-2 flex items-center gap-1.5 text-[11px]", tone)}>
      <span className={cn("size-1.5 rounded-full", authed === true ? "bg-emerald-500" : authed === false ? "bg-destructive" : "bg-muted-foreground/50")} />
      {label}{adapter.auth_method ? ` · ${adapter.auth_method}` : ""}
    </p>
  );
}

function TokenLine({ adapter }: { adapter: RuntimeCapability }) {
  const { t } = useI18n();
  const supported = adapter.token_capture === "supported";
  return (
    <p className={cn("mt-1 text-[11px]", supported ? "text-foreground" : "text-muted-foreground")}>
      {supported
        ? t("settings.runtimes.tokensUsed", { value: adapter.tokens_used.toLocaleString() })
        : t("settings.runtimes.tokensNotCaptured")}
    </p>
  );
}

function WorkspacesCard({ overview, onReload, onError }: { overview: SettingsOverview | null; onReload: () => Promise<void>; onError: (msg: string | null) => void }) {
  const { t } = useI18n();
  const base = overview?.projects.projects_base ?? "";
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  useEffect(() => { setDraft(base); setSaved(false); }, [base]);

  const save = async () => {
    setSaving(true); onError(null); setSaved(false);
    try { await setProjectsBase(draft.trim()); setSaved(true); await onReload(); }
    catch (err) { onError((err as Error).message); }
    finally { setSaving(false); }
  };

  const browse = async () => {
    setBrowsing(true); onError(null);
    try {
      const result = await pickProjectsFolder();
      if (!result.available) { onError(t("settings.workspaces.baseBrowseUnavailable")); return; }
      if (result.path) { setDraft(result.path); setSaved(false); }
    } catch (err) { onError((err as Error).message); }
    finally { setBrowsing(false); }
  };

  return (
    <Section icon={FolderGit2} title={t("settings.workspaces.title")} intro={t("settings.workspaces.intro")}>
      <div className="mb-4 rounded-lg border bg-muted/20 p-3">
        <label className="block space-y-1.5 text-sm font-medium">
          <span>{t("settings.workspaces.baseLabel")}</span>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input className="field flex-1 font-mono text-xs" value={draft} onChange={(e) => { setDraft(e.target.value); setSaved(false); }} />
            <Button type="button" variant="outline" onClick={() => void browse()} disabled={browsing || saving}>
              {browsing ? <RefreshCw className="animate-spin" /> : <FolderOpen />}{t("settings.workspaces.baseBrowse")}
            </Button>
            <Button type="button" onClick={() => void save()} disabled={saving || browsing || !draft.trim() || draft.trim() === base}>
              {saving ? <RefreshCw className="animate-spin" /> : null}{saved && draft.trim() === base ? t("settings.workspaces.baseSaved") : t("settings.workspaces.baseSave")}
            </Button>
          </div>
        </label>
        <p className="mt-1.5 text-[11px] text-muted-foreground">{t("settings.workspaces.baseHint")}</p>
      </div>
      <div className="space-y-4">
        {overview?.projects.items.map((project) => (
          <div key={project.project_id} className="rounded-lg border p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{project.name}</span>
              <Badge variant="outline">{project.project_id}</Badge>
              {project.project_id === overview.projects.current && <Badge variant="secondary">{t("settings.workspaces.current")}</Badge>}
              <StatusDot ok={project.root_is_git} on={t("settings.workspaces.git")} off={t("settings.workspaces.notGit")} />
            </div>
            <p className="mt-2 break-all font-mono text-[11px] text-muted-foreground">{project.root}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.workspaces.agents", { worker: project.worker_agent, reviewer: project.reviewer_agent })}</p>
            <div className="mt-3 space-y-1.5">
              {project.workspaces.map((ws) => (
                <div key={ws.adapter} className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="w-14 shrink-0 capitalize text-muted-foreground">{ws.adapter}</span>
                  <span className="break-all font-mono text-[11px]">{ws.path}</span>
                  <StatusDot ok={ws.exists && ws.writable} on={ws.writable ? t("settings.workspaces.writable") : t("settings.workspaces.exists")} off={ws.exists ? t("settings.workspaces.readonly") : t("settings.workspaces.missing")} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function LimitsCard({ overview }: { overview: SettingsOverview | null }) {
  const { t } = useI18n();
  const ex = overview?.execution;
  const cp = overview?.control_plane;
  return (
    <Section icon={Gauge} title={t("settings.limits.title")} intro="">
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <Stat label={t("settings.limits.globalConcurrency")} value={ex ? String(ex.max_concurrent_global) : "—"} />
        <Stat label={t("settings.limits.projectConcurrency")} value={ex ? String(ex.max_concurrent_per_project) : "—"} />
        <Stat label={t("settings.limits.networkMode")} value={ex ? ex.network_mode : "—"} />
        <Stat label={t("settings.limits.timeout")} value={ex ? t("settings.limits.seconds", { value: ex.default_timeout_seconds }) : "—"} />
        <Stat
          label={t("settings.limits.authConfigured")}
          value={cp ? (cp.auth_configured ? t("settings.limits.authOn") : t("settings.limits.authOff")) : "—"}
          ok={cp?.auth_configured}
        />
      </dl>
    </Section>
  );
}

function ActivityCard({ overview }: { overview: SettingsOverview | null }) {
  const { t } = useI18n();
  const runs = overview?.runs;
  return (
    <Section icon={Activity} title={t("settings.activity.title")} intro={t("settings.activity.intro")}>
      <div className="grid grid-cols-3 gap-3">
        <Stat label={t("settings.activity.total")} value={runs ? String(runs.total) : "—"} />
        <Stat label={t("settings.activity.running")} value={runs ? String(runs.running) : "—"} />
        <Stat label={t("settings.activity.queued")} value={runs ? String(runs.queued) : "—"} />
      </div>
      {runs && (Object.keys(runs.by_adapter).length > 0 || Object.keys(runs.by_state).length > 0) && (
        <div className="mt-3 space-y-2">
          <CountRow label={t("settings.activity.byState")} counts={runs.by_state} />
          <CountRow label={t("settings.activity.byAdapter")} counts={runs.by_adapter} />
        </div>
      )}
      {runs?.truncated && <p className="mt-2 text-[11px] text-muted-foreground">{t("settings.activity.truncated", { limit: 500 })}</p>}
    </Section>
  );
}

function CodeHostCard() {
  const { t } = useI18n();
  return (
    <Section icon={GitFork} title={t("settings.codeHost.title")} intro={t("settings.codeHost.intro")}>
      <div className="flex flex-col gap-3 opacity-60 sm:flex-row">
        <label className="flex-1 space-y-1.5 text-sm">
          <span className="text-muted-foreground">{t("settings.codeHost.providerLabel")}</span>
          <select disabled className="field"><option>GitHub</option><option>GitLab</option></select>
        </label>
        <label className="flex-[2] space-y-1.5 text-sm">
          <span className="text-muted-foreground">{t("settings.codeHost.remoteLabel")}</span>
          <input disabled className="field" placeholder={t("settings.codeHost.remotePlaceholder")} />
        </label>
      </div>
      <Badge variant="outline" className="mt-3">{t("settings.codeHost.soon")}</Badge>
    </Section>
  );
}

function Stat({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded-lg border bg-muted/20 p-3">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className={cn("mt-1 text-lg font-semibold tabular-nums", ok === false && "text-destructive", ok === true && "text-emerald-600 dark:text-emerald-400")}>{value}</dd>
    </div>
  );
}

function CountRow({ label, counts }: { label: string; counts: Record<string, number> }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="w-16 shrink-0 text-muted-foreground">{label}</span>
      {Object.entries(counts).map(([key, value]) => (
        <Badge key={key} variant="outline" className="capitalize">{key.replaceAll("_", " ")} · {value}</Badge>
      ))}
    </div>
  );
}

function StatusDot({ ok, on, off }: { ok: boolean; on: string; off: string }) {
  return (
    <span className={cn("flex items-center gap-1 text-[11px]", ok ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
      <span className={cn("size-1.5 rounded-full", ok ? "bg-emerald-500" : "bg-muted-foreground/50")} />
      {ok ? on : off}
    </span>
  );
}
