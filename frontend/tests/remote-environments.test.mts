import assert from "node:assert/strict";
import { test } from "node:test";

import type { RemoteEnvironment, RemoteEnvironmentInput } from "../src/lib/control-plane.ts";
import {
  emptyEnvironmentInput,
  environmentReadiness,
  KEYRING_SECRET_REF_LABEL,
  SSH_DEFAULT_SECRET_REF_LABEL,
  OWN_PLACE,
  applySshHost,
  unusedId,
  advancedHasError,
  autoFilled,
  defaultWorkspaceFor,
  environmentIdFrom,
  remotePathWithin,
  dispatchEnvironmentChoices,
  dispatchEnvironmentOf,
  toEnvironmentInput,
  toSubmission,
  validateEnvironmentInput,
  withCredentialSource,
} from "../src/lib/remote-environments.ts";

function env(overrides: Partial<RemoteEnvironment> = {}): RemoteEnvironment {
  return {
    environment_id: "kimono-vps",
    label: "kimono VPS",
    ssh_host: "vps.example.test",
    ssh_port: 22,
    ssh_user: "ubuntu",
    remote_workspace: "/home/ubuntu/kimono/.agora/ws/codex",
    remote_project_root: "/home/ubuntu/kimono",
    secret_ref: "AGORA_SSH_KEY_kimono",
    credential_source: "env_var",
    credential_present: true,
    host_key_mode: "strict",
    known_hosts_path: "/home/ubuntu/.agora/known_hosts",
    cleanup_worktree: false,
    project_id: "p1",
    created_at: "2026-09-28T00:00:00Z",
    updated_at: "2026-09-28T00:00:00Z",
    ...overrides,
  };
}

function input(overrides: Partial<RemoteEnvironmentInput> = {}): RemoteEnvironmentInput {
  return { ...emptyEnvironmentInput(), project_id: "p1", ...overrides };
}

// --- readiness: never claims a read this build does not perform ---------------------

test("a set credential reads as credential_set", () => {
  assert.equal(environmentReadiness(env({ credential_present: true })).readiness, "credential_set");
});

test("an unset credential reads as credential_missing, not as offline", () => {
  const view = environmentReadiness(env({ credential_present: false }));
  assert.equal(view.readiness, "credential_missing");
  // Deliberately no "online"/"reachable"/"offline" verdict — this build never connects.
});

test("a null credential_present reads as credential_unknown, not missing", () => {
  // null = "cannot tell" (e.g. no keyring backend); must not be merged into "missing".
  const view = environmentReadiness(env({ credential_present: null }));
  assert.equal(view.readiness, "credential_unknown");
});

// --- validation echoes the server's rules -------------------------------------------

test("a fully-specified input has no errors", () => {
  const ok = input({
    environment_id: "kimono-vps",
    label: "kimono VPS",
    ssh_host: "vps.example.test",
    ssh_user: "ubuntu",
    remote_workspace: "/srv/proj/ws",
    remote_project_root: "/srv/proj",
    secret_ref: "AGORA_SSH_KEY_kimono",
  });
  assert.deepEqual(validateEnvironmentInput(ok), []);
});

test("every missing required field is reported at once, not one at a time", () => {
  const errors = validateEnvironmentInput(emptyEnvironmentInput());
  const fields = errors.map((e) => e.field).sort();
  assert.deepEqual(fields, [
    "environment_id",
    "label",
    "project_id",
    "remote_project_root",
    "remote_workspace",
    "ssh_host",
    "ssh_user",
  ]);
});

test("an id with illegal characters is rejected", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "has spaces",
      label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/p/w", remote_project_root: "/p", secret_ref: "S",
    }),
  );
  assert.ok(errors.some((e) => e.field === "environment_id"));
});

test("a port out of range is rejected", () => {
  const base = {
    environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
    remote_workspace: "/p/w", remote_project_root: "/p", secret_ref: "S",
  };
  assert.ok(validateEnvironmentInput(input({ ...base, ssh_port: 0 })).some((e) => e.field === "ssh_port"));
  assert.ok(validateEnvironmentInput(input({ ...base, ssh_port: 70000 })).some((e) => e.field === "ssh_port"));
});

