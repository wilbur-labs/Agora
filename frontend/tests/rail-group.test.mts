import assert from "node:assert/strict";
import test from "node:test";

import {
  RAIL_ATTENTION_NOTICE_KEY,
  RAIL_ORDER,
  RAIL_STALE_NOTICE_KEY,
  RAIL_STALE_ORDER,
  attentionReachOf,
  attentionReadIsComplete,
  railGroupOf,
  railReadReport,
  railSubtitleOf,
  settleRead,
  type RailGroup,
  railRunIsCurrent,
} from "../src/lib/rail-group.ts";
import { workspace as workspaceDictionary } from "../src/lib/i18n/dictionaries/workspace.ts";

type Locale = "en" | "zh";

/** Resolve a "workspace.…" message key against one locale, or undefined. */
function lookup(locale: Locale, key: string): string | undefined {
  const parts = key.split(".");
  assert.equal(parts.shift(), "workspace", `key must be in the workspace namespace: ${key}`);
  let node: unknown = workspaceDictionary[locale];
  for (const part of parts) {
    if (!node || typeof node !== "object" || !(part in (node as Record<string, unknown>))) {
      return undefined;
    }
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" ? node : undefined;
}

const kinds = (...values: string[]): ReadonlySet<string> => new Set(values);

/** Every task state the rail can be handed, calm ones first. */
const ALL_STATES = [
  "running", "review", "verified", "done", "cancelled",
  "backlog", "requirements", "design", "planned", "blocked", "failed",
] as const;

/** The groups that assert the task needs nothing from the human right now. */
const NOTHING_WAITING: readonly RailGroup[] = ["running", "review", "done"];

test("a complete read groups exactly as before", () => {
  const complete = (state: string, observed: ReadonlySet<string> = kinds()) =>
    railGroupOf({ task: { state }, kinds: observed, attentionComplete: true });
  assert.equal(complete("running"), "running");
  assert.equal(complete("review"), "review");
  // Verified still owes the human a handoff, so it is review, not done.
  assert.equal(complete("verified"), "review");
  assert.equal(complete("done"), "done");
  assert.equal(complete("cancelled"), "done");
  for (const state of ["backlog", "requirements", "design", "planned", "blocked", "failed"]) {
    assert.equal(complete(state), "needsYou", state);
  }
  assert.equal(complete("running", kinds("approval")), "needsYou");
});

test("a capped read never files a task as needing nothing", () => {
  // The finding: >500 open attention items exist, a running task's approval falls
  // off that page, and the rail filed it under "Running" — while the task-scoped
  // read on the same screen showed the gate and blocked Continue.
  for (const state of ALL_STATES) {
    const group = railGroupOf({ task: { state }, kinds: kinds(), attentionComplete: false });
    assert.ok(
      !NOTHING_WAITING.includes(group),
      `capped read claimed "${group}" for a ${state} task it could not cover`,
    );
  }
  assert.equal(railGroupOf({ task: { state: "running" }, kinds: kinds(), attentionComplete: false }), "unknown");
  assert.equal(railGroupOf({ task: { state: "verified" }, kinds: kinds(), attentionComplete: false }), "unknown");
});

test("a capped read still keeps the claims it can prove", () => {
  // Presence is provable from any page; only absence is not. An observed item, and
  // a state that awaits the human on its own, must not be downgraded to unknown —
  // that would hide real work behind uncertainty.
  for (const kind of ["question", "approval", "blocker"]) {
    assert.equal(
      railGroupOf({ task: { state: "running" }, kinds: kinds(kind), attentionComplete: false }),
      "needsYou",
      kind,
    );
  }
  for (const state of ["blocked", "failed", "backlog", "requirements", "design", "planned"]) {
    assert.equal(
      railGroupOf({ task: { state }, kinds: kinds(), attentionComplete: false }),
      "needsYou",
      state,
    );
  }
});

test("an unrecognized task state is never assumed to be fine", () => {
  // A state added to the backend later must land in needsYou, not silently in a
  // group that says nothing is waiting.
  for (const attentionComplete of [true, false]) {
    assert.equal(
      railGroupOf({ task: { state: "some_future_state" }, kinds: kinds(), attentionComplete }),
      "needsYou",
    );
  }
});

test("the unknown group never shows the lifecycle state as if it were the ask", () => {
  // The other half of the substitution: the row rendered `ask ?? state`, so a task
  // whose ask was cut off by the cap got a calm "running" subtitle.
  assert.deepEqual(railSubtitleOf("unknown", null), { kind: "stateWithUnknownAttention" });
  assert.deepEqual(railSubtitleOf("running", null), { kind: "state" });
  // A real ask always wins, in any group: it is the specific thing to act on.
  for (const group of RAIL_ORDER) {
    assert.deepEqual(railSubtitleOf(group, "Approve the review"), { kind: "ask", text: "Approve the review" });
  }
});

test("unknown is ordered above every group that means nothing is waiting", () => {
  // An uncertain task may in fact need a human, so it must not sort below the
  // settled work where triage stops looking.
  const index = (group: RailGroup) => RAIL_ORDER.indexOf(group);
  assert.ok(index("needsYou") < index("unknown"));
  for (const group of NOTHING_WAITING) {
    assert.ok(index("unknown") < index(group), `unknown must precede ${group}`);
  }
  // Every group the grouping can return is renderable.
  for (const state of ALL_STATES) {
    for (const attentionComplete of [true, false]) {
      for (const observed of [kinds(), kinds("approval")]) {
        const group = railGroupOf({ task: { state }, kinds: observed, attentionComplete });
        assert.ok(RAIL_ORDER.includes(group), `${group} is not in RAIL_ORDER`);
      }
    }
  }
});

test("every rail message the truncation path renders exists in every locale", () => {
  const keys = [
    ...RAIL_ORDER.map((group) => `workspace.rail.groups.${group}`),
    ...Object.values(RAIL_ATTENTION_NOTICE_KEY),
    "workspace.rail.unknownSubtitle",
  ];
  for (const key of keys) {
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, key) !== undefined, `missing ${locale} message: ${key}`);
    }
  }
  // The capped notice names the cap and the subtitle names the state, so both need
  // their placeholder — a dropped one would leave the sentence half-written on
  // screen. The other two take no placeholder by design: neither a pending nor a
  // failed read has a number to report, and the limit was exactly the false
  // specific the old shared message supplied.
  for (const locale of ["en", "zh"] as const) {
    assert.match(lookup(locale, RAIL_ATTENTION_NOTICE_KEY.capped) ?? "", /\{limit\}/);
    assert.doesNotMatch(lookup(locale, RAIL_ATTENTION_NOTICE_KEY.unreadable) ?? "", /\{limit\}/);
    assert.doesNotMatch(lookup(locale, RAIL_ATTENTION_NOTICE_KEY.pending) ?? "", /\{limit\}/);
    assert.match(lookup(locale, "workspace.rail.unknownSubtitle") ?? "", /\{state\}/);
  }
});

