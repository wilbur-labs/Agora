"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  listWorkspaceSources,
  pickProjectsFolder,
  probeWorkspaceSource,
  registerWorkspaceSource,
  setProjectsBase,
  unregisterWorkspaceSource,
  type ProjectImportStatus,
  type RuntimeCapabilities,
  type RuntimeCapability,
  type SettingsOverview,
} from "@/lib/control-plane";
import {
  MANIFEST_REASON_KEY,
  SOURCE_REASON_KEY,
  crSummaryOf,
  sourceVerdictOf,
  type WorkspaceSourcePayload,
  type WorkspaceTaskPayload,
} from "@/lib/workspace-sources";
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
          <WorkSourcesCard onError={setError} />
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
      <ProjectImportNotice status={overview?.projects.import_status ?? null} />
      <div className="space-y-4">
        {overview?.projects.items.map((project) => (
          <div key={project.project_id} className="rounded-lg border p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{project.name}</span>
              <Badge variant="outline">{project.project_id}</Badge>
              {project.project_id === overview.projects.current && <Badge variant="secondary">{t("settings.workspaces.current")}</Badge>}
              <StatusDot ok={project.root_is_git} on={t("settings.workspaces.git")} off={t("settings.workspaces.notGit")} unknown={t("settings.workspaces.gitUnknown")} />
            </div>
            <p className="mt-2 break-all font-mono text-[11px] text-muted-foreground">{project.root}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.workspaces.agents", { worker: project.worker_agent, reviewer: project.reviewer_agent })}</p>
            <div className="mt-3 space-y-1.5">
              {project.workspaces.map((ws) => (
                <div key={ws.adapter} className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="w-14 shrink-0 capitalize text-muted-foreground">{ws.adapter}</span>
                  <span className="break-all font-mono text-[11px]">{ws.path}</span>
                  <StatusDot
                    ok={ws.exists === null || ws.writable === null ? null : ws.exists && ws.writable}
                    on={ws.writable ? t("settings.workspaces.writable") : t("settings.workspaces.exists")}
                    off={ws.exists ? t("settings.workspaces.readonly") : t("settings.workspaces.missing")}
                    unknown={t("settings.workspaces.stateUnknown")}
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

/** Says out loud what the YAML import source did. Projects are stored in the
 *  control-plane database, so a file that cannot be read must be reported as a
 *  failure — it may never quietly shorten the list below. */
// The work Agora reads rather than owns (W7).
//
// Named "work sources" and not "import" on purpose. This registers a directory and
// reads it live; nothing is copied in. Call it import and the first two questions are
// "why did my edit in the workspace show up here" and "why can I not edit it here" —
// the label would be describing a mechanism the product does not have.
//
// The interaction is probe-then-confirm. A path is checked and the result shown
// *before* anything is stored, because the three ways this can go wrong read very
// differently to the person: an empty `tasks/`, a directory with no `tasks/` at all
// (almost always the wrong folder), and one that could not be read. Storing first and
// rendering an empty list would collapse all three into "no tasks", which is the
// substitution this product exists to refuse.
function WorkSourcesCard({ onError }: { onError: (msg: string | null) => void }) {
  const { t } = useI18n();
  const [sources, setSources] = useState<WorkspaceSourcePayload[] | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [draft, setDraft] = useState("");
  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState<WorkspaceSourcePayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [browsing, setBrowsing] = useState(false);

  // `sources === null` with `readFailed` is "could not read the list", which is not the
  // same as "nothing registered" and must not render as the empty state.
  //
  // Each load carries a token and only the newest one may write. Registering and
  // forgetting both call this, so two are easily in flight at once, and a slower
  // earlier response landing last would put a source the person just forgot back on
  // the screen as registered — state that survives until they reload the page and
  // that the product has no way to notice is stale.
  const loadToken = useRef(0);
  const load = useCallback(async () => {
    const token = ++loadToken.current;
    try {
      const listed = await listWorkspaceSources();
      if (token !== loadToken.current) return;
      setSources(listed); setReadFailed(false);
    } catch {
      if (token !== loadToken.current) return;
      setReadFailed(true);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const browse = async () => {
    setBrowsing(true); onError(null);
    try {
      const result = await pickProjectsFolder();
      if (!result.available) { onError(t("settings.sources.browseUnavailable")); return; }
      if (result.path) { setDraft(result.path); setProbe(null); }
    } catch (err) { onError((err as Error).message); }
    finally { setBrowsing(false); }
  };

  // The probe is stored with the path it was asked about, and registration uses *that*
  // path rather than whatever the input holds now. Without the pairing: probe A, edit
  // the box to B while the request is in flight, A's answer arrives and offers the
  // confirm button, and confirming registers B — a directory nobody probed, through the
  // one control whose entire purpose is that you saw the answer first.
  const [probed, setProbed] = useState<string | null>(null);
  const runProbe = async () => {
    const asked = draft.trim();
    setProbing(true); onError(null); setProbe(null); setProbed(null);
    try { setProbe(await probeWorkspaceSource(asked)); setProbed(asked); }
    catch (err) { onError((err as Error).message); }
    finally { setProbing(false); }
  };

  const confirm = async () => {
    if (probed === null) return;
    setSaving(true); onError(null);
    try { await registerWorkspaceSource(probed); setDraft(""); setProbe(null); setProbed(null); await load(); }
    catch (err) { onError((err as Error).message); }
    finally { setSaving(false); }
  };

  const forget = async (root: string) => {
    onError(null);
    try { await unregisterWorkspaceSource(root); await load(); }
    catch (err) { onError((err as Error).message); }
  };

  return (
    <Section icon={FolderOpen} title={t("settings.sources.title")} intro={t("settings.sources.intro")}>
      <div className="mb-4 rounded-lg border bg-muted/20 p-3">
        <label className="block space-y-1.5 text-sm font-medium">
          <span>{t("settings.sources.addLabel")}</span>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              className="field flex-1 font-mono text-xs"
              placeholder={t("settings.sources.addPlaceholder")}
              value={draft}
              onChange={(e) => { setDraft(e.target.value); setProbe(null); }}
            />
            <Button type="button" variant="outline" onClick={() => void browse()} disabled={browsing || probing || saving}>
              {browsing ? <RefreshCw className="animate-spin" /> : <FolderOpen />}{t("settings.sources.browse")}
            </Button>
            <Button type="button" onClick={() => void runProbe()} disabled={probing || saving || !draft.trim()}>
              {probing ? <RefreshCw className="animate-spin" /> : null}{t("settings.sources.check")}
            </Button>
          </div>
        </label>
        <p className="mt-1.5 text-[11px] text-muted-foreground">{t("settings.sources.addHint")}</p>
        {/* The probe's answer, before anything is stored. Registration is only offered
            when there is something to register — a directory with no `tasks/` gets its
            own sentence and no button, because the fix is to pick another folder. */}
        {probe !== null && probed !== null && (
          <div className="mt-3 rounded-lg border bg-background p-3">
            <SourceSummary source={probe} />
            {draft.trim() !== probed ? (
              // The box was edited after the probe. Say so instead of offering a button
              // that would register the path shown rather than the path checked.
              <p className="mt-2 text-xs text-amber-600">{t("settings.sources.staleProbe")}</p>
            ) : sourceVerdictOf(probe).show !== "unavailable" ? (
              <Button type="button" className="mt-3" onClick={() => void confirm()} disabled={saving}>
                {saving ? <RefreshCw className="animate-spin" /> : null}{t("settings.sources.confirm")}
              </Button>
            ) : null}
          </div>
        )}
      </div>

      {readFailed ? (
        <p className="text-sm text-muted-foreground">{t("settings.sources.listUnreadable")}</p>
      ) : sources === null ? (
        <p className="text-sm text-muted-foreground">…</p>
      ) : sources.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("settings.sources.none")}</p>
      ) : (
        <div className="space-y-3">
          {sources.map((source) => (
            <div key={source.root} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <SourceSummary source={source} />
                <Button type="button" variant="outline" size="sm" onClick={() => void forget(source.root)}>
                  {t("settings.sources.forget")}
                </Button>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">{t("settings.sources.forgetHint")}</p>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// One source, as one sentence plus its tasks. Every branch of the verdict says
// something different; none of them says "0".
function SourceSummary({ source }: { source: WorkspaceSourcePayload }) {
  const { t } = useI18n();
  const verdict = sourceVerdictOf(source);
  return (
    <div className="min-w-0 flex-1">
      <p className="font-mono text-xs break-all">{source.root}</p>
      {verdict.show === "unavailable" ? (
        <p className="mt-1 text-xs text-amber-600">{t(SOURCE_REASON_KEY[verdict.reason])}</p>
      ) : verdict.show === "empty" ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("settings.sources.emptyTasksDir")}
          {/* A tasks/ holding only `_template` is not "nothing in it". Dropping the
              skipped list here contradicted this panel's own rule one branch over. */}
          {verdict.skipped.length > 0
            ? ` ${t("settings.sources.skipped", { names: verdict.skipped.join(", ") })}`
            : ""}
        </p>
      ) : (
        <>
          <p className="mt-1 text-xs text-muted-foreground">
            {verdict.show === "partial"
              ? t("settings.sources.taskCountAtLeast", { count: String(verdict.count) })
              : t("settings.sources.taskCount", { count: String(verdict.count) })}
            {verdict.skipped.length > 0
              ? ` · ${t("settings.sources.skipped", { names: verdict.skipped.join(", ") })}`
              : ""}
          </p>
          {/* An entry the server listed but could not stat. Naming it is the whole
              point: the alternative is a task missing from the only place it shows,
              with the count above reading as the total. */}
          {verdict.show === "partial" && (
            <p className="mt-1 text-xs text-amber-600">
              {t("settings.sources.unreadableEntries", { names: verdict.unreadable.join(", ") })}
            </p>
          )}
          <ul className="mt-2 space-y-1">
            {(source.tasks ?? []).map((task) => <TaskLine key={task.name} task={task} />)}
          </ul>
        </>
      )}
    </div>
  );
}

function TaskLine({ task }: { task: WorkspaceTaskPayload }) {
  const { t } = useI18n();
  const cr = crSummaryOf(task.cr_log);
  const adr = task.decisions.records?.length ?? null;
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs">
      <span className="font-medium">{task.name}</span>
      {task.manifest.available ? (
        <span className="min-w-0 truncate text-muted-foreground">
          {task.manifest.goal ?? t("settings.sources.goalEmpty")}
        </span>
      ) : (
        // Not "no goal": the file was not read, and saying which is the whole point.
        <span className="text-amber-600">{t(MANIFEST_REASON_KEY[task.manifest.reason ?? "unreadable"])}</span>
      )}
      {cr.show === "counts" ? (
        <span className="text-muted-foreground">
          {t("settings.sources.crOpen", { open: String(cr.open) })}
          {/* Never shown without this: an open count says nothing about the rows whose
              status could not be read, and on the first real log there were nine. */}
          {cr.unrecognised > 0
            ? ` · ${t("settings.sources.crUnrecognised", { n: String(cr.unrecognised) })}`
            : ""}
        </span>
      ) : (
        // Not nothing. The first version rendered this branch as blank, so "the CR log
        // could not be read" looked exactly like "this task has no CRs" — the
        // substitution the whole panel exists to refuse, in the panel that refuses it.
        <span className="text-amber-600">{t("settings.sources.crUnavailable")}</span>
      )}
      {adr === null ? (
        // `records: null` is "could not read adr/", including a directory that is there
        // and unreadable. Absent is the ordinary case and says nothing, so it is the
        // only one that renders nothing.
        task.decisions.reason === "absent"
          ? null
          : <span className="text-amber-600">{t("settings.sources.adrUnavailable")}</span>
      ) : adr > 0 ? (
        <span className="text-muted-foreground">{t("settings.sources.adrCount", { n: String(adr) })}</span>
      ) : null}
    </li>
  );
}

function ProjectImportNotice({ status }: { status: ProjectImportStatus | null }) {
  const { t } = useI18n();
  if (!status) return null;
  const conflicts = status.last_import?.conflicts ?? [];
  if (!status.error && conflicts.length === 0) return null;
  return (
    <div className="mb-4 space-y-2">
      {status.error && (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] leading-5 text-destructive">
          {t("settings.workspaces.importError", { source: status.source, reason: status.error })}
        </p>
      )}
      {conflicts.length > 0 && (
        <p className="rounded-lg bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-700 dark:text-amber-300">
          {t("settings.workspaces.importConflicts", {
            count: conflicts.length,
            source: status.source,
            projects: conflicts.map((c) => `${c.project_id} (${c.fields.join(", ")})`).join("; "),
          })}
        </p>
      )}
    </div>
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

function StatusDot({ ok, on, off, unknown }: { ok: boolean | null; on: string; off: string; unknown: string }) {
  // Three states, because the backend now reports three. A null folded into `off`
  // would say "missing" about a path nobody could read — the false claim this dot
  // exists to avoid making.
  const label = ok === null ? unknown : ok ? on : off;
  const tone = ok === null ? "text-amber-600 dark:text-amber-400" : ok ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground";
  const dot = ok === null ? "bg-amber-500" : ok ? "bg-emerald-500" : "bg-muted-foreground/50";
  return (
    <span className={cn("flex items-center gap-1 text-[11px]", tone)}>
      <span className={cn("size-1.5 rounded-full", dot)} />
      {label}
    </span>
  );
}
