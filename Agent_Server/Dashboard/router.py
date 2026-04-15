"""
数据仪表盘路由模块
提供系统统计数据的API接口

作者: 程序员Eighteen
"""
import calendar
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from database.connection import (
    get_db, 
    ExecutionCase, 
    TestRecord, 
    BugReport,
    TestReport,
    EmailRecord,
    LLMModel,
    SecurityScanTask
)

router = APIRouter(
    prefix="/api/dashboard",
    tags=["数据仪表盘"]
)


def _subtract_months(date: datetime, months: int) -> datetime:
    """日期减去指定月数"""
    year = date.year
    month = date.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = date.day
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        day = last_day
    return date.replace(year=year, month=month, day=day)


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """获取总体统计数据"""
    try:
        test_cases = db.query(func.count(ExecutionCase.id)).scalar() or 0
        test_reports = db.query(func.count(TestReport.id)).scalar() or 0
        bug_reports = db.query(func.count(BugReport.id)).scalar() or 0
        emails_sent = db.query(func.count(EmailRecord.id)).scalar() or 0
        
        return {
            "success": True,
            "data": {
                "testCases": test_cases,
                "testReports": test_reports,
                "bugReports": bug_reports,
                "emailsSent": emails_sent
            }
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.get("/test-result-stats")
def get_test_result_stats(db: Session = Depends(get_db)):
    """获取测试结果分布统计"""
    try:
        reports = db.query(TestReport).all()
        
        passed = 0
        failed = 0
        pending = 0
        
        for report in reports:
            if report.status == 'completed':
                if report.result and 'passed' in str(report.result).lower():
                    passed += 1
                else:
                    failed += 1
            elif report.status == 'pending':
                pending += 1
            elif report.status == 'failed':
                failed += 1
            else:
                passed += 1
        
        if passed == 0 and failed == 0 and pending == 0:
            total_cases = db.query(func.count(ExecutionCase.id)).scalar() or 0
            if total_cases > 0:
                pending = total_cases
            else:
                pending = 10
                passed = 5
                failed = 2
        
        return {
            "success": True,
            "data": {
                "passed": passed,
                "failed": failed,
                "pending": pending
            }
        }
    except Exception as e:
        return {
            "success": True,
            "data": {"passed": 5, "failed": 2, "pending": 10}
        }


@router.get("/priority-stats")
def get_priority_stats(db: Session = Depends(get_db)):
    """获取用例优先级分布统计"""
    try:
        result = db.query(
            ExecutionCase.priority,
            func.count(ExecutionCase.id).label('count')
        ).group_by(ExecutionCase.priority).all()
        
        stats = {'1': 0, '2': 0, '3': 0, '4': 0}
        for row in result:
            priority_str = str(row.priority) if row.priority else '4'
            if priority_str in stats:
                stats[priority_str] = row.count
        
        return {
            "success": True,
            "data": stats
        }
    except Exception as e:
        return {"success": False, "message": str(e), "data": {'1': 0, '2': 0, '3': 0, '4': 0}}


@router.get("/test-trend")
def get_test_trend(days: int = 30, db: Session = Depends(get_db)):
    """获取测试趋势"""
    try:
        if days not in [30, 90, 365]:
            days = 30
        
        end_date = datetime.now().replace(hour=23, minute=59, second=59)
        if days == 365:
            start_date = (end_date - timedelta(days=364)).replace(hour=0, minute=0, second=0)
        elif days == 90:
            start_date = _subtract_months(end_date, 3).replace(hour=0, minute=0, second=0)
        else:
            start_date = (end_date - timedelta(days=29)).replace(hour=0, minute=0, second=0)
        
        dates = []
        tests = []
        bugs = []
        cases = []
        
        if days == 365:
            interval_days = 7
            date_format = '%m-%d'
        elif days == 90:
            interval_days = 3
            date_format = '%m-%d'
        else:
            interval_days = 1
            date_format = '%m-%d'
        
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime(date_format)
            dates.append(date_str)
            
            period_start = current_date
            period_end = min(current_date + timedelta(days=interval_days), end_date + timedelta(seconds=1))
            
            test_count = db.query(func.count(TestReport.id)).filter(
                TestReport.created_at >= period_start,
                TestReport.created_at < period_end
            ).scalar() or 0
            tests.append(test_count)
            
            bug_count = db.query(func.count(BugReport.id)).filter(
                BugReport.created_at >= period_start,
                BugReport.created_at < period_end
            ).scalar() or 0
            bugs.append(bug_count)
            
            case_count = db.query(func.count(ExecutionCase.id)).filter(
                ExecutionCase.created_at >= period_start,
                ExecutionCase.created_at < period_end
            ).scalar() or 0
            cases.append(case_count)
            
            current_date += timedelta(days=interval_days)
        
        if dates:
            dates[-1] = end_date.strftime(date_format)
        
        return {
            "success": True,
            "data": {
                "dates": dates,
                "tests": tests,
                "bugs": bugs,
                "cases": cases
            }
        }
    except Exception as e:
        return {
            "success": True,
            "data": {"dates": [], "tests": [], "bugs": [], "cases": []}
        }


@router.get("/case-type-stats")
def get_case_type_stats(db: Session = Depends(get_db)):
    """获取测试用例类型分布统计"""
    try:
        result = db.query(
            ExecutionCase.case_type,
            func.count(ExecutionCase.id).label('count')
        ).group_by(ExecutionCase.case_type).all()
        
        data = [{"case_type": row.case_type or "未分类", "count": row.count} for row in result]
        
        return {
            "success": True,
            "data": data
        }
    except Exception as e:
        return {"success": False, "message": str(e), "data": []}


@router.get("/email-stats")
def get_email_stats(db: Session = Depends(get_db)):
    """获取邮件发送统计"""
    try:
        success = db.query(func.count(EmailRecord.id)).filter(
            EmailRecord.status == 'success'
        ).scalar() or 0
        
        failed = db.query(func.count(EmailRecord.id)).filter(
            EmailRecord.status == 'failed'
        ).scalar() or 0
        
        partial = db.query(func.count(EmailRecord.id)).filter(
            EmailRecord.status == 'partial'
        ).scalar() or 0
        
        return {
            "success": True,
            "data": {
                "success": success,
                "failed": failed,
                "partial": partial
            }
        }
    except Exception as e:
        return {"success": False, "message": str(e), "data": {"success": 0, "failed": 0, "partial": 0}}


@router.get("/recent-activities")
def get_recent_activities(db: Session = Depends(get_db)):
    """获取最近活动"""
    try:
        activities = []
        
        recent_reports = db.query(TestReport).order_by(
            TestReport.created_at.desc()
        ).limit(3).all()
        
        for report in recent_reports:
            activities.append({
                "type": "info",
                "title": "测试执行",
                "content": f"执行了测试任务",
                "time": report.created_at.strftime('%Y-%m-%d %H:%M') if report.created_at else ""
            })
        
        recent_bugs = db.query(BugReport).order_by(
            BugReport.created_at.desc()
        ).limit(3).all()
        
        for bug in recent_bugs:
            activities.append({
                "type": "error",
                "title": "Bug发现",
                "content": bug.title or "发现新Bug",
                "time": bug.created_at.strftime('%Y-%m-%d %H:%M') if bug.created_at else ""
            })
        
        recent_cases = db.query(ExecutionCase).order_by(
            ExecutionCase.created_at.desc()
        ).limit(3).all()
        
        for case in recent_cases:
            activities.append({
                "type": "success",
                "title": "用例新增",
                "content": case.title or "新增测试用例",
                "time": case.created_at.strftime('%Y-%m-%d %H:%M') if case.created_at else ""
            })
        
        activities.sort(key=lambda x: x['time'], reverse=True)
        
        return {
            "success": True,
            "data": activities[:10]
        }
    except Exception as e:
        return {"success": False, "message": str(e), "data": []}


# ========== 扩展接口 ==========

@router.get("/overview")
def get_overview(db: Session = Depends(get_db)):
    """获取概览数据"""
    total_cases = db.query(ExecutionCase).count()
    total_records = db.query(TestRecord).count()
    passed_records = db.query(TestRecord).filter(TestRecord.status == 'pass').count()
    failed_records = db.query(TestRecord).filter(TestRecord.status == 'fail').count()
    total_bugs = db.query(BugReport).count()
    pending_bugs = db.query(BugReport).filter(BugReport.status == '待处理').count()
    total_reports = db.query(TestReport).count()
    active_model = db.query(LLMModel).filter(LLMModel.is_active == 1).first()
    pass_rate = round(passed_records / total_records * 100, 2) if total_records > 0 else 0
    
    return {
        "success": True,
        "data": {
            "test_cases": {"total": total_cases},
            "executions": {
                "total": total_records,
                "passed": passed_records,
                "failed": failed_records,
                "pass_rate": pass_rate
            },
            "bugs": {"total": total_bugs, "pending": pending_bugs},
            "reports": {"total": total_reports},
            "current_model": {
                "name": active_model.model_name if active_model else "未配置",
                "provider": active_model.provider if active_model else None,
                "tokens_today": active_model.tokens_used_today if active_model else 0,
                "tokens_total": active_model.tokens_used_total if active_model else 0
            }
        }
    }


@router.get("/trends")
def get_trends(days: int = 7, db: Session = Depends(get_db)):
    """获取趋势数据"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    daily_stats = []
    for i in range(days):
        date = start_date + timedelta(days=i)
        next_date = date + timedelta(days=1)
        
        day_total = db.query(TestRecord).filter(
            TestRecord.executed_at >= date,
            TestRecord.executed_at < next_date
        ).count()
        
        day_passed = db.query(TestRecord).filter(
            TestRecord.executed_at >= date,
            TestRecord.executed_at < next_date,
            TestRecord.status == 'pass'
        ).count()
        
        daily_stats.append({
            "date": date.strftime("%Y-%m-%d"),
            "total": day_total,
            "passed": day_passed,
            "failed": day_total - day_passed
        })
    
    return {"success": True, "data": {"daily_stats": daily_stats}}


@router.get("/bug-distribution")
def get_bug_distribution(db: Session = Depends(get_db)):
    """获取 Bug 分布数据"""
    severity_stats = {}
    for level in ['一级', '二级', '三级', '四级']:
        count = db.query(BugReport).filter(BugReport.severity_level == level).count()
        severity_stats[level] = count
    
    status_stats = {}
    for status in ['待处理', '已确认', '已修复', '已关闭']:
        count = db.query(BugReport).filter(BugReport.status == status).count()
        status_stats[status] = count
    
    type_stats = {}
    for error_type in ['功能错误', '设计缺陷', '安全问题', '系统错误']:
        count = db.query(BugReport).filter(BugReport.error_type == error_type).count()
        type_stats[error_type] = count
    
    return {
        "success": True,
        "data": {
            "by_severity": severity_stats,
            "by_status": status_stats,
            "by_type": type_stats
        }
    }


@router.get("/security-stats")
def get_security_stats(db: Session = Depends(get_db)):
    """获取安全测试统计数据"""
    try:
        from database.connection import SecurityScanTask, SecurityTarget, SecurityVulnerability
        
        # 扫描任务统计
        total_scans = db.query(func.count(SecurityScanTask.id)).scalar() or 0
        finished_scans = db.query(func.count(SecurityScanTask.id)).filter(
            SecurityScanTask.status == 'finished'
        ).scalar() or 0
        failed_scans = db.query(func.count(SecurityScanTask.id)).filter(
            SecurityScanTask.status == 'failed'
        ).scalar() or 0
        running_scans = db.query(func.count(SecurityScanTask.id)).filter(
            SecurityScanTask.status == 'running'
        ).scalar() or 0

        # 按扫描类型统计
        type_stats = {}
        for scan_type in ['nuclei', 'sqlmap', 'xsstrike', 'fuzz', 'full_scan']:
            count = db.query(func.count(SecurityScanTask.id)).filter(
                SecurityScanTask.scan_type == scan_type
            ).scalar() or 0
            type_stats[scan_type] = count

        # 风险等级分布（基于漏洞严重程度）
        risk_stats = {
            'A': 0,  # 严重 (critical)
            'B': 0,  # 高危 (high)
            'C': 0,  # 中危 (medium)
            'D': 0   # 低危 (low + info)
        }
        
        # 从漏洞表统计风险等级
        critical_count = db.query(func.count(SecurityVulnerability.id)).filter(
            SecurityVulnerability.severity == 'critical'
        ).scalar() or 0
        high_count = db.query(func.count(SecurityVulnerability.id)).filter(
            SecurityVulnerability.severity == 'high'
        ).scalar() or 0
        medium_count = db.query(func.count(SecurityVulnerability.id)).filter(
            SecurityVulnerability.severity == 'medium'
        ).scalar() or 0
        low_count = db.query(func.count(SecurityVulnerability.id)).filter(
            SecurityVulnerability.severity == 'low'
        ).scalar() or 0
        info_count = db.query(func.count(SecurityVulnerability.id)).filter(
            SecurityVulnerability.severity == 'info'
        ).scalar() or 0
        
        risk_stats['A'] = critical_count
        risk_stats['B'] = high_count
        risk_stats['C'] = medium_count
        risk_stats['D'] = low_count + info_count

        # 漏洞严重程度汇总
        vuln_totals = {
            'critical': critical_count,
            'high': high_count,
            'medium': medium_count,
            'low': low_count,
            'info': info_count
        }

        # 安全测试用例状态统计（从漏洞状态推算）
        total_vulns = db.query(func.count(SecurityVulnerability.id)).scalar() or 0
        open_vulns = db.query(func.count(SecurityVulnerability.id)).filter(
            SecurityVulnerability.status == 'open'
        ).scalar() or 0
        fixed_vulns = db.query(func.count(SecurityVulnerability.id)).filter(
            SecurityVulnerability.status == 'fixed'
        ).scalar() or 0
        
        # 目标数量
        total_targets = db.query(func.count(SecurityTarget.id)).scalar() or 0
        
        case_total = total_targets
        case_pass = total_targets - (total_scans - finished_scans) if total_targets > 0 else 0
        case_bug = open_vulns
        case_pending = total_scans - finished_scans - failed_scans

        return {
            "success": True,
            "data": {
                "scan_summary": {
                    "total": total_scans,
                    "finished": finished_scans,
                    "failed": failed_scans,
                    "running": running_scans
                },
                "by_type": type_stats,
                "by_risk_level": risk_stats,
                "vuln_severity": vuln_totals,
                "case_status": {
                    "total": case_total,
                    "pass": case_pass,
                    "bug": case_bug,
                    "pending": case_pending
                }
            }
        }
    except Exception as e:
        print(f"获取安全统计数据失败: {e}")
        # 返回默认数据
        return {
            "success": True,
            "data": {
                "scan_summary": {
                    "total": 0,
                    "finished": 0,
                    "failed": 0,
                    "running": 0
                },
                "by_type": {
                    "nuclei": 0,
                    "sqlmap": 0,
                    "xsstrike": 0,
                    "fuzz": 0,
                    "full_scan": 0
                },
                "by_risk_level": {
                    "A": 0,
                    "B": 0,
                    "C": 0,
                    "D": 0
                },
                "vuln_severity": {
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "info": 0
                },
                "case_status": {
                    "total": 0,
                    "pass": 0,
                    "bug": 0,
                    "pending": 0
                }
            }
        }


@router.get("/recent-executions")
def get_recent_executions(limit: int = 10, db: Session = Depends(get_db)):
    """获取最近的执行记录"""
    records = db.query(TestRecord).order_by(TestRecord.id.desc()).limit(limit).all()
    
    data = [
        {
            "id": r.id,
            "test_case_id": r.test_case_id,
            "status": r.status,
            "duration": r.duration,
            "test_steps": r.test_steps,
            "executed_at": r.executed_at.isoformat() if r.executed_at else None
        }
        for r in records
    ]
    
    return {"success": True, "data": data}


@router.get("/system-logs")
def get_system_logs(limit: int = 50, db: Session = Depends(get_db)):
    """
    获取系统日志（从现有数据聚合）

    从 TestReport、BugReport、EmailRecord、TokenUsageLog 中
    提取最近的活动记录，按时间倒序返回。
    """
    from database.connection import TokenUsageLog

    logs = []

    try:
        # 最近的测试报告
        reports = db.query(TestReport).order_by(
            TestReport.created_at.desc()
        ).limit(limit // 4).all()
        for r in reports:
            summary = r.summary or {}
            total = summary.get("total", 0)
            passed = summary.get("pass", summary.get("passed", 0))
            logs.append({
                "level": "INFO",
                "source": "action",
                "message": f"测试报告生成: {r.title or '-'} (通过 {passed}/{total})",
                "created_at": r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else "",
                "sort_time": r.created_at or datetime.min,
            })

        # 最近的 Bug 报告
        bugs = db.query(BugReport).order_by(
            BugReport.created_at.desc()
        ).limit(limit // 4).all()
        for b in bugs:
            level = "ERROR" if b.severity_level in ("一级", "二级") else "WARNING"
            logs.append({
                "level": level,
                "source": "bug",
                "message": f"Bug [{b.severity_level}] {b.bug_name or '-'}",
                "created_at": b.created_at.strftime('%Y-%m-%d %H:%M:%S') if b.created_at else "",
                "sort_time": b.created_at or datetime.min,
            })

        # 最近的邮件记录
        emails = db.query(EmailRecord).order_by(
            EmailRecord.created_at.desc()
        ).limit(limit // 4).all()
        for e in emails:
            level = "INFO" if e.status == "success" else "WARNING" if e.status == "partial" else "ERROR"
            logs.append({
                "level": level,
                "source": "action",
                "message": f"邮件发送: {e.subject or '-'} ({e.status}, 成功 {e.success_count}/{e.total_count})",
                "created_at": e.created_at.strftime('%Y-%m-%d %H:%M:%S') if e.created_at else "",
                "sort_time": e.created_at or datetime.min,
            })

        # 最近的 Token 使用日志
        token_logs = db.query(TokenUsageLog).order_by(
            TokenUsageLog.created_at.desc()
        ).limit(limit // 4).all()
        for t in token_logs:
            level = "INFO" if t.success else "ERROR"
            logs.append({
                "level": level,
                "source": "system",
                "message": f"模型调用: {t.model_name or '-'} (Token: {t.total_tokens or 0}, {'成功' if t.success else '失败: ' + (t.error_type or '')})",
                "created_at": t.created_at.strftime('%Y-%m-%d %H:%M:%S') if t.created_at else "",
                "sort_time": t.created_at or datetime.min,
            })

    except Exception as e:
        return {"success": True, "data": [
            {"level": "WARNING", "source": "system", "message": f"日志加载异常: {str(e)}", "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        ]}

    # 按时间倒序排列，取前 limit 条
    logs.sort(key=lambda x: x["sort_time"], reverse=True)
    # 移除排序用的临时字段
    for log in logs:
        log.pop("sort_time", None)

    return {"success": True, "data": logs[:limit]}
