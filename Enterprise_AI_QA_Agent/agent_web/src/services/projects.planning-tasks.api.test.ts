import { afterEach, describe, expect, it, vi } from "vitest";
import { getCasePlanningTask, getCasePlanningTaskByRequest, listCasePlanningTasks, submitCasePlanningTask, submitRegressionPlanningTask, getTestCase, submitTestCaseReview, activateTestCase } from "./projects.api";

describe("project planning task HTTP contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends only the chosen fixed target identity and preserves the original submission", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({ id: "task" }) });
    vi.stubGlobal("fetch", fetchMock);
    const payload = { creation_key: "fixed-target", objective: "完整用例规划",
      environment: { environment_id: "env", snapshot_id: "snapshot", expected_snapshot_sha256: "a".repeat(64) },
      runtime_context: { empty: null, flag: false, count: 0 } };
    await submitCasePlanningTask("project", payload);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
    await getCasePlanningTaskByRequest("project", payload.creation_key);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("preserves a submission identity and encodes lookup keys independently of path segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({ id: "task" }) });
    vi.stubGlobal("fetch", fetchMock);
    const payload = { creation_key: "retry ?/# 中文", objective: "核对目标", runtime_context: { count: 0, flag: false } };
    const result = await submitCasePlanningTask("project?#", payload);
    await getCasePlanningTask("project?#", "task?#");
    await getCasePlanningTaskByRequest("project?#", payload.creation_key);
    await listCasePlanningTasks("project?#", 2, 3);
    const root = "/api/v1/projects/project%3F%23/case-planning-tasks";
    expect(result.id).toBe("task");
    expect(fetchMock.mock.calls[0][0]).toBe(root);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
    expect(fetchMock.mock.calls[1][0]).toBe(root + "/task%3F%23");
    const lookup = new URL(fetchMock.mock.calls[2][0], "http://fixture");
    expect(lookup.pathname).toBe(root + "/by-request");
    expect(lookup.searchParams.get("creation_key")).toBe(payload.creation_key);
    expect(fetchMock.mock.calls[3][0]).toBe(root + "?limit=2&offset=3");
  });

  it("encodes a regression parent and sends exact selected failures and versions", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({ id: "task" }) });
    vi.stubGlobal("fetch", fetchMock);
    const payload = { creation_key: "same-regression", result_ids: ["result"], version_overrides: { case: "v2" } };
    await submitRegressionPlanningTask("parent?#", payload);
    await getTestCase("case?#");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/runs/parent%3F%23/regression-planning-tasks");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/test-cases/case%3F%23");
  });

  it("preserves legacy review calls and sends exact version operations including explicit null CAS", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: "case" }) });
    vi.stubGlobal("fetch", fetchMock);
    await submitTestCaseReview("case?#");
    await submitTestCaseReview("case?#", "v2", "submit-original");
    await activateTestCase("case?#", "v2");
    await activateTestCase("case?#", "v2", { request_id: "activate-original", expected_active_version_id: null });
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ version_id: "v2", request_id: "submit-original" });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ version_id: "v2" });
    expect(JSON.parse(fetchMock.mock.calls[3][1].body)).toEqual({ version_id: "v2", request_id: "activate-original", expected_active_version_id: null });
    expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/test-cases/case%3F%23/activate");
  });
});
