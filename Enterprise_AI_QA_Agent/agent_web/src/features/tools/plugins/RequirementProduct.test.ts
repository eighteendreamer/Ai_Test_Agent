// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import saved from "../../../../../docs/upgrade_2026-10-03/p4-requirement-reviews-final/md/verification.json";
import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import type { RequirementMatrix as Matrix, RequirementReview, RequirementReviewRequest } from "../../../services/requirements.types";
import { HttpError } from "../../../services/http";
import RequirementReviewPanel from "./RequirementReviewPanel.vue";
import RequirementMatrix from "./RequirementMatrix.vue";
import DocumentImpactSnapshot from "./DocumentImpactSnapshot.vue";
import { normalizedReview, readAllRequirementPages } from "./requirementProduct";

const service = vi.hoisted(() => ({ submitRequirementReview: vi.fn(), getRequirementReviewSubmission: vi.fn(),
  listRequirementReviews: vi.fn(), listRequirements: vi.fn(), listRequirementVersions: vi.fn(), readRequirementMatrix: vi.fn() }));
vi.mock("../../../services/requirements.api", () => service);
const project = vi.hoisted(() => ({ getProjectOverview: vi.fn() }));
vi.mock("../../../services/api", () => ({ api: project }));
const clone = <T,>(value: T): T => structuredClone(value);
const view = () => clone({ snapshot: saved.source_snapshot, result: saved.source_result }) as unknown as DocumentImpactSnapshotView;
const first = () => clone(saved.first) as unknown as RequirementReview;
const second = () => clone(saved.second) as unknown as RequirementReview;
const props = () => ({ projectId: saved.first.project_id, documentId: saved.first.document_id, view: view() });
const page = <T,>(items: T[], offset = 0, more = false) => ({ items, limit: 100, offset, has_more: more });
const cacheKey = `qa-agent:requirement-review:${saved.first.project_id}:${saved.first.document_id}:${saved.source_snapshot.id}`;
const wrappers: ReturnType<typeof mount>[] = [];
const keep = (wrapper: ReturnType<typeof mount>) => { wrappers.push(wrapper); return wrapper; };
const button = (wrapper: ReturnType<typeof mount>, label: string) => wrapper.findAll("button").find(item => item.text() === label)!;
const panel = () => keep(mount(RequirementReviewPanel, { props: props(), global: { stubs: { RequirementMatrix: true } } }));
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); vi.stubGlobal("crypto", { randomUUID: () => "new-review-request" });
  project.getProjectOverview.mockResolvedValue({ project: { id: saved.first.project_id, status: "active" } });
  service.listRequirementReviews.mockResolvedValue(page([])); service.listRequirements.mockResolvedValue(page([]));
  service.submitRequirementReview.mockImplementation((_p, _d, _s, request) => Promise.resolve({ ...first(), request: normalizedReview(request),
    requirement_version: request.decision === "accept" ? first().requirement_version : null }));
  service.getRequirementReviewSubmission.mockResolvedValue(first());
  service.listRequirementVersions.mockResolvedValue([clone(saved.first.requirement_version), clone(saved.second.requirement_version)]);
  service.readRequirementMatrix.mockResolvedValue(clone(saved.matrix_v2));
});
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.unstubAllGlobals(); });

it("accepts explicit points and fixed case relations while preserving the actual snapshot", async () => {
  const w = panel(); await flushPromises();
  await w.get('[aria-label="需求标题"]').setValue("确认的订单保留期限");
  await w.get('[aria-label="需求正文"]').setValue("订单保留9天。");
  await button(w, "添加验证点").trigger("click");
  await w.get('[aria-label="验证点编号 1"]').setValue("retention");
  await w.get('[aria-label="验证点说明 1"]').setValue("完整验证9天保留期限");
  await w.get('input[type="checkbox"]').setValue(true);
  await w.get('[aria-label="评审理由"]').setValue("依据新版本原文确认，明确关联本条固定用例");
  await w.get("form").trigger("submit"); await flushPromises();
  const request = service.submitRequirementReview.mock.calls[0]![3] as RequirementReviewRequest;
  expect(request.points).toEqual([{ key: "retention", description: "完整验证9天保留期限" }]);
  expect(request.case_links).toEqual([{ case_version_id: saved.first.request.case_links[0]!.case_version_id, point_keys: ["retention"] }]);
  expect(w.text()).toContain("评审已保存"); expect(sessionStorage.getItem(cacheKey)).toBeNull();
  expect(props().view.snapshot).toEqual(saved.source_snapshot);
});

