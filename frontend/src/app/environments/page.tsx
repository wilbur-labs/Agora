"use client";

/**
 * Environments — mockup v2, screen 3.
 *
 * The top card is this machine, from real reads. Below it is the remote-environment
 * registry (CR-4): registered SSH hosts, with a form to add, edit and delete them. A
 * run uses one when it is chosen beside the message box at dispatch time (project
 * stream); otherwise it runs where its executor is configured. Not on this page yet: a
 * test connection and TOFU fingerprint confirmation. A credential value is never shown:
 * the list reports only whether it is present (env var set / key file exists / keyring
 * entry exists), or that this machine cannot tell.
 */

import { platformLabelOf } from "@/lib/shell";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, HelpCircle, Pencil, Plus, Server, Trash2, XCircle } from "lucide-react";

import { DeliveryShell } from "@/components/delivery-shell";
import { RemoteEnvironmentForm } from "@/components/remote-environment-form";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import {
  deleteRemoteEnvironment,
  getSettingsOverview,
  listProjects,
  listRemoteEnvironments,
  type RemoteEnvironment,
  type RuntimeCapabilities,
  type SettingsOverview,
} from "@/lib/control-plane";
import { environmentReadiness } from "@/lib/remote-environments";
import { probeRuntimes } from "@/lib/runtime-probe";

