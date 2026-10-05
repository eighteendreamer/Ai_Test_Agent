import type { DocumentImpactSnapshotView } from "../../../services/documentImpacts.types";
import type { RequirementBundle, RequirementPage, RequirementReview, RequirementReviewRequest, RequirementVersion } from "../../../services/requirements.types";

export function assertRequirement(value: RequirementBundle, projectId: string, documentId: string): void {
  if (value.requirement.project_id !== projectId || value.requirement.document_id !== documentId
    || value.version.requirement_id !== value.requirement.id) throw new Error("需求与当前项目、文档或版本身份不一致");
}

export function normalizedReview(payload: RequirementReviewRequest): RequirementReviewRequest {
  return { request_id: payload.request_id, rule_id: payload.rule_id, decision: payload.decision, note: payload.note,
    expected_review_id: payload.expected_review_id ?? null, requirement_id: payload.requirement_id ?? null,
    expected_version_id: payload.expected_version_id ?? null, change: payload.change ?? null,
    title: payload.title ?? null, statement: payload.statement ?? null, points: payload.points ?? [], case_links: payload.case_links ?? [] };
}

export function assertReview(value: RequirementReview, projectId: string, documentId: string,
  view: DocumentImpactSnapshotView, payload?: RequirementReviewRequest): void {
  const source = value.source;
  if (value.project_id !== projectId || value.document_id !== documentId || source.snapshot_id !== view.snapshot.id
    || source.task_id !== view.snapshot.task_id || source.snapshot_sha256 !== view.snapshot.sha256
    || source.input_sha256 !== view.snapshot.input_sha256 || source.manifest_sha256 !== view.snapshot.manifest_sha256
    || source.rule.id !== value.request.rule_id || !view.result.rules.some(rule => rule.id === value.request.rule_id)
    || (payload && JSON.stringify(normalizedReview(value.request)) !== JSON.stringify(normalizedReview(payload)))) {
    throw new Error("需求评审与当前固定结果或原请求参数不一致");
  }
  if ((value.request.decision === "accept") !== !!value.requirement_version || (value.requirement_version &&
    (value.requirement_version.review_id !== value.id
    || value.requirement_version.parent_version_id !== (value.request.expected_version_id ?? null)
    || (value.request.requirement_id && value.requirement_version.requirement_id !== value.request.requirement_id)))) {
    throw new Error("需求评审与产生的需求版本不一致");
  }
}

export async function readAllRequirementPages<T>(read: (offset: number) => Promise<RequirementPage<T>>,
  current: () => boolean, validate: (item: T) => void): Promise<T[] | null> {
  const items: T[] = []; let offset = 0;
  while (current()) {
    const page = await read(offset);
    if (!current()) return null;
    if (page.offset !== offset || (page.has_more && page.items.length === 0)) throw new Error("需求历史分页未前进，未取得完整历史");
    page.items.forEach(validate); items.push(...page.items);
    if (!page.has_more) return items;
    offset += page.items.length;
  }
  return null;
}

export function assertVersions(versions: RequirementVersion[], requirementId: string): void {
  if (!versions.length || versions.some(version => version.requirement_id !== requirementId)) {
    throw new Error("需求历史版本与指定需求不一致");
  }
}
