"""
数据库连接和模型定义

作者: 程序员Eighteen
"""
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, inspect, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects.mysql import LONGTEXT
from datetime import datetime
import os
from dotenv import load_dotenv

# 加载环境变量 - .env 文件在 Agent_Server 目录下
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)

# 数据库连接配置 - 必须从环境变量获取，不提供默认值
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

# 验证必需的环境变量
if not all([DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME]):
    raise ValueError(
        "数据库配置缺失！请在 .env 文件中配置以下环境变量：\n"
        "DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME"
    )

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

# 创建数据库引擎
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基类
Base = declarative_base()


# ============================================
# 数据库模型定义
# ============================================

class Project(Base):
    """项目管理表"""
    __tablename__ = 'projects'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    name = Column(String(100), nullable=False, unique=True, comment='项目名称')
    code = Column(String(50), nullable=False, unique=True, comment='项目代码')
    description = Column(Text, comment='项目描述')
    is_default = Column(Integer, default=0, comment='是否默认项目（0:否 1:是）')
    is_active = Column(Integer, default=1, comment='是否启用（0:否 1:是）')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class ExecutionCase(Base):
    """用例详情表 - 存储所有测试用例"""
    __tablename__ = 'execution_cases'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    zentao_case_id = Column(Integer, index=True, comment='禅道用例ID（用于去重和同步）')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    title = Column(String(200), nullable=False, comment='用例标题')
    module = Column(String(100), comment='所属模块')
    precondition = Column(Text, comment='前置条件')
    steps = Column(Text, nullable=False, comment='测试步骤（JSON格式）')
    expected = Column(Text, nullable=False, comment='预期结果')
    keywords = Column(String(200), comment='关键词')
    case_type = Column(String(50), comment='用例类型')
    priority = Column(String(20), comment='优先级', default='3')
    stage = Column(String(50), comment='适用阶段')
    test_data = Column(JSON, comment='测试数据（JSON格式）')
    csv_file_path = Column(String(500), comment='CSV文件路径')
    security_status = Column(String(20), default='待测试', comment='安全测试状态: 待测试/通过/bug')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class ExecutionBatch(Base):
    """执行批次中间表 - 用例与批次的对应关系"""
    __tablename__ = 'execution_batches'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    execution_case_id = Column(Integer, nullable=False, index=True, comment='用例ID')
    batch = Column(String(50), nullable=False, index=True, comment='批次号')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class TestRecord(Base):
    """执行记录表 - 记录单条/批量执行的汇总信息"""
    __tablename__ = 'test_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    batch_id = Column(Integer, nullable=False, index=True, comment='中间表ID')
    test_case_id = Column(Integer, comment='关联用例ID')
    execution_mode = Column(String(20), default='单量', comment='执行模式')
    total_cases = Column(Integer, default=1, comment='用例总数')
    passed_cases = Column(Integer, default=0, comment='通过数')
    failed_cases = Column(Integer, default=0, comment='失败数')
    execution_log = Column(LONGTEXT, comment='执行日志')
    status = Column(String(20), comment='测试结果')
    error_message = Column(LONGTEXT, comment='错误信息')
    executed_at = Column(DateTime, default=datetime.now, comment='执行时间')
    duration = Column(Integer, comment='执行耗时（秒）')
    test_steps = Column(Integer, default=0, comment='执行步数')


# 别名（兼容旧代码）
TestCase = ExecutionCase
TestResult = TestRecord


class TestReport(Base):
    """测试报告表"""
    __tablename__ = 'test_reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    title = Column(String(200), nullable=False, comment='报告标题')
    summary = Column(JSON, comment='测试统计摘要')
    details = Column(Text, comment='报告详细内容')
    file_path = Column(String(500), comment='报告文件路径')
    format_type = Column(String(20), comment='报告格式')
    total_steps = Column(Integer, default=0, comment='总步数')
    created_at = Column(DateTime, default=datetime.now, comment='生成时间')


