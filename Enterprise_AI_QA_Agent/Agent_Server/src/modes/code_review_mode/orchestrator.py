from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.modes.code_review_mode.models import DebateRound, ProjectSource, ReviewPoint, ReviewReportSkeleton
from src.modes.code_review_mode.project_source import normalize_project_source, project_source_root
from src.modes.code_review_mode.skills import CODE_REVIEW_REVIEWER_SKILLS
from src.modes.code_review_mode.team import REVIEW_TEAM_MEMBERS, SUMMARY_AGENT

if TYPE_CHECKING:
    from src.application.runtime.tool_runtime_service import ToolExecutionContext


MIN_CROSS_REVIEW_ROUNDS = 1
MAX_CROSS_REVIEW_ROUNDS = 4


def build_code_review_campaign(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> dict[str, Any]:
    project_source = normalize_project_source(arguments)
    review_points = _build_review_points(project_source, arguments)
    cross_review_round_count = _resolve_cross_review_round_count(arguments)
    debate_rounds = _build_debate_rounds(cross_review_round_count)
    total_round_count = len(debate_rounds)

    report = ReviewReportSkeleton(
        report_title=f"《{project_source.project_name or 'Unnamed Project'}》的辩论报告",
        project_name=project_source.project_name or "Unnamed Project",
        approval_time=datetime.utcnow(),
    )

    independent_workers = [
        _build_independent_worker_spec(
            member=member,
            project_source=project_source,
            review_points=review_points,
            arguments=arguments,
            context=context,
            debate_rounds=debate_rounds,
            total_round_count=total_round_count,
        )
        for member in REVIEW_TEAM_MEMBERS
    ]

    followup_workers: list[dict[str, Any]] = []
    for cross_round_number in range(1, cross_review_round_count + 1):
        debate_round_index = cross_round_number + 1
        source_round_index = debate_round_index - 1
        for member in REVIEW_TEAM_MEMBERS:
            followup_workers.append(
                _build_cross_review_worker_spec(
                    member=member,
                    project_source=project_source,
                    review_points=review_points,
                    arguments=arguments,
                    context=context,
                    debate_rounds=debate_rounds,
                    total_round_count=total_round_count,
                    debate_round_index=debate_round_index,
                    source_round_index=source_round_index,
                )
            )

    summary_agent_spec = _build_summary_agent_spec(
        project_source=project_source,
        review_points=review_points,
        arguments=arguments,
        context=context,
        debate_rounds=debate_rounds,
        total_round_count=total_round_count,
        cross_review_round_count=cross_review_round_count,
    )

    campaign_id = str(uuid4())
    review_scope = str(arguments.get("review_scope") or "project").strip() or "project"
    launch_workers = bool(arguments.get("launch_workers", True))
    return {
        "campaign_id": campaign_id,
        "project_source": project_source.model_dump(mode="python"),
        "review_scope": review_scope,
        "review_points": [point.model_dump(mode="python") for point in review_points],
        "review_team": [member.model_dump(mode="python") for member in REVIEW_TEAM_MEMBERS],
        "summary_agent": summary_agent_spec,
        "debate_rounds": [round_.model_dump(mode="python") for round_ in debate_rounds],
        "report_skeleton": report.model_dump(mode="python"),
        "dispatch_payload": {
            "workers": independent_workers,
            "followup_workers": followup_workers,
            "completion_worker": summary_agent_spec,
        },
        "launch_workers": launch_workers,
        "metrics": {
            "reviewer_count": len(REVIEW_TEAM_MEMBERS),
            "debate_round_count": total_round_count,
            "cross_review_round_count": cross_review_round_count,
            "review_point_count": len(review_points),
        },
        "summary": (
            f"已为“{project_source.project_name}”准备代码审批辩论任务，"
            f"包含 {len(REVIEW_TEAM_MEMBERS)} 位评审 Agent、{len(review_points)} 个审查点，"
            f"以及 {cross_review_round_count} 轮交叉攻防。"
        ),
    }


def _resolve_cross_review_round_count(arguments: dict[str, Any]) -> int:
    raw_value = arguments.get("cross_review_rounds")
    if raw_value is None:
        raw_value = arguments.get("debate_round_count")
    try:
        resolved = int(raw_value)
    except (TypeError, ValueError):
        resolved = 2
    return max(MIN_CROSS_REVIEW_ROUNDS, min(MAX_CROSS_REVIEW_ROUNDS, resolved))


def _build_debate_rounds(cross_review_round_count: int) -> list[DebateRound]:
    rounds = [
        DebateRound(
            round_id="independent_findings",
            name="独立审查",
            objective="每位评审官独立审查项目，在不读取同伴输出的前提下提出初始结论。",
        )
    ]
    for cross_round_number in range(1, cross_review_round_count + 1):
        rounds.append(
            DebateRound(
                round_id=f"cross_review_{cross_round_number}",
                name=f"第 {cross_round_number} 轮交叉攻防",
                objective=(
                    "评审官使用更强证据、严重度校准和隐患分析，对前一轮结论进行支持、质疑或修正。"
                ),
            )
        )
    rounds.append(
        DebateRound(
            round_id="summary_resolution",
            name="总结裁决",
            objective="总结审查官汇总辩论结论，形成项目最终结构化裁决。",
        )
    )
    return rounds


def _build_review_points(project_source: ProjectSource, arguments: dict[str, Any]) -> list[ReviewPoint]:
    targets = arguments.get("targets")
    normalized_targets = (
        [str(item).strip() for item in targets if str(item).strip()]
        if isinstance(targets, list)
        else []
    )
    review_points: list[ReviewPoint] = []
    if normalized_targets:
        for target in normalized_targets:
            review_points.append(
                ReviewPoint(
                    point_id=str(uuid4()),
                    title=f"Review target: {target}",
                    kind="path",
                    target=target,
                    summary=(
                        "围绕该目标检查正确性、架构、安全、可测性、维护成本以及可执行改进建议。"
                    ),
                )
            )
    else:
        scope_target = project_source_root(project_source)
        review_points.append(
            ReviewPoint(
                point_id=str(uuid4()),
                title=f"Project-wide review: {project_source.project_name}",
                kind="project",
                target=scope_target,
                summary=(
                    "审查整个项目范围，围绕关键结论展开辩论，并识别严重问题、缺陷、隐患、可行改进和优秀实践。"
                ),
            )
        )
    return review_points


def _build_independent_worker_spec(
    member,
    project_source: ProjectSource,
    review_points: list[ReviewPoint],
    arguments: dict[str, Any],
    context: ToolExecutionContext,
    debate_rounds: list[DebateRound],
    total_round_count: int,
) -> dict[str, Any]:
    reviewer_prompt = _build_independent_reviewer_prompt(
        member_name=member.name,
        focus=member.focus,
        responsibilities=member.responsibilities,
        project_source=project_source,
        review_points=review_points,
        arguments=arguments,
    )
    return {
        "description": f"{member.name} 对 {project_source.project_name} 的独立审查",
        "prompt": reviewer_prompt,
        "agent_key": member.agent_key,
        "model_key": str(arguments.get("worker_model_key") or "").strip() or None,
        "skill_keys": list(CODE_REVIEW_REVIEWER_SKILLS.get(member.key, [])),
        "context": _build_worker_context(
            member=member,
            project_source=project_source,
            review_points=review_points,
            context=context,
            debate_rounds=debate_rounds,
            debate_stage="independent_findings",
            debate_round_index=1,
            total_round_count=total_round_count,
            empty_context_policy="Do not assume any peer output exists during the first pass.",
        ),
    }


def _build_cross_review_worker_spec(
    member,
    project_source: ProjectSource,
    review_points: list[ReviewPoint],
    arguments: dict[str, Any],
    context: ToolExecutionContext,
    debate_rounds: list[DebateRound],
    total_round_count: int,
    debate_round_index: int,
    source_round_index: int,
) -> dict[str, Any]:
    reviewer_prompt = _build_cross_review_prompt(
        member_name=member.name,
        focus=member.focus,
        responsibilities=member.responsibilities,
        project_source=project_source,
        review_points=review_points,
        arguments=arguments,
        debate_round_index=debate_round_index,
        source_round_index=source_round_index,
    )
    return {
        "description": f"{member.name} 对 {project_source.project_name} 的第 {debate_round_index - 1} 轮交叉攻防",
        "prompt": reviewer_prompt,
        "agent_key": member.agent_key,
        "model_key": str(arguments.get("worker_model_key") or "").strip() or None,
        "skill_keys": list(CODE_REVIEW_REVIEWER_SKILLS.get(member.key, [])),
        "context": _build_worker_context(
            member=member,
            project_source=project_source,
            review_points=review_points,
            context=context,
            debate_rounds=debate_rounds,
            debate_stage="cross_review",
            debate_round_index=debate_round_index,
            total_round_count=total_round_count,
            empty_context_policy="You must debate peer findings instead of restarting project discovery.",
            source_stage="independent_findings" if source_round_index == 1 else "cross_review",
            source_round_index=source_round_index,
        ),
    }


def _build_worker_context(
    *,
    member,
    project_source: ProjectSource,
    review_points: list[ReviewPoint],
    context: ToolExecutionContext,
    debate_rounds: list[DebateRound],
    debate_stage: str,
    debate_round_index: int,
    total_round_count: int,
    empty_context_policy: str,
    source_stage: str = "",
    source_round_index: int = 0,
) -> dict[str, Any]:
    dispatch_role = "debate_followup" if debate_stage == "cross_review" else "worker"
    return {
        "campaign_kind": "code_review_debate",
        "campaign_project_name": project_source.project_name,
        "campaign_source": project_source.model_dump(mode="python"),
        "review_points": [point.model_dump(mode="python") for point in review_points],
        "review_focus": member.focus,
        "review_responsibilities": list(member.responsibilities),
        "debate_role": member.key,
        "debate_stage": debate_stage,
        "debate_round_index": debate_round_index,
        "debate_total_round_count": total_round_count,
        "dispatch_role": dispatch_role,
        "debate_rounds": [round_.model_dump(mode="python") for round_ in debate_rounds],
        "empty_context_policy": empty_context_policy,
        "source_stage": source_stage,
        "source_round_index": source_round_index,
        "result_contract": {
            "required_fields": [
                "finding_id",
                "point_id",
                "result_category",
                "summary",
                "risk_analysis",
                "recommended_actions",
                "evidence_refs",
                "confidence",
            ]
        },
        "parent_execution_context": {
            "session_id": context.session_id,
            "turn_id": context.turn_id,
            "trace_id": context.trace_id,
        },
    }


def _build_summary_agent_spec(
    project_source: ProjectSource,
    review_points: list[ReviewPoint],
    arguments: dict[str, Any],
    context: ToolExecutionContext,
    debate_rounds: list[DebateRound],
    total_round_count: int,
    cross_review_round_count: int,
) -> dict[str, Any]:
    email_recipients = [
        str(item).strip()
        for item in (arguments.get("email_to") if isinstance(arguments.get("email_to"), list) else [])
        if str(item).strip()
    ]
    delivery_channel = str(arguments.get("delivery_channel") or "artifact").strip().lower() or "artifact"
    email_subject = (
        str(arguments.get("email_subject") or "").strip()
        or f"{project_source.project_name} 代码审批辩论报告"
    )
    return {
        "description": f"{SUMMARY_AGENT.name} 对 {project_source.project_name} 的最终辩论总结",
        "prompt": _build_summary_prompt(
            project_source,
            review_points,
            cross_review_round_count=cross_review_round_count,
            email_recipients=email_recipients,
            delivery_channel=delivery_channel,
            email_subject=email_subject,
        ),
        "agent_key": SUMMARY_AGENT.agent_key,
        "model_key": str(arguments.get("summary_model_key") or arguments.get("worker_model_key") or "").strip() or None,
        "skill_keys": [],
        "context": {
            "campaign_kind": "code_review_debate_summary",
            "campaign_project_name": project_source.project_name,
            "campaign_source": project_source.model_dump(mode="python"),
            "review_points": [point.model_dump(mode="python") for point in review_points],
            "debate_rounds": [round_.model_dump(mode="python") for round_ in debate_rounds],
            "debate_round_index": total_round_count,
            "debate_total_round_count": total_round_count,
            "parent_execution_context": {
                "session_id": context.session_id,
                "turn_id": context.turn_id,
                "trace_id": context.trace_id,
            },
            "delivery_preferences": {
                "channel": delivery_channel,
                "email_to": email_recipients,
                "email_subject": email_subject,
            },
        },
        "trigger": "after_cross_review_collection",
    }


def _build_independent_reviewer_prompt(
    *,
    member_name: str,
    focus: str,
    responsibilities: list[str],
    project_source: ProjectSource,
    review_points: list[ReviewPoint],
    arguments: dict[str, Any],
) -> str:
    responsibilities_block = "\n".join(f"- {item}" for item in responsibilities)
    review_points_block = "\n".join(
        f"- [{point.point_id}] {point.title} | target={point.target} | summary={point.summary}"
        for point in review_points
    )
    change_summary = str(arguments.get("change_summary") or "").strip()
    diff_text = str(arguments.get("diff_text") or "").strip()
    diff_block = (
        diff_text if diff_text else "（未提供内联 diff 片段。请通过已注册工具读取项目文件和 Git 状态。）"
    )
    return (
        f"你是代码审批辩论团队中的{member_name}。\n"
        f"核心关注点：{focus}\n"
        "当前处于第 1 轮：独立审查。\n"
        "所有输出必须使用简体中文，禁止输出英文正文；如需引用代码标识、文件路径、接口名或严重级别枚举，可保留原始英文标识。\n\n"
        "你的职责：\n"
        f"{responsibilities_block}\n\n"
        "项目来源：\n"
        f"- source_type: {project_source.source_type}\n"
        f"- project_name: {project_source.project_name}\n"
        f"- root_path: {project_source_root(project_source)}\n"
        f"- branch: {project_source.branch or '（未指定）'}\n"
        f"- commit_range: {project_source.commit_range or '（未指定）'}\n\n"
        "推荐执行顺序：\n"
        "- 先使用 bootstrap 摘要，不要一开始就大范围读仓。\n"
        "- 只读取能够证明你结论所必需的最少文件。\n"
        "- 这一轮不要等待其他评审 agent，也不要引用同伴结论。\n\n"
        "审查点：\n"
        f"{review_points_block}\n\n"
        "变更摘要：\n"
        f"{change_summary or '（未提供变更摘要。）'}\n\n"
        "内联 diff 片段：\n"
        f"{diff_block}\n\n"
        "输出要求：\n"
        "- 请快速完成你负责范围内的独立结论输出。\n"
        "- 每条结论必须且只能标记一个结果类别：serious_issue、defect、risk、feasible、excellent。\n"
        "- 每条结论都要说明影响范围、上下游隐患、可执行缓解措施、可行性判断以及证据引用。\n"
        "- 输出结构必须清晰，便于后续其他 agent 发起攻防或支持。\n"
    )


def _build_cross_review_prompt(
    *,
    member_name: str,
    focus: str,
    responsibilities: list[str],
    project_source: ProjectSource,
    review_points: list[ReviewPoint],
    arguments: dict[str, Any],
    debate_round_index: int,
    source_round_index: int,
) -> str:
    responsibilities_block = "\n".join(f"- {item}" for item in responsibilities)
    review_points_block = "\n".join(
        f"- [{point.point_id}] {point.title} | target={point.target} | summary={point.summary}"
        for point in review_points
    )
    change_summary = str(arguments.get("change_summary") or "").strip()
    return (
        f"你是代码审批辩论团队中的{member_name}。\n"
        f"核心关注点：{focus}\n"
        f"当前处于第 {debate_round_index} 轮：交叉攻防。\n"
        f"你必须基于第 {source_round_index} 轮的输出继续攻防，不能脱离上轮材料另起炉灶。\n"
        "所有输出必须使用简体中文，禁止输出英文正文；如需引用代码标识、文件路径、接口名、finding id 或严重级别枚举，可保留原始英文标识。\n\n"
        "你的职责：\n"
        f"{responsibilities_block}\n\n"
        "项目来源：\n"
        f"- project_name: {project_source.project_name}\n"
        f"- root_path: {project_source_root(project_source)}\n\n"
        "审查点：\n"
        f"{review_points_block}\n\n"
        "变更摘要：\n"
        f"{change_summary or '（未提供变更摘要。）'}\n\n"
        "攻防要求：\n"
        "- 你会在上下文里收到上一轮同伴结论的 bundle，请优先围绕这些结论展开攻防。\n"
        "- 不要重新大范围探索整个仓库。\n"
        "- 对每条同伴结论都要判断是支持、质疑还是修正。\n"
        "- 重点攻击证据薄弱、影响分析缺失或严重度夸大的结论。\n"
        "- 对质量高的结论，要补充更深的隐患分析和更好的缓解方案。\n"
        "- 如果你不同意，必须明确说明理由，以及什么证据会改变你的判断。\n"
        "- 随着轮次推进，应逐步收敛到证据最强的立场，而不是重复第 1 轮内容。\n\n"
        "输出要求：\n"
        "- 尽量围绕 peer finding id 输出结构化攻防笔记。\n"
        "- 必须包含 support / challenge / refine 立场、证据以及建议的严重度调整。\n"
        "- 优先给出精确反驳，不要机械重复你在第 1 轮的原始审查结论。\n"
    )

def _build_summary_prompt(
    project_source: ProjectSource,
    review_points: list[ReviewPoint],
    *,
    cross_review_round_count: int,
    email_recipients: list[str],
    delivery_channel: str,
    email_subject: str,
) -> str:
    review_points_block = "\n".join(
        f"- [{point.point_id}] {point.title} | target={point.target}"
        for point in review_points
    )
    email_block = (
        "- 最终交付：仅保留本地 artifact 报告。\n"
        "- 使用 report-writer，并指定 template_key=code_review_debate。\n"
        "- report-writer 会自动落库 Markdown 和 HTML artifact。\n"
        if delivery_channel != "email" or not email_recipients
        else (
            "- 最终交付：先调用 report-writer，并指定 template_key=code_review_debate，保存 Markdown 和 HTML artifact。\n"
            "- 然后通过 skill 加载 mail-capability，调用 mail-send 使用全局当前邮箱准备同一份报告邮件。\n"
            "- 展示确认摘要并停止本轮；用户明确确认后，下一轮调用 mail-confirm。\n"
            f"- 邮件收件人：{', '.join(email_recipients)}\n"
            f"- 邮件主题：{email_subject}\n"
        )
    )
    return (
        f"你是项目“{project_source.project_name}”的{SUMMARY_AGENT.name}。\n"
        f"请汇总所有独立审查结论，以及全部 {cross_review_round_count} 轮交叉攻防内容，生成最终辩论报告。\n"
        "所有输出必须使用简体中文，禁止输出英文正文；如需引用代码标识、文件路径、接口名、finding id 或严重级别枚举，可保留原始英文标识。\n\n"
        "报告正文必须包含：\n"
        f"- 报告标题：《{project_source.project_name}》的辩论报告\n"
        "- 审批时间\n"
        "- 审批结果总览，分为：serious_issue、defect、risk、feasible、excellent\n"
        "- 参与 agent 数量\n"
        "- 每条结论最早由哪个 agent 提出\n"
        "- agent 之间的支持 / 反驳关系\n"
        "- 建议措施和证据引用\n"
        "- 必须使用 report-writer，并指定 template_key=code_review_debate，生成最终 Markdown 和 HTML 报告 artifact。\n"
        "- report-writer 成功后，只输出简短完成说明并停止，不要追问用户。\n\n"
        "交付偏好：\n"
        f"{email_block}\n"
        "审查点：\n"
        f"{review_points_block}\n"
    )
