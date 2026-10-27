import assert from "node:assert/strict";
import { test } from "node:test";

import { REVIEW_BODY_TRUNCATED_MARK, nextStepAfter, noNextStepIsCertain, reviewFixPrompt } from "../src/lib/auto-advance.ts";

const row = (id: string, step: number, state: string) => ({ task_id: id, step, title: `t${step}`, state });

test("the next step is the first planned row after the current one", () => {
  const rows = [row("a", 1, "done"), row("b", 2, "planned"), row("c", 3, "planned")];
  assert.equal(nextStepAfter(rows, "a")?.task_id, "b");
  assert.equal(nextStepAfter(rows, "b")?.task_id, "c");
});

test("rows that are not planned are skipped, whatever their state", () => {
  const rows = [row("a", 1, "done"), row("b", 2, "blocked"), row("c", 3, "backlog"), row("d", 4, "running"), row("e", 5, "planned")];
  assert.equal(nextStepAfter(rows, "a")?.task_id, "e");
});

test("with nothing planned after, a planned step left behind is next", () => {
  const rows = [row("a", 1, "planned"), row("b", 2, "done"), row("c", 3, "done")];
  assert.equal(nextStepAfter(rows, "c")?.task_id, "a");
});

test("nothing planned anywhere is null; the current row itself is never the answer", () => {
  assert.equal(nextStepAfter([row("a", 1, "planned"), row("b", 2, "done")], "a"), null);
  assert.equal(nextStepAfter([], "a"), null);
});

test("an unknown current id still yields the first planned row", () => {
  assert.equal(nextStepAfter([row("a", 1, "done"), row("b", 2, "planned")], "zzz")?.task_id, "b");
});

test("\"no next step\" is only certain over a whole read", () => {
  assert.equal(noNextStepIsCertain(true, null), true);
  assert.equal(noNextStepIsCertain(false, null), false);
  assert.equal(noNextStepIsCertain(true, row("a", 1, "planned")), false);
});

test("the fix prompt carries the reviewer's words and the person's, labelled apart", () => {
  const prompt = reviewFixPrompt({ reviewBody: "## 结论\n不能批准。", reason: "先修第一条" });
  assert.match(prompt, /^复核未通过/);
  assert.ok(prompt.includes("## 复核意见\n## 结论\n不能批准。"));
  assert.ok(prompt.includes("## 我的补充\n先修第一条"));
  assert.ok(prompt.indexOf("复核意见") < prompt.indexOf("我的补充"));
});

test("a prompt over the run limit is cut in the reviewer's body, marked, and keeps the reason", () => {
  const body = "x".repeat(40_000);
  const prompt = reviewFixPrompt({ reviewBody: body, reason: "改第一条" }, 16_000);
  assert.ok(prompt.length <= 16_000);
  assert.ok(prompt.includes(REVIEW_BODY_TRUNCATED_MARK));
  assert.ok(prompt.endsWith("## 我的补充\n改第一条"));
  // Under the limit nothing is cut and nothing is marked.
  const short = reviewFixPrompt({ reviewBody: "ok", reason: "" }, 16_000);
  assert.ok(!short.includes(REVIEW_BODY_TRUNCATED_MARK));
  // The default limit is the control plane's own run prompt limit.
  assert.ok(reviewFixPrompt({ reviewBody: body, reason: "" }).length <= 16_000);
});

test("an empty reason adds no empty section", () => {
  const prompt = reviewFixPrompt({ reviewBody: "x", reason: "   " });
  assert.ok(!prompt.includes("我的补充"));
});

test("what the agent receives does not depend on the item's (localized) title", () => {
  // CR-71: titles are written in the reader's language; the prompt takes no title at all.
  const prompt = reviewFixPrompt({ reviewBody: "## 结论\n不能批准。", reason: "" });
  assert.equal(prompt, "复核未通过，请按下面的意见修改；改完说明每一条是怎么处理的。\n\n## 复核意见\n## 结论\n不能批准。");
});
