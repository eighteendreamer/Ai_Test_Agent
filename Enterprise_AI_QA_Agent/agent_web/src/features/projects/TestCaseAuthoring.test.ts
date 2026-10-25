// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import JsonContentTree from "./JsonContentTree.vue";
import TestCaseTemplateEditor from "./TestCaseTemplateEditor.vue";
import TestCaseContentDialog from "./TestCaseContentDialog.vue";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../types";
import { allSelects, buttonByText, buttonTexts, dialogText, dialogElement, hasAlert, setInput, setSelect, setTab } from "../../test-utils/naive";

const api = vi.hoisted(() => ({ getTestCaseGenerationTemplate: vi.fn(), saveTestCaseGenerationTemplate: vi.fn(),
  listTestCaseVersions: vi.fn(), createTestCaseVersion: vi.fn(), getTestCase: vi.fn(), submitTestCaseReview: vi.fn(), activateTestCase: vi.fn() }));
vi.mock("../../services/api", () => ({ api }));

const version: TestCaseVersionRecord = {
  id: "version-one", case_id: "case-one", version: 1, content_format: "json-v1",
  content_json: { "业务扩展": { "未知字段": [0, false, null, "中文😀"] }, steps: [{ action: "按编号查询", expected: "显示详情", custom: { keep: true } }] },
  execution_notes: "完整内容是执行依据", generation_metadata: { template: { revision: 1 } },
  preconditions: [], steps: [], assertions: [], test_data: {}, cleanup: [], content_hash: "a".repeat(64),
  source_refs: [{ source_type: "document", source_id: "doc", label: "需求", metadata: {} }],
  model_key: "authoring-model", prompt_version: "json-v2", skill_versions: { authoring: "v1" }, created_at: "2026-10-03T00:00:00Z",
};
const testCase: TestCaseRecord = { id: "case-one", project_id: "project-one", case_key: "orders", title: "订单查询",
  mode_key: "api_testing", case_type: "json", priority: "P1", lifecycle_status: "active", active_version_id: "version-one",
  latest_version: 1, created_at: version.created_at, updated_at: version.created_at };

