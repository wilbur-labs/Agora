import assert from "node:assert/strict";
import test from "node:test";

import {
  MANIFEST_REASON_KEY,
  SOURCE_REASON_KEY,
  crSummaryOf,
  sourceVerdictOf,
  type CrLogPayload,
  type ManifestReason,
  type SourceReason,
  type WorkspaceSourcePayload,
} from "../src/lib/workspace-sources.ts";
import { settings as settingsDictionary } from "../src/lib/i18n/dictionaries/settings.ts";

type Locale = "en" | "zh";

function lookup(locale: Locale, key: string): string | undefined {
  const parts = key.split(".");
  assert.equal(parts.shift(), "settings", `key must be in the settings namespace: ${key}`);
  let node: unknown = settingsDictionary[locale];
  for (const part of parts) {
    if (!node || typeof node !== "object" || !(part in (node as Record<string, unknown>))) {
      return undefined;
    }
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" ? node : undefined;
}

const source = (over: Partial<WorkspaceSourcePayload> = {}): WorkspaceSourcePayload => ({
  available: true, reason: null, root: "D:/AI/claude-workspace", tasks: [], skipped: [],
  unreadable_entries: [], ...over,
});

const task = (name: string) => ({
  name,
  manifest: {
    available: true, reason: null, title: null, goal: "a goal",
    status: "active", machine: null, milestones: [],
  },
  resume: { available: true, reason: null, current_state: null, next_step: null, progress: null },
  cr_log: {
    available: true, reason: null, rows: [], open_count: 0,
    unrecognised_status_count: 0, source: "x",
  },
  decisions: { available: true, reason: null, records: [], source: "x" },
  has_workflow: true,
});

// --- W7: a reason is not a count ------------------------------------------------

test("a directory nobody could read is never a count of zero", () => {
  // The substitution this module exists to refuse, and the one the timeline and the
  // portfolio board were each corrected for: `tasks: null` is "I could not look",
  // `tasks: []` is "I looked and there is nothing". A panel that renders both as
  // "0 tasks" tells the user the workspace is empty when the truth is that the path is
  // wrong, or the drive is not mounted.
  for (const reason of ["absent", "unreadable", "not_a_directory", "no_tasks_dir"] as const) {
    const verdict = sourceVerdictOf(source({ available: false, reason, tasks: null }));
    assert.deepEqual(verdict, { show: "unavailable", reason });
    // …and `count` does not exist on that branch, so it cannot be rendered.
    // @ts-expect-error — the count lives only on the `tasks` variant.
    assert.equal(verdict.count, undefined);
  }
});

test("an empty tasks/ folder is its own answer, not a failure", () => {
  const verdict = sourceVerdictOf(source({ tasks: [] }));
  assert.deepEqual(verdict, { show: "empty", skipped: [] });
});

test("tasks are counted, and what was skipped is named", () => {
  const verdict = sourceVerdictOf(source({ tasks: [task("agora"), task("kimono")], skipped: ["_template"] }));
  assert.equal(verdict.show, "tasks");
  assert.equal(verdict.show === "tasks" && verdict.count, 2);
  // Named rather than silently dropped: a directory this reader chose not to read is a
  // fact, and silence about it looks like the template was never there.
  assert.deepEqual(verdict.show === "tasks" && verdict.skipped, ["_template"]);
});

test("an entry nobody could stat stops the folder being called empty", () => {
  // The server listed `beta` and could not stat it, so it named it rather than dropping
  // it. Ignoring that field here undid the whole point one layer up: the panel read the
  // shortened list and said the folder held nothing.
  const verdict = sourceVerdictOf(source({ tasks: [], unreadable_entries: ["beta"] }));
  assert.equal(verdict.show, "partial");
  assert.deepEqual(verdict.show === "partial" && verdict.unreadable, ["beta"]);
  assert.equal(verdict.show === "partial" && verdict.count, 0);
});

test("a count standing beside an unreadable entry is a floor, not a total", () => {
  const verdict = sourceVerdictOf(source({
    tasks: [task("agora")], skipped: ["_template"], unreadable_entries: ["beta"],
  }));
  assert.equal(verdict.show, "partial");
  assert.equal(verdict.show === "partial" && verdict.count, 1);
  // The skipped list still travels: two different reasons a name is not in the list.
  assert.deepEqual(verdict.show === "partial" && verdict.skipped, ["_template"]);
});

test("the whole-read variants cannot carry unreadable entries at all", () => {
  const verdict = sourceVerdictOf(source({ tasks: [task("agora")] }));
  assert.equal(verdict.show, "tasks");
  // Structural, not a convention: `unreadable` exists only on `partial`, so "2 tasks"
  // with something unread is not a sentence this panel can produce.
  // @ts-expect-error — the unreadable list lives only on the `partial` variant.
  assert.equal(verdict.unreadable, undefined);
});

test("a source claiming to be available with no list is still unavailable", () => {
  // Defence against a server that sends `available: true, tasks: null`. Trusting the
  // flag over the payload would put a length read on null; trusting the payload cannot.
  const verdict = sourceVerdictOf(source({ available: true, reason: null, tasks: null }));
  assert.equal(verdict.show, "unavailable");
  assert.equal(verdict.show === "unavailable" && verdict.reason, "unreadable");
});

test("an open CR count never travels without the rows nobody could read", () => {
  // Earned on the first real log this reader saw: nine of thirty-two rows had a status
  // it could not parse, and "11 open" alone would have been offered as the whole truth.
  const summary = crSummaryOf({
    available: true, reason: null, rows: [], open_count: 11,
    unrecognised_status_count: 9, source: "x",
  });
  assert.deepEqual(summary, { show: "counts", open: 11, unrecognised: 9 });

  const unreadable: CrLogPayload = {
    available: false, reason: "malformed", rows: null, open_count: null,
    unrecognised_status_count: null, source: "x",
  };
  const gone = crSummaryOf(unreadable);
  assert.equal(gone.show, "unavailable");
  // @ts-expect-error — `open` exists only on the counts variant.
  assert.equal(gone.open, undefined);
});

test("every reason has a sentence in both locales", () => {
  // A missing key renders as the raw key, and these strings are the entire value of the
  // panel: they are what tells a wrong folder from an unreadable one.
  const reasons: SourceReason[] = ["absent", "unreadable", "not_a_directory", "no_tasks_dir"];
  const manifests: ManifestReason[] = [
    "absent", "unreadable", "too_large", "malformed", "duplicate_key", "unknown_schema",
  ];
  for (const locale of ["en", "zh"] as const) {
    for (const reason of reasons) {
      assert.ok(lookup(locale, SOURCE_REASON_KEY[reason]), `${locale} missing ${reason}`);
    }
    for (const reason of manifests) {
      assert.ok(lookup(locale, MANIFEST_REASON_KEY[reason]), `${locale} missing ${reason}`);
    }
    for (const key of [
      "settings.sources.title", "settings.sources.intro", "settings.sources.check",
      "settings.sources.confirm", "settings.sources.forget", "settings.sources.forgetHint",
      "settings.sources.none", "settings.sources.listUnreadable",
      "settings.sources.emptyTasksDir", "settings.sources.taskCount",
      "settings.sources.taskCountAtLeast", "settings.sources.unreadableEntries",
      "settings.sources.skipped", "settings.sources.goalEmpty",
      "settings.sources.crOpen", "settings.sources.crUnrecognised",
      "settings.sources.adrCount", "settings.sources.browse",
      // Added one review round after the keys they name: a coverage list is itself a
      // claim, and this one was narrower than the panel it covers.
      "settings.sources.staleProbe", "settings.sources.crUnavailable",
      "settings.sources.adrUnavailable",
      "settings.sources.browseUnavailable", "settings.sources.addLabel",
      "settings.sources.addHint", "settings.sources.addPlaceholder",
    ]) {
      assert.ok(lookup(locale, key), `${locale} missing ${key}`);
    }
  }
});

test("no reason is described as a count in either locale", () => {
  // Wording is not generally testable, but this one thing is: none of the four "nothing
  // to show" sentences may contain a zero, because that is exactly the collapse.
  for (const locale of ["en", "zh"] as const) {
    for (const reason of ["absent", "unreadable", "not_a_directory", "no_tasks_dir"] as const) {
      const sentence = lookup(locale, SOURCE_REASON_KEY[reason]) ?? "";
      assert.ok(!/\b0\b|零/.test(sentence), `${locale} ${reason} states a count: ${sentence}`);
    }
  }
});
