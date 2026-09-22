import assert from "node:assert/strict";
import { test } from "node:test";

import {
  activeRunsOf, composeIntentOf, dispatchTargetOf, stepNumberOf, streamItemsOf, streamStatusOf,
  visibleTranscriptLines,
} from "../src/lib/project-stream.ts";

const run = (id: string, task: string, at: string, extra: Record<string, unknown> = {}) => ({
  run_id: id, task_id: task, adapter: "claude", state: "succeeded", queued_at: at,
  started_at: at, finished_at: null, role: "worker", reviews_run_id: null, ...extra,
});
const gate = (id: string, task: string, at: string, state = "open") => ({ item_id: id, task_id: task, state, created_at: at });
const steps = [
  { task_id: "t1", step: 1, title: "one", state: "done" },
  { task_id: "t2", step: 2, title: "two", state: "planned" },
  { task_id: "t3", step: 3, title: "three", state: "planned" },
];

test("the stream orders runs and gates by when they began, and names each one's step", () => {
  const items = streamItemsOf(
    [run("r2", "t2", "2026-09-17T02:00:00Z"), run("r1", "t1", "2026-09-17T01:00:00Z")],
    [gate("g1", "t1", "2026-09-17T01:30:00Z")],
    steps,
  );
  assert.deepEqual(items.map((it) => it.key), ["r1", "g1", "r2"]);
  assert.deepEqual(items.map((it) => it.step), [1, 1, 2]);
});

test("a review nests under the run it reviews instead of standing alone", () => {
  const items = streamItemsOf(
    [run("w", "t1", "2026-09-17T01:00:00Z"), run("rv", "t1", "2026-09-17T01:10:00Z", { role: "review", reviews_run_id: "w" })],
    [], steps,
  );
  assert.equal(items.length, 1);
  assert.equal(items[0].type, "run");
  if (items[0].type === "run") assert.deepEqual(items[0].unit.nested.map((r) => r.run_id), ["rv"]);
});

test("a run for a task not in the order has no step number, never a made-up one", () => {
  const items = streamItemsOf([run("r9", "t9", "2026-09-17T01:00:00Z")], [], steps);
  assert.equal(items[0].step, null);
  assert.equal(stepNumberOf(steps, "t9"), null);
});

test("a null read contributes nothing and does not throw", () => {
  assert.deepEqual(streamItemsOf(null, null, steps), []);
});

test("a typed line is a question by default; a note, an idea or work by prefix; never on an empty rest", () => {
  assert.deepEqual(composeIntentOf("备注：先记一下"), { kind: "note", text: "先记一下" });
  assert.deepEqual(composeIntentOf("note: remember"), { kind: "note", text: "remember" });
  assert.deepEqual(composeIntentOf("点子: 加一个语气判断"), { kind: "idea", text: "加一个语气判断" });
  assert.deepEqual(composeIntentOf("做：把骨架搭起来"), { kind: "dispatch", text: "把骨架搭起来" });
  assert.deepEqual(composeIntentOf("do: scaffold it"), { kind: "dispatch", text: "scaffold it" });
  // CR-75: a plain line is a question, not work — "还给mockup页面的链接" must never
  // become step 2's implementation again.
  // CR-99: a plain line goes to the agent as typed — it decides whether to answer or act.
  assert.deepEqual(composeIntentOf("  还给mockup页面的链接 "), { kind: "dispatch", text: "还给mockup页面的链接" });
  assert.equal(composeIntentOf("   "), null);
  assert.equal(composeIntentOf("备注："), null);
  assert.equal(composeIntentOf("做："), null);
});

test("work goes to the running step, or the first planned one", () => {
  const t = dispatchTargetOf(steps, new Set(), true);
  assert.equal(t.kind, "step"); if (t.kind === "step") assert.equal(t.step.task_id, "t2");
  const withRunning = [{ ...steps[1], state: "running" }, steps[2]];
  const r = dispatchTargetOf(withRunning, new Set(), true);
  if (r.kind === "step") assert.equal(r.step.task_id, "t2"); else assert.fail(r.kind);
});

test("a step with an unsettled run is busy — the line waits, it is not refused by the server", () => {
  const withRunning = [{ ...steps[1], state: "running" }, steps[2]];
  const t = dispatchTargetOf(withRunning, new Set(["t2"]), true);
  assert.equal(t.kind, "busy");
});

test("\"nothing to dispatch to\" has three sentences", () => {
  assert.deepEqual(dispatchTargetOf([{ ...steps[0] }], new Set(), true), { kind: "none", reason: "allDone" });
  assert.deepEqual(dispatchTargetOf([{ ...steps[0] }, { ...steps[1], state: "blocked" }], new Set(), true), { kind: "none", reason: "noneStartable" });
  assert.deepEqual(dispatchTargetOf([{ ...steps[0] }], new Set(), false), { kind: "none", reason: "unknown" });
  assert.deepEqual(dispatchTargetOf([], new Set(), true), { kind: "none", reason: "noneStartable" });
});

test("the header says running over waiting over idle, and unknown when it cannot read", () => {
  const live = run("r", "t2", "2026-09-17T01:00:00Z", { state: "running" });
  const s = streamStatusOf([live, run("old", "t1", "2026-09-17T00:00:00Z")], false, 3, steps);
  assert.equal(s.kind, "running"); if (s.kind === "running") { assert.equal(s.run.run_id, "r"); assert.equal(s.step, 2); assert.equal(s.also, 0); }
  assert.deepEqual(streamStatusOf([run("old", "t1", "2026-09-17T00:00:00Z")], false, 1, steps), { kind: "waiting", count: 1 });
  assert.deepEqual(streamStatusOf([], false, 0, steps), { kind: "idle" });
  assert.deepEqual(streamStatusOf([], true, 0, steps), { kind: "unknown" });
  assert.deepEqual(streamStatusOf([], false, null, steps), { kind: "unknown" });
  assert.equal(activeRunsOf([live, run("q", "t3", "2026-09-17T00:00:00Z", { state: "queued" })]).length, 2);
});

test("an empty thinking marker never takes a line in the window", () => {
  // What every thinking block rendered as before CR-104: its content went to the
  // deltas, which the reader dropped, and the closing copy it did render was empty.
  assert.deepEqual(visibleTranscriptLines("· \nit spoke\n", false), ["it spoke", ""]);
  // A marker with something after it is the agent thinking out loud, and stays.
  assert.deepEqual(visibleTranscriptLines("· really thinking", false), ["· really thinking"]);
});

test("a live turn holds back its mid-word tail, a settled one shows it", () => {
  const body = "a whole line\nhalf a li";
  assert.deepEqual(visibleTranscriptLines(body, true), ["a whole line"]);
  // Settled: an unterminated last line is all there will ever be of it.
  assert.deepEqual(visibleTranscriptLines(body, false), ["a whole line", "half a li"]);
});

test("a turn is never left with nothing to draw", () => {
  // The order is the guard. Counting raw lines first withheld the half line and then
  // filtered away the only line left, blanking the window (codex review, 2026-09-22).
  assert.deepEqual(visibleTranscriptLines("· \nhalf a li", true), ["half a li"]);
  assert.deepEqual(visibleTranscriptLines("half a li", true), ["half a li"]);
  assert.deepEqual(visibleTranscriptLines("", true), [""]);
});
