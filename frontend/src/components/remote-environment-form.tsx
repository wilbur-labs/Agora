"use client";

/**
 * Register / edit a remote (SSH) environment — CR-4, simplified in CR-26.
 *
 * Registering asks only what the person knows: the host and account, the project, and
 * where that project lives on the host. Everything else is derived (lib `autoFilled`)
 * until the person edits it, is shown in one line so nothing is filled silently, and
 * sits under "advanced". Editing an existing environment shows every field as stored.
 *
 * The judgement (validation, defaults, derivation, round-trip) lives in
 * `lib/remote-environments.ts` with node tests; this component only renders. A keyring
 * password is the only credential value it ever holds, write-only (adr/0001).
 */
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/control-plane";
import {
  createRemoteEnvironment,
  updateRemoteEnvironment,
  type RemoteEnvironment,
  type RemoteEnvironmentInput,
} from "@/lib/control-plane";
import {
  advancedHasError,
  autoFilled,
  emptyEnvironmentInput,
  toEnvironmentInput,
  toSubmission,
  validateEnvironmentInput,
  withCredentialSource,
  type FieldError,
} from "@/lib/remote-environments";
import { useI18n } from "@/lib/i18n";

export function RemoteEnvironmentForm({
  existing,
  projects,
  defaultProjectId = null,
  takenIds,
  onSaved,
  onCancel,
}: {
  existing?: RemoteEnvironment;
  /** The projects an environment can belong to; null when the list could not be read. */
  projects: { project_id: string; name: string }[] | null;
  /** The project assumed until one is chosen (the current one). */
  defaultProjectId?: string | null;
  /** Ids already registered, so a derived id does not collide (409). */
  takenIds?: ReadonlySet<string>;
  onSaved: (env: RemoteEnvironment) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const isEdit = existing !== undefined;
  const [input, setInput] = useState<RemoteEnvironmentInput>(
    existing ? toEnvironmentInput(existing) : emptyEnvironmentInput(),
  );
  // Fields the person typed into: those are never overwritten by derivation.
  const [edited, setEdited] = useState<ReadonlySet<keyof RemoteEnvironmentInput>>(new Set());
  const [advancedOpen, setAdvancedOpen] = useState(isEdit);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // What is shown and what is sent: derived for a new environment, as stored for an edit.
  const effective = useMemo(
    () => (isEdit ? input : autoFilled(input, edited, { defaultProjectId, takenIds })),
    [isEdit, input, edited, defaultProjectId, takenIds],
  );

  const errorFor = (field: keyof RemoteEnvironmentInput): string | null => {
    const found = errors.find((e) => e.field === field);
    return found ? t(found.messageKey) : null;
  };

  function set<K extends keyof RemoteEnvironmentInput>(field: K, value: RemoteEnvironmentInput[K]) {
    // Start from what is on screen, so editing one derived field keeps the others' values.
    setInput((prev) => ({ ...(isEdit ? prev : autoFilled(prev, edited, { defaultProjectId, takenIds })), [field]: value }));
    setEdited((prev) => new Set(prev).add(field));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = validateEnvironmentInput(effective, {
      existingCredentialPresent: isEdit && existing.credential_present === true,
    });
    setErrors(found);
    if (advancedHasError(found)) setAdvancedOpen(true);
    if (found.length > 0) return;
    setSubmitting(true);
    setSaveError(null);
    try {
      const body = toSubmission(effective);
      const saved = isEdit
        ? await updateRemoteEnvironment(existing.environment_id, body)
        : await createRemoteEnvironment(body);
      onSaved(saved);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setSaveError(t("environments.form.saveFailed", { error: message }));
    } finally {
      setSubmitting(false);
    }
  }

  const credentialName = t(
    effective.credential_source === "ssh_default" ? "environments.form.sourceSshDefault"
      : effective.credential_source === "keyring" ? "environments.form.sourceKeyring"
      : effective.credential_source === "key_path" ? "environments.form.sourceKeyPath"
      : "environments.form.sourceEnvVar",
  );

  return (
    <form onSubmit={submit} className="space-y-4 rounded-xl border bg-card p-5">
      <h3 className="text-base font-semibold">
        {isEdit ? t("environments.form.editTitle") : t("environments.form.addTitle")}
      </h3>

      {/* What only the person knows. */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("environments.form.hostLabel")} hint={t("environments.form.hostHint")} error={errorFor("ssh_host")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={effective.ssh_host}
            placeholder="10.36.94.35"
            onChange={(e) => set("ssh_host", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.userLabel")} error={errorFor("ssh_user")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={effective.ssh_user}
            onChange={(e) => set("ssh_user", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.projectLabel")} hint={t("environments.form.projectHint")} error={errorFor("project_id")}>
          <select
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={effective.project_id}
            onChange={(e) => set("project_id", e.target.value)}
          >
            <option value="">{projects === null ? t("environments.form.projectsUnreadable") : t("environments.form.projectPick")}</option>
            {/* An existing binding stays selectable even when the list could not be read. */}
            {projects === null && effective.project_id && <option value={effective.project_id}>{effective.project_id}</option>}
            {(projects ?? []).map((p) => <option key={p.project_id} value={p.project_id}>{p.name || p.project_id}</option>)}
          </select>
        </Field>
        <Field label={t("environments.form.projectRootLabel")} hint={t("environments.form.projectRootHint")} error={errorFor("remote_project_root")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={effective.remote_project_root}
            placeholder="/workplace/ai-projects/MindRAG"
            onChange={(e) => set("remote_project_root", e.target.value)}
          />
        </Field>
      </div>

      {/* Everything derived, said in one line: nothing is filled in silently. */}
      {!isEdit && (
        <p className="rounded-md bg-muted/50 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          {t("environments.form.willRegister", {
            id: effective.environment_id || "—",
            label: effective.label || "—",
            port: effective.ssh_port,
            credential: credentialName,
            workspace: effective.remote_workspace || "—",
          })}
        </p>
      )}

      <details
        open={advancedOpen}
        onToggle={(e) => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
        className="rounded-md border px-3 py-2"
      >
        <summary className="cursor-pointer text-sm font-medium">{t("environments.form.advanced")}</summary>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <Field label={t("environments.form.idLabel")} hint={t("environments.form.idHint")} error={errorFor("environment_id")}>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm font-mono disabled:opacity-60"
              value={effective.environment_id}
              disabled={isEdit}
              onChange={(e) => set("environment_id", e.target.value)}
            />
          </Field>
          <Field label={t("environments.form.labelLabel")} error={errorFor("label")}>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={effective.label}
              onChange={(e) => set("label", e.target.value)}
            />
          </Field>
          <Field label={t("environments.form.portLabel")} error={errorFor("ssh_port")}>
            <input
              type="number"
              className="w-full rounded-md border px-3 py-2 text-sm font-mono"
              value={effective.ssh_port}
              onChange={(e) => set("ssh_port", Number(e.target.value))}
            />
          </Field>
          <Field label={t("environments.form.credentialSourceLabel")} hint={t("environments.form.credentialSourceHint")}>
            <select
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={effective.credential_source}
              onChange={(e) => {
                const source = e.target.value as RemoteEnvironmentInput["credential_source"];
                setInput((prev) => withCredentialSource(isEdit ? prev : autoFilled(prev, edited, { defaultProjectId, takenIds }), source));
                setEdited((prev) => new Set(prev).add("credential_source"));
              }}
            >
              <option value="ssh_default">{t("environments.form.sourceSshDefault")}</option>
              <option value="keyring">{t("environments.form.sourceKeyring")}</option>
              <option value="key_path">{t("environments.form.sourceKeyPath")}</option>
              <option value="env_var">{t("environments.form.sourceEnvVar")}</option>
            </select>
          </Field>
          {effective.credential_source === "ssh_default" ? (
            <Field label={t("environments.form.sshDefaultLabel")} hint={t("environments.form.sshDefaultHint")}>
              <p className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
                {t("environments.form.sshDefaultBody")}
              </p>
            </Field>
          ) : effective.credential_source === "keyring" ? (
            <Field
              label={t("environments.form.passwordLabel")}
              hint={isEdit ? t("environments.form.passwordHintEdit") : t("environments.form.passwordHint")}
              error={errorFor("password")}
            >
              <input
                type="password"
                autoComplete="new-password"
                className="w-full rounded-md border px-3 py-2 text-sm font-mono"
                value={effective.password ?? ""}
                onChange={(e) => set("password", e.target.value ? e.target.value : null)}
              />
            </Field>
          ) : (
            <Field
              label={
                effective.credential_source === "key_path"
                  ? t("environments.form.keyPathLabel")
                  : t("environments.form.secretRefLabel")
              }
              hint={
                effective.credential_source === "key_path"
                  ? t("environments.form.keyPathHint")
                  : t("environments.form.secretRefHint")
              }
              error={errorFor("secret_ref")}
            >
              <input
                className="w-full rounded-md border px-3 py-2 text-sm font-mono"
                value={effective.secret_ref}
                onChange={(e) => set("secret_ref", e.target.value)}
              />
            </Field>
          )}
          <Field label={t("environments.form.workspaceLabel")} hint={t("environments.form.workspaceHint")} error={errorFor("remote_workspace")}>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm font-mono"
              value={effective.remote_workspace}
              onChange={(e) => set("remote_workspace", e.target.value)}
            />
          </Field>
          <Field label={t("environments.form.hostKeyModeLabel")}>
            <select
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={effective.host_key_mode}
              onChange={(e) => set("host_key_mode", e.target.value as "strict" | "tofu")}
            >
              <option value="strict">{t("environments.form.hostKeyStrict")}</option>
              <option value="tofu">{t("environments.form.hostKeyTofu")}</option>
            </select>
          </Field>
          <Field label={t("environments.form.knownHostsLabel")} hint={t("environments.form.knownHostsHint")} error={errorFor("known_hosts_path")}>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm font-mono"
              value={effective.known_hosts_path ?? ""}
              onChange={(e) => set("known_hosts_path", e.target.value ? e.target.value : null)}
            />
          </Field>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={effective.cleanup_worktree}
            onChange={(e) => set("cleanup_worktree", e.target.checked)}
          />
          {t("environments.form.cleanupLabel")}
        </label>
      </details>

      {saveError && (
        <p className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-700 dark:text-red-400">
          {saveError}
        </p>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={submitting}>{t("environments.form.save")}</Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
          {t("environments.form.cancel")}
        </Button>
      </div>
    </form>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint && !error && <span className="block text-xs text-muted-foreground">{hint}</span>}
      {error && <span className="block text-xs text-red-600 dark:text-red-400">{error}</span>}
    </label>
  );
}
