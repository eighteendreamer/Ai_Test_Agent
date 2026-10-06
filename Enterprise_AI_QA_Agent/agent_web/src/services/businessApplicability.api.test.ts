import { afterEach, expect, it, vi } from "vitest";
import * as decisions from "./businessApplicability.api";
import { api } from "./api";
import type { BusinessApplicabilityRequest } from "./businessApplicability.types";

afterEach(() => vi.unstubAllGlobals());

const selected: BusinessApplicabilityRequest = {
  request_id: "原请求 ?/#", document_id: "document", requirement_id: "requirement", requirement_version_id: "version",
  expected_snapshot_sha256: "a".repeat(64), expected_requirement_sha256: "b".repeat(64),
  coverage_id: "initial", expected_coverage_sha256: "b".repeat(64), note: "缺来源",
  points: [{ point_key: "retention", status: "unknown", note: "待查", observation_indexes: [] }],
  cases: [], observations: [],
};

it("preserves the exact scope and original request on submit and history reads", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({
    request: selected, scope: "selected_points_and_case_versions", observations: [{ value: false }],
  }) });
  vi.stubGlobal("fetch", fetchMock);
  const base = "/api/v1/projects/p%2F%23/business-environments/e%3F/snapshots/s%2F%23/applicability-reviews";
  const result = await decisions.submitBusinessApplicability("p/#", "e?", "s/#", selected);
  await decisions.getBusinessApplicabilityByRequest("p/#", "e?", "s/#", selected.request_id);
  await decisions.getBusinessApplicability("p/#", "e?", "s/#", "r/#");
  await decisions.listBusinessApplicability("p/#", "e?", "s/#", "v?", 1, 2);
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual([base,
    `${base}/by-request?${new URLSearchParams({ request_id: selected.request_id })}`, `${base}/r%2F%23`,
    `${base}?${new URLSearchParams({ requirement_version_id: "v?", limit: "1", offset: "2" })}`]);
  expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual(selected);
  expect(result.observations[0]!.value).toBe(false);
  expect(api.submitBusinessApplicability).toBe(decisions.submitBusinessApplicability);
});

it("does not resend or turn an unknown reply into a changed confirmation", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new Error("response unknown"));
  vi.stubGlobal("fetch", fetchMock);
  await expect(decisions.submitBusinessApplicability("p", "e", "s", selected)).rejects.toThrow("unknown");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValue({ ok: false, status: 409, headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({ detail: "确认已变化" }) });
  await expect(decisions.submitBusinessApplicability("p", "e", "s", selected)).rejects.toMatchObject({ status: 409 });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual(selected);
});
