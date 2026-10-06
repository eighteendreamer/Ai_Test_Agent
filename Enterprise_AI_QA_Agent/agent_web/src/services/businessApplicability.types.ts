import type { TestCaseVersionRecord } from "../types";
import type { RequirementCoverageReview, RequirementVersion } from "./requirements.types";
import type { BusinessEnvironmentSnapshot, BusinessToolObservation, BusinessToolObservationRequest } from "./businessEnvironments.types";

export interface ApplicabilityObservationInput extends BusinessToolObservationRequest {
  expected_output_sha256: string; expected_value_sha256: string;
}
export interface EnvironmentPointDecision {
  point_key: string; status: "effective" | "not_effective" | "unknown"; note: string; observation_indexes: number[];
}
export interface EnvironmentCaseDecision {
  case_version_id: string; point_keys: string[]; status: "valid" | "invalid" | "unknown"; note: string;
}
export interface BusinessApplicabilityRequest {
  request_id: string; document_id: string; requirement_id: string; requirement_version_id: string;
  expected_snapshot_sha256: string; expected_requirement_sha256: string;
  coverage_id: string; expected_coverage_sha256: string; expected_review_id?: string | null;
  action?: "review" | "revoke"; note: string; observations?: ApplicabilityObservationInput[];
  points?: EnvironmentPointDecision[]; cases?: EnvironmentCaseDecision[];
}
export interface BusinessApplicabilityReview {
  implementation_version: "business-applicability-v1"; id: string; project_id: string; environment_id: string;
  snapshot: BusinessEnvironmentSnapshot; requirement_version: RequirementVersion; coverage: RequirementCoverageReview | null;
  request: BusinessApplicabilityRequest; request_sha256: string; previous_review_id: string | null; revision: number;
  observations: BusinessToolObservation[];
  cases: (EnvironmentCaseDecision & { version: TestCaseVersionRecord; version_record_sha256: string })[];
  scope: "selected_points_and_case_versions"; decision_source: "explicit_review"; created_at: string; sha256: string;
}
export interface BusinessApplicabilityPage { items: BusinessApplicabilityReview[]; limit: number; offset: number; has_more: boolean }
