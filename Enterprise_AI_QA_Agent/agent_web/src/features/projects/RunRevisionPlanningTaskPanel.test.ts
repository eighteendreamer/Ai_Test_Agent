// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import CasePlanningTaskPanel from "./CasePlanningTaskPanel.vue";

const api = vi.hoisted(() => ({ getCasePlanningTask: vi.fn(), getCasePlan: vi.fn() }));
vi.mock("../../services/api", () => ({ api }));

beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); window.sessionStorage.clear(); });
afterEach(() => { vi.useRealTimers(); });

function task(status: "pending" | "applied" | "rejected") {
  return { id: "task", project_id: "project", creation_key: "fixed-request", status: "completed", plan_id: "candidate",
    message: "候选规划完成", round_count: 2, run_revision: { run_id: "run", status,
      revision: status === "applied" ? 1 : null, message: status === "rejected" ? "运行修订版本已变化" : "修订确认" } };
}

it("keeps polling a saved candidate until the Run revision is confirmed and avoids emitting a new-run plan", async () => {
  api.getCasePlanningTask.mockResolvedValueOnce(task("pending")).mockResolvedValue(task("applied"));
  const wrapper = mount(CasePlanningTaskPanel, { props: { projectId: "project", initialTaskId: "task",
    objective: "", runtimeJson: "{}", candidateCaseIds: [], disabled: false } });
  await flushPromises();
  expect(wrapper.text()).toContain("修订等待确认");
  expect(wrapper.emitted("completed")).toBeUndefined();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.text()).toContain("修订已生效（版本 1）");
  expect(api.getCasePlan).not.toHaveBeenCalled();
  expect(wrapper.emitted("completed")).toBeUndefined();
  await vi.advanceTimersByTimeAsync(3000);
  expect(api.getCasePlanningTask).toHaveBeenCalledTimes(2);
  wrapper.unmount();
});

it("shows a rejected revision while retaining its candidate task identity", async () => {
  api.getCasePlanningTask.mockResolvedValue(task("rejected"));
  const wrapper = mount(CasePlanningTaskPanel, { props: { projectId: "project", initialTaskId: "task",
    objective: "", runtimeJson: "{}", candidateCaseIds: [], disabled: false } });
  await flushPromises();
  expect(wrapper.text()).toContain("修订未应用");
  expect(wrapper.text()).toContain("运行修订版本已变化");
  expect(wrapper.emitted("completed")).toBeUndefined();
  await vi.advanceTimersByTimeAsync(3000);
  expect(api.getCasePlanningTask).toHaveBeenCalledTimes(1);
  wrapper.unmount();
});
