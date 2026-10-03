// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CasePlanDialog from "./CasePlanDialog.vue";

const api = vi.hoisted(() => ({ getCasePlanningPolicy: vi.fn(), saveCasePlanningPolicy: vi.fn(), createCasePlan: vi.fn(), getCasePlan: vi.fn() }));
vi.mock("../../services/api", () => ({ api }));
const config = () => ({ scope_key: "global", revision: 1, policy: { max_rounds: 5, max_candidates: 20, max_items: 50, max_context_chars: 180000, timeout_seconds: 180 } });
function plan(status = "planned") {
  return { id: "plan", project_id: "project", status, summary: "按完整用例规划", warnings: [], config_snapshot: config(),
    decision: { items: [{ key: "target", kind: "target", case_id: "case", case_version_id: "version", reason: "验证拒绝", evidence_ids: ["case:version"], depends_on: [], input_bindings: {} }],
      unresolved: status === "blocked" ? [{ item_key: "target", condition: "缺少依据", reason: "没有可用原文" }] : [] },
    evidence: [{ id: "case:version", kind: "case", version: "version", payload: { title: "未登录返回401", content: { steps: [{ order: 7, action: "末尾关键验证😀" }], custom: [0, false, null] }, execution_notes: "保持未登录" } }] };
}
function setup() { return mount(CasePlanDialog, { props: { projectId: "project", candidateCaseIds: ["case"] } }); }
function button(wrapper: ReturnType<typeof setup>, text: string) { return wrapper.findAll("button").find(b => b.text() === text)!; }

describe("case planning", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getCasePlanningPolicy.mockResolvedValue(config());
    api.saveCasePlanningPolicy.mockResolvedValue({ ...config(), scope_key: "project", revision: 2 });
    api.createCasePlan.mockResolvedValue(plan());
    api.getCasePlan.mockResolvedValue(plan("blocked"));
  });

  it("submits the objective and existing information, then displays full fixed content", async () => {
    const wrapper = setup(); await flushPromises();
    await wrapper.findAll("textarea")[0].setValue("验证未登录访问");
    await wrapper.findAll("textarea")[1].setValue('{"order_id":"existing"}');
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(api.createCasePlan).toHaveBeenCalledWith("project", { objective: "验证未登录访问", candidate_case_ids: ["case"], runtime_context: { order_id: "existing" } });
    expect(wrapper.text()).toContain("计划已生成");
    expect(wrapper.text()).toContain("目标用例 · 未登录返回401");
    expect(wrapper.text()).toContain("末尾关键验证😀");
    expect(wrapper.text()).toContain("false");
    expect(wrapper.text()).toContain("测试尚未开始");
    wrapper.unmount();
  });

  it("rejects an array as run input and shows request errors", async () => {
    const wrapper = setup(); await flushPromises();
    await wrapper.findAll("textarea")[0].setValue("验证");
    await wrapper.findAll("textarea")[1].setValue("[]");
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(api.createCasePlan).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toContain("JSON 对象");
    await wrapper.findAll("textarea")[1].setValue("{}");
    api.createCasePlan.mockRejectedValue(new Error("规划服务暂时不可用"));
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("暂时不可用");
    expect(wrapper.text()).not.toContain("计划已生成");
    wrapper.unmount();
  });

  it("saves project policy and reopens a plan with unresolved gaps", async () => {
    const wrapper = setup(); await flushPromises();
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenCalledWith("project", config().policy);
    await wrapper.find('input[placeholder="输入当前项目的计划编号"]').setValue("plan");
    await button(wrapper, "查看已保存计划").trigger("click"); await flushPromises();
    expect(api.getCasePlan).toHaveBeenCalledWith("project", "plan");
    expect(wrapper.text()).toContain("仍有执行缺口");
    expect(wrapper.text()).toContain("缺少依据");
    wrapper.unmount();
  });
});
