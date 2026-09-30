// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, KeepAlive, nextTick, ref } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import DockerManagementPlugin from "./DockerManagementPlugin.vue";

const api = vi.hoisted(() => ({ dockerOverview: vi.fn() }));
vi.mock("../../../services/api", () => ({ api }));
vi.mock("naive-ui", () => ({
  NModal: { template: "<div />" },
  NDataTable: { template: "<div />" },
  useDialog: () => ({ info: vi.fn() }),
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  api.dockerOverview.mockRejectedValue(new Error("Docker unavailable"));
});
afterEach(() => vi.useRealTimers());

it("clears message timers while cached and refreshes on return", async () => {
  const visible = ref(true);
  const Other = defineComponent({ render: () => h("div", "Other plugin") });
  const Host = defineComponent({
    render: () => h(KeepAlive, null, {
      default: () => visible.value ? h(DockerManagementPlugin) : h(Other),
    }),
  });
  const wrapper = mount(Host);
  try {
    await flushPromises();
    expect(api.dockerOverview).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(1);

    visible.value = false;
    await nextTick();
    expect(vi.getTimerCount()).toBe(0);

    visible.value = true;
    await nextTick();
    await flushPromises();
    expect(api.dockerOverview).toHaveBeenCalledTimes(2);
  } finally {
    wrapper.unmount();
  }
  expect(vi.getTimerCount()).toBe(0);
});

it("does not schedule a new message timer from a late inactive response", async () => {
  let rejectOverview!: (reason: Error) => void;
  api.dockerOverview.mockImplementationOnce(() => new Promise((_, reject) => {
    rejectOverview = reject;
  }));
  const visible = ref(true);
  const Other = defineComponent({ render: () => h("div", "Other plugin") });
  const Host = defineComponent({
    render: () => h(KeepAlive, null, {
      default: () => visible.value ? h(DockerManagementPlugin) : h(Other),
    }),
  });
  const wrapper = mount(Host);
  try {
    expect(api.dockerOverview).toHaveBeenCalledOnce();
    visible.value = false;
    await nextTick();
    rejectOverview(new Error("Docker unavailable"));
    await flushPromises();
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    wrapper.unmount();
  }
});
