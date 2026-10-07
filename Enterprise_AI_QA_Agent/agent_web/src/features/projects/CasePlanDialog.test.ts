// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CasePlanDialog from "./CasePlanDialog.vue";
import { HttpError } from "../../services/http";
import type { ModeDescriptor } from "../../types";
import documentExpected from "../../../tests/fixtures/case-document-expected.actual.json";

const api = vi.hoisted(() => ({ getCasePlanningPolicy: vi.fn(), saveCasePlanningPolicy: vi.fn(), createCasePlan: vi.fn(), submitCasePlanningTask: vi.fn(), submitRegressionPlanningTask: vi.fn(), getCasePlanningTaskByRequest: vi.fn(), getCasePlanningTask: vi.fn(), listCasePlanningTasks: vi.fn(), getCasePlan: vi.fn(), launchCasePlan: vi.fn(), getTestRun: vi.fn() }));
vi.mock("../../services/api", () => ({ api }));
const config = () => ({ scope_key: "global", revision: 1, policy: { max_rounds: 5, max_candidates: 20, max_items: 50, max_context_chars: 180000, timeout_seconds: 180 } });
function plan(status = "planned") {
  return { id: "plan", project_id: "project", status, summary: "按完整用例规划", warnings: [], config_snapshot: config(),
    decision: { items: [{ key: "target", kind: "target", case_id: "case", case_version_id: "version", reason: "验证拒绝", evidence_ids: ["case:version"], depends_on: [], input_bindings: {} }],
      unresolved: status === "blocked" ? [{ item_key: "target", condition: "缺少依据", reason: "没有可用原文" }] : [] },
    evidence: [{ id: "case:version", kind: "case", version: "version", payload: { title: "未登录返回401", content: { steps: [{ order: 7, action: "末尾关键验证😀" }], custom: [0, false, null] }, execution_notes: "保持未登录" } }] };
}
const modes: ModeDescriptor[] = ["api_testing", "code_review", "default"].map(key => ({ key, name: key,
  summary: "", description: "", category: "test", is_test_mode: key !== "default", case_driven_policy: key === "code_review" ? "exempt" : "required",
  default_agent_key: "", allowed_agent_keys: [], default_skill_keys: [], registered_tool_keys: [], harness_key: "", placeholder: false, tags: [] }));
function setup(projectId = "project") { return mount(CasePlanDialog, { props: { projectId, candidateCaseIds: ["case"], modes } }); }
function button(wrapper: ReturnType<typeof setup>, text: string) { return wrapper.findAll("button").find(b => b.text() === text)!; }
async function generate(wrapper: ReturnType<typeof setup>) {
  await flushPromises(); await wrapper.findAll("textarea")[0].setValue("验证");
  await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
}

