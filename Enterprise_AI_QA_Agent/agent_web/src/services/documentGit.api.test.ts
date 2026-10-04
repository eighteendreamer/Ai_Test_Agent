import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getDocumentGitSettings, getDocumentGitSubmission, importDocumentGitSource,
  resolveDocumentGitSource, saveDocumentGitSettings,
} from "./documentGit.api";
import type { GitDocumentSettings } from "../types";

describe("project Git document HTTP contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("separates project registration from fixed import and exact original submission lookup", async () => {
    const mock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal("fetch", mock);
    const root = "/api/v1/projects/project%3F%23";
    await getDocumentGitSettings("project?#");
    const settings: GitDocumentSettings = { repositories: [], policy: { timeout_seconds: 30,
      max_cpu_seconds: 20, max_memory_bytes: 536870912, max_blob_bytes: 20000000, max_output_bytes: 40000000 } };
    await saveDocumentGitSettings("project?#", settings);
    const resolve = { repository_id: "repo", reference: "main", path: "需求 文档/规则[1].md" };
    await resolveDocumentGitSource("project?#", resolve);
    const payload = { request_id: "same ?/# 中文", repository_id: "repo", config_revision: 3,
      commit: "a".repeat(40), blob: "b".repeat(40), path: resolve.path,
      document_id: "target?#", expected_parent_version_id: "parent" };
    await importDocumentGitSource("project?#", payload);
    await getDocumentGitSubmission("project?#", payload.request_id, payload.document_id);
    await getDocumentGitSubmission("project?#", payload.request_id);
    expect(mock.mock.calls[0][0]).toBe(root + "/document-git-settings");
    expect(mock.mock.calls[1][1].method).toBe("PUT");
    expect(JSON.parse(mock.mock.calls[1][1].body)).toEqual(settings);
    expect(mock.mock.calls[2][0]).toBe(root + "/document-git/resolve");
    expect(JSON.parse(mock.mock.calls[2][1].body)).toEqual(resolve);
    expect(mock.mock.calls[3][0]).toBe(root + "/document-git/import");
    expect(JSON.parse(mock.mock.calls[3][1].body)).toEqual(payload);
    const lookup = new URL(mock.mock.calls[4][0], "http://fixture");
    expect(lookup.pathname).toBe(root + "/document-git/submissions");
    expect(lookup.searchParams.get("request_id")).toBe(payload.request_id);
    expect(lookup.searchParams.get("document_id")).toBe(payload.document_id);
    expect(new URL(mock.mock.calls[5][0], "http://fixture").searchParams.has("document_id")).toBe(false);
  });
});
