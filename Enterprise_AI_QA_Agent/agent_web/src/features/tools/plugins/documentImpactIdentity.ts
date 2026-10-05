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
  if (result.implementation_version === "impact-coordinator-v2") {
    const plan = result.reasoning_checkpoint;
    const tasks = result.discovery_tasks;
    if (!plan || !tasks || result.protocol.version !== "impact-coordinator-v2" || plan.task_id !== snapshot.task_id
      || plan.input_sha256 !== result.input_sha256 || plan.manifest_sha256 !== result.manifest_sha256) {
      throw new Error("影响分组计划与固定任务依据不一致");
    }
    const members = tasks.flatMap(task => task.evidence_outcomes.map(outcome => `${outcome.unit.kind}:${outcome.unit.id}`));
    if (new Set(members).size !== members.length || result.units.some(unit => unit.unit.kind !== "case" && unit.candidates.length > 0)) {
      throw new Error("影响分组结果包含重复声明或复制候选，与固定证据不一致");
    }
  }
}
