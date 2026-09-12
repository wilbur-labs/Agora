/**
 * Project kickoff (W10) — what the panel may say at each step, decided once.
 *
 * The backend keeps the record in the project's repository and reports one read of it:
 * available with a record, or unavailable with a reason. The panel has to turn that into
 * a step, and the steps that are *not* steps of the flow — the read has not landed, the
 * read failed, the file is there but is not a record — must each be their own sentence
 * rather than the flow's first step: "start here" over a record that could not be read
 * would let a person start over a kickoff they had already half done.
 *
 * Deliberately free of React and of "@/" path aliases: `node --test` executes the
 * companion test file (tests/kickoff.test.mts) directly.
 */

export interface KickoffQuestionLike {
  id: string;
  question: string;
  why: string;
  suggestions: readonly string[];
}

export interface KickoffCharterLike {
  goal: string;
  non_goals: readonly string[];
  success_criteria: readonly string[];
}

export interface KickoffTaskLike {
  title: string;
  description: string;
  kind: string;
}

export interface KickoffProposalLike {
  charter: KickoffCharterLike;
  tasks: readonly KickoffTaskLike[];
  needs_mockup: boolean;
  mockup_task_missing: boolean;
}

export interface KickoffRecordLike {
  status: "questions" | "answered" | "proposed" | "adopting" | "adopted";
  /** One round of asking. Task keys carry it; a new round after an adoption gets a new one. */
  round: string;
  /** A hash of the record's content. Every mutation names the revision it saw; a stale
   *  one is refused. Content, not a counter: the file is versioned with the project, and
   *  two branches can hold the same counter over different answers. */
  revision: string;
  brief: string;
  charter_draft: KickoffCharterLike;
  questions: readonly KickoffQuestionLike[];
  answers: Readonly<Record<string, string>>;
  needs_mockup: boolean;
  mockup_note: string;
  read_notes: readonly string[];
  proposal: KickoffProposalLike | null;
  context: {
    readme: boolean;
    tree_entries: number;
    git_log_entries: number;
    unread: readonly string[];
    /** The text the agent was shown, verbatim — what its draft and questions were drawn from. */
    text: string;
  };
  /**
   * What was approved at the first adopt — sealed before any side effect — and the tasks
   * created so far. A retry resumes from `approved`; the body it sends is ignored.
   */
  adopted?: {
    charter: KickoffCharterLike;
    approved: readonly (KickoffTaskLike & { key: string })[];
    tasks: readonly { key: string; task_id: string; title: string; kind: string }[];
    /** True when a resume found the charter edited by a person while the adoption sat
     *  half done, and left theirs in place: the sealed charter was NOT applied. */
    charter_kept_edit?: boolean;
  } | null;
}

/** The read as the backend reports it. */
/** What this control plane holds of an adopted record — the record travels with the
 *  repository, the charter and tasks do not. */
export interface KickoffHereLike {
  /** This control plane recorded applying this round (a marker it wrote at adoption);
   *  false on a clone or after a database not restored. */
  applied_here: boolean;
  /** The control plane's charter against the approved one. "differs" after an ordinary
   *  edit is not incomplete; "absent" is. */
  charter: "matches" | "differs" | "absent";
  tasks_recorded: number;
  tasks_present: number;
  /** The record's own receipt names every approved task (the last record write landed). */
  receipt_complete: boolean;
  complete: boolean;
  /** The store could not be scanned (more tasks than the adoption's ceiling): the other
   *  fields are placeholders then, and nothing can be said about what is here. */
  scan_failed?: boolean;
  /** A task in the project carries one of this adoption's keys but is not the approved
   *  task; the server refuses to adopt over it. */
  key_conflict?: boolean;
}

export interface KickoffReadLike {
  available: boolean;
  /** Present for an adopted record; null or absent otherwise. */
  here?: KickoffHereLike | null;
  /** "absent" | "unreadable" | "malformed" | "foreign" | "linked" when unavailable;
   *  "foreign" is a record another project wrote into the same directory, "linked" a
   *  record path that is a link pointing elsewhere (neither read nor written). */
  reason: string | null;
  record: KickoffRecordLike | null;
}

/** The kind of the task the UI treats as "the mockup"; mirrors MOCKUP_TASK_KIND in the backend. */
export const MOCKUP_TASK_KIND = "mockup";

