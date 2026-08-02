import assert from "node:assert/strict";
import test from "node:test";

import {
  clearProtectedControlPlaneView,
  controlPlaneTaskIndexPath,
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

test("credential changes clear every protected view fact", () => {
  assert.deepEqual(
    clearProtectedControlPlaneView({
      projection: { task: "secret-task" },
      tasks: [{ task: "secret-task" }],
      taskTotal: 1,
      error: "old credential error",
    }),
    { projection: null, tasks: [], taskTotal: 0, error: null },
  );
});

test("Task discovery path is project-scoped and URL encoded", () => {
  assert.equal(
    controlPlaneTaskIndexPath("project/with space"),
    "/api/control-plane/projects/project%2Fwith%20space/tasks",
  );
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
