"""The existing canonical case content shape, shared by creation and verification."""
import hashlib
import json


def version_content(payload):
    content = {
        "preconditions": payload.preconditions,
        "steps": [item.model_dump(mode="json") for item in payload.steps],
        "assertions": [item.model_dump(mode="json") for item in payload.assertions],
        "test_data": payload.test_data,
        "cleanup": payload.cleanup,
        "source_refs": [item.model_dump(mode="json") for item in payload.source_refs],
        "model_key": payload.model_key,
        "prompt_version": payload.prompt_version,
        "skill_versions": payload.skill_versions,
    }
    # Preserve the exact legacy shape, including omission of empty new fields.
    if payload.content_json is not None:
        content["content_json"] = payload.content_json
    if payload.execution_notes:
        content["execution_notes"] = payload.execution_notes
    if payload.generation_metadata:
        content["generation_metadata"] = payload.generation_metadata
    return content


def version_content_hash(payload):
    canonical = json.dumps(version_content(payload), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
