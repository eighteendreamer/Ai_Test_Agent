// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import actual from "../../../../../docs/upgrade_2026-10-03/p3-document-difference-production-verified/docx/summary.json";
import failureEvidence from "../../../../../docs/upgrade_2026-10-03/p3-document-difference-production-verified/docx/failure.json";
import type { DocumentDifferenceSnapshotView, DocumentDifferenceTaskRecord } from "../../../types";
import DocumentDifferenceTask from "./DocumentDifferenceTask.vue";
import DocumentDifferenceSnapshot from "./DocumentDifferenceSnapshot.vue";

const api = vi.hoisted(() => ({ getDocumentDifferenceTask: vi.fn(), readDocumentDifferenceSnapshot: vi.fn(), readDocumentDifferenceFailure: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const record = () => structuredClone(actual.task) as DocumentDifferenceTaskRecord;
const view = () => structuredClone(actual.snapshot) as unknown as DocumentDifferenceSnapshotView;
const props = (task = record()) => ({ projectId: task.project_id, documentId: task.document_id, taskId: task.id });
const wrappers: ReturnType<typeof mount>[] = [];
function setup() { const wrapper = mount(DocumentDifferenceTask, { props: props(), global: { stubs: { DocumentImpactPanel: true } } }); wrappers.push(wrapper); return wrapper; }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers();
  api.getDocumentDifferenceTask.mockResolvedValue(record()); api.readDocumentDifferenceSnapshot.mockResolvedValue(view());
  api.readDocumentDifferenceFailure.mockResolvedValue({ ...structuredClone(failureEvidence), artifact: actual.failed_task.failure_artifact, task_id: actual.failed_task.id });
});
afterEach(() => { for (const wrapper of wrappers.splice(0)) wrapper.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("reads exact historical references using the real DOCX result, without settings or submission", async () => {
  const wrapper = setup(); await flushPromises();
  expect(api.getDocumentDifferenceTask).toHaveBeenCalledWith(props().projectId, props().documentId, props().taskId);
  expect(api.readDocumentDifferenceSnapshot).toHaveBeenCalledWith(props().projectId, props().documentId, record().snapshot_id);
  expect(wrapper.text()).toContain("有效期"); expect(wrapper.text()).toContain("7 天"); expect(wrapper.text()).toContain("9 天");
  expect(wrapper.text()).toContain("word/document.xml"); expect(wrapper.text()).toContain("docx_layout_scope");
  expect(wrapper.findComponent(DocumentDifferenceSnapshot).exists()).toBe(true);
  await wrapper.get('select[aria-label="文本差异视图"]').setValue("normalized");
  expect(wrapper.text()).toContain("仅归一换行");
});

it.each(["project", "task", "result", "old-reference", "result-hash"])("rejects mismatched %s identities", async mismatch => {
  const task = record(); const result = view();
  if (mismatch === "project") task.project_id = "other";
  if (mismatch === "task") task.id = "other";
  if (mismatch === "result") result.snapshot.task_id = "other";
  if (mismatch === "old-reference") result.result.old.snapshot_id = "other";
  if (mismatch === "result-hash") result.result.new.result_sha256 = "0".repeat(64);
  api.getDocumentDifferenceTask.mockResolvedValue(task); api.readDocumentDifferenceSnapshot.mockResolvedValue(result);
  const wrapper = setup(); await flushPromises();
  expect(wrapper.findComponent(DocumentDifferenceSnapshot).exists()).toBe(false);
  expect(wrapper.get('[role="alert"]').text()).toContain("不一致");
});

it("discards late result responses after a document/task switch", async () => {
  let release!: (value: DocumentDifferenceSnapshotView) => void;
  api.readDocumentDifferenceSnapshot.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const wrapper = setup(); await flushPromises();
  const next = { ...record(), id: "next-task", project_id: "next-project", document_id: "next-document", status: "queued" as const, snapshot_id: null };
  api.getDocumentDifferenceTask.mockResolvedValue(next);
  await wrapper.setProps(props(next)); await flushPromises();
  release(view()); await flushPromises();
  expect(wrapper.text()).toContain("next-task"); expect(wrapper.findComponent(DocumentDifferenceSnapshot).exists()).toBe(false);
  const calls = api.getDocumentDifferenceTask.mock.calls.length; wrapper.unmount();
  await vi.advanceTimersByTimeAsync(3000); expect(api.getDocumentDifferenceTask).toHaveBeenCalledTimes(calls);
});

it("polls nonterminal state then retains a readable genuine failure trace", async () => {
  const running = { ...record(), status: "running", snapshot_id: null };
  api.getDocumentDifferenceTask.mockResolvedValueOnce(running);
  const wrapper = setup(); await flushPromises(); expect(wrapper.text()).toContain("正在比较");
  const failed = { ...record(), status: "failed", snapshot_id: null, failure_artifact: actual.failed_task.failure_artifact };
  api.getDocumentDifferenceTask.mockResolvedValue(failed);
  api.readDocumentDifferenceFailure.mockResolvedValue({ ...failureEvidence, artifact: actual.failed_task.failure_artifact, task_id: record().id });
  await vi.advanceTimersByTimeAsync(1000); await flushPromises();
  expect(wrapper.get('[aria-label="比较失败诊断"]').text()).toContain(failureEvidence.error);
  expect(wrapper.findComponent(DocumentDifferenceSnapshot).exists()).toBe(false);
  const calls = api.getDocumentDifferenceTask.mock.calls.length;
  await vi.advanceTimersByTimeAsync(5000); expect(api.getDocumentDifferenceTask).toHaveBeenCalledTimes(calls);
});

it("shows read errors and retries the same task without creating a new one", async () => {
  api.getDocumentDifferenceTask.mockRejectedValueOnce(new Error("存储暂时不可用"));
  const wrapper = setup(); await flushPromises(); expect(wrapper.text()).toContain("存储暂时不可用");
  await wrapper.get("button").trigger("click"); await flushPromises();
  expect(api.getDocumentDifferenceTask).toHaveBeenCalledTimes(2);
  expect(api.getDocumentDifferenceTask.mock.calls[0]).toEqual(api.getDocumentDifferenceTask.mock.calls[1]);
  expect(wrapper.findComponent(DocumentDifferenceSnapshot).exists()).toBe(true);
});

it("emits only a verified task for the original submission and rejects changed request references", async () => {
  const task = record(); const request = { creation_key: task.creation_key, old_snapshot_id: task.old_snapshot_id, new_snapshot_id: task.new_snapshot_id };
  const wrapper = mount(DocumentDifferenceTask, { props: { ...props(), request }, global: { stubs: { DocumentImpactPanel: true } } }); wrappers.push(wrapper);
  await flushPromises(); expect(wrapper.emitted("updated")).toEqual([[task]]);
  await wrapper.setProps({ request: { ...request, old_snapshot_id: "another-snapshot" } }); await flushPromises();
  expect(wrapper.emitted("updated")).toHaveLength(1);
  expect(wrapper.get('[role="alert"]').text()).toContain("不一致");
  expect(wrapper.findComponent(DocumentDifferenceSnapshot).exists()).toBe(false);
});

it("renders literal external text, bounded detail, partial warnings and full JSON downloads", async () => {
  const input = view(); input.snapshot.status = "partial";
  input.result.raw.changes = Array.from({ length: 21 }, () => structuredClone(input.result.raw.changes[0]!));
  input.result.raw.changes[0]!.old.text = "<img src=x onerror=alert(1)>";
  input.result.raw.changes[0]!.character_status = "omitted";
  const wrapper = mount(DocumentDifferenceSnapshot, { props: { view: input } }); wrappers.push(wrapper);
  expect(wrapper.findAll(".difference-change")).toHaveLength(20);
  expect(wrapper.find("img").exists()).toBe(false); expect(wrapper.text()).toContain("<img src=x onerror=alert(1)>");
  expect(wrapper.text()).toContain("输入含部分提取结果"); expect(wrapper.text()).toContain("已省略");
  await wrapper.findAll("button").find(button => button.text() === "显示更多文本变化")!.trigger("click");
  expect(wrapper.findAll(".difference-change")).toHaveLength(21);
  const create = vi.fn().mockReturnValue("blob:comparison"); const revoke = vi.fn();
  vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  await wrapper.findAll("button").find(button => button.text() === "下载完整比较结果 JSON")!.trigger("click");
  expect(create).toHaveBeenCalledTimes(1); expect(revoke).toHaveBeenCalledWith("blob:comparison");
});
