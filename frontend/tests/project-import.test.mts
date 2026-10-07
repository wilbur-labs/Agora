import assert from "node:assert/strict";
import { test } from "node:test";

import type { ImportCandidate } from "../src/lib/control-plane.ts";
import { importGroupsOf, statusKeyOf, tasksLabelOf } from "../src/lib/project-import.ts";

function c(overrides: Partial<ImportCandidate>): ImportCandidate {
  return {
    raw_path: "E:/Work/x", root: "E:\\Work\\x", status: "ok", registered_as: null,
    suggested_id: "x", suggested_name: "X", tasks: [{ source: "E:\\Work", task: "x", title: "X" }],
    ...overrides,
  };
}

test("candidates sort into importable / registered / blocked / elsewhere", () => {
  const g = importGroupsOf([
    c({}),
    c({ status: "registered", registered_as: "agora", suggested_id: null }),
    c({ status: "missing", suggested_id: null }),
    c({ status: "not_git", suggested_id: null }),
    c({ status: "unreadable", suggested_id: null }),
    c({ status: "not_this_machine", root: null, suggested_id: null }),
    c({ status: "placeholder", root: null, suggested_id: null }),
  ]);
  assert.equal(g.importable.length, 1);
  assert.equal(g.registered.length, 1);
  assert.deepEqual(g.blocked.map((x) => x.status), ["missing", "not_git", "unreadable"]);
  assert.deepEqual(g.elsewhere.map((x) => x.status), ["not_this_machine", "placeholder"]);
});

test("an ok candidate without a suggested id is never offered", () => {
  assert.equal(importGroupsOf([c({ suggested_id: null })]).importable.length, 0);
});

test("statusKeyOf and tasksLabelOf name the reason and where the record came from", () => {
  assert.equal(statusKeyOf("not_git"), "portfolio.import.status.not_git");
  const label = tasksLabelOf(c({ tasks: [
    { source: "E:\\Work", task: "signal-hunt", title: null },
    { source: "E:/Processing/2026/AITools/kiro-workspace", task: "sh", title: null },
  ] }));
  assert.equal(label, "signal-hunt · Work, sh · kiro-workspace");
});
