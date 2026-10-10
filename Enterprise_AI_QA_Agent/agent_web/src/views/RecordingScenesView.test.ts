// @vitest-environment jsdom
import recordedFixture from "../../../docs/upgrade_2026-10-03/p5-recording-scene-product-first/version2.json";
import { flushPromises, mount } from "@vue/test-utils";
import { reactive } from "vue";
import { beforeEach, expect, it, vi } from "vitest";
import RecordingScenesView from "./RecordingScenesView.vue";
import type { SceneBundle } from "../features/recorder/sceneApi";
import { HttpError } from "../services/http";

const mocks=vi.hoisted(()=>({getProjectOverview:vi.fn(),listRecordings:vi.fn(),list:vi.fn(),create:vi.fn(),recoverCreation:vi.fn(),get:vi.fn(),versions:vi.fn(),save:vi.fn(),recoverVersion:vi.fn(),draft:vi.fn()}));
vi.mock("../services/api",()=>({api:{getProjectOverview:mocks.getProjectOverview}}));
vi.mock("../features/recorder/recordingApi",()=>({listRecordings:mocks.listRecordings}));
vi.mock("../features/recorder/sceneApi",()=>({scenesApi:mocks}));
vi.mock("vue-router",()=>({useRoute:()=>route}));
const route=reactive({params:{projectId:""}});
const recorded=recordedFixture as SceneBundle;
const id=recorded.project_id;
const mountView=()=>mount(RecordingScenesView,{global:{stubs:{RouterLink:{template:"<a><slot /></a>"}}}});
function button(wrapper:ReturnType<typeof mountView>,label:string) { return wrapper.findAll("button").find(b=>b.text()===label)!; }

beforeEach(()=>{
  window.sessionStorage.clear();
  vi.resetAllMocks();route.params.projectId=id;
  mocks.getProjectOverview.mockResolvedValue({project:{name:"实际场景项目",status:"active"}});
  mocks.listRecordings.mockResolvedValue([{id:recorded.version.source.recording_id,name:"新录制",status:"completed"}]);
  mocks.list.mockResolvedValue({items:[{scene_id:recorded.scene_id,name:recorded.version.content.name,version:2,step_count:recorded.version.content.steps.length}],has_more:false});
  mocks.get.mockResolvedValue(recorded);mocks.versions.mockResolvedValue([{id:recorded.version.id,version:2}]);
});

it("keeps an unknown creation identity and recovers by GET without an automatic write retry",async()=>{
  mocks.create.mockRejectedValue(new Error("connection closed after submission"));
  mocks.recoverCreation.mockResolvedValue(recorded);
  const wrapper=mountView();
  try {
    await flushPromises();await button(wrapper,"从录制创建场景").trigger("click");await flushPromises();
    expect(mocks.create).toHaveBeenCalledTimes(1);
    const identity=mocks.create.mock.calls[0]![1].request_id;
    expect(wrapper.get("[role=alert]").text()).toContain("connection closed");
    await button(wrapper,"恢复提交").trigger("click");await flushPromises();
    expect(mocks.recoverCreation).toHaveBeenCalledExactlyOnceWith(id,identity);
    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("已恢复保存结果");
  } finally { wrapper.unmount(); }
});

it("edits real JSON data from a recorded scene and prevents a malformed assertion submission",async()=>{
  const wrapper=mountView();
  try {
    await flushPromises();await wrapper.get("aside button").trigger("click");await flushPromises();
    const label=wrapper.findAll("label").find(n=>n.text().startsWith("业务断言 JSON"))!;
    await label.get("textarea").setValue("invalid json");
    await wrapper.findAll("label").find(n=>n.text().startsWith("本次修改原因"))!.get("input").setValue("实际输入");
    await button(wrapper,"保存新版本").trigger("click");await flushPromises();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(wrapper.find("[role=alert]").exists()).toBe(true);
    await label.get("textarea").setValue(JSON.stringify(recorded.version.content.assertions));
    mocks.save.mockResolvedValue(recorded);
    await button(wrapper,"保存新版本").trigger("click");await flushPromises();
    expect(mocks.save).toHaveBeenCalledTimes(1);
    const saved=mocks.save.mock.calls[0]![2];
    expect(saved.content.steps).toEqual(recorded.version.content.steps);
    expect(saved.content.aliases).toEqual(recorded.version.content.aliases);
    expect(saved.parent_version_id).toBe(recorded.version.id);
  } finally { wrapper.unmount(); }
});

it("discards an earlier project response that finishes after the route switches",async()=>{
  let resolve!:(value:unknown)=>void;
  const old=new Promise(ok=>{resolve=ok});
  mocks.getProjectOverview.mockImplementation((projectId:string)=>projectId===id?old:Promise.resolve({project:{name:"另一个项目",status:"active"}}));
  mocks.list.mockImplementation((projectId:string)=>Promise.resolve({items:projectId===id?[{scene_id:recorded.scene_id,name:"原项目场景"}]:[],has_more:false}));
  const wrapper=mountView();
  try {
    await flushPromises();route.params.projectId="other";await flushPromises();
    expect(wrapper.text()).toContain("另一个项目");
    resolve({project:{name:"原项目",status:"active"}});await flushPromises();
    expect(wrapper.text()).not.toContain("原项目场景");expect(wrapper.text()).toContain("另一个项目");
  } finally { wrapper.unmount(); }
});

it("keeps the editor usable after a confirmed rejected save",async()=>{
  mocks.save.mockRejectedValue(new HttpError("scene has a newer version; reload before saving",409));
  const wrapper=mountView();
  try {
    await flushPromises();await wrapper.get("aside button").trigger("click");await flushPromises();
    await wrapper.findAll("label").find(n=>n.text().startsWith("本次修改原因"))!.get("input").setValue("并发编辑");
    await button(wrapper,"保存新版本").trigger("click");await flushPromises();
    expect(wrapper.get("[role=alert]").text()).toContain("newer version");
    expect(wrapper.find(".pending").exists()).toBe(false);
    expect(wrapper.get("fieldset").attributes("disabled")).toBeUndefined();
  } finally { wrapper.unmount(); }
});

it("restores a submitted edit identity after remount without storing business content or sending another write",async()=>{
  mocks.save.mockRejectedValue(new Error("lost response"));
  let wrapper=mountView();
  try {
    await flushPromises();await wrapper.get("aside button").trigger("click");await flushPromises();
    await wrapper.findAll("label").find(n=>n.text().startsWith("本次修改原因"))!.get("input").setValue("实际编辑");
    await button(wrapper,"保存新版本").trigger("click");await flushPromises();
    const requestId=mocks.save.mock.calls[0]![2].request_id;
    const stored=window.sessionStorage.getItem(`recording-scene-submit:${id}`)!;
    expect(stored).not.toContain("完整输入");expect(stored).not.toContain("content");
    wrapper.unmount();wrapper=mountView();await flushPromises();
    expect(wrapper.text()).toContain(requestId);
    expect(button(wrapper,"使用原身份重试").attributes("disabled")).toBeDefined();
    mocks.recoverVersion.mockResolvedValue(recorded);
    await button(wrapper,"恢复提交").trigger("click");await flushPromises();
    expect(mocks.recoverVersion).toHaveBeenCalledExactlyOnceWith(id,recorded.scene_id,requestId);
    expect(mocks.save).toHaveBeenCalledTimes(1);
  } finally { wrapper.unmount(); }
});
