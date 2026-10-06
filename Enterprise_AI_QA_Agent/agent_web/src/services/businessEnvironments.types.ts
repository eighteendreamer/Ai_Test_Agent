export interface BusinessEnvironmentCreateRequest { request_id: string; environment_key: string }
export interface BusinessEnvironmentRecord {
  implementation_version: "business-environment-v1"; id: string; project_id: string;
  request: BusinessEnvironmentCreateRequest; request_sha256: string; status: "active" | "archived";
  created_at: string; updated_at: string; archived_at: string | null;
}
export interface BusinessTargetConfiguration { display_name: string; base_url: string; description?: string }
export interface BusinessTargetConfigurationRecord {
  config_key: "project.environment_target"; project_id: string; environment_id: string; scope_key: string;
  revision: number; payload: Required<BusinessTargetConfiguration>; sha256: string;
}
export interface BusinessEnvironmentCaptureRequest {
  request_id: string; expected_config_revision: number; expected_config_sha256: string;
}
export interface BusinessEnvironmentSnapshot {
  implementation_version: "business-environment-snapshot-v1"; id: string; project_id: string;
  environment_id: string; environment_key: string; registration_request_sha256: string;
  request: BusinessEnvironmentCaptureRequest; request_sha256: string;
  configuration: BusinessTargetConfigurationRecord; deployment_status: "unknown"; captured_at: string; sha256: string;
}
export interface BusinessEnvironmentPage { items: BusinessEnvironmentRecord[]; limit: number; offset: number; has_more: boolean }
export interface BusinessEnvironmentSnapshotPage { items: BusinessEnvironmentSnapshot[]; limit: number; offset: number; has_more: boolean }

export interface BusinessToolObservationRequest { tool_job_id: string; output_path_tokens: string[] }
export interface ApiHttpObservation {
  implementation_version: "api-http-observation-v1"; acquisition: "network" | "completed_request";
  observed_at: string;
  hops: { method: string; status_code: number; target: {
    scheme: "http" | "https"; host: string; port: number; path: string;
    query_omitted: boolean; userinfo_omitted: boolean; fragment_omitted: boolean;
  } }[];
}
export interface BusinessToolObservation {
  implementation_version: "business-tool-observation-v1"; project_id: string; environment_id: string;
  environment_snapshot_id: string; environment_snapshot_sha256: string; tool_job_id: string; tool_key: string;
  call_id: string; session_id: string; turn_id: string; run_id: string | null; run_item_id: string | null;
  attempt_id: string | null; job_status: string; output_sha256: string; output_path_tokens: string[];
  value: unknown; value_sha256: string; http_observation: ApiHttpObservation | null;
  target_status: "matched" | "mismatch" | "unknown"; limitations: string[]; observed_at: string | null;
  deployment_status: "unknown";
}
