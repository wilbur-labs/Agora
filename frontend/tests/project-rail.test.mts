import assert from "node:assert/strict";
import { test } from "node:test";

import { railOf, waitingTotalOf } from "../src/lib/project-rail.ts";

const projects = [
  { project_id: "a", name: "Alpha", hasGoal: true },
  { project_id: "b", name: "Beta", hasGoal: true },
  { project_id: "c", name: "Gamma", hasGoal: false },
  { project_id: "d", name: "Delta", hasGoal: true },
];

test("waiting first (most first), then running, then no goal, then idle", () => {
  const rows = railOf(projects,
    { items: [{ project_id: "d" }, { project_id: "b" }, { project_id: "b" }], truncated: false },
    { items: [{ project_id: "a" }], truncated: false });
  assert.deepEqual(rows.map((r) => [r.project_id, r.status, r.waiting]), [
    ["b", "waiting", 2], ["d", "waiting", 1], ["a", "running", 0], ["c", "noGoal", 0],
  ]);
});

test("a failed read is unknown, never idle or zero", () => {
  const rows = railOf([{ project_id: "a", name: "A", hasGoal: true }], null, { items: [], truncated: false });
  assert.equal(rows[0].status, "unknown");
  assert.equal(rows[0].waiting, null);
});

test("a cut read makes the count a floor and nothing idle", () => {
  const rows = railOf([{ project_id: "a", name: "A", hasGoal: true }, { project_id: "b", name: "B", hasGoal: true }],
    { items: [{ project_id: "a" }], truncated: true }, { items: [], truncated: false });
  assert.equal(rows[0].waitingAtLeast, true);
  assert.equal(rows.find((r) => r.project_id === "b")?.status, "unknown");
});

test("the top-bar total says when it is a floor or unknown", () => {
  assert.deepEqual(waitingTotalOf({ items: [1, 2], truncated: true }), { count: 2, atLeast: true });
  assert.equal(waitingTotalOf(null), null);
});
