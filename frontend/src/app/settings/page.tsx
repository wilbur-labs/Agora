"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Cpu,
  FolderGit2,
  FolderMinus,
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
  getSettingsOverview,
  listWorkspaceSources,
  pickProjectsFolder,
  probeWorkspaceSource,
  registerWorkspaceSource,
  removeProject,
  setProjectsBase,
  unregisterWorkspaceSource,
  type ProjectImportStatus,
  type RuntimeCapabilities,
  type RuntimeCapability,
  type SettingsOverview,
} from "@/lib/control-plane";
import { forgetRemovedProject, removalErrorOf } from "@/lib/project-removal";
import { isProbeSuperseded, probeRuntimes } from "@/lib/runtime-probe";
import {
  SOURCE_REASON_KEY,
  sourceVerdictOf,
  type WorkspaceSourcePayload,
} from "@/lib/workspace-sources";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

export default function SettingsPage() {
  const { t } = useI18n();
  const [runtimes, setRuntimes] = useState<RuntimeCapabilities | null>(null);
  const [overview, setOverview] = useState<SettingsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Which load is current. Loads overlap — the mount, a refresh, a locale change that
  // rebuilds `load` — and each read publishes itself the moment it lands, so an older
  // load's late answer would otherwise overwrite a newer one. Only the newest writes.
  const loadToken = useRef(0);

  // `force` re-probes the CLIs now (the refresh button); the mount joins the shared
  // answer the top bar already asked for (lib/runtime-probe.ts).
  const load = useCallback(async ({ force = false }: { force?: boolean } = {}) => {
    const token = ++loadToken.current;
    const current = () => token === loadToken.current;
    setLoading(true);
    setError(null);
    // Two reads, each landing on its own the moment it does. Under one `Promise.all` a
    // failed overview read threw away a CLI probe that had succeeded; under one
    // `allSettled` a hung overview read held a landed probe off the screen — while the
    // top bar, subscribed to the same probe, was already showing it. Each read publishes
    // itself; only the spinner waits for both.
    // Each read publishes its own answer — and its own failure — the moment it lands;
    // only the spinner waits for both. A failure held until the sibling read settled was
    // a failure never shown while the sibling hung.
    const failures: string[] = [];
    const describe = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));
    const fail = (reason: unknown) => {
      failures.push(describe(reason));
      setError(t("settings.error.load", { message: failures.join(" · ") }));
    };
    await Promise.allSettled([
      probeRuntimes({ force }).then(
        (caps) => { if (current()) setRuntimes(caps); },
        (reason: unknown) => {
          // Overtaken by a newer forced ask: that one owns the screen, and this one has
          // neither an answer to show nor a failure to report.
          if (isProbeSuperseded(reason) || !current()) return;
          // A failed probe is a failed probe: the cards must not keep showing the answer
          // the person just refused by refreshing, while the top bar has already dropped it.
          setRuntimes(null);
          fail(reason);
        },
      ),
      getSettingsOverview().then(
        (ov) => { if (current()) setOverview(ov); },
        (reason: unknown) => { if (current()) fail(reason); },
      ),
    ]);
    if (current()) setLoading(false); // a newer load owns the spinner otherwise
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
          <Button variant="outline" size="lg" onClick={() => void load({ force: true })} disabled={loading}>
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
      <p className="mt-4 rounded-lg bg-amber-500/5 px-3 py-2 text-[0.75rem] leading-5 text-amber-700 dark:text-amber-300">
        {t("settings.runtimes.authNote")}
      </p>
      <p className="mt-2 rounded-lg bg-muted/50 px-3 py-2 text-[0.75rem] leading-5 text-muted-foreground">
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
      <p className="mt-0.5 text-[0.75rem] text-muted-foreground">{adapter.provider}</p>
      {installed ? (
        <>
          <AuthLine adapter={adapter} />
          <TokenLine adapter={adapter} />
          <Badge variant={bidirectional ? "default" : "secondary"} className="mt-2">
            {bidirectional ? t("settings.runtimes.bidirectional") : t("settings.runtimes.captureOnly")}
          </Badge>
        </>
      ) : (
        <p className="mt-3 text-[0.75rem] text-destructive">{t("settings.runtimes.notInstalledHint")}</p>
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
    <p className={cn("mt-2 flex items-center gap-1.5 text-[0.75rem]", tone)}>
      <span className={cn("size-1.5 rounded-full", authed === true ? "bg-emerald-500" : authed === false ? "bg-destructive" : "bg-muted-foreground/50")} />
      {label}{adapter.auth_method ? ` · ${adapter.auth_method}` : ""}
    </p>
  );
}

function TokenLine({ adapter }: { adapter: RuntimeCapability }) {
  const { t } = useI18n();
  // Three answers, not two. "unknown" means the adapter registry could not be
  // built, so whether usage is read was never established — printing "not
  // captured" for it stated a confirmed absence and undid the very distinction
  // the payload had just been fixed to carry.
  const capture = adapter.token_capture;
  return (
    <p className={cn("mt-1 text-[0.75rem]", capture === "supported" ? "text-foreground" : "text-muted-foreground")}>
      {capture === "supported"
        ? t("settings.runtimes.tokensUsed", { value: adapter.tokens_used.toLocaleString() })
        : capture === "unsupported"
          ? t("settings.runtimes.tokensNotCaptured")
          : t("settings.runtimes.tokensCaptureUnknown")}
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
        <p className="mt-1.5 text-[0.75rem] text-muted-foreground">{t("settings.workspaces.baseHint")}</p>
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
            <RemoveProject projectId={project.project_id} name={project.name} onRemoved={onReload} />
            <p className="mt-2 break-all font-mono text-[0.75rem] text-muted-foreground">{project.root}</p>
            <p className="mt-1 text-[0.75rem] text-muted-foreground">{t("settings.workspaces.agents", { worker: project.worker_agent, reviewer: project.reviewer_agent })}</p>
            <div className="mt-3 space-y-1.5">
              {project.workspaces.map((ws) => (
                <div key={ws.adapter} className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="w-14 shrink-0 capitalize text-muted-foreground">{ws.adapter}</span>
                  <span className="break-all font-mono text-[0.75rem]">{ws.path}</span>
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

/**
 * "Remove from Agora" for one project (CR-56): two steps, inline — the second says what
 * removing means (the registration only; no file in the repository is deleted) before it
 * can be done. A refusal is the server's own sentence, with its counts, verbatim.
 *
 * Nothing on this page is "the project being viewed"; the one place that comes back to a
 * project by itself is the workbench, through its remembered last project, so that is what
 * is forgotten when it named this one. The server has already moved its current-project
 * pointer, which is where the workbench falls back to.
 */
function RemoveProject({ projectId, name, onRemoved }: { projectId: string; name: string; onRemoved: () => Promise<void> }) {
  const { t } = useI18n();
  const [step, setStep] = useState<"idle" | "confirm" | "removing">("idle");
  const [failure, setFailure] = useState<string | null>(null);

  const remove = async () => {
    setStep("removing");
    setFailure(null);
    try {
      await removeProject(projectId);
      let storage: Storage | null = null;
      try { storage = window.localStorage; } catch { /* unavailable: nothing remembered to forget */ }
      forgetRemovedProject(storage, projectId);
      await onRemoved();
      // The removal itself succeeded. If the overview could not be re-read the card is
      // still on screen from the old list; it must not stay frozen at "removing" (review).
      setStep("idle");
    } catch (err) {
      const { refused, message } = removalErrorOf(err);
      setFailure(t(refused ? "settings.workspaces.removeRefused" : "settings.workspaces.removeFailed", { message }));
      setStep("idle");
    }
  };

  return (
    <div className="mt-2">
      {step === "idle" ? (
        <Button type="button" variant="outline" size="sm" onClick={() => { setFailure(null); setStep("confirm"); }}>
          <FolderMinus />{t("settings.workspaces.remove")}
        </Button>
      ) : (
        <div className="space-y-2 rounded-md border border-dashed p-3">
          <p className="text-[0.8125rem]">{t("settings.workspaces.removeConfirm", { name: `${name} (${projectId})` })}</p>
          <div className="flex gap-2">
            <Button type="button" variant="destructive" size="sm" onClick={() => void remove()} disabled={step === "removing"}>
              {step === "removing" ? <RefreshCw className="animate-spin" /> : <FolderMinus />}
              {step === "removing" ? t("settings.workspaces.removing") : t("settings.workspaces.removeYes")}
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => setStep("idle")} disabled={step === "removing"}>
              {t("settings.workspaces.removeNo")}
            </Button>
          </div>
        </div>
      )}
      {failure && <p className="mt-2 text-[0.75rem] text-red-700 dark:text-red-400">{failure}</p>}
    </div>
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
        <p className="mt-1.5 text-[0.75rem] text-muted-foreground">{t("settings.sources.addHint")}</p>
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
              <p className="mt-2 text-[0.75rem] text-muted-foreground">{t("settings.sources.forgetHint")}</p>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// One source, as one sentence about what it holds. Every branch of the verdict says
// something different; none of them says "0". The per-task detail — goal, progress,
// where it stopped and what is next, the way onward — no longer lives here: W8 moved
// the task list itself to the workbench (项目工作台), and this card keeps only what
// registration needs, which is the confirmation that the folder is a workspace and how
// many tasks it holds.
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
        </>
      )}
    </div>
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
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[0.75rem] leading-5 text-destructive">
          {t("settings.workspaces.importError", { source: status.source, reason: status.error })}
        </p>
      )}
      {conflicts.length > 0 && (
        <p className="rounded-lg bg-amber-500/5 px-3 py-2 text-[0.75rem] leading-5 text-amber-700 dark:text-amber-300">
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
      {runs?.truncated && <p className="mt-2 text-[0.75rem] text-muted-foreground">{t("settings.activity.truncated", { limit: 500 })}</p>}
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
      <dt className="text-[0.75rem] text-muted-foreground">{label}</dt>
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
    <span className={cn("flex items-center gap-1 text-[0.75rem]", tone)}>
      <span className={cn("size-1.5 rounded-full", dot)} />
      {label}
    </span>
  );
}
