/**
 * The project conversation (CR-60..63) — what the middle column may say, decided once.
 *
 * One conversation per project. Before a goal exists it is the kickoff: the agent reads
 * the repository, asks one question at a time, and proposes a charter and the first
 * tasks. After adoption the same conversation stays open, and anything said in it is
 * judged against the charter — work to do now, a second version, or a remark.
 *
 * Everything here is a decision about what is *shown*, taken from the record the backend
 * reports (lib/kickoff.ts turns that read into a step). Two of them exist because the
 * older panel got them wrong:
 *
 *  - **"No goal" is a claim.** It may only be made from a read that landed. A dashboard
 *    read that failed produces `unknown`, and the gate that hides "dispatch" says it
 *    could not tell rather than that the project has no goal.
 *  - **A restatement is Agora's, not the agent's.** The kickoff's two passes do not
 *    answer per question, so what is echoed back after an answer is the answer as
 *    recorded — labelled as the record, never as the agent's understanding of it.
 *
 * Deliberately free of React and of "@/" path aliases: `node --test` executes the
 * companion test file (tests/project-chat.test.mts) directly.
 */

import type { KickoffQuestionLike, KickoffRecordLike, KickoffStep } from "./kickoff";

/** What a question's answer is set to when the person says "I am not sure — you decide".
 *  A marker sentence rather than no answer at all: the record then remembers the choice
 *  across a reload, and the second pass reads it as the instruction it is. */
export const YOU_DECIDE_ANSWER = "（不确定，你来定）";

export type AnswerChipKind = "suggestion" | "own" | "youDecide";

export interface AnswerChip {
  /** Stable within one question; "own" and "youDecide" are the two fixed ones. */
  key: string;
  kind: AnswerChipKind;
  /** The text a suggestion would store; null for the two fixed chips. */
  value: string | null;
}

/**
 * The candidates under one question. The agent's suggestions, then "write your own",
 * then "you decide" — the last two always present, because a question whose candidates
 * miss the real answer must not be a dead end, and skipping is a decision a person is
 * allowed to make.
 */
export function answerChipsOf(question: Pick<KickoffQuestionLike, "suggestions">): readonly AnswerChip[] {
  const suggestions = question.suggestions
    .map((text) => text.trim())
    .filter((text) => text.length > 0)
    .map((text, index): AnswerChip => ({ key: `s${index}`, kind: "suggestion", value: text }));
  return [...suggestions, { key: "own", kind: "own", value: null }, { key: "unsure", kind: "youDecide", value: null }];
}

/** Which question is on the table, or null when every one of them has been answered.
 *  The first unanswered one in the agent's order: the interview is a sequence, and
 *  jumping to the last unanswered one would re-open a question the person passed by. */
export function currentQuestionOf(record: Pick<KickoffRecordLike, "questions" | "answers">): {
  question: KickoffQuestionLike;
  index: number;
  total: number;
} | null {
  const total = record.questions.length;
  const index = record.questions.findIndex((q) => (record.answers[q.id] ?? "").trim().length === 0);
  if (index === -1) return null;
  return { question: record.questions[index], index, total };
}

export interface DraftField {
  /** The question's id, or one of the fixed keys below. */
  key: string;
  label: string;
  /** Null means "not settled yet" — shown as such, never as an empty value. */
  value: string | null;
  /** True for the field the last answer changed, so the panel can mark what just grew. */
  fresh: boolean;
}

/** The fixed key of the person's own opening words. */
export const DRAFT_BRIEF_KEY = "__brief";

/**
 * The goal draft as it stands: the person's own words, then one line per question —
 * answered or not. It grows as the conversation does, which is the point: the final
 * proposal should not be the first time they see what their answers add up to.
 *
 * `freshKey` marks the line the last answer wrote; pass null when nothing just moved.
 */
export function draftFieldsOf(
  record: Pick<KickoffRecordLike, "brief" | "questions" | "answers">,
  freshKey: string | null,
): readonly DraftField[] {
  const brief = record.brief.trim();
  return [
    { key: DRAFT_BRIEF_KEY, label: "", value: brief.length > 0 ? brief : null, fresh: freshKey === DRAFT_BRIEF_KEY },
    ...record.questions.map((question): DraftField => {
      const answer = (record.answers[question.id] ?? "").trim();
      return {
        key: question.id,
        label: question.question,
        value: answer.length > 0 ? answer : null,
        fresh: freshKey === question.id,
      };
    }),
  ];
}

