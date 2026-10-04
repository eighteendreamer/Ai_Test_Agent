import type { DocumentDifferenceTaskRecord, DocumentDifferenceTaskRequest } from "../../../types";

export function assertDifferenceTask(task: DocumentDifferenceTaskRecord, projectId: string, documentId: string,
  expected: { taskId?: string; request?: DocumentDifferenceTaskRequest } = {}): void {
  const request = expected.request;
  if (task.project_id !== projectId || task.document_id !== documentId || (expected.taskId && task.id !== expected.taskId)
    || (request && (task.creation_key !== request.creation_key || task.old_snapshot_id !== request.old_snapshot_id
      || task.new_snapshot_id !== request.new_snapshot_id))) {
    throw new Error("比较任务与指定项目、文档或原请求身份不一致");
  }
}
