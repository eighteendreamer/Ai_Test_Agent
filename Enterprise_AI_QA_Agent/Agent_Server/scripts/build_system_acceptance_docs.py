"""Build source-grounded catalog and editable SVG diagrams for acceptance."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/system_acceptance_2026-10-02"

# A test-file mapping declares coverage candidates, not complete feature acceptance.
DOMAINS = [
    ("A", "会话工作台", "Agent_Server/src/application/sessions/session_service.py", [
        ("会话创建与项目绑定", "session_project_binding project_management_api session_store_value_contract"),
        ("消息、流式输出与正文对账", "session_event_delivery assistant_stream_persistence execution_event_context"),
        ("审批、继续与中断", "runtime_interrupt_tool_calls security_approval_boundary cancelled_run_missing_approval_recovery"),
        ("持久派发、排队与恢复", "agent_task_dispatch busy_session_queue_concurrency coordinator_durable_children"),
        ("任务池与执行流程投影", "task_pool_service session_flow_projection"),
        ("会话时间、生命周期与清理", "session_turn_reaper session_lock_retention lifespan_resource_cleanup"),
    ]),
    ("B", "项目与用例驱动测试", "Agent_Server/src/application/test_runs/run_service.py", [
        ("项目管理与执行总览", "project_management_api project_overview project_execution_summary"),
        ("用例草稿生成与来源", "test_case_generation_pipeline"),
        ("评审、启用与固定版本", "test_case_lifecycle"),
        ("套件冻结版本", "test_suite_version_freeze"),
        ("运行领取、执行、结果与证据", "test_run_lifecycle test_run_postgres_claim test_run_outbox_transaction case_execution_verification"),
        ("失败项创建新回归运行", "test_run_regression project_regression_frontend_contract"),
    ]),
    ("C", "已接入的测试模式", "Agent_Server/src/modes", [
        ("默认 Agent 与工具协议", "model_tool_visibility deep_agent_runtime_adapter invalid_tool_plan_visibility"),
        ("代码治理与 SSH 源码", "code_review_skills code_review_ssh_source"),
        ("API 测试与断言", "api_request_body_contract api_task_executor_recovery api_tester_free_text_assertions api_mode_session_isolation"),
        ("性能测试与多引擎报告", "performance_runner performance_k6_adapter performance_jmeter_adapter performance_report_builder"),
        ("安全测试、授权与缺陷复测", "security_target_guard security_profile_capabilities security_bug_reproduction_package_api security_execution_environment_upgrade"),
        ("兼容性 runner 与持久回执", "compatibility_runner_service compatibility_runner_persistence compatibility_mode_skills"),
        ("冒烟方案审批与冻结执行", "smoke_plan_execution_boundary smoke_project_isolation smoke_mode_skills"),
        ("UI 自动化、录制与回放", "ui_mode_skills recording_approval_service recorder_session_service replay_executor recording_case_draft"),
    ]),
    ("D", "模型与扩展接入", "Agent_Server/src/application/registries/registry_service.py", [
        ("模型配置、流式协议与错误", "model_settings_routes model_clients model_failure_lifecycle google_stream_tool_call_identity openai_parallel_tool_capability"),
        ("OAuth 回调与授权状态", "oauth_callback_html"),
        ("Skill 管理、重载与路径边界", "skill_management_boundaries skill_registry_reload skill_route_error_classification"),
        ("MCP 管理与工具目录", "mcp_management_route_status tool_registry_lazy_catalog"),
        ("意图识别、权限与工具治理", "intent_safety_routing semantic_intent_failure_visibility tool_executor_safety_gate"),
    ]),
    ("E", "知识、文档与工件", "Agent_Server/src/application/knowledge/knowledge_graph_service.py", [
        ("项目知识图谱查询与编辑", "knowledge_graph_service knowledge_graph_read_only"),
        ("长期记忆与向量检索", "memory_vector_retrieval memory_vector_replica memory_embedding_runtime"),
        ("上下文压缩与索引重建", "context_compaction compaction_worker memory_vector_rebuild live_compaction_ownership"),
        ("API 文档导入、解析与项目绑定", "api_doc_resolution api_doc_project_binding api_docs_bom api_docs_origin_security"),
        ("附件选择、上传与工件下载", "attachment_reader_selection artifact_storage_service xlsx_artifact_download"),
        ("测试报表与模板渲染", "report_service performance_report_builder"),
    ]),
    ("F", "接入渠道与配置", "Agent_Server/src/application/settings/settings_service.py", [
        ("邮件配置、授权与发送", "mail_service mail_send_error_classification mail_setup_action_validation tencent_auth_persistence"),
        ("渠道凭据、配对与接入策略", "channel_config channel_credential_rotation channel_gateway_policy"),
        ("供应商 webhook 接入", "mail_webhook_contract"),
        ("设置、导出、导入与清理", "settings_data_export settings_data_import settings_cleanup_contract"),
        ("赞助信息配置与展示", "sponsor_config"),
    ]),
    ("G", "运行基础设施", "Agent_Server/src/main.py", [
        ("鉴权、CORS 与请求关联", "api_authentication cors_credentials request_context_middleware"),
        ("Redis 投递、重试与 Outbox", "redis_task_queue redis_task_retry postgres_task_outbox_relay live_redis_task_delivery"),
        ("配额、租约与资源占用", "resource_quota_api resource_lease_manager resource_occupancy execution_resource_waiting"),
        ("Docker 与浏览器执行环境", "docker_route_error_status browser_resource_harness python_playwright_lifecycle live_browser_lifecycle"),
        ("观测、Token 与多进程指标", "observability_contracts token_usage metrics_core metrics_multiprocess"),
        ("版本迁移与测试质量门", "schema_migrations quality_gate_reporting release_rollback_preflight"),
    ]),
    ("H", "前端与桌面体验", "agent_web/src/router/index.ts", [
        ("页面切换与异步生命周期", ""),
        ("流式消息、Markdown 与链接", "assistant_stream_browser_probe"),
        ("多语言与 UTC 时间显示", ""),
        ("正式构建、包拆分与外部协议", ""),
    ]),
    ("I", "目录能力与联合验收", "Agent_Server/src/registry/modes.py", [
        ("六种扩展模式：目录占位", ""),
        ("全系统联合验收与故障矩阵", "live_model_durable_dispatch live_model_tool_stream"),
    ]),
]

GROUPS = {
    "实现或治理仍有剩余": ["P0-33", "P1-11", "P1-15", "P1-29", "P1-38", "P1-62", "P1-67", "P1-68", "P1-74", "P1-77", "P1-81", "P1-82", "P2-5", "P2-6", "P2-13", "P2-22", "P2-24"],
    "待核查、定性或条件风险": ["P0-8", "P1-7", "P1-10", "P1-18", "P1-19", "P1-33", "P1-41", "P1-50", "P1-55", "P1-70", "P1-71", "P2-14", "P2-15", "P2-16"],
    "代码已修，主要剩环境或部署验收": ["P0-13", "P0-23", "P1-22", "P1-23", "P1-30", "P1-47", "P1-51", "P1-52", "P1-53", "P1-58", "P1-65", "P1-69", "P1-76", "P1-79", "P2-21"],
    "其他部署或历史数据收尾": ["P0-11", "P0-17", "P0-34", "P1-31"],
}


def svg_start(width, height, title):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f'<title>{escape(title)}</title>',
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="#54657a"/></marker></defs>',
            f'<rect width="{width}" height="{height}" fill="#f5f8fc"/>',
            '<g font-family="Microsoft YaHei, sans-serif">']


def text(x, y, value, size=20, color="#21344b", bold=False):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{700 if bold else 400}">{escape(value)}</text>'


def card(parts, x, y, w, h, title, lines, color="#e8f0fb", size=19):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="white" stroke="#c3d0df" stroke-width="2"/>')
    parts.append(f'<rect x="{x+1}" y="{y+1}" width="{w-2}" height="44" rx="11" fill="{color}"/>')
    parts.append(text(x+18, y+30, title, 22, bold=True))
    for i, line in enumerate(lines):
        parts.append(text(x+18, y+76+32*i, line, size))


def architecture():
    parts = svg_start(1600, 1510, "御策天检：系统技术架构（当前源码）")
    parts += [text(40, 50, "御策天检 · 系统技术架构", 34, bold=True),
              text(40, 88, "箭头表示主要请求/数据方向；SSE 返回运行事件；Redis 不作为业务事实主库", 20)]
    card(parts, 40, 120, 960, 125, "用户入口 · Vue 3 / Pinia / Vue Router", ["工作台、项目、任务池、知识、工具、报告、设置、流程、录制窗口", "Electron 包装同一前端；HTTP 提交与查询，SSE 订阅运行事件"])
    card(parts, 1040, 120, 520, 125, "外部入口", ["兼容性 CLI runner · 渠道入口", "供应商 webhook 处理仍有缺口"], "#fff2d7")
    card(parts, 40, 300, 1520, 155, "API 与应用服务 · FastAPI / 中央鉴权 / CORS / 请求上下文", ["25 个 router 注册；211 个 REST 装饰器定义（源码计数）", "会话 / 项目 / 用例 / 套件 / 运行 / 审批 / 工具任务 / 工件 / 报告 / 配额 / 设置", "文档 / 知识 / 录制 / 兼容性 / 邮件 / OAuth / Docker / 接入目录 / 指标"])
    card(parts, 40, 515, 470, 235, "Agent 编排与模型", ["SessionService → 持久任务派发", "InputOrchestrator / Coordinator", "LangGraph / Deep Agent 适配", "权限、Prompt、上下文、工具协议", "模型注册表 → 真实模型供应商"])
    card(parts, 550, 515, 500, 235, "用例驱动与模式运行", ["草稿 → 评审 → 启用固定版本", "套件冻结 → 运行条目原子领取", "结果与证据 → 新建失败项回归", "8 种运行模式；6 种目录占位", "录制 → 审批 → 回放 → 用例草稿"])
    card(parts, 1090, 515, 470, 235, "工具与外部执行", ["ToolRuntime / ToolJob / Artifact", "MCP / Skills / 注册工具目录", "Docker、浏览器、SSH、CLI", "API / 性能 / 安全 / 兼容性 runner", "配额、资源租约、回收与心跳"])
    card(parts, 40, 815, 1520, 150, "异步派发与观测", ["PostgreSQL 任务 / attempt / Outbox → Relay → Redis Stream → Worker → 编排与工具执行", "业务终态、消息与事件持久化 → SSE → Pinia → ChatTimeline；HTTP 回执与事件关联去重", "指标 / Token / 运行轨迹 / 工件证据；压缩、Embedding、索引重建为独立任务"])
    card(parts, 40, 1030, 470, 215, "PostgreSQL · 持久业务事实", ["会话、消息、事件、审批与快照", "项目、版本、套件、运行与证据", "任务、attempt、Outbox、工具工件", "压缩记录、向量与 Embedding", "Alembic 接入部分表，非全库"])
    card(parts, 550, 1030, 500, 215, "Redis / Memgraph", ["Redis：Stream、PEL、缓存、租约", "Redis：热记忆与向量副本", "Memgraph：项目知识与录制图", "图查询容量、旧索引保留", "与死信策略仍需部署治理"], "#fff2d7")
    card(parts, 1090, 1030, 470, 215, "MySQL / RustFS / 文件", ["MySQL：模型、邮件、渠道配置", "RustFS：工件与扫描隔离桶", "本地文档目录、临时执行工作区", "兼容性 CLI 持久状态与回执", "供应商及历史部署另行验收"])
    card(parts, 40, 1310, 1520, 145, "系统边界与验收口径", ["真实模型、邮件/渠道、对象存储、Docker、MCP、SSH 等按具体执行证据分层验收", "未知副作用 claim 不直接重放；数据保留、全库迁移、完整 CI 矩阵及旧部署迁移未闭环", "本轮只新增文档、测试脚本与记录；发现问题留档，不修改产品代码"], "#fff2d7")
    for y1, y2 in [(245, 300), (455, 515), (750, 815), (965, 1030), (1245, 1310)]:
        for x in (275, 800, 1325):
            parts.append(f'<path d="M{x},{y1+4} L{x},{y2-7}" stroke="#54657a" stroke-width="2" fill="none" marker-end="url(#arrow)"/>')
    parts.append(text(65, 282, "HTTP ↓  /  SSE ↑", 17))
    return "\n".join(parts+["</g></svg>"])


def functions():
    parts = svg_start(1800, 1570, "御策天检：系统功能架构与验收编号")
    parts += [text(40, 50, "御策天检 · 系统功能架构", 34, bold=True),
              text(40, 88, "编号用于目录、用例映射与逐项记录；存在回归测试不表示整项真实环境已验收", 21),
              '<rect x="540" y="120" width="720" height="60" rx="12" fill="#d9e8fb" stroke="#90a7c4"/>',
              text(750, 158, "企业 AI 测试与质量工作台", 27, bold=True)]
    parts.append('<path d="M900,180 L900,205 L20,205 M900,205 L1200,205" stroke="#54657a" stroke-width="2" fill="none"/>')
    for rail in (20, 610, 1200):
        parts.append(f'<path d="M{rail},205 L{rail},1097" stroke="#54657a" stroke-width="2" fill="none"/>')
    for index, (prefix, name, _, leaves) in enumerate(DOMAINS):
        col, row = index % 3, index // 3
        x, y = 40+col*590, 235+row*420
        labels = [f"{prefix}{i:02d}  {leaf[0]}" for i, leaf in enumerate(leaves, 1)]
        card(parts, x, y, 550, 360, f"{prefix} · {name}", labels, "#fff2d7" if prefix == "I" else "#e8f0fb", 21)
        parts.append(f'<path d="M{x-20},{y+22} L{x-7},{y+22}" stroke="#54657a" stroke-width="2" fill="none" marker-end="url(#arrow)"/>')
    parts += [text(40, 1510, "I01：集成、单元/组件、移动、视觉回归、可访问性、可靠性模式只有目录占位", 20),
              text(40, 1542, "模块 / 真实服务 / 组件浏览器 / 完整工作台 UI 为不同层级；缺证据的层级记为未执行", 20)]
    return "\n".join(parts+["</g></svg>"])


def main():
    evidence = json.loads((OUT / "inventory.json").read_text(encoding="utf-8"))
    known_tests = set(evidence["test_files"])
    features = []
    for prefix, domain, source, leaves in DOMAINS:
        if not (ROOT / source).exists():
            raise ValueError(f"Missing source: {source}")
        for number, (name, aliases) in enumerate(leaves, 1):
            tests = [f"test_{alias}.py" for alias in aliases.split()]
            if set(tests)-known_tests:
                raise ValueError(f"Unknown tests: {set(tests)-known_tests}")
            features.append({"id": f"{prefix}{number:02d}", "domain": domain, "name": name,
                             "source": source, "backend_test_files": tests,
                             "scope": "catalog_only" if prefix == "I" and number == 1 else "declared_test_assertions_only",
                             "full_ui_status": "not_executed"})
    (OUT / "functional_catalog.json").write_text(json.dumps(features, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (OUT / "system-architecture.svg").write_text(architecture(), encoding="utf-8")
    (OUT / "functional-architecture.svg").write_text(functions(), encoding="utf-8")
    rows = {row["id"]: row for row in evidence["findings"]}
    backlog = ["# 当前剩余工作的核对记录", "", f"依据提交：{evidence['commit'][:7]}；核对日期：2026-10-02。", "",
               "以下是清单正文的工作边界分类，不是已动态复现缺陷数量。原清单状态及全文行保留在 inventory.json。", ""]
    for category, ids in GROUPS.items():
        backlog += [f"## {category}（{len(ids)} 项）", "", "|编号|当前清单状态|", "|---|---|"]
        backlog += [f"|{key}|{rows[key]['current_heading']}|" for key in ids]
        backlog.append("")
    backlog += ["P1-80 的正式类型检查、P1-83 的已复现分片性能问题已修，不再列为未实现。I01 是已有目录占位，不自动升级为缺陷。", "",
                "验收优先验证正式流程，使用真实路径与既有断言。故障注入只检验明确的恢复契约，不破坏配置或替换业务逻辑来制造产品缺陷。"]
    (OUT / "remaining-work.md").write_text("\n".join(backlog)+"\n", encoding="utf-8")
    plan = ["# 系统功能说明与验收计划", "", "[系统说明](system-guide.md) · [剩余工作](remaining-work.md) · [实际验收报告](latest-report.md)", "",
            f"源码基线：{evidence['commit'][:7]}；完整事实快照见 [inventory.json](inventory.json)。", "",
            "系统以项目、用例固定版本和运行结果为业务事实；会话 Agent 负责理解任务、调用工具、审批与协作。前端提供工作台，后端通过持久任务、Outbox 与 Redis Worker 执行，结果及事件落库后通过 SSE 展示。", "",
            "正式用例流程：生成草稿 → 评审 → 启用固定版本 → 套件冻结版本 → 运行条目原子领取 → 结果与证据入库 → 失败项创建新回归运行。完成项不可重置复用；项目关联使用正式 project_id。", "",
            "源码计数：25 个 router 注册、211 个 REST 装饰器定义、12 个前端 path 项（含两个重定向及项目详情）、14 个模式 manifest、54 个磁盘 Skill。计数不代表线上健康或可用功能数量。", "",
            "8 种运行模式：默认、代码治理、API、性能、安全、兼容性、冒烟、UI 自动化。6 种扩展模式见 I01，目前没有独立完整运行器。", "",
            "## 两张图", "", "![技术架构](system-architecture.svg)", "", "![功能架构](functional-architecture.svg)", "",
            "SVG 是可编辑源文件，使用显式颜色及纯 SVG 图形，无 foreignObject 和外部资源。PNG 为渲染版；边界和视觉结果另行检查。", "",
            "## 执行与判定", "",
            "1. diagnostic 全集保存每个测试的 setup/call/teardown、跳过原因、依赖、commit 和源码哈希；默认跳过外部服务，不能作为发布通过。",
            "2. 隔离 PostgreSQL、Redis、MCP、迁移、API、SQL 清理、浏览器与真实模型分组，只验证组内断言。",
            "3. 运行前端 Vitest 与正式 build；真实 SessionService/HTTP/SSE 和生产构建页面验证三种时区。真实模型组件探针单列层级。",
            "4. 每项记录完整 case nodeid 与原报告。组内通过不等于供应商或完整 UI 通过；未执行业务按钮明确列为覆盖缺口。",
            "5. 只新增验收资料及脚本，发现问题留档；比较产品源码指纹确保没有产品修复。", "",
            "测试设计依据：项目既有 tests、run_test_quality_gate.py，以及本地课程《评测工程化与CICD》《Agent轨迹与协议鲁棒性》的版本化证据与工具轨迹评估。使用既有版本及样本，不新增未经评审的模型生成用例。", "",
            "## 功能与回归映射", "", "|编号|功能|后端回归文件数|", "|---|---|---|"]
    plan += [f"|{f['id']}|{f['name']}|{len(f['backend_test_files'])}|" for f in features]
    plan += ["", "真实 RustFS、Memgraph 容量、邮件/渠道写入、完整业务 UI、Electron GUI 及重启/供应商故障组合仍需独立验收。"]
    (OUT / "README.md").write_text("\n".join(plan)+"\n", encoding="utf-8")
    print(json.dumps({"features": len(features), "backlog_groups": {k: len(v) for k, v in GROUPS.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
