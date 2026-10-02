"""Run existing acceptance scopes and archive per-feature evidence without fixes."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from system_acceptance_inventory import ROOT, SERVER, product_fingerprint

DOCS = ROOT / "docs/system_acceptance_2026-10-02"
PROFILES = [
    "diagnostic", "session-store", "migration-core", "postgres-core", "redis-core",
    "metrics-core", "mcp-core", "api-execution", "sql-lifecycle", "browser-core",
    "model-browser",
]


def command_record(name, command, cwd, output):
    started = datetime.now(UTC).isoformat()
    with (output / f"{name}.log").open("x", encoding="utf-8") as log:
        try:
            result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=False)
            code = result.returncode
        except OSError as error:
            log.write(f"{type(error).__name__}: {error}\n")
            code = 127
    record = {"name": name, "command": command, "cwd": str(cwd), "started_at": started,
              "finished_at": datetime.now(UTC).isoformat(), "exit_code": code,
              "log": f"{name}.log"}
    print(json.dumps({"step": name, "exit_code": code}), flush=True)
    return record


def archive_features(output, gates, frontend, browser):
    features = json.loads((DOCS / "functional_catalog.json").read_text(encoding="utf-8"))
    directory = output / "features"
    directory.mkdir(exist_ok=True)
    summaries = []
    for feature in features:
        cases = []
        selected_files = set(feature["backend_test_files"])
        for report_path, gate in gates:
            for case in gate.get("cases", []):
                filename = case["nodeid"].split("::")[0].replace("\\", "/").split("/")[-1]
                if filename in selected_files:
                    cases.append({**case, "profile": gate["profile"],
                                  "evidence": report_path.relative_to(output).as_posix()})
        counts = dict(Counter(case["outcome"] for case in cases))
        status = "failed" if counts.get("failed") else "partial" if cases else "not_executed"
        if feature["scope"] == "catalog_only":
            status = "catalog_only"
        extras = []
        # The full Vitest result is linked, without treating every file as each H feature.
        if feature["id"].startswith("H"):
            extras.append({"type": "frontend_suite", "evidence": "frontend-tests.json",
                           "scope": "文件级断言另见原报告，不代表本功能的全部 UI 通过",
                           "status": "passed" if frontend.get("success") else "failed_or_unavailable"})
        if feature["id"] in {"A01", "A02", "A06", "H03"} and browser:
            extras.append({"type": "workbench_ui_probe", "evidence": "workbench-time.json",
                           "status": browser["status"],
                           "scope": browser["scope"], "checks": browser["results"]})
        record = {**feature, "acceptance_status": status, "backend_counts": counts,
                  "cases": cases, "additional_evidence": extras,
                  "limitations": [
                      "partial 表示有断言执行，尚非整项验收通过；通过个数可能含不同环境重复执行。",
                      "模块测试包含替身；真实依赖仅以相应 profile 和断言为准。",
                      "完整业务 UI、真实供应商操作及故障矩阵未被本记录推定为通过。",
                  ]}
        (directory / f"{feature['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        lines = [f"# {feature['id']} {feature['name']}", "",
                 f"结果：{status}；后端断言执行统计：{counts or '未映射执行用例'}。", "",
                 f"依据源码：{feature['source']}。", "",
                 "完整用例、阶段结果与原报告引用见同名 JSON。当前仅对列出的断言负责，不声称整项真实环境或完整 UI 已通过。", "",
                 "## 用例执行留档", "", "|分组|用例 nodeid|结果|", "|---|---|---|"]
        lines += [f"|{c['profile']}|{c['nodeid'].replace('|', '&#124;')}|{c['outcome']}|" for c in cases]
        if not cases:
            lines.append("|—|未执行后端映射用例|not_executed|")
        lines += ["", "## 其他证据", ""]
        lines += [f"- {e['type']}：{e['status']}；{e['scope']}。" for e in extras] or ["无单独浏览器/前端执行证据。"]
        lines += ["", "## 未覆盖边界", ""] + [f"- {line}" for line in record["limitations"]]
        (directory / f"{feature['id']}.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        summaries.append({"id": feature["id"], "name": feature["name"], "status": status, "counts": counts})
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--skip-live", action="store_true", help="Only diagnostic; explicitly leaves integrations unexecuted")
    args = parser.parse_args()
    before = product_fingerprint()
    output = DOCS / "runs" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:10]}"
    output.mkdir(parents=True, exist_ok=False)
    (DOCS / "current-run.txt").write_text(str(output)+"\n", encoding="utf-8")
    commands = []
    for profile in ["diagnostic"] if args.skip_live else PROFILES:
        commands.append(command_record(profile, [sys.executable, "scripts/run_test_quality_gate.py",
                                                "--profile", profile, "--output-root", str(output / "gates")], SERVER, output))
    npm = args.node.parent / "node_modules/npm/bin/npm-cli.js"
    if not npm.is_file():
        raise FileNotFoundError(f"npm CLI not found beside verified Node: {npm}")
    web = ROOT / "agent_web"
    commands.append(command_record("frontend-tests", [str(args.node), str(npm), "test", "--", "--reporter=json",
                                                      f"--outputFile={output / 'frontend-tests.json'}"], web, output))
    commands.append(command_record("frontend-build", [str(args.node), str(npm), "run", "build"], web, output))
    if commands[-1]["exit_code"] == 0:
        commands.append(command_record("workbench-time", [sys.executable, "-m", "scripts.frontend_time_browser_probe",
                                                         "--output", str(output / "workbench-time.json")], SERVER, output))
    gates = [(path, json.loads(path.read_text(encoding="utf-8")))
             for path in sorted(output.glob("gates/*/report.json"))]
    frontend_path = output / "frontend-tests.json"
    frontend = json.loads(frontend_path.read_text(encoding="utf-8")) if frontend_path.exists() else {}
    browser_path = output / "workbench-time.json"
    browser = json.loads(browser_path.read_text(encoding="utf-8")) if browser_path.exists() else {}
    summaries = archive_features(output, gates, frontend, browser)
    unchanged = before["sha256"] == product_fingerprint()["sha256"]
    report = {"generated_at_utc": datetime.now(UTC).isoformat(), "product_source_unchanged": unchanged,
              "product_sha256": before["sha256"], "commands": commands, "features": summaries,
              "profiles": [{"profile": g["profile"], "status": g["status"], "counts": g.get("counts"),
                            "evidence": p.relative_to(output).as_posix()} for p, g in gates],
              "frontend": {key: frontend.get(key) for key in ("success", "numPassedTests", "numFailedTests", "numPendingTests")},
              "browser": browser, "release_ready": False,
              "status": "partial" if unchanged and all(c["exit_code"] == 0 for c in commands) else "failed"}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# 本轮系统功能验收记录", "", f"执行目录：{output.name}；整体：{report['status']}；产品代码指纹未变化：{unchanged}。", "",
             "通过的是实际执行断言；整项功能验收仍为 partial。诊断组跳过的集成条目不是通过。", "",
             "## 后端分组", "", "|分组|状态|结果统计|", "|---|---|---|"]
    lines += [f"|{g['profile']}|{g['status']}|{g.get('counts')}|" for _, g in gates]
    lines += ["", f"前端：{report['frontend']}。浏览器时间探针：{browser.get('status', '未执行')}。", "",
              "## 按功能留档", "", "|编号|功能|状态|记录|", "|---|---|---|---|"]
    lines += [f"|{f['id']}|{f['name']}|{f['status']}|[执行记录](features/{f['id']}.md)|" for f in summaries]
    lines += ["", "所有完整 case nodeid、阶段结果、skip 原因及命令日志保存在本目录。详见 summary.json；当前不作为上线批准。",
              "", "Docker 破坏/强杀组、完整应用 HITL、邮件渠道真实写入、RustFS、Memgraph 容量与 Electron GUI 本轮未执行，避免触碰未经隔离的业务数据。"]
    (output / "README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    (DOCS / "latest-report.md").write_text(f"# 最近验收\n\n[本轮完整报告](runs/{output.name}/README.md)\n\n状态：{report['status']}。产品代码未变化：{unchanged}。\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": report["status"], "product_source_unchanged": unchanged}), flush=True)
    return 0 if report["status"] == "partial" else 1


if __name__ == "__main__":
    raise SystemExit(main())