test("only a complete read licenses an absence, and each other outcome says which it was", () => {
  // The grouping question is binary: does an absence in the page mean anything?
  assert.equal(attentionReadIsComplete("complete"), true);
  for (const reach of ["pending", "capped", "unreadable"] as const) {
    assert.equal(attentionReadIsComplete(reach), false, reach);
    // ...but the disclosure is not. A failed read must not be handed the cap's
    // explanation: previously loaded tasks stay on screen and the user was given a
    // specific, wrong reason for the uncertainty beside them.
    assert.ok(RAIL_ATTENTION_NOTICE_KEY[reach], reach);
  }
  const messages = Object.values(RAIL_ATTENTION_NOTICE_KEY);
  assert.equal(new Set(messages).size, messages.length, "two outcomes share one message");
  // No key for `complete`: there is nothing to disclose about a read that covered
  // everything, and a notice rendered over good data is its own false claim.
  assert.ok(!("complete" in RAIL_ATTENTION_NOTICE_KEY));
  // The three non-complete outcomes must also read differently to a person, not
  // merely resolve to different keys.
  for (const locale of ["en", "zh"] as const) {
    const rendered = messages.map((key) => lookup(locale, key));
    assert.equal(new Set(rendered).size, rendered.length, `${locale}: two outcomes read alike`);
  }
});

