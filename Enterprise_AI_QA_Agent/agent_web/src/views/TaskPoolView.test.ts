// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import TaskPoolView from "./TaskPoolView.vue";
import { registerLocale, setLocale } from "../services/i18n";
import zhCN from "../locales/zh-CN";

const api = vi.hoisted(() => ({ listTaskPoolEntries: vi.fn() }));
const push = vi.hoisted(() => vi.fn());
vi.mock("../services/api", () => ({ api }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
const planning = (status = "queued", id = "chosen-task") => ({ kind: "case_planning", project_name: "订单项目",
  task: { id, project_id: "chosen-project", status, phase: "ready", round_count: 2,
    updated_at: "2026-10-04T00:00:00Z", message: status === "failed" ? "必要依据读取失败" : "正在核对执行依据" } });
function button(w: ReturnType<typeof mount>, text: string) { return w.findAll("button").find(b => b.text() === text)!; }

describe("mixed task-pool page", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks();
    registerLocale("zh-CN", zhCN); setLocale("zh-CN");
    api.listTaskPoolEntries.mockResolvedValue({ items: [planning()], has_more: true, limit: 24, offset: 0 });
  });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("filters before paging and opens the exact project/task identity", async () => {
    const w = mount(TaskPoolView); await flushPromises();
    expect(w.text()).toContain("已排队"); expect(w.text()).toContain("已调用回合 2");
    await w.get('button[aria-label="查看规划任务"]').trigger("click");
    expect(push).toHaveBeenCalledWith({ name: "project-detail", params: { projectId: "chosen-project" }, query: { planning_task_id: "chosen-task" } });
    await button(w, "下一页").trigger("click"); await flushPromises();
    expect(api.listTaskPoolEntries).toHaveBeenLastCalledWith(24, 24, { kind: "all", state: "all", query: "" });
    await w.get("select").setValue("case_planning"); await flushPromises();
    expect(api.listTaskPoolEntries).toHaveBeenLastCalledWith(24, 0, { kind: "case_planning", state: "all", query: "" });
    await w.get("input").setValue("历史任务"); await vi.advanceTimersByTimeAsync(250); await flushPromises();
    expect(api.listTaskPoolEntries).toHaveBeenLastCalledWith(24, 0, { kind: "case_planning", state: "all", query: "历史任务" });
    await button(w, "运行中").trigger("click"); await flushPromises();
    expect(api.listTaskPoolEntries.mock.lastCall?.[2].state).toBe("active");
    w.unmount();
  });

  it("refreshes task state, presents failure, and stops querying after leaving", async () => {
    const w = mount(TaskPoolView); await flushPromises();
    api.listTaskPoolEntries.mockResolvedValue({ items: [planning("failed")], has_more: false });
    await vi.advanceTimersByTimeAsync(5000); await flushPromises();
    expect(w.text()).toContain("必要依据读取失败"); expect(w.text()).toContain("失败");
    api.listTaskPoolEntries.mockResolvedValue({ items: [planning("completed")], has_more: false });
    await button(w, "刷新").trigger("click"); await flushPromises();
    expect(w.text()).toContain("规划完成");
    api.listTaskPoolEntries.mockRejectedValue(new Error("数据库查询不可用"));
    await button(w, "刷新").trigger("click"); await flushPromises();
    expect(w.get('[role="alert"]').text()).toContain("数据库查询不可用");
    const calls = api.listTaskPoolEntries.mock.calls.length; w.unmount();
    await vi.advanceTimersByTimeAsync(10000); expect(api.listTaskPoolEntries).toHaveBeenCalledTimes(calls);
  });

  it("does not let an earlier response replace a newly filtered page", async () => {
    let resolveOld!: (value: unknown) => void;
    api.listTaskPoolEntries.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const w = mount(TaskPoolView); await flushPromises();
    await w.get("select").setValue("case_planning"); await flushPromises();
    resolveOld({ items: [planning("failed", "stale-task")], has_more: false }); await flushPromises();
    expect(w.find('[data-task-id="chosen-task"]').exists()).toBe(true);
    expect(w.find('[data-task-id="stale-task"]').exists()).toBe(false); w.unmount();
  });

  it("renders comparison fields and opens the exact historical task route", async () => {
    api.listTaskPoolEntries.mockResolvedValue({ items: [{ kind: "document_difference", project_name: "需求项目",
      document_title: "登录需求", task: { id: "history", project_id: "project", document_id: "document", status: "completed",
        phase: "finished", attempt_no: 1, updated_at: "2026-10-04T00:00:00Z", message: "比较结果已保存" } }], has_more: false });
    const w = mount(TaskPoolView); await flushPromises();
    expect(w.text()).toContain("登录需求"); expect(w.text()).toContain("比较已保存"); expect(w.text()).toContain("领取次数 1");
    await w.get('button[aria-label="查看比较任务"]').trigger("click");
    expect(push).toHaveBeenCalledWith({ name: "document-difference", params: { projectId: "project", documentId: "document", taskId: "history" } });
    await w.get("select").setValue("document_difference"); await flushPromises();
    expect(api.listTaskPoolEntries).toHaveBeenLastCalledWith(24, 0, { kind: "document_difference", state: "all", query: "" });
    w.unmount();
  });

  it("labels impact results as saved and opens the fixed impact task", async () => {
    api.listTaskPoolEntries.mockResolvedValue({ items: [{ kind: "document_impact", project_name: "需求项目", document_title: "订单需求",
      task: { id: "impact-history", project_id: "project", document_id: "document", status: "completed", phase: "finished",
        attempt_no: 2, updated_at: "2026-10-05T00:00:00Z", message: "候选结果已保存" } }], has_more: false });
    const w = mount(TaskPoolView); await flushPromises();
    expect(w.text()).toContain("文档影响分析"); expect(w.text()).toContain("影响结果已保存"); expect(w.text()).toContain("领取次数 2");
    await w.get('button[aria-label="查看影响任务"]').trigger("click");
    expect(push).toHaveBeenCalledWith({ name: "document-impact", params: { projectId: "project", documentId: "document", taskId: "impact-history" } });
    await w.get("select").setValue("document_impact"); await flushPromises();
    expect(api.listTaskPoolEntries).toHaveBeenLastCalledWith(24, 0, { kind: "document_impact", state: "all", query: "" }); w.unmount();
  });

  it("shows fixed generation/retry relations and routes using the actual user retry key", async () => {
    api.listTaskPoolEntries.mockResolvedValue({items:[{kind:"case_generation",project_name:"需求项目",document_title:"订单需求",task:{
      id:"child-task",project_id:"project",document_id:"document",group_id:"fixed-group",module_id:"fixed-module",status:"completed",
      phase:"finished",attempt_no:1,updated_at:"2026-10-06T00:00:00Z",message:"草稿已保存",result_kind:"update",
      retry:{id:"retry-id",parent_task_id:"failed-parent",creation_key:"user-key",strategy:"saved_response"}}}],has_more:false});
    const w=mount(TaskPoolView);await flushPromises();expect(w.text()).toContain("生成模块重试");expect(w.text()).toContain("复用已保存响应");
    expect(w.text()).toContain("草稿或候选已保存");await w.get('button[aria-label="查看生成任务"]').trigger("click");
    expect(push).toHaveBeenCalledWith({name:"case-generation-task",params:{projectId:"project",documentId:"document",groupId:"fixed-group",taskId:"child-task"},
      query:{parent_task_id:"failed-parent",retry_key:"user-key"}});
    await w.get("select").setValue("case_generation");await flushPromises();
    expect(api.listTaskPoolEntries).toHaveBeenLastCalledWith(24,0,{kind:"case_generation",state:"all",query:""});w.unmount();
  });
});
