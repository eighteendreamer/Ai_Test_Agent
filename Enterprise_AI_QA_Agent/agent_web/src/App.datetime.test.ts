// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import sample from "../tests/fixtures/session-created-utc.json";
import App from "./App.vue";
import { getLocale } from "./services/i18n";
import { compareEvents } from "./features/flow/stages";
import { parseServerDate } from "./utils/datetime";

const stores = vi.hoisted(() => ({
  app: { theme: "light", systemStatus: "ok", fetchSystemStatus: vi.fn().mockResolvedValue(undefined) },
  session: {
    session: null as unknown, activity: [] as unknown[], workerDispatches: [],
    workerFailureGuard: null, queuedTurnCount: 0, nextQueuedTurn: null,
    watcherPhase: "idle", watcherLastSyncLabel: "--", watcherFailures: 0,
    pendingApprovals: [], latestTurnId: "", selectedAgentKey: "", messages: [],
    bootstrap: vi.fn().mockResolvedValue({ agents: [], tools: [] }),
    startWatcher: vi.fn(), stopWatcher: vi.fn(), disconnectEvents: vi.fn(),
  },
}));
vi.mock("./stores/app", () => ({ useAppStore: () => stores.app }));
vi.mock("./stores/session", () => ({ useSessionStore: () => stores.session }));
vi.mock("vue-router", () => ({ useRoute: () => ({ name: "home", fullPath: "/home", meta: {} }) }));
vi.mock("./components/layout/AppSidebar.vue", () => ({ default: { template: "<div />" } }));
vi.mock("./components/layout/AppTopBar.vue", () => ({ default: { template: "<div />" } }));
vi.mock("naive-ui", () => ({
  darkTheme: {},
  NConfigProvider: { template: "<div><slot /></div>" },
  NDialogProvider: { template: "<div><slot /></div>" },
  NMessageProvider: { template: "<div><slot /></div>" },
}));
afterEach(() => vi.unstubAllEnvs());

it.each(["UTC", "Asia/Shanghai", "America/New_York"])(
  "renders a captured normal session event at its UTC instant in %s", async (timezone) => {
    vi.stubEnv("TZ", timezone);
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe(timezone);
    const event = sample.events[0];
    expect(parseServerDate(event.timestamp)?.getTime()).toBe(sample.expected_epoch_ms);
    // Equivalent UTC representations must not reorder the same event in Flow.
    expect(compareEvents(event, { ...event, timestamp: new Date(sample.expected_epoch_ms).toISOString() })).toBe(0);
    stores.session.session = sample.session;
    stores.session.activity = sample.events;
    const wrapper = mount(App, { global: { stubs: { RouterView: true } } });
    try {
      await flushPromises();
      await wrapper.get(".log-panel-head").trigger("click");
      const expected = new Date(sample.expected_epoch_ms).toLocaleTimeString(getLocale(), { hour12: false });
      expect(wrapper.get(".runtime-console-raw-log").text()).toContain(`[${expected}] session.created`);
    } finally {
      wrapper.unmount();
    }
  },
);