// --------------------------------------------------------------------------
// One refresh, four reads: each origin's reach settled from its own outcome.
// --------------------------------------------------------------------------

const ok = <T,>(value: T): PromiseSettledResult<T> => ({ status: "fulfilled", value });
const failed = (message: string): PromiseRejectedResult => ({
  status: "rejected",
  reason: new Error(message),
});

/** All four reads succeeding, with the attention page complete. */
const allRead = () => ({
  tasks: ok([]),
  projects: ok([]),
  attention: ok({ truncated: false }),
  runs: ok([]),
});

test("a sibling read's failure never claims the attention read failed", () => {
  // The finding: listOpenAttention() succeeds with items, listRuns() rejects, and
  // the single shared catch set the reach to "unreadable" — the rail printing
  // "Open attention items could not be read" about a read that had succeeded.
  const report = railReadReport({ ...allRead(), runs: failed("runs boom") });
  assert.equal(report.attentionReach, "complete");
  assert.deepEqual(report.staleOrigins, ["runs"]);
  assert.deepEqual(report.errors, ["runs boom"]);

  // Same for the other two siblings, and for a capped attention page: the cap is
  // the attention read's own outcome and a sibling cannot overwrite it either.
  for (const origin of RAIL_STALE_ORDER) {
    const clean = railReadReport({ ...allRead(), [origin]: failed(`${origin} boom`) });
    assert.equal(clean.attentionReach, "complete", origin);
    assert.deepEqual(clean.staleOrigins, [origin]);
    const capped = railReadReport({
      ...allRead(),
      attention: ok({ truncated: true }),
      [origin]: failed(`${origin} boom`),
    });
    assert.equal(capped.attentionReach, "capped", origin);
  }
});

test("the attention read's own failure is the only thing that makes it unreadable", () => {
  const report = railReadReport({ ...allRead(), attention: failed("attention boom") });
  assert.equal(report.attentionReach, "unreadable");
  // A failed attention read says nothing about its siblings either: they read
  // fine, so nothing on screen is stale on their account.
  assert.deepEqual(report.staleOrigins, []);
  assert.deepEqual(report.errors, ["attention boom"]);

  // Directly, as the mapping the page depends on. `pending` is unreachable from a
  // settled read, by construction: it means "no read has settled yet".
  assert.equal(attentionReachOf(ok({ truncated: false })), "complete");
  assert.equal(attentionReachOf(ok({ truncated: true })), "capped");
  assert.equal(attentionReachOf(failed("boom")), "unreadable");
});

test("every read that failed is reported, and every read that did not is not", () => {
  const none = railReadReport(allRead());
  assert.equal(none.attentionReach, "complete");
  assert.deepEqual(none.staleOrigins, []);
  assert.deepEqual(none.errors, []);

  const all = railReadReport({
    tasks: failed("tasks boom"),
    projects: failed("projects boom"),
    attention: failed("attention boom"),
    runs: failed("runs boom"),
  });
  assert.equal(all.attentionReach, "unreadable");
  assert.deepEqual(all.staleOrigins, [...RAIL_STALE_ORDER]);
  // Attention first, then the fixed order: the banner must not reshuffle between
  // refreshes, and no failure may be dropped.
  assert.deepEqual(all.errors, ["attention boom", "tasks boom", "projects boom", "runs boom"]);

  // Non-Error rejections still produce a message rather than "[object Object]"
  // being the whole story.
  const odd = railReadReport({ ...allRead(), tasks: { status: "rejected", reason: "plain string" } });
  assert.deepEqual(odd.errors, ["plain string"]);
});

