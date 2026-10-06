import type { RequirementMatrix, RequirementExecutionRequest, RequirementExecutionReview, RequirementExecutionCandidate } from "../../../services/requirements.types";
import type { TestRunDetail } from "../../../types";
import { assertCoverageMatrix } from "./requirementCoverageProduct";

const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const hash = /^[0-9a-f]{64}$/;
export const executionCoverage = (basis: RequirementMatrix) => basis.coverage_review?.id ?? "initial";
export const executionCoverageHash = (basis: RequirementMatrix) => basis.coverage_review?.sha256 ?? basis.version.content_sha256;
export function normalizedExecution(value: RequirementExecutionRequest): RequirementExecutionRequest {
  return { request_id: value.request_id, coverage_id: value.coverage_id.toLowerCase(),
    expected_execution_id: value.expected_execution_id?.toLowerCase() ?? null, note: value.note,
    checks: value.checks.map(check => ({ result_id: check.result_id.toLowerCase(), verification_id: check.verification_id.toLowerCase(),
      check_index: check.check_index, point_keys: [...check.point_keys] })) };
}
export function validateExecutionRequest(value: RequirementExecutionRequest, basis: RequirementMatrix, matchCoverage = true): void {
  if (!value || typeof value.request_id !== "string" || !value.request_id || value.request_id.length > 160
    || typeof value.note !== "string" || !value.note.trim() || value.note.length > 8000
    || typeof value.coverage_id !== "string" || (value.coverage_id !== "initial" && !uuid.test(value.coverage_id))
    || (matchCoverage && value.coverage_id.toLowerCase() !== executionCoverage(basis))
    || (value.expected_execution_id != null && !uuid.test(value.expected_execution_id))
    || !Array.isArray(value.checks) || value.checks.length > 200
    || Object.keys(value).some(key => !["request_id", "coverage_id", "expected_execution_id", "note", "checks"].includes(key))) throw new Error("执行确认原参数或覆盖依据无效，请保留原编号");
  const points = new Set(basis.version.points.map(point => point.key)); const refs = new Set<string>();
  for (const check of value.checks) {
    const key = `${check.result_id}/${check.verification_id}/${check.check_index}`.toLowerCase();
    if (!uuid.test(check.result_id) || !uuid.test(check.verification_id) || !Number.isInteger(check.check_index)
      || check.check_index < 0 || check.check_index > 199 || refs.has(key) || !Array.isArray(check.point_keys)
      || !check.point_keys.length || check.point_keys.length > 100 || check.point_keys.some(point => !points.has(point))
      || new Set(check.point_keys).size !== check.point_keys.length
      || Object.keys(check).some(key => !["result_id", "verification_id", "check_index", "point_keys"].includes(key))) throw new Error("正文核验引用或验证点无效");
    refs.add(key);
  }
}
export function assertExecution(value: RequirementExecutionReview, basis: RequirementMatrix, payload?: RequirementExecutionRequest, matchCoverage = true): void {
  if (value.implementation_version !== "requirement-execution-v1" || !uuid.test(value.id) || !hash.test(value.sha256)
    || !hash.test(value.request_sha256) || value.project_id !== basis.requirement.project_id || value.document_id !== basis.requirement.document_id
    || value.requirement_id !== basis.requirement.id || value.requirement_version_id !== basis.version.id
    || value.requirement_version_sha256 !== basis.version.content_sha256 || !Number.isInteger(value.revision) || value.revision < 1
    || value.previous_execution_id !== (value.request.expected_execution_id ?? null)
    || (matchCoverage && value.coverage_sha256 !== executionCoverageHash(basis))) throw new Error("执行确认与固定需求版本或覆盖依据不一致");
  validateExecutionRequest(value.request, basis, matchCoverage);
  if (payload && JSON.stringify(normalizedExecution(value.request)) !== JSON.stringify(normalizedExecution(payload))) throw new Error("执行确认与原请求参数不一致，原编号继续保留");
  if (value.checks.length !== value.request.checks.length) throw new Error("执行确认没有保留完整核验集合");
  value.checks.forEach((check, index) => {
    const given = value.request.checks[index];
    if (check.result_id !== given.result_id || check.verification_id !== given.verification_id || check.check_index !== given.check_index
      || JSON.stringify(check.point_keys) !== JSON.stringify(given.point_keys) || !hash.test(check.check_sha256)
      || !hash.test(check.result_payload_hash) || !hash.test(check.result_record_sha256) || !hash.test(check.version_record_sha256)
      || !["passed", "failed", "not_run"].includes(String(check.check.status))
      || (matchCoverage && !basis.points.some(point => point.case_links.some(link => link.case_id === check.case_id
        && link.case_version_id === check.case_version_id && link.version_record_sha256 === check.version_record_sha256
        && check.point_keys.every(key => link.point_keys.includes(key)))))) throw new Error("固定执行核验与原引用或用例版本不一致");
  });
}
export function assertExecutionView(value: RequirementMatrix, basis: RequirementMatrix, choice = "latest"): void {
  assertCoverageMatrix(value, basis, executionCoverage(basis)); assertExecutionMatrix(value);
  if (value.execution_review) assertExecution(value.execution_review, basis);
  if ((choice === "initial" && value.execution_review) || (choice !== "initial" && choice !== "latest" && value.execution_review?.id !== choice)) throw new Error("矩阵与指定执行历史不一致");
}
export function readBodyChecks(candidate: RequirementExecutionCandidate, detail: TestRunDetail, basis: RequirementMatrix) {
  const result = detail.results.find(value => value.id === candidate.result_id);
  if (detail.run.id !== candidate.run_id || detail.run.project_id !== basis.requirement.project_id || !result
    || result.run_id !== detail.run.id || result.case_id !== candidate.case_id || result.case_version_id !== candidate.case_version_id
    || result.status !== candidate.status || !hash.test(result.payload_hash)
    || !detail.items.some(item => item.id === result.run_item_id && item.result_id === result.id && item.case_version_id === result.case_version_id)) throw new Error("完整运行结果与候选身份不一致");
  const raw = result.actual.verification_results;
  if (!Array.isArray(raw)) throw new Error("原结果缺少结构化正文核验");
  const checks = candidate.checks.map(summary => {
    const verification = raw.find(value => value.id === summary.verification_id);
    const metadata = verification?.metadata;
    const check = metadata?.checks?.[summary.check_index] as Record<string, unknown> | undefined;
    if (!verification || !result.verification_ids.includes(verification.id) || verification.verifier !== "agent_case_receipt"
      || metadata.phase !== "case" || metadata.project_id !== basis.requirement.project_id || metadata.run_id !== detail.run.id
      || metadata.run_item_id !== result.run_item_id || !check || check.name !== summary.name || check.status !== summary.status
      || check.method !== summary.method || JSON.stringify(check.expected_path_tokens) !== JSON.stringify(summary.expected_path_tokens)) throw new Error("完整正文核验与候选摘要不一致");
    return { ...summary, check };
  });
  return { result, checks };
}

