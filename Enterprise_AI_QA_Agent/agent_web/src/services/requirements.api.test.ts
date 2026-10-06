import { afterEach, expect, it, vi } from "vitest";
import { getRequirementReviewSubmission, listRequirementReviews, listRequirements, listRequirementVersions, readRequirementMatrix, submitRequirementReview,
  getRequirementCoverageSubmission, listRequirementCoverage, submitRequirementCoverage,
  getRequirementExecutionSubmission, listRequirementExecution, listRequirementExecutionCandidates, submitRequirementExecution } from "./requirements.api";

afterEach(() => vi.unstubAllGlobals());

it("preserves fixed execution evidence, original recovery identity and history selectors", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const payload = { request_id: "执行 ?/#", coverage_id: "fixed-coverage", expected_execution_id: "previous", note: "原核验",
    checks: [{ result_id: "original-result", verification_id: "body-check", check_index: 3, point_keys: ["boundary"] }] };
  await submitRequirementExecution("p/#", "d?", "r/#", "v?", payload);
  await getRequirementExecutionSubmission("p/#", "d?", payload.request_id);
  await listRequirementExecution("p", "d", "r", "v", 1, 2);
  await listRequirementExecutionCandidates("p", "d", "r", "v", "coverage ?/#", 1, 2);
  await readRequirementMatrix("p", "d", "r", "v", "fixed-coverage", "initial");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/p%2F%23/documents/d%3F/requirements/r%2F%23/versions/v%3F/execution-reviews");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
  expect(new URL(fetchMock.mock.calls[1][0], "http://localhost").searchParams.get("request_id")).toBe(payload.request_id);
  expect(fetchMock.mock.calls[2][0]).toContain("/execution-reviews?limit=1&offset=2");
  expect(new URL(fetchMock.mock.calls[3][0], "http://localhost").searchParams.get("coverage_id")).toBe("coverage ?/#");
  expect(fetchMock.mock.calls[4][0]).toContain("coverage_id=fixed-coverage&execution_id=initial");
});

it("preserves the explicit review and original request identity across encoded routes", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const payload = { request_id: "原请求 ?/#", rule_id: "fixed", decision: "accept" as const, note: "已核对原文", change: "modified" as const,
    title: "保留期", statement: "保留九天", points: [{ key: "days", description: "验证九天" }], case_links: [] };
  await submitRequirementReview("p?#", "d/#", "s/?", payload);
  await getRequirementReviewSubmission("p?#", "d/#", payload.request_id);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/p%3F%23/documents/d%2F%23/impact-snapshots/s%2F%3F/reviews");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
  expect(new URL(fetchMock.mock.calls[1][0], "http://localhost").searchParams.get("request_id")).toBe(payload.request_id);
});

it("binds coverage to an explicit version, preserves pending identity and selects original history", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const payload = { request_id: "覆盖 ?/#", expected_coverage_id: "prior", note: "已核对完整用例",
    case_links: [{ case_version_id: "fixed", content_hash: "hash", point_keys: ["days"], generation_task_id: "original-task" }] };
  await submitRequirementCoverage("p/#", "d?", "r/#", "v?", payload);
  await getRequirementCoverageSubmission("p/#", "d?", payload.request_id);
  await listRequirementCoverage("p", "d", "r", "v", 1, 2);
  await readRequirementMatrix("p", "d", "r", "v", "initial");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/projects/p%2F%23/documents/d%3F/requirements/r%2F%23/versions/v%3F/coverage-reviews");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(payload);
  expect(new URL(fetchMock.mock.calls[1][0], "http://localhost").searchParams.get("request_id")).toBe(payload.request_id);
  expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/projects/p/documents/d/requirements/r/versions/v/coverage-reviews?limit=1&offset=2");
  expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/projects/p/documents/d/requirements/r/matrix?version_id=v&coverage_id=initial");
});

it("selects explicit history and paginates reviews independently from requirements", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  await listRequirementReviews("p", "d", "s", 1, 2);
  await listRequirements("p", "d", 3, 4);
  await listRequirementVersions("p", "d", "r/#");
  await readRequirementMatrix("p", "d", "r/#", "old ?/#");
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
    "/api/v1/projects/p/documents/d/impact-snapshots/s/reviews?limit=1&offset=2",
    "/api/v1/projects/p/documents/d/requirements?limit=3&offset=4",
    "/api/v1/projects/p/documents/d/requirements/r%2F%23/versions",
    "/api/v1/projects/p/documents/d/requirements/r%2F%23/matrix?version_id=old+%3F%2F%23",
  ]);
});
