import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getDocumentImpactSubmission, getDocumentImpactTask, listDocumentImpactSnapshots,
  listDocumentImpactTasks, readDocumentImpactResult, readDocumentImpactSnapshot, submitDocumentImpactTask,
  getDocumentImpactSettings, getDocumentImpactAnalysisSettings, getDocumentImpactBatchSettings,
  getDocumentImpactInvocationSettings, saveDocumentImpactSettings, saveDocumentImpactAnalysisSettings,
  saveDocumentImpactBatchSettings, saveDocumentImpactInvocationSettings,
  getDocumentImpactReasoningSettings, saveDocumentImpactReasoningSettings,
} from "./documentImpacts.api";

describe("document impact HTTP boundary", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the project reasoning policy route and preserves the complete payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    const payload = { max_group_units: 3, max_group_read_bytes: 20480 };
    await getDocumentImpactReasoningSettings("p?#");
    await saveDocumentImpactReasoningSettings("p?#", payload);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/p%3F%23/document-impact-reasoning-settings");
    expect(fetchMock.mock.calls[1][1].method).toBe("PUT");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(payload);
  });

  it("keeps the fixed selection and original request identity through escaped paths", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({ id: "task" }) });
    vi.stubGlobal("fetch", fetchMock);
    const payload = { creation_key: "重复请求 ?/# 中文", difference_snapshot_id: "fixed", case_version_ids: ["v2", "v1"], environment_key: null };
    await submitDocumentImpactTask("project?#", "doc/#", payload);
    await getDocumentImpactSubmission("project?#", "doc/#", payload.creation_key);
    await getDocumentImpactTask("project?#", "doc/#", "task?#");
    const root = "/api/v1/projects/project%3F%23/documents/doc%2F%23/impact-tasks";
    expect(fetchMock.mock.calls[0][0]).toBe(root);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
    const lookup = new URL(fetchMock.mock.calls[1][0], "http://fixture");
    expect(lookup.pathname).toBe(root + "/by-request");
    expect(lookup.searchParams.get("creation_key")).toBe(payload.creation_key);
    expect(fetchMock.mock.calls[2][0]).toBe(root + "/task%3F%23");
  });

  it("preserves partial, unknown and JSON values when reading historical results", async () => {
    const view = { snapshot: { id: "fixed" }, result: { status: "partial", semantic_status: "pending_review",
      deployment_status: "unknown", units: [{ status: "unknown" }], evidence: [false, 0, null] } };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => view });
    vi.stubGlobal("fetch", fetchMock);
    expect(await readDocumentImpactResult("p", "d", "t?#")).toEqual(view);
    expect(await readDocumentImpactSnapshot("p", "d", "s?#")).toEqual(view);
    await listDocumentImpactTasks("p", "d", "v?#", 20);
    await listDocumentImpactSnapshots("p", "d", undefined, 40);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/p/documents/d/impact-tasks/t%3F%23/result");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/projects/p/documents/d/impact-snapshots/s%3F%23");
    const query = new URL(fetchMock.mock.calls[2][0], "http://fixture");
    expect(query.searchParams.get("version_id")).toBe("v?#");
    expect(query.searchParams.get("offset")).toBe("20");
    expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/projects/p/documents/d/impact-snapshots?limit=20&offset=40");
  });

  it("addresses the four editable policies separately and propagates an unknown submit without retries", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ revision: 1 }) });
    vi.stubGlobal("fetch", fetchMock);
    await getDocumentImpactSettings("p");
    await getDocumentImpactAnalysisSettings("p");
    await getDocumentImpactBatchSettings("p");
    await getDocumentImpactInvocationSettings("p");
    const policies = [
      { max_case_versions: 20, max_context_chars: 1000, timeout_seconds: 120 },
      { model_key: null, analysis_guidance: "完整原文", max_input_bytes: 256000 },
      { max_segment_chars: 12000, max_segments: 100, boundary_context_chars: 500, max_retrieval_bytes: 256000 },
      { max_logical_calls: 10, max_reserved_estimated_tokens: 100000, call_timeout_seconds: 120, max_response_bytes: 10000,
        token_method: "utf8_bytes_estimate" as const, encoding_name: null, framing_allowance_tokens: 1024 },
    ] as const;
    await saveDocumentImpactSettings("p", policies[0]);
    await saveDocumentImpactAnalysisSettings("p", policies[1]);
    await saveDocumentImpactBatchSettings("p", policies[2]);
    await saveDocumentImpactInvocationSettings("p", policies[3]);
    const suffixes = ["settings", "analysis-settings", "batch-settings", "invocation-settings"];
    for (let i = 0; i < 4; i++) {
      expect(fetchMock.mock.calls[i][0]).toBe(`/api/v1/projects/p/document-impact-${suffixes[i]}`);
      expect(fetchMock.mock.calls[i + 4][0]).toBe(fetchMock.mock.calls[i][0]);
      expect(JSON.parse(fetchMock.mock.calls[i + 4][1].body)).toEqual(policies[i]);
    }
    fetchMock.mockClear();
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "提交结果未知，按原请求查询" }), { status: 503 }));
    await expect(submitDocumentImpactTask("p", "d", { creation_key: "same", difference_snapshot_id: "fixed" })).rejects.toThrow("提交结果未知");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