class BugReport(Base):
    """Bug报告表"""
    __tablename__ = 'bug_reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    test_record_id = Column(Integer, comment='关联执行记录ID')
    bug_name = Column(String(200), nullable=False, comment='Bug名称')
    test_case_id = Column(Integer, comment='关联测试用例ID')
    location_url = Column(String(500), comment='定位地址')
    error_type = Column(String(50), nullable=False, comment='错误类型')
    severity_level = Column(String(20), nullable=False, comment='严重程度')
    reproduce_steps = Column(Text, nullable=False, comment='复现步骤')
    screenshot_path = Column(String(500), comment='失败截图路径')
    result_feedback = Column(Text, comment='结果反馈')
    expected_result = Column(Text, comment='预期结果')
    actual_result = Column(Text, comment='实际结果')
    status = Column(String(20), default='待处理', comment='Bug状态')
    zentao_bug_id = Column(Integer, comment='禅道Bug ID（已推送时记录）')
    description = Column(Text, comment='问题描述')
    case_type = Column(String(50), comment='测试类型')
    execution_mode = Column(String(20), default='单量', comment='执行模式')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class LLMModel(Base):
    """LLM模型配置表"""
    __tablename__ = 'llm_models'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    model_name = Column(String(100), nullable=False, comment='模型名称')
    api_key = Column(String(500), nullable=False, comment='API密钥')
    base_url = Column(String(500), comment='API基础URL')
    provider = Column(String(50), comment='模型供应商code')
    is_active = Column(Integer, default=0, comment='是否激活（0:否 1:是）')
    priority = Column(Integer, default=1, comment='优先级')
    utilization = Column(Integer, default=100, comment='利用率百分比')
    tokens_used_total = Column(Integer, default=0, comment='总消耗TOKEN')
    tokens_used_today = Column(Integer, default=0, comment='今日消耗TOKEN')
    tokens_input_total = Column(Integer, default=0, comment='总输入TOKEN')
    tokens_output_total = Column(Integer, default=0, comment='总输出TOKEN')
    request_count_total = Column(Integer, default=0, comment='总请求次数')
    request_count_today = Column(Integer, default=0, comment='今日请求次数')
    failure_count_total = Column(Integer, default=0, comment='总失败次数')
    last_failure_reason = Column(String(50), comment='最近失败原因')
    last_used_at = Column(DateTime, comment='最近使用时间')
    auto_switch_enabled = Column(Integer, default=1, comment='是否参与自动切换')
    status = Column(String(50), default='待命', comment='模型状态')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class TokenUsageLog(Base):
    """Token 使用日志表（按次记录）"""
    __tablename__ = 'token_usage_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, nullable=False, comment='模型ID')
    model_name = Column(String(100), comment='模型名称')
    provider = Column(String(50), comment='供应商')
    prompt_tokens = Column(Integer, default=0, comment='输入Token')
    completion_tokens = Column(Integer, default=0, comment='输出Token')
    total_tokens = Column(Integer, default=0, comment='总Token')
    source = Column(String(50), comment='来源: chat/browser_use/oneclick/api_test')
    session_id = Column(Integer, comment='关联会话ID')
    success = Column(Integer, default=1, comment='是否成功')
    error_type = Column(String(50), comment='错误类型')
    duration_ms = Column(Integer, default=0, comment='耗时毫秒')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class ModelProvider(Base):
    """模型供应商表"""
    __tablename__ = 'model_providers'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    name = Column(String(100), nullable=False, unique=True, comment='供应商名称')
    code = Column(String(50), nullable=False, unique=True, comment='供应商代码')
    display_name = Column(String(100), nullable=False, comment='显示名称')
    default_base_url = Column(String(500), comment='默认API基础URL')
    is_active = Column(Integer, default=1, comment='是否启用')
    sort_order = Column(Integer, default=0, comment='排序顺序')
    description = Column(Text, comment='备注')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class Contact(Base):
    """联系人表"""
    __tablename__ = 'contacts'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    name = Column(String(100), nullable=False, comment='姓名')
    email = Column(String(200), nullable=False, comment='邮箱')
    role = Column(String(50), comment='角色')
    auto_receive_bug = Column(Integer, default=0, comment='自动接收BUG')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class EmailRecord(Base):
    """邮件发送记录表"""
    __tablename__ = 'email_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    subject = Column(String(200), nullable=False, comment='邮件主题')
    recipients = Column(JSON, nullable=False, comment='收件人列表')
    status = Column(String(20), nullable=False, comment='发送状态')
    success_count = Column(Integer, default=0, comment='成功发送数量')
    failed_count = Column(Integer, default=0, comment='失败发送数量')
    total_count = Column(Integer, nullable=False, comment='总接收人数')
    email_type = Column(String(50), default='report', comment='邮件类型')
    content_summary = Column(Text, comment='邮件内容摘要')
    email_ids = Column(JSON, comment='邮件ID列表')
    failed_details = Column(JSON, comment='失败详情')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class EmailConfig(Base):
    """邮件发送配置表"""
    __tablename__ = 'email_config'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    config_name = Column(String(50), unique=True, nullable=False, comment='配置名称')
    provider = Column(String(20), default='resend', nullable=False, comment='邮箱服务商')
    api_key = Column(String(200), nullable=False, comment='API Key')
    secret_key = Column(String(200), comment='Secret Key')
    sender_email = Column(String(200), nullable=False, comment='发件人邮箱')
    test_email = Column(String(200), comment='测试邮箱')
    test_mode = Column(Integer, default=1, comment='测试模式')
    is_active = Column(Integer, default=0, comment='是否激活')
    description = Column(Text, comment='备注说明')
    smtp_host = Column(String(200), comment='SMTP服务器地址')
    smtp_port = Column(Integer, default=587, comment='SMTP端口')
    smtp_username = Column(String(200), comment='SMTP用户名')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class ApiSpec(Base):
    """接口文件索引表"""
    __tablename__ = 'api_specs'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    service_name = Column(String(100), comment='服务名称')
    base_url = Column(String(500), comment='测试地址/后端地址')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class ApiSpecVersion(Base):
    """接口文件版本表"""
    __tablename__ = 'api_spec_versions'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    spec_id = Column(Integer, nullable=False, comment='关联 api_specs.id')
    original_filename = Column(String(255), nullable=False, comment='原始文件名')
    minio_bucket = Column(String(100), nullable=False, comment='MinIO Bucket')
    minio_key = Column(String(500), nullable=False, comment='MinIO 对象 Key')
    file_hash = Column(String(64), nullable=False, comment='文件 SHA256')
    file_size = Column(Integer, default=0, comment='文件大小(字节)')
    etag = Column(String(200), comment='MinIO ETag')
    parse_summary = Column(Text, comment='解析摘要(用于 LLM 匹配)')
    endpoint_count = Column(Integer, default=0, comment='接口数量')
    parse_warnings = Column(JSON, comment='解析警告')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class ApiEndpoint(Base):
    """接口资产表"""
    __tablename__ = 'api_endpoints'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    spec_version_id = Column(Integer, nullable=False, comment='关联 api_spec_versions.id')
    method = Column(String(10), nullable=False, comment='HTTP 方法')
    path = Column(String(300), nullable=False, comment='接口路径')
    summary = Column(String(300), comment='接口摘要')
    description = Column(Text, comment='接口描述')
    params = Column(JSON, comment='参数定义')
    success_example = Column(JSON, comment='成功响应示例')
    error_example = Column(JSON, comment='错误响应示例')
    notes = Column(Text, comment='备注')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class OneclickSession(Base):
    """一键测试会话表"""
    __tablename__ = 'oneclick_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    user_input = Column(Text, nullable=False, comment='用户输入的自然语言指令')
    status = Column(String(20), default='init', comment='会话状态')
    target_url = Column(String(500), comment='目标测试地址')
    login_info = Column(JSON, comment='登录信息')
    page_analysis = Column(JSON, comment='页面分析结果')
    page_capabilities = Column(JSON, comment='页面能力抽象（forms/buttons/tables/auth_required等）')
    task_tree = Column(JSON, comment='分层任务树（L1→L2→L3 结构）')
    generated_cases = Column(JSON, comment='LLM生成的测试用例')
    confirmed_cases = Column(JSON, comment='用户确认后的测试用例')
    execution_result = Column(JSON, comment='执行结果')
    report_id = Column(Integer, comment='关联报告ID')
    skill_ids = Column(JSON, comment='使用的Skills ID列表')
    messages = Column(JSON, comment='对话消息历史')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class TestEnvironment(Base):
    """测试环境配置表 — 存储被测系统的 URL、账号密码等"""
    __tablename__ = 'test_environments'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    name = Column(String(100), nullable=False, comment='环境名称（如：开发环境、测试环境）')
    base_url = Column(String(500), nullable=False, comment='系统首页URL')
    login_url = Column(String(500), comment='登录页URL（为空则与base_url相同）')
    username = Column(String(200), comment='登录账号')
    password = Column(String(200), comment='登录密码')
    extra_credentials = Column(JSON, comment='额外凭据（如验证码、token等）')
    description = Column(Text, comment='环境描述')
    is_default = Column(Integer, default=0, comment='是否为默认环境（0:否 1:是）')
    is_active = Column(Integer, default=1, comment='是否启用')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


