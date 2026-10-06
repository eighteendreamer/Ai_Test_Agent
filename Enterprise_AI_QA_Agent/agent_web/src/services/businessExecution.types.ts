import type { BusinessApplicabilityReview } from "./businessApplicability.types";

export interface BusinessExecutionAdmission {
  version: "business-execution-admission-v1";
  decision: "no_explicit_invalid";
  project_id: string;
  run_id: string;
  run_item_id: string;
  plan_id: string;
  case_id: string;
  case_version_id: string;
  planning_basis_sha256: string;
  environment_id: string;
  snapshot_id: string;
  snapshot_sha256: string;
  execution_attempt_id: string;
  checked_at: string;
  reviews: BusinessApplicabilityReview[];
  sha256: string;
}
