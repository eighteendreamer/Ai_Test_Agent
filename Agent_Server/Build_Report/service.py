"""
测试报告生成服务

作者: 程序员Eighteen
"""
import json
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from datetime import datetime


class TestReportService:
    """测试报告生成服务"""
    
    @staticmethod
    async def generate_report(
        test_result_ids: List[int],
        db: Session,
        format_type: str = "markdown",
        execution_mode: str = "单量",
        project_id: int = None
    ) -> Dict[str, Any]:
        """
        生成测试报告
        
        Args:
            test_result_ids: 测试结果 ID 列表
            db: 数据库会话
            format_type: 报告格式 (markdown/html/text)
            execution_mode: 执行模式 (单量/批量)
        
        Returns:
            生成的报告数据
        """
        from database.connection import TestRecord, TestCase, TestReport
        
        try:
            # 获取测试结果
            test_results = db.query(TestRecord).filter(
                TestRecord.id.in_(test_result_ids)
            ).all()
            
            if not test_results:
                return {
                    "success": False,
                    "message": "未找到测试结果"
                }
            
            # 收集测试数据
            results_data = []
            total_steps = 0
            total_duration = 0
            pass_count = 0
            fail_count = 0
            
            for result in test_results:
                # 获取关联的测试用例
                test_case = db.query(TestCase).filter(
                    TestCase.id == result.test_case_id
                ).first()
                
                result_data = {
                    "id": result.id,
                    "test_case_id": result.test_case_id,
                    "test_case_title": test_case.title if test_case else "未知",
                    "status": result.status,
                    "duration": result.duration or 0,
                    "steps": result.test_steps or 0,
                    "execution_log": result.execution_log,
                    "error_message": result.error_message,
                    "executed_at": result.executed_at.isoformat() if result.executed_at else None
                }
                results_data.append(result_data)
                
                total_steps += result.test_steps or 0
                total_duration += result.duration or 0
                
                if result.status == 'pass':
                    pass_count += 1
                else:
                    fail_count += 1
            
            # 构建报告摘要
            summary = {
                "total": len(results_data),
                "pass": pass_count,
                "fail": fail_count,
                "pass_rate": round(pass_count / len(results_data) * 100, 2) if results_data else 0,
                "duration": total_duration,
                "total_steps": total_steps,
                "execution_mode": execution_mode
            }
            
            # 调用 LLM 生成报告内容
            from llm import get_llm_client
            
            llm_client = get_llm_client()
            
            report_result = llm_client.generate_test_report(
                test_results=results_data,
                summary=summary,
                format_type=format_type
            )
            
            if not report_result.get('success'):
                # 生成简单报告
                report_content = TestReportService._generate_simple_report(
                    results_data, summary, format_type
                )
            else:
                report_content = report_result.get('content', '')
            
            # 保存报告到数据库
            report_title = f"测试报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            test_report = TestReport(
                project_id=project_id,
                title=report_title,
                summary=summary,
                details=report_content,
                format_type=format_type,
                total_steps=total_steps
            )
            
            db.add(test_report)
            db.commit()
            db.refresh(test_report)
            
            return {
                "success": True,
                "data": {
                    "id": test_report.id,
                    "title": report_title,
                    "summary": summary,
                    "details": report_content,
                    "format_type": format_type
                }
            }
        
        except Exception as e:
            import traceback
            print(f"[ReportService] 生成报告失败: {str(e)}")
            print(traceback.format_exc())
            return {
                "success": False,
                "message": f"生成报告失败: {str(e)}"
            }
    
    @staticmethod
    def _generate_simple_report(
        results: List[Dict],
        summary: Dict,
        format_type: str
    ) -> str:
        """生成简单报告"""
        if format_type == "markdown":
            report = f"""# 测试报告

## 测试概览
- 总用例数: {summary['total']}
- 通过: {summary['pass']}
- 失败: {summary['fail']}
- 通过率: {summary['pass_rate']}%
- 总耗时: {summary['duration']} 秒

## 测试结果详情

"""
            for result in results:
                status_icon = "✅" if result['status'] == 'pass' else "❌"
                report += f"""### {status_icon} {result['test_case_title']}
- 状态: {result['status']}
- 耗时: {result['duration']} 秒
- 执行步数: {result['steps']}

"""
            return report
        else:
            return json.dumps({
                "summary": summary,
                "results": results
            }, ensure_ascii=False, indent=2)
    
    @staticmethod
    def get_reports(
        db: Session,
        limit: int = 20,
        offset: int = 0,
        project_id: int = None
    ) -> Dict[str, Any]:
        """获取报告列表"""
        from database.connection import TestReport
        from sqlalchemy import or_

        query = db.query(TestReport)

        # 项目隔离过滤：严格按项目_id筛选。
        # 对项目1额外包含 IS NULL 的遗留数据。
        if project_id is not None:
            if project_id == 1:
                query = query.filter(
                    or_(TestReport.project_id == 1, TestReport.project_id.is_(None))
                )
            else:
                query = query.filter(TestReport.project_id == project_id)
        
        total = query.count()
        reports = query.order_by(TestReport.id.desc()).limit(limit).offset(offset).all()
        
        data = [
            {
                "id": report.id,
                "title": report.title,
                "summary": report.summary,
                "format_type": report.format_type,
                "created_at": report.created_at.isoformat() if report.created_at else None
            }
            for report in reports
        ]
        
        return {"data": data, "total": total}