# ============================================
# 安全测试平台数据模型 (基于新方案设计)
# ============================================

class SecurityTarget(Base):
    """安全测试目标表 - 资产管理"""
    __tablename__ = 'security_targets'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    name = Column(String(200), nullable=False, comment='目标名称')
    base_url = Column(String(500), nullable=False, comment='基础URL')
    description = Column(Text, comment='目标描述')
    target_type = Column(String(50), default='web', comment='目标类型: web/api/mobile/desktop')
    environment = Column(String(50), default='test', comment='环境: dev/test/staging/prod')
    auth_config = Column(JSON, comment='认证配置(用户名密码等)')
    scan_config = Column(JSON, comment='扫描配置(工具参数等)')
    is_active = Column(Integer, default=1, comment='是否启用')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class SecurityScanTask(Base):
    """安全扫描任务表 - 兼容 pentagi 风格"""
    __tablename__ = 'security_scan_tasks'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, comment='关联项目ID（可选）')
    target_id = Column(Integer, comment='关联目标ID（兼容旧版）')
    target_url = Column(String(1000), comment='目标URL')
    scan_type = Column(String(50), nullable=False, comment='扫描类型: nuclei/sqlmap/xsstrike/fuzz/full_scan/comprehensive')
    status = Column(String(20), default='pending', comment='状态: pending/running/finished/failed/stopped')
    current_phase = Column(String(100), comment='当前执行阶段')
    current_subtask_id = Column(Integer, comment='当前执行的子任务ID')
    report_id = Column(Integer, comment='关联的报告ID')
    progress = Column(Integer, default=0, comment='进度百分比 0-100')
    cancel_requested = Column(Integer, default=0, comment='是否请求取消: 0=否, 1=是')
    config = Column(JSON, comment='扫描配置（兼容旧版）')
    scope_config = Column(JSON, comment='范围配置')
    auth_strategy = Column(String(50), comment='认证策略')
    credential_ref = Column(String(200), comment='凭证引用')
    start_time = Column(DateTime, comment='开始时间（兼容旧版）')
    started_at = Column(DateTime, comment='开始时间')
    end_time = Column(DateTime, comment='结束时间（兼容旧版）')
    finished_at = Column(DateTime, comment='完成时间')
    duration = Column(Integer, comment='耗时(秒)')
    error_message = Column(Text, comment='错误信息')
    summary = Column(Text, comment='任务摘要/标题')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class SecurityScanResult(Base):
    """扫描结果表"""
    __tablename__ = 'security_scan_results'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, nullable=False, comment='关联任务ID')
    tool = Column(String(50), nullable=False, comment='扫描工具: nuclei/sqlmap/xsstrike/fuzz')
    severity = Column(String(20), nullable=False, comment='严重程度: critical/high/medium/low/info')
    title = Column(String(500), nullable=False, comment='漏洞标题')
    description = Column(Text, comment='漏洞描述')
    evidence = Column(LONGTEXT, comment='漏洞证据')
    url = Column(String(1000), comment='漏洞URL')
    param = Column(String(200), comment='漏洞参数')
    payload = Column(Text, comment='攻击载荷')
    raw_output = Column(LONGTEXT, comment='工具原始输出')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class SecurityVulnerability(Base):
    """漏洞记录表"""
    __tablename__ = 'security_vulnerabilities'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, comment='关联任务ID')
    target_id = Column(Integer, comment='关联目标ID（兼容旧版）')
    title = Column(String(500), nullable=False, comment='漏洞标题')
    severity = Column(String(20), nullable=False, comment='严重程度: critical/high/medium/low/info')
    vuln_type = Column(String(100), comment='漏洞类型: sql_injection/xss/csrf/etc')
    description = Column(Text, comment='漏洞描述')
    cvss = Column(Float, comment='CVSS评分')
    cwe = Column(String(50), comment='CWE编号')
    fix_suggestion = Column(Text, comment='修复建议')
    status = Column(String(20), default='open', comment='状态: open/fixed/false_positive/accepted')
    risk_score = Column(Integer, comment='风险评分 0-100')
    first_found = Column(DateTime, default=datetime.now, comment='首次发现时间')
    last_seen = Column(DateTime, default=datetime.now, comment='最后发现时间')
    scan_results = Column(JSON, comment='关联的扫描结果ID列表')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class SecurityScanLog(Base):
    """扫描日志表"""
    __tablename__ = 'security_scan_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, nullable=False, comment='关联任务ID')
    level = Column(String(20), default='info', comment='日志级别: debug/info/warning/error')
    message = Column(Text, nullable=False, comment='日志内容')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class PageKnowledgeRecord(Base):
    """页面知识库元数据表（MySQL 侧，与 Qdrant 向量库配合使用）"""
    __tablename__ = 'page_knowledge'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    url = Column(String(500), nullable=False, index=True, comment='页面URL')
    domain = Column(String(200), comment='站点域名')
    page_type = Column(String(50), comment='页面类型: login/list/detail/form/dashboard/mixed')
    summary = Column(Text, comment='一句话能力摘要')
    module_name = Column(String(100), comment='所属功能模块')
    hash_signature = Column(String(32), comment='页面结构签名（变更检测）')
    version = Column(Integer, default=1, comment='知识版本号')
    knowledge_json = Column(JSON, comment='完整页面知识 JSON')
    vector_point_id = Column(String(100), comment='Qdrant 向量记录 ID')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class QdrantCollectionConfig(Base):
    """Qdrant Collection 配置表（页面知识库向量存储配置）"""
    __tablename__ = 'qdrant_collection_config'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    collection_name = Column(String(100), nullable=False, default='page_knowledge', comment='Collection名称')
    vector_size = Column(Integer, default=1024, comment='向量维度')
    distance = Column(String(20), default='Cosine', comment='距离度量: Cosine/Dot/Euclid/Manhattan')
    qdrant_host = Column(String(200), default='localhost', comment='Qdrant主机')
    qdrant_port = Column(Integer, default=6333, comment='Qdrant端口')
    embedding_model = Column(String(200), default='Qwen/Qwen3-Embedding-4B', comment='Embedding模型名')
    embedding_api_url = Column(String(500), default='https://api.siliconflow.cn/v1/embeddings', comment='Embedding API地址')
    embedding_api_key = Column(String(500), default='', comment='Embedding API Key')
    is_active = Column(Integer, default=1, comment='是否启用')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class ProjectPlatformConfig(Base):
    """项目管理平台统一配置表（整合所有项目管理工具配置）"""
    __tablename__ = 'project_platform_config'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    platform_id = Column(String(50), nullable=False, unique=True, comment='平台标识（zentao/pingcode/worktile等）')
    platform_name = Column(String(100), nullable=False, comment='平台名称')
    config_name = Column(String(100), nullable=False, comment='配置名称')
    base_url = Column(String(500), nullable=False, comment='平台访问地址')
    account = Column(String(200), nullable=False, comment='登录账号')
    password = Column(String(500), nullable=False, comment='登录密码')
    api_token = Column(String(500), comment='API Token（某些平台使用）')
    default_product_id = Column(Integer, comment='默认产品ID（禅道等平台使用）')
    api_version = Column(String(20), default='v2', comment='API版本（禅道等平台使用）')
    last_token = Column(String(500), comment='最近获取的Token')
    token_expire_at = Column(DateTime, comment='Token过期时间')
    extra_config = Column(Text, comment='额外配置（JSON格式，存储平台特有字段）')
    is_active = Column(Integer, default=0, comment='是否激活（0:否 1:是）')
    is_enabled = Column(Integer, default=1, comment='是否启用（0:禁用 1:启用）')
    last_sync_at = Column(DateTime, comment='最后同步时间')
    description = Column(Text, comment='备注说明')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class CaseTemplateConfig(Base):
    """用例模板配置表 - 存储从项目管理平台同步的用例字段模板"""
    __tablename__ = 'case_template_config'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    source_platform = Column(String(50), comment='模板来源平台（zentao/jira/tapd等）')
    template_name = Column(String(100), nullable=False, default='默认模板', comment='模板名称')
    fields = Column(JSON, nullable=False, comment='字段定义列表（JSON）')
    priority_options = Column(JSON, comment='优先级选项')
    case_type_options = Column(JSON, comment='用例类型选项')
    stage_options = Column(JSON, comment='适用阶段选项')
    extra_prompt = Column(Text, comment='附加给LLM的提示词（如特殊格式要求）')
    is_active = Column(Integer, default=1, comment='是否启用（0:否 1:是）')
    synced_at = Column(DateTime, comment='最后同步时间')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class Skill(Base):
    """Skills管理表"""
    __tablename__ = 'skills'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    name = Column(String(100), nullable=False, comment='Skill名称')
    slug = Column(String(200), comment='Skill标识(owner/repo)')
    source = Column(String(200), nullable=False, comment='来源URL')
    version = Column(String(50), comment='版本')
    description = Column(Text, comment='描述')
    category = Column(String(50), comment='分类')
    content = Column(LONGTEXT, comment='Skill内容(Markdown)')
    config = Column(JSON, comment='配置信息')
    author = Column(String(100), comment='作者')
    is_active = Column(Integer, default=1, comment='是否启用')
    install_count = Column(Integer, default=0, comment='安装次数')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


