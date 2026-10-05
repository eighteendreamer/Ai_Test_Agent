import { afterEach, expect, it, vi } from "vitest";
import { getCaseGenerationSubmission, getCaseGenerationTask, getGenerationSettings, listCaseGenerations,
  readCaseGeneration, readCaseGenerationResponse, readCaseGenerationScope, saveGenerationSettings, submitCaseGeneration,
  retryCaseGeneration, getCaseGenerationRetrySubmission, listCaseGenerationRetries } from "./caseGeneration.api";

afterEach(() => vi.unstubAllGlobals());

it("keeps fixed versions and original submission identity across encoded routes", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const payload = { creation_key: "原请求 ?/#", requirement_id: "r", requirement_version_id: "fixed",
    target_case_version_ids: ["old-case"], include_new_cases: true, new_case_mode_key: "api_testing" };
  await submitCaseGeneration("p?#", "d/#", payload);
  await getCaseGenerationSubmission("p?#", "d/#", payload.creation_key);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/p%3F%23/documents/d%2F%23/case-generations");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
  expect(new URL(fetchMock.mock.calls[1][0], "http://localhost").searchParams.get("creation_key")).toBe(payload.creation_key);
});

it("reads fixed groups, modules and raw evidence and saves the full project policy", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const policy = { timeout_seconds: 600, call_timeout_seconds: 120, max_input_bytes: 256000,
    max_response_bytes: 8000000, framing_allowance_tokens: 1024, transport_policy: "single_attempt" as const };
  await getGenerationSettings("p");
  await saveGenerationSettings("p", policy);
  await listCaseGenerations("p", "d", 1, 2);
  await readCaseGeneration("p", "d", "g/#");
  await readCaseGenerationScope("p", "d", "g/#");
  await getCaseGenerationTask("p", "d", "t/?");
  await readCaseGenerationResponse("p", "d", "t/?");
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
    "/api/v1/projects/p/case-generation-settings", "/api/v1/projects/p/case-generation-settings",
    "/api/v1/projects/p/documents/d/case-generations?limit=1&offset=2",
    "/api/v1/projects/p/documents/d/case-generations/g%2F%23",
    "/api/v1/projects/p/documents/d/case-generations/g%2F%23/scope",
    "/api/v1/projects/p/documents/d/case-generation-tasks/t%2F%3F",
    "/api/v1/projects/p/documents/d/case-generation-tasks/t%2F%3F/response",
  ]);
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(policy);
});

it("submits an explicit new task for one parent and retains its request identity for recovery", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const payload = { creation_key: "原重试 ?/#" };
  await retryCaseGeneration("p", "d/#", "parent/?", payload);
  await getCaseGenerationRetrySubmission("p", "d/#", "parent/?", payload.creation_key);
  await listCaseGenerationRetries("p", "d/#", "parent/?", 1, 2);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/p/documents/d%2F%23/case-generation-tasks/parent%2F%3F/retries");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
  expect(new URL(fetchMock.mock.calls[1][0], "http://localhost").searchParams.get("creation_key")).toBe(payload.creation_key);
  expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/projects/p/documents/d%2F%23/case-generation-tasks/parent%2F%3F/retries?limit=1&offset=2");
});
