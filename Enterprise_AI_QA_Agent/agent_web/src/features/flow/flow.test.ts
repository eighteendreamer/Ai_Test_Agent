// @vitest-environment jsdom

import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import FlowInspector from "./FlowInspector.vue";
import { buildFlowPath, publishFlowSession, subscribeFlowSession } from "./openFlowWindow";
import { projectFlowNodes } from "./stages";
import { collectWorkerDispatches, workerFlowStatus, workerNodeId } from "./workers";
import { t } from "../../services/i18n";
import type { WorkerDispatchRecord } from "../../types";

function worker(overrides: Partial<WorkerDispatchRecord> = {}): WorkerDispatchRecord {
  return {
    task_id: "task-1",
    agent_key: "agent-1",
    description: "Worker one",
    status: "running",
    ...overrides,
  };
}

const inspectorProps = {
  open: true,
  stageId: "" as const,
  worker: null,
  status: "running" as const,
  events: [],
  graphState: null,
  toolJobs: [],
  artifacts: [],
  artifactsLoaded: true,
  turnId: "turn-1",
};

describe("Flow worker drill-down", () => {
  it("emits the child session when the worker inspector is drilled into", async () => {
    const wrapper = mount(FlowInspector, {
      props: {
        ...inspectorProps,
        worker: worker({ child_session_id: "child-session-1" }),
      },
    });

    await wrapper.get(".flow-inspector-head-actions .flow-reset-btn").trigger("click");

    expect(wrapper.emitted("drill")).toEqual([["child-session-1"]]);
  });

  it("does not render a drill-down action for workers without a child session", () => {
    const wrapper = mount(FlowInspector, {
      props: {
        ...inspectorProps,
        worker: worker(),
      },
    });

    expect(wrapper.find(".flow-inspector-head-actions .flow-reset-btn").exists()).toBe(false);
  });
});

describe("Flow inspector long content", () => {
  it("renders log metadata and messages in a complete, separate layout", () => {
    const longMessage = "Assistant response payload has been finalized for the client.\n" + "x".repeat(220);
    const wrapper = mount(FlowInspector, {
      props: {
        ...inspectorProps,
        stageId: "responder",
        events: [
          {
            id: "log-1",
            session_id: "session-1",
            type: "graph.response_ready",
            timestamp: "2026-08-20T12:44:05Z",
            payload: { turn_id: "turn-1", phase: "responder", message: longMessage },
          },
          {
            id: "log-2",
            session_id: "session-1",
            type: "runtime.turn_completed",
            timestamp: "2026-08-20T12:44:06Z",
            payload: { turn_id: "turn-1", phase: "responder" },
          },
        ],
      },
    });

    const entries = wrapper.findAll(".flow-inspector-log");
    expect(entries).toHaveLength(2);
    expect(entries[0].find(".flow-inspector-log-type").text()).toBe("graph.response_ready");
    expect(entries[0].find(".flow-inspector-log-time").text()).toContain("2026");
    expect(entries[0].find(".flow-inspector-log-message").text()).toBe(longMessage);
    expect(entries[1].find(".flow-inspector-log-message").text()).toBe(t("flow.inspector.not_carried"));
    expect(wrapper.findAll(".flow-inspector-log-head")).toHaveLength(2);
  });

  it("keeps prompt names and summaries on separate lines without a dropdown", async () => {
    const prompt = "# Identity\n\n" + "Long prompt content. ".repeat(30);
    const wrapper = mount(FlowInspector, {
      props: {
        ...inspectorProps,
        stageId: "prompt_assembler",
        graphState: {
          system_prompt: prompt,
          system_prompt_sections: [
            { title: "identity", body: "Identity section body" },
            { title: "execution_contract", body: "Execution section body" },
          ],
        },
      },
    });

    await wrapper.get(".runtime-console-tab:nth-child(4)").trigger("click");

    const disclosure = wrapper.get(".flow-inspector-disclosure");
    expect(disclosure.attributes("open")).toBeUndefined();
    expect(disclosure.get(".flow-inspector-disclosure-preview").text()).toContain("# Identity");
    expect(wrapper.findAll(".flow-inspector-disclosure")).toHaveLength(3);
    expect(wrapper.find(".flow-inspector-prompt-select").exists()).toBe(false);
    expect(wrapper.find(".flow-inspector-disclosure-title").element.tagName).toBe("SPAN");
    expect(wrapper.find(".flow-inspector-disclosure-preview").element.tagName).toBe("SPAN");

    await disclosure.get("summary").trigger("click");

    expect(disclosure.attributes("open")).toBeDefined();
    expect(disclosure.get("pre").text()).toContain(prompt.slice(-40).trim());
  });
});

