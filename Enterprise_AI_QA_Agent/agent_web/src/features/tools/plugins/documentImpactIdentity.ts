import type { DocumentImpactTaskRecord, DocumentImpactTaskRequest, DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";

export function assertImpactTask(task: DocumentImpactTaskRecord, projectId: string, documentId: string,
  expected: { taskId?: string; request?: DocumentImpactTaskRequest } = {}): void {
  if (task.project_id !== projectId || task.document_id !== documentId || (expected.taskId && task.id !== expected.taskId)
    || (expected.request && (task.creation_key !== expected.request.creation_key
      || task.difference_snapshot_id !== expected.request.difference_snapshot_id))) {
    throw new Error("影响任务与指定项目、文档或原请求身份不一致");
  }
}

export function assertImpactSnapshot(view: DocumentImpactSnapshotView, projectId: string, documentId: string,
  expected?: DocumentImpactTaskRecord): void {
  const { snapshot, result } = view;
  const seed = snapshot.seed;
  const difference = seed.difference_snapshot as { id?: unknown } | undefined;
  if (seed.project_id !== projectId || seed.document_id !== documentId || result.task_id !== snapshot.task_id
    || result.input_sha256 !== snapshot.input_sha256 || result.manifest_sha256 !== snapshot.manifest_sha256
    || result.status !== snapshot.status || result.units.length !== snapshot.unit_count || result.rules.length !== snapshot.rule_count
    || result.units.filter(unit => unit.status === "analyzed").length !== snapshot.analyzed_count
    || (expected && (expected.id !== snapshot.task_id || expected.snapshot_id !== snapshot.id
      || expected.version_id !== seed.version_id || difference?.id !== expected.difference_snapshot_id))) {
    throw new Error("影响结果与指定任务及固定输入不一致");
  }
}