test("tofu without a known_hosts path is rejected, mirroring the backend", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/p/w", remote_project_root: "/p", secret_ref: "S",
      host_key_mode: "tofu", known_hosts_path: null,
    }),
  );
  assert.ok(errors.some((e) => e.field === "known_hosts_path"));
});

test("tofu with a known_hosts path is accepted", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/p/w", remote_project_root: "/p", secret_ref: "AGORA_SSH_KEY_e",
      host_key_mode: "tofu", known_hosts_path: "/home/u/.agora/known_hosts",
    }),
  );
  assert.deepEqual(errors, []);
});

test("a secret_ref outside the AGORA_ namespace is rejected, mirroring the backend", () => {
  const base = {
    environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
    credential_source: "env_var" as const,
    remote_workspace: "/p/w", remote_project_root: "/p",
  };
  // A bare name (would let the server's presence probe reach any env var).
  assert.ok(
    validateEnvironmentInput(input({ ...base, secret_ref: "PATH" }))
      .some((e) => e.field === "secret_ref"),
  );
  // The AGORA_ namespace is accepted.
  assert.equal(
    validateEnvironmentInput(input({ ...base, secret_ref: "AGORA_SSH_KEY_e" }))
      .some((e) => e.field === "secret_ref"),
    false,
  );
});

// --- credential source (adr/0001) ---------------------------------------------------

test("key_path source does not enforce the AGORA_ prefix (secret_ref is a file path)", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/p/w", remote_project_root: "/p",
      credential_source: "key_path", secret_ref: "/home/u/.ssh/id_ed25519",
    }),
  );
  assert.deepEqual(errors, []);
});

test("keyring source on create requires a password", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/p/w", remote_project_root: "/p",
      credential_source: "keyring", secret_ref: "login", password: null,
    }),
  );
  assert.ok(errors.some((e) => e.field === "password"));
});

test("keyring source on edit with an existing credential accepts a blank password", () => {
  // A semantics: editing a keyring environment (existingCredentialPresent) without
  // re-entering the password keeps the stored one.
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/p/w", remote_project_root: "/p",
      credential_source: "keyring", secret_ref: "login", password: null,
    }),
    { existingCredentialPresent: true },
  );
  assert.deepEqual(errors, []);
});

// --- round trip ---------------------------------------------------------------------

test("toEnvironmentInput drops read-only fields, keeps editable ones, blanks password", () => {
  const back = toEnvironmentInput(env());
  assert.equal("credential_present" in back, false);
  assert.equal("created_at" in back, false);
  assert.equal(back.environment_id, "kimono-vps");
  assert.equal(back.host_key_mode, "strict");
  assert.equal(back.credential_source, "env_var");
  // A keyring password cannot be read back — always blank on edit.
  assert.equal(back.password, null);
});

test("emptyEnvironmentInput defaults to strict host key, port 22, this machine's SSH keys", () => {
  const blank = emptyEnvironmentInput();
  assert.equal(blank.host_key_mode, "strict");
  assert.equal(blank.ssh_port, 22);
  assert.equal(blank.credential_source, "ssh_default");
  assert.equal(blank.password, null);
});

// --- credential source switching / submission (codex review, CR-4 slice 2) ----------

function filled(overrides: Partial<RemoteEnvironmentInput> = {}): RemoteEnvironmentInput {
  return toEnvironmentInput(env({ secret_ref: "", ...overrides } as Partial<RemoteEnvironment>));
}

test("a keyring environment from a blank form validates without the hidden secret_ref", () => {
  const draft = { ...withCredentialSource(filled(), "keyring"), password: "pw" };
  assert.deepEqual(validateEnvironmentInput(draft), []);
  // and what is sent carries a non-empty label the server accepts
  assert.equal(toSubmission(draft).secret_ref, KEYRING_SECRET_REF_LABEL);
});

