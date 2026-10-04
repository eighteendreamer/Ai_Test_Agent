// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import actual from "../../../../../docs/upgrade_2026-10-03/p3-document-difference-production-verified/docx/summary.json";
import type { ApiDocRecord, ApiDocVersionRecord, DocumentParseSnapshot, DocumentDifferenceSettingsRecord, DocumentDifferenceTaskRecord } from "../../../types";
import ApiDocDifferences from "./ApiDocDifferences.vue";
import DocumentSnapshotSelector from "./DocumentSnapshotSelector.vue";
import DocumentDifferenceSettings from "./DocumentDifferenceSettings.vue";

const api = vi.hoisted(() => ({ listApiDocVersions: vi.fn(), listDocumentParseSnapshots: vi.fn(),
  getDocumentDifferenceSettings: vi.fn(), saveDocumentDifferenceSettings: vi.fn(),
  listDocumentDifferenceTasks: vi.fn(), listDocumentDifferenceSnapshots: vi.fn(),
  submitDocumentDifferenceTask: vi.fn(), getDocumentDifferenceSubmission: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
const wrappers: ReturnType<typeof mount>[] = [];
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const seed = actual.snapshot.snapshot.seed;
const old = () => clone(seed.old_snapshot) as unknown as DocumentParseSnapshot;
const newer = () => clone(seed.new_snapshot) as unknown as DocumentParseSnapshot;
const config = () => clone(seed.config) as DocumentDifferenceSettingsRecord;
function document(): ApiDocRecord {
  const source = newer(); const asset = source.seed.original_asset;
  return { id: source.seed.document_id, project_id: source.seed.project_id, current_version_id: source.seed.version_id,
    version_number: 2, title: "实际 DOCX 需求", filename: asset.filename, source: "manual", format_label: "DOCX",
    content_type: asset.content_type, size_bytes: asset.size_bytes, storage_uri: asset.storage_uri, original_asset: asset,
    preview_available: false, preview_truncated: false, uploaded_at: source.created_at, updated_at: source.created_at, metadata: {} };
}
function versions(): ApiDocVersionRecord[] {
  return [newer(), old()].map((source, index) => ({ id: source.seed.version_id, document_id: document().id,
    version: 2 - index, created_at: source.created_at, snapshot: { ...document(), current_version_id: source.seed.version_id } }));
}
function task(status: DocumentDifferenceTaskRecord["status"] = "queued", creationKey = "submission-1"): DocumentDifferenceTaskRecord {
  return { ...clone(actual.task), status, creation_key: creationKey, snapshot_id: status === "completed" ? actual.task.snapshot_id : null } as DocumentDifferenceTaskRecord;
}
const empty = () => ({ items: [], limit: 20, offset: 0, has_more: false });
const cacheKey = () => `qa-agent:document-difference:${document().project_id}:${document().id}`;
const payload = () => ({ creation_key: "submission-1", old_snapshot_id: old().id, new_snapshot_id: newer().id });
function button(wrapper: ReturnType<typeof mount>, label: string) { return wrapper.findAll("button").find(item => item.text() === label)!; }
function setup(canWrite = true) {
  const wrapper = mount(ApiDocDifferences, { props: { document: document(), canWrite },
    global: { stubs: { DocumentDifferenceTask: { name: "DocumentDifferenceTask", props: ["projectId", "documentId", "taskId", "request"],
      emits: ["updated"], template: '<div class="task-view">{{ taskId }}</div>' } } } });
  wrappers.push(wrapper); return wrapper;
}
function selector() {
  const wrapper = mount(DocumentSnapshotSelector, { props: { side: "old", projectId: document().project_id!,
    documentId: document().id, versions: versions(), versionId: old().seed.version_id, modelValue: null } });
  wrappers.push(wrapper); return wrapper;
}
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear();
  let sequence = 0; vi.stubGlobal("crypto", { randomUUID: () => `submission-${++sequence}` });
  api.listApiDocVersions.mockResolvedValue(versions()); api.getDocumentDifferenceSettings.mockResolvedValue(config());
  api.listDocumentParseSnapshots.mockImplementation((_project, _doc, version) => Promise.resolve({ ...empty(),
    items: [version === old().seed.version_id ? old() : newer()] }));
  api.listDocumentDifferenceTasks.mockResolvedValue(empty()); api.listDocumentDifferenceSnapshots.mockResolvedValue(empty());
  api.submitDocumentDifferenceTask.mockResolvedValue(task()); api.getDocumentDifferenceSubmission.mockResolvedValue(task());
  api.saveDocumentDifferenceSettings.mockResolvedValue({ ...config(), scope_key: document().project_id, revision: 2 });
});
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("recommends current and preceding actual DOCX snapshots and only submits after the user's action", async () => {
  const wrapper = setup(); await flushPromises();
  expect((wrapper.get('select[aria-label="旧侧原件版本"]').element as HTMLSelectElement).value).toBe(old().seed.version_id);
  expect((wrapper.get('select[aria-label="新侧原件版本"]').element as HTMLSelectElement).value).toBe(newer().seed.version_id);
  expect(api.submitDocumentDifferenceTask).not.toHaveBeenCalled();
  await button(wrapper, "提交文档比较").trigger("click"); await flushPromises();
  expect(api.submitDocumentDifferenceTask).toHaveBeenCalledExactlyOnceWith(document().project_id, document().id, payload());
  expect(JSON.parse(sessionStorage.getItem(cacheKey())!).payload).toEqual(payload());
  expect(wrapper.getComponent({ name: "DocumentDifferenceTask" }).props("request")).toEqual(payload());
});

it("recovers a committed request after a dropped reply and remount even when selections and settings change", async () => {
  api.submitDocumentDifferenceTask.mockRejectedValueOnce(new Error("回执丢失"));
  const wrapper = setup(); await flushPromises();
  await button(wrapper, "提交文档比较").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("回执丢失"); wrapper.unmount();
  api.getDocumentDifferenceSettings.mockResolvedValue({ ...config(), revision: 99 });
  const remounted = setup(); await flushPromises();
  await remounted.get('select[aria-label="旧侧原件版本"]').setValue(newer().seed.version_id); await flushPromises();
  expect(api.getDocumentDifferenceSubmission).toHaveBeenCalledExactlyOnceWith(document().project_id, document().id, "submission-1");
  expect(api.submitDocumentDifferenceTask).toHaveBeenCalledTimes(1);
  expect(remounted.getComponent({ name: "DocumentDifferenceTask" }).props("request")).toEqual(payload());
  expect(button(remounted, "提交文档比较").attributes("disabled")).toBeDefined();
});

it("retries the exact stored identity after request lookup 404 without taking current selector values", async () => {
  sessionStorage.setItem(cacheKey(), JSON.stringify({ payload: payload() }));
  api.getDocumentDifferenceSubmission.mockRejectedValue(new Error("未找到提交 404"));
  const wrapper = setup(); await flushPromises();
  await wrapper.get('select[aria-label="旧侧原件版本"]').setValue(newer().seed.version_id); await flushPromises();
  await button(wrapper, "重试原比较提交").trigger("click"); await flushPromises();
  expect(api.submitDocumentDifferenceTask).toHaveBeenCalledExactlyOnceWith(document().project_id, document().id, payload());
});

it("requires server proof before a cached terminal label unlocks a new request", async () => {
  sessionStorage.setItem(cacheKey(), JSON.stringify({ payload: payload(), task_id: task().id, status: "completed" }));
  api.getDocumentDifferenceSubmission.mockRejectedValue(new Error("数据库不可用"));
  const wrapper = setup(); await flushPromises();
  expect(button(wrapper, "提交文档比较").attributes("disabled")).toBeDefined();
  expect(wrapper.text()).toContain("数据库不可用");
  api.getDocumentDifferenceSubmission.mockResolvedValue(task("completed"));
  await button(wrapper, "查询上次比较提交").trigger("click"); await flushPromises();
  expect(button(wrapper, "提交文档比较").attributes("disabled")).toBeUndefined();
});

it.each(["project_id", "document_id", "creation_key", "old_snapshot_id", "new_snapshot_id"])("rejects a submit reply with mismatched %s", async field => {
  api.submitDocumentDifferenceTask.mockResolvedValue({ ...task(), [field]: "other" });
  const wrapper = setup(); await flushPromises();
  await button(wrapper, "提交文档比较").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("不一致"); expect(wrapper.find(".task-view").exists()).toBe(false);
  expect(JSON.parse(sessionStorage.getItem(cacheKey())!).payload).toEqual(payload());
});

it("does not overwrite an unknown request when opening history, and can follow it again", async () => {
  sessionStorage.setItem(cacheKey(), JSON.stringify({ payload: payload() }));
  api.getDocumentDifferenceSubmission.mockRejectedValueOnce(new Error("暂时未知"));
  api.listDocumentDifferenceTasks.mockResolvedValue({ ...empty(), items: [task("completed", "history")] });
  const wrapper = setup(); await flushPromises();
  await button(wrapper, `查看历史比较任务 ${task().id}`).trigger("click"); await flushPromises();
  expect(wrapper.getComponent({ name: "DocumentDifferenceTask" }).props("request")).toBeUndefined();
  expect(JSON.parse(sessionStorage.getItem(cacheKey())!).payload).toEqual(payload());
  await button(wrapper, "查询上次比较提交").trigger("click"); await flushPromises();
  expect(wrapper.getComponent({ name: "DocumentDifferenceTask" }).props("request")).toEqual(payload());
});

it("keeps historical results available when current settings and task catalog fail", async () => {
  api.getDocumentDifferenceSettings.mockRejectedValue(new Error("MySQL暂不可用"));
  api.listDocumentDifferenceTasks.mockRejectedValue(new Error("任务目录暂不可用"));
  api.listDocumentDifferenceSnapshots.mockResolvedValue({ ...empty(), items: [clone(actual.snapshot.snapshot)] });
  const wrapper = setup(false); await flushPromises();
  expect(wrapper.text()).toContain("MySQL暂不可用");
  expect(button(wrapper, "提交文档比较")).toBeUndefined();
  await button(wrapper, `查看历史比较结果 ${actual.snapshot.snapshot.id}`).trigger("click");
  expect(wrapper.find(".task-view").exists()).toBe(true);
  expect(api.submitDocumentDifferenceTask).not.toHaveBeenCalled();
});

it("retains a valid results page even when a task page has a foreign identity", async () => {
  api.listDocumentDifferenceTasks.mockResolvedValue({ ...empty(), items: [{ ...task(), project_id: "foreign" }] });
  api.listDocumentDifferenceSnapshots.mockResolvedValue({ ...empty(), items: [clone(actual.snapshot.snapshot)] });
  const wrapper = setup(); await flushPromises();
  expect(wrapper.text()).toContain("不一致");
  expect(button(wrapper, `查看历史比较结果 ${actual.snapshot.snapshot.id}`)).toBeDefined();
});

it("discards late submission responses after changing the current project/document", async () => {
  let release!: (value: DocumentDifferenceTaskRecord) => void;
  api.submitDocumentDifferenceTask.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const wrapper = setup(); await flushPromises();
  await button(wrapper, "提交文档比较").trigger("click");
  api.listApiDocVersions.mockResolvedValue([]); api.listDocumentParseSnapshots.mockResolvedValue(empty());
  await wrapper.setProps({ document: { ...document(), id: "another", project_id: "another-project" } }); await flushPromises();
  release(task()); await flushPromises();
  expect(wrapper.find(".task-view").exists()).toBe(false);
  expect(sessionStorage.getItem("qa-agent:document-difference:another-project:another")).toBeNull();
  expect(JSON.parse(sessionStorage.getItem(cacheKey())!).payload).toEqual(payload());
});

it("pages both task and result catalogs without replacing the other list", async () => {
  api.listDocumentDifferenceTasks.mockResolvedValueOnce({ ...empty(), items: [task()], has_more: true });
  api.listDocumentDifferenceSnapshots.mockResolvedValueOnce({ ...empty(), items: [clone(actual.snapshot.snapshot)], has_more: true });
  const wrapper = setup(); await flushPromises();
  api.listDocumentDifferenceTasks.mockResolvedValue({ ...empty(), offset: 1, items: [{ ...task(), id: "older-task" }] });
  await button(wrapper, "更多比较任务").trigger("click"); await flushPromises();
  expect(api.listDocumentDifferenceTasks).toHaveBeenLastCalledWith(document().project_id, document().id, undefined, 1);
  expect(wrapper.text()).toContain("older-task"); expect(wrapper.text()).toContain(actual.snapshot.snapshot.id);
  api.listDocumentDifferenceSnapshots.mockResolvedValue({ ...empty(), offset: 1, items: [{ ...clone(actual.snapshot.snapshot), id: "older-result" }] });
  await button(wrapper, "更多比较结果").trigger("click"); await flushPromises();
  expect(api.listDocumentDifferenceSnapshots).toHaveBeenLastCalledWith(document().project_id, document().id, undefined, 1);
  expect(wrapper.text()).toContain("older-result"); expect(wrapper.text()).toContain("older-task");
});

it("pages original versions and recommends by current identity and number, not list position or old project binding", async () => {
  const page = [...versions().reverse(), ...Array.from({ length: 18 }, (_, index) => ({ ...versions()[0]!, id: `version-${index + 3}`, version: index + 3 }))];
  page[0]!.snapshot.project_id = "previous-binding";
  api.listApiDocVersions.mockResolvedValueOnce(page);
  const wrapper = setup(); await flushPromises();
  expect((wrapper.get('select[aria-label="旧侧原件版本"]').element as HTMLSelectElement).value).toBe(old().seed.version_id);
  api.listApiDocVersions.mockResolvedValue([{ ...versions()[0]!, id: "older-original", version: 0 }]);
  await button(wrapper, "更多比较原件版本").trigger("click"); await flushPromises();
  expect(api.listApiDocVersions).toHaveBeenLastCalledWith(document().id, 20);
  expect(wrapper.findAll('option[value="older-original"]')).toHaveLength(2);
});

it("unlocks the next request after the viewer verifies a terminal task, and refreshes history", async () => {
  const wrapper = setup(); await flushPromises();
  await button(wrapper, "提交文档比较").trigger("click"); await flushPromises();
  const count = api.listDocumentDifferenceTasks.mock.calls.length;
  wrapper.getComponent({ name: "DocumentDifferenceTask" }).vm.$emit("updated", task("failed")); await flushPromises();
  expect(api.listDocumentDifferenceTasks).toHaveBeenCalledTimes(count + 1);
  expect(JSON.parse(sessionStorage.getItem(cacheKey())!).status).toBe("failed");
  expect(button(wrapper, "提交文档比较").attributes("disabled")).toBeUndefined();
});

it("exposes unavailable browser storage and still keeps the original request while mounted", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
  api.submitDocumentDifferenceTask.mockRejectedValue(new Error("未获回执"));
  const wrapper = setup(); await flushPromises();
  await button(wrapper, "提交文档比较").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("浏览器无法保存比较请求编号"); expect(wrapper.text()).toContain("submission-1");
  await button(wrapper, "查询上次比较提交").trigger("click"); await flushPromises();
  expect(api.getDocumentDifferenceSubmission).toHaveBeenCalledWith(document().project_id, document().id, "submission-1");
});

