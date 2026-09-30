// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import type { KnowledgeGraphResponse, KnowledgeProjectSummary } from "../types";
import KnowledgeView from "./KnowledgeView.vue";

const api = vi.hoisted(() => ({
  listKnowledgeProjects: vi.fn(),
  getKnowledgeGraph: vi.fn(),
  deleteKnowledgeProject: vi.fn(),
}));
vi.mock("../services/api", () => ({ api }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((ok) => { resolve = ok; });
  return { promise, resolve };
}

function project(id: string): KnowledgeProjectSummary {
  return {
    project_id: id,
    project_scope: `Project ${id}`,
    page_count: 1,
    element_count: 0,
    entity_count: 0,
    edge_count: 0,
  };
}

function graph(id: string): KnowledgeGraphResponse {
  return {
    summary: { ...project(id), relation_counts: {} },
    nodes: [{ id: `${id}-node`, label: `${id} page`, kind: "page", summary: "", metadata: {} }],
    edges: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listKnowledgeProjects.mockResolvedValue([project("a"), project("b")]);
});

it("keeps the selected project graph when the previous project's response arrives last", async () => {
  const old = deferred<KnowledgeGraphResponse>();
  const current = deferred<KnowledgeGraphResponse>();
  api.getKnowledgeGraph.mockImplementation((id: string) => id === "a" ? old.promise : current.promise);
  const wrapper = mount(KnowledgeView, { global: { stubs: { NModal: true, NButton: true } } });
  try {
    await flushPromises();
    expect(api.getKnowledgeGraph).toHaveBeenCalledWith("a");
    await wrapper.get(".knowledge-select").setValue("b");
    expect(api.getKnowledgeGraph).toHaveBeenCalledWith("b");

    current.resolve(graph("b"));
    await flushPromises();
    expect(wrapper.find(".node-list").text()).toContain("b page");

    old.resolve(graph("a"));
    await flushPromises();
    expect(wrapper.get(".knowledge-select").element).toHaveProperty("value", "b");
    expect(wrapper.find(".node-list").text()).toContain("b page");
    expect(wrapper.find(".node-list").text()).not.toContain("a page");
  } finally {
    wrapper.unmount();
  }
});

it("clears the previous graph while the newly selected project is loading", async () => {
  const current = deferred<KnowledgeGraphResponse>();
  api.getKnowledgeGraph.mockImplementation((id: string) => id === "a" ? Promise.resolve(graph(id)) : current.promise);
  const wrapper = mount(KnowledgeView, { global: { stubs: { NModal: true, NButton: true } } });
  try {
    await flushPromises();
    expect(wrapper.find(".node-list").text()).toContain("a page");
    await wrapper.get(".knowledge-select").setValue("b");
    expect(wrapper.find(".node-list").text()).not.toContain("a page");
    current.resolve(graph("b"));
    await flushPromises();
    expect(wrapper.find(".node-list").text()).toContain("b page");
  } finally {
    wrapper.unmount();
  }
});

it("does not reselect a hidden node when the search has no matches", async () => {
  api.getKnowledgeGraph.mockResolvedValue(graph("a"));
  const wrapper = mount(KnowledgeView, { global: { stubs: { NModal: true, NButton: true } } });
  try {
    await flushPromises();
    await wrapper.get(".node-row:not(.global-row)").trigger("click");
    expect(wrapper.findAll(".graph-node")).toHaveLength(1);

    await wrapper.get(".knowledge-search").setValue("not-present");
    await flushPromises();
    expect(wrapper.findAll(".node-row:not(.global-row)")).toHaveLength(0);
    expect(wrapper.findAll(".graph-node")).toHaveLength(0);
  } finally {
    wrapper.unmount();
  }
});