test("a read that throws settles as its own failure and does not abort its siblings", async () => {
  // A synchronous throw inside a Promise.all argument list rejects the batch
  // before the siblings start, which is how one origin's fault became everyone's.
  const [thrower, sibling] = await Promise.all([
    settleRead<string>(() => { throw new Error("sync boom"); }),
    settleRead(async () => "read fine"),
  ]);
  assert.equal(thrower.status, "rejected");
  assert.equal(sibling.status, "fulfilled");
  assert.equal(sibling.status === "fulfilled" ? sibling.value : null, "read fine");
  // Rejected async reads settle the same way, and the promise itself never
  // rejects — there is no combined failure path left to infer a reach from.
  const rejected = await settleRead(async () => { throw new Error("async boom"); });
  assert.equal(rejected.status, "rejected");
});

test("each stale-read notice speaks for its own read, in every locale", () => {
  const staleKeys = RAIL_STALE_ORDER.map((origin) => RAIL_STALE_NOTICE_KEY[origin]);
  assert.equal(new Set(staleKeys).size, staleKeys.length, "two origins share one message");
  for (const key of staleKeys) {
    for (const locale of ["en", "zh"] as const) {
      const message = lookup(locale, key);
      assert.ok(message !== undefined, `missing ${locale} message: ${key}`);
      // No placeholder: these notices report a failed read, and the 500-item cap
      // is exactly the false specific this whole area exists to keep out.
      assert.doesNotMatch(message ?? "", /\{limit\}/);
    }
  }
  // And none of them is the attention notice wearing a different key: a sibling
  // failure must be said in its own words.
  for (const locale of ["en", "zh"] as const) {
    const attentionMessages = new Set(
      Object.values(RAIL_ATTENTION_NOTICE_KEY).map((key) => lookup(locale, key)),
    );
    for (const key of staleKeys) {
      assert.ok(!attentionMessages.has(lookup(locale, key)), `${locale}: ${key} borrows attention's words`);
    }
  }
});

// --- CR-17: a run kept from before a failed refresh is not the current one ----

test("a run the rail is holding is current only while the runs read succeeds", () => {
  assert.equal(railRunIsCurrent([]), true);
  assert.equal(railRunIsCurrent(["tasks"]), true);
  assert.equal(railRunIsCurrent(["projects"]), true);
});

test("a failed runs read disqualifies the run in hand", () => {
  // The page keeps its previous page of runs when the read is rejected, which is
  // right -- blanking the rail on every failed refresh would be its own dishonesty.
  // But the run in hand is then from before the failure and may have finished since,
  // and the rail was rendering it as the current state dot, folding its timestamps
  // into the activity time, and ordering by it.
  assert.equal(railRunIsCurrent(["runs"]), false);
  assert.equal(railRunIsCurrent(["tasks", "runs"]), false);
  assert.equal(railRunIsCurrent(RAIL_STALE_ORDER), false);
});

test("the rail's rule is narrower than the portfolio card's, on purpose", () => {
  // Only a *rejected* read disqualifies. A capped one does not, because the rail
  // makes no claim from absence: it renders nothing when it holds no run for a task,
  // rather than "no runs yet". Its sibling on the board does make that claim, which
  // is why that one has to care about the cap and this one does not. `staleOrigins`
  // carries rejections only, and that is the whole input here.
  assert.deepEqual([...RAIL_STALE_ORDER], ["tasks", "projects", "runs"]);
  assert.equal(railRunIsCurrent(["projects", "tasks"]), true);
});

