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
  archivedCountOf,
  portfolioGroupCountOf,
  taskRunsLineOf,
  projectMetricsSayable,
  projectNextActionLineOf,
  dashboardClaimOf,
  taskTrustLineOf,
  workspaceProjectStripOf,
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

test("the reach comes from evidence of a further row, not from a full page", () => {
  // This used to read `length >= limit` off the array, which cannot tell a truncated
  // page from a complete result of exactly that size: it called an inventory of
  // exactly `limit` tasks capped, and the cards then said the read had missed
  // something. The read now asks for one row beyond the page (`lib/paged.ts`), so
  // `truncated` is the fact that a further row came back — and the limit is not a
  // parameter here any more, so no caller can page by one number and ask for another.
  const read = (truncated: boolean) =>
    ({ status: "fulfilled" as const, value: { items: [], truncated } });
  assert.equal(taskInventoryReachOf(read(false)), "complete");
  assert.equal(taskInventoryReachOf(read(true)), "capped");
  assert.equal(
    taskInventoryReachOf({ status: "rejected", reason: new Error("tasks down") }),
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

// --- CR-10: a group badge is a count only when the read covered everything ----

test("a group's badge is exact only when both reads covered everything", () => {
  assert.deepEqual(portfolioGroupCountOf(7, "complete", "complete"), { kind: "exact", count: 7 });
});

test("a complete task read does not make an attention-dependent group exact", () => {
  // Which column a card is in is decided on the open-attention page as well: a task
  // whose question lies past that page's cap is put under "could not be sorted"
  // rather than into a calm column. Counting on the inventory alone printed an exact
  // "Needs you 0" beside a group whose real size the board had no way to know.
  assert.deepEqual(portfolioGroupCountOf(3, "complete", "capped"), { kind: "atLeast", count: 3 });
  assert.deepEqual(portfolioGroupCountOf(3, "complete", "unreadable"), { kind: "unknown" });
  assert.deepEqual(portfolioGroupCountOf(0, "complete", "capped"), { kind: "unknown" });
});

test("a capped read on either side makes the badge 'at least'", () => {
  // `cards.length` is how many the board is holding, and it holds one page. Since
  // CR-28 a capped reach means a further task was actually returned by the probe, so
  // this is no longer "a full page might mean more" — it is "there is more". Rendering
  // it as the group's size would turn "I can see this many" into "there are this many"
  // either way.
  assert.deepEqual(portfolioGroupCountOf(500, "capped", "complete"), { kind: "atLeast", count: 500 });
});

test("a failed refresh supports no lower bound either", () => {
  // The board keeps its previous snapshot when a refresh fails, so those cards are
  // what was true last time. "At least seven" is a claim no current read stands
  // behind -- capped is the only reach that earns a lower bound.
  assert.deepEqual(portfolioGroupCountOf(7, "unreadable", "complete"), { kind: "unknown" });
});

test("a board with no result yet reports no count at all", () => {
  // "at least 0" is true and useless.
  assert.deepEqual(portfolioGroupCountOf(0, "pending", "pending"), { kind: "unknown" });
});

test("a floor of zero is not worth rendering", () => {
  assert.deepEqual(portfolioGroupCountOf(0, "capped", "complete"), { kind: "unknown" });
});

test("the unknown variant carries no number to render by accident", () => {
  // Narrowed to what it establishes. `atLeast` deliberately carries a count too, and
  // nothing in the type stops a caller rendering it without the "at least" wording --
  // that is a rendering rule, and rendering rules need a harness this suite does not
  // have. What is provable here is that `unknown` has no number at all.
  const unknown = portfolioGroupCountOf(3, "pending", "complete");
  assert.equal("count" in unknown, false);
});

test("the archived count depends on the task read and nothing else", () => {
  // Archive membership comes off the task itself, so the attention read has no
  // bearing on it -- and passing a literal "complete" to satisfy a parameter that
  // does not apply is how a reach stops meaning anything.
  assert.deepEqual(archivedCountOf(3, "complete"), { kind: "exact", count: 3 });
  assert.deepEqual(archivedCountOf(3, "capped"), { kind: "atLeast", count: 3 });
  assert.deepEqual(archivedCountOf(0, "capped"), { kind: "unknown" });
  assert.deepEqual(archivedCountOf(3, "unreadable"), { kind: "unknown" });
});

// --- CR-11: "No runs yet" is a claim about the task, not about the page ------

test("a run in hand is showable when the read that produced it succeeded", () => {
  for (const reach of ["complete", "capped"] as const) {
    assert.deepEqual(taskRunsLineOf(true, reach), { kind: "run" });
  }
});

test("a refresh in flight does not blank a run already on screen", () => {
  // `pending` is not a read that succeeded, so it is kept out of the test above. It
  // is still shown: a refresh in flight has not contradicted anything, and blanking
  // the board on every reload would be its own kind of dishonesty.
  assert.deepEqual(taskRunsLineOf(true, "pending"), { kind: "run" });
});

test("a run left over from a failed refresh is not stated as the current one", () => {
  // The board keeps the previous page when the runs read fails, so the run in hand
  // may have been queued and since finished. Rendering it is a stale fact dressed
  // as a fresh one -- "a run in hand is always showable" was true only of a read
  // that succeeded.
  assert.deepEqual(taskRunsLineOf(true, "unreadable"), { kind: "unknown" });
});

test("no runs yet is sayable only when the runs read covered everything", () => {
  assert.deepEqual(taskRunsLineOf(false, "complete"), { kind: "none" });
});

test("a task absent from a page the board did not fully read is unknown, not none", () => {
  // The failure this exists to stop: a capped page looks exactly like "this task has
  // no runs" from a card's seat, and the task-level Runs view would contradict it.
  for (const reach of ["pending", "capped", "unreadable"] as const) {
    assert.deepEqual(taskRunsLineOf(false, reach), { kind: "unknown" });
  }
});

test("the board's claims are wired to the reads, and consumed where they are rendered", async () => {
  // **What this test is, exactly.** It reads source text. It is not a render test,
  // because `node --test` strips types but does not transform JSX, so this suite
  // cannot mount a .tsx component at all. That limit is why the decisions live in
  // this module as pure functions in the first place — those are tested properly
  // above — and why this one exists only to check that the component still *uses*
  // them.
  //
  // It was much weaker before. Asserting that a helper is called is satisfied by a
  // call whose result is thrown away, and a review demonstrated seven mutations that
  // each restored a user-visible false claim while every assertion here passed: a
  // `void`-ed reach publication, raw counts rendered beside a dead helper call, the
  // old `latestRun ? … : "No runs yet"` ternary, `(metricsSayable || true)`, the enum
  // label put back inside the `unknown` branch, and one ownership guard deleted while
  // the other was duplicated so the occurrence count still passed. So the assertions
  // below pin the **rendering expressions**, not the calls.
  const { readFile } = await import("node:fs/promises");
  const file = await readFile(
    new URL("../src/app/portfolio/page.tsx", import.meta.url), "utf8");
  // Comments stripped first. Both halves of this test need it: a banned fragment must
  // be bannable in the prose that explains why it is banned -- the first version
  // failed on its own explanation -- and a required fragment satisfied by a comment
  // would be a hole rather than a check.
  const source = file.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");

  for (const [what, fragment] of [
    // the decision is taken…
    ["group count", "portfolioGroupCountOf(count, taskInventory, attentionRead)"],
    ["archived count", "archivedCountOf(archivedCount, taskInventory)"],
    ["run line", "taskRunsLineOf(Boolean(latestRun), runsReach)"],
    ["metrics", "projectMetricsSayable(dashboard.truncated)"],
    ["next action", "projectNextActionLineOf(next.action, next.partial)"],
    // …and its result is what gets rendered
    ["group badge", 'shown.kind === "exact"'],
    ["archived label", 'archivedShown.kind === "exact"'],
    ["run branch", 'runLine.kind === "run" && latestRun'],
    ["no-runs branch", 'runLine.kind === "none"'],
    ["unknown next action", 'nextLine.kind === "unknown" ? ('],
    ["unknown next action copy", 't("portfolio.cockpit.nextAction.unknown")'],
    ["health guard", "{metricsSayable ? ("],
    // …and the reach it depends on is actually published, from a probed read: the
    // limit is passed to `readPage`, which is what asks for one row beyond it, so a
    // page can no longer be called truncated merely for being full (CR-28)
    ["runs request", "readPage(RUNS_PAGE_LIMIT, (limit) => listRuns({ limit }))"],
    ["runs reach", "setRunsReach(taskInventoryReachOf(runsRead))"],
    // …and each ownership boundary is guarded at its own render site
    ["cockpit owner", "dashboard.project_id === projectFilter && (\n          <ProjectCockpit"],
    ["editor owner", "showCharter && dashboard && dashboard.project_id === projectFilter"],
  ] as const) {
    assert.ok(source.includes(fragment), `${what}: expected the source to contain ${fragment}`);
  }

  // Whitespace-insensitive, so an expression that spans lines can be pinned too.
  // Both floors are checked at their own render site: replacing either one's
  // `next.partial` with `false` put the exact copy back on a partial response while
  // the key itself stayed in the source, so the key alone proves nothing.
  const flat = source.replace(/\s+/g, " ");
  for (const [what, fragment] of [
    ["attention floor", 'next.partial ? t("portfolio.cockpit.nextAction.attentionAtLeast"'],
    ["pending floor", 'next.partial ? t("portfolio.cockpit.nextAction.pendingAtLeast"'],
  ] as const) {
    assert.ok(flat.includes(fragment), `${what}: expected the source to contain ${fragment}`);
  }

  for (const [what, banned] of [
    ["the raw group badge", '<Badge variant="secondary">{cards.length}</Badge>'],
    ["the raw all-clear condition", 'next.action === "none"'],
    // A guard that is always true is a guard that was deleted with extra steps.
    ["a short-circuited guard", "|| true"],
    // The card's verified percentage, from the same evidence the cockpit dropped.
    ["the card's verified percentage", "portfolio.cockpit.card.verified"],
    // …and the fields behind it, so the ratio cannot come back without the key.
    ["the card's verified weights", "dash.verified_weight"],
    // The weight-derived project progress, which maps the same unbound historical
    // worker success onto the current criterion set.
    ["the weight-derived progress", "metrics.progress_pct"],
    ["the current-criteria fraction", "metrics.produced_weight"],
  ] as const) {
    assert.ok(!source.includes(banned), `${what} must not be in the page: ${banned}`);
  }

  // A reach must come from a read, not from a literal that fixes its answer.
  for (const literal of ['"complete"', "'complete'"]) {
    assert.ok(!source.includes(`, ${literal})`),
      `a reach must come from a read, not from ${literal}`);
  }
  // The charter editor is bound to the project the cockpit shows, so a filter change
  // has to close it; without this it re-mounted for the next project.
  assert.ok(source.includes("setShowCharter(false); setProjectFilter("),
    "changing the project filter must close the charter editor");
});

// --- the cockpit's own two claims -------------------------------------------

test("the weight tiles are shown only when the dashboard read the whole project", () => {
  assert.equal(projectMetricsSayable(false), true);
  // On a project whose first five hundred tasks are done, the cockpit could show
  // 100% and a green health while task five hundred and one was blocked. A
  // percentage of an unstated subset is not a percentage of the project.
  assert.equal(projectMetricsSayable(true), false);
});

test("the cockpit's next-action line has three outcomes, not two", () => {
  assert.deepEqual(projectNextActionLineOf("none", false), { kind: "allClear" });
  assert.deepEqual(projectNextActionLineOf("resolve_blocker", false), { kind: "action" });
});

test("a partial page names no action at all, empty or otherwise", () => {
  // Two ways the old boolean let the claim through. Suppressing the dedicated
  // all-clear sentence left the enum label for `none` -- "Nothing to do" -- saying it
  // again one line down; and a named action from a partial page is the winner of that
  // page, not of the project, because a higher-ranked task can sit past the cap.
  assert.deepEqual(projectNextActionLineOf("none", true), { kind: "unknown" });
  assert.deepEqual(projectNextActionLineOf("start_requirements", true), { kind: "unknown" });
  assert.deepEqual(projectNextActionLineOf("resolve_blocker", true), { kind: "unknown" });
});

// --- the strings these decisions resolve to must exist in both locales -------

test("every literal portfolio key, and every templated key's parent, exists in both locales", async () => {
  // Extracted from the source rather than listed by hand. The hand-written version of
  // this test walked the keys that were *defined* and passed while the renderer asked
  // for `portfolio.group.atLeast`, which was defined as `portfolio.cockpit.group.atLeast`
  // -- so the badge rendered the literal key and every test stayed green. A list I write
  // is a list of what I remembered; this is a list of what the page does.
  //
  // Keys built by template are checked as far as they can be: a `${...}` inside one
  // makes the leaf unknowable from the source, so what is asserted is that the part
  // before it resolves to an **object** in both locales. That catches a wrong parent
  // path -- which is the mistake this test exists for -- and not a missing leaf. Any
  // decision this slice makes therefore asks with a whole literal instead, which is
  // why the card's run line spells both of its keys out.
  const { readFile } = await import("node:fs/promises");
  const { portfolio } = await import("../src/lib/i18n/dictionaries/portfolio.ts");
  const source = await readFile(
    new URL("../src/app/portfolio/page.tsx", import.meta.url), "utf8");

  const whole = new Set<string>();
  const prefixes = new Set<string>();
  for (const match of source.matchAll(/["'`]portfolio\.([A-Za-z0-9_.]+)(\$\{)?/g)) {
    if (match[2]) prefixes.add(match[1].replace(/\.$/, ""));
    else whole.add(match[1]);
  }
  assert.ok(whole.size > 20, `expected to find the page's keys, found ${whole.size}`);
  assert.ok(prefixes.size > 0, "expected to find the page's templated keys too");

  const resolve = (locale: "en" | "zh", key: string) =>
    key.split(".").reduce<unknown>(
      (node, part) => (node as Record<string, unknown> | undefined)?.[part],
      portfolio[locale] as unknown,
    );

  const missing: string[] = [];
  for (const locale of ["en", "zh"] as const) {
    for (const key of whole) {
      if (typeof resolve(locale, key) !== "string") missing.push(`${locale}.portfolio.${key}`);
    }
    for (const key of prefixes) {
      const node = resolve(locale, key);
      if (typeof node !== "object" || node === null) {
        missing.push(`${locale}.portfolio.${key}.* (no such group)`);
      }
    }
  }
  assert.deepEqual(missing, [], "these keys are rendered as their own name");
});

// --- CR-21: a dashboard's rollups are current, a page, or from before a failure ---

test("a dashboard that read the whole project is current", () => {
  assert.equal(dashboardClaimOf({ readFailed: false, truncated: false }), "current");
});

test("a truncated read is a page, and its `total` is the page's size", () => {
  // `done/total` was computed as `tasks.length`, so the denominator was the size of
  // the page rather than of the project. A fraction whose whole is wrong is worse
  // than a missing one.
  assert.equal(dashboardClaimOf({ readFailed: false, truncated: true }), "partial");
});

test("a failed refresh is stale even when what it kept had been complete", () => {
  // The page keeps the dashboard it had, which is right; but every number in it is
  // from before the failure, and the only guard was an early return that fires when
  // there is no dashboard at all. A retained one sailed straight past it.
  assert.equal(dashboardClaimOf({ readFailed: true, truncated: false }), "stale");
  // Checked before `truncated`, because a stale response's own flag is as old as the
  // rest of it.
  assert.equal(dashboardClaimOf({ readFailed: true, truncated: true }), "stale");
});

test("the workspace strip shows a fraction only when its whole is the project", () => {
  const at = (over: Partial<Parameters<typeof workspaceProjectStripOf>[0]> = {}) =>
    workspaceProjectStripOf({
      readFailed: false, truncated: false, done: 3, pageSize: 10,
      action: "none", partial: false, attentionCount: 0, attention: false, title: null, ...over,
    });

  assert.deepEqual(at().count, { kind: "exact", done: 3, total: 10 });
  // `pageSize` was being passed as `total`, so the whole of "{done}/{total} done" was
  // the size of the page. A fraction whose whole is wrong is worse than a missing one.
  assert.deepEqual(at({ truncated: true }).count, { kind: "atLeast", done: 3 });
  // Nothing at all from a response that predates the failure.
  assert.deepEqual(at({ readFailed: true }).count, { kind: "hidden" });
});

test("the workspace strip's sentence is the same decision the cockpit takes", () => {
  const at = (over: Partial<Parameters<typeof workspaceProjectStripOf>[0]>) =>
    workspaceProjectStripOf({
      readFailed: false, truncated: false, done: 0, pageSize: 0,
      action: "none", partial: false, attentionCount: 0, attention: false, title: null, ...over,
    });

  assert.deepEqual(at({}).next, { kind: "allClear" });
  // The `action` variant carries its own label, so the renderer does not have to
  // reach back to the response for it -- and cannot reach past the decision for
  // anything else while it is there.
  assert.deepEqual(at({ action: "resolve_blocker", attention: true, title: "t-1" }).next,
    { kind: "action", action: "resolve_blocker", attention: true, title: "t-1" });
  assert.deepEqual(at({ partial: true }).next, { kind: "unknown" });
  // Stale disqualifies it even though the response's own `partial` says otherwise:
  // that flag is as old as the rest of the response.
  assert.deepEqual(at({ readFailed: true }).next, { kind: "unknown" });
  assert.deepEqual(at({ readFailed: true, action: "resolve_blocker" }).next, { kind: "unknown" });
});

test("the workspace strip's attention count is a floor, or nothing", () => {
  const at = (over: Partial<Parameters<typeof workspaceProjectStripOf>[0]>) =>
    workspaceProjectStripOf({
      readFailed: false, truncated: false, done: 0, pageSize: 0,
      action: "none", partial: false, attentionCount: 4, attention: false, title: null, ...over,
    });

  assert.deepEqual(at({}).attention, { kind: "exact", count: 4 });
  assert.deepEqual(at({ truncated: true }).attention, { kind: "atLeast", count: 4 });
  assert.deepEqual(at({ readFailed: true }).attention, { kind: "hidden" });
  assert.deepEqual(at({ attentionCount: 0 }).attention, { kind: "hidden" });
});

test("the trust line asks about the failure before it asks about the task", () => {
  // Giving a retained dashTask precedence meant a failed refresh still answered "no
  // approved criteria yet" -- a plain absence claim from a read that established
  // nothing -- or spoke about the current criterion set from a response that predates
  // the failure. The next action beside it had already been nulled for exactly this.
  assert.equal(taskTrustLineOf({ readFailed: true, hasTask: true, hasSpec: true }), "unavailable");
  assert.equal(taskTrustLineOf({ readFailed: true, hasTask: true, hasSpec: false }), "unavailable");
  assert.equal(taskTrustLineOf({ readFailed: true, hasTask: false, hasSpec: false }), "unavailable");
  assert.equal(taskTrustLineOf({ readFailed: false, hasTask: false, hasSpec: false }), "loading");
  assert.equal(taskTrustLineOf({ readFailed: false, hasTask: true, hasSpec: false }), "noCriteria");
  // Never a percentage: `verified_weight / total_weight` is not bound to the revision
  // its criteria belong to. This was that number's third home.
  assert.equal(taskTrustLineOf({ readFailed: false, hasTask: true, hasSpec: true }), "unbound");
});

test("the workspace page renders these decisions and does not restate them", async () => {
  // Structural, and narrower than it used to be on purpose: now that every element's
  // decision is a variant returned by a pure function, what is left to check is that
  // the component reads those variants rather than the raw response. The behaviour
  // itself is tested above, where it can actually be executed -- this suite cannot
  // mount a .tsx component at all.
  const { readFile } = await import("node:fs/promises");
  const file = await readFile(
    new URL("../src/app/workspace/page.tsx", import.meta.url), "utf8");
  const source = file.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");

  for (const fragment of [
    "workspaceProjectStripOf({",
    "taskTrustLineOf({",
    "strip.count.kind ===",
    "strip.next.kind ===",
    "strip.attention.kind !==",
    "trustLine ===",
  ]) {
    assert.ok(source.includes(fragment), `expected the page to contain ${fragment}`);
  }
  for (const banned of [
    // the page's own arithmetic on a page's length
    "const total = dashboard.tasks.length",
    // the unbound verification number, in the third place it lived
    "dashTask?.verified_weight",
    "dashTask?.total_weight",
  ]) {
    assert.ok(!source.includes(banned), `${banned} must not be in the page`);
  }
  assert.ok(source.includes("dashError ? null : dashTask?.next_action"),
    "a stale dashTask must not supply this task's next action");
});

test("the projects screen is reachable from the first-level navigation", async () => {
  // It was removed from the navigation once, on the reasoning that Today would absorb it.
  // The mockup says otherwise in as many words: Today answers what to push today, this
  // screen answers what I have. Neither this suite's bucket tests nor the page's own
  // assertions can catch it vanishing from the shell again -- and that is the failure that
  // made it unreachable, because the URL kept working the whole time.
  const { readFile } = await import("node:fs/promises");
  const shell = await readFile(
    new URL("../src/components/delivery-shell.tsx", import.meta.url), "utf8");
  assert.ok(/href:\s*"\/portfolio"/.test(shell),
    "the shell's first-level navigation must link to the projects screen");

  // And it must be labelled from the dictionary in both locales rather than hard-coded,
  // so the entry cannot appear untranslated.
  const { common } = await import("../src/lib/i18n/dictionaries/common.ts");
  for (const locale of ["en", "zh"] as const) {
    const label: string = common[locale].nav.projects;
    assert.equal(typeof label, "string");
    assert.ok(label.trim().length > 0, locale + ": the projects entry needs a label");
  }
});
