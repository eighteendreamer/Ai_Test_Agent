"""
测试报告 API 路由

作者: 程序员Eighteen
"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from database.connection import get_db, resolve_project_context
from Build_Report.service import TestReportService

router = APIRouter(
    prefix="/api/reports",
    tags=["测试报告"]
)


class GenerateReportRequest(BaseModel):
    test_result_ids: List[int]
    format_type: str = "markdown"


class GenerateMixedReportRequest(BaseModel):
    report_ids: List[int]
    bug_report_ids: List[int] = []


class SendReportRequest(BaseModel):
    report_content: dict
    contact_ids: List[int]


@router.post("/generate")
async def generate_report(
    request: GenerateReportRequest,
    db: Session = Depends(get_db)
):
    """生成测试报告"""
    result = await TestReportService.generate_report(
        test_result_ids=request.test_result_ids,
        db=db,
        format_type=request.format_type
    )
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('message'))
    
    return result


@router.post("/generate-mixed")
async def generate_mixed_report(
    request: GenerateMixedReportRequest,
    db: Session = Depends(get_db)
):
    """综合分析多个测试报告和 Bug 报告，生成汇总报告"""
    from database.connection import TestReport, BugReport
    from llm import get_llm_client
    import json

    # 获取测试报告
    test_reports = db.query(TestReport).filter(
        TestReport.id.in_(request.report_ids)
    ).all()

    if not test_reports:
        raise HTTPException(status_code=404, detail="未找到指定的测试报告")

    # 收集测试报告数据
    reports_data = []
    total_pass = 0
    total_fail = 0
    total_cases = 0
    total_duration = 0
    for report in test_reports:
        summary = report.summary or {}
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except Exception:
                summary = {}
        total_pass += summary.get('pass', 0)
        total_fail += summary.get('fail', 0)
        total_cases += summary.get('total', 0)
        total_duration += summary.get('duration', 0)
        reports_data.append({
            "id": report.id,
            "title": report.title,
            "summary": summary,
            "details": report.details,
            "created_at": report.created_at.isoformat() if report.created_at else None
        })

    # 获取 Bug 报告（如果有）
    bugs_data = []
    if request.bug_report_ids:
        bug_reports = db.query(BugReport).filter(
            BugReport.id.in_(request.bug_report_ids)
        ).all()
        for bug in bug_reports:
            bugs_data.append({
                "id": bug.id,
                "bug_name": bug.bug_name,
                "error_type": bug.error_type,
                "severity_level": bug.severity_level,
                "reproduce_steps": bug.reproduce_steps,
                "expected_result": bug.expected_result,
                "actual_result": bug.actual_result,
                "result_feedback": bug.result_feedback
            })

    # 用 LLM 生成综合分析报告
    llm_client = get_llm_client()

    reports_json = json.dumps(reports_data, ensure_ascii=False, indent=2)
    bugs_json = json.dumps(bugs_data, ensure_ascii=False, indent=2) if bugs_data else "无"

    system_prompt = """你是一个专业的测试报告分析专家。
请根据提供的多个测试报告和 Bug 报告，生成一份综合分析报告。
报告应包含：整体测试概况、通过率分析、主要问题汇总、风险评估和改进建议。
请使用 Markdown 格式输出。"""

    user_prompt = f"""请综合分析以下测试数据并生成汇总报告：

测试统计：
- 总用例数: {total_cases}
- 通过数: {total_pass}
- 失败数: {total_fail}
- 通过率: {round(total_pass / total_cases * 100, 2) if total_cases > 0 else 0}%

测试报告数据:
{reports_json}

Bug 报告数据:
{bugs_json}

