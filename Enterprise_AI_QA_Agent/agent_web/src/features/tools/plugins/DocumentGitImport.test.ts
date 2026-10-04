// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import DocumentGitImport from "./DocumentGitImport.vue";
import { HttpError } from "../../../services/http";

const service = vi.hoisted(() => ({ getDocumentGitSettings: vi.fn(), saveDocumentGitSettings: vi.fn(),
  resolveDocumentGitSource: vi.fn(), importDocumentGitSource: vi.fn(), getDocumentGitSubmission: vi.fn() }));
const api = vi.hoisted(() => ({ getApiDoc: vi.fn() }));
vi.mock("../../../services/documentGit.api", () => service);
vi.mock("../../../services/api", () => ({ api }));
const wrappers: Array<ReturnType<typeof mount>> = [];
const origin = { kind: "git", repository_id: "repo", repository_name: "repo", repository_root: "G:/fixture",
  config_revision: 1, commit: "a".repeat(40), blob: "b".repeat(40), path: "需求.md", mode: "100644", sha256: "c".repeat(64),
  size_bytes: 12, git_version: "git fixture", reader_policy: {} };
const document = { id: "doc", project_id: "p", current_version_id: "v1", source_origin: origin };
const result = { document, version: { id: "v1", document_id: "doc", version: 1, snapshot: document }, created_new: true, source_origin: origin };
const settings = { scope_key: "p", revision: 1, payload: { repositories: [{ id: "repo", name: "repo", root_path: "G:/fixture", enabled: true }],
  policy: { timeout_seconds: 30, max_cpu_seconds: 20, max_memory_bytes: 536870912, max_blob_bytes: 20000000, max_output_bytes: 40000000 } } };
const cacheKey = "qa-agent:document-git:p:new";
function create(canWrite = true) {
  const wrapper = mount(DocumentGitImport, { props: { projectId: "p", canWrite } });
  wrappers.push(wrapper); return wrapper;
}
async function resolve(wrapper: ReturnType<typeof mount>) {
  await flushPromises();
  await wrapper.get('[aria-label="仓库内文件路径"]').setValue("需求.md");
  await wrapper.findAll("form")[1].trigger("submit"); await flushPromises();
}
function button(wrapper: ReturnType<typeof mount>, label: string) {
  const found = wrapper.findAll("button").find(x => x.text() === label);
  if (!found) throw new Error("missing button " + label); return found;
}
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear();
  service.getDocumentGitSettings.mockResolvedValue(structuredClone(settings));
  service.saveDocumentGitSettings.mockResolvedValue(structuredClone(settings));
  service.resolveDocumentGitSource.mockResolvedValue({ project_id: "p", origin: structuredClone(origin) });
  service.importDocumentGitSource.mockResolvedValue(structuredClone(result));
  service.getDocumentGitSubmission.mockRejectedValue(new HttpError("not found", 404));
  api.getApiDoc.mockResolvedValue(structuredClone(document));
});
afterEach(() => { for (const wrapper of wrappers.splice(0)) wrapper.unmount(); });

it("recovers exact original inputs after dropped response, remount and changed settings; emits current projection", async () => {
  const wrapper = create(); await resolve(wrapper);
  service.importDocumentGitSource.mockRejectedValueOnce(new HttpError("unknown submission", 503));
  await button(wrapper, "导入为新文档").trigger("click"); await flushPromises();
  const original = structuredClone(service.importDocumentGitSource.mock.calls[0][1]);
  expect(original.commit).toBe(origin.commit); expect(original.blob).toBe(origin.blob);
  expect(JSON.parse(sessionStorage.getItem(cacheKey)!).payload).toEqual(original);
  wrapper.unmount(); wrappers.pop();
  service.getDocumentGitSettings.mockResolvedValue({ ...settings, revision: 8, payload: { ...settings.payload, repositories: [] } });
  service.getDocumentGitSubmission.mockResolvedValue(structuredClone(result));
  api.getApiDoc.mockResolvedValue({ ...document, current_version_id: "v2" });
  const reopened = create(); await flushPromises();
  expect(service.getDocumentGitSubmission).toHaveBeenCalledWith("p", original.request_id, undefined);
  expect(reopened.text()).toContain("已保存文档版本 V1");
  expect(reopened.emitted("updated")?.[0]?.[0]).toMatchObject({ current_version_id: "v2" });
  expect(service.importDocumentGitSource).toHaveBeenCalledTimes(1);
});

it("404 recovery retries the original request; 503 cannot start a replacement", async () => {
  const wrapper = create(); await resolve(wrapper);
  service.importDocumentGitSource.mockRejectedValueOnce(new HttpError("unknown", 503));
  await button(wrapper, "导入为新文档").trigger("click"); await flushPromises();
  const original = service.importDocumentGitSource.mock.calls[0][1];
  await button(wrapper, "查询原 Git 提交").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("尚未找到原提交");
  expect(wrapper.findAll("button").some(x => x.text() === "另建一次 Git 导入")).toBe(false);
  await button(wrapper, "重试原 Git 提交").trigger("click"); await flushPromises();
  expect(service.importDocumentGitSource.mock.calls[1][1]).toEqual(original);
});

it("rejects a foreign saved result and does not replace its unknown request", async () => {
  const wrapper = create(); await resolve(wrapper);
  service.importDocumentGitSource.mockResolvedValue({ ...result, source_origin: { ...origin, commit: "d".repeat(40) } });
  await button(wrapper, "导入为新文档").trigger("click"); await flushPromises();
  expect(wrapper.text()).toContain("返回的提交不属于");
  expect(wrapper.emitted("updated")).toBeUndefined();
  expect(sessionStorage.getItem(cacheKey)).not.toBeNull();
});

it("readonly projects keep original submission recovery but expose no new import/settings save", async () => {
  const original = { request_id: "old", repository_id: origin.repository_id, config_revision: 1,
    commit: origin.commit, blob: origin.blob, path: origin.path };
  sessionStorage.setItem(cacheKey, JSON.stringify({ projectId: "p", payload: original }));
  service.getDocumentGitSubmission.mockResolvedValue(result);
  const wrapper = create(false); await flushPromises();
  expect(wrapper.text()).toContain("已保存文档版本 V1");
  expect(wrapper.findAll("button").map(x => x.text())).not.toContain("保存项目 Git 设置");
  expect(service.importDocumentGitSource).not.toHaveBeenCalled();
});

it("discards late source resolution when project changes", async () => {
  let release!: (value: unknown) => void;
  const wrapper = create(); await flushPromises();
  service.resolveDocumentGitSource.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  await wrapper.get('[aria-label="仓库内文件路径"]').setValue("需求.md");
  await wrapper.findAll("form")[1].trigger("submit");
  service.getDocumentGitSettings.mockResolvedValue({ ...settings, scope_key: "other" });
  await wrapper.setProps({ projectId: "other" }); await flushPromises();
  release({ project_id: "p", origin }); await flushPromises();
  expect(wrapper.text()).not.toContain("固定 commit：");
});

it("default settings pass native form constraints for fractional seconds and integer byte limits", async () => {
  const wrapper = create(); await flushPromises();
  for (const field of wrapper.findAll('input[type="number"]')) {
    expect((field.element as HTMLInputElement).checkValidity()).toBe(true);
  }
  expect(wrapper.get('[aria-label="Git 读取超时（秒）"]').attributes("step")).toBe("any");
});
