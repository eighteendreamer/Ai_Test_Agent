// @vitest-environment jsdom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import saved from "../../../../../docs/upgrade_2026-10-03/p4-requirement-execution-md-fixed/execution-md/verification.json";
import type { RequirementMatrix as Matrix } from "../../../services/requirements.types";
import RequirementMatrix from "./RequirementMatrix.vue";
import { assertExecutionMatrix } from "./requirementExecutionProduct";

const services = vi.hoisted(() => ({ readRequirementMatrix: vi.fn(), listRequirementVersions: vi.fn() }));
vi.mock("../../../services/requirements.api", () => services);
const original = () => structuredClone(saved.matrix) as unknown as Matrix;
const wrappers: ReturnType<typeof mount>[] = [];
const props = () => ({ projectId: saved.association.project_id, documentId: saved.association.document_id,
  requirementId: saved.association.requirement_id, revision: 1 });
const create = () => { const w = mount(RequirementMatrix, { props: props() }); wrappers.push(w); return w; };

beforeEach(() => {
  vi.resetAllMocks(); services.listRequirementVersions.mockResolvedValue([saved.version]);
  services.readRequirementMatrix.mockResolvedValue(original());
});
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()));

it("shows actual selected body checks separately from a failed cleanup and unknown deployment", async () => {
  const w = create(); await flushPromises();
  expect(w.text()).toContain("已关联正文执行核验");
  expect(w.text()).toContain("已关联核验：通过");
  expect(w.text()).toContain("已关联核验：未关联");
  expect(w.text()).toContain("原运行结果 failed");
  expect(w.text()).toContain("部分检查通过不能证明整个需求通过");
  expect(w.text()).toContain("当前环境是否生效未知");
  expect(w.text()).toContain(saved.results[0]!.id);
  expect(w.text()).toContain(saved.association.checks[0]!.case_version_id);
  expect(services.readRequirementMatrix).toHaveBeenCalledTimes(1);
});

it("retains False, zero, null and exact saved tool evidence in the complete matrix", async () => {
  const value = original(); value.execution_review = structuredClone(saved.literals) as unknown as Matrix["execution_review"];
  value.points[0]!.execution_checks = []; value.points[0]!.execution_status = "not_assessed";
  value.points[1]!.execution_checks = value.execution_review!.checks;
  value.points[1]!.execution_status = "passed";
  services.readRequirementMatrix.mockResolvedValue(value);
  const w = create(); await flushPromises();
  expect(w.text()).toContain('"actual": false'); expect(w.text()).toContain('"actual": 0');
  expect(w.text()).toContain('"actual": null'); expect(w.text()).toContain('"output_sha256"');
});

it.each(["version", "coverage", "check", "status"])("rejects a mismatched fixed %s without showing point results", async kind => {
  const value = original();
  if (kind === "version") value.execution_review!.requirement_version_id = "foreign";
  if (kind === "coverage") value.execution_review!.request.coverage_id = "initial";
  if (kind === "check") value.points[0]!.execution_checks![0]!.case_version_id = "foreign";
  if (kind === "status") value.points[0]!.execution_status = "failed";
  services.readRequirementMatrix.mockResolvedValue(value);
  const w = create(); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("不一致");
  expect(w.find(".matrix-point").exists()).toBe(false);
});

it("keeps the actual review immutable while accepting an unlinked historical matrix", () => {
  const value = original(); value.execution_review = null;
  value.points.forEach(point => { point.execution_checks = []; point.execution_status = "not_assessed"; });
  expect(() => assertExecutionMatrix(value)).not.toThrow();
  expect(original().execution_review).toEqual(saved.association);
});
