/**
 * How a review that did not finish is described on its card (CR-120, CR-121).
 *
 * Found in real use on 2026-09-25: a codex review ran out of quota and the card
 * showed the tail of codex's raw event stream — JSON lines, with the one sentence
 * that mattered ("try again at 8:44 PM") buried at the end. The server now settles
 * such a run with the provider's own reason (`agent reported: ...`), and this reads
 * that reason the way a person does.
 *
 * It also answers "was this review standing in for another one?" — the owner's rule
 * is that kiro reviews when codex is out of quota, and a card that just says "kiro
 * reviewed" hides that the reviewer they chose never looked.
 *
 * Mirrors `backend/agora/execution/review_fallback.py`; the two must agree on what
 * counts as a usage limit.
 */

export const REVIEW_SUBSTITUTES: Readonly<Record<string, string>> = { codex: "kiro" };

// Only codex's own phrase. A transient "rate limit ... retry in 1 second" or a
// "Disk quota exceeded" is not an allowance running out (kiro round 3 on CR-121).
const USAGE_LIMIT = /usage limit/i;
const GENERIC_FAILURE = "process exited with a non-zero status";
const RETRY_AT = /try again at ([^.]+?)\.?\s*$/i;
const AGENT_REPORTED = /^agent reported:\s*/;

export type ReviewFailure =
  | { kind: "quota"; when: string | null }
  | { kind: "other"; text: string };

export function isUsageLimit(reason: string | null | undefined): boolean {
  return !!reason && USAGE_LIMIT.test(reason);
}

/**
 * A failed run's reason, classified. `null` when the record gives none. When the error
 * is only the generic exit-status sentence, the last line of stderr is the better
 * reason — an expired login says so there, and nowhere else.
 */
export function reviewFailure(reason: string | null | undefined, stderr?: string | null): ReviewFailure | null {
  const lastErr = (stderr ?? "").split(/\r?\n/).map((l) => l.trim()).filter((l) => l !== "").pop();
  const error = (reason ?? "").trim();
  const said = error !== "" && error !== GENERIC_FAILURE ? error : (lastErr ?? error);
  if (said === "") return null;
  if (isUsageLimit(said)) {
    const match = RETRY_AT.exec(said);
    return { kind: "quota", when: match ? match[1].trim() : null };
  }
  return { kind: "other", text: said.replace(AGENT_REPORTED, "") };
}

interface ReviewLike {
  run_id: string;
  adapter: string;
  state: string;
}

/**
 * The review this one stood in for, if it did: an earlier review of the same work, by
 * the reviewer this one substitutes for, that failed on a usage limit. `reasonOf`
 * returns what the page has read of that run's error, or undefined if nothing yet —
 * and an unread reason is not a quota failure, so no claim is made from it.
 */
export function stoodInFor<T extends ReviewLike>(
  review: ReviewLike,
  siblings: readonly T[],
  reasonOf: (runId: string) => string | null | undefined,
): T | null {
  for (const other of siblings) {
    if (other.run_id === review.run_id) continue;
    if (REVIEW_SUBSTITUTES[other.adapter] !== review.adapter) continue;
    if (other.state !== "failed" && other.state !== "timed_out") continue;
    if (isUsageLimit(reasonOf(other.run_id))) return other;
  }
  return null;
}
