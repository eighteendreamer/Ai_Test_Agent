// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, KeepAlive, nextTick, ref } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { channelOrder } from "../channelStrategies";
import ChannelSettingsPlugin from "./ChannelSettingsPlugin.vue";

const api = vi.hoisted(() => ({
  listChannelConfigs: vi.fn(),
  getActiveChannelPairing: vi.fn(),
  getChannelPairing: vi.fn(),
}));

vi.mock("../../../services/api", () => ({ api }));
vi.mock("naive-ui", () => ({
  useMessage: () => ({ success: vi.fn(), error: vi.fn() }),
}));
vi.mock("./ChannelAdvancedSettingsModal.vue", () => ({
  default: { template: "<div />" },
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  api.listChannelConfigs.mockResolvedValue([]);
  api.getActiveChannelPairing.mockImplementation(async () => ({
    session_id: "pairing-1",
    status: "pending",
    qr_payload: "data:image/png;base64,AA==",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    destroy_at: new Date(Date.now() + 60_000).toISOString(),
    interval: 2,
  }));
  api.getChannelPairing.mockImplementation(api.getActiveChannelPairing);
});

afterEach(() => {
  vi.useRealTimers();
});

it("stops pairing timers while cached and resumes them on activation", async () => {
  const visible = ref(true);
  const Other = defineComponent({ render: () => h("div", "Other plugin") });
  const Host = defineComponent({
    render: () => h(KeepAlive, null, {
      default: () => visible.value ? h(ChannelSettingsPlugin) : h(Other),
    }),
  });
  const wrapper = mount(Host);
  try {
    await flushPromises();
    expect(api.listChannelConfigs).toHaveBeenCalledOnce();
    await wrapper.findAll(".channel-settings__channel")[channelOrder.indexOf("feishu")].trigger("click");
    await flushPromises();
    expect(api.getActiveChannelPairing).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(2);

    visible.value = false;
    await nextTick();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(api.getChannelPairing).not.toHaveBeenCalled();

    visible.value = true;
    await nextTick();
    expect(vi.getTimerCount()).toBe(2);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(api.getChannelPairing).toHaveBeenCalledWith("pairing-1");
  } finally {
    wrapper.unmount();
  }
  expect(vi.getTimerCount()).toBe(0);
});
