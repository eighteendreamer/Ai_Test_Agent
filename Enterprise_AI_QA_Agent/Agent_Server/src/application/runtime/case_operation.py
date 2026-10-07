"""Logical operations are independent of provider calls and do not imply effects."""
from copy import deepcopy
from dataclasses import dataclass
import logging

from src.application.runtime.case_action_binding import (
    ARGUMENT, OPERATION_PROTOCOL, CaseOperationSelection, CaseConsumptionSelection, _digest, business_arguments,
)
from src.schemas.http_observation import HttpResponseObservation

RECEIPT_KEY = "case_operation"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaseOperationRecoveryRequired:
    origin_job_id: str


def operation_identity(job, binding):
    raw = job.input_payload.get(ARGUMENT)
    selection_type = CaseConsumptionSelection if isinstance(raw, dict) and "consumption_closure" in raw else CaseOperationSelection
    selection = selection_type.model_validate(raw)
    scope = {"protocol":OPERATION_PROTOCOL, "operation_key":selection.operation_key,
        **{key:getattr(binding,key) for key in (
            "project_id", "run_id", "run_item_id", "plan_id", "attempt_id", "item_key", "context_sha256")}}
    return {"operation_id":_digest(scope), "scope":scope,
        "request_sha256":_digest({"tool_key":job.tool_key,
            "arguments":business_arguments(job.input_payload,binding)})}


def operation_receipt(identity, *, origin_job_id, decision, action_sent, output=None, origin_output=None):
    value = {"version":OPERATION_PROTOCOL, **identity, "origin_job_id":origin_job_id,
        "decision":decision, "action_sent":action_sent,"execution_claimed":decision == "dispatched"}
    if output is not None:
        value["replayed_output_sha256"] = _digest(output)
    if origin_output is not None:
        value["origin_output_sha256"] = _digest(origin_output)
    return {**value,"sha256":_digest(value)}


def replay_output(origin):
    output = deepcopy(origin.output_payload)
    # Only server-owned API task/campaign checkpoints are protocol locations.
    # Never search arbitrary response bodies for a key with the same spelling.
    def mark(checkpoint):
        if not isinstance(checkpoint,dict):
            return
        locations = [checkpoint,checkpoint.get("completed_request")]
        for location in locations:
            if not isinstance(location,dict) or "http_observation_v1" not in location:
                continue
            try:
                observation = HttpResponseObservation.model_validate(location["http_observation_v1"])
            except ValueError:
                # Preserve invalid original evidence; it cannot prove a network
                # observation, but must not block recovery of a returned output.
                logger.warning("case_operation_http_origin_unavailable",extra={"origin_job_id":origin.id})
                continue
            location["http_observation_v1"] = observation.model_copy(
                update={"acquisition":"completed_request"}).model_dump(mode="json")
    if origin.tool_key == "api-test-runner":
        task = output.get("task_result")
        if isinstance(task,dict):
            mark(task.get("execution_checkpoint"))
        mark(output.get("execution_checkpoint"))
        state = output.get("api_testing_state")
        if isinstance(state,dict):
            mark(state.get("execution_checkpoint"))
            campaign = state.get("campaign")
            if isinstance(campaign,dict):
                for task in campaign.get("tasks",[]):
                    if isinstance(task,dict):
                        mark(task.get("execution_checkpoint"))
    output["case_operation_replay"] = {"origin_job_id":origin.id,
        "origin_output_sha256":_digest(origin.output_payload), "action_sent":False}
    return output
