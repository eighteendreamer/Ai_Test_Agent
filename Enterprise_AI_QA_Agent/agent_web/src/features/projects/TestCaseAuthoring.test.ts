// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JsonContentTree from "./JsonContentTree.vue";
import TestCaseTemplateEditor from "./TestCaseTemplateEditor.vue";
import TestCaseContentDialog from "./TestCaseContentDialog.vue";
import type { TestCaseRecord, TestCaseVersionRecord } from "../../types";

const api = vi.hoisted(() => ({ getTestCaseGenerationTemplate: vi.fn(), saveTestCaseGenerationTemplate: vi.fn(),
  listTestCaseVersions: vi.fn(), createTestCaseVersion: vi.fn() }));
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
  beforeEach(() => {
    vi.clearAllMocks();
    api.listTestCaseVersions.mockResolvedValue([structuredClone(version)]);
    api.getTestCaseGenerationTemplate.mockResolvedValue({ project_id: "project-one", scope_key: "global", revision: 1, is_default: true,
      content_hash: "b".repeat(64), payload: { name: "默认模板", writing_guidance: "按来源编写", execution_notes: "保存自身说明", example_json: { objective: "验证业务" } } });
    api.saveTestCaseGenerationTemplate.mockImplementation(async (id, payload) => ({ project_id: id, scope_key: id, revision: 1, is_default: false, payload }));
    api.createTestCaseVersion.mockImplementation(async (_id, payload) => ({ ...version, ...payload, id: "version-two", version: 2 }));
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
    expect(wrapper.text()).toContain("系统默认模板");
    await wrapper.findAll("textarea")[0].setValue("保留业务扩展");
    await wrapper.findAll("textarea")[2].setValue('{"custom":{"nested":[0,false]}}');
    await wrapper.findAll("button").find(button => button.text() === "保存项目模板")!.trigger("click");
    await flushPromises();
    expect(api.saveTestCaseGenerationTemplate).toHaveBeenCalledWith("project-one", expect.objectContaining({
      writing_guidance: "保留业务扩展", example_json: { custom: { nested: [0, false] } },
    }));
    expect(wrapper.emitted("saved")).toHaveLength(1);
    await wrapper.findAll("textarea")[2].setValue("{");
    await wrapper.findAll("button").find(button => button.text() === "保存项目模板")!.trigger("click");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    expect(api.saveTestCaseGenerationTemplate).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("shows full versions, steps and raw JSON without loading a template", async () => {
    const wrapper = mount(TestCaseContentDialog, { props: { testCase } });
    await flushPromises();
    expect(wrapper.text()).toContain("未知字段");
    expect(wrapper.text()).toContain("完整内容是执行依据");
    await wrapper.findAll("button").find(button => button.text() === "步骤")!.trigger("click");
    expect(wrapper.text()).toContain("按编号查询");
    expect(wrapper.text()).toContain("custom");
    await wrapper.findAll("button").find(button => button.text() === "JSON 原文")!.trigger("click");
    expect(JSON.parse(wrapper.find("pre").text())).toEqual(version.content_json);
    expect(api.getTestCaseGenerationTemplate).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("saves edits as another version and retains the old content and source", async () => {
    const wrapper = mount(TestCaseContentDialog, { props: { testCase } });
    await flushPromises();
    await wrapper.findAll("button").find(button => button.text() === "编辑新版本")!.trigger("click");
    await wrapper.findAll("textarea")[1].setValue('{"自定义":{"保留":[0,false,null]},"新增":"值"}');
    await wrapper.findAll("button").find(button => button.text() === "保存新草稿版本")!.trigger("click");
    await flushPromises();
    expect(api.createTestCaseVersion).toHaveBeenCalledWith("case-one", expect.objectContaining({
      content_json: { "自定义": { "保留": [0, false, null] }, "新增": "值" }, source_refs: version.source_refs,
      generation_metadata: { template: { revision: 1 }, manual_edit: { source_version_id: "version-one" } },
    }));
    expect(wrapper.emitted("saved")).toHaveLength(1);
    await wrapper.find("select").setValue("version-one");
    expect(wrapper.text()).toContain("业务扩展");
    expect(wrapper.text()).not.toContain('"新增"');
    expect(api.getTestCaseGenerationTemplate).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