请生成一份专业的综合测试分析报告。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        response = await llm_client.achat(messages=messages, temperature=0.3)
        return {
            "success": True,
            "data": {
                "content": response,
                "summary": {
                    "total": total_cases,
                    "pass": total_pass,
                    "fail": total_fail,
                    "pass_rate": round(total_pass / total_cases * 100, 2) if total_cases > 0 else 0,
                    "duration": total_duration,
                    "report_count": len(reports_data),
                    "bug_count": len(bugs_data)
                }
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成报告失败: {str(e)}")


@router.post("/send-report")
async def send_report_to_contacts(
    request: SendReportRequest,
    db: Session = Depends(get_db)
):
    """将报告发送给指定联系人"""
    from database.connection import Contact, EmailConfig, EmailRecord
    import json
    from datetime import datetime

    # 获取联系人
    contacts = db.query(Contact).filter(Contact.id.in_(request.contact_ids)).all()
    if not contacts:
        raise HTTPException(status_code=404, detail="未找到指定联系人")

    # 获取激活的邮件配置
    email_config = db.query(EmailConfig).filter(EmailConfig.is_active == 1).first()
    if not email_config:
        raise HTTPException(status_code=400, detail="未配置邮件服务，请先在邮件管理中配置并激活")

    # 构建邮件内容
    report = request.report_content
    subject = f"综合测试评估报告 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    conclusion_md = report.get('conclusion', '')
    summary_text = report.get('summary', '')
    quality = report.get('quality_rating', '-')
    pass_rate = report.get('pass_rate', '-')
    bug_count = report.get('bug_count', 0)
    duration = report.get('duration', '-')

    # 将 markdown 结论转为 HTML
    import markdown as md_lib
    conclusion_html = md_lib.markdown(
        conclusion_md,
        extensions=['tables', 'fenced_code', 'nl2br']
    ) if conclusion_md else '<p>暂无分析结论</p>'

    html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:'Helvetica Neue',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
<div style="max-width:680px;margin:20px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
  <!-- 头部 -->
  <div style="background:linear-gradient(135deg,#007857,#00a67e);padding:32px 40px;color:#fff;">
    <h1 style="margin:0;font-size:22px;font-weight:600;">综合测试评估报告</h1>
    <p style="margin:8px 0 0;font-size:13px;opacity:0.85;">{datetime.now().strftime('%Y年%m月%d日 %H:%M')}</p>
  </div>
  <!-- 概要卡片 -->
  <div style="padding:24px 40px 0;">
    <p style="color:#666;font-size:14px;line-height:1.8;margin:0 0 16px;">{summary_text}</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
      <tr>
        <td style="width:25%;text-align:center;padding:16px 8px;background:#f0fdf4;border-radius:8px;">
          <div style="font-size:24px;font-weight:700;color:#007857;">{quality}</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">质量评级</div>
        </td>
        <td style="width:8px;"></td>
        <td style="width:25%;text-align:center;padding:16px 8px;background:#f0fdf4;border-radius:8px;">
          <div style="font-size:24px;font-weight:700;color:#16a34a;">{pass_rate}%</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">通过率</div>
        </td>
        <td style="width:8px;"></td>
        <td style="width:25%;text-align:center;padding:16px 8px;background:#fef2f2;border-radius:8px;">
          <div style="font-size:24px;font-weight:700;color:#dc2626;">{bug_count}</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">Bug 数量</div>
        </td>
        <td style="width:8px;"></td>
        <td style="width:25%;text-align:center;padding:16px 8px;background:#eff6ff;border-radius:8px;">
          <div style="font-size:24px;font-weight:700;color:#2563eb;">{duration}</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">执行时长</div>
        </td>
      </tr>
    </table>
  </div>
  <!-- 分割线 -->
  <div style="padding:0 40px;"><hr style="border:none;border-top:1px solid #e5e7eb;margin:0;"></div>
  <!-- AI 分析结论 -->
  <div style="padding:24px 40px 32px;">
    <h2 style="font-size:16px;font-weight:600;color:#1e293b;margin:0 0 16px;">AI 分析结论</h2>
    <div style="color:#334155;font-size:14px;line-height:1.9;">
      <style>
        table {{border-collapse:collapse;width:100%;margin:12px 0;}}
        th,td {{border:1px solid #e2e8f0;padding:8px 12px;text-align:left;font-size:13px;}}
        th {{background:#f8fafc;font-weight:600;}}
        h1,h2,h3 {{color:#1e293b;}}
        h1 {{font-size:18px;margin:20px 0 10px;}}
        h2 {{font-size:16px;margin:18px 0 8px;}}
        h3 {{font-size:14px;margin:14px 0 6px;}}
        ul,ol {{padding-left:20px;}}
        li {{margin:4px 0;}}
        hr {{border:none;border-top:1px solid #e5e7eb;margin:16px 0;}}
        code {{background:#f1f5f9;padding:2px 6px;border-radius:3px;font-size:13px;}}
        blockquote {{border-left:3px solid #007857;padding-left:12px;margin:8px 0;color:#64748b;}}
      </style>
      {conclusion_html}
    </div>
  </div>
  <!-- 底部 -->
  <div style="background:#f8fafc;padding:16px 40px;text-align:center;font-size:12px;color:#94a3b8;">
    此邮件由 AI 测试平台自动生成发送
  </div>
</div>
</body></html>"""

    from Email_manage.sender import dispatch_send

    recipients = [{"id": c.id, "name": c.name, "email": c.email} for c in contacts]
    success_count = 0
    failed_count = 0
    failed_details = []
    recipients_result = []

    for contact in contacts:
        to_email = email_config.test_email if email_config.test_mode == 1 and email_config.test_email else contact.email
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, dispatch_send, email_config, to_email, subject, html_content)
            success_count += 1
            recipients_result.append({"name": contact.name, "email": contact.email, "status": "success"})
        except Exception as e:
            failed_count += 1
            failed_details.append({"name": contact.name, "email": contact.email, "error": str(e)})
            recipients_result.append({"name": contact.name, "email": contact.email, "status": "failed", "error": str(e)})

    # 记录发送历史
    status = 'success' if failed_count == 0 else ('partial' if success_count > 0 else 'failed')
    record = EmailRecord(
        subject=subject,
        recipients=recipients_result,
        status=status,
        success_count=success_count,
        failed_count=failed_count,
        total_count=len(contacts),
        email_type='report',
        content_summary=summary_text[:200] if summary_text else None
    )
    db.add(record)
    db.commit()

    return {
        "code": 200,
        "message": f"发送完成：成功 {success_count}，失败 {failed_count}",
        "data": {
            "success_count": success_count,
            "failed_count": failed_count,
            "failed_details": failed_details
        }
    }


# 各服务商发送实现已统一迁移至 Email_manage/sender.py
# 新增服务商请在 Email_manage/sender.py 的 _PROVIDER_MAP 中注册


@router.get("/list")
def get_reports(
    limit: int = 20,
    offset: int = 0,
    project_id: int = None,
    db: Session = Depends(get_db)
):
    """获取报告列表"""
    project = resolve_project_context(db, project_id)

    if not project:
        return {
            "success": True,
            "data": [],
            "total": 0
        }

    result = TestReportService.get_reports(db=db, limit=limit, offset=offset, project_id=project.id)
    return {
        "success": True,
        "data": result['data'],
        "total": result['total']
    }


@router.get("/{report_id}")
def get_report(
    report_id: int,
    db: Session = Depends(get_db)
):
    """获取单个报告详情"""
    from database.connection import TestReport, get_active_project_by_id
    
    report = db.query(TestReport).filter(TestReport.id == report_id).first()
    
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    # 验证报告所属项目是否启用
    if report.project_id:
        project = get_active_project_by_id(db, report.project_id)
        if not project:
            raise HTTPException(status_code=400, detail="报告所属项目未启用")
    
    return {
        "success": True,
        "data": {
            "id": report.id,
            "title": report.title,
            "summary": report.summary,
            "details": report.details,
            "format_type": report.format_type,
            "total_steps": report.total_steps,
            "created_at": report.created_at.isoformat() if report.created_at else None
        }
    }
