// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ApiDocVersions from "./ApiDocVersions.vue";
import type { ApiDocRecord, ApiDocVersionRecord } from "../../../types";
import { prepareDocumentRequest } from "./documentUploadRequest";

const api = vi.hoisted(() => ({ listApiDocVersions: vi.fn(), readApiDocVersionContent: vi.fn(), getApiDoc: vi.fn(),
  uploadApiDocVersion: vi.fn(), initializeApiDocHistory: vi.fn(), getApiDocVersionSubmission: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
vi.mock("../../../services/i18n", () => ({ t: (key: string) => key }));

function document(version = 1): ApiDocRecord {
  return { id: "document", project_id: "project", current_version_id: `version-${version}`, version_number: version,
    catalog_revision: version, title: `V${version} title`, filename: "requirements.md", source: "manual_upload",
    format_label: "Markdown", content_type: "text/markdown", size_bytes: 3, storage_uri: `rustfs://current/${version}`,
    preview_available: true, preview_truncated: false, uploaded_at: "2026-10-04T00:00:00Z",
    updated_at: "2026-10-04T00:00:00Z", metadata: {} };
}
function version(number = 1): ApiDocVersionRecord {
  return { id: `version-${number}`, document_id: "document", version: number,
    created_at: "2026-10-04T00:00:00Z", snapshot: document(number) };
}
function button(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll("button").find(node => node.text() === label)!;
}
async function chooseFile(wrapper: ReturnType<typeof mount>) {
  const input = wrapper.get('input[type="file"]');
  Object.defineProperty(input.element, "files", { configurable: true, value: [new File(["# v2"], "new.md")] });
  await input.trigger("change");
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  // Control the identity boundary here; real Web Crypto is exercised in Chromium.
  vi.stubGlobal("crypto", { randomUUID: () => "controlled-request",
    subtle: { digest: async (_algorithm: string, data: Uint8Array) => data.buffer } });
  api.listApiDocVersions.mockResolvedValue([version()]);
  api.getApiDoc.mockResolvedValue(document(3));
});
afterEach(() => vi.unstubAllGlobals());

it("restores the original request and parent after uncertain upload and refresh; emits the actual current document", async () => {
  const props = { document: document(), canWrite: true };
  let wrapper = mount(ApiDocVersions, { props });
  await flushPromises();
  await chooseFile(wrapper);
  api.uploadApiDocVersion.mockRejectedValueOnce(new Error("controlled response lost"));
  await button(wrapper, "apiDocs.upload_new_version").trigger("click");
  await vi.waitFor(() => expect(api.uploadApiDocVersion).toHaveBeenCalledOnce());
  await flushPromises();
  const original = api.uploadApiDocVersion.mock.calls[0][1];
  expect(original.expected_parent_version_id).toBe("version-1");
  expect(wrapper.get('[role="alert"]').text()).toContain("response lost");
  wrapper.unmount();
  wrapper = mount(ApiDocVersions, { props: { ...props, document: document(3) } });
  await flushPromises();
  await chooseFile(wrapper);
  api.uploadApiDocVersion.mockResolvedValue({ document: document(2), version: version(2), created_new: true });
  await button(wrapper, "apiDocs.upload_new_version").trigger("click");
  await vi.waitFor(() => expect(wrapper.emitted("updated")).toBeTruthy());
  expect(api.uploadApiDocVersion.mock.calls[1][1]).toEqual(original);
  expect(wrapper.emitted("updated")![0][0]).toEqual(document(3));
  expect(sessionStorage.getItem("qa-agent:api-doc-version:document")).toBeNull();
  wrapper.unmount();
});

it("finds a saved submission without resending the file and reads a selected historical snapshot", async () => {
  await prepareDocumentRequest("qa-agent:api-doc-version:document", { example: "already sent" },
    { operation: "version", project_id: "project", expected_parent_version_id: "version-1" });
  api.getApiDocVersionSubmission.mockResolvedValue({ document: document(2), version: version(2), created_new: true });
  const wrapper = mount(ApiDocVersions, { props: { document: document(3), canWrite: true } });
  await flushPromises();
  expect(wrapper.get('input[type="file"]').attributes("accept")).toContain(".docx");
  await button(wrapper, "apiDocs.find_submission").trigger("click");
  await flushPromises();
  expect(api.uploadApiDocVersion).not.toHaveBeenCalled();
  api.readApiDocVersionContent.mockResolvedValue({ document: document(), content: "old historical rule", full_length: 19, truncated: false });
  await button(wrapper, "apiDocs.view_version V1").trigger("click");
  await flushPromises();
  expect(api.readApiDocVersionContent).toHaveBeenCalledWith("document", "version-1");
  expect(wrapper.get(".document-version-preview pre").text()).toBe("old historical rule");
  wrapper.unmount();
});

it("rejects changed retry parameters before creating a new submission identity", async () => {
  const key = "qa-agent:api-doc-version:document";
  const first = await prepareDocumentRequest(key, { content: "original" }, { operation: "version", project_id: "project" });
  await expect(prepareDocumentRequest(key, { content: "changed" }, { operation: "version", project_id: "project" }))
    .rejects.toThrow("apiDocs.pending_changed");
  expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(first);
});