export type ComposeMode =
  /** No record yet: the person writes what the project is, and that starts the kickoff. */
  | { kind: "brief" }
  /** A question is on the table; the big box is the long-form answer to it. */
  | { kind: "answer"; questionId: string }
  /** Every question answered, no proposal yet. */
  | { kind: "askForProposal" }
  /** A proposal is on the table: saying something asks for another one. */
  | { kind: "reply" }
  /** Adopted: anything said is judged against the charter. */
  | { kind: "idea" }
  /** Nothing can be typed, and why. */
  | { kind: "closed"; reason: "loading" | "readFailed" | "unreadable" | "adopting" | "notAppliedHere" };

export function composeModeOf(step: KickoffStep): ComposeMode {
  switch (step.kind) {
    case "loading":
      return { kind: "closed", reason: "loading" };
    case "readFailed":
      return { kind: "closed", reason: "readFailed" };
    case "unreadable":
      return { kind: "closed", reason: "unreadable" };
    case "start":
      return { kind: "brief" };
    case "questions":
    case "answered": {
      const current = currentQuestionOf(step.record);
      return current === null ? { kind: "askForProposal" } : { kind: "answer", questionId: current.question.id };
    }
    case "proposed":
      return { kind: "reply" };
    case "adopting":
      // A sealed adoption is mid-flight; typing into it would be typing at a record that
      // is being applied. The panel offers the resume instead.
      return { kind: "closed", reason: "adopting" };
    case "adopted":
      // The record says adopted; this control plane may not hold what it names — a clone,
      // or a database that was not restored. Proposing work against a charter that is not
      // here, into an order that is not here, would be proposing it against nothing. The
      // re-apply comes first.
      return step.here !== null && !step.here.complete
        ? { kind: "closed", reason: "notAppliedHere" }
        : { kind: "idea" };
  }
}

/**
 * Whether this project can be given work.
 *
 * Three answers, not two. "This project has no goal" is a claim about the project, and a
 * dashboard read that did not land cannot support it — hiding the buttons *and* saying
 * the goal is missing would be inventing the reason. `unknown` hides nothing and says
 * the state could not be read.
 */
export type ProjectGate =
  | { kind: "open" }
  | { kind: "noCharter" }
  | { kind: "unknown" };

export function projectGateOf(
  charterGoal: string | null | undefined,
  read: "landed" | "failed" | "pending",
): ProjectGate {
  if (read !== "landed") return { kind: "unknown" };
  return (charterGoal ?? "").trim().length > 0 ? { kind: "open" } : { kind: "noCharter" };
}

export interface PositionChip {
  key: string;
  kind: "suggested" | "last" | "later" | "no";
  /** 1-based, for the two chips that place the card; null for the two that do not. */
  position: number | null;
}

/**
 * Where an accepted card may go. The agent's suggestion, the end of the order, the
 * second version, and "no". The suggestion is dropped when it *is* the end — two chips
 * that do the same thing read as two different outcomes.
 */
export function positionChipsOf(
  suggested: number | null,
  orderLength: number,
): readonly PositionChip[] {
  const last = orderLength + 1;
  const chips: PositionChip[] = [];
  if (suggested !== null && suggested >= 1 && suggested < last) {
    chips.push({ key: "suggested", kind: "suggested", position: suggested });
  }
  chips.push({ key: "last", kind: "last", position: last });
  chips.push({ key: "later", kind: "later", position: null });
  chips.push({ key: "no", kind: "no", position: null });
  return chips;
}

/**
 * What to say about where a placed card ended up. The board is ordered by priority and
 * then by recency, and two neighbours that already share a priority cannot be split — so
 * the position asked for is a request, and this reads the answer instead of assuming it.
 */
export type LandingNote =
  | { kind: "exact"; position: number }
  | { kind: "moved"; asked: number; position: number }
  | { kind: "unknown" };

export function landingNoteOf(placement: {
  asked_position: number;
  landed_at: number | null;
  order: readonly unknown[];
}): LandingNote {
  if (placement.landed_at === null) return { kind: "unknown" };
  // "Put it last" on a shorter order than the one the person saw is still last: the ask
  // is compared against what it could mean on the order that came back.
  const asked = Math.min(placement.asked_position, Math.max(placement.order.length, 1));
  return placement.landed_at === asked
    ? { kind: "exact", position: placement.landed_at }
    : { kind: "moved", asked, position: placement.landed_at };
}
