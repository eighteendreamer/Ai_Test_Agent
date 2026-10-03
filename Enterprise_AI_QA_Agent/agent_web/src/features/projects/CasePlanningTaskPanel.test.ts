// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CasePlanningTaskPanel from "./CasePlanningTaskPanel.vue";
import { HttpError } from "../../services/http";

const api = vi.hoisted(() => ({ submitCasePlanningTask: vi.fn(), getCasePlanningTask: vi.fn(),
  getCasePlanningTaskByRequest: vi.fn(), getCasePlan: vi.fn(), listCasePlanningTasks: vi.fn() }));
vi.mock("../../services/api", () => ({ api }));
const task = (status = "queued", extra = {}) => ({ id: "task", project_id: "project", creation_key: "original", status,
  plan_id: status === "completed" ? "plan" : null, round_count: 0, message: "已保存", ...extra });
function setup(projectId = "project") { return mount(CasePlanningTaskPanel, { props: { projectId, objective: "原始目标",
  runtimeJson: '{"count":0,"fixture_secret":"fixture-sensitive-marker"}', candidateCaseIds: ["case"], disabled: false } }); }
function button(w: ReturnType<typeof setup>, label: string) { return w.findAll("button").find(b => b.text() === label)!; }

describe("durable project planning UI", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); window.sessionStorage.clear();
    api.submitCasePlanningTask.mockImplementation(async (_, payload) => task("queued", { creation_key: payload.creation_key }));
    api.getCasePlan.mockResolvedValue({ id: "plan", project_id: "project" }); api.listCasePlanningTasks.mockResolvedValue({ items: [] });
  });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("opens the explicitly selected historical task instead of the project's latest request", async () => {
    window.sessionStorage.setItem("case-planning-task:project", JSON.stringify({ creationKey: "latest-request", taskId: "latest-task" }));
    api.getCasePlanningTask.mockResolvedValue(task("completed", { id: "historical-task", creation_key: "historical-request" }));
    const w = mount(CasePlanningTaskPanel, { props: { projectId: "project", initialTaskId: "historical-task",
      objective: "", runtimeJson: "{}", candidateCaseIds: [], disabled: false } });
    await flushPromises();
    expect(api.getCasePlanningTask).toHaveBeenCalledWith("project", "historical-task");
    expect(api.getCasePlanningTaskByRequest).not.toHaveBeenCalled();
    expect(w.emitted("completed")?.[0][0]).toEqual({ id: "plan", project_id: "project" });
    expect(api.submitCasePlanningTask).not.toHaveBeenCalled();
    w.unmount();
  });

  it("shows a failed explicit lookup and retries the same identity without falling back", async () => {
    api.getCasePlanningTask.mockRejectedValueOnce(new HttpError("task query unavailable", 503));
    const w = mount(CasePlanningTaskPanel, { props: { projectId: "project", initialTaskId: "historical-task",
      objective: "", runtimeJson: "{}", candidateCaseIds: [], disabled: false } });
    await flushPromises(); expect(w.text()).toContain("task query unavailable");
    api.getCasePlanningTask.mockResolvedValue(task("completed", { id: "different-task" }));
    await button(w, "查询规划任务").trigger("click"); await flushPromises();
    expect(api.getCasePlanningTask.mock.calls.map(call => call[1])).toEqual(["historical-task", "historical-task"]);
    expect(w.text()).toContain("规划任务身份不一致"); expect(w.emitted("completed")).toBeUndefined();
    expect(api.getCasePlan).not.toHaveBeenCalled(); expect(api.getCasePlanningTaskByRequest).not.toHaveBeenCalled();
    w.unmount();
  });

  it("keeps unknown submissions in memory, persists only identity and recovers after reopen", async () => {
    api.submitCasePlanningTask.mockRejectedValueOnce(new Error("response lost"));
    const w = setup(); await button(w, "生成执行计划").trigger("click"); await flushPromises();
    const submitted = api.submitCasePlanningTask.mock.calls[0][1];
    const stored = window.sessionStorage.getItem("case-planning-task:project")!;
    expect(JSON.parse(stored).creationKey).toBe(submitted.creation_key);
    expect(stored).not.toContain("fixture-sensitive-marker"); expect(stored).not.toContain("原始目标");
    await w.setProps({ objective: "新目标", runtimeJson: "{}" });
    await button(w, "重试规划提交").trigger("click"); await flushPromises();
    expect(api.submitCasePlanningTask.mock.calls[1][1]).toEqual(submitted);
    w.unmount();
    api.getCasePlanningTaskByRequest.mockResolvedValue(task("completed", { creation_key: submitted.creation_key }));
    const reopened = setup(); await flushPromises();
    expect(api.getCasePlanningTaskByRequest).toHaveBeenCalledWith("project", submitted.creation_key);
    expect(reopened.emitted("completed")?.[0][0]).toEqual({ id: "plan", project_id: "project" });
    const other = setup("other"); await flushPromises(); expect(other.text()).not.toContain(submitted.creation_key);
    reopened.unmount(); other.unmount();
  });

  it("polls a queued task, stops on completion and allows an explicit new identity", async () => {
    const w = setup(); await button(w, "生成执行计划").trigger("click"); await flushPromises();
    const key = api.submitCasePlanningTask.mock.calls[0][1].creation_key;
    api.getCasePlanningTask.mockResolvedValue(task("completed", { creation_key: key }));
    await vi.advanceTimersByTimeAsync(1000); await flushPromises();
    expect(api.getCasePlanningTask).toHaveBeenCalledWith("project", "task");
    expect(w.emitted("completed")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(5000); expect(api.getCasePlanningTask).toHaveBeenCalledTimes(1);
    await button(w, "另建规划任务").trigger("click"); await flushPromises();
    expect(api.submitCasePlanningTask.mock.calls[1][1].creation_key).not.toBe(key);
    w.unmount(); await vi.advanceTimersByTimeAsync(5000); expect(api.getCasePlanningTask).toHaveBeenCalledTimes(1);
  });

  it("shows poll failures without creating another task and rejects a foreign result", async () => {
    const w = setup(); await button(w, "生成执行计划").trigger("click"); await flushPromises();
    api.getCasePlanningTask.mockRejectedValueOnce(new HttpError("query unavailable", 503));
    await vi.advanceTimersByTimeAsync(1000); await flushPromises();
    expect(w.text()).toContain("query unavailable"); expect(api.submitCasePlanningTask).toHaveBeenCalledTimes(1);
    api.getCasePlanningTask.mockResolvedValue(task("completed", { project_id: "foreign" }));
    await button(w, "查询规划任务").trigger("click"); await flushPromises();
    expect(w.text()).toContain("不属于当前项目"); expect(api.getCasePlan).not.toHaveBeenCalled();
    w.unmount();
  });

  it("can find project tasks when browser storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
    const w = setup(); await button(w, "生成执行计划").trigger("click"); await flushPromises();
    expect(w.text()).toContain("浏览器无法保存任务编号");
    api.listCasePlanningTasks.mockResolvedValue({ items: [task("completed")] });
    await button(w, "项目规划任务").trigger("click"); await flushPromises();
    api.getCasePlanningTask.mockResolvedValue(task("completed"));
    await button(w, "规划任务已完成 · task").trigger("click"); await flushPromises();
    expect(w.emitted("completed")).toHaveLength(1); w.unmount();
  });
});
