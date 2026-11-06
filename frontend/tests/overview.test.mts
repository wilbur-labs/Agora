import assert from "node:assert/strict";
import test from "node:test";

import { crCountOf, gateLineOf, overviewCardsOf, overviewSummaryOf, sourceKeyOf } from "../src/lib/overview.ts";
import type { WorkProjectRow } from "../src/lib/work-state.ts";
import type { WorkspaceSourcePayload, WorkspaceTaskPayload } from "../src/lib/workspace-sources.ts";

function row(over: Partial<WorkProjectRow> = {}): WorkProjectRow {
  return {
    key: "k", source: "E:\\Work", task: "alpha", title: null, workspace: "claude", openable: true, reason: null,
    want_open: false, terminal: null, starting: false, launch: null, stale: false, agent_exited: false,
    stage: "todo", position: null, ...over,
  };
}

function task(name: string, over: Partial<WorkspaceTaskPayload> = {}): WorkspaceTaskPayload {
  return {
    name,
    manifest: { available: true, reason: null, title: `${name} title`, goal: null, status: "active", machine: null,
      primary_repo: null, primary_repo_malformed: false, milestones: [] },
    resume: { available: true, reason: null, current_state: null, next_step: null,
      progress: { done: 3, in_progress: 1, todo: 0, convention: "markers", reason: null } },
    cr_log: { available: true, reason: null, rows: [], open_count: 0, unrecognised_status_count: 0, malformed_ids: [], source: "" },
    decisions: { available: true, reason: null, records: [], unnumbered_files: [], source: "" },
    has_workflow: false,
    repo_link: { state: "not_recorded", project_id: null, repo: null },
    ...over,
  } as unknown as WorkspaceTaskPayload;
}

function source(root: string, tasks: WorkspaceTaskPayload[] | null): WorkspaceSourcePayload {
  return { available: tasks !== null, reason: tasks === null ? "unreadable" : null, root, tasks,
    skipped: [], unreadable_entries: [] } as unknown as WorkspaceSourcePayload;
}

test("the two reads are joined on the directory, however each spells it", () => {
  assert.equal(sourceKeyOf("E:\\Work\\"), sourceKeyOf("e:/Work"));
  const cards = overviewCardsOf({ projects: [row()] }, [source("E:/Work", [task("alpha")])], false);
  assert.equal(cards[0].record.show, "read");
  assert.equal(cards[0].record.show === "read" && cards[0].record.title, "alpha title");
});

test("a project the sources read did not cover claims nothing — no bar, no zero", () => {
  const view = { projects: [row()] };
  assert.equal(overviewCardsOf(view, null, false)[0].record.show, "loading");
  assert.equal(overviewCardsOf(view, [source("E:/Work", [task("alpha")])], true)[0].record.show, "unread");
  assert.equal(overviewCardsOf(view, [source("E:/Work", null)], false)[0].record.show, "unread");
  assert.equal(overviewCardsOf(view, [source("E:/Other", [task("alpha")])], false)[0].record.show, "unread");
});

test("CRs not closed are OPEN and DECIDED; an unread status is counted apart, never as closed", () => {
  const rows = ["open", "decided", "done", "unrecognised"].map((status_token) => ({ status_token }));
  const log = { available: true, reason: null, rows, open_count: 1, unrecognised_status_count: 1, malformed_ids: [], source: "" };
  assert.deepEqual(crCountOf(log), { show: "counts", notClosed: 2, unrecognised: 1 });
  assert.deepEqual(crCountOf({ ...log, available: false, rows: null, reason: "absent" }), { show: "none" });
  assert.deepEqual(crCountOf({ ...log, available: false, rows: null, reason: "malformed" }), { show: "unreadable", reason: "malformed" });
});