it("does not submit without usable text and does not replace a selected off-page snapshot on refresh", async () => {
  api.listDocumentParseSnapshots.mockResolvedValue(empty());
  const parent = setup(); await flushPromises();
  expect(button(parent, "提交文档比较").attributes("disabled")).toBeDefined();
  api.listDocumentParseSnapshots.mockResolvedValueOnce({ ...empty(), items: [old()] });
  const wrapper = selector(); await flushPromises(); await wrapper.setProps({ modelValue: old() });
  api.listDocumentParseSnapshots.mockResolvedValue({ ...empty(), items: [{ ...old(), id: "newest-parse" }], has_more: true });
  await button(wrapper, "刷新旧侧快照").trigger("click"); await flushPromises();
  expect(wrapper.emitted("update:modelValue")!.slice(-1)[0]).toEqual([old()]);
  expect(wrapper.find(`option[value="${old().id}"]`).exists()).toBe(true);
});

it("pages snapshots, disables unsupported inputs and preserves a chosen historical snapshot on refresh", async () => {
  const partial = { ...old(), status: "partial", id: "partial" };
  const unsupported = { ...old(), status: "unsupported", id: "unsupported" };
  api.listDocumentParseSnapshots.mockResolvedValueOnce({ ...empty(), items: [unsupported, partial], has_more: true });
  const wrapper = selector(); await flushPromises();
  expect(wrapper.get('option[value="unsupported"]').attributes("disabled")).toBeDefined();
  expect(wrapper.emitted("update:modelValue")!.slice(-1)[0]).toEqual([partial]);
  api.listDocumentParseSnapshots.mockResolvedValueOnce({ ...empty(), offset: 2, items: [old()] });
  await button(wrapper, "更多旧侧快照").trigger("click"); await flushPromises();
  expect(api.listDocumentParseSnapshots).toHaveBeenLastCalledWith(document().project_id, document().id, old().seed.version_id, 2);
  await wrapper.get('select[aria-label="旧侧解析快照"]').setValue(old().id);
  await wrapper.setProps({ modelValue: old() });
  api.listDocumentParseSnapshots.mockResolvedValue({ ...empty(), items: [partial, old()] });
  await button(wrapper, "刷新旧侧快照").trigger("click"); await flushPromises();
  expect(wrapper.emitted("update:modelValue")!.slice(-1)[0]).toEqual([old()]);
});

