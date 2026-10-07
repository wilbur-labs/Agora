import assert from "node:assert/strict";
import { test } from "node:test";

import type { RecordedGoal } from "../src/lib/control-plane.ts";
import { adoptableGoal, recordedGoalViewOf, recordLabelOf } from "../src/lib/recorded-goals.ts";

const g = (o: Partial<RecordedGoal>): RecordedGoal => ({
  source: "E:\\Work", task: "t", title: null, goal: "ship it", found_in: "task.yaml", ...o,
});

test("records without a goal are kept apart, not dropped", () => {
  const view = recordedGoalViewOf([g({ task: "a" }), g({ task: "b", goal: null, found_in: null }), g({ task: "c", goal: "  " })]);
  assert.deepEqual(view.withGoal.map((x) => x.task), ["a"]);
  assert.deepEqual(view.withoutGoal.map((x) => x.task), ["b", "c"]);
});

test("the same goal written twice is offered once", () => {
  const view = recordedGoalViewOf([g({ task: "a" }), g({ task: "b", source: "E:/kiro-workspace" })]);
  assert.equal(view.withGoal.length, 1);
});

test("the label names the task and its workspace", () => {
  assert.equal(recordLabelOf(g({ task: "signal-hunt" })), "signal-hunt · Work");
  assert.equal(recordLabelOf(g({ task: "sh", source: "E:/Processing/2026/AITools/kiro-workspace" })), "sh · kiro-workspace");
});

test("a goal longer than the charter accepts is not offered as one click", () => {
  assert.equal(adoptableGoal(g({ goal: "x".repeat(2000) })), true);
  assert.equal(adoptableGoal(g({ goal: "x".repeat(2001) })), false);
  assert.equal(adoptableGoal(g({ goal: null })), false);
});
