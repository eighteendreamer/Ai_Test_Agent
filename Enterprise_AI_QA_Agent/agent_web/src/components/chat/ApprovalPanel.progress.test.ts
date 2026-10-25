// @vitest-environment jsdom
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import zhCN from "../../locales/zh-CN";
import { registerLocale, setLocale } from "../../services/i18n";
import { useSessionStore } from "../../stores/session";
import type { SessionDetail } from "../../types";
import ApprovalPanel from "./ApprovalPanel.vue";
import ConversationWorkbenchPlugin from "../../features/workbench/plugins/ConversationWorkbenchPlugin.vue";

describe("per-item approval panel", () => {
  beforeEach(() => {
    setActivePinia(createPinia()); registerLocale("zh-CN", zhCN); setLocale("zh-CN");
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("allows choosing B before A and keeps recording details scoped to A", async () => {
    const store = useSessionStore();
    store.session = { id: "session-1", pending_approvals: ["A", "B"].map((id) => ({
      id, tool_key: "inspect", tool_name: id, status: "pending", reason: id, created_at: "2026-10-26T00:00:00Z",
      metadata: id === "A" ? { approval_type: "ui_recording", recording_request: { project_id: "project-A" } } : {},
    })), framework_approval_progress: [] } as unknown as SessionDetail;
    const resolve = vi.spyOn(store, "resolveApproval").mockResolvedValue();
    const wrapper = mount(ApprovalPanel);
    const cards = wrapper.findAll(".approval-sidecard");
    expect(cards).toHaveLength(2);
    expect(cards[0].text()).toContain("project-A");
    expect(cards[1].text()).not.toContain("project-A");
    await cards[1].find("button.primary-btn").trigger("click");
    expect(resolve).toHaveBeenCalledWith("B", "approved", expect.any(String));
    wrapper.unmount();
  });

  it("renders durable decided progress after all pending cards disappear", () => {
    const store = useSessionStore();
    store.session = { pending_approvals: [], framework_approval_progress: [
      { approval_id: "B", tool_name: "Inspect B", decision: "approved", phase: "decision_submitted" },
      { approval_id: "C", tool_name: "Inspect C", decision: "denied", phase: "applied", outcome: "rejected" },
      { approval_id: "D", tool_name: "Inspect D", decision: "pending", phase: "recovery_required" },
    ] } as unknown as SessionDetail;
    const wrapper = mount(ApprovalPanel);
    expect(wrapper.text()).toContain("决定已提交");
    expect(wrapper.text()).toContain("已拒绝执行");
    expect(wrapper.text()).toContain("需要恢复对账");
    expect(wrapper.text()).not.toContain("approvalPanel.decision_pending");
    expect(wrapper.findAll("button")).toHaveLength(0);
    wrapper.unmount();
  });

  it("keeps the workbench approval panel mounted after the final decision", () => {
    const store = useSessionStore();
    store.session = { pending_approvals: [], framework_approval_progress: [
      { approval_id: "B", tool_name: "Inspect B", decision: "approved", phase: "applying_result" },
    ] } as unknown as SessionDetail;
    const wrapper = mount(ConversationWorkbenchPlugin, { global: { stubs: {
      ChatTimeline: true, RuntimeStatusPanel: true, ChatComposer: true, RecordingTimelinePanel: true,
    } } });
    expect(wrapper.findComponent(ApprovalPanel).exists()).toBe(true);
    expect(wrapper.text()).toContain("结果入库中");
    wrapper.unmount();
  });
});
