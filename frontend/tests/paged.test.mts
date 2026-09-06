import assert from "node:assert/strict";
import test from "node:test";

import { MAX_PAGE, PROBE, SERVER_PAGE_MAX, pageOf, readPage } from "../src/lib/paged.ts";

// --- CR-28: a full page is not evidence that more exists -----------------------

test("the probe fits inside the bound the server actually enforces", () => {
  // The mistake this pins was not in the logic, it was in assuming a parameter's
  // existence meant its range: the first version paged by 500 and asked for 501, which
  // every list route refuses (`le=500`), so four reads would have 422-ed on every load
  // with the whole frontend gate green. The page has to be smaller than the bound
  // because the probe row comes out of the bound, not out of the page.
  assert.ok(MAX_PAGE + PROBE <= SERVER_PAGE_MAX,
    `asking for ${MAX_PAGE + PROBE} against a bound of ${SERVER_PAGE_MAX} is a 422`);
  // And the page is as large as that allows — one row of evidence, not more.
  assert.equal(MAX_PAGE + PROBE, SERVER_PAGE_MAX);
  // ⚠️ Be clear what this does NOT establish, because the first version of it was
  // described as pinning the mirror and review showed it does no such thing: both
  // assertions above are arithmetic over this file's own two constants, so editing
  // `SERVER_PAGE_MAX` to 501 keeps them green while every maximum-sized read starts
  // 422-ing. The mirror is pinned on the other side —
  // `backend/tests/test_list_page_bound.py` reads `SERVER_PAGE_MAX` out of this module
  // and compares it against the routes' own declarations, so editing either side alone
  // fails there. Verified by mutation, not assumed.
});

test("truncation is the extra row, not a full page", () => {
  // The defect this replaces: `rows.length >= limit`. It cannot tell a truncated page
  // from a complete result of exactly that size, so a task with exactly a hundred runs
  // -- all read -- was announced as a history that could not be read in full.
  assert.deepEqual(pageOf([1, 2, 3], 3), { items: [1, 2, 3], truncated: false });
  assert.deepEqual(pageOf([1, 2, 3, 4], 3), { items: [1, 2, 3], truncated: true });
  assert.deepEqual(pageOf([], 3), { items: [], truncated: false });
});

test("the probe row is never shown", () => {
  // It exists to be counted, not rendered: a page that leaked it would be one item
  // longer than the limit it promises, and every count downstream would be off by one.
  const page = pageOf([1, 2, 3, 4], 3);
  assert.equal(page.items.length, 3);
  assert.ok(!page.items.includes(4));
});

test("readPage owns both numbers, so they cannot disagree", async () => {
  // The failure this shape prevents: asking for `limit` while paging by `limit` turns a
  // full page into `truncated: false` -- the one direction that must never happen,
  // because it would state completeness rather than merely fail to confirm it.
  const asked: number[] = [];
  const rows = Array.from({ length: 11 }, (_, i) => i);
  const page = await readPage(10, (limit) => {
    asked.push(limit);
    return Promise.resolve(rows.slice(0, limit));
  });
  assert.deepEqual(asked, [10 + PROBE]);
  assert.equal(page.items.length, 10);
  assert.equal(page.truncated, true);
});

test("a short read is complete, and says so", async () => {
  const page = await readPage(10, (limit) =>
    Promise.resolve(Array.from({ length: 4 }, (_, i) => i).slice(0, limit)));
  assert.equal(page.truncated, false);
  assert.equal(page.items.length, 4);
});

test("a read that rejects is not turned into an empty page", async () => {
  // `readPage` adds no error handling of its own: a failed read must stay failed, so
  // the caller decides between "unreadable" and "none". Swallowing it here would put
  // an empty, complete-looking page in front of every caller at once.
  await assert.rejects(
    readPage(10, () => Promise.reject(new Error("down"))),
    /down/,
  );
});
