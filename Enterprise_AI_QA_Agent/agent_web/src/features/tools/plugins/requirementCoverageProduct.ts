import type { RequirementBundle, RequirementCoverageRequest, RequirementCoverageReview, RequirementMatrix } from "../../../services/requirements.types";
import { assertRequirement } from "./requirementProduct";

const hash = /^[0-9a-f]{64}$/;
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
export function normalizedCoverage(value: RequirementCoverageRequest): RequirementCoverageRequest {
  return { request_id: value.request_id, expected_coverage_id: value.expected_coverage_id ?? null, note: value.note,
    case_links: value.case_links.map(link => ({ case_version_id: link.case_version_id, content_hash: link.content_hash,
      point_keys: [...link.point_keys], generation_task_id: link.generation_task_id ?? null })) };
}
export function validateCoverageRequest(value: RequirementCoverageRequest, basis: RequirementBundle): void {
  if (!value || typeof value.request_id !== "string" || !value.request_id || value.request_id.length > 160
    || typeof value.note !== "string" || !value.note.trim() || value.note.length > 8000
    || (value.expected_coverage_id != null && !uuid.test(value.expected_coverage_id))
    || !Array.isArray(value.case_links) || value.case_links.length > 100
    || Object.keys(value).some(key => !["request_id", "expected_coverage_id", "note", "case_links"].includes(key))) throw new Error("覆盖确认原参数无效，请保留原编号并核对");
  const points = new Set(basis.version.points.map(point => point.key));
  const versions = new Set<string>();
  for (const link of value.case_links) {
    if (!link || !uuid.test(link.case_version_id) || !hash.test(link.content_hash)
      || !Array.isArray(link.point_keys) || !link.point_keys.length || link.point_keys.length > 100
      || link.point_keys.some(key => !points.has(key)) || new Set(link.point_keys).size !== link.point_keys.length
      || (link.generation_task_id != null && !uuid.test(link.generation_task_id)) || versions.has(link.case_version_id)
      || Object.keys(link).some(key => !["case_version_id", "content_hash", "point_keys", "generation_task_id"].includes(key))) throw new Error("覆盖版本、哈希、验证点或生成来源无效");
    versions.add(link.case_version_id);
  }
  if (basis.version.change === "removed" && value.case_links.length) throw new Error("移除需求版本不能新增设计覆盖");
}
export function assertCoverage(value: RequirementCoverageReview, basis: RequirementBundle, payload?: RequirementCoverageRequest): void {
  const { requirement, version } = basis;
  if (value.implementation_version !== "requirement-coverage-v1" || value.project_id !== requirement.project_id
    || value.document_id !== requirement.document_id || value.requirement_id !== requirement.id
    || value.requirement_version_id !== version.id || value.requirement_version_sha256 !== version.content_sha256
    || !uuid.test(value.id) || !hash.test(value.sha256) || !hash.test(value.request_sha256)
    || !Number.isInteger(value.revision) || value.revision < 1
    || value.previous_coverage_id !== (value.request.expected_coverage_id ?? null)) throw new Error("覆盖确认与固定项目、文档或需求版本不一致");
  validateCoverageRequest(value.request, basis);
  if (payload && JSON.stringify(normalizedCoverage(value.request)) !== JSON.stringify(normalizedCoverage(payload))) throw new Error("覆盖确认与原请求参数不一致，原编号继续保留");
  if (value.case_links.length !== value.request.case_links.length) throw new Error("确认记录没有保留完整关联集合");
  value.case_links.forEach((link, index) => {
    const given = value.request.case_links[index];
    if (!uuid.test(link.case_id) || !hash.test(link.version_record_sha256) || link.case_version_id !== given.case_version_id
      || link.content_hash !== given.content_hash || JSON.stringify(link.point_keys) !== JSON.stringify(given.point_keys)
      || !["draft", "pending_review", "approved", "unavailable"].includes(link.review_status)
      || (link.review ? link.review.version_id !== link.case_version_id || link.review.status !== link.review_status : link.review_status !== "unavailable")
      || (given.generation_task_id ?? null) !== (link.generation?.task_id ?? null)) throw new Error("确认的完整用例版本、评审状态或来源不一致");
    if (link.generation && !link.generation.receipt.versions.some(item => item.case_id === link.case_id
      && item.version_id === link.case_version_id && item.content_hash === link.content_hash)) throw new Error("生成收据与确认版本不一致");
  });
}
export function assertCoverageMatrix(value: RequirementMatrix, basis: RequirementBundle, choice = "latest"): void {
  assertRequirement(value, basis.requirement.project_id, basis.requirement.document_id);
  if (value.requirement.id !== basis.requirement.id || value.version.id !== basis.version.id
    || value.version.content_sha256 !== basis.version.content_sha256) throw new Error("矩阵与所选固定需求版本不一致");
  if (value.coverage_review) assertCoverage(value.coverage_review, basis);
  if ((choice === "initial" && value.coverage_review) || (choice !== "initial" && choice !== "latest"
    && value.coverage_review?.id !== choice)) throw new Error("矩阵与指定覆盖历史不一致");
}
