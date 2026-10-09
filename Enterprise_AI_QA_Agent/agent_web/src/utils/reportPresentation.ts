import type { SessionSummary, ToolArtifactRecord, VerificationResult } from "../types";

export type ReportGrade = "poor" | "attention" | "in_progress" | "good";

export function reportExecutionStatus(session: SessionSummary, meta: Record<string, unknown>): string {
  if (session.mode_key === "security_testing" && meta.ready === true) {
    const settlement = meta.settlement as { status?: string } | undefined;
    if (settlement?.status === "partial" || settlement?.status === "failed") return settlement.status;
    if (settlement?.status === "success") return "completed";
  }
  return session.status;
}

export function reportGrade(session: SessionSummary, meta: Record<string, unknown>, verifications: VerificationResult[]): ReportGrade {
  if (session.status === "failed") return "poor";
  if (session.mode_key !== "code_review") {
    if (meta.ready !== true) return "in_progress";
    if (session.mode_key === "performance_testing") {
      if (meta.verdict === "fail") return "poor";
      const persistence = meta.metrics_persistence as { status?: string } | undefined;
      return meta.verdict === "pass" && persistence?.status !== "failed" ? "good" : "attention";
    }
    const settlement = meta.settlement as { status?: string } | undefined;
    if (settlement?.status === "failed") return "poor";
    return settlement?.status === "success" && meta.failed_tasks === 0 && meta.total_findings === 0
      && verifications.some((v) => v.status === "passed") ? "good" : "attention";
  }
  if (session.status === "running" || session.status === "waiting_approval") return "in_progress";
  if (verifications.some((v) => v.status === "failed" || v.status === "partial")) return "attention";
  return "good";
}

export function primaryReportArtifact(artifacts: ToolArtifactRecord[], meta: Record<string, unknown>): ToolArtifactRecord | undefined {
  if (meta.kind && meta.ready !== true && !meta.tool_job_id) return undefined;
  const current = meta.tool_job_id ? artifacts.filter((a) => a.tool_job_id === meta.tool_job_id) : artifacts;
  return current.find((a) => ["report_markdown", "performance_report_markdown"].includes(a.artifact_type))
    ?? current.find((a) => ["report_html", "performance_report_html"].includes(a.artifact_type));
}