# ============================================
# Pentest_Agent Runtime Tables (pentagi-style)
# ============================================

class PentestSubtask(Base):
    """渗透测试子任务表 — 对应 pentagi SubtaskWorker 层"""
    __tablename__ = 'pentest_subtasks'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, nullable=False, index=True, comment='关联主任务ID (security_scan_tasks.id)')
    title = Column(String(300), nullable=False, comment='子任务标题')
    description = Column(Text, comment='子任务详细描述')
    status = Column(String(20), default='pending', comment='状态: pending/running/finished/failed/skipped')
    position = Column(Integer, default=0, comment='执行顺序')
    result = Column(LONGTEXT, comment='子任务执行结果摘要')
    started_at = Column(DateTime, comment='开始时间')
    finished_at = Column(DateTime, comment='完成时间')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class PentestMessageChain(Base):
    """Agent 消息链记录表 — 存储每条 agent chain 的完整消息上下文"""
    __tablename__ = 'pentest_message_chains'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, nullable=False, index=True, comment='关联主任务ID')
    subtask_id = Column(Integer, index=True, comment='关联子任务ID（可为空）')
    agent_type = Column(String(50), nullable=False, comment='Agent角色: primary/pentester/searcher/reporter/generator/refiner/reflector')
    messages_json = Column(LONGTEXT, comment='消息链 JSON 序列化')
    usage_summary = Column(JSON, comment='Token 用量统计')
    duration_seconds = Column(Integer, default=0, comment='累计耗时（秒）')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class PentestToolCall(Base):
    """工具调用记录表 — 存储每次 tool call 的入参、出参、状态"""
    __tablename__ = 'pentest_tool_calls'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, nullable=False, index=True, comment='关联主任务ID')
    subtask_id = Column(Integer, index=True, comment='关联子任务ID')
    chain_id = Column(Integer, index=True, comment='关联消息链ID')
    agent_role = Column(String(50), comment='发起调用的 Agent 角色')
    tool_name = Column(String(100), nullable=False, comment='工具名称')
    tool_args = Column(LONGTEXT, comment='工具入参 JSON')
    tool_result = Column(LONGTEXT, comment='工具返回结果')
    status = Column(String(20), default='ok', comment='状态: ok/error/timeout')
    duration_ms = Column(Integer, default=0, comment='执行耗时（毫秒）')
    exit_code = Column(Integer, comment='命令退出码（terminal 工具）')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class PentestArtifact(Base):
    """渗透测试产物表 — 存储工具输出文件、截图、证据等"""
    __tablename__ = 'pentest_artifacts'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, nullable=False, index=True, comment='关联主任务ID')
    subtask_id = Column(Integer, index=True, comment='关联子任务ID')
    artifact_type = Column(String(50), comment='类型: output/screenshot/report/evidence/log')
    file_path = Column(String(1000), comment='文件路径（相对 artifact_root）')
    content_type = Column(String(100), comment='MIME 类型')
    artifact_metadata = Column(JSON, comment='附加元数据')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')


