import assert from "node:assert/strict";
import test from "node:test";

import {
  ProjectionRequestLifecycle,
  runProtocolDimensions,
} from "../src/lib/control-plane-view.ts";

test("forget invalidates the active projection request", () => {
  const lifecycle = new ProjectionRequestLifecycle();
  const request = lifecycle.begin();

  lifecycle.invalidate();

  assert.equal(request.signal.aborted, true);
  assert.equal(lifecycle.isCurrent(request.requestId), false);
});

test("starting even an invalid reconnect attempt retires the older lease", () => {
  const lifecycle = new ProjectionRequestLifecycle();
  const older = lifecycle.begin();
  const invalidAttempt = lifecycle.begin();

  lifecycle.finish(invalidAttempt.requestId);

  assert.equal(older.signal.aborted, true);
  assert.equal(lifecycle.isCurrent(older.requestId), false);
  assert.equal(lifecycle.isCurrent(invalidAttempt.requestId), true);
});

test("wait, process, exit, and semantic facts remain independent", () => {
  const nonzero = runProtocolDimensions({
    process_status: null,
    wait_state: "settled",
    process_exit_code: 7,
    timed_out: false,
    semantic_result: "succeeded",
  });
  assert.deepEqual(nonzero, {
    process: "unavailable",
    wait: "settled",
    exit: "7",
    exitFailed: true,
    semantic: "succeeded",
  });

  const semanticFailureAfterZeroExit = runProtocolDimensions({
    process_status: "exited",
    wait_state: "settled",
    process_exit_code: 0,
    timed_out: false,
    semantic_result: "failed",
  });
  assert.equal(semanticFailureAfterZeroExit.exitFailed, false);
  assert.equal(semanticFailureAfterZeroExit.semantic, "failed");
});
