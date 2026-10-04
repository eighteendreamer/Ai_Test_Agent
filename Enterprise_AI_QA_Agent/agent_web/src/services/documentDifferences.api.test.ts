import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getDocumentDifferenceTask, getDocumentDifferenceSubmission, getDocumentDifferenceSettings,
  listDocumentDifferenceTasks, listDocumentDifferenceSnapshots, readDocumentDifferenceSnapshot,
  readDocumentDifferenceFailure, saveDocumentDifferenceSettings, submitDocumentDifferenceTask,
} from "./documentDifferences.api";
import type { DocumentDifferenceSettings } from "../types";

describe("document comparison HTTP contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("preserves exact ordered snapshot references and the submission identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({ id: "task" }) });
    vi.stubGlobal("fetch", fetchMock);
    const payload = { creation_key: "retry ?/# 中文", old_snapshot_id: "old", new_snapshot_id: "new" };
    expect((await submitDocumentDifferenceTask("project?#", "doc/#", payload)).id).toBe("task");
    await getDocumentDifferenceTask("project?#", "doc/#", "task?#");
    await getDocumentDifferenceSubmission("project?#", "doc/#", payload.creation_key);
    const root = "/api/v1/projects/project%3F%23/documents/doc%2F%23/difference-tasks";
    expect(fetchMock.mock.calls[0][0]).toBe(root);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
    expect(fetchMock.mock.calls[1][0]).toBe(root + "/task%3F%23");
    const lookup = new URL(fetchMock.mock.calls[2][0], "http://fixture");
    expect(lookup.pathname).toBe(root + "/by-request");
    expect(lookup.searchParams.get("creation_key")).toBe(payload.creation_key);
  });

  it("uses new-side version filters and keeps history/failure/settings endpoints separate", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ warnings: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    const root = "/api/v1/projects/project/documents/doc";
    await listDocumentDifferenceTasks("project", "doc", "new?#", 20);
    await listDocumentDifferenceSnapshots("project", "doc", undefined, 40);
    await readDocumentDifferenceSnapshot("project", "doc", "snapshot?#");
    await readDocumentDifferenceFailure("project", "doc", "task?#");
    await getDocumentDifferenceSettings("project");
    const query = new URL(fetchMock.mock.calls[0][0], "http://fixture");
    expect(query.searchParams.get("version_id")).toBe("new?#");
    expect(query.searchParams.get("offset")).toBe("20");
    expect(fetchMock.mock.calls[1][0]).toBe(root + "/difference-snapshots?limit=20&offset=40");
    expect(fetchMock.mock.calls[2][0]).toBe(root + "/difference-snapshots/snapshot%3F%23");
    expect(fetchMock.mock.calls[3][0]).toBe(root + "/difference-tasks/task%3F%23/failure");
    expect(fetchMock.mock.calls[4][0]).toBe("/api/v1/projects/project/document-difference-settings");
    const payload: DocumentDifferenceSettings = { policy: {
      max_input_bytes: 1000, max_text_chars: 2000, max_lines: 100, max_structure_items: 10,
      max_changes: 20, max_character_pair_product: 10000, max_character_detail_chars: 100,
      timeout_seconds: 70, max_cpu_seconds: 30, max_memory_bytes: 536870912, max_output_bytes: 40000000,
    } };
    await saveDocumentDifferenceSettings("project", payload);
    expect(fetchMock.mock.calls[5][1].method).toBe("PUT");
    expect(JSON.parse(fetchMock.mock.calls[5][1].body)).toEqual(payload);
  });

  it("exposes unavailable submission errors without silently changing input or retry identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "提交结果未知，请按原请求查询" }), {
      status: 503, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(submitDocumentDifferenceTask("project", "doc", {
      creation_key: "same", old_snapshot_id: "old", new_snapshot_id: "new",
    })).rejects.toThrow("提交结果未知");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
