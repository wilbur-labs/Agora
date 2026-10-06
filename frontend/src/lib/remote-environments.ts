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
import type { RemoteEnvironment, RemoteEnvironmentInput, SshConfigHost } from "./control-plane";

/**
 * What this build can honestly say about a registered environment. Deliberately not
 * "online/offline": this build does not connect (connection test is the rest of slice 2),
 * so reachability is unknown. What it knows is whether the credential is present, per
 * `credential_source` — and a third state, "cannot tell" (e.g. no keyring backend on this
 * machine), kept apart from "not set" (adr/0001, second constitution rule).
 */
export type EnvironmentReadiness =
  | "credential_set" // the credential (env var / key file / keyring entry) is present
  | "credential_missing" // registered, but the credential is not present here — cannot connect yet
  | "credential_unknown"; // cannot tell whether it is present (e.g. no keyring backend)

export interface EnvironmentReadinessView {
  readiness: EnvironmentReadiness;
  /** The one-line reason, as a dictionary key the page localizes. */
  detailKey: string;
}

/**
 * Map an environment to what can be said about it now. `credential_present` is the
 * server's three-state answer (true / false / null) — a fact, not a guess. null means
 * "this machine cannot tell" (no keyring backend), which is reported as its own state,
 * never merged into "missing".
 */