test("a non-keyring source still requires secret_ref", () => {
  const errors = validateEnvironmentInput(filled({ credential_source: "key_path" }));
  assert.ok(errors.some((e) => e.field === "secret_ref"));
});

test("leaving keyring drops the typed password and the auto label", () => {
  const keyring = { ...filled(), credential_source: "keyring" as const, password: "pw", secret_ref: KEYRING_SECRET_REF_LABEL };
  const next = withCredentialSource(keyring, "key_path");
  assert.equal(next.password, null);
  assert.equal(next.secret_ref, "");
});

test("leaving keyring keeps a secret_ref the person typed", () => {
  const keyring = { ...filled(), credential_source: "keyring" as const, password: "pw", secret_ref: "AGORA_SSH_X" };
  assert.equal(withCredentialSource(keyring, "env_var").secret_ref, "AGORA_SSH_X");
});

test("a non-keyring submission never carries a password", () => {
  const stale = { ...filled({ secret_ref: "AGORA_SSH_X" }), password: "left-over" };
  assert.equal(toSubmission(stale).password, null);
});

test("a keyring submission keeps a typed label and the password", () => {
  const draft = { ...filled(), credential_source: "keyring" as const, password: "pw", secret_ref: "my-label" };
  const sent = toSubmission(draft);
  assert.equal(sent.secret_ref, "my-label");
  assert.equal(sent.password, "pw");
});

// --- the dispatch box's environment dropdown (CR-4 wiring) ---------------------------

test("the adapter's own place is always the first choice", () => {
  for (const state of [{ kind: "loading" as const }, { kind: "failed" as const }, { kind: "ready" as const, environments: [] }]) {
    const { choices } = dispatchEnvironmentChoices(state, "p1");
    assert.equal(choices[0].value, OWN_PLACE);
  }
});

test("registered environments follow, labelled with where they reach", () => {
  const { choices, notice } = dispatchEnvironmentChoices({
    kind: "ready",
    environments: [env({ environment_id: "ai", label: "AI server", ssh_user: "me", ssh_host: "10.0.0.5" }),
      env({ environment_id: "vps", label: "VPS", ssh_user: "u", ssh_host: "vps", ssh_port: 2222 })],
  }, "p1");
  assert.equal(notice, null);
  assert.deepEqual(choices.slice(1), [
    { value: "ai", label: "AI server", target: "me@10.0.0.5" },
    { value: "vps", label: "VPS", target: "u@vps:2222" },
  ]);
});

test("a list that could not be read says so, not that there are none", () => {
  const { choices, notice } = dispatchEnvironmentChoices({ kind: "failed" }, "p1");
  assert.equal(notice, "failed");
  assert.equal(choices.length, 1);
});

test("the own place sends no environment", () => {
  assert.deepEqual(dispatchEnvironmentOf(OWN_PLACE, { kind: "ready", environments: [] }, "p1"), { ok: true, environmentId: null });
});

test("a chosen environment that was removed is refused, not swapped for the own place", () => {
  assert.deepEqual(
    dispatchEnvironmentOf("ai", { kind: "ready", environments: [env({ environment_id: "other" })] }, "p1"),
    { ok: false, reason: "gone" },
  );
});

test("a chosen environment that cannot be checked yet is passed to the server", () => {
  assert.deepEqual(dispatchEnvironmentOf("ai", { kind: "failed" }, "p1"), { ok: true, environmentId: "ai" });
  assert.deepEqual(
    dispatchEnvironmentOf("ai", { kind: "ready", environments: [env({ environment_id: "ai" })] }, "p1"),
    { ok: true, environmentId: "ai" },
  );
});

test("a worktree directory outside the project root is refused in the form", () => {
  const errors = validateEnvironmentInput(input({
    environment_id: "ai", label: "AI", ssh_host: "h", ssh_user: "u", secret_ref: "AGORA_X",
    remote_project_root: "/workplace/ai-projects/MindRAG",
    remote_workspace: "/workplace/ai-projects/.agora-worktrees",
  }));
  assert.ok(errors.some((e) => e.field === "remote_workspace" && e.messageKey.endsWith("workspaceOutsideRoot")));
});

