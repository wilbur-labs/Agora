import assert from "node:assert/strict";
import test from "node:test";

import {
  PORTFOLIO_ATTENTION_NOTICE_KEY,
  PORTFOLIO_TASK_INVENTORY_NOTICE_KEY,
  PORTFOLIO_GROUPS,
  PORTFOLIO_ORDER,
  attentionReadCovers,
  portfolioAllClearSayable,
  portfolioBucketOf,
  taskInventoryReachOf,
  type AttentionRead,
  type PortfolioBucket,
  type TaskInventoryReach,
} from "../src/lib/portfolio-bucket.ts";
import { railReadReport } from "../src/lib/rail-group.ts";
import { portfolio as portfolioDictionary } from "../src/lib/i18n/dictionaries/portfolio.ts";

const place = (state: string, kinds: string[], attentionComplete: boolean): PortfolioBucket =>
  portfolioBucketOf({ task: { state }, kinds: new Set(kinds), attentionComplete });

/**
 * Every calm lifecycle state the board can be handed, so no test rests on one example.
 * `requirements` and `design` were missing while this comment said "every": both fall
 * to `shaping` through the same default, and a regression that special-cased either
 * would have passed here.
 */
const CALM_STATES = [
  "running", "verified", "done", "cancelled",
  "backlog", "requirements", "design", "planned",
];

test("an observed item places the task whatever the read's limit was", () => {
  // Presence is provable from a truncated page: the item is on it.
  for (const complete of [true, false]) {
    assert.equal(place("running", ["question"], complete), "needsReply");
    assert.equal(place("running", ["approval"], complete), "needsApproval");
    assert.equal(place("running", ["blocker"], complete), "blocked");
  }
});

test("a lifecycle state that demands action does not depend on the attention read", () => {
  // These never consulted the page, so a capped page must not take them away — and
  // must not turn them into `unknown`, which would hide a certain demand behind an
  // uncertainty that does not apply to it.
  for (const complete of [true, false]) {
    assert.equal(place("review", [], complete), "needsApproval");
    assert.equal(place("blocked", [], complete), "blocked");
    assert.equal(place("failed", [], complete), "blocked");
  }
});

test("a complete read lets an absence place the task in its settled column", () => {
  assert.equal(place("running", [], true), "running");
  assert.equal(place("verified", [], true), "verified");
  assert.equal(place("done", [], true), "done");
  assert.equal(place("cancelled", [], true), "closed");
  assert.equal(place("backlog", [], true), "shaping");
  assert.equal(place("planned", [], true), "shaping");
});

test("an incomplete read cannot place a task in any column that means 'nothing waiting'", () => {
  // The defect this module exists for: the board kept only `items` from a page that
  // reports `truncated`, so a task whose question was off-page was filed by state
  // alone — a task waiting for an answer shown under Running, in the main triage
  // surface. Every calm column has to be unreachable from an absence that proves
  // nothing, or the hole is only half closed.
  for (const state of CALM_STATES) {
    assert.equal(place(state, [], false), "unknown", state);
  }
});

test("unknown is ordered above every calm column and below the ones that demand action", () => {
  const at = (bucket: PortfolioBucket) => PORTFOLIO_ORDER.indexOf(bucket);
  assert.ok(at("unknown") > at("needsReply"));
  assert.ok(at("unknown") > at("needsApproval"));
  assert.ok(at("unknown") > at("blocked"));
  for (const calm of ["running", "verified", "done", "closed", "shaping"] as const) {
    assert.ok(at("unknown") < at(calm), `unknown must precede ${calm}`);
  }
  // And nothing is missing from the order, or a bucket would render nowhere.
  const every: PortfolioBucket[] = [
    "needsReply", "needsApproval", "blocked", "unknown",
    "running", "verified", "done", "closed", "shaping",
  ];
  // Compared as a *set*, so this assertion answers one question — is any bucket
  // missing — and the next one answers the other. Comparing the sorted arrays
  // directly made the length check below dead weight: array equality already counts
  // multiplicity, so a duplicated bucket failed here and the "exactly once" line
  // never had a mutation of its own. A check that cannot fail on its own is not a
  // check, and this file is where that was said about someone else's test.
  assert.deepEqual([...new Set(PORTFOLIO_ORDER)].sort(), every.sort());
  // Exactly once: a bucket in two sections renders its tasks twice, and the set
  // above still looks complete.
  assert.equal(PORTFOLIO_ORDER.length, every.length);
  // The order is derived from the sections the page renders, so this test is about
  // the board and not about a constant kept beside it. It was the latter until
  // 2026-09-04, and the two had already disagreed.
  assert.deepEqual(
    PORTFOLIO_GROUPS.flatMap((group) => [...group.buckets]),
    [...PORTFOLIO_ORDER],
  );
  // Only the settled section collapses; an action or unsorted section hidden behind
  // a disclosure is a task nobody sees.
  for (const group of PORTFOLIO_GROUPS) {
    assert.equal(Boolean(group.collapsible), group.id === "completed", group.id);
  }
});

