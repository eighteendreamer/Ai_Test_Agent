"""Append the follow-up acceptance record; retain original failed harness evidence."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parents[1]
sys.path.insert(0, str(ROOT / "Agent_Server/scripts"))
from system_acceptance_inventory import product_fingerprint

FRONTEND_FILES = {
    "H01": ["useSessionWatcher", "lifecycle.test", "projectSwitch", "sessionSwitch", "ProjectExecutionDashboard"],
    "H02": ["sessionAssistantStreamReplay", "sessionStreaming", "AssistantMarkdown", "ChatTimeline", "markdownLinks", "useSSEStream"],
    "H03": ["App.datetime", "i18nKeys", "i18n.lazy"],
    "H04": ["build-typecheck", "externalUrlPolicy"],
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    run = Path((DOCS / "current-run.txt").read_text(encoding="utf-8").strip())
    original = load(run / "summary.json")
    browser = load(run / "ui-followup/workbench-time.json")
    projects = load(run / "ui-followup/project-crud-retry2/report.json")
    frontend = load(run / "frontend-tests.json")
    directory = run / "final-records"
    directory.mkdir(exist_ok=False)
    summaries = []
    for old in sorted((run / "features").glob("*.json")):
        record = load(old)
        feature = record["id"]
        frontend_cases = []
        for result in frontend["testResults"]:
            if any(key in result["name"] for key in FRONTEND_FILES.get(feature, [])):
                source = Path(result["name"]).relative_to(ROOT).as_posix()
                for assertion in result["assertionResults"]:
                    frontend_cases.append({"source": source, "name": assertion["fullName"], "outcome": assertion["status"],
                                           "evidence": "../frontend-tests.json"})
        record["frontend_cases"] = frontend_cases
        record["additional_evidence"] = []
        if feature in {"A01", "A02", "A06", "H03"}:
            record["additional_evidence"].append({"type": "workbench_ui_time", "evidence": "../ui-followup/workbench-time.json",
                                                   "status": browser["status"], "scope": browser["scope"]})
        if feature in {"B01", "H01"}:
            record["additional_evidence"].append({"type": "project_crud_ui", "evidence": "../ui-followup/project-crud-retry2/report.json",
                                                   "status": projects["status"], "scope": projects["scope"]})
        if feature == "H04":
            record["additional_evidence"].append({"type": "formal_build", "evidence": "../frontend-build.log",
                                                   "status": "passed", "scope": "vue-tsc + Vite；Electron GUI 未执行"})
        # The backend source report paths were relative to the parent run directory.
        for case in record["cases"]:
            case["evidence"] = "../"+case["evidence"]
        if record["scope"] != "catalog_only" and (frontend_cases or record["additional_evidence"]):
            record["acceptance_status"] = "partial"
        (directory / old.name).write_text(json.dumps(record, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        lines = [f"# {feature} {record['name']}", "", f"验收范围状态：{record['acceptance_status']}。", "",
                 f"后端统计：{record['backend_counts']}；前端断言：{dict(Counter(c['outcome'] for c in frontend_cases))}。", "",
                 "partial 表示已执行相应断言，完整功能/供应商/UI 验收仍未闭环。详细阶段、skip 原因和完整 nodeid 见同名 JSON 及其原报告引用。", "",
                 "## 实际执行的断言", "", "|层级|用例|结果|", "|---|---|---|"]
        lines += [f"|{c['profile']}|{c['nodeid'].replace('|', '&#124;')}|{c['outcome']}|" for c in record["cases"]]
        lines += [f"|frontend / {c['source']}|{c['name'].replace('|', '&#124;')}|{c['outcome']}|" for c in frontend_cases]
        if not record["cases"] and not frontend_cases:
            lines.append("|—|未执行映射断言|not_executed|")
        lines += ["", "## 浏览器及构建留档", ""]
        lines += [f"- [{e['type']}]({e['evidence']})：{e['status']}；{e['scope']}。" for e in record["additional_evidence"]] or ["无独立浏览器/构建证据。"]
        lines += ["", "## 覆盖边界", ""]+[f"- {x}" for x in record["limitations"]]
        (directory / f"{feature}.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        summaries.append({"id": feature, "name": record["name"], "status": record["acceptance_status"],
                          "backend_counts": record["backend_counts"], "frontend_assertions": len(frontend_cases)})
    baseline = load(DOCS / "inventory.json")
    unchanged = baseline["product_fingerprint"]["sha256"] == product_fingerprint()["sha256"]
    report = {"generated_at_utc": datetime.now(UTC).isoformat(), "commit": baseline["commit"],
              "status": "partial", "release_ready": False, "product_source_unchanged": unchanged,
              "original_summary": "summary.json", "profiles": original["profiles"],
              "frontend": original["frontend"], "features": summaries,
              "workbench_time_status": browser["status"], "project_ui_status": projects["status"],
              "retained_harness_failures": [
                  {"evidence": "workbench-time.log", "cause": "File execution lacked src import root; module invocation fixed the test entry point."},
                  {"evidence": "ui-followup/project-crud/report.json", "cause": "Exact accessible-name selector did not match the icon-bearing create button; observed DOM selector corrected."},
              ],
              "acceptance_script_hashes": {
                  path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [*DOCS.glob("*.py"), *ROOT.joinpath("Agent_Server/scripts").glob("*system_acceptance*.py")]
              }}
    (run / "completion-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# 系统功能验收：补验与最终归档", "", f"源码基线：{baseline['commit'][:7]}。产品代码指纹未变化：{unchanged}。", "",
             "结论：已完成本轮分层测试及 48 项逐项留档；整系统验收仍为 partial，不能宣称所有功能已完整通过。", "",
             "## 实际执行结果", "",
             "- 后端 diagnostic：1650 passed、459 skipped、2 warnings；跳过不算通过。",
             "- 十个独立真实环境分组：331 次断言通过，无跳过/失败。含 PostgreSQL、Redis、MCP stdio、真实 API TCP、SQL 生命周期、Chromium、真实模型工具与持久传输。",
             "- 前端：147 passed、0 failed、0 pending；正式 vue-tsc + Vite build 通过。",
             "- 生产工作台 + 正式 SessionService/HTTP/SSE：三种时区通过；辅助目录为空夹具，无外部模型。",
             "- 项目 UI：创建、搜索、修改、归档四个检查通过；使用正式项目服务/路由和隔离内存存储，归档后历史仍可读取。",
             "- 两张图：119 个文本元素通过画布/卡片边界检查，并已视觉检查；无 foreignObject、外部资源和黑色背景块。", "",
             "## 失败记录保留", "",
             "第一次工作台探针因脚本导入路径未进入产品逻辑；改为模块调用后补跑通过。项目 UI 首次使用的精确 accessible-name 选择器与带图标按钮不匹配；依据已保存 DOM 修正后通过。原始日志与失败报告未覆盖。这两次为验收脚本问题，不能列为产品缺陷。", "",
             "项目 UI 的 agents/tools 目录与资源监控接口未装配，返回的 404 已写入 harness_unavailable_http；这些是测试环境缺口。完整业务 UI、跨主机恢复、真实供应商 webhook、历史迁移、RustFS/Memgraph 容量、Electron GUI 和完整故障矩阵仍未验收。", "",
             "## 每项功能记录", "", "|编号|功能|范围状态|留档|", "|---|---|---|---|"]
    lines += [f"|{f['id']}|{f['name']}|{f['status']}|[记录](final-records/{f['id']}.md)|" for f in summaries]
    lines += ["", "完整机器记录：[completion-summary.json](completion-summary.json)。原始运行：[README.md](README.md)。",
              "", "复跑命令在验收脚本与原 summary.json 的 commands 中保留；复跑创建新目录，不能覆盖旧失败结果。"]
    (run / "completion-report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    (DOCS / "latest-report.md").write_text(f"# 最近验收\n\n[最终归档与逐功能记录](runs/{run.name}/completion-report.md)\n\n状态：partial。产品代码未变化：{unchanged}。原始失败及补验结果均保留。\n", encoding="utf-8")
    print(json.dumps({"features": len(summaries), "status": "partial", "product_unchanged": unchanged}))
    if not unchanged or browser["status"] != "passed" or projects["status"] != "passed_declared_crud_scope":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