test("remotePathWithin compares components, not string prefixes", () => {
  assert.equal(remotePathWithin("/a/b/.agora/workspaces", "/a/b"), true);
  assert.equal(remotePathWithin("/a/bc/x", "/a/b"), false);
  assert.equal(remotePathWithin("/a/b", "/a/b"), false);
  assert.equal(remotePathWithin("/a/b/../c", "/a/b"), false);
  assert.equal(remotePathWithin("a/b/c", "/a/b"), false);
});

test("remotePathWithin ignores '.' segments like the server's PurePosixPath", () => {
  assert.equal(remotePathWithin("/a/./b/ws", "/a/b"), true);
  assert.equal(remotePathWithin("/a/b/ws", "/a/./b"), true);
});

test("the dispatch dropdown offers only this project's environments", () => {
  const { choices } = dispatchEnvironmentChoices({
    kind: "ready",
    environments: [env({ environment_id: "mine", project_id: "p1" }), env({ environment_id: "theirs", project_id: "p2" }),
      env({ environment_id: "unbound", project_id: null })],
  }, "p1");
  assert.deepEqual(choices.map((c) => c.value), [OWN_PLACE, "mine"]);
});

test("another project's environment is refused as gone, not sent", () => {
  assert.deepEqual(
    dispatchEnvironmentOf("theirs", { kind: "ready", environments: [env({ environment_id: "theirs", project_id: "p2" })] }, "p1"),
    { ok: false, reason: "gone" },
  );
});

test("an environment must name its project", () => {
  const errors = validateEnvironmentInput(input({
    environment_id: "ai", label: "AI", ssh_host: "h", ssh_user: "u", secret_ref: "AGORA_X",
    remote_project_root: "/p", remote_workspace: "/p/w", project_id: "",
  }));
  assert.ok(errors.some((e) => e.field === "project_id"));
});

test("ssh_default needs no secret_ref and sends no password (CR-24)", () => {
  const draft = { ...withCredentialSource(input(), "ssh_default"), password: "stale" };
  assert.deepEqual(validateEnvironmentInput({ ...draft, secret_ref: "" }).filter((e) => e.field === "secret_ref"), []);
  const sent = toSubmission({ ...draft, secret_ref: "" });
  assert.equal(sent.secret_ref, SSH_DEFAULT_SECRET_REF_LABEL);
  assert.equal(sent.password, null);
});

test("leaving ssh_default drops its auto label", () => {
  const draft = { ...input(), credential_source: "ssh_default" as const, secret_ref: SSH_DEFAULT_SECRET_REF_LABEL };
  assert.equal(withCredentialSource(draft, "env_var").secret_ref, "");
});

// --- registering with only what the person knows (CR-26) ---------------------------

test("environmentIdFrom turns a host or label into a valid id", () => {
  assert.equal(environmentIdFrom("10.36.94.35"), "10-36-94-35");
  assert.equal(environmentIdFrom("AI-Server"), "ai-server");
  assert.equal(environmentIdFrom("sharp@AI 服务器"), "sharp-ai");
  assert.equal(environmentIdFrom("--.x"), "x");
  assert.equal(environmentIdFrom(""), "");
});

test("defaultWorkspaceFor puts worktrees inside the project root", () => {
  assert.equal(defaultWorkspaceFor("/workplace/ai-projects/MindRAG/"), "/workplace/ai-projects/MindRAG/.agora/workspaces");
  assert.equal(defaultWorkspaceFor("relative/path"), "");
  assert.equal(defaultWorkspaceFor("/"), "");
});

test("autoFilled derives everything but host, account, project and root", () => {
  const filled = autoFilled(
    { ...emptyEnvironmentInput(), ssh_host: "10.36.94.35", ssh_user: "sharp", remote_project_root: "/home/sharp/p" },
    new Set(),
    { defaultProjectId: "agora" },
  );
  assert.equal(filled.environment_id, "agora-10-36-94-35");
  assert.equal(filled.label, "sharp@10.36.94.35");
  assert.equal(filled.remote_workspace, "/home/sharp/p/.agora/workspaces");
  assert.equal(filled.project_id, "agora");
  assert.equal(filled.credential_source, "ssh_default");
  assert.deepEqual(validateEnvironmentInput(filled), []);  // registrable as is
});

