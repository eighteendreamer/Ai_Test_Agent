import type { GenerationGroup, GenerationRetryView, GenerationScope, GenerationTask } from "../../../services/caseGeneration.types";

export function retryAvailability(task: GenerationTask): { strategy: "frozen_request" | "saved_response" | null; reason: string } {
  if (task.status !== "failed") return { strategy: null, reason: "只有明确失败的任务可以创建重试。" };
  const checkpoint = task.checkpoint;
  if (checkpoint?.receipt || checkpoint?.phase === "result_saved") return { strategy: null, reason: "原任务已有结果收据，请读取原结果。" };
  if (checkpoint?.phase === "response_saved" && checkpoint.response) return { strategy: "saved_response", reason: "新任务将复用已保存响应，不再次调用模型。" };
  if (checkpoint || task.error_code === "call_outcome_unknown") return { strategy: null, reason: "原调用结果未知且没有持久响应，不能重新发送。" };
  return { strategy: "frozen_request", reason: "新任务将执行原固定请求，使用新提交时的项目生成策略。" };
}

export function assertGenerationRetry(value: GenerationRetryView, parent: GenerationTask, group: GenerationGroup,
  scope: GenerationScope, creationKey?: string): void {
  const { retry, task } = value;
  const module = scope.modules.find(item => item.id === parent.module_id);
  const availability = retryAvailability(parent);
  if (!module || !availability.strategy || retry.implementation_version !== "generation-retry-v1"
    || parent.project_id !== group.project_id || parent.document_id !== group.document_id || parent.group_id !== group.id
    || retry.parent_task_id !== parent.id || retry.project_id !== group.project_id || retry.document_id !== group.document_id
    || retry.group_id !== group.id || retry.group_sha256 !== group.sha256 || retry.scope_sha256 !== scope.sha256
    || retry.module_id !== module.id || retry.module_sha256 !== module.sha256 || retry.strategy !== availability.strategy
    || (creationKey !== undefined && retry.creation_key !== creationKey)
    || !retry.id || !retry.sha256 || !retry.parent_task_sha256
    || !retry.task_id || retry.task_id === parent.id || retry.task_id !== task.id
    || task.project_id !== group.project_id || task.document_id !== group.document_id || task.group_id !== group.id
    || task.module_id !== module.id || task.version_id !== parent.version_id
    || ![group.project_id, "global"].includes(retry.config.scope_key)
    || (retry.strategy === "saved_response" ? JSON.stringify(retry.response) !== JSON.stringify(parent.checkpoint?.response) : retry.response !== null)
    || (task.checkpoint?.receipt && (task.checkpoint.receipt.module_id !== module.id || task.checkpoint.receipt.scope_sha256 !== scope.sha256))
    || (retry.strategy === "saved_response" && task.checkpoint?.response
      && JSON.stringify(task.checkpoint.response) !== JSON.stringify(retry.response))) {
    throw new Error("重试关系与原请求、父任务或固定范围不一致，原编号继续保留");
  }
}
