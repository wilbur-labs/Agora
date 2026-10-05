import assert from "node:assert/strict";
import { test } from "node:test";

import type { RemoteEnvironment, RemoteEnvironmentInput } from "../src/lib/control-plane.ts";
import {
  emptyEnvironmentInput,
  environmentReadiness,
  KEYRING_SECRET_REF_LABEL,
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
    created_at: "2026-09-28T00:00:00Z",
    updated_at: "2026-09-28T00:00:00Z",
    ...overrides,
  };
}

function input(overrides: Partial<RemoteEnvironmentInput> = {}): RemoteEnvironmentInput {
  return { ...emptyEnvironmentInput(), ...overrides };
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
    remote_workspace: "/srv/ws",
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
    "remote_project_root",
    "remote_workspace",
    "secret_ref",
    "ssh_host",
    "ssh_user",
  ]);
});

test("an id with illegal characters is rejected", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "has spaces",
      label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/w", remote_project_root: "/p", secret_ref: "S",
    }),
  );
  assert.ok(errors.some((e) => e.field === "environment_id"));
});

test("a port out of range is rejected", () => {
  const base = {
    environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
    remote_workspace: "/w", remote_project_root: "/p", secret_ref: "S",
  };
  assert.ok(validateEnvironmentInput(input({ ...base, ssh_port: 0 })).some((e) => e.field === "ssh_port"));
  assert.ok(validateEnvironmentInput(input({ ...base, ssh_port: 70000 })).some((e) => e.field === "ssh_port"));
});

test("tofu without a known_hosts path is rejected, mirroring the backend", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/w", remote_project_root: "/p", secret_ref: "S",
      host_key_mode: "tofu", known_hosts_path: null,
    }),
  );
  assert.ok(errors.some((e) => e.field === "known_hosts_path"));
});

test("tofu with a known_hosts path is accepted", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/w", remote_project_root: "/p", secret_ref: "AGORA_SSH_KEY_e",
      host_key_mode: "tofu", known_hosts_path: "/home/u/.agora/known_hosts",
    }),
  );
  assert.deepEqual(errors, []);
});

test("a secret_ref outside the AGORA_ namespace is rejected, mirroring the backend", () => {
  const base = {
    environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
    remote_workspace: "/w", remote_project_root: "/p",
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
      remote_workspace: "/w", remote_project_root: "/p",
      credential_source: "key_path", secret_ref: "/home/u/.ssh/id_ed25519",
    }),
  );
  assert.deepEqual(errors, []);
});

test("keyring source on create requires a password", () => {
  const errors = validateEnvironmentInput(
    input({
      environment_id: "e", label: "x", ssh_host: "h", ssh_user: "u",
      remote_workspace: "/w", remote_project_root: "/p",
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
      remote_workspace: "/w", remote_project_root: "/p",
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

test("emptyEnvironmentInput defaults to strict host key, port 22, env_var source", () => {
  const blank = emptyEnvironmentInput();
  assert.equal(blank.host_key_mode, "strict");
  assert.equal(blank.ssh_port, 22);
  assert.equal(blank.credential_source, "env_var");
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