it("clears unavailable inputs and ignores old snapshot responses after a version switch", async () => {
  let release!: (value: unknown) => void;
  api.listDocumentParseSnapshots.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const wrapper = selector(); await flushPromises();
  api.listDocumentParseSnapshots.mockResolvedValue(empty());
  await wrapper.setProps({ versionId: newer().seed.version_id }); await flushPromises();
  release({ ...empty(), items: [old()] }); await flushPromises();
  expect(wrapper.emitted("update:modelValue")!.slice(-1)[0]).toEqual([null]);
  expect(wrapper.find(`option[value="${old().id}"]`).exists()).toBe(false);
});

it("rejects foreign snapshots and exposes the parser warning from actual DOCX evidence", async () => {
  api.listDocumentParseSnapshots.mockResolvedValue({ ...empty(), items: [{ ...old(), seed: { ...old().seed, project_id: "foreign" } }] });
  const wrapper = selector(); await flushPromises(); expect(wrapper.text()).toContain("不一致");
  await wrapper.setProps({ modelValue: { ...old(), status: "partial" } });
  expect(wrapper.text()).toContain(old().warnings[0]!.message);
});

it("saves all eleven policy fields without changing the original reactive settings and keeps success visible", async () => {
  const wrapper = mount(DocumentDifferenceSettings, { props: { projectId: document().project_id!, record: config(), canWrite: true } }); wrappers.push(wrapper);
  expect(wrapper.findAll('input[type="number"]')).toHaveLength(11);
  await wrapper.get('input[aria-label="比较超时（秒）"]').setValue(75);
  await wrapper.get('form').trigger('submit'); await flushPromises();
  expect(api.saveDocumentDifferenceSettings).toHaveBeenCalledExactlyOnceWith(document().project_id, { policy: { ...config().payload.policy, timeout_seconds: 75 } });
  expect(config().payload.policy.timeout_seconds).not.toBe(75);
  await wrapper.setProps({ record: wrapper.emitted('saved')![0]![0] as DocumentDifferenceSettingsRecord });
  expect(wrapper.text()).toContain("项目比较设置已保存");
});

