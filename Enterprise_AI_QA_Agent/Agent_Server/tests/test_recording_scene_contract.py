"""Regression using actual newly recorded source from the failed product acceptance."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.application.recorder.scene_service import RecordingSceneService
from src.infrastructure.recording_scene_store import PostgresRecordingSceneStore
from src.schemas.recording_scene import SceneContent

SOURCE = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p5-recording-scene-product-first/version2.json"


def actual():
    value=json.loads(SOURCE.read_text(encoding="utf-8"))
    return SceneContent.model_validate(value["version"]["content"]),value["version"]["source"]


def service():
    return RecordingSceneService(store=None,recorder_service=None,project_service=None,case_service=None)


def test_full_recording_values_and_repeated_operations_survive():
    content,source=actual()
    service()._validate_content(content,source)
    original=source["original_draft"]
    clicks=[s for s in original["steps"] if s["data"]["recorder_type"]=="click"]
    assert len(clicks)==2 and clicks[0]["data"]["seq"]!=clicks[1]["data"]["seq"]
    fill=next(s for s in original["steps"] if s["data"]["recorder_type"]=="fill")
    assert fill["data"]["event"]["value"]=="完整输入😀"*40
    assert original["test_data"]["recording_observations"]


@pytest.mark.parametrize("mutation",["missing_seq","duplicate_alias","unknown_alias","wrong_target","tampered_original"])
def test_confirmed_invalid_references_are_visible(mutation):
    content,source=actual()
    if mutation=="missing_seq": content.steps[0].source_seq=999999
    elif mutation=="duplicate_alias": content.aliases.append(content.aliases[0].model_copy())
    elif mutation=="unknown_alias": content.steps[0].alias="unregistered"
    elif mutation=="wrong_target":
        content.aliases[0].source_seq=next(s["data"]["seq"] for s in source["original_draft"]["steps"] if s["data"]["recorder_type"]=="fill")
    else: source["original_draft"]["steps"][0]["action"]="tampered"
    with pytest.raises(ValueError): service()._validate_content(content,source)


def test_snapshot_integrity_uses_full_business_and_source():
    bundle=json.loads(SOURCE.read_text(encoding="utf-8"))
    version=bundle["version"]
    head={"id":bundle["scene_id"],"project_id":bundle["project_id"],"current_version_id":bundle["current_version_id"]}
    row={"id":version["id"],"version":version["version"],"request_id":version["request_id"],"request_hash":version["request_hash"],"payload":version}
    store=object.__new__(PostgresRecordingSceneStore)
    assert store._bundle(head,row).version.id==version["id"]
    row=deepcopy(row);row["payload"]["content"]["name"]="different"
    with pytest.raises(ValueError,match="hash mismatch"): store._bundle(head,row)


def test_no_selector_overwrite_or_unknown_contract_fields():
    content,_=actual()
    data=content.model_dump(mode="json")
    data["aliases"][0]["selector"]="#different"
    with pytest.raises(ValidationError): SceneContent.model_validate(data)


def test_repeating_a_recorded_action_is_an_explicit_edited_step():
    content,source=actual()
    content.steps.append(content.steps[0].model_copy())
    service()._validate_content(content,source)
