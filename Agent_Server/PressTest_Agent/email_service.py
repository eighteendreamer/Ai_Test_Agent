"""
压测报告邮件发送服务

负责：
1. 生成压测报告
2. 获取自动接收邮件的用户
3. 发送邮件

作者: 程序员Eighteen
"""
import logging
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from database.connection import EmailConfig, EmailRecord, Contact
from Email_manage.sender import dispatch_send
from .report import PressTestReportGenerator
from .models import TaskResultResponse, PressTestDSL

logger = logging.getLogger(__name__)


class PressTestEmailService:
    """压测报告邮件发送服务"""
    
    def __init__(self, db: Session, llm_provider=None):
        """
        初始化邮件服务
        
        Args:
            db: 数据库会话
            llm_provider: LLM Provider 实例
        """
        self.db = db
        self.llm_provider = llm_provider
        self.report_generator = PressTestReportGenerator(llm_provider)
    
    def send_report(
        self,
        task_id: int,
        task_name: str,
        dsl: PressTestDSL,
        result: TaskResultResponse,
        created_at: datetime,
        finished_at: datetime
    ) -> dict:
        """
        发送压测报告邮件
        
        Args:
            task_id: 任务 ID
            task_name: 任务名称
            dsl: 压测 DSL
            result: 任务结果
            created_at: 创建时间
            finished_at: 完成时间
            
        Returns:
            发送结果统计
        """
        try:
            # 1. 获取激活的邮件配置
            email_config = self._get_active_email_config()
            if not email_config:
                logger.warning("未找到激活的邮件配置，跳过邮件发送")
                return {
                    "success": False,
                    "message": "未配置邮件服务",
                    "sent_count": 0,
                    "failed_count": 0
                }
            
            # 2. 获取自动接收邮件的用户
            recipients = self._get_auto_receive_contacts()
            if not recipients:
                logger.info("没有设置自动接收邮件的用户，跳过邮件发送")
                return {
                    "success": True,
                    "message": "没有接收人",
                    "sent_count": 0,
                    "failed_count": 0
                }
            
            # 3. 生成报告
            html_content = self.report_generator.generate_html_report(
                task_name=task_name,
                dsl=dsl,
                result=result,
                task_id=task_id,
                created_at=created_at,
                finished_at=finished_at
            )
            
            # 4. 发送邮件
            subject = f"压测报告 - {task_name}"
            sent_count = 0
            failed_count = 0
            failed_details = []
            
            for contact in recipients:
                try:
                    # 处理测试模式
                    to_email = contact.email
                    if email_config.test_mode == 1 and email_config.test_email:
                        to_email = email_config.test_email
                        logger.info(f"测试模式：将邮件发送到 {to_email} 而不是 {contact.email}")
                    
                    # 发送邮件
                    dispatch_send(
                        config=email_config,
                        to_email=to_email,
                        subject=subject,
                        html_body=html_content
                    )
                    
                    sent_count += 1
                    logger.info(f"成功发送压测报告到 {to_email}")
                    
                except Exception as e:
                    failed_count += 1
                    error_msg = str(e)
                    failed_details.append({
                        "email": contact.email,
                        "name": contact.name,
                        "error": error_msg
                    })
                    logger.error(f"发送邮件到 {contact.email} 失败: {error_msg}")
            
            # 5. 记录发送结果
            self._save_email_record(
                subject=subject,
                recipients=[c.email for c in recipients],
                success_count=sent_count,
                failed_count=failed_count,
                failed_details=failed_details,
                content_summary=f"压测任务 #{task_id} - {task_name}"
            )
            
            return {
                "success": sent_count > 0,
                "message": f"成功发送 {sent_count} 封，失败 {failed_count} 封",
                "sent_count": sent_count,
                "failed_count": failed_count,
                "failed_details": failed_details
            }
            
        except Exception as e:
            logger.error(f"发送压测报告失败: {e}")
            return {
                "success": False,
                "message": f"发送失败: {str(e)}",
                "sent_count": 0,
                "failed_count": 0
            }
    
    def _get_active_email_config(self) -> Optional[EmailConfig]:
        """获取激活的邮件配置"""
        return self.db.query(EmailConfig).filter(
            EmailConfig.is_active == 1
        ).first()
    
    def _get_auto_receive_contacts(self) -> List[Contact]:
        """获取自动接收邮件的联系人"""
        return self.db.query(Contact).filter(
            Contact.auto_receive_bug == 1,
            Contact.email.isnot(None),
            Contact.email != ''
        ).all()
    
    def _save_email_record(
        self,
        subject: str,
        recipients: List[str],
        success_count: int,
        failed_count: int,
        failed_details: List[dict],
        content_summary: str
    ):
        """保存邮件发送记录"""
        try:
            # 确定状态
            total_count = success_count + failed_count
            if failed_count == 0:
                status = 'success'
            elif success_count == 0:
                status = 'failed'
            else:
                status = 'partial'
            
            # 创建记录
            record = EmailRecord(
                subject=subject,
                recipients=recipients,
                status=status,
                success_count=success_count,
                failed_count=failed_count,
                total_count=total_count,
                email_type='presstest_report',
                content_summary=content_summary,
                failed_details=failed_details if failed_details else None,
                created_at=datetime.now()
            )
            
            self.db.add(record)
            self.db.commit()
            
            logger.info(f"邮件发送记录已保存: ID={record.id}")
            
        except Exception as e:
            logger.error(f"保存邮件记录失败: {e}")
            self.db.rollback()
