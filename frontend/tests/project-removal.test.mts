import assert from "node:assert/strict";
import { test } from "node:test";

import { LAST_PROJECT_KEY, forgetRemovedProject, removalErrorOf } from "../src/lib/project-removal.ts";

class Refusal extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function memory(initial: Record<string, string>) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    removeItem: (key: string) => { data.delete(key); },
  };
}

test("a 409 or 404 is the server's refusal, kept verbatim with its counts", () => {
  const message = "Project agora still has 1 task(s), 6 run(s); nothing was removed";
  assert.deepEqual(removalErrorOf(new Refusal(409, message)), { refused: true, message });
  assert.deepEqual(removalErrorOf(new Refusal(404, "Unknown project: x")), { refused: true, message: "Unknown project: x" });
});

test("anything else is a failure, not dressed up as a refusal", () => {
  assert.equal(removalErrorOf(new Refusal(500, "boom")).refused, false);
  assert.deepEqual(removalErrorOf(new TypeError("Failed to fetch")), { refused: false, message: "Failed to fetch" });
  assert.deepEqual(removalErrorOf("odd"), { refused: false, message: "odd" });
});

test("the workbench forgets the removed project only when it was the remembered one", () => {
  const store = memory({ [LAST_PROJECT_KEY]: "agora" });
  assert.equal(forgetRemovedProject(store, "other"), false);
  assert.equal(store.data.get(LAST_PROJECT_KEY), "agora");
  assert.equal(forgetRemovedProject(store, "agora"), true);
  assert.equal(store.data.has(LAST_PROJECT_KEY), false);
});

test("storage that is missing or throws is a lost convenience, not an error", () => {
  assert.equal(forgetRemovedProject(null, "agora"), false);
  const throwing = { getItem: () => { throw new Error("blocked"); }, removeItem: () => {} };
  assert.equal(forgetRemovedProject(throwing, "agora"), false);
});
