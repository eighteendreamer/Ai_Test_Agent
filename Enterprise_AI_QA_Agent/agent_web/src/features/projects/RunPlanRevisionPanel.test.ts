// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import RunPlanRevisionPanel from "./RunPlanRevisionPanel.vue";
import { buttonByText, clickButton } from "../../test-utils/naive";

const api = vi.hoisted(() => ({
  listTestRunPlanRevisions: vi.fn(),
  reviseTestRunPlan: vi.fn(),
}));

vi.mock("../../services/api", () => ({ api }));

vi.mock("./CasePlanDialog.vue", async () => {
  const { defineComponent, h } = await import("vue");
  return {
    default: defineComponent({
      name: "CasePlanDialog",
      props: { candidateOnly: Boolean },
      emits: ["candidate", "close"],
      setup(_props, { emit }) {
        return () => h("div", [
          h("button", {
            "data-testid": "select-candidate",
            onClick: () => emit("candidate", { id: "candidate-plan", project_id: "project-1" }),
          }, "选择候选计划"),
          h("button", {
            "data-testid": "select-foreign-candidate",
            onClick: () => emit("candidate", { id: "foreign-plan", project_id: "other-project" }),
          }, "选择其他项目计划"),
        ]);
      },
    }),
  };
});

const record = {
  run_id: "run-1",
  revision: 3,
  idempotency_key: "revision-key-3",
  request_sha256: "a".repeat(64),
  candidate_plan_id: "plan-3",
  reason: "补齐查询前置",
  before_snapshot_sha256: "b".repeat(64),
  after_snapshot_sha256: "c".repeat(64),
  added_item_ids: ["item-new"],
  updated_item_ids: [],
  cancelled_item_ids: [],
  item_changes: [{
    change: "added",
    run_item_id: "item-new",
    after: { id: "item-new", status: "queued", case_id: "case-2", case_version_id: "version-2" },
  }],
  created_at: "2026-10-20T12:00:00Z",
};

const props = {
  projectId: "project-1",
  runId: "run-1",
  expectedRevision: 2,
  canRevise: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  api.listTestRunPlanRevisions.mockResolvedValue([]);
});

afterEach(() => {
  window.sessionStorage.clear();
});

it("shows immutable history and the affected RunItem details", async () => {
  api.listTestRunPlanRevisions.mockResolvedValue([record]);
  const wrapper = mount(RunPlanRevisionPanel, { props });
  await flushPromises();

  expect(wrapper.text()).toContain("修订 3");
  expect(wrapper.text()).toContain("补齐查询前置");
  expect(wrapper.text()).toContain("plan-3");
  await wrapper.get(".n-collapse-item__header-main").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("item-new");
  expect(wrapper.text()).toContain("version-2");
  expect(wrapper.text()).toContain("刷新运行详情后再提交");
  expect(buttonByText(wrapper, "提交计划修订")!.attributes("disabled")).toBeDefined();
});

it("submits the current revision and refreshes the panel after success", async () => {
  api.reviseTestRunPlan.mockResolvedValue(record);
  const wrapper = mount(RunPlanRevisionPanel, { props });
  await flushPromises();
  await wrapper.get("input").setValue(" plan-3 ");
  await wrapper.get("textarea").setValue(" 补齐查询前置 ");
  await clickButton(wrapper, "提交计划修订");

  expect(api.reviseTestRunPlan).toHaveBeenCalledWith("project-1", "run-1", expect.objectContaining({
    expected_revision: 2,
    plan_id: "plan-3",
    reason: "补齐查询前置",
    idempotency_key: expect.any(String),
  }));
  expect(wrapper.emitted("revised")?.[0]).toEqual([record]);
  expect(wrapper.text()).toContain("已提交修订 3");
});

it("opens the existing planning flow and accepts a candidate only from this project", async () => {
  const wrapper = mount(RunPlanRevisionPanel, { props });
  await flushPromises();

  await wrapper.get("textarea").setValue("旧计划原因");
  await wrapper.get('[data-testid="open-candidate-planner"]').trigger("click");
  expect(wrapper.findComponent({ name: "CasePlanDialog" }).props("candidateOnly")).toBe(true);
  await wrapper.get('[data-testid="select-candidate"]').trigger("click");
  await flushPromises();

  expect((wrapper.get("input").element as HTMLInputElement).value).toBe("candidate-plan");
  expect((wrapper.get("textarea").element as HTMLTextAreaElement).value).toBe("");
  expect(wrapper.text()).toContain("已选择候选计划 candidate-plan");
  expect(wrapper.findComponent({ name: "CasePlanDialog" }).exists()).toBe(false);
});

it("rejects a candidate plan that belongs to another project", async () => {
  const wrapper = mount(RunPlanRevisionPanel, { props });
  await flushPromises();
  await wrapper.get('[data-testid="open-candidate-planner"]').trigger("click");
  await wrapper.get('[data-testid="select-foreign-candidate"]').trigger("click");

  expect((wrapper.get("input").element as HTMLInputElement).value).toBe("");
  expect(wrapper.get('[role="alert"]').text()).toContain("候选计划不属于当前项目");
});

it("reuses the same idempotency key when a submission outcome is unknown", async () => {
  api.reviseTestRunPlan.mockRejectedValueOnce(new Error("connection dropped"))
    .mockResolvedValueOnce(record);
  const wrapper = mount(RunPlanRevisionPanel, { props });
  await flushPromises();
  await wrapper.get("input").setValue("plan-3");
  await wrapper.get("textarea").setValue("补齐查询前置");

  await clickButton(wrapper, "提交计划修订");
  expect(wrapper.text()).toContain("重试会复用相同请求和幂等键");
  expect((wrapper.get("input").element as HTMLInputElement).disabled).toBe(true);
  const firstRequest = api.reviseTestRunPlan.mock.calls[0][2];

  await clickButton(wrapper, "重试原修订");
  const secondRequest = api.reviseTestRunPlan.mock.calls[1][2];

  expect(secondRequest).toEqual(firstRequest);
  expect(wrapper.emitted("revised")?.[0]).toEqual([record]);
});

it("does not offer submission for a run that is not eligible for revision", async () => {
  const wrapper = mount(RunPlanRevisionPanel, { props: { ...props, canRevise: false } });
  await flushPromises();

  expect(wrapper.text()).toContain("此运行当前不支持修订");
  expect(wrapper.find("form").exists()).toBe(false);
});

it("reconciles a saved pending request when its idempotency key is already in history", async () => {
  const pending = {
    expected_revision: 2,
    idempotency_key: record.idempotency_key,
    plan_id: record.candidate_plan_id,
    reason: record.reason,
  };
  window.sessionStorage.setItem("run-plan-revision:project-1:run-1", JSON.stringify(pending));
  api.listTestRunPlanRevisions.mockResolvedValue([record]);

  const wrapper = mount(RunPlanRevisionPanel, { props });
  await flushPromises();

  expect(api.reviseTestRunPlan).not.toHaveBeenCalled();
  expect(window.sessionStorage.getItem("run-plan-revision:project-1:run-1")).toBeNull();
  expect(wrapper.text()).toContain("修订 3 已在历史记录中确认");
  expect(wrapper.emitted("revised")?.[0]).toEqual([record]);
});
