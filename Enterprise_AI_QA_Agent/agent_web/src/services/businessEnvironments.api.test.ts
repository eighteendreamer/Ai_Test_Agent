import { afterEach, expect, it, vi } from "vitest";
import * as environments from "./businessEnvironments.api";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("preserves the original capture selection and encodes project, environment and request identities", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const registration = { request_id: "登记 ?/#", environment_key: "stage" };
  const capture = { request_id: "捕获 ?/#", expected_config_revision: 3, expected_config_sha256: "a".repeat(64) };
  const config = { display_name: "选定目标", base_url: "https://example.com/app" };
  await environments.registerBusinessEnvironment("p/#", registration);
  await environments.getBusinessEnvironmentRegistration("p/#", registration.request_id);
  await environments.getBusinessEnvironment("p/#", "e?");
  await environments.saveBusinessTargetConfiguration("p/#", "e?", config);
  await environments.readBusinessTargetConfiguration("p/#", "e?");
  await environments.captureBusinessEnvironment("p/#", "e?", capture);
  await environments.getBusinessEnvironmentCapture("p/#", "e?", capture.request_id);
  await environments.getBusinessEnvironmentSnapshot("p/#", "e?", "s/#");
  await environments.listBusinessEnvironmentSnapshots("p/#", "e?", 1, 2);
  await environments.archiveBusinessEnvironment("p/#", "e?");
  await environments.listBusinessEnvironments("p/#", { status: "archived", limit: 1, offset: 2 });
  const base = "/api/v1/projects/p%2F%23/business-environments";
  expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
    base, `${base}/by-request?${new URLSearchParams({ request_id: registration.request_id })}`,
    `${base}/e%3F`, `${base}/e%3F/configuration`, `${base}/e%3F/configuration`,
    `${base}/e%3F/snapshots`, `${base}/e%3F/snapshots/by-request?${new URLSearchParams({ request_id: capture.request_id })}`,
    `${base}/e%3F/snapshots/s%2F%23`, `${base}/e%3F/snapshots?limit=1&offset=2`,
    `${base}/e%3F/archive`, `${base}?limit=1&offset=2&status=archived`,
  ]);
  expect(JSON.parse(fetchMock.mock.calls[5]![1].body)).toEqual(capture);
  expect(fetchMock.mock.calls[5]![1].method).toBe("POST");
  expect(JSON.parse(fetchMock.mock.calls[3]![1].body)).toEqual(config);
  expect(fetchMock.mock.calls[3]![1].method).toBe("PUT");
  expect(api.captureBusinessEnvironment).toBe(environments.captureBusinessEnvironment);
});

it("does not auto resend a capture after a lost response or conflict", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new Error("actual response unknown"));
  vi.stubGlobal("fetch", fetchMock);
  const payload = { request_id: "original", expected_config_revision: 1, expected_config_sha256: "b".repeat(64) };
  await expect(environments.captureBusinessEnvironment("p", "e", payload)).rejects.toThrow("unknown");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValue({ ok: false, status: 409, headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({ detail: "目标配置已变化" }) });
  await expect(environments.captureBusinessEnvironment("p", "e", payload)).rejects.toMatchObject({ status: 409 });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
