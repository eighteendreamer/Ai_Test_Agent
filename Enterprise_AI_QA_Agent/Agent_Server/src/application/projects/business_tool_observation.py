"""Inspect actual persisted origins; response values are not deployment decisions."""
from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.application.test_runs.agent_case_assessment import literal_value
from src.modes.api_testing_mode.http_observation import HTTP_OBSERVATION_KEY, HttpResponseObservation, target_in_scope
from src.runtime.postgres_business_environment_store import environment_digest
from src.schemas.business_tool_observation import BusinessToolObservation
from src.schemas.tool_job import ToolOutputObservation


def inspect_tool_observation(snapshot, job, request):
    if job.metadata.get("project_id") != snapshot.project_id:
        raise KeyError("工具证据不属于当前项目")
    if job.status.value not in {"completed", "partial", "failed", "denied", "cancelled"}:
        raise ValueError("工具证据尚无终态，请读取原任务状态")
    value = literal_value(job.output_payload, request.output_path_tokens)
    sanitized = OutputSafetyPolicy().sanitize_for_audit(value)
    limitations, observation, status = [], None, "unknown"
    task = job.output_payload.get("task_result") if job.tool_key == "api-test-runner" else None
    raw = task.get("execution_checkpoint", {}) if isinstance(task, dict) else {}
    if isinstance(raw, dict) and HTTP_OBSERVATION_KEY in raw:
        try:
            observation = HttpResponseObservation.model_validate(raw[HTTP_OBSERVATION_KEY])
        except ValueError:
            limitations.append("invalid_http_observation")
    if observation is None:
        limitations.append("actual_response_origin_unavailable")
    elif observation.acquisition != "network":
        limitations.append("completed_request_is_not_new_network_evidence")
    else:
        status = "matched" if all(target_in_scope(hop.target, snapshot.configuration.payload.base_url)
            for hop in observation.hops) else "mismatch"
        if status == "mismatch":
            limitations.append("request_or_response_hop_outside_fixed_target")
        if observation.observed_at.tzinfo is None or observation.observed_at < snapshot.captured_at:
            limitations.append("observation_precedes_fixed_target_or_has_no_timezone")
            status = "unknown"
        try:
            checkpoint = ToolOutputObservation.model_validate(job.metadata.get("tool_output_observation"))
            if checkpoint.output_hash != environment_digest(job.output_payload):
                raise ValueError("Returned output changed after checkpoint")
            if not isinstance(task, dict) or task.get("response_status") != observation.hops[-1].status_code:
                raise ValueError("Response status and origin evidence differ")
        except ValueError:
            limitations.append("returned_output_checkpoint_missing_or_different")
            status = "unknown"
    if environment_digest(value) != environment_digest(sanitized):
        limitations.append("selected_value_redacted")
    return BusinessToolObservation(project_id=snapshot.project_id, environment_id=snapshot.environment_id,
        environment_snapshot_id=snapshot.id, environment_snapshot_sha256=snapshot.sha256,
        tool_job_id=job.id, tool_key=job.tool_key, call_id=job.call_id, session_id=job.session_id, turn_id=job.turn_id,
        run_id=job.metadata.get("run_id"), run_item_id=job.metadata.get("run_item_id"), attempt_id=job.metadata.get("attempt_id"),
        job_status=job.status.value, output_sha256=environment_digest(job.output_payload),
        output_path_tokens=request.output_path_tokens, value=sanitized, value_sha256=environment_digest(sanitized),
        http_observation=observation, target_status=status, limitations=limitations,
        observed_at=observation.observed_at if observation else None)
