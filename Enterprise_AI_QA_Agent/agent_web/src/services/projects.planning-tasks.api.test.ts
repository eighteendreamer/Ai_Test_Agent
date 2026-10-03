import { afterEach, describe, expect, it, vi } from "vitest";
import { getCasePlanningTask, getCasePlanningTaskByRequest, listCasePlanningTasks, submitCasePlanningTask } from "./projects.api";

describe("project planning task HTTP contract", () => {
  afterEach(() => vi.unstubAllGlobals());

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
});
