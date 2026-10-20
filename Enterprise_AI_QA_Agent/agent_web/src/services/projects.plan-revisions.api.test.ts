import { afterEach, expect, it, vi } from "vitest";
import { listTestRunPlanRevisions, reviseTestRunPlan } from "./projects.api";

afterEach(() => vi.unstubAllGlobals());

it("submits an idempotent Run plan revision and reads its immutable history", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const payload = { expected_revision: 2, idempotency_key: "change/?#", plan_id: "plan/next",
    reason: "补齐目标前置" };

  await reviseTestRunPlan("project/?", "run/#", payload);
  await listTestRunPlanRevisions("project/?", "run/#");

  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/project%2F%3F/runs/run%2F%23/plan-revisions");
  expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
  expect(fetchMock.mock.calls[1][0]).toBe(fetchMock.mock.calls[0][0]);
  expect(fetchMock.mock.calls[1][1].method).toBeUndefined();
  expect(fetchMock.mock.calls[1][1].body).toBeUndefined();
  expect(fetchMock.mock.calls[1][1].headers).toEqual({ "Content-Type": "application/json" });
});
