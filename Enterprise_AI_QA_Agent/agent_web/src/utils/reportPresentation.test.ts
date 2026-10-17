import { describe, expect, it } from "vitest";
import fixture from "../../../docs/upgrade_2026-10-03/p5-security-report-verified/report-record.json";
import stale from "../../../docs/upgrade_2026-10-03/p5-perf-report-before/report-product-observations.json";
import type { SessionSummary, ToolArtifactRecord, VerificationResult } from "../types";
import { primaryReportArtifact, reportExecutionStatus, reportGrade } from "./reportPresentation";

const security = { mode_key: "security_testing", status: "completed" } as SessionSummary;
const perf = { mode_key: "performance_testing", status: "completed" } as SessionSummary;
const api = { mode_key: "api_testing", status: "completed" } as SessionSummary;

describe("reports backed by actual persisted security evidence", () => {
  it("keeps the real partial campaign with four findings as attention", () => {
    expect(reportGrade(security, { ...fixture.output.report, ready: true }, [])).toBe("attention");
    expect(reportExecutionStatus({ ...security, status: "idle" }, { ...fixture.output.report, ready: true })).toBe("partial");
  });
  it("never grades a missing report or missing verdict as good", () => {
    expect(reportGrade(security, { ready: false }, [])).toBe("in_progress");
    expect(reportGrade(security, { ready: true, total_findings: 0, failed_tasks: 0 }, [])).toBe("attention");
    expect(reportGrade(perf, { ready: true }, [])).toBe("attention");
  });
  it("shows the known performance verdict and persistence failure", () => {
    expect(reportGrade(perf, { ready: true, verdict: "fail" }, [])).toBe("poor");
    expect(reportGrade(perf, { ready: true, verdict: "inconclusive" }, [])).toBe("attention");
    expect(reportGrade(perf, { ready: true, verdict: "pass", metrics_persistence: { status: "failed" } }, [])).toBe("attention");
  });
  it("projects API report readiness, grade and Markdown artifact", () => {
    expect(reportExecutionStatus(api, { ready: true, job_status: "completed" })).toBe("completed");
    expect(reportGrade(api, { ready: true, failed_tasks: 0, skipped_tasks: 0 }, [])).toBe("good");
    expect(reportGrade(api, { ready: true, failed_tasks: 1, skipped_tasks: 0 }, [])).toBe("poor");
    const artifact = { artifact_type: "api_test_report_md", tool_job_id: "api-job" } as ToolArtifactRecord;
    expect(primaryReportArtifact([artifact], { kind: "api_testing", ready: true, tool_job_id: "api-job" })).toBe(artifact);
  });
  it("preserves code review verification grades", () => {
    const review = { mode_key: "code_review", status: "completed" } as SessionSummary;
    expect(reportGrade(review, {}, [{ status: "failed" } as VerificationResult])).toBe("attention");
    expect(reportGrade(review, {}, [])).toBe("good");
  });
  it("cannot display an older report body for the newest persisted job", () => {
    const old = { artifact_type: "report_markdown", tool_job_id: "old" } as ToolArtifactRecord;
    const current = { artifact_type: "performance_report_markdown", tool_job_id: "current" } as ToolArtifactRecord;
    expect(primaryReportArtifact([old, current], { tool_job_id: "current" })).toBe(current);
    expect(primaryReportArtifact([old], { tool_job_id: "current" })).toBeUndefined();
    expect(primaryReportArtifact([old], { kind: "security_testing", ready: false })).toBeUndefined();
  });
  it("shows the reproduced failed attempt even when the session remains idle", () => {
    const observed = stale.find((entry) => entry.stage === "missing")!;
    const meta = { kind: "performance_testing", ready: false, job_status: "failed", tool_job_id: observed.expected_job_id };
    const session = { ...perf, status: "idle" } as SessionSummary;
    expect(reportGrade(session, meta, [])).toBe("poor");
    expect(reportExecutionStatus(session, meta)).toBe("failed");
    expect(primaryReportArtifact([{ tool_job_id: observed.meta.tool_job_id,
      artifact_type: "performance_report_markdown" } as ToolArtifactRecord], meta)).toBeUndefined();
  });
  it("separates waiting, interrupted delivery and a completed report", () => {
    expect(reportGrade(perf, { ready: false, job_status: "running" }, [])).toBe("in_progress");
    expect(reportGrade(perf, { ready: false, job_status: "completed" }, [])).toBe("attention");
    expect(reportGrade(perf, { ready: true, job_status: "partial", verdict: "pass" }, [])).toBe("attention");
    expect(reportExecutionStatus(perf, { ready: true, job_status: "completed" })).toBe("completed");
    expect(reportExecutionStatus(perf, { ready: false, job_status: "completed" })).toBe("partial");
  });
});
