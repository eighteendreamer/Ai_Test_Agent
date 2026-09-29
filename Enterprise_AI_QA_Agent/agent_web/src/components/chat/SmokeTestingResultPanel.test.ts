// @vitest-environment jsdom
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../services/api";
import { useSessionStore } from "../../stores/session";
import type { AgentTaskAccepted, SessionDetail, ToolJobRecord } from "../../types";
import SmokeTestingResultPanel from "./SmokeTestingResultPanel.vue";

function smokeJob(version: number, caseId: string): ToolJobRecord {
  return {
    id: `job-${version}`,
    session_id: "session-1",
    turn_id: `turn-${version}`,
    trace_id: `trace-${version}`,
    call_id: `call-${version}`,
    tool_key: "smoke-suite-runner",
    tool_name: "Smoke runner",
    status: "completed",
    attempt: 1,
    input_payload: {},
    output_payload: {
      plan_uri: `rustfs://test/plan-v${version}.json`,
      plan: {
        plan_id: "plan-1",
        version,
        title: "Reviewed plan",
        status: "awaiting_user_confirmation",
        cases: [{ case_id: caseId, title: caseId, selected: true, execution_eligible: true }],
      },
    },
    summary: "Awaiting review",
    artifact_count: 0,
    created_at: `2026-09-29T00:00:0${version}Z`,
    updated_at: `2026-09-29T00:00:0${version}Z`,
    metadata: {},
  };
}

describe("smoke plan confirmation", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    const store = useSessionStore();
    store.session = {
      id: "session-1",
      status: "idle",
      verification_results: [],
      metadata: {},
    } as unknown as SessionDetail;
    store.toolJobs = [smokeJob(1, "case-old"), smokeJob(2, "case-new")];
  });

  afterEach(() => {
    useSessionStore().disconnectEvents();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sends the reviewed plan version with the selected cases", async () => {
    class FakeEventSource {
      readyState = 1;
      onopen = null;
      onerror = null;
      onmessage = null;
      constructor(readonly url: string) {}
      close() { this.readyState = 2; }
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    const store = useSessionStore();
    const sendMessage = vi.spyOn(api, "sendMessage").mockResolvedValue({
      session: { ...store.session!, messages: [] },
      events: [],
      output: { id: "receipt-1", role: "system", content: "Queued", created_at: "2026-09-29T00:00:03Z", metadata: {} },
      task: {
        id: "task-1", operation: "message", session_id: "session-1", turn_id: "turn-2",
        request_id: "request-1", trace_id: "trace-2", status: "queued", priority: 50,
        attempt_no: 0, created_at: "2026-09-29T00:00:03Z", updated_at: "2026-09-29T00:00:03Z",
      },
    } satisfies AgentTaskAccepted);
    const wrapper = mount(SmokeTestingResultPanel);

    await wrapper.find(".smoke-plan-list article button").trigger("click");

    expect(sendMessage).toHaveBeenCalledWith(
      "session-1",
      expect.stringContaining("v2"),
      "default",
      [],
      {
        smoke_action: "execute_approved_plan",
        plan_id: "plan-1",
        plan_version: 2,
        selected_case_ids: ["case-new"],
      },
      expect.objectContaining({ client_message_id: expect.any(String) }),
    );
  });

  it("keeps selections independent between revisions of one plan", () => {
    const wrapper = mount(SmokeTestingResultPanel);
    const plans = wrapper.findAll(".smoke-plan-list article");

    expect(plans).toHaveLength(2);
    expect((plans[0].find("input[type='checkbox']").element as HTMLInputElement).checked).toBe(true);
    expect((plans[1].find("input[type='checkbox']").element as HTMLInputElement).checked).toBe(true);
  });
});
