import assert from "node:assert/strict";
import test from "node:test";

import {
  AttentionResponseRetryKey,
  CandidateDispositionRetryKey,
  candidateDispositionActionReady,
  clearProtectedControlPlaneView,
  controlPlaneAttentionResponsePath,
  controlPlaneCandidateDispositionPath,
  controlPlanePlanApprovalPath,
  controlPlaneResponseBusy,
  controlPlaneTaskIndexPath,
  PlanApprovalRetryKey,
  planApprovalActionReady,
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

test("Attention response path binds and URL encodes Project, Task, and item", () => {
  assert.equal(
    controlPlaneAttentionResponsePath(
      "project/with space",
      "task/with space",
      "item/with space",
    ),
    "/api/control-plane/projects/project%2Fwith%20space/tasks/task%2Fwith%20space/attention/item%2Fwith%20space/responses",
  );
});

test("Plan approval path binds and URL encodes Project and Task", () => {
  assert.equal(
    controlPlanePlanApprovalPath("project/with space", "task/with space"),
    "/api/control-plane/projects/project%2Fwith%20space/tasks/task%2Fwith%20space/plan-approvals",
  );
});

test("candidate disposition path binds and URL encodes every authority identity", () => {
  assert.equal(
    controlPlaneCandidateDispositionPath(
      "project/with space",
      "task/with space",
      "candidate/with space",
    ),
    "/api/control-plane/projects/project%2Fwith%20space/tasks/task%2Fwith%20space/consultation-candidates/candidate%2Fwith%20space/dispositions",
  );
});

test("an unchanged Attention draft keeps its retry key after uncertainty", () => {
  const retry = new AttentionResponseRetryKey();
  const draft = {
    itemId: "attn_1",
    expectedVersion: 1,
    action: "answer" as const,
    response: "Tokyo",
  };
  let nonce = 0;
  const createNonce = () => `nonce-${++nonce}`;

  assert.equal(retry.forDraft(draft, createNonce), "attention-response:nonce-1");
  assert.equal(retry.forDraft(draft, createNonce), "attention-response:nonce-1");
  assert.equal(
    retry.forDraft({ ...draft, response: "Osaka" }, createNonce),
    "attention-response:nonce-2",
  );
  assert.equal(
    retry.forDraft({ ...draft, expectedVersion: 2 }, createNonce),
    "attention-response:nonce-3",
  );
});

test("an unchanged Plan approval keeps its retry key after uncertainty", () => {
  const retry = new PlanApprovalRetryKey();
  const draft = {
    taskId: "task_1",
    expectedTaskVersion: 7,
    planId: "plan_1",
    expectedPlanVersion: 4,
    reason: "Reviewed all formal evidence.",
  };
  let nonce = 0;
  const createNonce = () => `nonce-${++nonce}`;

  assert.equal(retry.forDraft(draft, createNonce), "plan-approval:nonce-1");
  assert.equal(retry.forDraft(draft, createNonce), "plan-approval:nonce-1");
  assert.equal(
    retry.forDraft({ ...draft, reason: "Updated rationale." }, createNonce),
    "plan-approval:nonce-2",
  );
  assert.equal(
    retry.forDraft({ ...draft, expectedTaskVersion: 8 }, createNonce),
    "plan-approval:nonce-3",
  );
  assert.equal(
    retry.forDraft({ ...draft, expectedPlanVersion: 5 }, createNonce),
    "plan-approval:nonce-4",
  );
});

test("an unchanged candidate disposition keeps its retry key after uncertainty", () => {
  const retry = new CandidateDispositionRetryKey();
  const draft = {
    candidateId: "candidate_1",
    expectedCandidateSha256: "a".repeat(64),
    expectedPlanVersion: 4,
    action: "adopt" as const,
    reason: "Reviewed the bounded advice.",
  };
  let nonce = 0;
  const createNonce = () => `nonce-${++nonce}`;

  assert.equal(
    retry.forDraft(draft, createNonce),
    "candidate-disposition:nonce-1",
  );
  assert.equal(
    retry.forDraft(draft, createNonce),
    "candidate-disposition:nonce-1",
  );
  assert.equal(
    retry.forDraft({ ...draft, action: "reject" }, createNonce),
    "candidate-disposition:nonce-2",
  );
  assert.equal(
    retry.forDraft({ ...draft, reason: "Changed rationale." }, createNonce),
    "candidate-disposition:nonce-3",
  );
  assert.equal(
    retry.forDraft({ ...draft, expectedPlanVersion: 5 }, createNonce),
    "candidate-disposition:nonce-4",
  );
  assert.equal(
    retry.forDraft({ ...draft, expectedCandidateSha256: "b".repeat(64) }, createNonce),
    "candidate-disposition:nonce-5",
  );
});

test("projection refresh blocks Attention submission until its lease settles", () => {
  assert.equal(controlPlaneResponseBusy(true, null), true);
  assert.equal(controlPlaneResponseBusy(false, "attn_1"), true);
  assert.equal(controlPlaneResponseBusy(false, null, true), true);
  assert.equal(controlPlaneResponseBusy(false, null, false, "candidate_1"), true);
  assert.equal(controlPlaneResponseBusy(false, null), false);
});

test("Plan approval is actionable only as the sole matching human action", () => {
  const planApproval = { kind: "plan_approval", source_id: "plan_1" };

  assert.equal(planApprovalActionReady([planApproval], "plan_1"), true);
  assert.equal(planApprovalActionReady([planApproval], "plan_other"), false);
  assert.equal(
    planApprovalActionReady(
      [planApproval, { kind: "candidate_disposition", source_id: "candidate_1" }],
      "plan_1",
    ),
    false,
  );
});

test("candidate disposition is actionable only for the matching projected action", () => {
  const actions = [
    { kind: "attention", source_id: "attention_1" },
    { kind: "candidate_disposition", source_id: "candidate_1" },
  ];

  assert.equal(candidateDispositionActionReady(actions, "candidate_1"), true);
  assert.equal(candidateDispositionActionReady(actions, "candidate_other"), false);
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
