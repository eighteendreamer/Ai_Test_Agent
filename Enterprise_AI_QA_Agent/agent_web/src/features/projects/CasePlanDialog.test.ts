// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CasePlanDialog from "./CasePlanDialog.vue";
import { HttpError } from "../../services/http";
import type { CasePlanningPolicy, ModeDescriptor } from "../../types";
import { NCheckbox, NSelect } from "naive-ui";
import { checkboxByTestId, checkboxByText, inputNumberByTestId, setCheckbox, setInputNumber } from "../../test-utils/naive";
import documentExpected from "../../../tests/fixtures/case-document-expected.actual.json";
import itemOperationActual from "../../../tests/fixtures/case-item-operation.actual.json";
import linkedSourceActual from "../../../tests/fixtures/case-linked-source.actual.json";

const api = vi.hoisted(() => ({ getCasePlanningPolicy: vi.fn(), saveCasePlanningPolicy: vi.fn(), createCasePlan: vi.fn(), submitCasePlanningTask: vi.fn(), submitRegressionPlanningTask: vi.fn(), getCasePlanningTaskByRequest: vi.fn(), getCasePlanningTask: vi.fn(), listCasePlanningTasks: vi.fn(), getCasePlan: vi.fn(), launchCasePlan: vi.fn(), getTestRun: vi.fn(), listBusinessEnvironments: vi.fn(), listBusinessEnvironmentSnapshots: vi.fn() }));
vi.mock("../../services/api", () => ({ api }));
const config = () => ({ scope_key: "global", revision: 1, policy: { max_rounds: 5, max_candidates: 20, max_items: 50, max_context_chars: 180000, timeout_seconds: 180 } });
function plan(status = "planned") {
  return { id: "plan", project_id: "project", status, summary: "按完整用例规划", warnings: [], rounds: [], config_snapshot: config(),
    decision: { items: [{ key: "target", kind: "target", case_id: "case", case_version_id: "version", reason: "验证拒绝", evidence_ids: ["case:version"], depends_on: [], input_bindings: {} }],
      unresolved: status === "blocked" ? [{ item_key: "target", condition: "缺少依据", reason: "没有可用原文" }] : [] },
    evidence: [{ id: "case:version", kind: "case", version: "version", payload: { title: "未登录返回401", content: { steps: [{ order: 7, action: "末尾关键验证😀" }], custom: [0, false, null] }, execution_notes: "保持未登录" } }] };
}
const modes: ModeDescriptor[] = ["api_testing", "code_review", "default"].map(key => ({ key, name: key,
  summary: "", description: "", category: "test", is_test_mode: key !== "default", case_driven_policy: key === "code_review" ? "exempt" : "required",
  default_agent_key: "", allowed_agent_keys: [], default_skill_keys: [], registered_tool_keys: [], harness_key: "", placeholder: false, tags: [] }));
