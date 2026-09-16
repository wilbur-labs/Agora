/**
 * What happens on its own after a review is answered (CR-70, CR-83).
 *
 * The person's rule, in their words: "把我需要点击的内容能换成自动都换成自动，页面显示的内容
 * 可以多一些，但是少跳转、少按钮" — anything that can be automatic is; show more, click
 * less. Before this, approving a review left the task at `verified` with a "mark done"
 * the dashboard named and no page offered, and rejecting one blocked the task and closed
 * the conversation until a person re-typed the reviewer's words to the worker.
 *
 * Two judgements live here, both pure, so the component only sequences calls:
 *
 *   - which step is next once one is done — the first *planned* row after the current
 *     one in the board's order, else the first planned row before it (a step skipped
 *     earlier is still owed), else none;
 *   - what the worker is told when a review is rejected — the reviewer's words and the
 *     person's, kept apart and labelled, so the agent can tell who said what.
 */

import { CONTINUE_PROMPT_LIMIT } from "./continue-run.ts";

export interface AdvanceRowLike {
  task_id: string;
  step: number;
  title: string;
  state: string;
}

/** Only a planned step can be started; anything else is either done, running, or not ready. */
export const STARTABLE_STATE = "planned";

export function nextStepAfter<T extends AdvanceRowLike>(
  rows: readonly T[],
  currentTaskId: string,
): T | null {
  const at = rows.findIndex((row) => row.task_id === currentTaskId);
  const after = rows.slice(at + 1).find((row) => row.state === STARTABLE_STATE);
  if (after !== undefined) return after;
  const before = rows.slice(0, Math.max(at, 0)).find((row) => row.state === STARTABLE_STATE);
  return before ?? null;
}

/** Whether the rows are known well enough to say "there is no next step" (rule: never
 *  claim an absence over a cut read). */
export function noNextStepIsCertain(rowsWhole: boolean, next: AdvanceRowLike | null): boolean {
  return next === null && rowsWhole;
}

/**
 * The prompt a rejected review turns into. The reviewer's verdict first; then the
 * person's reason if they gave one. Empty reason → no empty section.
 *
 * Capped at the run prompt limit (`CONTINUE_PROMPT_LIMIT`, the control plane's own),
 * because an attention body may be twice that long: sending it whole gets a 422 *after*
 * the item was answered and the task moved to planned — the worst place to fail. The
 * person's reason is kept whole and the reviewer's body is what gets cut, with a marker
 * saying so; the full verdict stays on record in the attention item.
 */
export const REVIEW_BODY_TRUNCATED_MARK = "…（复核意见过长，此处按 run 提示词上限截断；全文在「待你处理」的记录里）";

export function reviewFixPrompt(input: {
  reviewTitle: string;
  reviewBody: string;
  reason: string;
}, limit: number = CONTINUE_PROMPT_LIMIT): string {
  const head = [
    "复核未通过，请按下面的意见修改；改完说明每一条是怎么处理的。",
    "",
    `## 复核意见（${input.reviewTitle.trim() || "未命名"}）`,
  ].join("\n");
  const reason = input.reason.trim();
  const tail = reason === "" ? "" : `\n\n## 我的补充\n${reason}`;
  const body = input.reviewBody.trim();
  const whole = `${head}\n${body}${tail}`;
  if (whole.length <= limit) return whole;
  const room = limit - head.length - 1 - tail.length - REVIEW_BODY_TRUNCATED_MARK.length;
  const cut = room > 0 ? body.slice(0, room) : "";
  return `${head}\n${cut}${REVIEW_BODY_TRUNCATED_MARK}${tail}`.slice(0, limit);
}