describe("Dynamic flow projection", () => {
  it("creates only the phases observed in the selected turn", () => {
    const stages = projectFlowNodes([
      { id: "1", session_id: "session-1", type: "runtime.turn_started", timestamp: "2026-08-20T00:00:00Z", payload: { turn_id: "turn-1" } },
      { id: "2", session_id: "session-1", type: "graph.context_built", timestamp: "2026-08-20T00:00:01Z", payload: { turn_id: "turn-1", phase: "context_builder" } },
      { id: "3", session_id: "session-1", type: "graph.execution_started", timestamp: "2026-08-20T00:00:02Z", payload: { turn_id: "turn-1", phase: "router" } },
    ], "turn-1");

    expect(stages.stages.map((stage) => stage.phase)).toEqual(["context_builder", "router"]);
    expect(stages.edges).toEqual([
      { id: "e-context_builder-router", source: "context_builder", target: "router", kind: "stage" },
    ]);
  });
});

describe("Flow worker projection helpers", () => {
  it("merges graph worker details and keeps only the selected parent turn", () => {
    const result = collectWorkerDispatches(
      {
        worker_dispatches: [
          worker({ task_id: "task-1", parent_turn_id: "turn-1" }),
          worker({ task_id: "task-old", parent_turn_id: "turn-old" }),
        ],
      },
      {
        worker_dispatches: [
          worker({ task_id: "task-1", child_session_id: "child-session-1", status: "completed" }),
        ],
      },
      "turn-1",
    );

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      task_id: "task-1",
      child_session_id: "child-session-1",
      status: "completed",
    });
  });

  it("maps worker status and node identity for the canvas", () => {
    expect(workerFlowStatus("waiting")).toBe("waiting_approval");
    expect(workerNodeId(worker({ task_id: "task-42" }))).toBe("worker:task-42");
  });
});

describe("Flow window navigation", () => {
  it("builds a session and turn path for the flow window", () => {
    expect(buildFlowPath({ sessionId: "session 1", turnId: "turn/2" })).toBe(
      "/flow?session=session+1&turn=turn%2F2",
    );
  });

  it("publishes and receives session changes through the flow channel", () => {
    class FakeBroadcastChannel {
      static instances: FakeBroadcastChannel[] = [];
      onmessage: ((event: MessageEvent) => void) | null = null;
      messages: unknown[] = [];
      closed = false;

      constructor() {
        FakeBroadcastChannel.instances.push(this);
      }

      postMessage(message: unknown) {
        this.messages.push(message);
      }

      close() {
        this.closed = true;
      }
    }

    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
    const received: Array<{ sessionId: string; turnId: string }> = [];
    const stop = subscribeFlowSession((target) => received.push(target));

    publishFlowSession({ sessionId: "session-1", turnId: "turn-1" });
    const published = FakeBroadcastChannel.instances[1];
    FakeBroadcastChannel.instances[0].onmessage?.({
      data: { type: "session-changed", sessionId: "session-2", turnId: "turn-2" },
    } as MessageEvent);

    expect(published.messages).toEqual([
      { type: "session-changed", sessionId: "session-1", turnId: "turn-1" },
    ]);
    expect(received).toEqual([{ sessionId: "session-2", turnId: "turn-2" }]);
    stop();
    expect(FakeBroadcastChannel.instances[0].closed).toBe(true);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
