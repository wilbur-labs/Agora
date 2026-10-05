"use client";

/**
 * Register / edit a remote (SSH) environment — CR-4 slice 1.
 *
 * The judgement (validation, defaults, round-trip) lives in `lib/remote-environments.ts`
 * with node tests; this component only renders fields and reports what that layer
 * decides. It never handles a credential value: the only credential-related field is
 * `secret_ref`, the *name* of an env var (NFR-2 / SECURITY-12).
 */
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/control-plane";
import {
  createRemoteEnvironment,
  updateRemoteEnvironment,
  type RemoteEnvironment,
  type RemoteEnvironmentInput,
} from "@/lib/control-plane";
import {
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
  onSaved,
  onCancel,
}: {
  existing?: RemoteEnvironment;
  /** The projects an environment can belong to; null when the list could not be read. */
  projects: { project_id: string; name: string }[] | null;
  onSaved: (env: RemoteEnvironment) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const isEdit = existing !== undefined;
  const [input, setInput] = useState<RemoteEnvironmentInput>(
    existing ? toEnvironmentInput(existing) : emptyEnvironmentInput(),
  );
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const errorFor = (field: keyof RemoteEnvironmentInput): string | null => {
    const found = errors.find((e) => e.field === field);
    return found ? t(found.messageKey) : null;
  };

  function set<K extends keyof RemoteEnvironmentInput>(field: K, value: RemoteEnvironmentInput[K]) {
    setInput((prev) => ({ ...prev, [field]: value }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = validateEnvironmentInput(input, {
      existingCredentialPresent: isEdit && existing.credential_present === true,
    });
    setErrors(found);
    if (found.length > 0) return;
    setSubmitting(true);
    setSaveError(null);
    try {
      const body = toSubmission(input);
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

  return (
    <form onSubmit={submit} className="space-y-4 rounded-xl border bg-card p-5">
      <h3 className="text-base font-semibold">
        {isEdit ? t("environments.form.editTitle") : t("environments.form.addTitle")}
      </h3>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("environments.form.idLabel")} hint={t("environments.form.idHint")} error={errorFor("environment_id")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono disabled:opacity-60"
            value={input.environment_id}
            disabled={isEdit}
            onChange={(e) => set("environment_id", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.labelLabel")} error={errorFor("label")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={input.label}
            onChange={(e) => set("label", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.hostLabel")} error={errorFor("ssh_host")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={input.ssh_host}
            onChange={(e) => set("ssh_host", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.portLabel")} error={errorFor("ssh_port")}>
          <input
            type="number"
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={input.ssh_port}
            onChange={(e) => set("ssh_port", Number(e.target.value))}
          />
        </Field>
        <Field label={t("environments.form.userLabel")} error={errorFor("ssh_user")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={input.ssh_user}
            onChange={(e) => set("ssh_user", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.credentialSourceLabel")} hint={t("environments.form.credentialSourceHint")}>
          <select
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={input.credential_source}
            onChange={(e) =>
              setInput((prev) =>
                withCredentialSource(prev, e.target.value as RemoteEnvironmentInput["credential_source"]),
              )
            }
          >
            <option value="env_var">{t("environments.form.sourceEnvVar")}</option>
            <option value="key_path">{t("environments.form.sourceKeyPath")}</option>
            <option value="keyring">{t("environments.form.sourceKeyring")}</option>
          </select>
        </Field>
        {input.credential_source === "keyring" ? (
          <Field
            label={t("environments.form.passwordLabel")}
            hint={isEdit ? t("environments.form.passwordHintEdit") : t("environments.form.passwordHint")}
            error={errorFor("password")}
          >
            <input
              type="password"
              autoComplete="new-password"
              className="w-full rounded-md border px-3 py-2 text-sm font-mono"
              value={input.password ?? ""}
              onChange={(e) => set("password", e.target.value ? e.target.value : null)}
            />
          </Field>
        ) : (
          <Field
            label={
              input.credential_source === "key_path"
                ? t("environments.form.keyPathLabel")
                : t("environments.form.secretRefLabel")
            }
            hint={
              input.credential_source === "key_path"
                ? t("environments.form.keyPathHint")
                : t("environments.form.secretRefHint")
            }
            error={errorFor("secret_ref")}
          >
            <input
              className="w-full rounded-md border px-3 py-2 text-sm font-mono"
              value={input.secret_ref}
              onChange={(e) => set("secret_ref", e.target.value)}
            />
          </Field>
        )}
        <Field label={t("environments.form.workspaceLabel")} hint={t("environments.form.workspaceHint")} error={errorFor("remote_workspace")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={input.remote_workspace}
            onChange={(e) => set("remote_workspace", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.projectLabel")} hint={t("environments.form.projectHint")} error={errorFor("project_id")}>
          <select
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={input.project_id}
            onChange={(e) => set("project_id", e.target.value)}
          >
            <option value="">{projects === null ? t("environments.form.projectsUnreadable") : t("environments.form.projectPick")}</option>
            {/* An existing binding stays selectable even when the list could not be read. */}
            {projects === null && input.project_id && <option value={input.project_id}>{input.project_id}</option>}
            {(projects ?? []).map((p) => <option key={p.project_id} value={p.project_id}>{p.name || p.project_id}</option>)}
          </select>
        </Field>
        <Field label={t("environments.form.projectRootLabel")} error={errorFor("remote_project_root")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={input.remote_project_root}
            onChange={(e) => set("remote_project_root", e.target.value)}
          />
        </Field>
        <Field label={t("environments.form.hostKeyModeLabel")}>
          <select
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={input.host_key_mode}
            onChange={(e) => set("host_key_mode", e.target.value as "strict" | "tofu")}
          >
            <option value="strict">{t("environments.form.hostKeyStrict")}</option>
            <option value="tofu">{t("environments.form.hostKeyTofu")}</option>
          </select>
        </Field>
        <Field label={t("environments.form.knownHostsLabel")} hint={t("environments.form.knownHostsHint")} error={errorFor("known_hosts_path")}>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            value={input.known_hosts_path ?? ""}
            onChange={(e) => set("known_hosts_path", e.target.value ? e.target.value : null)}
          />
        </Field>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={input.cleanup_worktree}
          onChange={(e) => set("cleanup_worktree", e.target.checked)}
        />
        {t("environments.form.cleanupLabel")}
      </label>

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