export function environmentReadiness(env: RemoteEnvironment): EnvironmentReadinessView {
  if (env.credential_present === null) {
    return {
      readiness: "credential_unknown",
      detailKey: "environments.remoteList.credentialUnknown",
    };
  }
  if (env.credential_present) {
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

/** The credential env-var name rule, mirrored from the server: the AGORA_ namespace.
 * Applies ONLY to credential_source "env_var" (a key_path secret_ref is a file path, a
 * keyring secret_ref is a free label). Constrained so the server's presence probe cannot
 * be pointed at an unrelated env var. */
const ENV_VAR_SECRET_REF_PATTERN = /^AGORA_[A-Za-z0-9_]+$/;

/**
 * The form's pre-flight check. Returns every problem at once (not first-only), because
 * a form that surfaces one error, then the next on resubmit, makes the person pay a
 * round trip per field. The server re-validates regardless — this is the friendly echo,
 * not the gate.
 *
 * `existingCredentialPresent` mirrors the server's update rule (adr/0001): editing a
 * keyring environment without re-entering the password is allowed *when a password is
 * already stored* (A semantics — change a label, keep the secret). On a create, or when
 * switching to keyring with nothing stored, a password is required.
 *
 * `tofu` requiring `known_hosts_path` is mirrored from the backend
 * (`RemoteEnvironmentRequest._validate_credential` at register time, and
 * `RemoteEnvironment._known_hosts_arg` again at connect).
 */
export function validateEnvironmentInput(
  input: RemoteEnvironmentInput,
  { existingCredentialPresent = false }: { existingCredentialPresent?: boolean } = {},
): FieldError[] {
  const errors: FieldError[] = [];
  const required: [keyof RemoteEnvironmentInput, string][] = [
    ["environment_id", "environments.form.errors.idRequired"],
    ["label", "environments.form.errors.labelRequired"],
    ["ssh_host", "environments.form.errors.hostRequired"],
    ["ssh_user", "environments.form.errors.userRequired"],
    ["remote_workspace", "environments.form.errors.workspaceRequired"],
    ["remote_project_root", "environments.form.errors.projectRootRequired"],
    ["project_id", "environments.form.errors.projectRequired"],
  ];
  // keyring and ssh_default hide the secret_ref field (only a label there, filled in by
  // `toSubmission`), so requiring it would block on a field the person cannot see.
  if (!hidesSecretRef(input.credential_source)) {
    required.push(["secret_ref", "environments.form.errors.secretRefRequired"]);
  }
  for (const [field, messageKey] of required) {
    if (!String(input[field] ?? "").trim()) {
      errors.push({ field, messageKey });
    }
  }

  if (input.environment_id.trim() && !ID_PATTERN.test(input.environment_id.trim())) {
    errors.push({ field: "environment_id", messageKey: "environments.form.errors.idPattern" });
  }

  // Per-source credential rules (adr/0001).
  if (input.credential_source === "env_var") {
    if (
      input.secret_ref.trim() &&
      !ENV_VAR_SECRET_REF_PATTERN.test(input.secret_ref.trim())
    ) {
      errors.push({ field: "secret_ref", messageKey: "environments.form.errors.secretRefPattern" });
    }
  } else if (input.credential_source === "keyring") {
    // A password is needed unless one is already stored for this environment (edit).
    if (!String(input.password ?? "").trim() && !existingCredentialPresent) {
      errors.push({ field: "password", messageKey: "environments.form.errors.passwordRequired" });
    }
  }
  // key_path: secret_ref is a file path; no pattern to enforce client-side.

  // The remote provisioner builds worktrees only inside the project root; mirrored from
  // the server (`remote_path_within`) so the form says it before the host refuses it.
  if (
    input.remote_workspace.trim() && input.remote_project_root.trim()
    && !remotePathWithin(input.remote_workspace.trim(), input.remote_project_root.trim())
  ) {
    errors.push({ field: "remote_workspace", messageKey: "environments.form.errors.workspaceOutsideRoot" });
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

/** The label a keyring environment's `secret_ref` gets when the person never typed one.
 * For keyring the server only needs a non-empty label; the password is keyed by the
 * environment id, not by this. */
export const KEYRING_SECRET_REF_LABEL = "os-keyring";
/** The label an ssh_default environment's unused `secret_ref` gets (CR-24). */
export const SSH_DEFAULT_SECRET_REF_LABEL = "ssh-default";

/** Sources whose `secret_ref` is not something the person types. */
export function hidesSecretRef(source: RemoteEnvironmentInput["credential_source"]): boolean {
  return source === "keyring" || source === "ssh_default";
}

function autoLabelOf(source: RemoteEnvironmentInput["credential_source"]): string | null {
  return source === "keyring" ? KEYRING_SECRET_REF_LABEL : source === "ssh_default" ? SSH_DEFAULT_SECRET_REF_LABEL : null;
}

/** Switch the credential source. Leaving keyring drops the typed password (its input is
 * hidden from then on, and the server refuses a password for any other source) and the
 * auto label (it is not a valid env var name or key path). */
export function withCredentialSource(
  input: RemoteEnvironmentInput,
  source: RemoteEnvironmentInput["credential_source"],
): RemoteEnvironmentInput {
  if (source === input.credential_source) return input;
  const next = { ...input, credential_source: source };
  if (input.credential_source === "keyring") next.password = null;
  const label = autoLabelOf(input.credential_source);
  if (label !== null && next.secret_ref === label) next.secret_ref = "";
  return next;
}

/** What is actually sent: a password only for keyring, and a keyring label filled in
 * when the (hidden) secret_ref is blank. */
export function toSubmission(input: RemoteEnvironmentInput): RemoteEnvironmentInput {
  const body = input.credential_source === "keyring" ? input : { ...input, password: null };
  const label = autoLabelOf(input.credential_source);
  return label !== null && !body.secret_ref.trim() ? { ...body, secret_ref: label } : body;
}

/** Whether POSIX `path` lies strictly inside `root`, compared by path components
 * (`/a/bc` is not under `/a/b`). Lexical, like the server's check; the host re-checks
 * with symlinks resolved. */
export function remotePathWithin(path: string, root: string): boolean {
  if (!path.startsWith("/") || !root.startsWith("/")) return false;
  // "." segments vanish, as in the server's PurePosixPath; ".." is refused below.
  const child = path.split("/").filter((part) => part && part !== ".");
  const parent = root.split("/").filter((part) => part && part !== ".");
  if (child.includes("..")) return false;
  if (child.length <= parent.length) return false;
  return parent.every((part, i) => child[i] === part);
}

// --- registering with only what the person knows (CR-26) -----------------------------
//
// Three things only the person knows: which host (and account), which project, and where
// that project lives on the host. Everything else is derived until they edit it.

/** Fields kept under "advanced"; the form opens that section when one of them errs. */
export const ADVANCED_FIELDS: readonly (keyof RemoteEnvironmentInput)[] = [
  "environment_id", "label", "ssh_port", "credential_source", "secret_ref", "password",
  "remote_workspace", "host_key_mode", "known_hosts_path", "cleanup_worktree",
];

/** A valid environment id from free text: `10.36.94.35` -> `10-36-94-35`,
 * `AI-Server` -> `ai-server`. Empty when nothing usable is left. */
export function environmentIdFrom(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[^a-z0-9]+/, "")
    .replace(/-+$/, "")
    .slice(0, 64);
}

/** The worktree directory for a project root: `<root>/.agora/workspaces` — inside the
 * root, as the remote provisioner requires. Empty for a root that is not absolute. */
export function defaultWorkspaceFor(root: string): string {
  const trimmed = root.trim().replace(/\/+$/, "");
  return trimmed.startsWith("/") && trimmed.length > 1 ? `${trimmed}/.agora/workspaces` : "";
}

/** `base`, or `base-2`, `base-3`… — the first not in `taken`. */
export function unusedId(base: string, taken: ReadonlySet<string>): string {
  if (!base || !taken.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const suffix = `-${n}`;
    const candidate = `${base.slice(0, 64 - suffix.length)}${suffix}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** `input` with every field the person has not edited derived from the ones they have.
 * `defaultProjectId` is the project to assume when none is chosen (the current one);
 * `takenIds` are the registered environment ids — one host serves one environment per
 * project, so the derived id carries the project and steps around ids already taken. */
export function autoFilled(
  input: RemoteEnvironmentInput,
  edited: ReadonlySet<keyof RemoteEnvironmentInput>,
  { defaultProjectId = null, takenIds = new Set<string>() }: {
    defaultProjectId?: string | null; takenIds?: ReadonlySet<string>;
  } = {},
): RemoteEnvironmentInput {
  const next = { ...input };
  if (!edited.has("project_id") && !input.project_id && defaultProjectId) {
    next.project_id = defaultProjectId;
  }
  if (!edited.has("label")) {
    next.label = input.ssh_user.trim() && input.ssh_host.trim()
      ? `${input.ssh_user.trim()}@${input.ssh_host.trim()}`
      : input.ssh_host.trim();
  }
  if (!edited.has("environment_id")) {
    const place = edited.has("label") ? next.label : input.ssh_host;
    const base = environmentIdFrom(place.trim() ? [next.project_id, place].filter(Boolean).join("-") : "");
    next.environment_id = unusedId(base, takenIds);
  }
  if (!edited.has("remote_workspace")) {
    next.remote_workspace = defaultWorkspaceFor(input.remote_project_root);
  }
  return next;
}

/** The fields choosing an ~/.ssh/config host fills in (CR-25): the address (HostName, or
 * the alias itself when none is set — ssh resolves it the same way), the account and port
 * as the config gives them — and when it gives none, reset (empty account, port 22) rather
 * than kept from a host picked before — and its IdentityFile as a key-path credential.
 * These count as the person's choice: derivation never overwrites them. */
export function applySshHost(
  input: RemoteEnvironmentInput,
  host: SshConfigHost,
): { input: RemoteEnvironmentInput; edited: (keyof RemoteEnvironmentInput)[] } {
  const next = {
    ...input,
    ssh_host: host.hostname || host.alias,
    ssh_user: host.user ?? "",
    ssh_port: host.port ?? 22,
  };
  const edited: (keyof RemoteEnvironmentInput)[] = ["ssh_host", "ssh_user", "ssh_port"];
  if (host.identity_file) {
    next.credential_source = "key_path";
    next.secret_ref = host.identity_file;
    next.password = null;
    edited.push("credential_source", "secret_ref");
  } else if (input.credential_source === "key_path") {
    // A key path left over from a previously picked host does not belong to this one.
    next.credential_source = "ssh_default";
    next.secret_ref = "";
  }
  return { input: next, edited };
}

/** Whether any error sits in a field folded under "advanced" (so the form opens it). */
export function advancedHasError(errors: FieldError[]): boolean {
  return errors.some((e) => (ADVANCED_FIELDS as readonly string[]).includes(e.field));
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
    // Like the ssh command: nothing to type (CR-24). The commonest case, so the default.
    credential_source: "ssh_default",
    password: null,
    host_key_mode: "strict",
    known_hosts_path: null,
    cleanup_worktree: false,
    project_id: "",
  };
}

/** Turn a stored environment back into an editable input (drops the read-only fields).
 * `password` is always null: a keyring password cannot be read back, so editing starts
 * blank and an empty password on save means "keep the stored one" (adr/0001). */
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
    credential_source: env.credential_source,
    password: null,
    host_key_mode: env.host_key_mode,
    known_hosts_path: env.known_hosts_path,
    cleanup_worktree: env.cleanup_worktree,
    project_id: env.project_id ?? "",
  };
}

// --- choosing where a run executes (CR-4: the environment dropdown beside the box) ---

/** What the dispatch box knows about registered environments. Three states kept apart:
 * still loading, could not be read, and read (possibly empty). */
export type EnvironmentListState =
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "ready"; environments: RemoteEnvironment[] };

/** One choice in the dropdown. `value` "" is the adapter's own place (this machine,
 * or the remote its configuration names); anything else is a registered environment id. */
export interface DispatchEnvironmentChoice {
  value: string;
  /** Registered: its label. Own place: null (the caller says it, from the adapter). */
  label: string | null;
  /** Registered: `user@host[:port]`. */
  target: string | null;
}

export const OWN_PLACE = "";

/** The dropdown's choices, and what to say beside it. A list that could not be read
 * is "failed", never shown as if there were no environments. */
export function dispatchEnvironmentChoices(state: EnvironmentListState, projectId: string): {
  choices: DispatchEnvironmentChoice[];
  notice: "loading" | "failed" | null;
} {
  const own: DispatchEnvironmentChoice = { value: OWN_PLACE, label: null, target: null };
  if (state.kind !== "ready") return { choices: [own], notice: state.kind };
  return {
    choices: [
      own,
      // Only this project's environments: another project's root is that project's
      // repository (one environment = one project; the server refuses it too).
      ...state.environments.filter((env) => env.project_id === projectId).map((env) => ({
        value: env.environment_id,
        label: env.label,
        target: `${env.ssh_user}@${env.ssh_host}${env.ssh_port !== 22 ? `:${env.ssh_port}` : ""}`,
      })),
    ],
    notice: null,
  };
}

/** The environment a send goes to, decided at the moment of sending. The adapter's own
 * place is `null`. A chosen environment that is no longer in a list that WAS read is
 * refused ("gone") rather than quietly replaced by the adapter's own place; one that
 * cannot be checked (list loading or unreadable) is passed through — the server
 * refuses an unknown id itself. */
export function dispatchEnvironmentOf(
  selected: string,
  state: EnvironmentListState,
  projectId: string,
): { ok: true; environmentId: string | null } | { ok: false; reason: "gone" } {
  if (selected === OWN_PLACE) return { ok: true, environmentId: null };
  if (state.kind === "ready" && !state.environments.some(
    (env) => env.environment_id === selected && env.project_id === projectId,
  )) {
    return { ok: false, reason: "gone" };
  }
  return { ok: true, environmentId: selected };
}
