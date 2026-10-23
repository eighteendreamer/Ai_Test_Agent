// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BusinessEnvironmentManager from "./BusinessEnvironmentManager.vue";
import { HttpError } from "../../services/http";

const api = vi.hoisted(() => ({
  listBusinessEnvironments: vi.fn(), getBusinessEnvironmentRegistration: vi.fn(), registerBusinessEnvironment: vi.fn(),
  readBusinessTargetConfiguration: vi.fn(), saveBusinessTargetConfiguration: vi.fn(),
  listBusinessEnvironmentSnapshots: vi.fn(), getBusinessEnvironmentCapture: vi.fn(), captureBusinessEnvironment: vi.fn(),
  archiveBusinessEnvironment: vi.fn(),
}));
vi.mock("../../services/api", () => ({ api }));

const environment = (requestId = "registered") => ({ id: "environment", project_id: "project",
  request: { request_id: requestId, environment_key: "staging" }, status: "active", created_at: "2026-10-21T00:00:00Z",
  updated_at: "2026-10-21T00:00:00Z", archived_at: null });
const configuration = (baseUrl = "https://staging.example.test", revision = 1) => ({
  config_key: "project.environment_target", project_id: "project", environment_id: "environment", scope_key: "environment",
  revision, payload: { display_name: "Staging", base_url: baseUrl, description: "test target" }, sha256: "b".repeat(64),
});
function setup() { return mount(BusinessEnvironmentManager, { props: { projectId: "project" } }); }
function button(wrapper: ReturnType<typeof setup>, label: string) { return wrapper.findAll("button").find(value => value.text() === label)!; }

describe("project business environment management", () => {
  beforeEach(() => {
    vi.clearAllMocks(); window.sessionStorage.clear();
    api.listBusinessEnvironments.mockImplementation(async (_project, params) => ({
      items: params.status === "active" ? [environment()] : [{ ...environment(), status: "archived" }],
      offset: params.offset, limit: 50, has_more: false,
    }));
    api.getBusinessEnvironmentRegistration.mockRejectedValue(new HttpError("not found", 404));
    api.registerBusinessEnvironment.mockImplementation(async (_project, request) => environment(request.request_id));
    api.readBusinessTargetConfiguration.mockRejectedValue(new HttpError("环境目标尚未配置，请先保存目标设置", 409));
    api.saveBusinessTargetConfiguration.mockImplementation(async (_project, _environmentId, payload) => ({ ...configuration(), payload }));
    api.listBusinessEnvironmentSnapshots.mockResolvedValue({ items: [], offset: 0, limit: 20, has_more: false });
    api.getBusinessEnvironmentCapture.mockRejectedValue(new HttpError("not found", 404));
    api.captureBusinessEnvironment.mockImplementation(async (_project, _environmentId, request) => ({
      id: "snapshot", project_id: "project", environment_id: "environment", environment_key: "staging",
      request, configuration: configuration(), deployment_status: "unknown", captured_at: "2026-10-21T00:00:00Z", sha256: "c".repeat(64),
    }));
  });

  it("registers an environment, saves its target in MySQL config, then captures and reads a fixed snapshot", async () => {
    const wrapper = setup(); await flushPromises();
    expect(api.listBusinessEnvironments).toHaveBeenCalled();
    await wrapper.get("form input").setValue("staging");
    expect((wrapper.get("form input").element as HTMLInputElement).value).toBe("staging");
    expect((wrapper.get("form button").element as HTMLButtonElement).disabled).toBe(false);
    await wrapper.get("form").trigger("submit"); await flushPromises();
    const registration = api.registerBusinessEnvironment.mock.calls[0][1];
    expect(api.getBusinessEnvironmentRegistration).toHaveBeenCalledWith("project", registration.request_id);
    expect(registration.environment_key).toBe("staging");
    expect(api.registerBusinessEnvironment).toHaveBeenCalledWith("project", registration);

    const displayName = wrapper.findAll("label").find(label => label.text().includes("目标显示名称"))!.get("input");
    const baseUrl = wrapper.findAll("label").find(label => label.text().includes("目标地址"))!.get("input");
    await displayName.setValue("Staging target"); await baseUrl.setValue("https://staging.example.test/app");
    await button(wrapper, "保存目标配置").trigger("click"); await flushPromises();
    expect(api.saveBusinessTargetConfiguration).toHaveBeenCalledWith("project", "environment", {
      display_name: "Staging target", base_url: "https://staging.example.test/app", description: "",
    });
    await button(wrapper, "创建配置快照").trigger("click"); await flushPromises();
    const capture = api.captureBusinessEnvironment.mock.calls[0][2];
    expect(api.getBusinessEnvironmentCapture).toHaveBeenCalledWith("project", "environment", capture.request_id);
    expect(capture).toEqual({ request_id: expect.any(String), expected_config_revision: 1, expected_config_sha256: "b".repeat(64) });
    expect(wrapper.text()).toContain("目标配置快照已保存；部署状态仍为未知");
    wrapper.unmount();
  });

  it("re-reads an uncertain configuration write and never blindly resends it", async () => {
    api.readBusinessTargetConfiguration
      .mockResolvedValueOnce(configuration())
      .mockResolvedValueOnce(configuration("https://staging.example.test/changed", 2));
    api.saveBusinessTargetConfiguration.mockRejectedValueOnce(new TypeError("response lost"));
    const wrapper = setup(); await flushPromises();
    await button(wrapper, "staging · active · environment").trigger("click"); await flushPromises();
    const baseUrl = wrapper.findAll("label").find(label => label.text().includes("目标地址"))!.get("input");
    await baseUrl.setValue("https://staging.example.test/changed");
    await button(wrapper, "保存目标配置").trigger("click"); await flushPromises();
    expect(api.saveBusinessTargetConfiguration).toHaveBeenCalledTimes(1);
    expect(api.readBusinessTargetConfiguration).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("保存响应未收到，但已重读并确认当前配置与本次内容一致");
    wrapper.unmount();
  });
});
