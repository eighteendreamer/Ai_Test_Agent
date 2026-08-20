// @vitest-environment jsdom

import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import FlowInspector from "./FlowInspector.vue";
import { buildFlowPath, publishFlowSession, subscribeFlowSession } from "./openFlowWindow";
import { collectWorkerDispatches, workerFlowStatus, workerNodeId } from "./workers";
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
