import type { GenerationGroup, GenerationGroupView, GenerationScope, GenerationTaskRequest } from "../../../services/caseGeneration.types";

export function normalizedGeneration(value: GenerationTaskRequest): GenerationTaskRequest {
  return { creation_key: value.creation_key, requirement_id: value.requirement_id, requirement_version_id: value.requirement_version_id,
    target_case_version_ids: value.target_case_version_ids ?? [], include_new_cases: value.include_new_cases ?? false,
    new_case_mode_key: value.new_case_mode_key ?? null, max_new_cases: value.max_new_cases ?? 1, model_key: value.model_key ?? null };
}

export function assertGenerationGroup(group: GenerationGroup, projectId: string, documentId: string): void {
  if (group.project_id !== projectId || group.document_id !== documentId || !group.module_ids.length
    || new Set(group.module_ids).size !== group.module_ids.length) throw new Error("生成组与当前项目、文档或固定模块不一致");
}

export function assertGenerationScope(scope: GenerationScope, group: GenerationGroup, request?: GenerationTaskRequest): void {
  if (scope.project_id !== group.project_id || scope.document_id !== group.document_id || scope.sha256 !== group.scope_sha256
    || scope.requirement.id !== scope.request.requirement_version_id || scope.requirement.requirement_id !== scope.request.requirement_id
    || scope.review.id !== scope.requirement.review_id || scope.review.requirement_version?.id !== scope.requirement.id
    || scope.review.project_id !== group.project_id || scope.review.document_id !== group.document_id
    || JSON.stringify(scope.modules.map(item => item.id)) !== JSON.stringify(group.module_ids)) {
    throw new Error("生成范围与原组、需求版本或评审不一致");
  }
  if (request && (group.creation_key !== request.creation_key || JSON.stringify(normalizedGeneration({
    creation_key: group.creation_key, ...scope.request })) !== JSON.stringify(normalizedGeneration(request)))) {
    throw new Error("保存的生成组与原请求参数不一致，原请求继续保留");
  }
}

export function assertGenerationReport(report: GenerationGroupView, group: GenerationGroup, scope: GenerationScope): void {
  assertGenerationGroup(report.group, group.project_id, group.document_id);
  if (JSON.stringify(report.group) !== JSON.stringify(group)
    || JSON.stringify(report.modules.map(item => item.module_id)) !== JSON.stringify(group.module_ids)
    || new Set(report.modules.map(item => item.id)).size !== report.modules.length
    || report.modules.some(item => item.project_id !== group.project_id || item.document_id !== group.document_id
      || item.group_id !== group.id || (item.checkpoint?.receipt &&
        (item.checkpoint.receipt.module_id !== item.module_id || item.checkpoint.receipt.scope_sha256 !== scope.sha256)))) {
    throw new Error("生成结果与固定组、模块或收据不一致");
  }
}