class PentestReport(Base):
    """渗透测试报告记录表 — runtime 专用报告存储"""
    __tablename__ = 'pentest_reports'

    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    task_id = Column(Integer, nullable=False, index=True, comment='关联主任务ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    title = Column(String(300), comment='报告标题')
    format_type = Column(String(20), default='markdown', comment='格式: markdown/html/json')
    file_path = Column(String(1000), comment='报告文件路径')
    summary = Column(Text, comment='执行摘要')
    findings_critical = Column(Integer, default=0, comment='严重漏洞数')
    findings_high = Column(Integer, default=0, comment='高危漏洞数')
    findings_medium = Column(Integer, default=0, comment='中危漏洞数')
    findings_low = Column(Integer, default=0, comment='低危漏洞数')
    emailed = Column(Integer, default=0, comment='是否已发送邮件')
    emailed_at = Column(DateTime, comment='邮件发送时间')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


# ============================================
# 数据库初始化和会话管理
# ============================================

def init_db():
    """Initialize tables and run non-destructive schema upgrades."""
    try:
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        tables_to_create = {
            'projects': Project,
            'execution_cases': ExecutionCase,
            'execution_batches': ExecutionBatch,
            'test_records': TestRecord,
            'test_reports': TestReport,
            'bug_reports': BugReport,
            'llm_models': LLMModel,
            'token_usage_logs': TokenUsageLog,
            'model_providers': ModelProvider,
            'contacts': Contact,
            'email_records': EmailRecord,
            'email_config': EmailConfig,
            'api_specs': ApiSpec,
            'api_spec_versions': ApiSpecVersion,
            'api_endpoints': ApiEndpoint,
            'oneclick_sessions': OneclickSession,
            'test_environments': TestEnvironment,
            'skills': Skill,
            'security_targets': SecurityTarget,
            'security_scan_tasks': SecurityScanTask,
            'security_scan_results': SecurityScanResult,
            'security_vulnerabilities': SecurityVulnerability,
            'security_scan_logs': SecurityScanLog,
            'pentest_subtasks': PentestSubtask,
            'pentest_message_chains': PentestMessageChain,
            'pentest_tool_calls': PentestToolCall,
            'pentest_artifacts': PentestArtifact,
            'pentest_reports': PentestReport,
            'page_knowledge': PageKnowledgeRecord,
            'qdrant_collection_config': QdrantCollectionConfig,
            'project_platform_config': ProjectPlatformConfig,
            'case_template_config': CaseTemplateConfig,
            'presstest_tasks': PressTestTask,
        }

        for table_name in tables_to_create:
            if table_name not in existing_tables:
                Base.metadata.tables[table_name].create(bind=engine, checkfirst=True)
                print(f"[DB] created table '{table_name}'")
            else:
                print(f"[DB] table '{table_name}' already exists")

        # Apply lightweight schema upgrades for existing deployments.
        _upgrade_existing_tables(inspector)

        print("\n[DB] initialization complete")
    except Exception as e:
        print(f"[DB] initialization failed: {e}")
        raise


def _upgrade_existing_tables(inspector):
    """
    自动迁移：检查已有表是否缺少新增列，自动 ALTER TABLE 添加

    解决 SQLAlchemy create_all(checkfirst=True) 不会为已有表添加新列的问题
    """
    from sqlalchemy import text

    # 定义需要检查的新增列: (表名, 列名, SQL类型, 默认值)
    new_columns = [
        ('llm_models', 'tokens_input_total', 'INT DEFAULT 0', None),
        ('llm_models', 'tokens_output_total', 'INT DEFAULT 0', None),
        ('llm_models', 'request_count_total', 'INT DEFAULT 0', None),
        ('llm_models', 'request_count_today', 'INT DEFAULT 0', None),
        ('llm_models', 'failure_count_total', 'INT DEFAULT 0', None),
        ('llm_models', 'last_failure_reason', 'VARCHAR(50) DEFAULT NULL', None),
        ('llm_models', 'last_used_at', 'DATETIME DEFAULT NULL', None),
        ('llm_models', 'auto_switch_enabled', 'INT DEFAULT 1', None),
        ('execution_cases', 'security_status', "VARCHAR(20) DEFAULT '待测试'", None),
        ('execution_cases', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('test_records', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('test_reports', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('bug_reports', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('api_specs', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('api_specs', 'base_url', 'VARCHAR(500) DEFAULT NULL', None),
        ('api_spec_versions', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('oneclick_sessions', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('test_environments', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('security_targets', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('page_knowledge', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('email_config', 'smtp_host', 'VARCHAR(200) DEFAULT NULL', None),
        ('email_config', 'smtp_port', 'INT DEFAULT 587', None),
        ('email_config', 'smtp_username', 'VARCHAR(200) DEFAULT NULL', None),
        ('bug_reports', 'zentao_bug_id', 'INT DEFAULT NULL', None),
        ('project_platform_config', 'default_product_id', 'INT DEFAULT NULL', None),
        ('project_platform_config', 'api_version', "VARCHAR(20) DEFAULT 'v2'", None),
        ('project_platform_config', 'last_token', 'VARCHAR(500) DEFAULT NULL', None),
        ('project_platform_config', 'token_expire_at', 'DATETIME DEFAULT NULL', None),
        # security_targets — pentagi-style 升级字段
        ('security_targets', 'scope_config', 'JSON DEFAULT NULL', None),
        ('security_targets', 'environment_id', 'INT DEFAULT NULL', None),
        ('security_targets', 'entry_urls', 'JSON DEFAULT NULL', None),
        ('security_targets', 'auth_strategy', 'VARCHAR(100) DEFAULT NULL', None),
        ('security_targets', 'credential_ref', 'VARCHAR(200) DEFAULT NULL', None),
        # security_scan_tasks — 升级为 Flow shell
        ('security_scan_tasks', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('security_scan_tasks', 'engine_type', "VARCHAR(50) DEFAULT 'pentest_agent'", None),
        ('security_scan_tasks', 'session_id', 'VARCHAR(100) DEFAULT NULL', None),
        ('security_scan_tasks', 'current_phase', 'VARCHAR(50) DEFAULT NULL', None),
        ('security_scan_tasks', 'current_subtask_id', 'INT DEFAULT NULL', None),
        ('security_scan_tasks', 'cancel_requested', 'INT DEFAULT 0', None),
        ('security_scan_tasks', 'report_id', 'INT DEFAULT NULL', None),
        ('security_scan_tasks', 'summary', 'TEXT DEFAULT NULL', None),
        # security_scan_results — 细粒度字段
        ('security_scan_results', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('security_scan_results', 'subtask_id', 'INT DEFAULT NULL', None),
        ('security_scan_results', 'tool_call_id', 'INT DEFAULT NULL', None),
        ('security_scan_results', 'cwe', 'VARCHAR(50) DEFAULT NULL', None),
        ('security_scan_results', 'cvss', 'FLOAT DEFAULT NULL', None),
        ('security_scan_results', 'confidence', 'VARCHAR(20) DEFAULT NULL', None),
        ('security_scan_results', 'http_method', 'VARCHAR(20) DEFAULT NULL', None),
        ('security_scan_results', 'endpoint', 'VARCHAR(500) DEFAULT NULL', None),
        ('security_scan_results', 'fingerprint', 'VARCHAR(64) DEFAULT NULL', None),
        ('security_scan_results', 'normalized_data', 'JSON DEFAULT NULL', None),
        # security_vulnerabilities — 聚合字段升级
        ('security_vulnerabilities', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('security_vulnerabilities', 'fingerprint', 'VARCHAR(64) DEFAULT NULL', None),
        ('security_vulnerabilities', 'cvss', 'FLOAT DEFAULT NULL', None),
        ('security_vulnerabilities', 'confidence', 'VARCHAR(20) DEFAULT NULL', None),
        ('security_vulnerabilities', 'first_task_id', 'INT DEFAULT NULL', None),
        ('security_vulnerabilities', 'last_task_id', 'INT DEFAULT NULL', None),
        # security_scan_logs — 结构化日志升级
        ('security_scan_logs', 'project_id', 'INT DEFAULT 1', 'INDEX'),
        ('security_scan_logs', 'subtask_id', 'INT DEFAULT NULL', None),
        ('security_scan_logs', 'agent_role', 'VARCHAR(50) DEFAULT NULL', None),
        ('security_scan_logs', 'tool_name', 'VARCHAR(100) DEFAULT NULL', None),
        ('security_scan_logs', 'phase', 'VARCHAR(50) DEFAULT NULL', None),
        ('security_scan_logs', 'payload', 'JSON DEFAULT NULL', None),
    ]

    with engine.connect() as conn:
        for table_name, col_name, col_type, index_type in new_columns:
            try:
                existing_cols = [c['name'] for c in inspector.get_columns(table_name)]
                if col_name not in existing_cols:
                    sql = f"ALTER TABLE `{table_name}` ADD COLUMN `{col_name}` {col_type}"
                    conn.execute(text(sql))
                    conn.commit()
                    print(f"  ✓ 已添加列 {table_name}.{col_name}")
                    
                    # 如果需要添加索引
                    if index_type == 'INDEX':
                        try:
                            index_sql = f"CREATE INDEX idx_{col_name} ON `{table_name}`(`{col_name}`)"
                            conn.execute(text(index_sql))
                            conn.commit()
                            print(f"  ✓ 已添加索引 {table_name}.idx_{col_name}")
                        except Exception as idx_e:
                            # 索引可能已存在，忽略错误
                            pass
            except Exception as e:
                print(f"  ⚠ 添加列 {table_name}.{col_name} 失败: {e}")


class PressTestTask(Base):
    """压测任务表"""
    __tablename__ = 'presstest_tasks'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    project_id = Column(Integer, default=1, index=True, comment='关联项目ID')
    name = Column(String(200), nullable=False, comment='任务名称')
    description = Column(Text, comment='任务描述')
    engine = Column(String(50), comment='使用的引擎: k6/locust/jmeter')
    dsl_config = Column(JSON, comment='压测 DSL 配置')
    status = Column(String(50), default='created', index=True, comment='任务状态')
    result = Column(JSON, comment='压测结果')
    error_message = Column(Text, comment='错误信息')
    duration = Column(Integer, comment='执行时长（秒）')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    started_at = Column(DateTime, comment='开始时间')
    finished_at = Column(DateTime, comment='完成时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


def get_db():
    """获取数据库会话（FastAPI依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 别名（兼容旧代码）
get_db_session = get_db



def get_default_project(db: Session):
    """
    Return the active default project only.

    This is a pure read helper. It must not create, repair, or promote
    projects implicitly. A project is treated as the default only when
    both `is_default = 1` and `is_active = 1`.
    """
    return db.query(Project).filter(
        Project.is_default == 1,
        Project.is_active == 1,
    ).order_by(Project.id.asc()).first()


def get_active_project_by_id(db: Session, project_id: int):
    """
    根据 ID 获取项目（必须是启用状态）
    
    如果项目不存在或未启用，返回 None
    """
    return db.query(Project).filter(
        Project.id == project_id,
        Project.is_active == 1
    ).first()


def resolve_project_context(db: Session, project_id: int = None, required: bool = False):
    """
    Resolve the current project context without mutating project data.

    Priority:
    1. If `project_id` is provided, return that project only when it is active.
    2. Otherwise return the active default project.
    """
    project = get_active_project_by_id(db, project_id) if project_id is not None else get_default_project(db)
    if required and project is None:
        raise ValueError("No active default project is configured")
    return project


if __name__ == "__main__":
    init_db()
