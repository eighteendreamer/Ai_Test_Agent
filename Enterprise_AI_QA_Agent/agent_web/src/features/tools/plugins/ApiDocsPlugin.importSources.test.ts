// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { beforeEach, expect, it, vi } from "vitest";
import type { IntegrationImportSourcesResponse, IntegrationRecord } from "../../../types";
import ApiDocsPlugin from "./ApiDocsPlugin.vue";

const api = vi.hoisted(() => ({
  listApiDocs: vi.fn(),
  listIntegrations: vi.fn(),
  listProjects: vi.fn(),
  listIntegrationImportSources: vi.fn(),
  importApiDocFromIntegration: vi.fn(),
}));
vi.mock("../../../services/api", () => ({ api }));
vi.mock("naive-ui", () => ({
  useMessage: () => ({ error: vi.fn(), success: vi.fn() }),
  NSelect: { template: "<div />" },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((ok) => { resolve = ok; });
  return { promise, resolve };
}

function integration(id: string): IntegrationRecord {
  return {
    id,
    name: `Integration ${id}`,
    kind: "mcp",
    enabled: true,
    capabilities: [],
    headers: {},
    env: {},
    auth_type: "none",
    auth_config: {},
    metadata: {},
    created_at: "2026-09-30T00:00:00Z",
    updated_at: "2026-09-30T00:00:00Z",
  };
}

function catalog(id: string, workspaceId = ""): IntegrationImportSourcesResponse {
  return {
    integration_id: id,
    kind: "mcp",
    supports_workspace_selection: Boolean(workspaceId),
    workspaces: workspaceId ? [{ id: workspaceId, name: workspaceId, document_count: 1 }] : [],
    sources: [{ id: `${id}-${workspaceId || "source"}`, label: `${id} source`, document_url: "https://example.test/openapi.json", kind: "openapi", workspace_id: workspaceId || null }],
  };
}

async function openIntegrationImport(wrapper: ReturnType<typeof mount>) {
  window.dispatchEvent(new Event("qa-agent:open-api-doc-upload"));
  await nextTick();
  await wrapper.findAll(".import-mode-tabs button")[2].trigger("click");
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listApiDocs.mockResolvedValue([]);
  api.listIntegrations.mockResolvedValue([integration("a"), integration("b")]);
  api.listProjects.mockResolvedValue({ items: [] });
  api.importApiDocFromIntegration.mockImplementation(() => new Promise(() => {}));
});

it("does not import a source from the previous integration after responses arrive out of order", async () => {
  const old = deferred<IntegrationImportSourcesResponse>();
  const current = deferred<IntegrationImportSourcesResponse>();
  api.listIntegrationImportSources.mockImplementation((id: string) => id === "a" ? old.promise : current.promise);
  const wrapper = mount(ApiDocsPlugin);
  try {
    await flushPromises();
    await openIntegrationImport(wrapper);
    await wrapper.findAll(".upload-modal select")[0].setValue("a");
    await wrapper.findAll(".upload-modal select")[0].setValue("b");
    current.resolve(catalog("b"));
    await flushPromises();
    old.resolve(catalog("a"));
    await flushPromises();

    expect(wrapper.find(".upload-modal").text()).toContain("b source");
    expect(wrapper.find(".upload-modal").text()).not.toContain("a source");
    await wrapper.get(".upload-modal .modal-actions .primary-btn").trigger("click");
    expect(api.importApiDocFromIntegration).toHaveBeenCalledWith(expect.objectContaining({
      integration_id: "b",
      import_source_id: "b-source",
    }));
  } finally {
    wrapper.unmount();
  }
});

it("ignores a previous integration's workspace response after selecting another integration", async () => {
  const oldWorkspace = deferred<IntegrationImportSourcesResponse>();
  api.listIntegrationImportSources.mockImplementation((id: string, workspaceId: string | null) => {
    if (id === "b") return Promise.resolve(catalog("b"));
    if (workspaceId === "w1") return oldWorkspace.promise;
    return Promise.resolve({ ...catalog("a"), supports_workspace_selection: true,
      workspaces: [{ id: "w1", name: "w1", document_count: 1 }], sources: [] });
  });
  const wrapper = mount(ApiDocsPlugin);
  try {
    await flushPromises();
    await openIntegrationImport(wrapper);
    await wrapper.findAll(".upload-modal select")[0].setValue("a");
    await flushPromises();
    expect(api.listIntegrationImportSources).toHaveBeenCalledWith("a", "w1");
    await wrapper.findAll(".upload-modal select")[0].setValue("b");
    await flushPromises();
    oldWorkspace.resolve(catalog("a", "w1"));
    await flushPromises();

    expect(wrapper.find(".upload-modal").text()).toContain("b source");
    expect(wrapper.find(".upload-modal").text()).not.toContain("a source");
    expect(api.listIntegrationImportSources).toHaveBeenCalledWith("b", null);
  } finally {
    wrapper.unmount();
  }
});
