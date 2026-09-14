// @vitest-environment jsdom

import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const flowStore = vi.hoisted(() => ({
  fitView: vi.fn((_options?: { padding: number }) => Promise.resolve(true)),
  getViewport: vi.fn<() => { x: number; y: number; zoom: number }>(
    () => ({ x: 100, y: 200, zoom: 1 }),
  ),
  setViewport: vi.fn<(viewport: { x: number; y: number; zoom: number }) => Promise<boolean>>(
    () => Promise.resolve(true),
  ),
}));

vi.mock("@vue-flow/core", async () => {
  const { defineComponent, h, onMounted } = await import("vue");

  return {
    Panel: defineComponent({
      name: "Panel",
      setup(_, { slots }) {
        return () => h("div", slots.default?.());
      },
    }),
    VueFlow: defineComponent({
      name: "VueFlow",
      emits: ["init", "update:nodes", "update:edges"],
      setup(_, { emit, slots }) {
        onMounted(() => {
          emit("init", {
            fitView: flowStore.fitView,
            viewportHelper: {
              value: {
                getViewport: flowStore.getViewport,
                setViewport: flowStore.setViewport,
              },
            },
          });
        });
        return () => h("div", { class: "vue-flow-stub" }, slots.default?.());
      },
    }),
  };
});

import FlowCanvas from "./FlowCanvas.vue";

function mountCanvas() {
  return mount(FlowCanvas, {
    props: {
      sessionId: "session-1",
      turnId: "turn-1",
      stages: [],
      stageEdges: [],
      workers: [],
    },
  });
}

function dispatchWheel(
  element: Element,
  options: WheelEventInit,
): WheelEvent {
  const event = new WheelEvent("wheel", {
    bubbles: true,
    cancelable: true,
    deltaMode: WheelEvent.DOM_DELTA_PIXEL,
    clientX: 110,
    clientY: 220,
    ...options,
  });
  element.dispatchEvent(event);
  return event;
}

describe("FlowCanvas wheel viewport controls", () => {
  beforeEach(() => {
    flowStore.fitView.mockClear();
    flowStore.getViewport.mockClear();
    flowStore.getViewport.mockReturnValue({ x: 100, y: 200, zoom: 1 });
    flowStore.setViewport.mockClear();
  });

  it("routes wheel modifiers to vertical pan, horizontal pan, and cursor zoom", async () => {
    const wrapper = mountCanvas();
    const viewport = wrapper.get(".flow-canvas-viewport").element;
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({
      left: 10,
      top: 20,
      right: 1010,
      bottom: 720,
      width: 1000,
      height: 700,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    });

    const vertical = dispatchWheel(viewport, { deltaY: 40 });
    expect(vertical.defaultPrevented).toBe(true);
    expect(flowStore.setViewport).toHaveBeenLastCalledWith({ x: 100, y: 160, zoom: 1 });

    const horizontal = dispatchWheel(viewport, { deltaY: 40, shiftKey: true });
    expect(horizontal.defaultPrevented).toBe(true);
    expect(flowStore.setViewport).toHaveBeenLastCalledWith({ x: 60, y: 200, zoom: 1 });

    const zoom = dispatchWheel(viewport, { deltaY: -100, ctrlKey: true });
    expect(zoom.defaultPrevented).toBe(true);
    const lastCall = flowStore.setViewport.mock.calls[flowStore.setViewport.mock.calls.length - 1];
    const nextViewport = lastCall?.[0];
    expect(nextViewport?.zoom).toBeGreaterThan(1);
    expect((100 - nextViewport!.x) / nextViewport!.zoom).toBeCloseTo(0);
    expect((200 - nextViewport!.y) / nextViewport!.zoom).toBeCloseTo(0);
  });
});
