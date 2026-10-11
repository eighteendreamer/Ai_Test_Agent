"""Resolve only explicitly saved scene expectations, never recorded actuals."""
import logging

from src.application.runtime.case_action_binding import _digest
from src.application.runtime.case_effect_expected_sources import referenced_expected_value
from src.schemas.scene_receipt import SCENE_RECEIPT_PROTOCOL
from src.application.test_runs.literal_evidence import literal_value

logger = logging.getLogger(__name__)


def scene_expected_value(reference, item, context):
    if context.scene_receipt_protocol != SCENE_RECEIPT_PROTOCOL:
        raise ValueError("当前固定运行未启用场景预期回执")
    matches = [e for e in context.evidence if e.id == reference.evidence_id]
    if len(matches) != 1 or reference.evidence_id not in item.evidence_ids:
        raise ValueError(f"执行项 {item.key} 的场景预期没有唯一已读来源")
    scene = matches[0]
    payload = scene.payload
    asset = {"content":payload.get("content"),"source":payload.get("source")}
    if (scene.kind != "scene" or scene.content_hash != reference.content_hash
        or scene.content_hash != _digest(asset) or scene.payload_hash != _digest(payload)
        or payload.get("project_id") != context.project_id or payload.get("scene_id") != scene.source_id
        or payload.get("version_id") != scene.version or payload.get("content_hash") != scene.content_hash
        or payload.get("evidence_id") != scene.id
        or scene.id != f"ui_recording_scene:{scene.source_id}:{scene.version}:{scene.content_hash}"):
        raise ValueError(f"执行项 {item.key} 的场景原件、投影或项目版本身份不一致")
    tokens = reference.payload_path_tokens
    # SceneContent defines the two authored expectation fields. Raw recorder
    # response, aliases and organization status describe evidence, not criteria.
    if not (len(tokens) >= 4 and tokens[0] == "content" and tokens[1] in {"steps","assertions"}
        and tokens[2].isdigit() and tokens[3] == "expected"):
        raise ValueError("场景预期必须引用已保存的步骤预期或业务断言，不能引用录制实际响应")
    if tokens[1] == "steps":
        authored = literal_value(payload, tokens[:4])
        if authored is None or (isinstance(authored, str) and not authored.strip()):
            raise ValueError("场景步骤尚未填写业务预期，不能用空白录制字段作为通过标准")
    proof = {"kind":"scene","project_id":context.project_id,"evidence_id":scene.id,
        "source_id":scene.source_id,"version":scene.version,"content_hash":scene.content_hash,
        "payload_hash":scene.payload_hash,"payload_path_tokens":list(tokens),
        "verification_status":payload.get("verification_status")}
    value, proof = referenced_expected_value(reference.model_dump(mode="json"),payload,proof,
        item_key=item.key,label="场景预期")
    logger.info("agent_case_scene_expected_resolved",extra={"project_id":context.project_id,
        "run_id":context.run_id,"run_item_id":context.run_item_id,"plan_item_key":item.key,
        "scene_id":scene.source_id,"scene_version_id":scene.version,"content_hash":scene.content_hash,
        "value_type":type(value).__name__})
    return value, proof