test("the rail drops the stale run in one place, not at each render site", async () => {
  // Structural, and the same honest limit as its counterpart on the portfolio board:
  // node --test strips types but does not transform JSX, so this suite cannot mount
  // the component. Dropping the run where the task list is derived is what keeps
  // anything downstream from using a run the page cannot vouch for; a guard at each
  // render site is one chance per site to be forgotten.
  //
  // W9 changed the shape this guards, not the guarantee. The column used to derive
  // three things from the run (a state dot, a relative time, the ordering) and the
  // drop had to happen where the groups were built so all three stopped at once. The
  // project-level column derives one — whether a run is in flight — so the drop moved
  // to where the project's task list is derived.
  //
  // W11 moved it once more, for the same reason and to the same effect. The task list
  // left the column for the page: the plan is derived in `planRows`, the gate is decided
  // there once, and `planRowsOf` is the only thing that turns it into a `running` field.
  // The column is now a step further from the runs than it was — it is not given tasks
  // at all — so the second half of this test reads the plan's renderer instead. Still
  // one place, still upstream of every render.
  const { readFile } = await import("node:fs/promises");
  const file = await readFile(
    new URL("../src/app/workspace/page.tsx", import.meta.url), "utf8");
  const source = file.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(source.includes('projectActiveReach === "complete"'),
    "the page must ask whether the active-run read can be vouched for");
  // Anchored to the assignment, not merely to the expression appearing somewhere: a
  // regex that only proves the gate exists in the file would pass on dead code, or on a
  // gate computed and then ignored.
  // The gate is decided once in the page and handed to the module that owns the plan;
  // nothing between them may re-derive it.
  assert.ok(/const runsAreCurrent = projectActiveReach === "complete";/.test(source),
    "that verdict must be the one handed to planRowsOf, not recomputed downstream");
  const plan = await readFile(new URL("../src/lib/task-plan.ts", import.meta.url), "utf8");
  const planSource = plan.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(/const running = input\.runsAreCurrent && input\.runningTaskIds\.has\(/.test(planSource),
    "the gate must be what produces the derived `running` field");
  // The same gate must also decide the invitation to start one: a stale snapshot that
  // still offered "Continue" would be claiming no run is active on a read that cannot
  // say so. `continueEntryGate` turns the null into its own refusal.
  assert.ok(/activeRunCount: input\.runsAreCurrent \?/.test(planSource),
    "a stale run snapshot must reach the continue gate as unknown, not as zero");
  // And the render site cannot reach past it, structurally: the column is not given the
  // runs at all, so there is nothing behind the gate for it to consult. Stronger than
  // forbidding one spelling of a second lookup, which aliases and helpers walk around.
  const column = await readFile(
    new URL("../src/components/project-nav.tsx", import.meta.url), "utf8");
  const columnSource = column.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  const rendered = await readFile(
    new URL("../src/components/task-plan.tsx", import.meta.url), "utf8");
  const renderedSource = rendered.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(renderedSource.includes("row.running"),
    "the rendered state must read the gated field");
  assert.ok(!/runs\.some\(|runs\.filter\(|RunSummary/.test(renderedSource),
    "the plan's renderer must not receive or re-derive runs either");
  // `runs: NavCapability` is the resource's capability, not the run data — what must not
  // reach the column is the runs themselves, so match on how they would be consulted and
  // on the type they would arrive as.
  assert.ok(!/\brunsAreCurrent\b|\bruns\.some\(|\bruns\.filter\(|\bRunSummary\b/.test(columnSource),
    "the column must not receive or re-derive runs — the gate is upstream of it");
});

test("the runs notice describes what the rail now does, not what it used to", async () => {
  // The notice outlived the presentation it described: it said the dots and times
  // *shown* might be out of date, while CR-17 had just stopped showing them. A
  // disclosure that survives its own subject is the same defect one level up — and
  // it was my own fix that made it false.
  const { workspace } = await import("../src/lib/i18n/dictionaries/workspace.ts");
  for (const locale of ["en", "zh"] as const) {
    const notice: string = workspace[locale].rail.runsUnreadable;
    assert.equal(typeof notice, "string");
    // It must not promise that anything derived from the failed read is on screen.
    for (const claim of ["out of date", "过时", "dots", "状态点", "activity times", "活动时间"]) {
      assert.ok(!notice.includes(claim),
        `${locale}: the runs notice must not describe a stale run as shown: ${claim}`);
    }
  }
});
