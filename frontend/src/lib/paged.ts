// A bounded read that *knows* whether more exists, instead of guessing from its own
// size.
//
// Every paged read in this app decided truncation the same way — `rows.length >= LIMIT`
// — and that comparison cannot tell a truncated page from a complete result of exactly
// that size. It is pessimistic by one and never optimistic, which is the right
// direction, so nothing derived from it was unsafe. What it did do is make a definite
// claim out of a guess: on a task with exactly a hundred runs, all of them read, the
// page announced that the run history could not be read in full. Review found eight
// sentences saying some version of that, across attention, the portfolio cards, the
// review gate and the recap — and the recap's reaches the seed prompt handed to an
// agent, so the false sentence was not only shown to a person.
//
// Wording each of those sentences down to "cannot be confirmed complete" was the
// cheaper fix and the wrong one: it is eight edits, it costs every one of those panels
// a true thing it could have said, and the next paged read added to this app starts the
// cycle again. The evidence is what was missing, so this module gets it — **ask for one
// more row than will be shown.** Getting `limit + 1` back is proof that a `limit + 1`th
// row exists; getting fewer is proof that it does not. `execution/deliverables.py` was
// already doing exactly this (it sets `truncated` only once a further entry actually
// arrives) and was the one place in the codebase whose definite wording was earned.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/paged.test.mts) directly.

/** How many rows beyond the page are asked for. One is all the evidence needed. */
export const PROBE = 1;

/**
 * The largest `limit` the control plane accepts: every list route declares
 * `Query(default=100, ge=1, le=500)` (attention/router.py, tasks/router.py,
 * execution/router.py x2). Asking for more is a 422, not a bigger page.
 */
export const SERVER_PAGE_MAX = 500;

/**
 * The largest page that can be *shown*, because the probe row has to fit inside the
 * server's bound. This is not a compromise, it is what the bound means: with `le=500`
 * nobody can distinguish 500 items from more than 500, so a reader that shows 499 and
 * knows is strictly better informed than one that shows 500 and guesses.
 *
 * Review caught the first version of this file asking for `500 + PROBE` at four sites.
 * Every one of those reads would have returned 422 unconditionally — the attention
 * rail, the portfolio's task and run reads, and the task-scoped gate read, all failing
 * on every load — and the frontend gate cannot see it: `node --test` never calls the
 * API, and `tsc`, eslint and `next build` have no opinion about a query bound. The
 * mistake underneath was assuming a parameter's *existence* meant its range. That is
 * the same defect this module exists to remove, made about the API instead of the data.
 *
 * The durable fix is for the routes to probe server-side and return the answer, which
 * would delete this constant along with the mirror it maintains (CR-28 names it).
 */
export const MAX_PAGE = SERVER_PAGE_MAX - PROBE;

/**
 * A page and what it knows about the rest. `truncated` is a fact here, not an
 * inference: it is true when the read returned a row beyond the page.
 */
export interface Page<T> {
  items: readonly T[];
  truncated: boolean;
}

/**
 * Split an over-fetched read into the page and the answer.
 *
 * `rows` must come from a read asked for `limit + PROBE`. Asking for exactly `limit`
 * and calling this would report `truncated: false` on a page that is in fact full,
 * which is the one direction that must never happen — hence `readPage` below, which
 * owns both numbers so a caller cannot pair a page size with a different request size.
 */
export function pageOf<T>(rows: readonly T[], limit: number): Page<T> {
  return rows.length > limit
    ? { items: rows.slice(0, limit), truncated: true }
    : { items: rows, truncated: false };
}

/**
 * Read one page, with the evidence. The limit is written once and this function does
 * the asking, so the request size and the page size cannot disagree — the failure that
 * would turn a pessimistic guess into an optimistic one.
 */
export async function readPage<T>(
  limit: number,
  read: (limit: number) => Promise<readonly T[]>,
): Promise<Page<T>> {
  return pageOf(await read(limit + PROBE), limit);
}
