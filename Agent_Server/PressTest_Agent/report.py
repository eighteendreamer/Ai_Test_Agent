"""
压测报告生成器

生成 HTML 格式的压测报告，包含：
- 测试概览
- 性能指标
- 图表展示
- AI 分析
- 优化建议

作者: 程序员Eighteen
"""
from typing import Dict, Any, Optional
from datetime import datetime
import logging

from .models import TaskResultResponse, PressTestDSL

logger = logging.getLogger(__name__)


class PressTestReportGenerator:
    """压测报告生成器"""
    
    def __init__(self, llm_provider=None):
        """
        初始化报告生成器
        
        Args:
            llm_provider: LLM Provider 实例（用于生成更详细的分析）
        """
        self.llm_provider = llm_provider
    
    def generate_html_report(
        self,
        task_name: str,
        dsl: PressTestDSL,
        result: TaskResultResponse,
        task_id: int,
        created_at: datetime,
        finished_at: datetime
    ) -> str:
        """
        生成 HTML 格式的压测报告
        
        Args:
            task_name: 任务名称
            dsl: 压测 DSL
            result: 任务结果
            task_id: 任务 ID
            created_at: 创建时间
            finished_at: 完成时间
            
        Returns:
            HTML 报告内容
        """
        # 计算执行时长
        duration_seconds = (finished_at - created_at).total_seconds()
        duration_str = self._format_duration(duration_seconds)
        
        # 性能评分
        score = self._calculate_score(result)
        score_color = self._get_score_color(score)
        score_text = self._get_score_text(score)
        
        # 生成 HTML
        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>压测报告 - {task_name}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 40px 20px;
            color: #333;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }}
        
        .header {{
            background: linear-gradient(135deg, #00a870 0%, #00c896 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 32px;
            margin-bottom: 10px;
        }}
        
        .header .subtitle {{
            font-size: 16px;
            opacity: 0.9;
        }}
        
        .score-section {{
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        
        .score-circle {{
            width: 150px;
            height: 150px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.2);
            margin: 0 auto 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 48px;
            font-weight: bold;
            border: 5px solid rgba(255, 255, 255, 0.5);
        }}
        
        .score-text {{
            font-size: 24px;
            font-weight: 600;
        }}
        
        .content {{
            padding: 40px;
        }}
        
        .section {{
            margin-bottom: 40px;
        }}
        
        .section-title {{
            font-size: 24px;
            font-weight: 600;
            color: #00a870;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #00a870;
        }}
        
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .info-card {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 12px;
            border-left: 4px solid #00a870;
        }}
        
        .info-label {{
            font-size: 14px;
            color: #666;
            margin-bottom: 8px;
        }}
        
        .info-value {{
            font-size: 20px;
            font-weight: 600;
            color: #333;
        }}
        
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }}
        
        .metric-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 24px;
            border-radius: 12px;
            text-align: center;
        }}
        
        .metric-label {{
            font-size: 14px;
            opacity: 0.9;
            margin-bottom: 8px;
        }}
        
        .metric-value {{
            font-size: 32px;
            font-weight: bold;
        }}
        
        .metric-unit {{
            font-size: 14px;
            opacity: 0.8;
        }}
        
        .alert {{
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
        }}
        
        .alert-info {{
            background: #e3f2fd;
            border-left: 4px solid #2196f3;
            color: #1565c0;
        }}
        
        .alert-success {{
            background: #e8f5e9;
            border-left: 4px solid #4caf50;
            color: #2e7d32;
        }}
        
        .alert-warning {{
            background: #fff3e0;
            border-left: 4px solid #ff9800;
            color: #e65100;
        }}
        
        .recommendations {{
            list-style: none;
        }}
        
        .recommendations li {{
            padding: 15px;
            margin-bottom: 10px;
            background: #f8f9fa;
            border-radius: 8px;
            border-left: 4px solid #ffc107;
        }}
        
        .recommendations li:before {{
            content: "💡 ";
            margin-right: 8px;
        }}
        
        .footer {{
            background: #f8f9fa;
            padding: 30px;
            text-align: center;
            color: #666;
            font-size: 14px;
        }}
        
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        
        .badge-success {{
            background: #4caf50;
            color: white;
        }}
        
        .badge-warning {{
            background: #ff9800;
            color: white;
        }}
        
        .badge-error {{
            background: #f44336;
            color: white;
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="header">
            <h1>🚀 压测报告</h1>
            <div class="subtitle">{task_name}</div>
        </div>
        
        <!-- 性能评分 -->
        <div class="score-section">
            <div class="score-circle" style="background: {score_color};">
                {score}
            </div>
            <div class="score-text">{score_text}</div>
        </div>
        
        <!-- 内容区域 -->
        <div class="content">
            <!-- 测试概览 -->
            <div class="section">
                <h2 class="section-title">📊 测试概览</h2>
                <div class="info-grid">
                    <div class="info-card">
                        <div class="info-label">任务 ID</div>
                        <div class="info-value">#{task_id}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">压测引擎</div>
                        <div class="info-value">{dsl.engine or '自动选择'}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">测试类型</div>
                        <div class="info-value">{dsl.type.value}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">执行时长</div>
                        <div class="info-value">{duration_str}</div>
                    </div>
                </div>
                
                <div class="info-grid">
                    <div class="info-card">
                        <div class="info-label">目标 URL</div>
                        <div class="info-value" style="font-size: 14px; word-break: break-all;">{dsl.request.url}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">请求方法</div>
                        <div class="info-value">{dsl.request.method}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">虚拟用户数</div>
                        <div class="info-value">{dsl.load.vus}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">持续时间</div>
                        <div class="info-value">{dsl.load.duration}</div>
                    </div>
                </div>
            </div>
            
            <!-- 性能指标 -->
            <div class="section">
                <h2 class="section-title">📈 性能指标</h2>
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-label">总请求数</div>
                        <div class="metric-value">{result.total_requests:,}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">平均 RPS</div>
                        <div class="metric-value">{result.avg_rps:.2f}</div>
                        <div class="metric-unit">请求/秒</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">平均响应时间</div>
                        <div class="metric-value">{result.avg_response_time:.2f}</div>
                        <div class="metric-unit">毫秒</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">P95 响应时间</div>
                        <div class="metric-value">{result.p95_response_time:.2f}</div>
                        <div class="metric-unit">毫秒</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">P99 响应时间</div>
                        <div class="metric-value">{result.p99_response_time:.2f}</div>
                        <div class="metric-unit">毫秒</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">错误率</div>
                        <div class="metric-value">{result.error_rate:.2f}%</div>
                    </div>
                </div>
            </div>
            
            <!-- AI 分析 -->
            {self._generate_ai_analysis_section(result)}
            
            <!-- 优化建议 -->
            {self._generate_recommendations_section(result)}
        </div>
        
        <!-- 页脚 -->
        <div class="footer">
            <p>报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>由 AI Test Agent - PressTest Agent 自动生成</p>
        </div>
    </div>
</body>
</html>
"""
        
        return html
    
    def _format_duration(self, seconds: float) -> str:
        """格式化时长"""
        if seconds < 60:
            return f"{seconds:.1f} 秒"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f} 分钟"
        else:
            hours = seconds / 3600
            return f"{hours:.1f} 小时"
    
    def _calculate_score(self, result: TaskResultResponse) -> int:
        """
        计算性能评分（0-100）
        
        评分标准：
        - 错误率 < 1%: +30 分
        - 平均响应时间 < 100ms: +25 分
        - P95 响应时间 < 200ms: +20 分
        - P99 响应时间 < 500ms: +15 分
        - RPS > 100: +10 分
        """
        score = 0
        
        # 错误率评分
        if result.error_rate < 0.1:
            score += 30
        elif result.error_rate < 1:
            score += 25
        elif result.error_rate < 5:
            score += 15
        elif result.error_rate < 10:
            score += 5
        
        # 平均响应时间评分
        if result.avg_response_time < 50:
            score += 25
        elif result.avg_response_time < 100:
            score += 20
        elif result.avg_response_time < 200:
            score += 15
        elif result.avg_response_time < 500:
            score += 10
        elif result.avg_response_time < 1000:
            score += 5
        
        # P95 响应时间评分
        if result.p95_response_time < 100:
            score += 20
        elif result.p95_response_time < 200:
            score += 15
        elif result.p95_response_time < 500:
            score += 10
        elif result.p95_response_time < 1000:
            score += 5
        
        # P99 响应时间评分
        if result.p99_response_time < 200:
            score += 15
        elif result.p99_response_time < 500:
            score += 10
        elif result.p99_response_time < 1000:
            score += 5
        
        # RPS 评分
        if result.avg_rps > 1000:
            score += 10
        elif result.avg_rps > 500:
            score += 8
        elif result.avg_rps > 100:
            score += 5
        elif result.avg_rps > 50:
            score += 3
        
        return min(score, 100)
    
    def _get_score_color(self, score: int) -> str:
        """获取评分颜色"""
        if score >= 90:
            return "rgba(76, 175, 80, 0.8)"  # 绿色
        elif score >= 70:
            return "rgba(255, 193, 7, 0.8)"  # 黄色
        else:
            return "rgba(244, 67, 54, 0.8)"  # 红色
    
    def _get_score_text(self, score: int) -> str:
        """获取评分文本"""
        if score >= 90:
            return "优秀 - 性能表现出色"
        elif score >= 80:
            return "良好 - 性能表现不错"
        elif score >= 70:
            return "中等 - 性能尚可"
        elif score >= 60:
            return "及格 - 性能一般"
        else:
            return "较差 - 需要优化"
    
    def _generate_ai_analysis_section(self, result: TaskResultResponse) -> str:
        """生成 AI 分析部分"""
        if not result.ai_analysis:
            return ""
        
        return f"""
            <div class="section">
                <h2 class="section-title">🤖 AI 分析</h2>
                <div class="alert alert-info">
                    {result.ai_analysis}
                </div>
            </div>
        """
    
    def _generate_recommendations_section(self, result: TaskResultResponse) -> str:
        """生成优化建议部分"""
        if not result.recommendations or len(result.recommendations) == 0:
            return ""
        
        recommendations_html = "\n".join([
            f"<li>{rec}</li>"
            for rec in result.recommendations
        ])
        
        return f"""
            <div class="section">
                <h2 class="section-title">💡 优化建议</h2>
                <ul class="recommendations">
                    {recommendations_html}
                </ul>
            </div>
        """
