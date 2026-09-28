import assert from "node:assert/strict";
import { test } from "node:test";

import type { RemoteEnvironment, RemoteEnvironmentInput } from "../src/lib/control-plane.ts";
import {
  emptyEnvironmentInput,
  environmentReadiness,
  toEnvironmentInput,
  validateEnvironmentInput,
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
    secret_present: true,
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

test("a set credential env var reads as credential_set", () => {
  assert.equal(environmentReadiness(env({ secret_present: true })).readiness, "credential_set");
});

test("an unset credential env var reads as credential_missing, not as offline", () => {
  const view = environmentReadiness(env({ secret_present: false }));
  assert.equal(view.readiness, "credential_missing");
  // Deliberately no "online"/"reachable"/"offline" verdict — slice 1 never connects.
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

// --- round trip ---------------------------------------------------------------------

test("toEnvironmentInput drops read-only fields and keeps the editable ones", () => {
  const back = toEnvironmentInput(env());
  assert.equal("secret_present" in back, false);
  assert.equal("created_at" in back, false);
  assert.equal(back.environment_id, "kimono-vps");
  assert.equal(back.host_key_mode, "strict");
});

test("emptyEnvironmentInput defaults to strict host key and port 22", () => {
  const blank = emptyEnvironmentInput();
  assert.equal(blank.host_key_mode, "strict");
  assert.equal(blank.ssh_port, 22);
});
