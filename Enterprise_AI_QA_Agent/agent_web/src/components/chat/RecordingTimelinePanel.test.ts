// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import { reactive } from "vue";

const api = vi.hoisted(() => ({
  listRecordings: vi.fn(),
  getRecordingDetail: vi.fn(),
  createRecordingCaseDraft: vi.fn(),
  getRecordingCaseDraft: vi.fn(),
  isUserActionEvent: (event: { type: string }) => ["click", "dblclick", "fill", "key", "submit", "scroll", "navigate", "file_change"].includes(event.type),
  recorderEventLabel: (event: { target?: { locators?: { text?: string } } | null }) => event.target?.locators?.text || "页面",
}));
const session = vi.hoisted(() => ({ session: { id: "session-1" } as { id: string } | null }));
const sessionStore = reactive(session);

vi.mock("../../features/recorder/recordingApi", () => api);
vi.mock("../../stores/session", () => ({ useSessionStore: () => sessionStore }));
vi.mock("../../features/projects/TestCaseContentDialog.vue", () => ({ default: { props: ["testCase", "initialVersionId"], template: '<div data-testid="case-dialog">{{testCase.id}}:{{initialVersionId}}</div>' } }));
vi.mock("../../services/i18n", () => ({ t: (key: string, params?: Record<string, string>) => {
  if (params?.id) return `${key}:${params.id}`;
  return key;
} }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const recording = {
  id: "recording-1",
  project_id: "project-1",
  name: "真实录制",
  entry_url: "https://example.test",
  driver_kind: "cdp-attach",
  status: "completed",
  session_id: "session-1",
  approval_id: null,
  step_count: 1,
  created_at: "2026-10-10T00:00:00Z",
  updated_at: "2026-10-10T00:00:01Z",
  started_at: "2026-10-10T00:00:00Z",
  ended_at: "2026-10-10T00:00:01Z",
  finalize_metrics: {},
  metadata: {},
  events: [{
    seq: 0,
    type: "click",
    timestamp: "2026-10-10T00:00:00Z",
    page: { url: "https://example.test" },
    target: { locators: { text: "提交" } },
    pixel: null,
    value: null,
    page_effect: {},
    screenshot_ref: null,
  }],
};

beforeEach(() => {
  vi.clearAllMocks();
  sessionStore.session = {id: "session-1"};
  api.listRecordings.mockResolvedValue([recording]);
  api.getRecordingDetail.mockResolvedValue(structuredClone(recording));
  api.getRecordingCaseDraft.mockResolvedValue(null);
  api.createRecordingCaseDraft.mockResolvedValue({
    recording_id: "recording-1",
    step_count: 1,
    case: { id: "case-1" },
    version: { id: "version-1" },
  });
});

it("creates one project-scoped case draft from a completed recording", async () => {
  const { default: RecordingTimelinePanel } = await import("./RecordingTimelinePanel.vue");
  const wrapper = mount(RecordingTimelinePanel);
  await flushPromises();

  const button = wrapper.get('[data-testid="recording-create-case-draft"]');
  await button.trigger("click");
  await flushPromises();

  expect(api.createRecordingCaseDraft).toHaveBeenCalledTimes(1);
  expect(api.createRecordingCaseDraft).toHaveBeenCalledWith("recording-1", "project-1");
  expect(wrapper.get('[data-testid="recording-case-draft-id"]').text()).toContain("case-1");
  expect(button.attributes("disabled")).toBeDefined();
  await wrapper.get('[data-testid="recording-case-draft-id"]').trigger("click");
  expect(wrapper.get('[data-testid="case-dialog"]').text()).toContain("case-1:version-1");
  wrapper.unmount();
});

it("recovers a committed draft after a lost POST reply without a second POST", async () => {
  api.createRecordingCaseDraft.mockRejectedValueOnce(new Error("lost reply"));
  const { default: Panel } = await import("./RecordingTimelinePanel.vue");
  const wrapper = mount(Panel); await flushPromises();
  await wrapper.get('[data-testid="recording-create-case-draft"]').trigger("click"); await flushPromises();
  api.getRecordingCaseDraft.mockResolvedValueOnce({recording_id: "recording-1", case: {id: "saved-case"}, version: {id: "saved-version"}});
  await wrapper.get('[data-testid="recording-create-case-draft"]').trigger("click"); await flushPromises();
  expect(api.createRecordingCaseDraft).toHaveBeenCalledTimes(1);
  expect(wrapper.get('[data-testid="recording-case-draft-id"]').text()).toContain("saved-case");
  wrapper.unmount();
});

it("does not show a late draft result in another session", async () => {
  let resolve!: (value: unknown) => void;
  api.createRecordingCaseDraft.mockImplementationOnce(() => new Promise(done => {resolve = done;}));
  const { default: Panel } = await import("./RecordingTimelinePanel.vue");
  const wrapper = mount(Panel); await flushPromises();
  await wrapper.get('[data-testid="recording-create-case-draft"]').trigger("click");
  const next = {...recording, id: "recording-2", session_id: "session-2", project_id: "project-2"};
  api.listRecordings.mockResolvedValue([next]); api.getRecordingDetail.mockResolvedValue(next);
  sessionStore.session = {id: "session-2"}; await flushPromises();
  resolve({recording_id: "recording-1", case: {id: "old-case"}, version: {id: "old-version"}}); await flushPromises();
  expect(wrapper.find('[data-testid="recording-case-draft-id"]').exists()).toBe(false);
  expect(wrapper.get('[data-testid="recording-create-case-draft"]').attributes("disabled")).toBeUndefined();
  wrapper.unmount();
});

it("shows a saved draft on reopen with zero new POSTs", async () => {
  api.getRecordingCaseDraft.mockResolvedValueOnce({recording_id: "recording-1", case: {id: "saved-case"}, version: {id: "original-version"}});
  const { default: Panel } = await import("./RecordingTimelinePanel.vue");
  const wrapper = mount(Panel); await flushPromises();
  expect(wrapper.get('[data-testid="recording-case-draft-id"]').text()).toContain("saved-case");
  expect(api.createRecordingCaseDraft).not.toHaveBeenCalled();
  wrapper.unmount();
});

it("does not create after a failed read of an unknown submission", async () => {
  api.getRecordingCaseDraft.mockRejectedValue(new Error("read unavailable"));
  const { default: Panel } = await import("./RecordingTimelinePanel.vue");
  const wrapper = mount(Panel); await flushPromises();
  await wrapper.get('[data-testid="recording-create-case-draft"]').trigger("click"); await flushPromises();
  expect(api.createRecordingCaseDraft).not.toHaveBeenCalled();
  expect(wrapper.get('[data-testid="recording-case-draft-error"]').text()).toContain("read unavailable");
  wrapper.unmount();
});

it("shows the backend error and allows a retry after a failed draft request", async () => {
  api.createRecordingCaseDraft.mockRejectedValueOnce(new Error("HTTP 409"));
  const { default: RecordingTimelinePanel } = await import("./RecordingTimelinePanel.vue");
  const wrapper = mount(RecordingTimelinePanel);
  await flushPromises();

  const button = wrapper.get('[data-testid="recording-create-case-draft"]');
  await button.trigger("click");
  await flushPromises();
  expect(wrapper.get('[data-testid="recording-case-draft-error"]').text()).toContain("HTTP 409");

  api.createRecordingCaseDraft.mockResolvedValueOnce({
    recording_id: "recording-1", step_count: 1, case: { id: "case-2" }, version: { id: "version-2" },
  });
  await button.trigger("click");
  await flushPromises();
  expect(api.createRecordingCaseDraft).toHaveBeenCalledTimes(2);
  expect(wrapper.get('[data-testid="recording-case-draft-id"]').text()).toContain("case-2");
  wrapper.unmount();
});
