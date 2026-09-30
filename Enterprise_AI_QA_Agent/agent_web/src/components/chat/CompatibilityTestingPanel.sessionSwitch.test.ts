// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick, reactive } from "vue";
import { beforeEach, expect, it, vi } from "vitest";
import type { ChatMessage } from "../../types";
import CompatibilityTestingPanel from "./CompatibilityTestingPanel.vue";

const api = vi.hoisted(() => ({
  listCompatibilityRunners: vi.fn(),
  listCompatibilityRunnerTasks: vi.fn(),
  getCompatibilitySummary: vi.fn(),
  getCompatibilityReport: vi.fn(),
  draftCompatibilityPlan: vi.fn(),
}));
const store = reactive({
  session: { id: "a" } as { id: string } | null,
  messages: [] as ChatMessage[],
  selectedModeKey: "compatibility_testing",
  recentToolJobs: [],
  isBusy: false,
});
vi.mock("../../services/api", () => ({ api }));
vi.mock("../../stores/session", () => ({ useSessionStore: () => store }));
vi.mock("./AssistantMarkdown.vue", () => ({ default: { template: "<div />" } }));

function userMessage(id: string, content: string): ChatMessage {
  return { id, role: "user", content, created_at: "2026-09-30T00:00:00Z", metadata: {} };
}

beforeEach(() => {
  vi.clearAllMocks();
  store.session = { id: "a" };
  store.messages = [];
  store.selectedModeKey = "compatibility_testing";
  api.listCompatibilityRunners.mockResolvedValue([]);
  api.listCompatibilityRunnerTasks.mockResolvedValue([]);
  api.getCompatibilitySummary.mockResolvedValue({ total: 0 });
  api.getCompatibilityReport.mockResolvedValue({ total_tasks: 0, artifacts: [] });
  api.draftCompatibilityPlan.mockImplementation(() => new Promise(() => {}));
});

it("does not treat another session's historical confirmation as a new draft request", async () => {
  const wrapper = mount(CompatibilityTestingPanel);
  try {
    await flushPromises();
    for (let step = 1; step <= 5; step += 1) {
      store.messages = [...store.messages, userMessage(`a-${step}`, `第 ${step} 步回答`)];
      await nextTick();
    }
    expect(wrapper.get(".wizard-eyebrow").text()).toContain("6/6");
    expect(api.draftCompatibilityPlan).not.toHaveBeenCalled();

    store.session = { id: "b" };
    store.messages = [userMessage("b-history", "可以生成")];
    await flushPromises();
    expect(api.draftCompatibilityPlan).not.toHaveBeenCalled();
    expect(wrapper.get(".wizard-eyebrow").text()).toContain("1/6");

    store.messages = [...store.messages, userMessage("b-new", "这是 Web 产品")];
    await nextTick();
    expect(wrapper.get(".wizard-eyebrow").text()).toContain("2/6");
    expect(api.draftCompatibilityPlan).not.toHaveBeenCalled();
  } finally {
    wrapper.unmount();
  }
});

it("drafts once for a new confirmation in the active session", async () => {
  const wrapper = mount(CompatibilityTestingPanel);
  try {
    await flushPromises();
    for (let step = 1; step <= 5; step += 1) {
      store.messages = [...store.messages, userMessage(`a-${step}`, `第 ${step} 步回答`)];
      await nextTick();
    }
    store.messages = [...store.messages, userMessage("a-confirm", "确认生成")];
    await nextTick();
    expect(api.draftCompatibilityPlan).toHaveBeenCalledOnce();

    store.messages = [...store.messages, userMessage("a-repeat", "再次生成")];
    await nextTick();
    expect(api.draftCompatibilityPlan).toHaveBeenCalledOnce();
  } finally {
    wrapper.unmount();
  }
});
