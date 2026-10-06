import type { RequirementMatrix } from "../../../services/requirements.types";

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
