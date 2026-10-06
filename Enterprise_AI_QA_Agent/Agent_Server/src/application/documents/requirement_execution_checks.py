"""Validate original result content and read only its body-phase assessments."""
from src.application.testing.verification_service import VerificationService
from src.runtime.postgres_requirement_store import requirement_hash
from src.schemas.run_management import RunItemCompletion, TestCaseResultRecord
from src.schemas.session import VerificationResult
from src.schemas.case_execution import CaseExecutionCheck


def fixed_result(value):
    result = TestCaseResultRecord.model_validate(value)
    content = result.model_dump(mode="json", include=set(RunItemCompletion.model_fields)-{"payload_hash"})
    if requirement_hash(content) != result.payload_hash:
        raise ValueError("执行结果正文与原payload_hash不同")
    return result


def body_checks(result, item_key, project_id):
    verifications = [VerificationResult.model_validate(value) for value in result.actual.get("verification_results", [])]
    if len({value.id for value in verifications}) != len(verifications):
        raise ValueError("原结果包含重复核验身份")
    tool_ids = {ref.evidence_id for ref in result.evidence_refs if ref.evidence_type == "tool_job"}
    values = {}
    for verification in verifications:
        if verification.metadata.get("phase") != "case":
            continue
        if (verification.id not in result.verification_ids or verification.verifier != "agent_case_receipt" or
            verification.metadata.get("project_id") != project_id or
            verification.metadata.get("run_id") != result.run_id or
            verification.metadata.get("run_item_id") != result.run_item_id or
            verification.metadata.get("plan_item_key") != item_key):
            raise ValueError("正文核验与原结果身份不同")
        checks = verification.metadata.get("checks", [])
        if (verification.assertion_count != len(checks) or
            verification.passed_count != sum(check.get("status") == "passed" for check in checks) or
            verification.failed_count != sum(check.get("status") == "failed" for check in checks)):
            raise ValueError("正文核验计数与实际检查不同")
        if checks and (not verification.evidence or any(
            ref.source_type != "tool_job" or ref.source_id not in tool_ids for ref in verification.evidence)):
            raise ValueError("正文核验没有匹配的实际工具证据")
        for index, check in enumerate(checks):
            original = CaseExecutionCheck.model_validate({key: check[key]
                for key in CaseExecutionCheck.model_fields if key in check})
            if (check.get("method") == "model") != (original.operator == "semantic"):
                raise ValueError("原核验方法与固定比较算子不同")
            if check.get("status") not in {"passed", "failed", "not_run"}:
                raise ValueError("原核验判定无效")
            if check.get("method") == "deterministic" and check["status"] != "not_run":
                status = "passed" if VerificationService._matches_assertion(
                    check["actual"], check["expected"], check["operator"]) else "failed"
                if status != check["status"]:
                    raise ValueError("原确定性核验与实际值不同")
            elif check.get("method") == "model" and (check.get("operator") != "semantic" or
                not check.get("model_key") or not check.get("reason") or
                (check["status"] != "not_run" and check.get("verdict") != check["status"])):
                raise ValueError("原语义核验缺少匹配的方法和依据")
            elif check.get("method") not in {"deterministic", "model"}:
                raise ValueError("原核验方法不可识别")
            values[(verification.id, index)] = check
    return values


def point_status(checks):
    statuses = [check.check["status"] for check in checks]
    if not statuses:
        return "not_assessed"
    if "failed" in statuses:
        return "failed"
    return "passed" if all(status == "passed" for status in statuses) else "partial"
