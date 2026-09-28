/**
 * Judgement layer for registered remote environments (CR-4 slice 1).
 *
 * The page renders; the decisions live here with node tests, following this
 * project's `lib/*.ts` pattern. Two decisions, both bound by the second constitution
 * rule ("读不到 / 不知道 / 没有 are three different statements, never merged"):
 *
 * 1. `environmentReadiness` — what to say about a registered environment. Slice 1
 *    does NOT connect, so it can never claim "reachable". The honest statuses are:
 *    the credential env var is set, or it is not. That is all this build knows, and
 *    it must not be dressed up as "online".
 * 2. `validateEnvironmentInput` — the form's own check, mirroring the server's so a
 *    person is told what is wrong before the round trip, not the same rules re-decided
 *    (the server remains the authority; this is an early, friendlier echo).
 */
import type { RemoteEnvironment, RemoteEnvironmentInput } from "./control-plane";

/**
 * What this build can honestly say about a registered environment. Deliberately not
 * "online/offline": slice 1 never connects, so reachability is unknown, and saying
 * either would be claiming a read this build does not perform. Slice 2 (connection
 * test + TOFU) is what earns a reachability status.
 */
export type EnvironmentReadiness =
  | "credential_set" // secret_ref names an env var that is currently set on the server
  | "credential_missing"; // registered, but that env var is not set here — cannot connect yet

export interface EnvironmentReadinessView {
  readiness: EnvironmentReadiness;
  /** The one-line reason, as a dictionary key the page localizes. */
  detailKey: string;
}

/**
 * Map an environment to what can be said about it now. `secret_present` is the
 * server's answer to "is the env var named by secret_ref set" — a fact, not a guess —
 * so this is a total mapping over a boolean, never an invented third value.
 */
export function environmentReadiness(env: RemoteEnvironment): EnvironmentReadinessView {
  if (env.secret_present) {
    return { readiness: "credential_set", detailKey: "environments.remoteList.credentialSet" };
  }
  return {
    readiness: "credential_missing",
    detailKey: "environments.remoteList.credentialMissing",
  };
}

export interface FieldError {
  field: keyof RemoteEnvironmentInput;
  /** Dictionary key the form localizes. */
  messageKey: string;
}

/** The id rule, mirrored from the server (`validate_environment_id`): letters, numbers,
 * dashes, underscores, and it must start with an alphanumeric (so it is URL-safe). */
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

/** The credential env-var name rule, mirrored from the server
 * (`RemoteEnvironmentRequest.secret_ref`): the AGORA_ namespace. Constrained so the
 * server's presence probe cannot be pointed at an unrelated env var; the form says so
 * before the round trip rather than after a 422. */
const SECRET_REF_PATTERN = /^AGORA_[A-Za-z0-9_]+$/;

/**
 * The form's pre-flight check. Returns every problem at once (not first-only), because
 * a form that surfaces one error, then the next on resubmit, makes the person pay a
 * round trip per field. The server re-validates regardless — this is the friendly echo,
 * not the gate.
 *
 * `tofu` requiring `known_hosts_path` is mirrored from the backend
 * (`RemoteEnvironmentRequest._tofu_needs_known_hosts` at register time, and
 * `RemoteEnvironment._known_hosts_arg` again at connect): tofu with no file to pin
 * into is refused there, so the form says so before the round trip rather than after
 * a 422.
 */
export function validateEnvironmentInput(input: RemoteEnvironmentInput): FieldError[] {
  const errors: FieldError[] = [];
  const required: [keyof RemoteEnvironmentInput, string][] = [
    ["environment_id", "environments.form.errors.idRequired"],
    ["label", "environments.form.errors.labelRequired"],
    ["ssh_host", "environments.form.errors.hostRequired"],
    ["ssh_user", "environments.form.errors.userRequired"],
    ["remote_workspace", "environments.form.errors.workspaceRequired"],
    ["remote_project_root", "environments.form.errors.projectRootRequired"],
    ["secret_ref", "environments.form.errors.secretRefRequired"],
  ];
  for (const [field, messageKey] of required) {
    if (!String(input[field] ?? "").trim()) {
      errors.push({ field, messageKey });
    }
  }

  if (input.environment_id.trim() && !ID_PATTERN.test(input.environment_id.trim())) {
    errors.push({ field: "environment_id", messageKey: "environments.form.errors.idPattern" });
  }

  if (input.secret_ref.trim() && !SECRET_REF_PATTERN.test(input.secret_ref.trim())) {
    errors.push({ field: "secret_ref", messageKey: "environments.form.errors.secretRefPattern" });
  }

  if (!Number.isInteger(input.ssh_port) || input.ssh_port < 1 || input.ssh_port > 65535) {
    errors.push({ field: "ssh_port", messageKey: "environments.form.errors.portRange" });
  }

  if (input.host_key_mode === "tofu" && !String(input.known_hosts_path ?? "").trim()) {
    errors.push({
      field: "known_hosts_path",
      messageKey: "environments.form.errors.tofuNeedsKnownHosts",
    });
  }

  return errors;
}

/** A blank input for a new environment, with the safe defaults (strict host key, port 22). */
export function emptyEnvironmentInput(): RemoteEnvironmentInput {
  return {
    environment_id: "",
    label: "",
    ssh_host: "",
    ssh_port: 22,
    ssh_user: "",
    remote_workspace: "",
    remote_project_root: "",
    secret_ref: "",
    host_key_mode: "strict",
    known_hosts_path: null,
    cleanup_worktree: false,
  };
}

/** Turn a stored environment back into an editable input (drops the read-only fields). */
export function toEnvironmentInput(env: RemoteEnvironment): RemoteEnvironmentInput {
  return {
    environment_id: env.environment_id,
    label: env.label,
    ssh_host: env.ssh_host,
    ssh_port: env.ssh_port,
    ssh_user: env.ssh_user,
    remote_workspace: env.remote_workspace,
    remote_project_root: env.remote_project_root,
    secret_ref: env.secret_ref,
    host_key_mode: env.host_key_mode,
    known_hosts_path: env.known_hosts_path,
    cleanup_worktree: env.cleanup_worktree,
  };
}
