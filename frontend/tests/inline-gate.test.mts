import assert from "node:assert/strict";
import { test } from "node:test";

import { defaultGateAction, gateReplyReachOf, gateResponseReady, inlineGateOf } from "../src/lib/inline-gate.ts";
import type { TaskAttentionView } from "../src/lib/task-attention.ts";

type Item = { item_id: string; kind: string; state: string };
const raised = (itemId: string | null = "attn_1") => ({
  event_type: "attention.created",
  payload: itemId === null ? {} : { item_id: itemId },
});
const item = (id: string): Item => ({ item_id: id, kind: "approval", state: "open" });
const open = (...ids: string[]): TaskAttentionView<Item> => ({ kind: "open", items: ids.map(item) });
const partial = (...ids: string[]): TaskAttentionView<Item> => ({ kind: "partial", items: ids.map(item) });

test("an event that does not open an item is not a gate, whatever the read says", () => {
  for (const type of ["attention.responded", "attention.cancelled", "run.started", "note"]) {
    assert.deepEqual(inlineGateOf({ event_type: type, payload: { item_id: "attn_1" } }, open("attn_1")), { kind: "notAGate" });
  }
});

test("a bridged item is raised like a created one", () => {
  const gate = inlineGateOf({ event_type: "attention.bridge_captured", payload: { item_id: "attn_1" } }, open("attn_1"));
  assert.equal(gate.kind, "open");
});

test("a read that has not landed is loading; a failed read is unreadable, never settled", () => {
  assert.deepEqual(inlineGateOf(raised(), { kind: "loading" }), { kind: "loading" });
  assert.deepEqual(inlineGateOf(raised(), { kind: "unreadable" }), { kind: "unreadable" });
});

test("an item in the open page is open, with the item attached", () => {
  const gate = inlineGateOf(raised("attn_2"), open("attn_1", "attn_2"));
  assert.equal(gate.kind, "open");
  if (gate.kind === "open") assert.equal(gate.item.item_id, "attn_2");
});

test("absent from a whole read means settled; absent from a cut page means unknown", () => {
  assert.deepEqual(inlineGateOf(raised("attn_9"), open("attn_1")), { kind: "settled" });
  assert.deepEqual(inlineGateOf(raised("attn_9"), { kind: "none" }), { kind: "settled" });
  assert.deepEqual(inlineGateOf(raised("attn_9"), partial("attn_1")), { kind: "unknown" });
  // Present in a cut page is still present.
  assert.equal(inlineGateOf(raised("attn_1"), partial("attn_1")).kind, "open");
});

test("an event that names no item cannot be matched, so it is unknown — not settled", () => {
  assert.deepEqual(inlineGateOf(raised(null), open("attn_1")), { kind: "unknown" });
  assert.deepEqual(inlineGateOf(raised(null), { kind: "none" }), { kind: "unknown" });
  assert.deepEqual(inlineGateOf({ event_type: "attention.created", payload: { item_id: "" } }, open("attn_1")), { kind: "unknown" });
  assert.deepEqual(inlineGateOf({ event_type: "attention.created", payload: null }, open("attn_1")), { kind: "unknown" });
});

test("approvals default to approve; questions and blockers are answered", () => {
  assert.equal(defaultGateAction("approval"), "approve");
  assert.equal(defaultGateAction("question"), "answer");
  assert.equal(defaultGateAction("blocker"), "answer");
});

test("an approval or rejection may be wordless; an answer may not be empty", () => {
  assert.equal(gateResponseReady("approve", ""), true);
  assert.equal(gateResponseReady("reject", "   "), true);
  assert.equal(gateResponseReady("answer", "   "), false);
  assert.equal(gateResponseReady("answer", "yes"), true);
});

test("only a rejected review gate promises that an agent reads the words (CR-62)", () => {
  const review = "agora-review";
  assert.equal(gateReplyReachOf({ kind: "approval", requester: review }, "reject"), "reviewFix");
  assert.equal(gateReplyReachOf({ kind: "approval", requester: review }, "approve"), "recordOnly");
  assert.equal(gateReplyReachOf({ kind: "blocker", requester: review }, "answer"), "reviewOwed");
  // A bridge approval carries the decision back, never the text; questions and other
  // blockers are stored on the item and sent nowhere.
  assert.equal(gateReplyReachOf({ kind: "approval", requester: "codex-app-server" }, "reject"), "recordOnly");
  assert.equal(gateReplyReachOf({ kind: "question", requester: "claude-bridge" }, "answer"), "recordOnly");
  assert.equal(gateReplyReachOf({ kind: "blocker", requester: "user" }, "answer"), "recordOnly");
});

test("every reply reach has its sentence in both locales", async () => {
  const { workspace } = await import("../src/lib/i18n/dictionaries/workspace.ts");
  for (const locale of ["en", "zh"] as const) {
    const gate = workspace[locale].timeline.gate as unknown as Record<string, Record<string, unknown>>;
    for (const reach of ["reviewFix", "reviewOwed", "recordOnly"]) {
      assert.equal(typeof gate.answerHint[reach], "string", `${locale} answerHint.${reach}`);
      assert.equal(typeof gate.rejectPlaceholder[reach], "string", `${locale} rejectPlaceholder.${reach}`);
    }
  }
});
