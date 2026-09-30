import { afterEach, describe, expect, it, vi } from "vitest";

import { connectEvents, getSession, getToolJobDetail, resolveApproval } from "./sessions.api";

describe("session API path parameters", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes session and nested IDs as path segments", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);
    const sessionId = "session?part#one";
    const nestedId = "item?part#two";

    await getSession(sessionId);
    await resolveApproval(sessionId, nestedId, "approved");
    await getToolJobDetail(sessionId, nestedId);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/sessions/session%3Fpart%23one",
      "/api/v1/sessions/session%3Fpart%23one/approvals/item%3Fpart%23two",
      "/api/v1/sessions/session%3Fpart%23one/tool-jobs/item%3Fpart%23two",
    ]);
  });

  it("encodes the SSE session path while retaining its query cursor", () => {
    const urls: string[] = [];
    class FakeEventSource {
      constructor(url: string) { urls.push(url); }
    }
    vi.stubGlobal("EventSource", FakeEventSource);

    connectEvents("session?part#one", () => {}, "event?part#two");

    expect(urls).toEqual([
      "/api/v1/sessions/session%3Fpart%23one/events?last_event_id=event%3Fpart%23two",
    ]);
  });
});