test("autoFilled never overwrites what the person typed", () => {
  const typed = { ...emptyEnvironmentInput(), ssh_host: "h", label: "My box", environment_id: "", remote_workspace: "/r/ws", remote_project_root: "/r" };
  const filled = autoFilled(typed, new Set(["label", "remote_workspace"] as const), { defaultProjectId: "agora" });
  assert.equal(filled.label, "My box");
  assert.equal(filled.environment_id, "agora-my-box");  // derived from the label they chose
  assert.equal(filled.remote_workspace, "/r/ws");
  const chosen = autoFilled({ ...typed, project_id: "" }, new Set(["project_id"] as const), { defaultProjectId: "agora" });
  assert.equal(chosen.project_id, "");  // cleared on purpose: not refilled
});

test("advancedHasError opens the folded section only for its own fields", () => {
  assert.equal(advancedHasError([{ field: "remote_workspace", messageKey: "x" }]), true);
  assert.equal(advancedHasError([{ field: "ssh_host", messageKey: "x" }]), false);
});

test("a second project on the same host gets its own id; taken ids are stepped around", () => {
  const base = { ...emptyEnvironmentInput(), ssh_host: "10.36.94.35", ssh_user: "sharp", remote_project_root: "/p" };
  const a = autoFilled({ ...base, project_id: "mindrag" }, new Set(["project_id"] as const));
  const b = autoFilled({ ...base, project_id: "lora" }, new Set(["project_id"] as const));
  assert.notEqual(a.environment_id, b.environment_id);
  const again = autoFilled({ ...base, project_id: "mindrag" }, new Set(["project_id"] as const), {
    takenIds: new Set([a.environment_id]),
  });
  assert.equal(again.environment_id, `${a.environment_id}-2`);
  assert.equal(unusedId("x", new Set(["x", "x-2"])), "x-3");
});

// --- picking a host from ~/.ssh/config (CR-25) --------------------------------------

test("picking a config host fills address, account and port, and marks them chosen", () => {
  const { input: picked, edited } = applySshHost(emptyEnvironmentInput(), {
    alias: "AI-Server", hostname: "10.36.94.14", user: "sharp", port: null, identity_file: null,
  });
  assert.equal(picked.ssh_host, "10.36.94.14");
  assert.equal(picked.ssh_user, "sharp");
  assert.equal(picked.ssh_port, 22);
  assert.equal(picked.credential_source, "ssh_default");
  assert.deepEqual(edited, ["ssh_host", "ssh_user", "ssh_port"]);
});

test("a config IdentityFile becomes a key-path credential; a later pick without one undoes it", () => {
  const { input: withKey } = applySshHost(emptyEnvironmentInput(), {
    alias: "vps", hostname: "vps.example.test", user: "u", port: 2222, identity_file: "~/.ssh/vps_ed25519",
  });
  assert.equal(withKey.credential_source, "key_path");
  assert.equal(withKey.secret_ref, "~/.ssh/vps_ed25519");
  assert.equal(withKey.ssh_port, 2222);
  const { input: plain } = applySshHost(withKey, { alias: "box", hostname: null, user: null, port: null, identity_file: null });
  assert.equal(plain.ssh_host, "box");  // no HostName: ssh resolves the alias itself
  assert.equal(plain.credential_source, "ssh_default");
  assert.equal(plain.secret_ref, "");
});

test("picking another host does not keep the previous host's port or account", () => {
  const { input: first } = applySshHost(emptyEnvironmentInput(), {
    alias: "vps", hostname: "vps", user: "admin", port: 2222, identity_file: null,
  });
  const { input: second } = applySshHost(first, { alias: "box", hostname: "10.0.0.5", user: null, port: null, identity_file: null });
  assert.equal(second.ssh_port, 22);
  assert.equal(second.ssh_user, "");
});
