// @vitest-environment jsdom
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { ExecutionEvent } from "../../types";
import ExecutionTrace from "./ExecutionTrace.vue";

describe("Runtime graph event collection boundary", () => {
  it("does not present material persistence as application or test completion", async () => {
    const event = (type: string): ExecutionEvent => ({
      id: type,
      type,
      session_id: "session",
      timestamp: "2026-10-27T00:00:00Z",
      payload: { turn_id: "turn", message: "original fact" },
    });
    const events = [event("runtime.turn_started"), event("runtime.event_log_completed")];
    const wrapper = mount(ExecutionTrace, { props: { turnId: "turn", events } });
    expect(wrapper.find(".chat-execution-trace-label").text()).toBe("正在处理");
    expect(wrapper.find(".chat-execution-step.is-complete").exists()).toBe(false);
    await wrapper.setProps({ events: [...events, event("turn.completed")] });
    expect(wrapper.find(".chat-execution-trace-label").text()).toBe("已完成");
    wrapper.unmount();
  });
});
