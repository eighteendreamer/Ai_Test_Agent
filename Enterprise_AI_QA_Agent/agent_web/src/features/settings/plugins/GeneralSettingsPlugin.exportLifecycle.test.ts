// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, KeepAlive, nextTick, ref } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import GeneralSettingsPlugin from "./GeneralSettingsPlugin.vue";

const api = vi.hoisted(() => ({
  exportPreview: vi.fn(),
  exportStart: vi.fn(),
  exportProgress: vi.fn(),
  exportDownload: vi.fn(),
}));
const settingsStore = vi.hoisted(() => ({
  settingsSnapshot: {
    language: "zh-CN",
    modelOutputLanguage: "follow-system",
    notifySessionCompleteWhenAway: true,
    notifyApprovalRequiredWhenAway: true,
    notificationsAwayOnly: true,
    fontFamily: "system",
    fontSize: "standard",
    reduceMotion: false,
    lastSavedAt: "",
  },
  notificationPermissionStatus: "unsupported",
  lastSavedAt: "",
  previewAppearance: vi.fn(),
  restoreAppearance: vi.fn(),
  saveGeneralSettings: vi.fn(),
  resetGeneralSettings: vi.fn(),
}));

vi.mock("../../../services/api", () => ({ api }));
vi.mock("../../../stores/generalSettings", () => ({
  useGeneralSettingsStore: () => settingsStore,
}));
vi.mock("naive-ui", () => ({
  NModal: { template: "<div><slot /></div>" },
}));
vi.mock("../../../components/common/DropdownSelect.vue", () => ({
  default: { template: "<div />" },
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  api.exportPreview.mockResolvedValue({ ok: true, session_count: 1 });
  api.exportStart.mockResolvedValue({ ok: true, task_id: "export-1", total: 1 });
  api.exportProgress.mockResolvedValue({ progress: 0, total: 1, status: "running" });
  api.exportDownload.mockResolvedValue(undefined);
});
afterEach(() => vi.useRealTimers());

async function startExport(wrapper: ReturnType<typeof mount>) {
  await wrapper.findAll(".action-list-item")[0].trigger("click");
  await flushPromises();
  await wrapper.findAll(".dm-card")[0].find(".action-btn.primary").trigger("click");
  await flushPromises();
  expect(api.exportStart).toHaveBeenCalledOnce();
}

it("pauses export polling while cached and resumes the same task on return", async () => {
  const visible = ref(true);
  const Other = defineComponent({ render: () => h("div", "Other plugin") });
  const Host = defineComponent({
    render: () => h(KeepAlive, null, {
      default: () => visible.value ? h(GeneralSettingsPlugin) : h(Other),
    }),
  });
  const wrapper = mount(Host);
  try {
    await startExport(wrapper);
    expect(vi.getTimerCount()).toBe(1);

    visible.value = false;
    await nextTick();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(api.exportProgress).not.toHaveBeenCalled();

    visible.value = true;
    await nextTick();
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(400);
    expect(api.exportProgress).toHaveBeenCalledWith("export-1");
    expect(api.exportStart).toHaveBeenCalledOnce();

    api.exportProgress.mockResolvedValue({ progress: 1, total: 1, status: "done" });
    await vi.advanceTimersByTimeAsync(400);
    await flushPromises();
    expect(api.exportDownload).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    wrapper.unmount();
  }
  expect(vi.getTimerCount()).toBe(0);
});

it("ignores an export result that arrives after the settings plugin is destroyed", async () => {
  let resolveProgress!: (value: { progress: number; total: number; status: string }) => void;
  api.exportProgress.mockImplementationOnce(() => new Promise((resolve) => {
    resolveProgress = resolve;
  }));
  const wrapper = mount(GeneralSettingsPlugin);
  await startExport(wrapper);
  await vi.advanceTimersByTimeAsync(400);
  expect(api.exportProgress).toHaveBeenCalledOnce();

  wrapper.unmount();
  resolveProgress({ progress: 1, total: 1, status: "done" });
  await flushPromises();

  expect(api.exportDownload).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it("waits for each progress response before scheduling another poll", async () => {
  let resolveProgress!: (value: { progress: number; total: number; status: string }) => void;
  api.exportProgress.mockImplementationOnce(() => new Promise((resolve) => {
    resolveProgress = resolve;
  }));
  const wrapper = mount(GeneralSettingsPlugin);
  try {
    await startExport(wrapper);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(api.exportProgress).toHaveBeenCalledOnce();
    resolveProgress({ progress: 0, total: 1, status: "running" });
    await flushPromises();
    expect(vi.getTimerCount()).toBe(1);
  } finally {
    wrapper.unmount();
  }
  expect(vi.getTimerCount()).toBe(0);
});
