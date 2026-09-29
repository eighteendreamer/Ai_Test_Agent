from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.modes.code_review_mode.governance.models import (
    ApprovalDecision,
    ChangedFile,
    GovernanceFinding,
    RiskScore,
)
from src.modes.code_review_mode.project_source import (
    ignored_names_from_arguments,
    is_ignored_project_path,
    normalize_project_source,
    resolve_local_project_root,
)

SEVERITY_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}

SEVERITY_PENALTY = {
    "critical": 35,
    "high": 18,
    "medium": 8,
    "low": 3,
    "info": 1,
}

DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}

TEST_PATH_TOKENS = ("test", "tests", "__tests__", ".spec.", ".test.")
GRAPH_FILE_LANGUAGES = {"python", "typescript", "javascript", "vue", "java", "sql"}
logger = logging.getLogger("uvicorn.error.code_review_mode.governance")


class CodeGovernanceRuntime:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = (workspace_root or Path.cwd()).resolve()

    async def handle(self, arguments: dict[str, Any], context) -> dict[str, Any]:
        project_source = normalize_project_source(arguments)
        if project_source.source_type != "local":
            return self._skipped_result(
                "Code governance runner currently supports local project sources only.",
                reason="unsupported_project_source",
            )

        try:
            root = resolve_local_project_root(project_source)
        except Exception as exc:
            return self._failed_result(f"Failed to resolve project source: {exc}", error=str(exc))

        ignored_names = ignored_names_from_arguments(arguments)
        diff_payload = await self._collect_diff(root, project_source, arguments)
        if diff_payload.get("collection_error"):
            error = str(diff_payload["collection_error"])
            logger.warning("code_governance_diff_collection_failed", extra={"project_root": str(root), "error": error})
            return self._failed_result(f"Failed to collect review diff: {error}", error=error)
        changed_files = [
            item
            for item in self._build_changed_files(diff_payload)
            if not is_ignored_project_path(item.path, ignored_names)
        ]
        code_graph = self._build_code_graph(root, changed_files, arguments)
        scanner_runs = await self._run_external_scanners(root, arguments, changed_files)
        findings = [
            *self._scan_diff_rules(diff_payload, ignored_names),
            *self._scan_changed_file_rules(changed_files),
            *self._scan_test_impact(changed_files),
            *self._scan_code_graph_rules(code_graph),
            *self._load_external_scanner_findings(root, arguments),
            *[
                finding
                for run in scanner_runs
                for finding in run.get("findings", [])
                if isinstance(finding, GovernanceFinding)
            ],
        ]
        findings = self._dedupe_findings(findings)
        risk_score = self._score_findings(findings)
        decision = self._decide(findings, risk_score, arguments)
        failed_scanners = [str(run.get("scanner") or "unknown") for run in scanner_runs if run.get("status") == "failed"]
        skipped_scanners = [str(run.get("scanner") or "unknown") for run in scanner_runs if run.get("status") == "skipped"]
        if failed_scanners:
            logger.warning(
                "code_governance_scanners_failed",
                extra={"project_root": str(root), "scanners": failed_scanners},
            )
            decision = ApprovalDecision(
                status="blocked",
                reason=f"Requested governance scanners failed: {', '.join(failed_scanners)}.",
                blocking_findings=list(decision.blocking_findings),
                required_actions=[*decision.required_actions, "Fix failed scanners and re-run code governance review."],
                policy=dict(decision.policy),
            )
        elif skipped_scanners and decision.status != "blocked":
            logger.warning(
                "code_governance_scanners_skipped",
                extra={"project_root": str(root), "scanners": skipped_scanners},
            )
            decision = ApprovalDecision(
                status="warning",
                reason=f"Requested governance scanners were skipped: {', '.join(skipped_scanners)}; review is incomplete.",
                required_actions=[*decision.required_actions, "Run the skipped scanners before approving this change."],
                policy=dict(decision.policy),
            )
        report_markdown = self._build_report_markdown(
            project_name=project_source.project_name or root.name,
            changed_files=changed_files,
            findings=findings,
            risk_score=risk_score,
            decision=decision,
            code_graph=code_graph,
        )
        report_json = {
            "project": {
                "name": project_source.project_name or root.name,
                "root_path": str(root),
                "branch": project_source.branch,
                "commit_range": diff_payload.get("commit_range", ""),
            },
            "decision": decision.model_dump(mode="python"),
            "risk_score": risk_score.model_dump(mode="python"),
            "changed_files": [item.model_dump(mode="python") for item in changed_files],
            "code_graph": code_graph,
            "findings": [item.model_dump(mode="python") for item in findings],
            "scanner_runs": [
                {key: value for key, value in run.items() if key != "findings"}
                for run in scanner_runs
            ],
            "metrics": self._build_metrics(changed_files, findings, scanner_runs, code_graph),
            "diff": {
                "mode": diff_payload.get("diff_mode", ""),
                "stat": diff_payload.get("diff_stat", ""),
                "status_lines": diff_payload.get("status_lines", []),
                "truncated": diff_payload.get("truncated", False),
            },
            "ignored_paths": sorted(ignored_names),
        }

        return {
            "status": "completed",
            "ok": decision.status != "blocked",
            "summary": self._summary(decision, risk_score, findings),
            "approval_decision": decision.status,
            "decision": decision.model_dump(mode="python"),
            "risk_score": risk_score.model_dump(mode="python"),
            "findings": [item.model_dump(mode="python") for item in findings],
            "changed_files": [item.model_dump(mode="python") for item in changed_files],
            "code_graph": code_graph,
            "metrics": report_json["metrics"],
            "scanner_runs": report_json["scanner_runs"],
            "report_markdown": report_markdown,
            "report_json": report_json,
            "artifacts": [
                {
                    "type": "governance_report_json",
                    "label": "governance-report.json",
                    "content": json.dumps(report_json, ensure_ascii=False, indent=2),
                },
                {
                    "type": "governance_report_markdown",
                    "label": "governance-report.md",
                    "content": report_markdown,
                },
            ],
        }

    async def _collect_diff(self, root: Path, project_source, arguments: dict[str, Any]) -> dict[str, Any]:
        git_root_result = await self._run("git", ["-C", str(root), "rev-parse", "--show-toplevel"])
        if not git_root_result["ok"]:
            inline_diff = str(arguments.get("diff_text") or "")
            return {
                "diff_mode": "not_git_repo",
                "commit_range": "",
                "status_lines": [],
                "diff_stat": "",
                "diff_text": inline_diff,
                "numstat_lines": [],
                "truncated": False,
                "collection_error": "git repository unavailable and no inline diff was supplied" if not inline_diff.strip() else "",
            }
        git_root = git_root_result["stdout"].strip()
        commit_range = str(arguments.get("commit_range") or project_source.commit_range or "").strip()
        diff_mode = str(arguments.get("diff_mode") or ("range" if commit_range else "working_tree")).strip()
        paths = [str(item).strip() for item in arguments.get("paths") or arguments.get("targets") or [] if str(item).strip()]
        status_result = await self._run("git", ["-C", git_root, "-c", "core.quotePath=false", "status", "--short"])
        diff_args = self._build_git_diff_args(git_root, commit_range, diff_mode, paths, stat_only=False)
        stat_args = self._build_git_diff_args(git_root, commit_range, diff_mode, paths, stat_only=True)
        numstat_args = self._build_git_diff_args(git_root, commit_range, diff_mode, paths, numstat=True)
        name_status_args = self._build_git_diff_args(git_root, commit_range, diff_mode, paths, name_status=True)
        diff_result = await self._run("git", diff_args)
        stat_result = await self._run("git", stat_args)
        numstat_result = await self._run("git", numstat_args)
        name_status_result = await self._run("git", name_status_args)
        command_results = {
            "git status": status_result,
            "git diff": diff_result,
            "git diff --stat": stat_result,
            "git diff --numstat": numstat_result,
            "git diff --name-status": name_status_result,
        }
        failed_commands = [
            f"{name} (exit={result.get('exit_code')}, stderr={str(result.get('stderr') or '')[:300]})"
            for name, result in command_results.items()
            if not result.get("ok")
        ]
        if failed_commands:
            return {"collection_error": "; ".join(failed_commands)}
        inline_diff = str(arguments.get("diff_text") or "").strip()
        diff_text = inline_diff or diff_result["stdout"]
        max_chars = int(arguments.get("max_chars") or 80000)
        truncated = len(diff_text) > max_chars
        if truncated:
            diff_text = diff_text[:max_chars]
        return {
            "git_root": git_root,
            "diff_mode": diff_mode,
            "commit_range": commit_range,
            "status_lines": [line for line in status_result["stdout"].splitlines() if line.strip()],
            "diff_stat": stat_result["stdout"].strip(),
            "diff_text": diff_text,
            "numstat_lines": [line for line in numstat_result["stdout"].splitlines() if line.strip()],
            "name_status_lines": [line for line in name_status_result["stdout"].splitlines() if line.strip()],
            "truncated": truncated,
        }

    def _build_git_diff_args(
        self,
        git_root: str,
        commit_range: str,
        diff_mode: str,
        paths: list[str],
        *,
        stat_only: bool = False,
        numstat: bool = False,
        name_status: bool = False,
    ) -> list[str]:
        args = ["-C", git_root, "-c", "core.quotePath=false", "diff"]
        if stat_only:
            args.append("--stat")
        elif numstat:
            args.append("--numstat")
        elif name_status:
            args.append("--name-status")
        else:
            args.append("--unified=3")
        if diff_mode == "staged":
            args.append("--cached")
        if commit_range:
            args.append(commit_range)
        if paths:
            args.append("--")
            args.extend(paths)
        return args

    def _build_changed_files(self, diff_payload: dict[str, Any]) -> list[ChangedFile]:
        by_path: dict[str, ChangedFile] = {}
        for line in diff_payload.get("name_status_lines", []):
            parts = str(line).split("\t")
            if len(parts) < 2:
                continue
            status = parts[0].strip()
            path = parts[-1].strip()
            by_path[path] = ChangedFile(
                path=path,
                change_type=self._change_type_from_status(status),
                language=self._language_for_path(path),
                risk_hints=self._risk_hints_for_path(path),
            )
        for line in diff_payload.get("numstat_lines", []):
            parts = str(line).split("\t")
            if len(parts) < 3:
                continue
            additions = self._parse_numstat(parts[0])
            deletions = self._parse_numstat(parts[1])
            path = parts[2].strip()
            existing = by_path.get(path)
            if existing is not None:
                existing.additions = additions
                existing.deletions = deletions
                continue
            by_path[path] = ChangedFile(
                path=path,
                change_type="modified",
                language=self._language_for_path(path),
                additions=additions,
                deletions=deletions,
                risk_hints=self._risk_hints_for_path(path),
            )
        for line in diff_payload.get("status_lines", []):
            status = str(line[:2]).strip()
            path = str(line[3:] if len(line) > 3 else line).strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1].strip()
            if not path:
                continue
            existing = by_path.get(path)
            change_type = self._change_type_from_status(status)
            if existing is not None:
                existing.change_type = change_type if change_type != "unknown" else existing.change_type
                continue
            by_path[path] = ChangedFile(
                path=path,
                change_type=change_type,
                language=self._language_for_path(path),
                risk_hints=self._risk_hints_for_path(path),
            )
        return sorted(by_path.values(), key=lambda item: item.path)

    def _scan_diff_rules(
        self,
        diff_payload: dict[str, Any],
        ignored_names: set[str] | None = None,
    ) -> list[GovernanceFinding]:
        findings: list[GovernanceFinding] = []
        current_file = ""
        new_line = 0
        for raw_line in str(diff_payload.get("diff_text") or "").splitlines():
            if raw_line.startswith("+++ b/"):
                current_file = raw_line.removeprefix("+++ b/").strip()
                continue
            if raw_line.startswith("@@"):
                match = re.search(r"\+(\d+)", raw_line)
                new_line = int(match.group(1)) if match else 0
                continue
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                if is_ignored_project_path(current_file, ignored_names):
                    continue
                line = raw_line[1:]
                findings.extend(self._scan_added_line(current_file, new_line or None, line))
                new_line += 1
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                continue
            else:
                new_line += 1 if new_line else 0
        return findings

    def _scan_added_line(self, path: str, line_number: int | None, line: str) -> list[GovernanceFinding]:
        findings: list[GovernanceFinding] = []
        stripped = line.strip()
        lowered = stripped.lower()
        if re.search(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}", stripped):
            findings.append(
                self._finding(
                    source="builtin-secret-rule",
                    category="secret",
                    severity="critical",
                    title="Possible secret committed in code",
                    summary="The diff adds a token, password, API key, or secret-like assignment.",
                    file_path=path,
                    line=line_number,
                    recommendation="Move the value to a secret manager or environment variable and rotate the exposed credential.",
                    deterministic=True,
                    confidence=0.88,
                    evidence=[{"line": stripped[:240]}],
                )
            )
        if "shell=true" in lowered or re.search(r"\bos\.system\s*\(", stripped) or re.search(r"\bsubprocess\.", stripped) and "shell=True" in stripped:
            findings.append(
                self._finding(
                    source="builtin-command-rule",
                    category="security",
                    severity="high",
                    title="Risky shell command execution",
                    summary="The diff adds command execution that may cross a privilege or input boundary.",
                    file_path=path,
                    line=line_number,
                    recommendation="Use argument arrays, validate inputs, and route dangerous operations through approval policies.",
                    deterministic=True,
                    confidence=0.78,
                    evidence=[{"line": stripped[:240]}],
                )
            )
        if re.search(r"\b(eval|exec)\s*\(", stripped):
            findings.append(
                self._finding(
                    source="builtin-dynamic-code-rule",
                    category="security",
                    severity="high",
                    title="Dynamic code execution added",
                    summary="The diff adds eval/exec style dynamic execution.",
                    file_path=path,
                    line=line_number,
                    recommendation="Replace dynamic execution with explicit dispatch or a constrained parser.",
                    deterministic=True,
                    confidence=0.82,
                    evidence=[{"line": stripped[:240]}],
                )
            )
        sql_statement = re.search(
            r"(?i)\b(?:delete\s+from\s+[a-z_][\w.]*|update\s+[a-z_][\w.]*\s+set\b)",
            stripped,
        )
        if (
            path.lower().endswith((".sql", ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs"))
            and not stripped.startswith(("#", "--", "//", "*"))
            and sql_statement
            and " where " not in f" {lowered} "
        ):
            findings.append(
                self._finding(
                    source="builtin-sql-rule",
                    category="database",
                    severity="critical",
                    title="Potential destructive SQL without WHERE",
                    summary="The diff adds UPDATE or DELETE SQL without an obvious WHERE clause on the same statement.",
                    file_path=path,
                    line=line_number,
                    recommendation="Add a WHERE clause, transaction guard, dry-run check, and migration rollback plan.",
                    deterministic=True,
                    confidence=0.75,
                    evidence=[{"line": stripped[:240]}],
                )
            )
        if "innerhtml" in lowered or "document.write" in lowered:
            findings.append(
                self._finding(
                    source="builtin-xss-rule",
                    category="security",
                    severity="medium",
                    title="Potential unsafe DOM write",
                    summary="The diff adds direct DOM HTML writes that may create XSS risk if fed by user-controlled content.",
                    file_path=path,
                    line=line_number,
                    recommendation="Prefer escaped rendering and sanitize any HTML that must be injected.",
                    deterministic=True,
                    confidence=0.7,
                    evidence=[{"line": stripped[:240]}],
                )
            )
        return findings

    def _scan_changed_file_rules(self, changed_files: list[ChangedFile]) -> list[GovernanceFinding]:
        findings: list[GovernanceFinding] = []
        for item in changed_files:
            filename = Path(item.path).name
            normalized = item.path.replace("\\", "/").lower()
            if filename in DEPENDENCY_FILES:
                findings.append(
                    self._finding(
                        source="builtin-dependency-rule",
                        category="dependency",
                        severity="medium",
                        title="Dependency manifest changed",
                        summary=f"Dependency or lock file changed: {item.path}.",
                        file_path=item.path,
                        recommendation="Run dependency vulnerability and license checks before approval.",
                        deterministic=True,
                        confidence=0.72,
                    )
                )
            if ".github/workflows/" in normalized or normalized.endswith((".yml", ".yaml")) and "ci" in normalized:
                findings.append(
                    self._finding(
                        source="builtin-ci-rule",
                        category="architecture",
                        severity="medium",
                        title="CI/CD configuration changed",
                        summary=f"Pipeline or workflow configuration changed: {item.path}.",
                        file_path=item.path,
                        recommendation="Review permissions, secret usage, branch filters, and deployment gates.",
                        deterministic=True,
                        confidence=0.68,
                    )
                )
        return findings

    def _scan_test_impact(self, changed_files: list[ChangedFile]) -> list[GovernanceFinding]:
        changed_source = [
            item for item in changed_files
            if item.language in {"python", "typescript", "javascript", "vue", "java", "sql"}
            and not self._is_test_path(item.path)
            and item.change_type != "deleted"
        ]
        changed_tests = [item for item in changed_files if self._is_test_path(item.path)]
        if not changed_source or changed_tests:
            return []
        risky = [
            item for item in changed_source
            if item.risk_hints or item.additions + item.deletions >= 40
        ]
        if not risky:
            return []
        return [
            self._finding(
                source="builtin-test-impact-rule",
                category="test",
                severity="medium",
                title="Risky code changed without nearby test changes",
                summary="The diff changes risky or non-trivial source files but does not include obvious test updates.",
                recommendation="Add focused tests for changed behavior, edge cases, and regression paths.",
                deterministic=True,
                confidence=0.62,
                evidence=[{"changed_source": [item.path for item in risky[:12]]}],
            )
        ]

    def _build_code_graph(self, root: Path, changed_files: list[ChangedFile], arguments: dict[str, Any]) -> dict[str, Any]:
        if bool(arguments.get("skip_code_graph")):
            return self._empty_code_graph(skipped=True, reason="disabled_by_argument")

        graph_files = self._select_graph_files(root, changed_files, arguments)
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        for path in graph_files:
            rel_path = self._relative_path(root, path)
            file_id = self._graph_node_id("file", rel_path)
            language = self._language_for_path(rel_path)
            role = self._layer_role_for_path(rel_path)
            nodes[file_id] = {
                "id": file_id,
                "kind": "file",
                "label": rel_path,
                "path": rel_path,
                "language": language,
                "role": role,
            }
            if language == "python":
                self._extend_python_graph(path, rel_path, file_id, nodes, edges)
            elif language in {"javascript", "typescript", "vue"}:
                self._extend_frontend_graph(path, rel_path, file_id, nodes, edges)
            elif language in {"java", "sql"}:
                self._extend_text_code_graph(path, rel_path, file_id, nodes, edges)

        impacted_paths = {item.path.replace("\\", "/") for item in changed_files}
        impacted_node_ids = {
            node_id
            for node_id, node in nodes.items()
            if str(node.get("path") or "").replace("\\", "/") in impacted_paths
        }
        for edge in edges:
            if edge.get("source") in impacted_node_ids or edge.get("target") in impacted_node_ids:
                impacted_node_ids.add(str(edge.get("source")))
                impacted_node_ids.add(str(edge.get("target")))

        return {
            "status": "completed",
            "summary": (
                f"Built lightweight code graph with {len(nodes)} nodes, {len(edges)} edges, "
                f"and {len(impacted_node_ids)} impacted nodes."
            ),
            "nodes": list(nodes.values())[:500],
            "edges": edges[:1200],
            "impacted_node_ids": sorted(impacted_node_ids)[:500],
            "metrics": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "impacted_node_count": len(impacted_node_ids),
                "scanned_file_count": len(graph_files),
            },
        }

    def _empty_code_graph(self, *, skipped: bool = False, reason: str = "") -> dict[str, Any]:
        return {
            "status": "skipped" if skipped else "completed",
            "summary": reason or "No code graph nodes were built.",
            "nodes": [],
            "edges": [],
            "impacted_node_ids": [],
            "metrics": {
                "node_count": 0,
                "edge_count": 0,
                "impacted_node_count": 0,
                "scanned_file_count": 0,
            },
        }

    def _select_graph_files(self, root: Path, changed_files: list[ChangedFile], arguments: dict[str, Any]) -> list[Path]:
        max_files = max(20, min(int(arguments.get("max_graph_files") or 400), 2000))
        ignored_names = ignored_names_from_arguments(arguments)
        selected: dict[str, Path] = {}
        changed_dirs: set[Path] = set()
        for item in changed_files:
            if item.language not in GRAPH_FILE_LANGUAGES or item.change_type == "deleted":
                continue
            if is_ignored_project_path(item.path, ignored_names):
                continue
            candidate = (root / item.path).resolve()
            if candidate.exists() and candidate.is_file():
                selected[str(candidate)] = candidate
                changed_dirs.add(candidate.parent)

        source_roots = [
            root / "src",
            root / "app",
            root / "backend" / "app",
            root / "frontend" / "src",
        ]
        scan_roots = [path for path in [*changed_dirs, *source_roots, root] if path.exists()]
        for scan_root in scan_roots:
            for dirpath, dirnames, filenames in os.walk(scan_root):
                current_path = Path(dirpath)
                rel_dir = self._relative_path(root, current_path)
                if rel_dir != "." and is_ignored_project_path(rel_dir, ignored_names):
                    dirnames[:] = []
                    continue
                dirnames[:] = [
                    dirname
                    for dirname in dirnames
                    if dirname.lower() not in ignored_names
                    and not is_ignored_project_path(current_path.joinpath(dirname), ignored_names)
                ]
                for filename in sorted(filenames):
                    if len(selected) >= max_files:
                        break
                    if filename.startswith("."):
                        continue
                    path = current_path / filename
                    rel_path = self._relative_path(root, path)
                    if is_ignored_project_path(rel_path, ignored_names):
                        continue
                    try:
                        if not path.is_file():
                            continue
                    except OSError:
                        continue
                    if self._language_for_path(rel_path) not in GRAPH_FILE_LANGUAGES:
                        continue
                    selected[str(path.resolve())] = path.resolve()
                if len(selected) >= max_files:
                    break
            if len(selected) >= max_files:
                break
        return list(selected.values())[:max_files]

    def _extend_python_graph(
        self,
        path: Path,
        rel_path: str,
        file_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return

        imported_symbols: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    imported_symbols[local_name] = f"{module}.{alias.name}".strip(".")
                    import_id = self._graph_node_id("symbol", imported_symbols[local_name])
                    nodes.setdefault(import_id, {"id": import_id, "kind": "symbol", "label": imported_symbols[local_name]})
                    edges.append({"source": file_id, "target": import_id, "type": "imports"})
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local_name = alias.asname or alias.name.split(".")[0]
                    imported_symbols[local_name] = alias.name
                    import_id = self._graph_node_id("symbol", alias.name)
                    nodes.setdefault(import_id, {"id": import_id, "kind": "symbol", "label": alias.name})
                    edges.append({"source": file_id, "target": import_id, "type": "imports"})

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                class_id = self._graph_node_id("class", f"{rel_path}:{node.name}")
                nodes[class_id] = {
                    "id": class_id,
                    "kind": "class",
                    "label": node.name,
                    "path": rel_path,
                    "line": node.lineno,
                    "role": self._layer_role_for_name(node.name, rel_path),
                }
                edges.append({"source": file_id, "target": class_id, "type": "defines"})
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._add_python_function_node(
                            child,
                            rel_path,
                            file_id,
                            nodes,
                            edges,
                            imported_symbols,
                            class_name=node.name,
                            class_id=class_id,
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_python_function_node(node, rel_path, file_id, nodes, edges, imported_symbols)

    def _add_python_function_node(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        rel_path: str,
        file_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
        imported_symbols: dict[str, str],
        *,
        class_name: str = "",
        class_id: str = "",
    ) -> None:
        qualified_name = f"{class_name}.{node.name}" if class_name else node.name
        function_id = self._graph_node_id("function", f"{rel_path}:{qualified_name}")
        route_paths = self._python_route_paths(node)
        nodes[function_id] = {
            "id": function_id,
            "kind": "function",
            "label": qualified_name,
            "path": rel_path,
            "line": node.lineno,
            "role": "controller" if route_paths else self._layer_role_for_path(rel_path),
            "metadata": {"routes": route_paths},
        }
        edges.append({"source": class_id or file_id, "target": function_id, "type": "defines"})

        for call in [item for item in ast.walk(node) if isinstance(item, ast.Call)]:
            call_name = self._python_call_name(call.func)
            if not call_name:
                continue
            target_label = imported_symbols.get(call_name.split(".")[0], call_name)
            call_id = self._graph_node_id("call", target_label)
            nodes.setdefault(call_id, {"id": call_id, "kind": "call", "label": target_label, "role": self._role_for_call_name(target_label)})
            edges.append({"source": function_id, "target": call_id, "type": "calls", "line": getattr(call, "lineno", None)})

            db_target = self._db_target_from_call(target_label)
            if db_target:
                db_id = self._graph_node_id("database", db_target)
                nodes.setdefault(db_id, {"id": db_id, "kind": "database", "label": db_target, "role": "database"})
                edges.append({"source": function_id, "target": db_id, "type": "touches_database", "line": getattr(call, "lineno", None)})

        for loop in [item for item in ast.walk(node) if isinstance(item, (ast.For, ast.AsyncFor, ast.While))]:
            for call in [item for item in ast.walk(loop) if isinstance(item, ast.Call)]:
                call_name = self._python_call_name(call.func)
                if call_name and self._looks_like_query_call(call_name):
                    edges.append(
                        {
                            "source": function_id,
                            "target": self._graph_node_id("call", call_name),
                            "type": "loop_calls_query",
                            "line": getattr(call, "lineno", None),
                        }
                    )

    def _extend_frontend_graph(
        self,
        path: Path,
        rel_path: str,
        file_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"\bfetch\s*\(\s*([^)]+)", text):
            endpoint = self._extract_endpoint_literal(match.group(1))
            api_id = self._graph_node_id("api", endpoint or "dynamic-fetch")
            nodes.setdefault(api_id, {"id": api_id, "kind": "api", "label": endpoint or "dynamic fetch", "role": "api"})
            edges.append({"source": file_id, "target": api_id, "type": "calls_api", "line": self._line_number(text, match.start())})
        for match in re.finditer(r"\bnew\s+URL\s*\(([^)]+)", text):
            url_id = self._graph_node_id("api", "new URL")
            nodes.setdefault(url_id, {"id": url_id, "kind": "api", "label": "new URL", "role": "api"})
            edges.append({"source": file_id, "target": url_id, "type": "constructs_url", "line": self._line_number(text, match.start())})

    def _extend_text_code_graph(
        self,
        path: Path,
        rel_path: str,
        file_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"(?i)\b(select|insert|update|delete)\b", text):
            db_id = self._graph_node_id("database", "sql-statement")
            nodes.setdefault(db_id, {"id": db_id, "kind": "database", "label": "sql-statement", "role": "database"})
            edges.append({"source": file_id, "target": db_id, "type": "touches_database", "line": self._line_number(text, match.start())})

    def _scan_code_graph_rules(self, code_graph: dict[str, Any]) -> list[GovernanceFinding]:
        if not isinstance(code_graph, dict) or code_graph.get("status") == "skipped":
            return []
        nodes = {
            str(node.get("id")): node
            for node in code_graph.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        findings: list[GovernanceFinding] = []
        for edge in code_graph.get("edges", []):
            if not isinstance(edge, dict):
                continue
            source = nodes.get(str(edge.get("source"))) or {}
            target = nodes.get(str(edge.get("target"))) or {}
            edge_type = str(edge.get("type") or "")
            source_role = str(source.get("role") or "")
            target_role = str(target.get("role") or "")
            source_path = str(source.get("path") or "")
            line = int(edge.get("line") or 0) or None

            if source_role == "controller" and target_role in {"repository", "database"}:
                findings.append(
                    self._finding(
                        source="code-graph-architecture-rule",
                        category="architecture",
                        severity="high",
                        title="Controller/API layer directly touches repository or database",
                        summary="The code graph shows an API/controller function calling repository or database behavior directly.",
                        file_path=source_path,
                        line=line,
                        recommendation="Route controller work through a service/application layer and keep persistence behind repositories.",
                        deterministic=True,
                        confidence=0.76,
                        evidence=[{"edge": edge, "source": source, "target": target}],
                    )
                )
            if edge_type == "loop_calls_query":
                findings.append(
                    self._finding(
                        source="code-graph-performance-rule",
                        category="performance",
                        severity="medium",
                        title="Possible N+1 query or repeated data access in loop",
                        summary="The code graph found query-like or repository calls inside a loop.",
                        file_path=source_path,
                        line=line,
                        recommendation="Batch the query, prefetch required data, or move repeated lookups outside the loop.",
                        deterministic=True,
                        confidence=0.68,
                        evidence=[{"edge": edge, "source": source, "target": target}],
                    )
                )
        return findings

    def _load_external_scanner_findings(self, root: Path, arguments: dict[str, Any]) -> list[GovernanceFinding]:
        scanner_artifacts = arguments.get("scanner_artifacts")
        if not isinstance(scanner_artifacts, dict):
            return []
        findings: list[GovernanceFinding] = []
        for scanner, path_value in scanner_artifacts.items():
            path = self._resolve_artifact_path(root, str(path_value or ""))
            if path is None or not path.exists() or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            findings.extend(self._findings_from_scanner_payload(str(scanner), payload))
        return findings

    async def _run_external_scanners(
        self,
        root: Path,
        arguments: dict[str, Any],
        changed_files: list[ChangedFile],
    ) -> list[dict[str, Any]]:
        if not bool(arguments.get("run_external_scanners") or arguments.get("execute_external_scanners")):
            return []
        scanner_names = self._requested_scanners(arguments, changed_files)
        ignored_names = ignored_names_from_arguments(arguments)
        timeout_seconds = float(arguments.get("scanner_timeout_seconds") or 120)
        timeout_seconds = max(10.0, min(timeout_seconds, 900.0))
        runs: list[dict[str, Any]] = []
        for scanner in scanner_names:
            runs.append(await self._run_single_scanner(root, scanner, timeout_seconds, ignored_names))
        return runs

    def _requested_scanners(self, arguments: dict[str, Any], changed_files: list[ChangedFile]) -> list[str]:
        raw = arguments.get("external_scanners") or arguments.get("scanners")
        if isinstance(raw, str):
            requested = [item.strip().lower().replace("-", "_") for item in raw.split(",")]
        elif isinstance(raw, list):
            requested = [str(item).strip().lower().replace("-", "_") for item in raw]
        else:
            requested = ["semgrep", "gitleaks", "bandit", "pip_audit", "npm_audit"]
        requested = [item for item in requested if item]
        if "all" in requested:
            requested = ["semgrep", "gitleaks", "bandit", "pip_audit", "npm_audit"]

        languages = {item.language for item in changed_files}
        filenames = {Path(item.path).name for item in changed_files}
        filtered: list[str] = []
        for scanner in requested:
            if scanner == "bandit" and "python" not in languages:
                continue
            if scanner == "pip_audit" and not ({"requirements.txt", "pyproject.toml", "poetry.lock"} & filenames):
                continue
            if scanner == "npm_audit" and not ({"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"} & filenames):
                continue
            if scanner not in filtered:
                filtered.append(scanner)
        return filtered

    async def _run_single_scanner(
        self,
        root: Path,
        scanner: str,
        timeout_seconds: float,
        ignored_names: set[str],
    ) -> dict[str, Any]:
        command = self._scanner_command(scanner, ignored_names)
        scanner_label = scanner.replace("_", "-")
        if command is None:
            return {
                "scanner": scanner_label,
                "status": "skipped",
                "ok": True,
                "reason": "unsupported_scanner",
                "finding_count": 0,
            }
        executable, args = command
        resolved_executable = shutil.which(executable)
        if not resolved_executable:
            return {
                "scanner": scanner_label,
                "status": "skipped",
                "ok": True,
                "reason": "scanner_not_installed",
                "command": " ".join([executable, *args]),
                "finding_count": 0,
            }

        result = await self._run(resolved_executable, args, timeout_seconds=timeout_seconds, cwd=root)
        payload = self._loads_json(result["stdout"]) or self._loads_json(result["stderr"])
        findings = self._findings_from_scanner_payload(scanner, payload) if payload is not None else []
        status = "completed" if result["ok"] or findings else "failed"
        return {
            "scanner": scanner_label,
            "status": status,
            "ok": status != "failed",
            "exit_code": result["exit_code"],
            "command": " ".join([executable, *args]),
            "finding_count": len(findings),
            "stdout_chars": len(result["stdout"]),
            "stderr_excerpt": result["stderr"][-1000:],
            "findings": findings,
        }

    def _scanner_command(self, scanner: str, ignored_names: set[str] | None = None) -> tuple[str, list[str]] | None:
        scanner_key = scanner.lower().replace("-", "_")
        ignored = sorted(ignored_names or [])
        if scanner_key == "semgrep":
            args = ["scan", "--json", "--config", "auto"]
            for name in ignored:
                args.extend(["--exclude", name])
            args.append(".")
            return ("semgrep", args)
        if scanner_key == "bandit":
            args = ["-r", ".", "-f", "json"]
            if ignored:
                args.extend(["-x", ",".join(ignored)])
            return ("bandit", args)
        if scanner_key in {"pip_audit", "pipaudit"}:
            return ("pip-audit", ["-f", "json"])
        if scanner_key in {"npm_audit", "npmaudit"}:
            return ("npm", ["audit", "--json", "--audit-level=low"])
        if scanner_key == "gitleaks":
            return ("gitleaks", ["detect", "--source", ".", "--report-format", "json", "--no-banner"])
        return None

    def _loads_json(self, value: str) -> Any | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def _findings_from_scanner_payload(self, scanner: str, payload: Any) -> list[GovernanceFinding]:
        scanner_key = scanner.lower().replace("-", "_")
        if scanner_key == "semgrep":
            return self._parse_semgrep(payload)
        if scanner_key == "bandit":
            return self._parse_bandit(payload)
        if scanner_key in {"pip_audit", "pipaudit"}:
            return self._parse_pip_audit(payload)
        if scanner_key in {"npm_audit", "npmaudit"}:
            return self._parse_npm_audit(payload)
        if scanner_key == "gitleaks":
            return self._parse_gitleaks(payload)
        return []

    def _parse_semgrep(self, payload: Any) -> list[GovernanceFinding]:
        items = payload.get("results", []) if isinstance(payload, dict) else []
        findings: list[GovernanceFinding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
            metadata = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
            severity = self._normalize_severity(extra.get("severity") or metadata.get("impact"), default="medium")
            findings.append(
                self._finding(
                    source="semgrep",
                    category=self._category_from_text(f"{item.get('check_id', '')} {extra.get('message', '')}"),
                    severity=severity,
                    title=str(item.get("check_id") or "Semgrep finding"),
                    summary=str(extra.get("message") or "Semgrep detected a code risk."),
                    file_path=str(item.get("path") or ""),
                    line=int((item.get("start") or {}).get("line") or 0) or None if isinstance(item.get("start"), dict) else None,
                    recommendation="Review the Semgrep finding and fix or suppress with justification.",
                    deterministic=True,
                    confidence=0.8,
                    metadata={"raw_source": "semgrep"},
                )
            )
        return findings

    def _parse_bandit(self, payload: Any) -> list[GovernanceFinding]:
        items = payload.get("results", []) if isinstance(payload, dict) else []
        findings: list[GovernanceFinding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            severity = self._normalize_severity(item.get("issue_severity"), default="medium")
            findings.append(
                self._finding(
                    source="bandit",
                    category="security",
                    severity=severity,
                    title=str(item.get("test_name") or item.get("test_id") or "Bandit finding"),
                    summary=str(item.get("issue_text") or "Bandit detected a Python security risk."),
                    file_path=str(item.get("filename") or ""),
                    line=int(item.get("line_number") or 0) or None,
                    recommendation="Review the Bandit finding and add a safer implementation or justified suppression.",
                    deterministic=True,
                    confidence=float(item.get("issue_confidence") == "HIGH") * 0.1 + 0.75,
                    metadata={"test_id": item.get("test_id")},
                )
            )
        return findings

    def _parse_pip_audit(self, payload: Any) -> list[GovernanceFinding]:
        dependencies = payload.get("dependencies", []) if isinstance(payload, dict) else []
        findings: list[GovernanceFinding] = []
        for dep in dependencies:
            if not isinstance(dep, dict):
                continue
            for vuln in dep.get("vulns") or []:
                if not isinstance(vuln, dict):
                    continue
                findings.append(
                    self._finding(
                        source="pip-audit",
                        category="dependency",
                        severity="high",
                        title=f"Vulnerable Python dependency: {dep.get('name', 'unknown')}",
                        summary=str(vuln.get("description") or vuln.get("id") or "pip-audit reported a vulnerability."),
                        recommendation="Upgrade the dependency to a fixed version or document a compensating control.",
                        deterministic=True,
                        confidence=0.85,
                        metadata={"dependency": dep.get("name"), "version": dep.get("version"), "vulnerability": vuln.get("id")},
                    )
                )
        return findings

    def _parse_npm_audit(self, payload: Any) -> list[GovernanceFinding]:
        vulnerabilities = payload.get("vulnerabilities", {}) if isinstance(payload, dict) else {}
        findings: list[GovernanceFinding] = []
        if not isinstance(vulnerabilities, dict):
            return findings
        for name, vuln in vulnerabilities.items():
            if not isinstance(vuln, dict):
                continue
            severity = self._normalize_severity(vuln.get("severity"), default="medium")
            findings.append(
                self._finding(
                    source="npm-audit",
                    category="dependency",
                    severity=severity,
                    title=f"Vulnerable npm dependency: {name}",
                    summary=str(vuln.get("title") or vuln.get("name") or "npm audit reported a vulnerability."),
                    recommendation="Upgrade the package or apply an audited override.",
                    deterministic=True,
                    confidence=0.82,
                    metadata={"dependency": name, "range": vuln.get("range")},
                )
            )
        return findings

    def _parse_gitleaks(self, payload: Any) -> list[GovernanceFinding]:
        items = payload if isinstance(payload, list) else payload.get("findings", []) if isinstance(payload, dict) else []
        findings: list[GovernanceFinding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            findings.append(
                self._finding(
                    source="gitleaks",
                    category="secret",
                    severity="critical",
                    title=str(item.get("RuleID") or item.get("rule") or "Secret leak"),
                    summary="Gitleaks reported a committed secret.",
                    file_path=str(item.get("File") or item.get("file") or ""),
                    line=int(item.get("StartLine") or item.get("line") or 0) or None,
                    recommendation="Remove the secret, rotate it, and replace it with a managed secret reference.",
                    deterministic=True,
                    confidence=0.9,
                    metadata={"fingerprint": item.get("Fingerprint")},
                )
            )
        return findings

    def _score_findings(self, findings: list[GovernanceFinding]) -> RiskScore:
        total_penalty = min(100, sum(SEVERITY_PENALTY[item.severity] for item in findings))
        score = max(0, 100 - total_penalty)

        def category_score(category: str) -> int:
            penalty = sum(SEVERITY_PENALTY[item.severity] for item in findings if item.category == category)
            return max(0, 100 - min(100, penalty))

        if score < 60 or any(item.severity == "critical" for item in findings):
            level = "CRITICAL"
        elif score < 80:
            level = "HIGH"
        elif score < 90:
            level = "MEDIUM"
        else:
            level = "LOW"
        return RiskScore(
            score=score,
            level=level,
            security=category_score("security"),
            performance=category_score("performance"),
            architecture=category_score("architecture"),
            database=category_score("database"),
            dependency=category_score("dependency"),
            test_coverage=category_score("test"),
            maintainability=category_score("maintainability"),
        )

    def _decide(self, findings: list[GovernanceFinding], risk_score: RiskScore, arguments: dict[str, Any]) -> ApprovalDecision:
        policy = self._policy(arguments)
        blocking: list[GovernanceFinding] = []
        critical = [item for item in findings if item.severity == "critical"]
        high = [item for item in findings if item.severity == "high"]
        high_security = [item for item in high if item.category in {"security", "secret"}]
        if policy["block_on_critical"]:
            blocking.extend(critical)
        if policy["block_on_high_security"]:
            blocking.extend(high_security)
        if policy["block_on_secret"]:
            blocking.extend([item for item in findings if item.category == "secret"])
        if len(high) >= int(policy["max_high_findings"]) + 1:
            blocking.extend(high)
        if risk_score.score < int(policy["minimum_score"]):
            blocking.extend([item for item in findings if SEVERITY_ORDER[item.severity] >= SEVERITY_ORDER["medium"]])
        blocking_ids = sorted({item.id for item in blocking})
        if blocking_ids:
            return ApprovalDecision(
                status="blocked",
                reason="Governance policy blocked this change because one or more high-risk findings require remediation.",
                blocking_findings=blocking_ids,
                required_actions=[
                    "Fix or explicitly waive all blocking findings.",
                    "Re-run deterministic scanners and code governance review.",
                    "Attach targeted tests for changed high-risk behavior.",
                ],
                policy=policy,
            )
        if findings or risk_score.score < int(policy["auto_pass_score"]):
            return ApprovalDecision(
                status="warning",
                reason="The change can continue with reviewer attention, but residual findings remain.",
                required_actions=["Review non-blocking findings before merge."],
                policy=policy,
            )
        return ApprovalDecision(
            status="pass",
            reason="No blocking governance findings were detected.",
            policy=policy,
        )

    def _policy(self, arguments: dict[str, Any]) -> dict[str, Any]:
        policy = arguments.get("policy") if isinstance(arguments.get("policy"), dict) else {}
        return {
            "minimum_score": int(policy.get("minimum_score", 80)),
            "auto_pass_score": int(policy.get("auto_pass_score", 90)),
            "max_high_findings": int(policy.get("max_high_findings", 2)),
            "block_on_critical": bool(policy.get("block_on_critical", True)),
            "block_on_high_security": bool(policy.get("block_on_high_security", True)),
            "block_on_secret": bool(policy.get("block_on_secret", True)),
        }

    def _build_report_markdown(
        self,
        *,
        project_name: str,
        changed_files: list[ChangedFile],
        findings: list[GovernanceFinding],
        risk_score: RiskScore,
        decision: ApprovalDecision,
        code_graph: dict[str, Any] | None = None,
    ) -> str:
        counts = Counter(item.severity for item in findings)
        graph_metrics = ((code_graph or {}).get("metrics") or {}) if isinstance(code_graph, dict) else {}
        lines = [
            f"# Code Governance Report: {project_name}",
            "",
            "## Decision",
            "",
            f"- Status: {decision.status}",
            f"- Reason: {decision.reason}",
            f"- Score: {risk_score.score}",
            f"- Level: {risk_score.level}",
            f"- Findings: critical={counts['critical']}, high={counts['high']}, medium={counts['medium']}, low={counts['low']}, info={counts['info']}",
            f"- Code Graph: nodes={graph_metrics.get('node_count', 0)}, edges={graph_metrics.get('edge_count', 0)}, impacted={graph_metrics.get('impacted_node_count', 0)}",
            "",
            "## Changed Files",
            "",
        ]
        if changed_files:
            for item in changed_files[:80]:
                lines.append(f"- {item.path} ({item.change_type}, {item.language}, +{item.additions}/-{item.deletions})")
        else:
            lines.append("- No changed files were captured.")
        lines.extend(["", "## Findings", ""])
        if findings:
            for item in sorted(findings, key=lambda value: (-SEVERITY_ORDER[value.severity], value.category, value.file_path)):
                location = f"{item.file_path}:{item.line}" if item.file_path and item.line else item.file_path
                lines.extend(
                    [
                        f"### {item.id} [{item.severity}] {item.title}",
                        "",
                        f"- Category: {item.category}",
                        f"- Source: {item.source}",
                        f"- Location: {location or 'n/a'}",
                        f"- Summary: {item.summary}",
                        f"- Recommendation: {item.recommendation or 'Review and remediate.'}",
                        "",
                    ]
                )
        else:
            lines.append("- No findings.")
        return "\n".join(lines).strip()

    def _build_metrics(
        self,
        changed_files: list[ChangedFile],
        findings: list[GovernanceFinding],
        scanner_runs: list[dict[str, Any]] | None = None,
        code_graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        severity_counts = Counter(item.severity for item in findings)
        category_counts = Counter(item.category for item in findings)
        scanner_runs = scanner_runs or []
        graph_metrics = ((code_graph or {}).get("metrics") or {}) if isinstance(code_graph, dict) else {}
        return {
            "changed_file_count": len(changed_files),
            "finding_count": len(findings),
            "severity_counts": dict(severity_counts),
            "category_counts": dict(category_counts),
            "blocking_candidate_count": sum(1 for item in findings if item.severity in {"critical", "high"}),
            "scanner_run_count": len(scanner_runs),
            "scanner_completed_count": sum(1 for item in scanner_runs if item.get("status") == "completed"),
            "scanner_skipped_count": sum(1 for item in scanner_runs if item.get("status") == "skipped"),
            "scanner_failed_count": sum(1 for item in scanner_runs if item.get("status") == "failed"),
            "code_graph_node_count": int(graph_metrics.get("node_count") or 0),
            "code_graph_edge_count": int(graph_metrics.get("edge_count") or 0),
            "code_graph_impacted_node_count": int(graph_metrics.get("impacted_node_count") or 0),
            "code_graph_scanned_file_count": int(graph_metrics.get("scanned_file_count") or 0),
        }

    async def _run(
        self,
        command: str,
        args: list[str],
        timeout_seconds: float = 20.0,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *args,
                cwd=str(cwd) if cwd else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except Exception as exc:
            return {"ok": False, "stdout": "", "stderr": str(exc), "exit_code": -1}
        return {
            "ok": proc.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": proc.returncode,
        }

    def _finding(
        self,
        *,
        source: str,
        category: str,
        severity: str,
        title: str,
        summary: str,
        file_path: str = "",
        line: int | None = None,
        evidence: list[dict[str, Any]] | None = None,
        confidence: float = 0.0,
        deterministic: bool = False,
        recommendation: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GovernanceFinding:
        return GovernanceFinding(
            id=f"GOV-{uuid4().hex[:10].upper()}",
            source=source,
            category=category,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            title=title,
            summary=summary,
            file_path=file_path,
            line=line,
            evidence=evidence or [],
            confidence=confidence,
            deterministic=deterministic,
            recommendation=recommendation,
            metadata=metadata or {},
        )

    def _dedupe_findings(self, findings: list[GovernanceFinding]) -> list[GovernanceFinding]:
        seen: set[tuple[str, str, str, int | None, str]] = set()
        result: list[GovernanceFinding] = []
        for item in findings:
            key = (item.source, item.title, item.file_path, item.line, item.category)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _resolve_artifact_path(self, root: Path, raw_path: str) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(raw_path)
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        if resolved != root and root not in resolved.parents and self._workspace_root not in resolved.parents:
            return None
        return resolved

    def _failed_result(self, summary: str, *, error: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "ok": False,
            "summary": summary,
            "approval_decision": "blocked",
            "decision": {
                "status": "blocked",
                "reason": summary,
                "blocking_findings": [],
                "required_actions": ["Fix governance runner configuration and re-run review."],
            },
            "risk_score": {
                "score": 0,
                "level": "CRITICAL",
                "security": 0,
                "performance": 0,
                "architecture": 0,
                "database": 0,
                "dependency": 0,
                "test_coverage": 0,
                "maintainability": 0,
            },
            "findings": [],
            "error": error,
        }

    def _skipped_result(self, summary: str, *, reason: str) -> dict[str, Any]:
        return {
            "status": "completed",
            "ok": True,
            "summary": summary,
            "approval_decision": "warning",
            "decision": {
                "status": "warning",
                "reason": summary,
                "blocking_findings": [],
                "required_actions": ["Run deterministic governance scanners in CI for this source type."],
            },
            "risk_score": {
                "score": 100,
                "level": "LOW",
                "security": 100,
                "performance": 100,
                "architecture": 100,
                "database": 100,
                "dependency": 100,
                "test_coverage": 100,
                "maintainability": 100,
            },
            "findings": [],
            "changed_files": [],
            "metrics": {
                "changed_file_count": 0,
                "finding_count": 0,
                "skipped": True,
                "skip_reason": reason,
            },
        }

    def _summary(self, decision: ApprovalDecision, risk_score: RiskScore, findings: list[GovernanceFinding]) -> str:
        return (
            f"Code governance decision={decision.status}, score={risk_score.score}, "
            f"level={risk_score.level}, findings={len(findings)}."
        )

    def _parse_numstat(self, value: str) -> int:
        try:
            return int(value)
        except ValueError:
            return 0

    def _relative_path(self, root: Path, path: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.name

    def _graph_node_id(self, kind: str, label: str) -> str:
        normalized = re.sub(r"\s+", " ", str(label or "unknown").strip())
        return f"{kind}:{normalized}"

    def _layer_role_for_path(self, path: str) -> str:
        normalized = path.replace("\\", "/").lower()
        name = Path(path).stem.lower()
        if any(token in normalized for token in ("/api/", "/routes/", "/controllers/", "/controller/")):
            return "controller"
        if "service" in name or "/services/" in normalized or "/modules/" in normalized and normalized.endswith("/service.py"):
            return "service"
        if any(token in normalized for token in ("/repositories/", "/repository/", "/mapper/", "/dao/")):
            return "repository"
        if any(token in normalized for token in ("/domain/", "/schemas/", "/entities/", "/models/")):
            return "domain"
        if normalized.endswith(".sql"):
            return "database"
        if any(token in normalized for token in ("/stores/", "/views/", "/components/", "/pages/")):
            return "frontend"
        return "module"

    def _layer_role_for_name(self, name: str, path: str) -> str:
        lowered = name.lower()
        if lowered.endswith("controller") or lowered.endswith("router"):
            return "controller"
        if lowered.endswith("service"):
            return "service"
        if lowered.endswith(("repository", "mapper", "dao")):
            return "repository"
        if lowered.endswith(("entity", "model", "schema")):
            return "domain"
        return self._layer_role_for_path(path)

    def _role_for_call_name(self, name: str) -> str:
        lowered = name.lower()
        if any(token in lowered for token in ("repository", "mapper", "dao", ".collection", ".aql", "get_db")):
            return "repository"
        if any(token in lowered for token in ("service",)):
            return "service"
        if any(token in lowered for token in ("execute", "query", "insert", "update", "delete", "replace", "select")):
            return "database"
        if any(token in lowered for token in ("fetch", "request", "urlopen", "requests.", "httpx.")):
            return "api"
        return "call"

    def _python_route_paths(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        routes: list[str] = []
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            name = self._python_call_name(decorator.func)
            if not name or not any(name.endswith(f".{method}") for method in ("get", "post", "put", "patch", "delete")):
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                routes.append(str(decorator.args[0].value))
            else:
                routes.append(name)
        return routes

    def _python_call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._python_call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Subscript):
            return self._python_call_name(node.value)
        if isinstance(node, ast.Call):
            return self._python_call_name(node.func)
        return ""

    def _db_target_from_call(self, call_name: str) -> str:
        lowered = call_name.lower()
        if ".collection" in lowered:
            return "collection"
        if ".aql.execute" in lowered or lowered.endswith(".execute"):
            return "query-execute"
        if any(token in lowered for token in (".insert", ".update", ".replace", ".delete", ".remove")):
            return "database-write"
        return ""

    def _looks_like_query_call(self, call_name: str) -> bool:
        lowered = call_name.lower()
        return any(
            token in lowered
            for token in (
                "repository.",
                "_repository.",
                "mapper.",
                "dao.",
                ".collection",
                ".aql.execute",
                ".execute",
                ".query",
                "selectbyid",
                "select_by_id",
                "findbyid",
                "find_by_id",
            )
        )

    def _extract_endpoint_literal(self, value: str) -> str:
        match = re.search(r"['\"]([^'\"]+)['\"]", value)
        if not match:
            template_match = re.search(r"`([^`]+)`", value)
            if template_match:
                return template_match.group(1)[:160]
            return ""
        return match.group(1)[:160]

    def _line_number(self, text: str, offset: int) -> int:
        return text.count("\n", 0, max(0, offset)) + 1

    def _change_type_from_status(self, status: str) -> str:
        if status.startswith("A"):
            return "added"
        if status.startswith("D"):
            return "deleted"
        if status.startswith("R"):
            return "renamed"
        if status:
            return "modified"
        return "unknown"

    def _language_for_path(self, path: str) -> str:
        suffix = Path(path).suffix.lower()
        return {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".vue": "vue",
            ".java": "java",
            ".html": "html",
            ".md": "markdown",
            ".sql": "sql",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
        }.get(suffix, "other")

    def _risk_hints_for_path(self, path: str) -> list[str]:
        normalized = path.replace("\\", "/").lower()
        hints: list[str] = []
        if any(token in normalized for token in ("auth", "oauth", "permission", "security", "credential")):
            hints.append("auth-or-security-boundary")
        if any(token in normalized for token in ("database", "migration", "sql", "store", "repository")):
            hints.append("database-impact")
        if Path(path).name in DEPENDENCY_FILES:
            hints.append("dependency-impact")
        if any(token in normalized for token in ("runtime", "executor", "subprocess", "command")):
            hints.append("execution-boundary")
        return hints

    def _is_test_path(self, path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        return any(token in normalized for token in TEST_PATH_TOKENS)

    def _normalize_severity(self, value: Any, *, default: str) -> str:
        text = str(value or "").strip().lower()
        if text in {"critical", "error"}:
            return "critical"
        if text in {"high"}:
            return "high"
        if text in {"medium", "warning", "moderate"}:
            return "medium"
        if text in {"low"}:
            return "low"
        if text in {"info", "informational"}:
            return "info"
        return default

    def _category_from_text(self, value: str) -> str:
        text = value.lower()
        if any(token in text for token in ("secret", "token", "password", "credential")):
            return "secret"
        if any(token in text for token in ("sql", "database", "query")):
            return "database"
        if any(token in text for token in ("dependency", "package", "vulnerab")):
            return "dependency"
        if any(token in text for token in ("performance", "n+1", "loop")):
            return "performance"
        return "security"