async function expandPolicy(wrapper: any) {
  const header = wrapper.findAll(".n-collapse-item__header-main").find((node: any) => node.text().includes("规划设置"));
  const item = header?.element?.closest(".n-collapse-item");
  if (header && item && !item.classList.contains("n-collapse-item--active")) await header.trigger("click");
  await flushPromises();
  await new Promise(resolve => setTimeout(resolve, 0));
  await flushPromises();
}
async function setup(projectId = "project", candidateOnly = false) {
  const wrapper = mount(CasePlanDialog, { props: { projectId, candidateCaseIds: ["case"], modes, candidateOnly },
    global: { stubs: { teleport: true } } });
  await flushPromises();
  await expandPolicy(wrapper);
  return wrapper;
}
async function mountWith(props: Record<string, unknown>) {
  const wrapper = mount(CasePlanDialog, { props: { projectId: "project", candidateCaseIds: ["case"], modes, ...props },
    global: { stubs: { teleport: true } } });
  await flushPromises();
  await expandPolicy(wrapper);
  return wrapper;
}
function findSelect(wrapper: Awaited<ReturnType<typeof setup>>, value: string) {
  return wrapper.findAllComponents(NSelect).find(select =>
    (select.props("options") as Array<{ value: string }> | undefined)?.some(option => option.value === value));
}
async function chooseSelect(wrapper: Awaited<ReturnType<typeof setup>>, value: string) {
  const select = findSelect(wrapper, value);
  if (!select) throw new Error(`未找到包含 ${value} 的下拉项`);
  select.vm.$emit("update:value", value);
  await flushPromises();
}
async function savePolicy(wrapper: Awaited<ReturnType<typeof setup>>) {
  await button(wrapper, "保存项目规划设置").trigger("click");
  await flushPromises();
  await expandPolicy(wrapper);
}
function button(wrapper: Awaited<ReturnType<typeof setup>>, text: string) { return wrapper.findAll("button").find(b => b.text() === text)!; }
async function generate(wrapper: Awaited<ReturnType<typeof setup>>) {
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
    api.listBusinessEnvironments.mockResolvedValue({ items: [{ id: "environment", project_id: "project", request: { request_id: "register", environment_key: "staging" }, status: "active" }], offset: 0, limit: 50, has_more: false });
    api.listBusinessEnvironmentSnapshots.mockResolvedValue({ items: [{ id: "snapshot", project_id: "project", environment_id: "environment",
      configuration: { payload: { display_name: "Staging", base_url: "https://staging.example.test" }, revision: 3 }, sha256: "a".repeat(64),
      captured_at: "2026-10-21T00:00:00Z", deployment_status: "unknown" }], offset: 0, limit: 20, has_more: false });
    api.getCasePlan.mockResolvedValue(plan());
    api.launchCasePlan.mockResolvedValue({ run: { id: "run-1", status: "queued" }, items: [], attempts: [], results: [] });
  });

  it("saves evidence-only receipt correction as a project policy and can turn it off", async () => {
    const wrapper = await setup(); await flushPromises();
    const receipt = () => checkboxByText(wrapper, "回执错误时只读纠正一次")!;
    receipt().vm.$emit("update:checked", true);
    await flushPromises();
    expect(wrapper.text()).toContain("使用原预算和已保存证据，不重复测试操作");
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", expect.objectContaining({ receipt_correction_protocol: "case-receipt-correction-v1" }));
    receipt().vm.$emit("update:checked", false);
    await flushPromises();
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[api.saveCasePlanningPolicy.mock.calls.length - 1]?.[1]).not.toHaveProperty("receipt_correction_protocol");
  });

  it("saves explicit receipt timeout and retries, then clears them when correction is disabled", async () => {
    api.saveCasePlanningPolicy.mockImplementation(async (_, policy) => ({ ...config(), revision: 2, policy }));
    const wrapper = await setup(); await flushPromises();
    const receipt = () => checkboxByText(wrapper, "回执错误时只读纠正一次")!;
    receipt().vm.$emit("update:checked", true);
    await flushPromises();
    await setCheckbox(wrapper, "receipt-transport", true);
    await setInputNumber(wrapper, "receipt-timeout", 95);
    await setInputNumber(wrapper, "receipt-retries", 0);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", expect.objectContaining({
      receipt_correction_transport_options: { timeout_seconds: 95, max_retries: 0 } }));
    receipt().vm.$emit("update:checked", false);
    await flushPromises();
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1]).not.toHaveProperty("receipt_correction_transport_options");
  });

  it("submits the objective and existing information, then displays full fixed content", async () => {
    const wrapper = await setup(); await flushPromises();
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

  it("freezes the selected project snapshot id and hash into the planning request", async () => {
    const wrapper = await setup(); await flushPromises();
    await button(wrapper, "选择固定测试环境快照（可选）").trigger("click"); await flushPromises();
    expect(api.listBusinessEnvironments).toHaveBeenCalledWith("project", { status: "active", limit: 50, offset: 0 });
    await chooseSelect(wrapper, "environment");
    expect(api.listBusinessEnvironmentSnapshots).toHaveBeenCalledWith("project", "environment", 20, 0);
    await wrapper.get('input[type="radio"]').trigger("change"); await flushPromises();
    expect(wrapper.text()).toContain("规划请求将固定环境 environment、快照 snapshot");
    await wrapper.findAll("textarea")[0].setValue("验证 staging 上的订单查询");
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(api.submitCasePlanningTask).toHaveBeenCalledWith("project", expect.objectContaining({
      objective: "验证 staging 上的订单查询",
      environment: { environment_id: "environment", snapshot_id: "snapshot", expected_snapshot_sha256: "a".repeat(64) },
    }));
    expect(wrapper.text()).toContain("部署状态未知");
    wrapper.unmount();
  });

  it("keeps an empty candidate list so the server performs project-scoped automatic recall", async () => {
    const wrapper = await mountWith({ candidateCaseIds: [] });
    await flushPromises();
    await wrapper.findAll("textarea")[0].setValue("验证订单详情");
    await button(wrapper, "生成执行计划").trigger("click"); await flushPromises();
    expect(api.submitCasePlanningTask).toHaveBeenCalledWith("project", expect.objectContaining({
      objective: "验证订单详情", candidate_case_ids: [], runtime_context: {},
    }));
    wrapper.unmount();
  });

  it("lets the operator review and select a candidate plan without launching another Run", async () => {
    const wrapper = await setup("project", true);
    await generate(wrapper);

    expect(wrapper.text()).toContain("候选计划编号：plan");
    expect(wrapper.text()).toContain("完整固定用例");
    expect(wrapper.text()).not.toContain("启动测试运行");
    await button(wrapper, "将此计划用于当前 Run 修订").trigger("click");
    await flushPromises();

    expect(wrapper.emitted("candidate")?.[0]?.[0]).toMatchObject({ id: "plan", project_id: "project" });
    expect(api.launchCasePlan).not.toHaveBeenCalled();
  });

  it("rejects an array as run input and shows request errors", async () => {
    const wrapper = await setup(); await flushPromises();
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
    const wrapper = await setup(); await flushPromises();
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenCalledWith("project", config().policy);
    await wrapper.find('input[placeholder="输入当前项目的计划编号"]').setValue("plan");
    await button(wrapper, "查看已保存计划").trigger("click"); await flushPromises();
    expect(api.getCasePlan).toHaveBeenCalledWith("project", "plan");
    expect(wrapper.text()).toContain("仍有执行缺口");
    expect(wrapper.text()).toContain("缺少依据");
    wrapper.unmount();
  });

  it("does not present a no-match model summary as an execution result", async () => {
    api.getCasePlan.mockResolvedValue({
      ...plan("no_match"),
      summary: "前置用例已创建订单，实际响应HTTP 201。",
      decision: { items: [], unresolved: [{ condition: "缺少目标用例", reason: "没有匹配的正式目标。" }] },
    });
    const wrapper = await setup(); await flushPromises();
    await wrapper.find('input[placeholder="输入当前项目的计划编号"]').setValue("plan");
    await button(wrapper, "查看已保存计划").trigger("click"); await flushPromises();
    expect(wrapper.text()).toContain("未找到符合本次目标的正式用例；本次未创建测试运行。");
    expect(wrapper.text()).not.toContain("HTTP 201");
    expect(wrapper.text()).toContain("缺少目标用例");
    wrapper.unmount();
  });

  it("saves action ownership for new plans and can disable it without altering other policy", async () => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "action-binding", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
    });
    await setCheckbox(wrapper, "action-binding", false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", config().policy);
    wrapper.unmount();
  });

  it("freezes opt-in action admission and disables it when ownership is removed", async () => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "action-admission", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1", action_admission_protocol: "case-action-admission-v1",
    });
    await setCheckbox(wrapper, "action-binding", false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", config().policy);
    wrapper.unmount();
  });

  it("enables operation recovery with its prerequisites and removes it with admission", async () => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
      ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
    }));
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "operation-recovery", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
    });
    await setCheckbox(wrapper, "action-admission", false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
    });
    wrapper.unmount();
  });

  it("freezes preparation scopes with prerequisites and removes them when recovery is disabled", async () => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
      ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
    }));
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "preparation-scopes", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
      preparation_scope_protocol: "preparation-scope-v1",
    });
    await setCheckbox(wrapper, "operation-recovery", false);
    await savePolicy(wrapper);
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
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "effect-evidence", true);
    await setInputNumber(wrapper, "effect-evidence-budget", 4096);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
      ...config().policy, action_binding_protocol: "case-action-binding-v1",
      action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
      preparation_scope_protocol: "preparation-scope-v1", effect_evidence_protocol: "case-effect-evidence-v1", effect_evidence_max_bytes: 4096,
    });
    await setCheckbox(wrapper, "preparation-scopes", false);
    await savePolicy(wrapper);
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
      const wrapper = await setup(); await flushPromises();
      await setCheckbox(wrapper, "effect-coverage", true);
      await setInputNumber(wrapper, "effect-coverage-budget", 8192);
      await savePolicy(wrapper);
      expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
        ...config().policy, action_binding_protocol: "case-action-binding-v1",
        action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
        preparation_scope_protocol: "preparation-scope-v1", effect_evidence_protocol: "case-effect-evidence-v1",
        effect_coverage_protocol: "case-effect-coverage-v1", effect_coverage_max_bytes: 8192,
      });
      await setCheckbox(wrapper, prerequisite, false);
      await savePolicy(wrapper);
      const calls = api.saveCasePlanningPolicy.mock.calls;
      const policy = calls[calls.length - 1][1];
      expect(policy).not.toHaveProperty("effect_coverage_protocol");
      expect(policy).not.toHaveProperty("effect_coverage_max_bytes");
      expect(inputNumberByTestId(wrapper, "effect-coverage-budget")).toBeUndefined();
      wrapper.unmount();
    },
  );

  it.each(["action-binding", "action-admission", "operation-recovery", "preparation-scopes", "effect-evidence", "effect-coverage", "planning-stages"])(
    "saves staged planning and removes it when %s is disabled", async (prerequisite) => {
      api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({
        ...config(), scope_key: projectId, revision: 2, policy: { ...policy },
      }));
      const wrapper = await setup(); await flushPromises();
      await setCheckbox(wrapper, "planning-stages", true);
      await savePolicy(wrapper);
      expect(api.saveCasePlanningPolicy).toHaveBeenLastCalledWith("project", {
        ...config().policy, action_binding_protocol: "case-action-binding-v1",
        action_admission_protocol: "case-action-admission-v1", operation_protocol: "case-operation-v1",
        preparation_scope_protocol: "preparation-scope-v1", effect_evidence_protocol: "case-effect-evidence-v1",
        effect_coverage_protocol: "case-effect-coverage-v1", planning_stage_protocol: "case-planning-staged-evidence-v1",
      });
      await setCheckbox(wrapper, prerequisite, false);
      await savePolicy(wrapper);
      const calls = api.saveCasePlanningPolicy.mock.calls;
      expect(calls[calls.length - 1][1]).not.toHaveProperty("planning_stage_protocol");
      wrapper.unmount();
    },
  );

  it("launches the saved identity with any test mode and opens the returned run", async () => {
    const wrapper = await setup(); await generate(wrapper);
    expect((findSelect(wrapper, "code_review")!.props("options") as Array<{ label: string }>).map(option => option.label)).toEqual(["api_testing", "code_review"]);
    await chooseSelect(wrapper, "code_review");
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
    const wrapper = await setup(); await generate(wrapper);
    await chooseSelect(wrapper, "code_review");
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    const payload = { ...api.launchCasePlan.mock.calls[0][2] };
    expect(findSelect(wrapper, "code_review")!.props("disabled")).toBe(true);
    expect(wrapper.text()).toContain("启动结果尚未确认");
    wrapper.unmount();
    api.getCasePlan.mockResolvedValue(plan());
    const reopened = await setup(); await flushPromises();
    await reopened.get('input[placeholder="输入当前项目的计划编号"]').setValue("plan");
    await button(reopened, "查看已保存计划").trigger("click"); await flushPromises();
    await button(reopened, "重试启动请求").trigger("click"); await flushPromises();
    expect(api.launchCasePlan.mock.calls[1][2]).toEqual(payload);
    reopened.unmount();
    api.getCasePlan.mockResolvedValue({ ...plan(), project_id: "other" });
    const other = await setup("other"); await generate(other);
    expect(other.text()).not.toContain(payload.creation_key);
    expect(button(other, "启动测试运行").exists()).toBe(true);
    other.unmount();
  });

  it("unlocks options after a definite rejection and preserves request in memory if storage fails", async () => {
    api.launchCasePlan.mockRejectedValueOnce(new HttpError("会话不属于当前项目", 409));
    const wrapper = await setup(); await generate(wrapper);
    await button(wrapper, "启动测试运行").trigger("click"); await flushPromises();
    const rejected = api.launchCasePlan.mock.calls[0][2].creation_key;
    expect(findSelect(wrapper, "code_review")!.props("disabled")).toBe(false);
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
    const wrapper = await setup(); await generate(wrapper);
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
    const w = await mountWith({ candidateCaseIds: [], regression: selection });
    await flushPromises();
    expect(w.findAllComponents(NCheckbox).length).toBeGreaterThan(0);
    expect(w.findAllComponents(NCheckbox)[0].props("disabled")).toBe(false);
    expect(api.submitRegressionPlanningTask).not.toHaveBeenCalled();
    w.findAllComponents(NCheckbox)[0].vm.$emit("update:checked", true);
    await flushPromises();
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
    const w = await mountWith({ candidateCaseIds: [], regression: selection });
    await flushPromises();
    await w.findAllComponents(NCheckbox)[0].vm.$emit("update:checked", true);
    await flushPromises();
    await button(w, "提交回归规划").trigger("click"); await flushPromises();
    expect(api.submitRegressionPlanningTask.mock.calls[0][1].version_overrides).toEqual({ case: "new-version" });
    expect(w.findAllComponents(NCheckbox)[0].props("disabled")).toBe(true);
    expect(findSelect(w, "code_review")!.props("value")).toBe("code_review");
    expect(findSelect(w, "code_review")!.props("disabled")).toBe(true);
    await button(w, "启动测试运行").trigger("click"); await flushPromises();
    expect(api.launchCasePlan.mock.calls[0][2]).toMatchObject({ mode_key: "code_review", session_id: "bound-session" });
    expect(w.text()).toContain("回归来源：parent"); w.unmount();
  });
  it("saves a consistent formal mixed-effects policy and preserves old settings when disabled", async () => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "formal-effects", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      preparation_scope_protocol: "preparation-scope-v2", effect_evidence_protocol: "case-effect-evidence-v2",
      effect_coverage_protocol: "case-effect-coverage-v2", planning_stage_protocol: "case-planning-staged-evidence-v2",
      operation_protocol: "case-operation-v1", timeout_seconds: 180,
    });
    wrapper.unmount();
  });
  it.each(["formal-effects", "planning-stages", "effect-evidence"])("freezes document expectations and clears them when %s is disabled", async (name) => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "document-expected", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      effect_expected_source_protocol: "case-effect-document-expected-v1",
      planning_stage_protocol: "case-planning-staged-evidence-v2", timeout_seconds: 180,
    });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].effect_expected_source_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it("renders the actual fixed document reference with the original full case", async () => {
    expect(documentExpected.RustFS_original_verified).toBe(true);
    api.getCasePlan.mockResolvedValue(documentExpected.plan);
    api.submitCasePlanningTask.mockImplementation(async (projectId, payload) => ({ id: "actual-plan-task", project_id: projectId,
      creation_key: payload.creation_key, status: "completed", plan_id: documentExpected.plan.id, round_count: 4, message: "完成" }));
    const wrapper = await setup(documentExpected.plan.project_id); await generate(wrapper);
    expect(wrapper.text()).toContain("DELETE同一路径返回200及刚删除的订单JSON。");
    expect(wrapper.text()).toContain("LAST-DEFAULT-COVERAGE-STEP");
    expect(wrapper.text()).toContain("产生与清理证据声明");
    expect(wrapper.text()).not.toContain("证据契约");
    wrapper.unmount();
  });
  it.each(["item-operation-selections", "source-identity", "operation-prefetch", "document-expected", "formal-effects", "operation-recovery"])("saves all-item operation selection and retains valid policy after disabling %s", async (name) => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({ scope_key: projectId, revision: 2, policy: { ...policy } }));
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "item-operation-selections", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      operation_prefetch_protocol: "case-planning-item-operation-prefetch-v2",
      source_identity_protocol: "case-planning-source-full-hash-v1", timeout_seconds: 180,
    });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].operation_prefetch_protocol).not.toBe("case-planning-item-operation-prefetch-v2");
    if (name === "item-operation-selections" || name === "source-identity") {
      expect(api.saveCasePlanningPolicy.mock.calls[1][1].operation_prefetch_protocol).toBe("case-planning-declared-operation-prefetch-v1");
    }
    wrapper.unmount();
  });
  it.each(["linked-sources", "source-identity", "document-expected", "formal-effects", "operation-recovery"])("saves explicit linked sources and clears them with %s", async (name) => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({ scope_key: projectId, revision: 2, policy: { ...policy } }));
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "linked-sources", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({ linked_source_protocol: "case-planning-linked-sources-v2",
      source_identity_protocol: "case-planning-source-full-hash-v1", timeout_seconds: 180 });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].linked_source_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it("renders actual formal-only operation selection without changing fixed content", async () => {
    const actual = itemOperationActual.plan;
    api.getCasePlan.mockResolvedValue(actual);
    api.submitCasePlanningTask.mockImplementation(async (projectId, payload) => ({ id: "operation-task", project_id: projectId,
      creation_key: payload.creation_key, status: "completed", plan_id: actual.id, round_count: 3, message: "完成" }));
    const wrapper = await setup(actual.project_id); await generate(wrapper);
    expect(wrapper.text()).toContain("逻辑阶段选定的工具接口");
    expect(wrapper.text()).toContain("api-test-runner");
    expect(wrapper.text()).toContain("execute_task");
    expect(wrapper.text()).toContain("LAST-DEFAULT-COVERAGE-STEP");
    expect(wrapper.text()).not.toContain("证据契约");
    expect(actual.decision.items.every(item => item.kind === "target")).toBe(true);
    wrapper.unmount();
  });
  it.each(["evidence-references", "source-identity", "document-expected", "formal-effects", "operation-recovery"])("saves evidence reference policy and clears it with %s", async (name) => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({ scope_key: projectId, revision: 2, policy: { ...policy } }));
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "evidence-references", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({ evidence_reference_protocol: "case-planning-evidence-references-v1",
      source_identity_protocol: "case-planning-source-full-hash-v1", timeout_seconds: 180 });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].evidence_reference_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it.each(["evidence-references", "source-identity", "document-expected", "formal-effects"])("freezes separate evidence tasks and clears them with %s", async (name) => {
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({ scope_key: projectId, revision: 2, policy: { ...policy } }));
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "effect-tasks", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({ planning_stage_protocol: "case-planning-staged-evidence-v3",
      evidence_reference_protocol: "case-planning-evidence-references-v1", timeout_seconds: 180, max_rounds: 5 });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].planning_stage_protocol).not.toBe("case-planning-staged-evidence-v3");
    wrapper.unmount();
  });
  it("preserves the configured v2 references when saving and enabling separate tasks", async () => {
    const policy: CasePlanningPolicy = { ...config().policy,
      action_binding_protocol: "case-action-binding-v1", action_admission_protocol: "case-action-admission-v1",
      operation_protocol: "case-operation-v1", preparation_scope_protocol: "preparation-scope-v2",
      effect_evidence_protocol: "case-effect-evidence-v2", effect_coverage_protocol: "case-effect-coverage-v2",
      planning_stage_protocol: "case-planning-staged-evidence-v2", effect_expected_source_protocol: "case-effect-document-expected-v1",
      source_identity_protocol: "case-planning-source-full-hash-v1", evidence_reference_protocol: "case-planning-evidence-references-v2" };
    api.getCasePlanningPolicy.mockResolvedValue({ ...config(), policy });
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, saved) => ({ scope_key: projectId, revision: 2, policy: { ...saved } }));
    const wrapper = await setup(); await flushPromises();
    expect(checkboxByTestId(wrapper, "evidence-references")!.props("checked")).toBe(true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1].evidence_reference_protocol).toBe("case-planning-evidence-references-v2");
    await setCheckbox(wrapper, "effect-tasks", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1]).toMatchObject({
      evidence_reference_protocol: "case-planning-evidence-references-v2", planning_stage_protocol: "case-planning-staged-evidence-v3" });
    wrapper.unmount();
  });
  it("keeps the actual old document policy until the user enables scene reads", async () => {
    const actual = linkedSourceActual.plan.config_snapshot;
    api.getCasePlanningPolicy.mockResolvedValue(actual);
    api.saveCasePlanningPolicy.mockImplementation(async (projectId, policy) => ({ scope_key: projectId, revision: 2, policy: { ...policy } }));
    const wrapper = await setup(); await flushPromises();
    expect(wrapper.text()).toContain("当前配置只读取关联文档");
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1].linked_source_protocol).toBe("case-planning-linked-documents-v1");
    await setCheckbox(wrapper, "linked-sources", false);
    await setCheckbox(wrapper, "linked-sources", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].linked_source_protocol).toBe("case-planning-linked-sources-v2");
    expect(wrapper.text()).not.toContain("当前配置只读取关联文档");
    wrapper.unmount();
  });
  it("renders actual authored source read before the first model with complete fixed content", async () => {
    const actual = linkedSourceActual.plan;
    api.getCasePlan.mockResolvedValue(actual);
    api.submitCasePlanningTask.mockImplementation(async (projectId, payload) => ({ id: "linked-task", project_id: projectId,
      creation_key: payload.creation_key, status: "completed", plan_id: actual.id, round_count: 2, message: "完成" }));
    const wrapper = await setup(actual.project_id); await generate(wrapper);
    expect(wrapper.text()).toContain("用例关联来源的固定读取记录");
    expect(wrapper.text()).toContain("authored_snapshot");
    expect(wrapper.text()).toContain("LAST-DEFAULT-COVERAGE-STEP");
    expect(wrapper.text()).not.toContain("证据契约");
    expect(actual.rounds).toHaveLength(2);
    wrapper.unmount();
  });
  it.each(["document-expected", "formal-effects", "operation-recovery"])("saves unified references independently and clears them with %s", async (name) => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "source-identity", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      source_identity_protocol: "case-planning-source-full-hash-v1",
      effect_expected_source_protocol: "case-effect-document-expected-v1", timeout_seconds: 180,
    });
    expect(api.saveCasePlanningPolicy.mock.calls[0][1].operation_prefetch_protocol).toBeUndefined();
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].source_identity_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it.each(["scene-queries", "source-identity", "document-expected", "formal-effects", "operation-recovery"])("saves scene queries and clears them with %s", async (name) => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "scene-queries", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      scene_query_protocol: "case-planning-scene-query-v1", source_identity_protocol: "case-planning-source-full-hash-v1",
      planning_stage_protocol: "case-planning-staged-evidence-v2", timeout_seconds: 180,
    });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].scene_query_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it.each(["scene-receipts", "scene-queries", "source-identity", "document-expected", "formal-effects", "operation-recovery"])("saves scene receipts and clears them with %s", async (name) => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "scene-receipts", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      scene_receipt_protocol: "case-scene-receipt-v1", scene_query_protocol: "case-planning-scene-query-v1",
      source_identity_protocol: "case-planning-source-full-hash-v1",
    });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].scene_receipt_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it.each(["document-expected", "formal-effects", "operation-recovery"])("saves declared operation reading and clears it with %s", async (name) => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "operation-prefetch", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      operation_prefetch_protocol: "case-planning-declared-operation-prefetch-v1",
      effect_expected_source_protocol: "case-effect-document-expected-v1", timeout_seconds: 180,
    });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].operation_prefetch_protocol).toBeUndefined();
    wrapper.unmount();
  });
  it.each(["action-binding", "action-admission", "operation-recovery", "preparation-scopes", "effect-evidence", "effect-coverage", "planning-stages"])("removes mixed-effect policy consistently when %s is disabled", async (name) => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "formal-effects", true);
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    const saved = api.saveCasePlanningPolicy.mock.calls[0][1];
    expect(Object.values(saved).some(value => typeof value === "string" && value.endsWith("-v2"))).toBe(false);
    wrapper.unmount();
  });

  it.each(["consumption-phase-policy", "formal-effects", "operation-recovery"])("saves consumption closure and clears it with %s", async (name) => {
    const wrapper = await setup(); await flushPromises();
    await setCheckbox(wrapper, "consumption-phase-policy", true);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[0][1]).toMatchObject({
      consumption_protocol: "case-consumption-phase-v1", effect_coverage_protocol: "case-effect-coverage-v2",
      action_admission_protocol: "case-action-admission-v1", timeout_seconds: 180,
    });
    await setCheckbox(wrapper, name, false);
    await savePolicy(wrapper);
    expect(api.saveCasePlanningPolicy.mock.calls[1][1].consumption_protocol).toBeUndefined();
    wrapper.unmount();
  });

});