describe("project case authoring", () => {
  afterEach(() => { document.body.innerHTML = ""; });
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    api.getTestCase.mockResolvedValue(structuredClone(testCase));
    api.listTestCaseVersions.mockResolvedValue([structuredClone(version)]);
    api.getTestCaseGenerationTemplate.mockResolvedValue({ project_id: "project-one", scope_key: "global", revision: 1, is_default: true,
      content_hash: "b".repeat(64), payload: { name: "默认模板", writing_guidance: "按来源编写", execution_notes: "保存自身说明", example_json: { objective: "验证业务" } } });
    api.saveTestCaseGenerationTemplate.mockImplementation(async (id, payload) => ({ project_id: id, scope_key: id, revision: 1, is_default: false, payload }));
    api.createTestCaseVersion.mockImplementation(async (_id, payload) => ({ ...version, ...payload, id: "version-two", version: 2 }));
  });

  it("opens the explicitly selected version rather than a later draft", async () => {
    const later = { ...version, id: "later-version", version: 2 };
    api.listTestCaseVersions.mockResolvedValue([version, later]);
    const wrapper = mount(TestCaseContentDialog, { props: { testCase, initialVersionId: version.id } });
    await flushPromises();
    expect(allSelects(wrapper)[0].props("value")).toBe(version.id);
    expect(dialogText()).toContain("完整内容是执行依据");
    wrapper.unmount();
  });

  it("keeps complete fixed contents readable without exposing write actions", async () => {
    const wrapper = mount(TestCaseContentDialog, { props: { testCase, initialVersionId: version.id, canWrite: false } });
    await flushPromises();
    expect(dialogText()).toContain("未知字段");
    expect(buttonTexts(wrapper).some(text => ["提交当前版本评审", "启用当前版本"].includes(text))).toBe(false);
    await setTab(wrapper, "edit");
    expect(buttonByText(wrapper, "保存新草稿版本")!.props("disabled")).toBe(true);
    expect(api.submitTestCaseReview).not.toHaveBeenCalled();
    expect(api.activateTestCase).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("renders nested unknown fields and escapes source markup", () => {
    const wrapper = mount(JsonContentTree, { props: { label: "用例", value: { "扩展": [0, false, null, "<img src=x onerror=alert(1)>", { "深层": "中文😀" }] } } });
    expect(wrapper.text()).toContain("深层");
    expect(wrapper.text()).toContain("中文😀");
    expect(wrapper.text()).toContain("false");
    expect(wrapper.text()).toContain("null");
    expect(wrapper.find("img").exists()).toBe(false);
    wrapper.unmount();
  });

  it("copies the default into project configuration and rejects malformed JSON", async () => {
    const wrapper = mount(TestCaseTemplateEditor, { props: { projectId: "project-one" } });
    await flushPromises();
    expect(dialogText()).toContain("系统默认模板");
    await setInput(wrapper, 1, "保留业务扩展");
    await setInput(wrapper, 3, '{"custom":{"nested":[0,false]}}');
    await buttonByText(wrapper, "保存项目模板")!.trigger("click");
    await flushPromises();
    expect(api.saveTestCaseGenerationTemplate).toHaveBeenCalledWith("project-one", expect.objectContaining({
      writing_guidance: "保留业务扩展", example_json: { custom: { nested: [0, false] } },
    }));
    expect(wrapper.emitted("saved")).toHaveLength(1);
    await setInput(wrapper, 3, "{");
    await buttonByText(wrapper, "保存项目模板")!.trigger("click");
    await flushPromises();
    expect(hasAlert(wrapper)).toBe(true);
    expect(api.saveTestCaseGenerationTemplate).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("shows full versions, steps and raw JSON without loading a template", async () => {
    const wrapper = mount(TestCaseContentDialog, { props: { testCase } });
    await flushPromises();
    expect(dialogText()).toContain("未知字段");
    expect(dialogText()).toContain("完整内容是执行依据");
    await setTab(wrapper, "steps");
    expect(dialogText()).toContain("按编号查询");
    expect(dialogText()).toContain("custom");
    await setTab(wrapper, "raw");
    expect(JSON.parse(dialogElement("pre")!.textContent || "")).toEqual(version.content_json);
    expect(api.getTestCaseGenerationTemplate).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("saves edits as another version and retains the old content and source", async () => {
    const wrapper = mount(TestCaseContentDialog, { props: { testCase } });
    await flushPromises();
    await setTab(wrapper, "edit");
    await setInput(wrapper, 1, '{"自定义":{"保留":[0,false,null]},"新增":"值"}');
    await buttonByText(wrapper, "保存新草稿版本")!.trigger("click");
    await flushPromises();
    expect(api.createTestCaseVersion).toHaveBeenCalledWith("case-one", expect.objectContaining({
      content_json: { "自定义": { "保留": [0, false, null] }, "新增": "值" }, source_refs: version.source_refs,
      generation_metadata: { template: { revision: 1 }, manual_edit: { source_version_id: "version-one" } },
    }));
    expect(wrapper.emitted("saved")).toHaveLength(1);
    await setSelect(wrapper, "version-one");
    expect(dialogText()).toContain("业务扩展");
    expect(dialogText()).not.toContain('"新增"');
    expect(api.getTestCaseGenerationTemplate).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("reviews the selected draft and enables it even when a newer draft exists", async () => {
    const v2 = { ...version, id: "version-two", version: 2 };
    const v3 = { ...version, id: "version-three", version: 3 };
    let current: TestCaseRecord = { ...testCase, latest_version: 3, version_reviews: {
      "version-one": { version_id: "version-one", version: 1, status: "approved", origin: "native", events: [] },
      "version-two": { version_id: "version-two", version: 2, status: "draft", origin: "native", events: [] },
      "version-three": { version_id: "version-three", version: 3, status: "draft", origin: "native", events: [] },
    } };
    api.listTestCaseVersions.mockResolvedValue([version, v2, v3]);
    api.getTestCase.mockImplementation(async () => structuredClone(current));
    api.submitTestCaseReview.mockImplementation(async (_caseId, id, requestId) => {
      const review = current.version_reviews![id];
      review.status = "pending_review";
      review.events.push({ action: "submitted", at: version.created_at, request_id: requestId });
      return structuredClone(current);
    });
    api.activateTestCase.mockImplementation(async (_caseId, id, operation) => {
      const review = current.version_reviews![id];
      review.status = "approved";
      review.events.push({ action: "activated", at: version.created_at, request_id: operation.request_id });
      current = { ...current, active_version_id: id };
      return structuredClone(current);
    });
    const wrapper = mount(TestCaseContentDialog, { props: { testCase } });
    await flushPromises();
    await setSelect(wrapper, v2.id);
    await buttonByText(wrapper, "提交当前版本评审")!.trigger("click");
    await flushPromises();
    expect(current.active_version_id).toBe(version.id);
    expect(dialogText()).toContain("待评审");
    await buttonByText(wrapper, "启用当前版本")!.trigger("click");
    await flushPromises();
    expect(api.activateTestCase).toHaveBeenCalledWith(testCase.id, v2.id, {
      request_id: expect.any(String), expected_active_version_id: version.id,
    });
    expect(dialogText()).toContain("当前启用");
    expect(dialogText()).toContain("版本评审记录");
    expect(current.version_reviews?.[v2.id].events.some(event => event.action === "activated")).toBe(true);
    await setSelect(wrapper, v3.id);
    expect(buttonTexts(wrapper)).not.toContain("启用当前版本");
    wrapper.unmount();
  });

  it("queries a lost reply after reopening and keeps the later active version", async () => {
    let current = structuredClone(testCase);
    api.getTestCase.mockImplementation(async () => structuredClone(current));
    const v2 = { ...version, id: "version-two", version: 2 };
    api.listTestCaseVersions.mockResolvedValue([version, v2]);
    current.latest_version = 2;
    api.submitTestCaseReview.mockImplementation(async (_id, versionId, requestId) => {
      current = { ...current, version_reviews: { [versionId]: { version_id: versionId, version: 2,
        status: "pending_review", origin: "native", events: [{ action: "submitted", at: version.created_at, request_id: requestId }] } } };
      throw new Error("提交已生效但响应丢失");
    });
    const first = mount(TestCaseContentDialog, { props: { testCase } });
    await flushPromises();
    await buttonByText(first, "提交当前版本评审")!.trigger("click");
    await flushPromises();
    const stored = window.sessionStorage.getItem(`case-version-review:${testCase.project_id}:${testCase.id}`)!;
    expect(JSON.parse(stored).versionId).toBe(v2.id);
    expect(stored).not.toContain("content_json");
    first.unmount();
    current.active_version_id = "later-version";
    const second = mount(TestCaseContentDialog, { props: { testCase } });
    await flushPromises();
    expect(window.sessionStorage.length).toBe(0);
    expect(api.submitTestCaseReview).toHaveBeenCalledTimes(1);
    expect(api.activateTestCase).not.toHaveBeenCalled();
    second.unmount();
  });
});
