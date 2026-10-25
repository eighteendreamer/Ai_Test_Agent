// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CaseImportDialog from "./CaseImportDialog.vue";
import { buttonByText, clickButton, clickCollapseHeader, dialogText, hasAlert, setSelectByLabel } from "../../test-utils/naive";

const api = vi.hoisted(() => Object.fromEntries([
  "getCaseImportSettings", "saveCaseImportSettings", "uploadCaseImport", "inspectCaseImportSheets",
  "previewCaseImport", "commitCaseImport", "getCaseImportBatch",
].map(name => [name, vi.fn()])));
vi.mock("../../services/api", () => ({ api }));
const sheet = { name: "用例", headers: ["用例编号", "标题", "前置条件"], suggested_columns: { case_key: "用例编号", title: "标题", preconditions: "前置条件" } };
const batch = () => ({ conflicting_case_keys: [], batch: { id: "batch", status: "previewed", case_ids: [], preview: {
  candidates: [{ row: 2, draft: { case_key: "negative", title: "保留负向条件", content_json: { "未知字段": [0, false, null, "中文😀"] } } }], issues: [],
} } });
function setup() { return mount(CaseImportDialog, { props: { projectId: "project", modes: [{ key: "api_testing", name: "API 测试" }] } }); }
async function choose(filename = "cases.xlsx") {
  const input = document.body.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("未渲染文件选择输入框");
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["actual input"], filename)] });
  input.dispatchEvent(new Event("change"));
  await vi.waitFor(() => expect(api.uploadCaseImport).toHaveBeenCalled());
  await flushPromises();
}

describe("case file import", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getCaseImportSettings.mockResolvedValue({ scope_key: "global", revision: 1, payload: { policy: { limits: { max_bytes: 5000000 } }, options: null } });
    api.uploadCaseImport.mockResolvedValue({ id: "source", filename: "cases.xlsx", size_bytes: 12, sheets: [sheet] });
    api.previewCaseImport.mockImplementation(async () => batch());
    api.commitCaseImport.mockImplementation(async () => ({ ...batch(), batch: { ...batch().batch, status: "committed", case_ids: ["case"] } }));
  });
  afterEach(() => { document.body.innerHTML = ""; });

  it("reads a File, submits sheet mapping and preserves arbitrary preview JSON", async () => {
    const wrapper = setup(); await flushPromises(); await choose();
    await clickButton(wrapper, "生成导入预览");
    expect(api.uploadCaseImport).toHaveBeenCalledWith("project", "cases.xlsx", expect.any(String));
    expect(api.previewCaseImport).toHaveBeenCalledWith("project", "source", expect.objectContaining({ layout: "rows", case_sheet: "用例", columns: expect.objectContaining({ title: "标题" }) }));
    expect(dialogText()).toContain("未知字段"); expect(dialogText()).toContain("false"); expect(dialogText()).toContain("中文😀");
    await clickButton(wrapper, "提交草稿");
    expect(wrapper.emitted("imported")).toHaveLength(1); expect(dialogText()).toContain("等待评审");
    wrapper.unmount();
  });

  it("invalidates old preview and sends an explicit unmapped column", async () => {
    const wrapper = setup(); await flushPromises(); await choose();
    await clickButton(wrapper, "生成导入预览");
    await clickCollapseHeader(0);
    await setSelectByLabel(wrapper, "前置条件", "");
    expect(dialogText()).not.toContain("有效候选");
    await clickButton(wrapper, "生成导入预览");
    expect(api.previewCaseImport.mock.lastCall![2].columns.preconditions).toBe("");
    wrapper.unmount();
  });

  it.each(["issue", "conflict"])("prevents submission for %s and retains the visible report", async kind => {
    const invalid = batch();
    if (kind === "issue") (invalid.batch.preview.issues as any[]).push({ severity: "error", sheet: "用例", row: 3, message: "JSON 内容错误" });
    else (invalid.conflicting_case_keys as string[]).push("negative");
    api.previewCaseImport.mockResolvedValue(invalid);
    const wrapper = setup(); await flushPromises(); await choose();
    await clickButton(wrapper, "生成导入预览");
    expect(buttonByText(wrapper, "提交草稿")!.attributes("disabled")).toBeDefined();
    expect(dialogText()).toContain(kind === "issue" ? "第 3 行" : "编号已存在");
    expect(api.commitCaseImport).not.toHaveBeenCalled(); wrapper.unmount();
  });

  it("recovers committed state after a lost commit response", async () => {
    api.commitCaseImport.mockRejectedValue(new Error("响应丢失"));
    api.getCaseImportBatch.mockResolvedValue({ ...batch(), batch: { ...batch().batch, status: "committed", case_ids: ["case"] } });
    const wrapper = setup(); await flushPromises(); await choose();
    await clickButton(wrapper, "生成导入预览");
    await clickButton(wrapper, "提交草稿");
    expect(api.commitCaseImport).toHaveBeenCalledTimes(1); expect(api.getCaseImportBatch).toHaveBeenCalledWith("project", "batch");
    expect(wrapper.emitted("imported")).toHaveLength(1); expect(hasAlert(wrapper, "error")).toBe(false);
    wrapper.unmount();
  });
});