export type KickoffStep =
  /** The read has not landed. */
  | { kind: "loading" }
  /** The read failed — the request, not the file. */
  | { kind: "readFailed" }
  /** The file is there and could not be read, is not a record, or is another project's
   *  record on the same directory. Not "start here". */
  | { kind: "unreadable"; reason: "unreadable" | "malformed" | "foreign" | "linked" | "root_missing" }
  /** Never started: the one state where "start" is the honest offer. */
  | { kind: "start" }
  | { kind: "questions"; record: KickoffRecordLike; answered: number; total: number }
  | { kind: "answered"; record: KickoffRecordLike; answered: number; total: number }
  | { kind: "proposed"; record: KickoffRecordLike; proposal: KickoffProposalLike }
  /** Adoption started and did not finish (a failure halfway); adopting again resumes it
   *  from what was sealed — `approved` is what will be created, not the proposal. */
  | { kind: "adopting"; record: KickoffRecordLike; approved: number; created: number; scanFailed: boolean; keyConflict: boolean }
  | { kind: "adopted"; record: KickoffRecordLike; created: number; charterKept: boolean; here: KickoffHereLike | null };

export function kickoffStepOf(read: KickoffReadLike | null, readFailed: boolean): KickoffStep {
  if (read === null) return readFailed ? { kind: "readFailed" } : { kind: "loading" };
  if (!read.available || read.record === null) {
    if (read.reason === "absent") return { kind: "start" };
    const reason =
      read.reason === "malformed" || read.reason === "foreign" || read.reason === "linked" || read.reason === "root_missing"
        ? read.reason
        : "unreadable";
    return { kind: "unreadable", reason };
  }
  const record = read.record;
  const total = record.questions.length;
  const answered = record.questions.filter((q) => (record.answers[q.id] ?? "").trim().length > 0).length;
  switch (record.status) {
    case "questions":
      return { kind: "questions", record, answered, total };
    case "answered":
      return { kind: "answered", record, answered, total };
    case "proposed":
      // A "proposed" record with no proposal in it is not a proposal to show; it is a
      // record the answers step should have reset. Shown as answered, so the person can
      // ask for the proposal again rather than adopt nothing.
      return record.proposal === null
        ? { kind: "answered", record, answered, total }
        : { kind: "proposed", record, proposal: record.proposal };
    case "adopting":
      return {
        kind: "adopting", record,
        approved: record.adopted?.approved.length ?? 0,
        // What exists is what the store holds under the approved keys, when the backend
        // said (a task created just before the record write failed is in the store and
        // not in the record); the record's own count only when it did not.
        created: read.here?.tasks_present ?? record.adopted?.tasks.length ?? 0,
        scanFailed: read.here?.scan_failed === true,
        keyConflict: read.here?.key_conflict === true,
      };
    case "adopted":
      // The count is what was created, not what was proposed: the person unticks.
      return {
        kind: "adopted", record,
        created: record.adopted?.tasks.length ?? 0,
        charterKept: record.adopted?.charter_kept_edit === true,
        // Null when the backend did not say: the panel then makes no claim about this
        // control plane, rather than claiming "set" from the file alone.
        here: read.here ?? null,
      };
  }
}

/**
 * What stands between the person and "adopt", for the tasks *as ticked*: every question
 * answered is not required (a skip is a decision), but a UI project whose first ticked
 * task is not the mockup is about to adopt UI work ahead of the mockup — the step W8 paid
 * for. Judged on the selection, not on the proposal: unticking the mockup is exactly the
 * case the warning exists for.
 */
/** Why the adopt button is off, or null. The server refuses the same two things (422);
 *  the button says so before the click rather than after. */
export function adoptBlockedBy(goal: string, successCriteria: readonly string[]): "goal" | "criteria" | null {
  if (!goal.trim()) return "goal";
  if (successCriteria.filter((line) => line.trim().length > 0).length === 0) return "criteria";
  return null;
}

export function proposalCaveatsOf(
  proposal: Pick<KickoffProposalLike, "needs_mockup">,
  chosen: readonly Pick<KickoffTaskLike, "kind">[],
): readonly ("mockupTaskMissing" | "noTasks")[] {
  const caveats: ("mockupTaskMissing" | "noTasks")[] = [];
  if (chosen.length === 0) caveats.push("noTasks");
  if (proposal.needs_mockup && (chosen.length === 0 || chosen[0].kind !== MOCKUP_TASK_KIND)) caveats.push("mockupTaskMissing");
  return caveats;
}
