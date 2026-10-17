"""Build structured campaign reports with Markdown output and evidence.

Supports:
- JSON structured report (for frontend rendering)
- Markdown formatted report (for human reading / email)
- Request/response evidence snippets per failed task
- Artifact persistence interface (caller provides storage service)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.modes.api_testing_mode.campaign_state import (
    ApiTestCampaign,
    ApiTestTask,
    CampaignReport,
)
from src.modes.api_testing_mode.contracts import TASK_COMPLETED, TASK_FAILED, TASK_SKIPPED
from src.modes.api_testing_mode.http_observation import HTTP_OBSERVATION_KEY


class ReportBuilder:
    """Aggregate campaign task results into structured and Markdown reports."""

    def build(self, *, campaign: ApiTestCampaign) -> CampaignReport:
        tasks = campaign.tasks
        completed = sum(1 for t in tasks if t.status == TASK_COMPLETED)
        failed = sum(1 for t in tasks if t.status == TASK_FAILED)
        skipped = sum(1 for t in tasks if t.status == TASK_SKIPPED)
        total_duration = sum(t.duration_ms for t in tasks)
        passed_checks = 0
        failed_checks = 0
        for task in tasks:
            for check in task.check_results:
                if check.get("passed"):
                    passed_checks += 1
                else:
                    failed_checks += 1

        findings = self._collect_findings(tasks)
        task_summaries = [self._task_summary(task) for task in tasks]

        all_passed = failed == 0 and skipped == 0
        summary = (
            f"Campaign '{campaign.objective or campaign.campaign_id}' completed: "
            f"{completed}/{len(tasks)} tasks passed, "
            f"{failed} failed, {skipped} skipped. "
            f"Checks: {passed_checks} passed, {failed_checks} failed. "
            f"Total time: {total_duration:.0f}ms."
        )
        if all_passed:
            summary = f"✅ {summary}"
        else:
            summary = f"⚠️ {summary}"

        return CampaignReport(
            campaign_id=campaign.campaign_id,
            project_name=campaign.project_name,
            summary=summary,
            total_tasks=len(tasks),
            completed=completed,
            failed=failed,
            skipped=skipped,
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            duration_ms=round(total_duration, 2),
            tasks=task_summaries,
            findings=findings,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def build_markdown(self, *, campaign: ApiTestCampaign, report: CampaignReport) -> str:
        """Generate a human-readable Markdown report."""
        lines: list[str] = []
        lines.append(f"# API 测试报告")
        lines.append("")
        lines.append(f"**项目**: {campaign.project_name or '未指定'}")
        lines.append(f"**目标**: {campaign.objective or '接口验证'}")
        lines.append(f"**生成时间**: {report.generated_at}")
        lines.append(f"**Campaign ID**: `{report.campaign_id}`")
        lines.append("")

        # Summary section.
        lines.append("## 📊 执行摘要")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 总任务数 | {report.total_tasks} |")
        lines.append(f"| 通过 | {report.completed} |")
        lines.append(f"| 失败 | {report.failed} |")
        lines.append(f"| 跳过 | {report.skipped} |")
        lines.append(f"| 断言通过 | {report.passed_checks} |")
        lines.append(f"| 断言失败 | {report.failed_checks} |")
        lines.append(f"| 总耗时 | {report.duration_ms:.0f}ms |")
        lines.append("")

        # Findings section.
        if report.findings:
            lines.append("## 🔍 发现问题")
            lines.append("")
            for finding in report.findings:
                lines.append(f"- {finding}")
            lines.append("")

        # Task details.
        lines.append("## 📋 任务详情")
        lines.append("")
        for task_data in report.tasks:
            status_icon = "✅" if task_data.get("status") == TASK_COMPLETED else (
                "❌" if task_data.get("status") == TASK_FAILED else "⏭️"
            )
            lines.append(f"### {status_icon} {task_data.get('method', '')} {task_data.get('path', '')}")
            lines.append("")
            lines.append(f"- **状态**: {task_data.get('status', '')}")
            lines.append(f"- **响应码**: {task_data.get('response_status', 'N/A')}")
            lines.append(f"- **耗时**: {task_data.get('duration_ms', 0):.0f}ms")
            lines.append(f"- **断言**: {task_data.get('checks_passed', 0)} 通过 / {task_data.get('checks_failed', 0)} 失败")

            if task_data.get("error"):
                lines.append(f"- **错误**: {task_data['error']}")

            # Evidence snippet for failed tasks.
            if task_data.get("status") == TASK_FAILED and task_data.get("evidence"):
                lines.append("")
                lines.append("**证据片段:**")
                lines.append("```json")
                lines.append(task_data["evidence"])
                lines.append("```")

            lines.append("")

        return "\n".join(lines)

    def build_artifacts(
        self,
        *,
        campaign: ApiTestCampaign,
        report: CampaignReport,
        markdown_report: str,
    ) -> list[dict[str, Any]]:
        """Build artifact payloads ready for persistence.

        Returns a list of artifact dicts that can be stored via
        ``artifact_storage_service.store_uploaded_bytes()``.
        """
        artifacts: list[dict[str, Any]] = []

        # 1. JSON report artifact.
        json_content = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
        artifacts.append({
            "type": "api_test_report_json",
            "filename": f"api_test_report_{campaign.campaign_id[:8]}.json",
            "content": json_content.encode("utf-8"),
            "content_type": "application/json",
            "label": "API 测试报告 (JSON)",
        })

        # 2. Markdown report artifact.
        artifacts.append({
            "type": "api_test_report_md",
            "filename": f"api_test_report_{campaign.campaign_id[:8]}.md",
            "content": markdown_report.encode("utf-8"),
            "content_type": "text/markdown",
            "label": "API 测试报告 (Markdown)",
        })

        # 3. Per-task evidence artifacts for failed tasks.
        for task in campaign.tasks:
            if task.status != TASK_FAILED:
                continue
            evidence = self._build_task_evidence(task)
            if evidence:
                evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2)
                artifacts.append({
                    "type": "api_test_task_evidence",
                    "filename": f"evidence_{task.task_id[:12]}.json",
                    "content": evidence_json.encode("utf-8"),
                    "content_type": "application/json",
                    "label": f"证据: {task.method} {task.path}",
                    "task_id": task.task_id,
                })

        return artifacts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _task_summary(self, task: ApiTestTask) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "task_id": task.task_id,
            "name": task.name,
            "method": task.method,
            "path": task.path,
            "full_url": task.full_url,
            "capability": task.capability,
            "status": task.status,
            "response_status": task.response_status,
            "duration_ms": task.duration_ms,
            "check_count": len(task.check_results),
            "checks_passed": sum(1 for c in task.check_results if c.get("passed")),
            "checks_failed": sum(1 for c in task.check_results if not c.get("passed")),
            "request_outcome": task.request_outcome,
            "reconciliation_for": task.reconciliation_for or None,
            "reconciliation_status": task.reconciliation_status,
            "reconciliation_observation": task.reconciliation_observation or None,
            "reconciliation_required": task.request_outcome == "unknown" and task.reconciliation_status in {"none", "inconclusive"},
            "error": task.last_error or None,
            "checks": task.check_results,
        }

        if HTTP_OBSERVATION_KEY in task.execution_checkpoint:
            summary[HTTP_OBSERVATION_KEY] = task.execution_checkpoint[HTTP_OBSERVATION_KEY]
        # Add evidence snippet for failed tasks.
        if task.status == TASK_FAILED:
            evidence = self._build_evidence_snippet(task)
            if evidence:
                summary["evidence"] = evidence

        return summary

    def _build_evidence_snippet(self, task: ApiTestTask) -> str:
        """Build a compact evidence string for inclusion in Markdown."""
        evidence: dict[str, Any] = {}
        if task.response_status:
            evidence["response_status"] = task.response_status
        if task.response_body:
            body = task.response_body
            if isinstance(body, dict):
                # Truncate large response bodies.
                body_str = json.dumps(body, ensure_ascii=False)
                if len(body_str) > 500:
                    body_str = body_str[:500] + "..."
                evidence["response_body_preview"] = body_str
            elif isinstance(body, str) and len(body) > 500:
                evidence["response_body_preview"] = body[:500] + "..."
            else:
                evidence["response_body_preview"] = body
        if task.last_error:
            evidence["error"] = task.last_error
        failed_checks = [c for c in task.check_results if not c.get("passed")]
        if failed_checks:
            evidence["failed_assertions"] = failed_checks[:3]
        if not evidence:
            return ""
        return json.dumps(evidence, ensure_ascii=False, indent=2)

    def _build_task_evidence(self, task: ApiTestTask) -> dict[str, Any] | None:
        """Build full evidence dict for artifact persistence."""
        if task.status != TASK_FAILED:
            return None
        evidence = {
            "task_id": task.task_id,
            "name": task.name,
            "method": task.method,
            "path": task.path,
            "full_url": task.full_url,
            "request": {
                "headers": task.request_headers,
                "query": task.request_query,
                "body": task.request_body,
            },
            "response": {
                "status": task.response_status,
                "headers": dict(list(task.response_headers.items())[:20]),
                "body": self._truncate_body(task.response_body),
            },
            "assertions": task.check_results,
            "error": task.last_error,
            "request_outcome": task.request_outcome,
            "reconciliation_for": task.reconciliation_for or None,
            "reconciliation_status": task.reconciliation_status,
            "reconciliation_observation": task.reconciliation_observation or None,
            "reconciliation_required": task.request_outcome == "unknown" and task.reconciliation_status in {"none", "inconclusive"},
            "duration_ms": task.duration_ms,
            "attempts": task.attempts,
        }
        if HTTP_OBSERVATION_KEY in task.execution_checkpoint:
            evidence[HTTP_OBSERVATION_KEY] = task.execution_checkpoint[HTTP_OBSERVATION_KEY]
        return evidence

    def _truncate_body(self, body: Any, max_chars: int = 2000) -> Any:
        if body is None:
            return None
        if isinstance(body, dict):
            serialized = json.dumps(body, ensure_ascii=False)
            if len(serialized) <= max_chars:
                return body
            return serialized[:max_chars] + "...(truncated)"
        if isinstance(body, str):
            return body[:max_chars] if len(body) > max_chars else body
        return str(body)[:max_chars]

    def _collect_findings(self, tasks: list[ApiTestTask]) -> list[str]:
        findings: list[str] = []
        for task in tasks:
            if task.status == TASK_FAILED:
                failed_assertions = [
                    c.get("description") or c.get("name", "")
                    for c in task.check_results
                    if not c.get("passed")
                ]
                if failed_assertions:
                    findings.append(
                        f"❌ {task.method} {task.path}: {'; '.join(failed_assertions)}"
                    )
                elif task.last_error:
                    findings.append(f"❌ {task.method} {task.path}: {task.last_error}")
            elif task.status == TASK_SKIPPED:
                findings.append(f"⏭️ {task.method} {task.path}: skipped ({task.last_error})")
        return findings


__all__ = ["ReportBuilder"]