export default function EnvironmentsPage() {
  const { t } = useI18n();
  const [runtimes, setRuntimes] = useState<RuntimeCapabilities | null>(null);
  const [runtimesFailed, setRuntimesFailed] = useState(false);
  const [overview, setOverview] = useState<SettingsOverview | null>(null);
  const [overviewFailed, setOverviewFailed] = useState(false);

  const [remotes, setRemotes] = useState<RemoteEnvironment[] | null>(null);
  const [remotesFailed, setRemotesFailed] = useState(false);
  // The form's target: null = closed, "new" = registering, or an environment to edit.
  const [formTarget, setFormTarget] = useState<"new" | RemoteEnvironment | null>(null);
  // Which projects an environment can belong to (one environment = one project). null
  // = the list could not be read; the form then says so instead of offering nothing.
  const [projectChoices, setProjectChoices] = useState<{ project_id: string; name: string }[] | null>(null);
  const [currentProject, setCurrentProject] = useState<string | null>(null);

  function reloadRemotes() {
    listRemoteEnvironments()
      .then((data) => { setRemotes(data.environments); setRemotesFailed(false); })
      .catch(() => { setRemotes(null); setRemotesFailed(true); });
  }

  useEffect(() => {
    listProjects()
      .then((data) => {
        setProjectChoices(data.items.map((p) => ({ project_id: p.project_id, name: p.name })));
        setCurrentProject(data.items.some((p) => p.project_id === data.current) ? data.current : null);
      })
      .catch(() => setProjectChoices(null));
  }, []);

  useEffect(() => {
    // Two independent reads, each reporting only for itself. A failed probe of
    // the CLIs says nothing about the project list, and vice versa; one shared
    // error flag would make either failure describe both.
    // The shared probe (lib/runtime-probe.ts): the top bar asked already, so this
    // joins that answer instead of spawning the CLIs a second time on one screen.
    probeRuntimes()
      .then((data) => { setRuntimes(data); setRuntimesFailed(false); })
      .catch(() => { setRuntimes(null); setRuntimesFailed(true); });
    getSettingsOverview()
      .then((data) => { setOverview(data); setOverviewFailed(false); })
      .catch(() => { setOverview(null); setOverviewFailed(true); });
    reloadRemotes();
  }, []);

  async function handleDelete(environmentId: string) {
    await deleteRemoteEnvironment(environmentId);
    reloadRemotes();
  }

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
              {runtimes?.platform ? platformLabelOf(runtimes.platform) : t("environments.unknownShort")}
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
                  <p className="mt-0.5 break-all font-mono text-[0.75rem] text-muted-foreground">{project.root}</p>
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

        {/* The remote-environment registry (CR-4 slice 1): registered SSH hosts a run
            could use. Registering records the host; it does not connect yet. */}
        <section className="space-y-4 rounded-xl border p-5">
          <header className="flex flex-wrap items-center gap-3">
            <Server className="size-4" />
            <div>
              <h2 className="text-base font-semibold">{t("environments.remoteList.title")}</h2>
              <p className="text-sm text-muted-foreground">{t("environments.remoteList.subtitle")}</p>
            </div>
            {formTarget === null && (
              <Button className="ml-auto" onClick={() => setFormTarget("new")}>
                <Plus className="size-4" />
                {t("environments.remoteList.add")}
              </Button>
            )}
          </header>

          {formTarget !== null && (
            <RemoteEnvironmentForm
              existing={formTarget === "new" ? undefined : formTarget}
              projects={projectChoices}
              defaultProjectId={currentProject}
              takenIds={new Set((remotes ?? []).map((env) => env.environment_id))}
              onSaved={() => { setFormTarget(null); reloadRemotes(); }}
              onCancel={() => setFormTarget(null)}
            />
          )}

          {remotesFailed && <Notice>{t("environments.remoteList.unreadable")}</Notice>}

          {remotes && remotes.length === 0 && formTarget === null && (
            <p className="text-sm text-muted-foreground">{t("environments.remoteList.empty")}</p>
          )}

          <div className="space-y-3">
            {remotes?.map((env) => {
              const readiness = environmentReadiness(env);
              // readiness already chose the sentence off `credential_present` (a
              // three-state fact); resolve it once and pass it to all three Tri slots so
              // the icon/colour follows the boolean while the text is already correct —
              // no re-resolving a key against a second, mismatched param set.
              const readinessDetail = t(readiness.detailKey);
              return (
                <div key={env.environment_id} className="rounded-lg border px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{env.label}</span>
                    <span className="font-mono text-xs text-muted-foreground">
                      {t("environments.remoteList.hostLine", {
                        user: env.ssh_user, host: env.ssh_host, port: env.ssh_port,
                      })}
                    </span>
                    <span className={env.project_id ? "text-xs text-muted-foreground" : "text-xs text-amber-700 dark:text-amber-400"}>
                      {env.project_id
                        ? t("environments.remoteList.projectLine", {
                          project: projectChoices?.find((p) => p.project_id === env.project_id)?.name || env.project_id,
                        })
                        : t("environments.remoteList.unbound")}
                    </span>
                    <span className="ml-auto flex items-center gap-2">
                      <Button variant="outline" size="sm" onClick={() => setFormTarget(env)}>
                        <Pencil className="size-3.5" />
                        {t("environments.remoteList.edit")}
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => handleDelete(env.environment_id)}>
                        <Trash2 className="size-3.5" />
                        {t("environments.remoteList.delete")}
                      </Button>
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <Tri
                      value={env.credential_present}
                      yes={readinessDetail}
                      no={readinessDetail}
                      unknown={readinessDetail}
                    />
                    <span>
                      {env.host_key_mode === "tofu"
                        ? t("environments.remoteList.hostKeyTofu")
                        : t("environments.remoteList.hostKeyStrict")}
                    </span>
                    <span className="font-mono">
                      {env.credential_source === "ssh_default"
                        ? t("environments.remoteList.credentialViaSshDefault")
                        : env.credential_source === "keyring"
                        ? t("environments.remoteList.credentialViaKeyring")
                        : env.credential_source === "key_path"
                        ? t("environments.remoteList.credentialViaKeyPath", { path: env.secret_ref })
                        : t("environments.remoteList.secretRefLabel", { secretRef: env.secret_ref })}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("environments.remoteList.notWiredNote")}
          </p>
        </section>

        {/* What a registered environment does when chosen, and what this page cannot do
            yet (test connection, fingerprint confirmation) — said, not implied. */}
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