test("only a complete read covers an absence", () => {
  assert.equal(attentionReadCovers("complete"), true);
  for (const read of ["pending", "capped", "unreadable"] as const) {
    assert.equal(attentionReadCovers(read), false, read);
  }
});

test("a failing sibling read is not reported as an unreadable attention read", () => {
  // The board derives the reach with rail-group's `railReadReport`, the one
  // implementation of "a read speaks for itself" (this module's own copy is gone —
  // the page fed it a *combined* failure, so a failing tasks read was announced as
  // "Open items could not be read"). Pinned here at the board's level too, because
  // this file is what a future reader of the board opens.
  const ok = { status: "fulfilled" as const, value: { truncated: false } };
  assert.equal(railReadReport({
    tasks: { status: "rejected", reason: new Error("tasks down") },
    projects: ok,
    attention: ok,
    runs: ok,
  }).attentionReach, "complete");
  assert.equal(railReadReport({
    tasks: ok,
    projects: ok,
    attention: { status: "rejected", reason: new Error("attention down") },
    runs: ok,
  }).attentionReach, "unreadable");
  assert.equal(railReadReport({
    tasks: ok,
    projects: ok,
    attention: { status: "fulfilled", value: { truncated: true } },
    runs: ok,
  }).attentionReach, "capped");
  // And the sibling's failure is still *reported*, just not as attention's: the
  // board's banner shows it, so a failed read never passes for a quiet one.
  assert.deepEqual(railReadReport({
    tasks: { status: "rejected", reason: new Error("tasks down") },
    projects: ok,
    attention: ok,
    runs: ok,
  }).errors, ["tasks down"]);
  // capped and unreadable bucket identically but must not share a sentence: "the page
  // reached its read limit" and "the read failed" are different facts, and telling
  // someone whose request failed that a cap was reached is a specific falsehood.
  assert.notEqual(
    PORTFOLIO_ATTENTION_NOTICE_KEY.capped,
    PORTFOLIO_ATTENTION_NOTICE_KEY.unreadable,
  );
});

test("the reassurance needs the task read as well as the attention read", () => {
  // "Nothing needs you right now" was printed whenever the action group was empty
  // and the attention read was complete. An empty action group is also what a
  // *failed task read* produces: no tasks at all, so no cards in any group. The
  // board then reassured the user directly under its own load-failure banner, about
  // an inventory it had not managed to read — which could hold review, blocked and
  // failed tasks, each of which reaches an action column without the attention page.
  const complete: TaskInventoryReach = "complete";
  assert.equal(portfolioAllClearSayable({ attentionRead: "complete", taskInventory: complete }), true);
  for (const taskInventory of ["unreadable", "capped"] as const) {
    assert.equal(
      portfolioAllClearSayable({ attentionRead: "complete", taskInventory }),
      false,
      taskInventory,
    );
  }
  // And the attention read's own limits still count, with the inventory in hand.
  for (const read of ["pending", "capped", "unreadable"] as const) {
    assert.equal(portfolioAllClearSayable({ attentionRead: read, taskInventory: complete }), false, read);
  }
});

test("a full page of tasks is not a complete inventory", () => {
  // `listTasks` returns a bare array: no total, no `truncated`. A page filled to the
  // cap is indistinguishable from "there is more", and the tasks past it can be
  // exactly the ones that need an answer — so the reach is read from the page's own
  // length, and a full page is capped rather than complete. Pessimistic by one case
  // (a list of exactly `limit` that is genuinely complete) and never optimistic.
  const page = (n: number) => ({ status: "fulfilled" as const, value: new Array(n).fill(null) });
  assert.equal(taskInventoryReachOf(page(9), 10), "complete");
  assert.equal(taskInventoryReachOf(page(10), 10), "capped");
  assert.equal(taskInventoryReachOf(page(0), 10), "complete");
  assert.equal(
    taskInventoryReachOf({ status: "rejected", reason: new Error("tasks down") }, 10),
    "unreadable",
  );
});

