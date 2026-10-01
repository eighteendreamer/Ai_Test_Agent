// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import GeneralSettingsPlugin from "./GeneralSettingsPlugin.vue";

const api = vi.hoisted(() => ({ cleanupData: vi.fn() }));
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
  vi.clearAllMocks();
  api.cleanupData.mockResolvedValue({
    affected_count: 3,
    details: { retained_sessions: 0, total_sessions: 3 },
  });
});

it("sends an explicit all-session scope for cleanup preview and deletion", async () => {
  const wrapper = mount(GeneralSettingsPlugin);
  try {
    await wrapper.findAll(".action-list-item")[2].trigger("click");
    const cleanup = wrapper.findAll(".dm-card")[2];
    const dayButtons = cleanup.findAll(".dm-day-btn");
    await dayButtons[dayButtons.length - 1].trigger("click");
    await cleanup.find(".action-btn.primary").trigger("click");
    await flushPromises();

    expect(api.cleanupData).toHaveBeenCalledWith({
      action: "cleanup", dry_run: true, time_range_days: 0,
    });

    await cleanup.find(".action-btn.danger").trigger("click");
    await flushPromises();

    expect(api.cleanupData).toHaveBeenLastCalledWith({
      action: "cleanup", dry_run: false, time_range_days: 0,
      confirm: true, confirm_all_sessions: true,
    });
  } finally {
    wrapper.unmount();
  }
});