// Scope checks protect the displayed fixed association; server owns hash validation.
export function assertExecutionMatrix(matrix: RequirementMatrix): void {
  const review = matrix.execution_review;
  if (review && (review.project_id !== matrix.requirement.project_id
    || review.document_id !== matrix.requirement.document_id || review.requirement_id !== matrix.requirement.id
    || review.requirement_version_id !== matrix.version.id || review.requirement_version_sha256 !== matrix.version.content_sha256
    || review.request.coverage_id !== (matrix.coverage_review?.id ?? "initial")
    || review.coverage_sha256 !== (matrix.coverage_review?.sha256 ?? matrix.version.content_sha256))) {
    throw new Error("执行关联与固定需求版本或覆盖依据不一致");
  }
  for (const point of matrix.points) {
    const checks = point.execution_checks ?? [];
    const expected = review?.checks.filter(check => check.point_keys.includes(point.point.key)) ?? [];
    if (JSON.stringify(checks) !== JSON.stringify(expected)
      || checks.some(check => !point.case_links.some(link => link.case_id === check.case_id
        && link.case_version_id === check.case_version_id && link.version_record_sha256 === check.version_record_sha256))) {
      throw new Error("矩阵核验与固定执行关联或用例版本不一致");
    }
    const statuses = checks.map(check => check.check.status);
    const status = !statuses.length ? "not_assessed" : statuses.includes("failed") ? "failed"
      : statuses.every(value => value === "passed") ? "passed" : "partial";
    if (point.execution_status !== status || (checks.length && point.execution_scope !== "selected_checks")) {
      throw new Error("执行核验状态或范围与实际关联不一致");
    }
  }
}