test("a stale decoration does not silence the board, only a missing inventory does", () => {
  // `projects` and `runs` decorate cards. Suppressing the line for them would make
  // the board go quiet over a detail it never needed to make the claim — the mirror
  // image of the bug above, and just as wrong. The reach the reassurance depends on
  // comes from the task read alone, so a sibling's failure cannot reach it.
  const ok = { status: "fulfilled" as const, value: { truncated: false } };
  const down = { status: "rejected" as const, reason: new Error("down") };
  for (const origin of ["projects", "runs"] as const) {
    const report = railReadReport({ tasks: ok, projects: ok, attention: ok, runs: ok, [origin]: down });
    assert.equal(
      portfolioAllClearSayable({ attentionRead: report.attentionReach, taskInventory: "complete" }),
      true,
      origin,
    );
    // …and it is still disclosed, so a stale card is never passed off as fresh.
    assert.deepEqual(report.staleOrigins, [origin]);
  }
});

test("every outcome that is not a complete read has a sentence in both locales", () => {
  const lookup = (locale: "en" | "zh", key: string): string | undefined => {
    const parts = key.split(".");
    assert.equal(parts.shift(), "portfolio", `key must be in the portfolio namespace: ${key}`);
    let node: unknown = portfolioDictionary[locale];
    for (const part of parts) {
      if (!node || typeof node !== "object" || !(part in (node as Record<string, unknown>))) {
        return undefined;
      }
      node = (node as Record<string, unknown>)[part];
    }
    return typeof node === "string" ? node : undefined;
  };
  const outcomes: Exclude<AttentionRead, "complete">[] = ["pending", "capped", "unreadable"];
  for (const outcome of outcomes) {
    for (const locale of ["en", "zh"] as const) {
      const text = lookup(locale, PORTFOLIO_ATTENTION_NOTICE_KEY[outcome]);
      assert.ok(text, `${locale} is missing ${PORTFOLIO_ATTENTION_NOTICE_KEY[outcome]}`);
    }
  }
  // The task read's own outcomes, for the same reason and with a stricter one on top:
  // this notice is the *only* place a capped inventory is disclosed. A missing key
  // here does not fall back to the load-error banner, it renders nothing, and the
  // board is silently back to an unexplained empty column.
  const inventory: Exclude<TaskInventoryReach, "complete">[] = ["pending", "capped", "unreadable"];
  for (const outcome of inventory) {
    for (const locale of ["en", "zh"] as const) {
      const key = PORTFOLIO_TASK_INVENTORY_NOTICE_KEY[outcome];
      assert.ok(lookup(locale, key), `${locale} is missing ${key}`);
    }
  }
  // Distinct sentences: "there are more than I read" and "I could not read it" are
  // different facts about the inventory, exactly as they are about attention.
  assert.notEqual(
    PORTFOLIO_TASK_INVENTORY_NOTICE_KEY.capped,
    PORTFOLIO_TASK_INVENTORY_NOTICE_KEY.unreadable,
  );
  // Both notices render through the board's `Notice`, which puts its children in a
  // div as plain React text. Markup in the copy is therefore not formatting, it is
  // literal characters on screen — the zh attention line shipped `**不**` and read as
  // `**不**`. A sentence that tries to stress the word carrying its meaning and
  // silently fails to is worth one assertion.
  //
  // The three forms below are the ones a writer of these sentences actually reaches
  // for; this is not a Markdown parser and does not claim to be. Underscores and
  // lone asterisks are left alone deliberately: they appear inside ordinary prose,
  // and a check that fires on them would be dropped rather than obeyed.
  const RENDERED_LITERALLY = [
    { markup: "**", what: "bold" },
    { markup: "`", what: "code" },
    { markup: "](", what: "a link" },
  ];
  for (const key of [
    ...Object.values(PORTFOLIO_ATTENTION_NOTICE_KEY),
    ...Object.values(PORTFOLIO_TASK_INVENTORY_NOTICE_KEY),
  ]) {
    for (const locale of ["en", "zh"] as const) {
      const text = lookup(locale, key) ?? "";
      for (const { markup, what } of RENDERED_LITERALLY) {
        assert.ok(
          !text.includes(markup),
          `${locale} ${key} writes ${what} as markdown, which this notice renders literally: ${text}`,
        );
      }
    }
  }
  // The column label too, or `unknown` renders as a blank heading.
  for (const locale of ["en", "zh"] as const) {
    assert.ok(lookup(locale, "portfolio.buckets.unknown"), `${locale} is missing the unknown label`);
  }
});