test("an unreadable RESUME is said as unreadable, not as a project with no progress", () => {
  const unread = task("alpha", { resume: { available: false, reason: "unreadable", current_state: null, next_step: null, progress: null } } as never);
  const card = overviewCardsOf({ projects: [row()] }, [source("E:/Work", [unread])], false)[0];
  assert.equal(card.record.show === "read" && card.record.progress.show, "resumeUnreadable");
});

test("the terminal state and the restore's attention ride along; the summary counts open terminals", () => {
  const cards = overviewCardsOf(
    { projects: [row({ key: "a", terminal: { terminal_id: "t", alive: true } }), row({ key: "b", task: "beta" })] },
    [source("E:/Work", [task("alpha"), task("beta")])], false, new Set(["b"]),
  );
  assert.deepEqual(cards.map((c) => [c.state, c.attention]), [["open", false], ["notOpen", true]]);
  assert.deepEqual(overviewSummaryOf(cards), { projects: 2, open: 1 });
});

// --- pause / archive / restore (CR-80) ------------------------------------------------------
import { shelfErrorOf, shelvedGroupsOf, type ShelvedProject } from "../src/lib/work-state.ts";

function held(over: Partial<ShelvedProject>): ShelvedProject {
  return { key: "k", source: "E:/Work", task: "t", title: null, workspace: "claude", status: "paused",
    archived_dir: false, ...over };
}

test("a refused change says why; an open terminal is said in the page's words", () => {
  assert.deepEqual(shelfErrorOf(JSON.stringify({ code: "terminal_open", message: "Close it" })), { kind: "terminalOpen" });
  assert.deepEqual(shelfErrorOf(JSON.stringify({ code: "refused", message: "tasks/_archive/x already exists" })),
    { kind: "refused", reason: "tasks/_archive/x already exists" });
  assert.deepEqual(shelfErrorOf("HTTP 500"), { kind: "refused", reason: "HTTP 500" });
});

test("shelved projects split into paused, archived, and a status the page has no word for", () => {
  const groups = shelvedGroupsOf([
    held({ key: "p", status: "paused" }),
    held({ key: "a", status: "archived", archived_dir: true }),
    held({ key: "d", status: "active", archived_dir: true }),
    held({ key: "x", status: "done" }),
  ]);
  assert.deepEqual(groups.paused.map((p) => p.key), ["p"]);
  assert.deepEqual(groups.archived.map((p) => p.key), ["a", "d"]);
  assert.deepEqual(groups.other.map((p) => p.key), ["x"]);
});

test("cards follow the person's order and carry the stage and whether todo would be refused", () => {
  const cards = overviewCardsOf({ projects: [
    row({ key: "a", task: "a" }),
    row({ key: "b", task: "b", stage: "doing", position: 0, want_open: true }),
    row({ key: "c", task: "c", position: 1 }),
  ] }, [], false);
  assert.deepEqual(cards.map((c) => [c.key, c.stage, c.busy]), [["b", "doing", true], ["c", "todo", false], ["a", "todo", false]]);
});

test("the review gate line: quiet when installed and clean or not installed, said when a read fails", () => {
  const read = (over: object) => ({ state: "read", reviewed: 4, skipped: 0, merges: 1, unrecorded: 0, truncated: false, ...over }) as const;
  assert.equal(gateLineOf(undefined), null);  // an older server: nothing claimed
  assert.equal(gateLineOf({ state: "not_installed", reason: null }), null);
  assert.equal(gateLineOf(read({})), null);
  assert.deepEqual(gateLineOf(read({ unrecorded: 2, skipped: 1 })), { show: "counts", unrecorded: 2, skipped: 1, truncated: false });
  assert.deepEqual(gateLineOf(read({ truncated: true })), { show: "counts", unrecorded: 0, skipped: 0, truncated: true });
  assert.deepEqual(gateLineOf({ state: "unreadable", reason: null }), { show: "unreadable", reason: "unreadable" });
  const cards = overviewCardsOf({ projects: [row()] },
    [source("E:/Work", [task("alpha", { review_gate: read({ unrecorded: 3 }) })])], false);
  assert.deepEqual(cards[0].record.show === "read" && cards[0].record.gate,
    { show: "counts", unrecorded: 3, skipped: 0, truncated: false });
});

