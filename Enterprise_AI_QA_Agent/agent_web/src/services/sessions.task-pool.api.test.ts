import { describe, expect, it, vi, afterEach } from "vitest";
import { listTaskPoolEntries, listTaskPoolPage } from "./sessions.api";

describe("mixed task-pool HTTP contract", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("encodes literal filters and retains the legacy endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    await listTaskPoolEntries(24, 48, { kind: "case_planning", state: "active", query: "中文 ?/%_", project_id: "project" });
    const url = new URL(fetchMock.mock.calls[0][0], "http://fixture");
    expect(url.pathname).toBe("/api/v1/task-pool/entries");
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: "24", offset: "48", kind: "case_planning", state: "active", query: "中文 ?/%_", project_id: "project" });
    await listTaskPoolPage(2, 3);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/task-pool?limit=2&offset=3");
  });
});
