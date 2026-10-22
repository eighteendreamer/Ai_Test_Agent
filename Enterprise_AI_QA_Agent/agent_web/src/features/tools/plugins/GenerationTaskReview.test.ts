// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import report from "../../../../../docs/upgrade_2026-10-03/p1-alternate-document-recovery-current-20261022-01/generation-result.json";
import scope from "../../../../../docs/upgrade_2026-10-03/p1-alternate-document-recovery-current-20261022-01/fixed-scope.json";
import observedDraft from "../../../../../docs/upgrade_2026-10-03/p1-alternate-document-recovery-current-20261022-01/draft-version-0.json";
import CaseGenerationTask from "./CaseGenerationTask.vue";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../../types";

const generation = vi.hoisted(() => ({ readCaseGeneration: vi.fn(), readCaseGenerationScope: vi.fn() }));
vi.mock("../../../services/caseGeneration.api", () => generation);
const api = vi.hoisted(() => ({ getProjectOverview: vi.fn(), getTestCase: vi.fn(), listTestCaseVersions: vi.fn(),
  createTestCaseVersion: vi.fn(), submitTestCaseReview: vi.fn(), activateTestCase: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const draft = observedDraft as unknown as TestCaseVersionRecord;
const record: TestCaseRecord = { id: draft.case_id, project_id: scope.project_id, case_key: "order-query",
  title: "订单查询草稿", case_type: "json", mode_key: "api_testing", priority: "P1", lifecycle_status: "draft",
  active_version_id: null, latest_version: 2, created_at: draft.created_at, updated_at: draft.created_at };
const later: TestCaseVersionRecord = { ...draft, id: "later-draft", version: 2, content_hash: "f".repeat(64),
  content_json: { objective: "较新的草稿", steps: [] } };
const wrappers: ReturnType<typeof mount>[] = [];
function panel() {
  const wrapper = mount(CaseGenerationTask, { props: { projectId: scope.project_id, documentId: scope.document_id,
    groupId: report.group.id, taskId: report.modules[0]!.id } });
  wrappers.push(wrapper);
  return wrapper;
}
function button(wrapper: ReturnType<typeof mount>, text: string) {
  const found = wrapper.findAll("button").find(item => item.text() === text);
  expect(found, `Missing action: ${text}`).toBeDefined();
  return found!;
}
beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  generation.readCaseGeneration.mockResolvedValue(structuredClone(report));
  generation.readCaseGenerationScope.mockResolvedValue(structuredClone(scope));
  api.getProjectOverview.mockResolvedValue({ project: { id: scope.project_id, status: "active" } });
  api.getTestCase.mockResolvedValue(structuredClone(record));
  api.listTestCaseVersions.mockResolvedValue(structuredClone([later, draft]));
  api.createTestCaseVersion.mockImplementation(async (_id, payload) => ({ ...draft, ...payload, id: "manual-revision", version: 3 }));
});
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()));

it("opens the real generated version even when a later draft exists, then appends a correction", async () => {
  const wrapper = panel();
  await flushPromises();
  expect(wrapper.text()).toContain(`固定草稿 ${draft.case_id} · V1 · ${draft.id}`);
  await button(wrapper, `评审与修订此草稿 ${draft.id}`).trigger("click");
  await flushPromises();
  const dialog = wrapper.get('[role="dialog"][aria-label="测试用例内容"]');
  expect((dialog.get("select").element as HTMLSelectElement).value).toBe(draft.id);
  expect(dialog.text()).toContain("response.order.user_id");
  await button(wrapper, "编辑新版本").trigger("click");
  const correction = structuredClone(draft.content_json!);
  (correction.steps as Record<string, unknown>[]).forEach(step => delete step.evidence_field);
  correction["待确认事项"] = ["从项目契约确认响应字段后执行", "确认他人订单和不存在订单的测试数据"];
  await dialog.get("textarea.json-editor").setValue(JSON.stringify(correction));
  await button(wrapper, "保存新草稿版本").trigger("click");
  await flushPromises();
  expect(api.createTestCaseVersion).toHaveBeenCalledWith(draft.case_id, expect.objectContaining({
    content_json: correction, source_refs: draft.source_refs, model_key: draft.model_key,
    prompt_version: draft.prompt_version, skill_versions: draft.skill_versions,
    generation_metadata: { ...draft.generation_metadata, manual_edit: { source_version_id: draft.id } },
  }));
  expect((dialog.get("select").element as HTMLSelectElement).value).toBe("manual-revision");
  expect(draft.content_json!.steps).toEqual(observedDraft.content_json.steps);
  expect(api.submitTestCaseReview).not.toHaveBeenCalled();
  expect(api.activateTestCase).not.toHaveBeenCalled();
  await button(wrapper, "关闭").trigger("click");
  expect(wrapper.text()).toContain(`固定草稿 ${draft.case_id} · V1 · ${draft.id}`);
});

it("keeps archived project drafts readable and prevents editing or activation", async () => {
  api.getProjectOverview.mockResolvedValue({ project: { id: scope.project_id, status: "archived" } });
  const wrapper = panel();
  await flushPromises();
  await button(wrapper, `评审与修订此草稿 ${draft.id}`).trigger("click");
  await flushPromises();
  expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
  expect(button(wrapper, "编辑新版本").attributes("disabled")).toBeDefined();
  expect(wrapper.findAll("button").some(item => item.text() === "提交当前版本评审")).toBe(false);
  expect(api.createTestCaseVersion).not.toHaveBeenCalled();
});

it("does not open a version that differs from the fixed generation receipt", async () => {
  api.listTestCaseVersions.mockResolvedValue([{ ...draft, content_hash: "wrong-hash" }]);
  const wrapper = panel();
  await flushPromises();
  await button(wrapper, `评审与修订此草稿 ${draft.id}`).trigger("click");
  await flushPromises();
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  expect(wrapper.get('[role="alert"]').text()).toContain("完整草稿与项目或固定收据不一致");
});

it("discards an old project's pending review read after navigation", async () => {
  let release: (value: TestCaseRecord) => void = () => {};
  api.getTestCase.mockImplementationOnce(() => new Promise<TestCaseRecord>(resolve => { release = resolve; }));
  const wrapper = panel();
  await flushPromises();
  await button(wrapper, `评审与修订此草稿 ${draft.id}`).trigger("click");
  await flushPromises();
  await wrapper.setProps({ projectId: "other-project" });
  release(structuredClone(record));
  await flushPromises();
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  expect(api.createTestCaseVersion).not.toHaveBeenCalled();
});