it.each(["readonly", "foreign", "switched"])("protects settings in the %s case", async scenario => {
  let release!: (value: DocumentDifferenceSettingsRecord) => void;
  if (scenario === "switched") api.saveDocumentDifferenceSettings.mockImplementation(() => new Promise(resolve => { release = resolve; }));
  if (scenario === "foreign") api.saveDocumentDifferenceSettings.mockResolvedValue({ ...config(), scope_key: "foreign" });
  const wrapper = mount(DocumentDifferenceSettings, { props: { projectId: document().project_id!, record: config(), canWrite: scenario !== "readonly" } }); wrappers.push(wrapper);
  await wrapper.get('form').trigger('submit'); await flushPromises();
  if (scenario === "readonly") expect(api.saveDocumentDifferenceSettings).not.toHaveBeenCalled();
  if (scenario === "foreign") { expect(wrapper.text()).toContain("不一致"); expect(wrapper.emitted('saved')).toBeUndefined(); }
  if (scenario === "switched") {
    await wrapper.setProps({ projectId: "next-project", record: { ...config(), scope_key: "next-project" } });
    release({ ...config(), scope_key: document().project_id! }); await flushPromises();
    expect(wrapper.emitted('saved')).toBeUndefined(); expect(wrapper.text()).not.toContain("已保存");
  }
});
