// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProjectExecutionDashboard from "./ProjectExecutionDashboard.vue";
import { waitingReasonLabel } from "./executionLabels";

const api = vi.hoisted(() => ({
  getProjectExecutionSummary: vi.fn(), getResourceOccupancy: vi.fn(), listResourceQuotas: vi.fn(),
}));
vi.mock("../../services/api", () => ({ api }));

function summary(projectId: string, total = 26) {
  return { project_id: projectId, stats: { total, queued: total - 3, claimed: 0, running: 1,
    waiting_resource: 2, waiting_approval: 0, passed: 0, failed: 0, error: 0, blocked: 0, skipped: 0, cancelled: 0 },
    waiting_reasons: { "resource_type:browser": 1, "resource_type:docker": 1 }, sampled_at: "2026-09-18T01:00:00Z" };
}

describe("project execution dashboard", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    api.getProjectExecutionSummary.mockImplementation(async (id: string) => summary(id));
    api.getResourceOccupancy.mockResolvedValue({ active_count: 1, cleanup_pending_count: 1,
      cleanup_pending: [{ resource_type: "docker" }], by_type: { browser: 1 }, leases: [],
      quota_usage: { "project:p1": 2, "project_resource_type:p1:browser": 1, "project_resource_type:p1:docker": 1 } });
    api.listResourceQuotas.mockResolvedValue([{ resource_type: "browser", limit: 3 }, { resource_type: "docker", limit: 0 }]);
  });
  afterEach(() => { vi.useRealTimers(); });

  it("shows authoritative item totals, waiting reasons and retained cleanup quota", async () => {
    const wrapper = mount(ProjectExecutionDashboard, { props: { projectId: "p1" } });
    try {
      await flushPromises();
      expect(wrapper.text()).toContain("执行条目26");
      expect(wrapper.text()).toContain("等待浏览器：1");
      expect(wrapper.text()).toContain("等待Docker：1");
      const rows = wrapper.findAll("tbody tr");
      expect(rows[1].findAll("td").map(td => td.text())).toEqual(["浏览器", "1", "0", "1", "3"]);
      expect(rows[2].findAll("td").map(td => td.text())).toEqual(["Docker", "0", "1", "1", "未单独设限"]);
    } finally { wrapper.unmount(); }
    expect(vi.getTimerCount()).toBe(0);
  });

  it("rejects late responses from the previously selected project", async () => {
    let resolveOld!: (value: ReturnType<typeof summary>) => void;
    api.getProjectExecutionSummary.mockImplementation((id: string) => id === "p1"
      ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(summary(id, 88)));
    const wrapper = mount(ProjectExecutionDashboard, { props: { projectId: "p1" } });
    try {
      await wrapper.setProps({ projectId: "p2" });
      await flushPromises();
      resolveOld(summary("p1"));
      await flushPromises();
      expect(wrapper.text()).toContain("执行条目88");
      expect(wrapper.text()).not.toContain("执行条目26");
      expect(api.getResourceOccupancy.mock.calls[0][1].aborted).toBe(true);
    } finally { wrapper.unmount(); }
    expect(vi.getTimerCount()).toBe(0);
  });

  it("shows Agent task limits separately from physical capacity", async () => {
    api.getResourceOccupancy.mockResolvedValue({ active_count: 3, cleanup_pending_count: 0,
      cleanup_pending: [], by_type: { agent: 2, browser: 1 }, leases: [],
      quota_usage: { "project:p1": 1, "project_resource_type:p1:agent": 2 } });
    api.listResourceQuotas.mockResolvedValue([{ resource_type: "agent", limit: 5 }]);
    const wrapper = mount(ProjectExecutionDashboard, { props: { projectId: "p1" } });
    try {
      await flushPromises();
      expect(wrapper.findAll("tbody tr")[0].findAll("td").map(td => td.text())).toEqual(["Agent", "2", "0", "2", "5"]);
      expect(wrapper.text()).toContain("本项目资源槽位占用 1");
      expect(wrapper.text()).toContain("活动资源 1");
    } finally { wrapper.unmount(); }
  });

  it("preserves known data with an explicit stale warning when Redis fails", async () => {
    const wrapper = mount(ProjectExecutionDashboard, { props: { projectId: "p1" } });
    try {
      await flushPromises();
      api.getResourceOccupancy.mockRejectedValue(new Error("offline"));
      await wrapper.get("button").trigger("click");
      await flushPromises();
      expect(wrapper.get('[role="alert"]').text()).toContain("资源占用暂不可用");
      expect(wrapper.text()).toContain("执行条目26");
      expect(wrapper.text()).toContain("活动资源 1");
    } finally { wrapper.unmount(); }
  });

  it("bounds a stalled request and stops automatic work after unmount", async () => {
    api.getResourceOccupancy.mockImplementation((_id: string, signal: AbortSignal) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const wrapper = mount(ProjectExecutionDashboard, { props: { projectId: "p1" } });
    await vi.advanceTimersByTimeAsync(10000);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("资源占用暂不可用");
    expect(wrapper.find("table").exists()).toBe(false);
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.getProjectExecutionSummary).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps project/run quota reasons distinct", () => {
    expect(waitingReasonLabel("project_resource_type:p1:browser")).toBe("等待项目浏览器配额");
    expect(waitingReasonLabel("run:r1")).toBe("等待本次运行配额");
  });
});
