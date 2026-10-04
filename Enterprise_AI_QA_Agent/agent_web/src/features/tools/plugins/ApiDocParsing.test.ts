// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ApiDocParsing from "./ApiDocParsing.vue";
import { HttpError } from "../../../services/http";
import type { ApiDocRecord, ApiDocVersionRecord, DocumentParsingSettingsRecord, DocumentParseTaskRecord, DocumentParseSnapshotView } from "../../../types";

const api = vi.hoisted(() => ({ getDocumentParsingSettings: vi.fn(), saveDocumentParsingSettings: vi.fn(),
  getDocumentParserCapabilities: vi.fn(),
  listApiDocVersions: vi.fn(), listDocumentParseTasks: vi.fn(), listDocumentParseSnapshots: vi.fn(),
  submitDocumentParseTask: vi.fn(), getDocumentParseTask: vi.fn(), getDocumentParseSubmission: vi.fn(),
  readDocumentParseSnapshot: vi.fn(), readDocumentParseFailure: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const wrappers: Array<ReturnType<typeof mount>> = [];
const now = "2026-10-04T00:00:00Z";
function settings(): DocumentParsingSettingsRecord {
  return { scope_key: "global", revision: 1, payload: { options: { parser_id: "auto", encoding: "utf-8-sig", pdf_extraction_mode: "plain" },
    parser_by_extension: { ".md": "markdown", ".pdf": "pdf", ".docx": "docx" }, policy: { timeout_seconds: 60, max_cpu_seconds: 30,
      max_memory_bytes: 536870912, max_output_bytes: 40000000, limits: { max_bytes: 20000000, max_expanded_bytes: 100000000,
        max_zip_entries: 3000, max_pages: 1000, max_blocks: 50000, max_text_chars: 5000000, max_table_cells: 50000 } } } };
}
function doc(number = 1, projectId = "project"): ApiDocRecord {
  return { id: "document", project_id: projectId, current_version_id: `version-${number}`, version_number: number,
    title: "实际需求", filename: "requirements.pdf", source: "manual", format_label: "PDF", content_type: "application/pdf",
    size_bytes: 897, storage_uri: "rustfs://fixture/original", original_asset: { id: "original", kind: "original", filename: "requirements.pdf",
      content_type: "application/pdf", size_bytes: 897, sha256: "a".repeat(64), storage_uri: "rustfs://fixture/original",
      created_at: now, reason: "original", security_report: {} },
    preview_available: false, preview_truncated: false, uploaded_at: now, updated_at: now, metadata: {} };
}
function versions(): ApiDocVersionRecord[] { return [2, 1].map(number => ({ id: `version-${number}`, document_id: "document", version: number, created_at: now, snapshot: doc(number) })); }
function task(status: DocumentParseTaskRecord["status"] = "queued", extra: Partial<DocumentParseTaskRecord> = {}): DocumentParseTaskRecord {
  return { id: "task", project_id: "project", document_id: "document", version_id: "version-1", creation_key: "parse-1",
    status, phase: status === "queued" ? "queued" : status === "running" ? "parsing" : "finished", attempt_no: 1,
    snapshot_id: status === "completed" ? "snapshot" : null, message: "实际任务状态", created_at: now, updated_at: now, ...extra };
}
function snapshot(id = "snapshot", versionId = "version-1"): DocumentParseSnapshotView {
  const capability = { parser_id: "pdf", formats: [".pdf"], implementation_version: "v1", text: true, sections: false,
    tables: false, ocr: false, source_positions: ["page"], transport: "local" as const, available: true };
  return { snapshot: { id, task_id: "task", seed: { project_id: "project", document_id: "document", version_id: versionId,
    original_asset: doc().original_asset!, config: settings(), options: settings().payload.options, parser: capability },
    storage_uri: "rustfs://fixture/result", sha256: "b".repeat(64), size_bytes: 1829, status: "partial", parser: capability,
    dependency_versions: {}, warnings: [], created_at: now }, result: { schema_version: "document-parse-v1", filename: "requirements.pdf",
      source_sha256: "a".repeat(64), source_size_bytes: 897, parser: capability, dependency_versions: {}, options: settings().payload.options,
      text: "实际订单有效期 7 天", blocks: [], sections: [], tables: [], warnings: [{ code: "structure", message: "PDF 表格无法可靠提取" }], status: "partial", metadata: {} } };
}
function button(wrapper: ReturnType<typeof mount>, label: string) { return wrapper.findAll("button").find(node => node.text() === label)!; }
function setup(document = doc(), canWrite = true) { const wrapper = mount(ApiDocParsing, { props: { document, canWrite } }); wrappers.push(wrapper); return wrapper; }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); sessionStorage.clear();
  let sequence = 0; vi.stubGlobal("crypto", { randomUUID: () => `parse-${++sequence}` });
  api.getDocumentParsingSettings.mockResolvedValue(settings()); api.listApiDocVersions.mockResolvedValue(versions());
  api.getDocumentParserCapabilities.mockResolvedValue([snapshot().result.parser]);
  api.listDocumentParseTasks.mockResolvedValue({ items: [], has_more: false, limit: 20, offset: 0 });
  api.listDocumentParseSnapshots.mockResolvedValue({ items: [], has_more: false, limit: 20, offset: 0 });
  api.submitDocumentParseTask.mockResolvedValue(task()); api.getDocumentParseTask.mockResolvedValue(task("completed"));
  api.getDocumentParseSubmission.mockResolvedValue(task("completed")); api.readDocumentParseSnapshot.mockResolvedValue(snapshot());
});
afterEach(() => { for (const wrapper of wrappers.splice(0)) wrapper.unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("keeps the original version/request after a lost response, refresh and configuration change", async () => {
  const wrapper = setup(); await flushPromises();
  api.submitDocumentParseTask.mockRejectedValueOnce(new Error("response lost"));
  await button(wrapper, "提交解析").trigger("click"); await flushPromises();
  const original = api.submitDocumentParseTask.mock.calls[0][2];
  expect(original).toEqual({ creation_key: "parse-1", version_id: "version-1" });
  expect(wrapper.text()).toContain("response lost"); wrapper.unmount();
  api.getDocumentParseSubmission.mockRejectedValueOnce(new HttpError("not found", 404));
  const changed = settings(); changed.revision = 2; changed.payload.options.pdf_extraction_mode = "layout";
  api.getDocumentParsingSettings.mockResolvedValue(changed);
  const reopened = setup(doc(2)); await flushPromises();
  expect(button(reopened, "提交解析").attributes("disabled")).toBeDefined();
  api.submitDocumentParseTask.mockResolvedValue(task("completed"));
  await button(reopened, "重试原解析提交").trigger("click"); await flushPromises();
  expect(api.submitDocumentParseTask.mock.calls[1][2]).toEqual(original);
  expect(reopened.find(".document-parse-snapshot").text()).toContain("version-1");
  expect(reopened.text()).toContain("PDF 表格无法可靠提取");
});

it("recovers a submitted task by request without posting again; 503 remains unknown", async () => {
  sessionStorage.setItem("qa-agent:document-parse:project:document", JSON.stringify({ payload: { creation_key: "parse-1", version_id: "version-1" }, status: "completed" }));
  api.getDocumentParseSubmission.mockRejectedValueOnce(new HttpError("query unavailable", 503));
  const wrapper = setup(); await flushPromises();
  expect(wrapper.text()).toContain("query unavailable"); expect(api.submitDocumentParseTask).not.toHaveBeenCalled();
  expect(button(wrapper, "提交解析").attributes("disabled")).toBeDefined();
  await button(wrapper, "查询上次解析提交").trigger("click"); await flushPromises();
  expect(api.getDocumentParseSubmission).toHaveBeenLastCalledWith("project", "document", "parse-1");
  expect(api.submitDocumentParseTask).not.toHaveBeenCalled(); expect(wrapper.text()).toContain("实际订单有效期 7 天");
});

it("polls once to completion, stops on close and never replaces an explicitly selected historical snapshot", async () => {
  api.listDocumentParseSnapshots.mockResolvedValue({ items: [snapshot("old").snapshot], has_more: false, limit: 20, offset: 0 });
  const wrapper = setup(); await flushPromises(); await button(wrapper, "提交解析").trigger("click"); await flushPromises();
  api.readDocumentParseSnapshot.mockResolvedValue(snapshot("old"));
  await button(wrapper, "查看解析快照 old").trigger("click"); await flushPromises();
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(api.getDocumentParseTask).toHaveBeenCalledOnce(); expect(api.readDocumentParseSnapshot).toHaveBeenCalledOnce();
  expect(wrapper.find(".document-parse-snapshot").text()).toContain("快照：old");
  await vi.advanceTimersByTimeAsync(5000); expect(api.getDocumentParseTask).toHaveBeenCalledOnce();
  wrapper.unmount(); await vi.advanceTimersByTimeAsync(5000); expect(api.getDocumentParseTask).toHaveBeenCalledOnce();
});

it("ignores a late snapshot after changing document identity and rejects a foreign task", async () => {
  api.listDocumentParseSnapshots.mockResolvedValue({ items: [snapshot().snapshot], has_more: false, limit: 20, offset: 0 });
  let resolve!: (value: DocumentParseSnapshotView) => void;
  api.readDocumentParseSnapshot.mockReturnValue(new Promise<DocumentParseSnapshotView>(ok => { resolve = ok; }));
  const wrapper = setup(); await flushPromises();
  await button(wrapper, "查看解析快照 snapshot").trigger("click");
  api.listDocumentParseSnapshots.mockResolvedValue({ items: [], has_more: false, limit: 20, offset: 0 });
  await wrapper.setProps({ document: doc(1, "other-project") }); await flushPromises();
  resolve(snapshot()); await flushPromises(); expect(wrapper.find(".document-parse-snapshot").exists()).toBe(false);
  api.submitDocumentParseTask.mockResolvedValue(task());
  await button(wrapper, "提交解析").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("解析任务与当前文档或原请求身份不一致");
});

it("shows storage failure while retaining in-memory request and permits historical reads for archived projects", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("controlled quota"); });
  const wrapper = setup(); await flushPromises(); await button(wrapper, "提交解析").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("浏览器无法保存解析请求编号"); expect(wrapper.text()).toContain("parse-1");
  const archived = setup(doc(), false); await flushPromises();
  expect(button(archived, "提交解析")).toBeUndefined(); expect(button(archived, "保存项目解析设置")).toBeUndefined();
});

