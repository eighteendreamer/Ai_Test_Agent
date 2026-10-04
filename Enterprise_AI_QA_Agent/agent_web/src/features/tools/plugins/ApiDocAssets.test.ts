// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ApiDocAssets from "./ApiDocAssets.vue";
import type { ApiDocRecord, ApiDocumentAsset } from "../../../types";

const api = vi.hoisted(() => ({ downloadApiDocAsset: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
vi.mock("../../../services/i18n", () => ({ t: (key: string) => key }));

function asset(id: string, kind: "original" | "derived"): ApiDocumentAsset {
  return { id, kind, filename: `${id}.json`, size_bytes: 3, sha256: "a".repeat(64),
    content_type: "application/json", storage_uri: `rustfs://tests/${id}`,
    created_at: "2026-10-04T00:00:00Z", reason: "upload", security_report: {} };
}

function document(original = true): ApiDocRecord {
  return { id: "document", title: "Uploaded document", filename: "current.md", source: "manual_upload",
    format_label: "Markdown", content_type: "text/markdown", size_bytes: 3, storage_uri: "rustfs://current",
    preview_available: true, preview_truncated: false, uploaded_at: "2026-10-04T00:00:00Z",
    updated_at: "2026-10-04T00:00:00Z", metadata: {}, original_asset: original ? asset("original", "original") : null,
    derived_assets: [asset("derived", "derived")] };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:download"), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  api.downloadApiDocAsset.mockResolvedValue(new Blob(["raw"]));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("downloads the original and a retained derived asset by their own identities", async () => {
  const wrapper = mount(ApiDocAssets, { props: { document: document() } });
  await wrapper.findAll("button")[0].trigger("click");
  await flushPromises();
  expect(api.downloadApiDocAsset).toHaveBeenLastCalledWith("document", "original");
  await wrapper.findAll("button")[1].trigger("click");
  await flushPromises();
  expect(api.downloadApiDocAsset).toHaveBeenLastCalledWith("document", "derived");
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  wrapper.unmount();
});

it("shows a missing original without reclassifying current derived content", () => {
  const wrapper = mount(ApiDocAssets, { props: { document: document(false) } });
  expect(wrapper.text()).toContain("apiDocs.original_missing");
  expect(wrapper.findAll("button")).toHaveLength(1);
  wrapper.unmount();
});

it("shows a failed download and enables retry without creating a download", async () => {
  api.downloadApiDocAsset.mockRejectedValue(new Error("asset checksum mismatch"));
  const wrapper = mount(ApiDocAssets, { props: { document: document() } });
  await wrapper.findAll("button")[0].trigger("click");
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain("checksum mismatch");
  expect(wrapper.findAll("button")[0].attributes("disabled")).toBeUndefined();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  wrapper.unmount();
});
