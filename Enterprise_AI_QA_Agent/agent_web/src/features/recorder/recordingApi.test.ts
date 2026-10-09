import { afterEach, expect, it, vi } from "vitest";

import { createRecordingCaseDraft, getRecordingCaseDraft, listRecordings } from "./recordingApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("creates a recording case draft within the selected project", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    recording_id: "rec-1",
    step_count: 2,
    case: { id: "case-1", project_id: "project/one" },
    version: { id: "version-1", source_refs: [{ source_type: "ui_recording" }] },
  }), { status: 201, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await createRecordingCaseDraft("rec/1", "project/one");

  expect(result.recording_id).toBe("rec-1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/recordings/rec%2F1/case-draft?project_id=project%2Fone",
    { method: "POST", headers: { "Content-Type": "application/json" } },
  );
});

it("surfaces the backend status when draft creation fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({detail: "录制尚未固化"}),
    { status: 409, headers: { "content-type": "application/json" } })));

  await expect(createRecordingCaseDraft("rec-1")).rejects.toThrow(
    "录制尚未固化",
  );
});

it("queries a saved draft without sending a write", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("null", {status: 200}));
  vi.stubGlobal("fetch", fetchMock);
  expect(await getRecordingCaseDraft("rec/1", "project/one")).toBeNull();
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/recordings/rec%2F1/case-draft?project_id=project%2Fone",
    {headers: {"Content-Type": "application/json"}});
});

it("filters recordings by session and project before the server page limit", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response('{"items":[],"count":0}', {status: 200}));
  vi.stubGlobal("fetch", fetchMock);
  await listRecordings(20, {sessionId: "session/one", projectId: "project/one"});
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/recordings?limit=20&session_id=session%2Fone&project_id=project%2Fone");
});