it("reads later history pages for the latest decision and never automatically inherits design links", async () => {
  service.listRequirementReviews.mockImplementation((_p, _d, _s, _limit, offset) => Promise.resolve(offset === 0 ? page([first()], 0, true) : page([second()], 1)));
  service.listRequirements.mockResolvedValue(page([{ requirement: saved.matrix_v2.requirement, version: saved.second.requirement_version }]));
  const w = panel(); await flushPromises();
  expect(w.text()).toContain(saved.second.id);
  await w.get('[aria-label="关联需求"]').setValue(saved.matrix_v2.requirement.id);
  expect((w.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false);
  await w.get('[aria-label="评审理由"]').setValue("确认关联，覆盖需要重新评审");
  await w.get("form").trigger("submit"); await flushPromises();
  const request = service.submitRequirementReview.mock.calls[0]![3] as RequirementReviewRequest;
  expect(request.expected_review_id).toBe(saved.second.id); expect(request.expected_version_id).toBe(saved.second.requirement_version!.id);
  expect(request.case_links).toEqual([]);
  // The fake response's old version conflicts with the new parent; it must not be accepted as success.
  expect(w.text()).toContain("产生的需求版本不一致");
  expect(service.listRequirementReviews.mock.calls.some(call => call[4] === 1)).toBe(true);
});

it("recovers an actually saved original fixture on refresh, including on an archived project", async () => {
  sessionStorage.setItem(cacheKey, JSON.stringify(saved.first.request));
  project.getProjectOverview.mockResolvedValue({ project: { id: saved.first.project_id, status: "archived" } });
  service.listRequirementReviews.mockResolvedValue(page([first()]));
  const w = panel(); await flushPromises();
  expect(service.getRequirementReviewSubmission).toHaveBeenCalledWith(saved.first.project_id, saved.first.document_id, saved.first.request.request_id);
  expect(w.text()).toContain("评审已保存"); expect(w.text()).toContain("不可提交新评审");
  expect(service.submitRequirementReview).not.toHaveBeenCalled(); expect(sessionStorage.getItem(cacheKey)).toBeNull();
});

it("preserves every original request field after unknown response/404 and retries unchanged", async () => {
  sessionStorage.setItem(cacheKey, JSON.stringify(saved.first.request));
  service.getRequirementReviewSubmission.mockRejectedValue(new HttpError("not found", 404));
  const w = panel(); await flushPromises();
  expect(w.text()).toContain("尚未查到原评审请求"); expect(button(w, "保存需求评审").attributes("disabled")).toBeDefined();
  await w.get('[aria-label="评审理由"]').setValue("后来的修改不能替换原请求");
  await button(w, "重试原需求评审").trigger("click"); await flushPromises();
  expect(service.submitRequirementReview.mock.calls[0]![3]).toEqual(normalizedReview(saved.first.request as RequirementReviewRequest));
  expect(sessionStorage.getItem(cacheKey)).toBeNull();
});

it("keeps a new unknown submission pending and never exposes a new request while it is unresolved", async () => {
  service.submitRequirementReview.mockRejectedValue(new TypeError("response lost"));
  const w = panel(); await flushPromises();
  await w.get('[aria-label="评审决定"]').setValue("defer"); await w.get('[aria-label="评审理由"]').setValue("等待业务确认");
  await w.get("form").trigger("submit"); await flushPromises();
  const pending = JSON.parse(sessionStorage.getItem(cacheKey)!);
  expect(pending.request_id).toBe("new-review-request"); expect(w.text()).toContain("提交结果尚未确认");
  expect(button(w, "保存需求评审").attributes("disabled")).toBeDefined(); expect(w.text()).not.toContain("重新读取依据并评审");
});

it("handles a known conflict by explicit reread without silently rebasing or resending", async () => {
  service.submitRequirementReview.mockRejectedValue(new HttpError("父版本冲突", 409));
  const w = panel(); await flushPromises();
  await w.get('[aria-label="评审决定"]').setValue("reject"); await w.get('[aria-label="评审理由"]').setValue("不采纳");
  await w.get("form").trigger("submit"); await flushPromises();
  expect(w.text()).toContain("原请求已明确拒绝"); expect(button(w, "重试原需求评审")).toBeUndefined();
  service.listRequirementReviews.mockResolvedValue(page([second()]));
  await button(w, "重新读取依据并评审").trigger("click"); await flushPromises();
  expect(w.text()).toContain(saved.second.id); expect(service.submitRequirementReview).toHaveBeenCalledTimes(1);
  expect(sessionStorage.getItem(cacheKey)).toBeNull();
});

it("refreshing the catalog does not silently rebase an existing draft onto a newer parent", async () => {
  service.listRequirements.mockResolvedValue(page([{ requirement: saved.matrix_v1.requirement, version: saved.first.requirement_version }]));
  const w = panel(); await flushPromises();
  await w.get('[aria-label="关联需求"]').setValue(saved.matrix_v1.requirement.id);
  await w.get('[aria-label="需求正文"]').setValue("仍基于V1的编辑内容");
  await w.get('[aria-label="评审理由"]').setValue("确认本次原依据");
  service.listRequirements.mockResolvedValue(page([{ requirement: saved.matrix_v2.requirement, version: saved.second.requirement_version }]));
  service.listRequirementReviews.mockResolvedValue(page([second()]));
  await button(w, "刷新需求与评审历史").trigger("click"); await flushPromises();
  expect(w.text()).toContain("本表单仍保留原编辑依据");
  await w.get("form").trigger("submit"); await flushPromises();
  expect(service.submitRequirementReview.mock.calls[0]![3].expected_version_id).toBe(saved.first.requirement_version!.id);
  expect(service.submitRequirementReview.mock.calls[0]![3].statement).toBe("仍基于V1的编辑内容");
});

it("refuses incomplete pagination and foreign reviews rather than using a partial latest decision", async () => {
  service.listRequirementReviews.mockResolvedValue(page([], 0, true));
  const w = panel(); await flushPromises();
  expect(w.text()).toContain("分页未前进"); expect(button(w, "保存需求评审").attributes("disabled")).toBeDefined();
  service.listRequirementReviews.mockResolvedValue(page([{ ...first(), project_id: "foreign" }]));
  await button(w, "刷新需求与评审历史").trigger("click"); await flushPromises();
  expect(w.text()).toContain("不一致"); expect(button(w, "保存需求评审").attributes("disabled")).toBeDefined();
});

it("drops delayed original-project history after changing the document context", async () => {
  let resolve!: (value: ReturnType<typeof page<RequirementReview>>) => void;
  service.listRequirementReviews.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const w = panel(); await flushPromises();
  const next = view(); next.snapshot.seed = { ...next.snapshot.seed, document_id: "new-document" };
  await w.setProps({ documentId: "new-document", view: next }); await flushPromises();
  resolve(page([first()])); await flushPromises();
  expect(w.text()).not.toContain(saved.first.request.note); expect(w.text()).not.toContain(saved.first.id);
});

it("shows actual saved V1 and V2 matrices separately, preserving each captured pointer and unknown execution", async () => {
  service.readRequirementMatrix.mockImplementation((_p, _d, _r, selected) => Promise.resolve(clone(selected === saved.matrix_v1.version.id ? saved.matrix_v1 : saved.matrix_v2)));
  const w = keep(mount(RequirementMatrix, { props: { projectId: saved.first.project_id, documentId: saved.first.document_id,
    requirementId: saved.matrix_v2.requirement.id, revision: 1 } })); await flushPromises();
  expect(w.text()).toContain("关联用例待复核"); expect(w.text()).toContain("执行结果尚未关联");
  await w.get('[aria-label="需求历史版本"]').setValue(saved.matrix_v1.version.id); await flushPromises();
  expect(w.text()).toContain("已有设计覆盖");
  // V1 fixture was captured before V2; its then-current pointer must not be fabricated as V2.
  expect(w.text().includes("正在查看历史版本")).toBe(saved.matrix_v1.version.id !== saved.matrix_v1.requirement.current_version_id);
  expect(w.text()).toContain(saved.matrix_v1.points[0]!.case_links[0]!.case_version_id);
});

it("does not show a foreign matrix or use external statements as markup", async () => {
  const bad = clone(saved.matrix_v2) as unknown as Matrix; bad.requirement.project_id = "foreign";
  service.readRequirementMatrix.mockResolvedValue(bad);
  const w = keep(mount(RequirementMatrix, { props: { projectId: saved.first.project_id, documentId: saved.first.document_id,
    requirementId: saved.matrix_v2.requirement.id, revision: 1 } })); await flushPromises();
  expect(w.text()).toContain("不一致"); expect(w.find(".matrix-point").exists()).toBe(false);
  const safe = clone(saved.matrix_v2); safe.version.statement = '<img src=x onerror="alert(1)">';
  service.readRequirementMatrix.mockResolvedValue(safe); await button(w, "刷新需求版本与矩阵").trigger("click"); await flushPromises();
  expect(w.find("img").exists()).toBe(false); expect(w.text()).toContain(safe.version.statement);
});

it("ignores an old matrix response after a new revision was loaded", async () => {
  let resolve!: (value: Matrix) => void;
  service.readRequirementMatrix.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const w = keep(mount(RequirementMatrix, { props: { projectId: saved.first.project_id, documentId: saved.first.document_id,
    requirementId: saved.matrix_v2.requirement.id, revision: 1 } })); await flushPromises();
  await w.setProps({ revision: 2 }); await flushPromises();
  const delayed = clone(saved.matrix_v1) as unknown as Matrix; delayed.requirement.project_id = "foreign";
  resolve(delayed); await flushPromises();
  expect(w.text()).toContain("关联用例待复核"); expect(w.text()).not.toContain("不一致");
});

it("keeps the existing impact renderer read-only until the formal review entry is opened", async () => {
  const w = keep(mount(DocumentImpactSnapshot, { props: { ...props() } }));
  expect(service.listRequirementReviews).not.toHaveBeenCalled();
  await button(w, "打开需求评审与追踪").trigger("click"); await flushPromises();
  expect(service.listRequirementReviews).toHaveBeenCalled(); expect(w.findComponent(RequirementReviewPanel).exists()).toBe(true);
});

it("stops reading pages when their context is no longer current", async () => {
  let valid = true;
  const result = await readAllRequirementPages(async () => { valid = false; return page([first()], 0, true); }, () => valid, () => { throw new Error("stale must not validate"); });
  expect(result).toBeNull();
});