// --- the workflow selection (CR-75 slice 4) ---------------------------------------------------
import { workflowViewOf } from "../src/lib/overview.ts";

const ALL = { requirements: true, breakdown: true, aidlc: false, deviation: true, review: true, records: true };

function withWorkflow(workflow: unknown, gate?: WorkspaceTaskPayload["review_gate"]): WorkspaceTaskPayload {
  const t = task("t");
  return { ...t, manifest: { ...t.manifest, workflow } as WorkspaceTaskPayload["manifest"], review_gate: gate };
}

test("the workflow switch is offered only when the file was read", () => {
  const read = withWorkflow({ state: "default", features: ALL, reason: null, base: "b" });
  assert.equal(workflowViewOf(withWorkflow(null), "claude"), null);     // manifest not read
  assert.equal(workflowViewOf(withWorkflow(undefined), "claude"), null); // an older server
  const view = workflowViewOf(read, "claude");
  assert.ok(view && view.show === "editable");
  assert.equal(view.isDefault, true);
  assert.equal(view.off, 0);
  assert.equal(view.base, "b");
});

test("off counts the switches turned off; aidlc, off by default, is not one of them", () => {
  const view = workflowViewOf(withWorkflow({ state: "set", features: { ...ALL, records: false, deviation: false },
    reason: null, base: "b" }), "claude");
  assert.ok(view && view.show === "editable");
  assert.equal(view.off, 2);
  assert.equal(view.isDefault, false);
  const aidlc = workflowViewOf(withWorkflow({ state: "set", features: { ...ALL, aidlc: true }, reason: null, base: "b" }), "claude");
  assert.ok(aidlc && aidlc.show === "editable" && aidlc.off === 0 && aidlc.features.aidlc);
  assert.ok(aidlc.aidlcUnsupported);   // claude records it and says it is not applied
});

test("a kiro project is offered the switch too; there AI-DLC is on by default and supported", () => {
  const KIRO_ALL = { ...ALL, aidlc: true };
  const view = workflowViewOf(withWorkflow({ state: "default", features: KIRO_ALL, reason: null, base: "b" }), "kiro");
  assert.ok(view && view.show === "editable");
  assert.equal(view.workspace, "kiro");
  assert.equal(view.off, 0);
  assert.equal(view.aidlcUnsupported, false);
  const off = workflowViewOf(withWorkflow({ state: "set", features: { ...KIRO_ALL, aidlc: false, review: false },
    reason: null, base: "b" }), "kiro");
  assert.ok(off && off.show === "editable");
  assert.equal(off.off, 2);            // turning kiro's AI-DLC off is one of the switches turned off
  assert.deepEqual(workflowViewOf(withWorkflow({ state: "unreadable", features: null, reason: "r", base: "b" }), "kiro"),
    { show: "unreadable", reason: "r" });
});

test("an unreadable block is said, never shown as the defaults", () => {
  assert.deepEqual(workflowViewOf(withWorkflow({ state: "unreadable", features: null, reason: "unknown switch: x", base: "b" }), "claude"),
    { show: "unreadable", reason: "unknown switch: x" });
});

test("the panel knows whether the repository's review gate is installed, whatever review is set to", () => {
  const wf = { state: "set", features: ALL, reason: null, base: "b" };
  const installed = workflowViewOf(withWorkflow(wf, { state: "unreadable", reason: "x" }), "claude");
  assert.ok(installed && installed.show === "editable" && installed.gateInstalled);
  const absent = workflowViewOf(withWorkflow(wf, { state: "not_installed", reason: null }), "claude");
  assert.ok(absent && absent.show === "editable" && !absent.gateInstalled);
  const older = workflowViewOf(withWorkflow(wf), "claude");
  assert.ok(older && older.show === "editable" && !older.gateInstalled);
});