it("saves human-readable settings through the project API and reads actual failure diagnostics", async () => {
  const saved = settings(); saved.scope_key = "project"; saved.revision = 2; saved.payload.options.pdf_extraction_mode = "layout";
  api.saveDocumentParsingSettings.mockResolvedValue(saved);
  api.submitDocumentParseTask.mockResolvedValue(task("failed", { failure_artifact: { storage_uri: "rustfs://fixture/failure", sha256: "c".repeat(64), size_bytes: 77, exit_code: 2 } }));
  api.readDocumentParseFailure.mockResolvedValue({ task_id: "task", artifact: task("failed").failure_artifact,
    exit_code: 2, error: "加密 PDF 不支持", stdout: "", stderr: "" });
  const wrapper = setup(); await flushPromises();
  await wrapper.get('select[aria-label="PDF 文本提取"]').setValue("layout");
  await wrapper.get(".document-parse-settings form").trigger("submit"); await flushPromises();
  expect(api.saveDocumentParsingSettings.mock.calls[0][0]).toBe("project");
  expect(api.saveDocumentParsingSettings.mock.calls[0][1].options.pdf_extraction_mode).toBe("layout");
  await button(wrapper, "提交解析").trigger("click"); await flushPromises();
  await button(wrapper, "查看解析失败诊断").trigger("click"); await flushPromises();
  expect(wrapper.get(".parse-failure").text()).toContain("加密 PDF 不支持");
  expect(wrapper.get(".parse-failure").text()).toContain("空输出");
});

it("uses declared parser capabilities and keeps historical results readable when MySQL is unavailable", async () => {
  api.getDocumentParsingSettings.mockRejectedValue(new HttpError("config unavailable", 503));
  api.listDocumentParseSnapshots.mockResolvedValue({ items: [snapshot().snapshot], has_more: false, limit: 20, offset: 0 });
  const wrapper = setup(); await flushPromises();
  expect(wrapper.text()).toContain("config unavailable");
  expect(button(wrapper, "提交解析").attributes("disabled")).toBeDefined();
  await button(wrapper, "查看解析快照 snapshot").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("实际订单有效期 7 天");
  api.getDocumentParsingSettings.mockResolvedValue(settings());
  const unsupportedVersions = versions(); unsupportedVersions[1].snapshot.original_asset!.filename = "openapi.json";
  api.listApiDocVersions.mockResolvedValue(unsupportedVersions);
  const unsupported = setup(); await flushPromises();
  expect(unsupported.text()).toContain("没有支持此原件格式的可用解析器");
  expect(button(unsupported, "提交解析").attributes("disabled")).toBeDefined();
});
