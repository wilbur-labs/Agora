import assert from "node:assert/strict";
import test from "node:test";

import { nextAction, refusalOf, SKILL_NAME_RE, skillTemplate } from "../src/lib/skills-state.ts";

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
  for (const bad of ["", "-a", "a-", "A", "a_b", "a".repeat(65)]) assert.ok(!SKILL_NAME_RE.test(bad), bad);
  assert.match(skillTemplate("x"), /^---\nname: x\ndescription: .+\n---\n/);
});
