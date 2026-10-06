import type { BusinessEnvironmentSnapshot } from "./businessEnvironments.types";
import type { BusinessApplicabilityReview } from "./businessApplicability.types";

export interface BusinessEnvironmentSelection {
  environment_id: string;
  snapshot_id: string;
  expected_snapshot_sha256: string;
}

export interface BusinessPlanningEnvironment {
  version: "business-planning-environment-v1";
  snapshot: BusinessEnvironmentSnapshot;
  reviews: BusinessApplicabilityReview[];
  sha256: string;
}
