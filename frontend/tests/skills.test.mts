import assert from "node:assert/strict";
import test from "node:test";

import {
  matchSkills, nextAction, projectSkillsOf, refusalOf, SKILL_NAME_RE, skillTemplate, type SkillsView,
} from "../src/lib/skills-state.ts";

test("each install state leads to one next step; a foreign or unreadable copy to none (CR-75 slice 5)", () => {
  assert.equal(nextAction(undefined), "install");
  assert.equal(nextAction("not_installed"), "install");
  assert.equal(nextAction("outdated"), "update");
  assert.equal(nextAction("partial"), "update");
  assert.equal(nextAction("installed"), "uninstall");
  assert.equal(nextAction("modified"), "force");
  assert.equal(nextAction("foreign"), "blocked");
  assert.equal(nextAction("unreadable"), "blocked");
});

test("the server's refusal message is taken out of the error text", () => {
  assert.deepEqual(refusalOf('{"code":"refused","message":"still installed in alpha"}'),
    { code: "refused", message: "still installed in alpha" });
  assert.deepEqual(refusalOf("HTTP 500"), { code: "failed", message: "HTTP 500" });
});

test("names follow claude's rule, and the template carries the front matter", () => {
  for (const ok of ["a", "pdf-tips", "x1"]) assert.ok(SKILL_NAME_RE.test(ok), ok);
  for (const bad of ["", "-a", "a-", "A", "a_b", "a--b", "a".repeat(65)]) assert.ok(!SKILL_NAME_RE.test(bad), bad);
  assert.match(skillTemplate("x"), /^---\nname: x\ndescription: .+\n---\n/);
});

const view = (over: Partial<SkillsView> = {}): SkillsView => ({
  library: {
    state: "ok", root: "/lib", reason: null,
    skills: [
      { name: "pdf-tips", description: "Render Japanese PDFs", sha: "1", updated: null, error: null },
      { name: "api-notes", description: "Pleasanter API", sha: "2", updated: null, error: null },
      { name: "broken", description: null, sha: null, updated: null, error: "no SKILL.md" },
      { name: "edited", description: "x", sha: "3", updated: null, error: null },
    ],
  },
  projects: [
    { key: "k1", task: "alpha", title: "Alpha", workspace: "claude", repo: "/r", reason: null, shelved: false },
    { key: "k2", task: "beta", title: null, workspace: "claude", repo: null, reason: "no primary_repo", shelved: false },
  ],
  installs: {
    "pdf-tips": { k1: { summary: "installed", copies: [] } },
    "edited": { k1: { summary: "modified", copies: [] } },
    "broken": { k1: { summary: "not_installed", copies: [] } },
  },
  workflow: [], importable: [], reserved: [], targets: {},
  ...over,
});

test("a card lists what its project has and what it could take (CR-90)", () => {
  const got = projectSkillsOf(view(), "k1");
  assert.equal(got.show, "lists");
  if (got.show !== "lists") return;
  assert.deepEqual(got.has.map((s) => [s.name, s.summary]), [["pdf-tips", "installed"], ["edited", "modified"]]);
  // A skill the library cannot read is not offered; one with no install row is.
  assert.deepEqual(got.canTake.map((s) => s.name), ["api-notes"]);
  assert.equal(got.libraryEmpty, false);
  // ... and is counted, so "nothing left to add" is not read as "all installed".
  assert.equal(got.unreadable, 1);
  const onlyBroken = view({ library: { state: "ok", root: "/lib", reason: null,
    skills: [{ name: "broken", description: null, sha: null, updated: null, error: "no SKILL.md" }] }, installs: {} });
  assert.deepEqual(projectSkillsOf(onlyBroken, "k1"), { show: "lists", has: [], canTake: [], libraryEmpty: false, unreadable: 1 });
});

test("a card says why it has no skills list rather than showing an empty one", () => {
  assert.deepEqual(projectSkillsOf(view(), "k2"), { show: "no_repo", reason: "no primary_repo" });
  assert.deepEqual(projectSkillsOf(view(), "nope"), { show: "no_project" });
  const unknown = view({ library: { state: "unknown", root: null, reason: "2 work roots", skills: [] } });
  assert.deepEqual(projectSkillsOf(unknown, "k1"), { show: "library", state: "unknown", reason: "2 work roots" });
  const empty = projectSkillsOf(view({ library: { state: "absent", root: "/lib", reason: null, skills: [] }, installs: {} }), "k1");
  assert.deepEqual(empty, { show: "lists", has: [], canTake: [], libraryEmpty: true, unreadable: 0 });
});

test("the dropdown's filter needs every word, in the name or the description", () => {
  const got = projectSkillsOf(view({ installs: {} }), "k1");
  if (got.show !== "lists") throw new Error("lists expected");
  assert.deepEqual(matchSkills(got.canTake, "").map((s) => s.name), ["pdf-tips", "api-notes", "edited"]);
  assert.deepEqual(matchSkills(got.canTake, "PDF japanese").map((s) => s.name), ["pdf-tips"]);
  assert.deepEqual(matchSkills(got.canTake, "pleasanter").map((s) => s.name), ["api-notes"]);
  assert.deepEqual(matchSkills(got.canTake, "pdf pleasanter"), []);
});