describe("case planning", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    api.getCasePlanningPolicy.mockResolvedValue(config());
    api.saveCasePlanningPolicy.mockResolvedValue({ ...config(), scope_key: "project", revision: 2 });
    api.submitCasePlanningTask.mockImplementation(async (projectId, payload) => ({ id: "task", project_id: projectId, creation_key: payload.creation_key, status: "completed", plan_id: "plan", round_count: 1, message: "完成" }));
    api.getCasePlanningTaskByRequest.mockRejectedValue(new HttpError("not found", 404));
    api.listCasePlanningTasks.mockResolvedValue({ items: [] });
    api.getCasePlan.mockResolvedValue(plan());
    api.launchCasePlan.mockResolvedValue({ run: { id: "run-1", status: "queued" }, items: [], attempts: [], results: [] });
  });

  it("submits the objective and existing information, then displays full fixed content", async () => {
    const wrapper = setup(); await flushPromises();
    await wrapper.findAll("textarea")[0].setValue("验证未登录访问");
    await wrapper.findAll("textarea")[1].setValue('{"order_id":"existing"}');
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(api.submitCasePlanningTask).toHaveBeenCalledWith("project", { creation_key: expect.any(String), objective: "验证未登录访问", candidate_case_ids: ["case"], runtime_context: { order_id: "existing" } });
    expect(wrapper.text()).toContain("计划已生成");
    expect(wrapper.text()).toContain("目标用例 · 未登录返回401");
    expect(wrapper.text()).toContain("末尾关键验证😀");
    expect(wrapper.text()).toContain("false");
    expect(wrapper.text()).toContain("生成计划只保存执行依据");
    wrapper.unmount();
  });

  it("rejects an array as run input and shows request errors", async () => {
    const wrapper = setup(); await flushPromises();
    await wrapper.findAll("textarea")[0].setValue("验证");
    await wrapper.findAll("textarea")[1].setValue("[]");
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(api.submitCasePlanningTask).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toContain("JSON 对象");
    await wrapper.findAll("textarea")[1].setValue("{}");
    api.submitCasePlanningTask.mockRejectedValue(new Error("规划服务暂时不可用"));
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("暂时不可用");
    expect(wrapper.text()).not.toContain("计划已生成");
    wrapper.unmount();
  });

  it("saves project policy and reopens a plan with unresolved gaps", async () => {
    api.getCasePlan.mockResolvedValue(plan("blocked"));
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

  it("saves action ownership for new plans and can disable it without altering other policy", async () => {
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="action-binding"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
    });
    await wrapper.get('[data-testid="action-binding"]').setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", config().policy);
    wrapper.unmount();
  });

  it("freezes opt-in action admission and disables it when ownership is removed", async () => {
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="action-admission"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1", action_admission_protocol: "case-action-admission-v1",
    });
    await wrapper.get('[data-testid="action-binding"]').setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", config().policy);
    wrapper.unmount();
  });

  it("enables operation recovery with its prerequisites and removes it with admission", async () => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
      ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
    }));
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="operation-recovery"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
    });
    await wrapper.get('[data-testid="action-admission"]').setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
    });
    wrapper.unmount();
  });

  it("freezes preparation scopes with prerequisites and removes them when recovery is disabled", async () => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
      ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
    }));
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="preparation-scopes"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
      preparation_scope_protocol: "preparation-scope-v1",
    });
    await wrapper.get('[data-testid="operation-recovery"]').setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1",
    });
    wrapper.unmount();
  });

  it("saves effect evidence with frozen scope and removes it when scope is disabled", async () => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
      ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
    }));
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="effect-evidence"]').setValue(true);
    await wrapper.get('[data-testid="effect-evidence-budget"]').setValue(4096);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
      preparation_scope_protocol: "preparation-scope-v1", effect_evidence_protocol: "case-effect-evidence-v1", effect_evidence_max_bytes: 4096,
    });
    await wrapper.get('[data-testid="preparation-scopes"]').setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
    });
    wrapper.unmount();
  });

  it.each(["action-binding", "action-admission", "operation-recovery", "preparation-scopes", "effect-evidence"])(
    "removes coverage and its budget when prerequisite %s is disabled", async (prerequisite) => {
      api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
        ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
      }));
      const wrapper = setup(); await flushPromises();
      await wrapper.get('[data-testid="effect-coverage"]').setValue(true);
      await wrapper.get('[data-testid="effect-coverage-budget"]').setValue(8192);
      await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
      expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
        ...config().policy, action_binding_protocol: "case-action-binding-v1",
        action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
        preparation_scope_protocol: "preparation-scope-v1", effect_evidence_protocol: "case-effect-evidence-v1",
        effect_coverage_protocol: "case-effect-coverage-v1", effect_coverage_max_bytes: 8192,
      });
      await wrapper.get(`[data-testid="${prerequisite}"]`).setValue(false);
      await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
      const calls = api.saveCasePlanningPolicy.mock.calls;
      const policy = calls[calls.length - 1][1];
      expect(policy).not.toHaveProperty("effect_coverage_protocol");
      expect(policy).not.toHaveProperty("effect_coverage_max_bytes");
      expect(wrapper.find('[data-testid="effect-coverage-budget"]').exists()).toBe(false);
      wrapper.unmount();
    },
  );

  it.each(["action-binding", "action-admission", "operation-recovery", "preparation-scopes", "effect-evidence", "effect-coverage", "planning-stages"])(
    "saves staged planning and removes it when %s is disabled", async (prerequisite) => {
      api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
        ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
      }));
      const wrapper = setup(); await flushPromises();
      await wrapper.get('[data-testid="planning-stages"]').setValue(true);
      await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
      expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
        ...config().policy, action_binding_protocol: "case-action-binding-v1",
        action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
        preparation_scope_protocol: "preparation-scope-v1", effect_evidence_protocol: "case-effect-evidence-v1",
        effect_coverage_protocol: "case-effect-coverage-v1", planning_stage_protocol: "case-planning-staged-evidence-v1",
      });
      await wrapper.get(`[data-testid="${prerequisite}"]`).setValue(false);
      await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
      const calls = api.saveCasePlanningPolicy.mock.calls;
      expect(calls[calls.length - 1][1]).not.toHaveProperty("planning_stage_protocol");
      wrapper.unmount();
    },
  );

  it("launches the saved identity with any test mode and opens the returned run", async () => {
    const wrapper = setup(); await generate(wrapper);
    expect(wrapper.findAll('select option').map(option => option.text())).toEqual(["api_testing", "code_review"]);
    await wrapper.get('select').setValue("code_review");
    await wrapper.get('input[placeholder="会话必须属于当前项目"]').setValue(" same-project-session ");
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    const payload = api.launchCasePlan.mock.calls[0][2];
    expect(api.launchCasePlan).toHaveBeenCalledWith("project", "plan", { creation_key: expect.any(String), mode_key: "code_review", session_id: "same-project-session" });
    expect(Object.keys(payload).sort()).toEqual(["creation_key", "mode_key", "session_id"]);
    expect(wrapper.text()).toContain("已保存运行：run-1");
    expect(wrapper.text()).toContain("保存成功不代表测试通过");
    expect(wrapper.emitted("started")?.[0][0]).toEqual(expect.objectContaining({ run: { id: "run-1", status: "queued" } }));
    await button(wrapper, "另建一次运行").trigger("click");
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    expect(api.launchCasePlan.mock.calls[1][2].creation_key).not.toBe(payload.creation_key);
    wrapper.unmount();
  });

  it("keeps the exact uncertain request through closing and reopening, isolated by project", async () => {
    api.launchCasePlan.mockRejectedValueOnce(new Error("连接在提交后断开"));
    const wrapper = setup(); await generate(wrapper);
    await wrapper.get('select').setValue("code_review");
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    const payload = { ...api.launchCasePlan.mock.calls[0][2] };
    expect(wrapper.get('select').attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("启动结果尚未确认");
    wrapper.unmount();
    api.getCasePlan.mockResolvedValue(plan());
    const reopened = setup(); await flushPromises();
    await reopened.get('input[placeholder="输入当前项目的计划编号"]').setValue("plan");
    await button(reopened, "查看已保存计划").trigger("click"); await flushPromises();
    await button(reopened, "重试启动请求").trigger("click"); await flushPromises();
    expect(api.launchCasePlan.mock.calls[1][2]).toEqual(payload);
    reopened.unmount();
    api.getCasePlan.mockResolvedValue({ ...plan(), project_id: "other" });
    const other = setup("other"); await generate(other);
    expect(other.text()).not.toContain(payload.creation_key);
    expect(button(other, "启动测试运行").exists()).toBe(true);
    other.unmount();
  });

  it("unlocks options after a definite rejection and preserves request in memory if storage fails", async () => {
    api.launchCasePlan.mockRejectedValueOnce(new HttpError("会话不属于当前项目", 409));
    const wrapper = setup(); await generate(wrapper);
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    const rejected = api.launchCasePlan.mock.calls[0][2].creation_key;
    expect(wrapper.get('select').attributes("disabled")).toBeUndefined();
    const failure = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage unavailable"); });
    api.launchCasePlan.mockRejectedValueOnce(new Error("连接断开"));
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    const uncertain = api.launchCasePlan.mock.calls[1][2].creation_key;
    expect(uncertain).not.toBe(rejected);
    expect(wrapper.text()).toContain("浏览器未能保存启动请求");
    await button(wrapper, "重试启动请求").trigger("click"); await flushPromises();
    expect(api.launchCasePlan.mock.calls[2][2].creation_key).toBe(uncertain);
    failure.mockRestore(); wrapper.unmount();
  });

  it("reopens the saved run through the existing API and rejects a mismatched cached identity", async () => {
    const wrapper = setup(); await generate(wrapper);
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    api.getTestRun.mockResolvedValue({ run: { id: "run-1", project_id: "foreign", plan_snapshot: { plan: { id: "plan" } } } });
    await button(wrapper, "查看运行").trigger("click"); await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("不属于当前项目和计划");
    expect(wrapper.emitted("started")).toHaveLength(1);
    const detail = { run: { id: "run-1", project_id: "project", plan_snapshot: { plan: { id: "plan" } }, status: "completed" } };
    api.getTestRun.mockResolvedValue(detail);
    await button(wrapper, "查看运行").trigger("click"); await flushPromises();
    expect(api.getTestRun).toHaveBeenCalledWith("run-1");
    expect(wrapper.emitted("started")?.[1][0]).toEqual(detail);
    wrapper.unmount();
  });

  it("allows original version selection after a refreshed submission lookup returns missing, keeping its identity", async () => {
    const selection = { parentRunId: "parent", resultIds: ["failure"], versionOptions: [
      { caseId: "case", title: "变更后查询", originalVersionId: "old-version", activeVersionId: "new-version" }] };
    window.sessionStorage.setItem('case-planning-task:project:regression:parent:["failure"]',
      JSON.stringify({ creationKey: "unconfirmed-request", taskId: "" }));
    api.getCasePlanningTaskByRequest.mockRejectedValue(new HttpError("当前项目没有该规划提交", 404));
    api.submitRegressionPlanningTask.mockImplementation(async (_, payload) => ({ id: "task", project_id: "project",
      creation_key: payload.creation_key, status: "queued", plan_id: null, round_count: 0, message: "排队" }));
    const w = mount(CasePlanDialog, { props: { projectId: "project", candidateCaseIds: [], modes, regression: selection } });
    await flushPromises();
    expect(w.find('input[type="checkbox"]').exists()).toBe(true);
    expect(w.get('input[type="checkbox"]').attributes("disabled")).toBeUndefined();
    expect(api.submitRegressionPlanningTask).not.toHaveBeenCalled();
    await w.get('input[type="checkbox"]').setValue(true);
    await button(w, "重试规划提交").trigger("click"); await flushPromises();
    expect(api.submitRegressionPlanningTask).toHaveBeenCalledWith("parent", {
      creation_key: "unconfirmed-request", result_ids: ["failure"], version_overrides: { case: "new-version" }, session_id: null });
    w.unmount();
  });

  it("chooses an enabled replacement and keeps the frozen regression mode/session on launch", async () => {
    const selection = { parentRunId: "parent", resultIds: ["failure"], versionOptions: [
      { caseId: "case", title: "变更后查询", originalVersionId: "old-version", activeVersionId: "new-version" }] };
    const fixed = { ...plan(), regression: { parent_run_id: "parent", mode_key: "code_review", session_id: "bound-session",
      source_results_by_case: { case: "failure" }, version_overrides: { case: "new-version" },
      submission: { result_ids: ["failure"], session_id: null } } };
    api.getCasePlan.mockResolvedValue(fixed);
    api.submitRegressionPlanningTask.mockImplementation(async (_, payload) => ({ id: "task", project_id: "project",
      creation_key: payload.creation_key, status: "completed", plan_id: "plan", round_count: 1, message: "完成" }));
    const w = mount(CasePlanDialog, { props: { projectId: "project", candidateCaseIds: [], modes, regression: selection } });
    await flushPromises();
    await w.get('input[type="checkbox"]').setValue(true);
    await button(w, "提交回归规划").trigger("click"); await flushPromises();
    expect(api.submitRegressionPlanningTask.mock.calls[0][1].version_overrides).toEqual({ case: "new-version" });
    expect(w.get('input[type="checkbox"]').attributes()).toHaveProperty("disabled");
    expect(w.get('select').element.value).toBe("code_review");
    expect(w.get('select').attributes()).toHaveProperty("disabled");
    await button(w, "启动测试运行").trigger("click"); await flushPromises();
    expect(api.launchCasePlan.mock.calls[0][2]).toMatchObject({ mode_key: "code_review", session_id: "bound-session" });
    expect(w.text()).toContain("回归来源：parent"); w.unmount();
  });
  it("saves a consistent formal mixed-effects policy and preserves old settings when disabled", async () => {
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="formal-effects"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      preparation_scope_protocol: "preparation-scope-v2", effect_evidence_protocol: "case-effect-evidence-v2",
      effect_coverage_protocol: "case-effect-coverage-v2", planning_stage_protocol: "case-planning-staged-evidence-v2",
      operation_protocol: "case-operation-v1", timeout_seconds: 180,
    });
    wrapper.unmount();
  });
  it.each(["formal-effects", "planning-stages", "effect-evidence"])("freezes document expectations and clears them when %s is disabled", async (name) => {
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="document-expected"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      effect_expected_source_protocol: "case-effect-document-expected-v1",
      planning_stage_protocol: "case-planning-staged-evidence-v2", timeout_seconds: 180,
    });
    await wrapper.get(`[data-testid="${name}"]`).setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].effect_expected_source_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it("renders the actual fixed document reference with the original full case", async () => {
    expect(documentExpected.RustFS_original_verified).toBe(true);
    api.getCasePlan.mockResolvedValue(documentExpected.plan);
    api.submitCasePlanningTask.mockImplementation(async (projectId, payload) => ({ id: "actual-plan-task", project_id: projectId,
      creation_key: payload.creation_key, status: "completed", plan_id: documentExpected.plan.id, round_count: 4, message: "完成" }));
    const wrapper = setup(documentExpected.plan.project_id); await generate(wrapper);
    expect(wrapper.text()).toContain("DELETE同一路径返回200及刚删除的订单JSON。");
    expect(wrapper.text()).toContain("LAST-DEFAULT-COVERAGE-STEP");
    expect(wrapper.text()).toContain("产生与清理证据声明");
    expect(wrapper.text()).not.toContain("证据契约");
    wrapper.unmount();
  });
  it.each(["document-expected", "formal-effects", "operation-recovery"])("saves unified references independently and clears them with %s", async (name) => {
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="source-identity"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      source_identity_protocol: "case-planning-source-full-hash-v1",
      effect_expected_source_protocol: "case-effect-document-expected-v1", timeout_seconds: 180,
    });
    expect(api.saveCasePlanningPolicy.mock.calls[0][1].operation_prefetch_protocol).toBeUndefined();
    await wrapper.get(`[data-testid="${name}"]`).setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].source_identity_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it.each(["document-expected", "formal-effects", "operation-recovery"])("saves declared operation reading and clears it with %s", async (name) => {
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="operation-prefetch"]').setValue(true);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      operation_prefetch_protocol: "case-planning-declared-operation-prefetch-v1",
      effect_expected_source_protocol: "case-effect-document-expected-v1", timeout_seconds: 180,
    });
    await wrapper.get(`[data-testid="${name}"]`).setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].operation_prefetch_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it.each(["action-binding", "action-admission", "operation-recovery", "preparation-scopes", "effect-evidence", "effect-coverage", "planning-stages"])("removes mixed-effect policy consistently when %s is disabled", async (name) => {
    const wrapper = setup(); await flushPromises();
    await wrapper.get('[data-testid="formal-effects"]').setValue(true);
    await wrapper.get(`[data-testid="${name}"]`).setValue(false);
    await button(wrapper, "保存项目规划设置").trigger("click"); await flushPromises();
    const saved = api.saveCasePlanningPolicy.mock.calls[0][1];
    expect(Object.values(saved).some(value => typeof value === "string" && value.endsWith("-v2"))).toBe(false);
    wrapper.unmount();
  });

});
