from __future__ import annotations

import json
from typing import Any

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.infrastructure.storage_utils import make_json_safe
from src.modes.smoke_testing_mode.catalog_store import SmokeCatalogStore
from src.modes.smoke_testing_mode.contracts import (
    RegressionCandidateCase,
    SmokeExecutionPlan,
    SmokeRunResult,
)


class SmokePlanStore:
    """Versioned RustFS storage plus PostgreSQL catalog for smoke test assets."""

    def __init__(
        self,
        *,
        artifact_storage_service: ArtifactStorageService | None,
        catalog_store: SmokeCatalogStore | None,
    ) -> None:
        self._artifact_storage_service = artifact_storage_service
        self._catalog_store = catalog_store

    async def initialize(self) -> None:
        if self._catalog_store is not None:
            await self._catalog_store.initialize()

    async def save_plan_version(
        self,
        plan: SmokeExecutionPlan,
        *,
        revision_reason: str = "",
        user_revision: str = "",
    ) -> tuple[SmokeExecutionPlan, list[str]]:
        warnings: list[str] = []
        json_name = f"plan.v{plan.version}.json"
        md_name = f"plan.v{plan.version}.md"
        json_uri = ""
        md_uri = ""
        try:
            json_uri = await self._store_json(plan, json_name, plan.model_dump(mode="json"))
            md_uri = await self._store_text(plan, md_name, self.plan_markdown(plan), "text/markdown")
            plan.rustfs_uris[json_name] = json_uri
            plan.rustfs_uris[md_name] = md_uri
            plan.rustfs_uris["plan_uri"] = json_uri
            plan.rustfs_uris["plan_md_uri"] = md_uri
        except Exception as exc:
            warnings.append(f"RustFS 保存方案版本失败：{exc}")

        if self._catalog_store is not None:
            try:
                await self._catalog_store.save_plan(plan)
                await self._catalog_store.save_plan_version(
                    plan,
                    plan_uri=json_uri,
                    plan_md_uri=md_uri,
                    revision_reason=revision_reason,
                    user_revision=user_revision,
                )
            except Exception as exc:
                warnings.append(f"PostgreSQL 登记方案版本失败：{exc}")
        return plan, warnings

    async def save_approved_plan(self, plan: SmokeExecutionPlan, selected_case_ids: list[str] | None = None) -> tuple[SmokeExecutionPlan, list[str]]:
        warnings: list[str] = []
        if selected_case_ids is not None:
            selected = set(selected_case_ids)
            for case in plan.cases:
                case.selected = case.case_id in selected
        plan.status = "approved_for_execution"
        try:
            plan.rustfs_uris["approved_plan_uri"] = await self._store_json(
                plan,
                "approved-plan.json",
                plan.model_dump(mode="json"),
            )
            plan.rustfs_uris["selected_cases_uri"] = await self._store_json(
                plan,
                "selected-cases.json",
                [case.model_dump(mode="json") for case in plan.cases if case.selected],
            )
        except Exception as exc:
            warnings.append(f"RustFS 保存冻结方案失败：{exc}")
        if self._catalog_store is not None:
            try:
                await self._catalog_store.save_plan(plan)
            except Exception as exc:
                warnings.append(f"PostgreSQL 登记冻结方案失败：{exc}")
        return plan, warnings

    async def save_run_result(
        self,
        *,
        plan: SmokeExecutionPlan,
        result: SmokeRunResult,
        report_markdown: str,
        regression_candidates: list[RegressionCandidateCase],
    ) -> tuple[SmokeRunResult, list[str]]:
        warnings: list[str] = []
        evidence_manifest = {
            "plan_id": result.plan_id,
            "run_id": result.run_id,
            "case_results": [item.model_dump(mode="json") for item in result.case_results],
        }
        try:
            result.rustfs_uris["run_result_uri"] = await self._store_json(plan, "run-result.json", result.model_dump(mode="json"))
            result.rustfs_uris["report_uri"] = await self._store_text(plan, "run-report.md", report_markdown, "text/markdown")
            result.rustfs_uris["evidence_manifest_uri"] = await self._store_json(plan, "evidence/evidence-manifest.json", evidence_manifest)
            result.rustfs_uris["regression_candidates_uri"] = await self._store_json(
                plan,
                "regression-candidates.json",
                [item.model_dump(mode="json") for item in regression_candidates],
            )
        except Exception as exc:
            warnings.append(f"RustFS 保存执行结果失败：{exc}")
        if self._catalog_store is not None:
            try:
                await self._catalog_store.save_run(result)
                await self._catalog_store.save_regression_candidates(regression_candidates)
            except Exception as exc:
                warnings.append(f"PostgreSQL 登记执行结果失败：{exc}")
        return result, warnings

    async def load_plan(self, plan_id: str) -> tuple[SmokeExecutionPlan | None, list[str]]:
        warnings: list[str] = []
        if self._catalog_store is None:
            warnings.append("PostgreSQL catalog 未配置，无法按 plan_id 加载方案。")
            return None, warnings
        try:
            row = await self._catalog_store.get_plan_record(plan_id)
        except Exception as exc:
            warnings.append(f"PostgreSQL 查询方案失败：{exc}")
            return None, warnings
        if not row:
            return None, warnings
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            try:
                return SmokeExecutionPlan.model_validate(metadata), warnings
            except Exception:
                pass
        uri = str(row.get("plan_uri") or row.get("approved_plan_uri") or "").strip()
        if not uri:
            warnings.append("catalog 中没有可读取的方案 URI。")
            return None, warnings
        if self._artifact_storage_service is None:
            warnings.append("ArtifactStorageService 未配置，无法读取 RustFS 方案。")
            return None, warnings
        try:
            stored = await self._artifact_storage_service.read_object_uri(uri)
            payload = json.loads(stored.get("content", b"{}").decode("utf-8"))
            return SmokeExecutionPlan.model_validate(payload), warnings
        except Exception as exc:
            warnings.append(f"RustFS 读取方案失败：{exc}")
            return None, warnings

    def plan_markdown(self, plan: SmokeExecutionPlan) -> str:
        lines = [
            f"# {plan.title} v{plan.version}",
            "",
            f"- Plan ID: {plan.plan_id}",
            f"- Project: {plan.project_scope or '未匹配'}",
            f"- Target: {plan.target_url or '未提供'}",
            f"- Status: {plan.status}",
            f"- Credential: {plan.credential_summary or '未找到测试凭据'}",
            "",
            "## 用例",
        ]
        for index, case in enumerate(plan.cases, start=1):
            checked = "[x]" if case.selected else "[ ]"
            lines.extend(
                [
                    "",
                    f"### {checked} {index}. {case.title}",
                    f"- Case ID: {case.case_id}",
                    f"- Type: {case.case_type}",
                    f"- Risk: {case.risk_level}",
                    f"- Eligible: {case.execution_eligible}",
                    f"- Requires Approval: {case.requires_approval}",
                ]
            )
            for step in case.steps:
                lines.append(f"- Step: {step.title}")
                if step.api:
                    lines.append(f"  - API: {step.api.method.upper()} {step.api.url}")
                    if step.api.query:
                        lines.append(f"  - Query: `{json.dumps(step.api.query, ensure_ascii=False)}`")
                    if step.api.body is not None:
                        lines.append(f"  - Body: `{json.dumps(step.api.body, ensure_ascii=False)}`")
                    lines.append(f"  - Expected Status: {step.api.expected_status}")
                    if step.api.expected_fields:
                        lines.append(f"  - Expected Fields: {', '.join(step.api.expected_fields)}")
                if step.ui:
                    lines.append(f"  - UI: {step.ui.action} {step.ui.page_url}")
                    if step.ui.locator:
                        lines.append(f"  - Locator: `{step.ui.locator}`")
                    if step.ui.expected_visible_text:
                        lines.append(f"  - Expected Text: {step.ui.expected_visible_text}")
            if case.assertions:
                lines.append("- Assertions:")
                for assertion in case.assertions:
                    lines.append(f"  - {assertion.description or assertion.kind}: {assertion.target} {assertion.operator} {assertion.expected}")
        if plan.review_notes:
            lines.extend(["", "## 审查备注", *[f"- {item}" for item in plan.review_notes]])
        return "\n".join(lines).strip()

    async def _store_json(self, plan: SmokeExecutionPlan, filename: str, payload: Any) -> str:
        data = json.dumps(make_json_safe(payload), ensure_ascii=False, indent=2).encode("utf-8")
        return await self._store_bytes(plan, filename, data, "application/json")

    async def _store_text(self, plan: SmokeExecutionPlan, filename: str, text: str, content_type: str) -> str:
        return await self._store_bytes(plan, filename, text.encode("utf-8"), content_type)

    async def _store_bytes(self, plan: SmokeExecutionPlan, filename: str, content: bytes, content_type: str) -> str:
        if self._artifact_storage_service is None:
            raise RuntimeError("ArtifactStorageService is not configured.")
        prefix = f"smoke-plans/{_safe_segment(plan.project_scope or 'unscoped')}/{_safe_segment(plan.plan_id)}"
        result = await self._artifact_storage_service.store_uploaded_bytes(
            content=content,
            filename=filename,
            object_prefix=prefix,
            content_type=content_type,
        )
        return str(result.get("uri") or result.get("path") or "")


def _safe_segment(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))
    return normalized.strip("._") or "default"

