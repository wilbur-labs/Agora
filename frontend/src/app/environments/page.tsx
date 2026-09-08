"use client";

/**
 * Environments — mockup v2, screen 3, restricted to what Agora can actually read.
 *
 * The mockup shows three machines: this one, a VPS, and a third that is known
 * about but unreachable. **Two of those three have no data source.** There is no
 * environment registry: `agora/environments.py` defines the `run` + `probe` seam
 * (W2) and exactly one implementation, `LocalEnvironment`. Registering a remote
 * host is W3-a, and W3-a is not built.
 *
 * So this screen shows the one environment that exists, from real reads, and says
 * plainly that the others cannot be registered yet. It does not draw a greyed-out
 * card for a machine nobody told Agora about — that would be inventing the very
 * inventory the mockup's own note says must never be confused with an empty one.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, HelpCircle, Server, XCircle } from "lucide-react";

import { DeliveryShell } from "@/components/delivery-shell";
import { useI18n } from "@/lib/i18n";
import {
  getRuntimeCapabilities,
  getSettingsOverview,
  type RuntimeCapabilities,
  type SettingsOverview,
} from "@/lib/control-plane";

export default function EnvironmentsPage() {
  const { t } = useI18n();
  const [runtimes, setRuntimes] = useState<RuntimeCapabilities | null>(null);
  const [runtimesFailed, setRuntimesFailed] = useState(false);
  const [overview, setOverview] = useState<SettingsOverview | null>(null);
  const [overviewFailed, setOverviewFailed] = useState(false);

  useEffect(() => {
    // Two independent reads, each reporting only for itself. A failed probe of
    // the CLIs says nothing about the project list, and vice versa; one shared
    // error flag would make either failure describe both.
    getRuntimeCapabilities()
      .then((data) => { setRuntimes(data); setRuntimesFailed(false); })
      .catch(() => { setRuntimes(null); setRuntimesFailed(true); });
    getSettingsOverview()
      .then((data) => { setOverview(data); setOverviewFailed(false); })
      .catch(() => { setOverview(null); setOverviewFailed(true); });
  }, []);

  const projects = overview?.projects.items ?? [];

  return (
    <DeliveryShell active="Environments">
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <header className="space-y-1">
          <h1 className="text-2xl font-bold">{t("environments.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("environments.subtitle")}</p>
        </header>

        <section className="overflow-hidden rounded-xl border bg-card">
          <header className="flex flex-wrap items-center gap-3 border-b bg-muted/40 px-5 py-3">
            <Server className="size-4" />
            <h2 className="text-base font-semibold">{t("environments.local.title")}</h2>
            <span className="font-mono text-xs text-muted-foreground">
              {runtimes?.platform ?? t("environments.unknownShort")}
            </span>
            <span className="ml-auto rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 dark:text-emerald-400">
              {t("environments.local.current")}
            </span>
          </header>

          <div className="grid gap-6 p-5 lg:grid-cols-2">
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("environments.agents.title")}
              </h3>
              {runtimesFailed && <Notice>{t("environments.agents.unreadable")}</Notice>}
              {runtimes?.adapters.map((adapter) => (
                <div key={adapter.adapter} className="rounded-lg border px-3 py-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{adapter.adapter}</span>
                    <span className="font-mono text-xs text-muted-foreground">
                      {adapter.version ?? t("environments.unknownShort")}
                    </span>
                    <span className="ml-auto">
                      <Tri
                        value={
                          adapter.installation_status === "installed" ? true
                          : adapter.installation_status === "not_found" ? false
                          : null
                        }
                        yes={t("environments.agents.installed")}
                        no={t("environments.agents.missing")}
                        unknown={t("environments.agents.uninspectable")}
                      />
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <Tri
                      value={adapter.auth_check_status === "checked" ? adapter.authenticated : null}
                      yes={t("environments.agents.signedIn")}
                      no={t("environments.agents.signedOut")}
                      unknown={t("environments.agents.authUnknown", { status: adapter.auth_check_status })}
                    />
                    <span>
                      {adapter.token_capture === "supported" ? t("environments.agents.usageRead")
                        : adapter.token_capture === "unsupported" ? t("environments.agents.usageNotRead")
                        : t("environments.agents.usageUnknown")}
                    </span>
                    {adapter.tokens_used > 0 && (
                      <span className="font-mono">{adapter.tokens_used.toLocaleString()}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("environments.projects.title")}
              </h3>
              {overviewFailed && <Notice>{t("environments.projects.unreadable")}</Notice>}
              {overview?.projects.import_status.error && (
                <Notice>
                  {t("environments.projects.importFailed", {
                    error: overview.projects.import_status.error,
                  })}
                </Notice>
              )}
              {projects.map((project) => (
                <div key={project.project_id} className="rounded-lg border px-3 py-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{project.name || project.project_id}</span>
                    <span className="ml-auto text-xs">
                      <Tri
                        value={project.root_is_git}
                        yes={t("environments.projects.git")}
                        no={t("environments.projects.notGit")}
                        unknown={t("environments.projects.gitUnknown")}
                      />
                    </span>
                  </div>
                  <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">{project.root}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("environments.projects.worktrees", {
                      ready: project.workspaces.filter((w) => w.exists === true).length,
                      total: project.workspaces.length,
                      unknown: project.workspaces.filter((w) => w.exists === null).length,
                    })}
                  </p>
                </div>
              ))}
              {overview && projects.length === 0 && (
                <p className="text-sm text-muted-foreground">{t("environments.projects.none")}</p>
              )}
              {overview && (
                <p className="text-xs text-muted-foreground">
                  {t("environments.capacity", {
                    global: overview.execution.max_concurrent_global,
                    project: overview.execution.max_concurrent_per_project,
                    running: overview.runs.running,
                    queued: overview.runs.queued,
                  })}
                </p>
              )}
            </div>
          </div>
        </section>

        {/* The two cards the mockup shows and this build cannot: said as a
            capability that is missing, not drawn as machines that are offline. */}
        <section className="rounded-xl border border-dashed p-5">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <AlertTriangle className="size-4 text-amber-500" />
            {t("environments.remote.title")}
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {t("environments.remote.body")}
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {t("environments.remote.why")}
          </p>
          <Link href="/settings" className="mt-3 inline-block text-sm text-primary hover:underline">
            {t("environments.remote.settingsLink")}
          </Link>
        </section>
      </div>
    </DeliveryShell>
  );
}

/**
 * A three-valued fact. `null` renders as unknown, never as "no".
 *
 * Every boolean on this screen comes from a filesystem probe or a subprocess
 * probe, and both can fail to answer; `agora.fsprobe` keeps absence and unknown
 * apart all the way to here precisely so this component can too.
 */
function Tri({ value, yes, no, unknown }: { value: boolean | null; yes: string; no: string; unknown: string }) {
  if (value === true) {
    return <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400"><CheckCircle2 className="size-3.5" />{yes}</span>;
  }
  if (value === false) {
    return <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400"><XCircle className="size-3.5" />{no}</span>;
  }
  return <span className="inline-flex items-center gap-1 text-muted-foreground"><HelpCircle className="size-3.5" />{unknown}</span>;
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:text-amber-300">
      {children}
    </p>
  );
}
