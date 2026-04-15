"""
k6 压测引擎 Agent

适用场景：HTTP 接口高并发压测
"""
import json
from typing import Dict, Any
from ..models import EngineType, TaskResultResponse, MetricsData, TaskStatus
from .base import BaseEngine


class K6Agent(BaseEngine):
    """k6 压测引擎"""
    
    @property
    def engine_type(self) -> EngineType:
        return EngineType.K6
    
    def generate_script(self) -> str:
        """生成 k6 JavaScript 脚本"""
        req = self.dsl.request
        load = self.dsl.load
        
        # 构建请求头
        headers = json.dumps(req.headers or {}, indent=2)
        
        # 构建请求体
        body = ""
        if req.body:
            if isinstance(req.body, dict):
                body = f"JSON.stringify({json.dumps(req.body)})"
            else:
                body = f"'{req.body}'"
        
        # 构建负载配置
        options = {
            "vus": load.vus,
            "duration": load.duration,
        }
        
        # 添加阈值
        if self.dsl.thresholds:
            thresholds = {}
            if self.dsl.thresholds.max_response_time:
                thresholds["http_req_duration"] = [f"p(95)<{self.dsl.thresholds.max_response_time}"]
            if self.dsl.thresholds.max_error_rate:
                thresholds["http_req_failed"] = [f"rate<{self.dsl.thresholds.max_error_rate / 100}"]
            options["thresholds"] = thresholds
        
        script = f"""
import http from 'k6/http';
import {{ check, sleep }} from 'k6';

export const options = {json.dumps(options, indent=2)};

export default function () {{
    const url = '{req.url}';
    const headers = {headers};
    
    let response;
    """
        
        # 根据 HTTP 方法生成请求
        method = req.method.upper()
        if method == 'GET':
            script += f"""
    response = http.get(url, {{ headers: headers, timeout: '{req.timeout}s' }});
    """
        elif method == 'POST':
            script += f"""
    const body = {body};
    response = http.post(url, body, {{ headers: headers, timeout: '{req.timeout}s' }});
    """
        elif method == 'PUT':
            script += f"""
    const body = {body};
    response = http.put(url, body, {{ headers: headers, timeout: '{req.timeout}s' }});
    """
        elif method == 'DELETE':
            script += f"""
    response = http.del(url, null, {{ headers: headers, timeout: '{req.timeout}s' }});
    """
        
        # 添加检查
        script += """
    check(response, {
        'status is 200': (r) => r.status === 200,
        'response time < 500ms': (r) => r.timings.duration < 500,
    });
    """
        
        # 添加思考时间
        if self.dsl.think_time:
            script += f"""
    sleep({self.dsl.think_time});
    """
        
        script += """
}
"""
        
        return script
    
    def execute(self, script: str) -> Dict[str, Any]:
        """执行 k6 脚本"""
        import subprocess
        import tempfile
        import os
        
        # 保存脚本到临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(script)
            script_path = f.name
        
        try:
            # 执行 k6
            cmd = ['k6', 'run', '--out', 'json=' + script_path + '.json', script_path]
            
            self.logger.info(f"执行命令: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.dsl.load.duration  # 使用 DSL 中的持续时间作为超时
            )
            
            # 读取 JSON 输出
            json_output_path = script_path + '.json'
            if os.path.exists(json_output_path):
                with open(json_output_path, 'r') as f:
                    metrics = [json.loads(line) for line in f if line.strip()]
            else:
                metrics = []
            
            return {
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode,
                'metrics': metrics,
            }
            
        finally:
            # 清理临时文件
            if os.path.exists(script_path):
                os.unlink(script_path)
            json_output_path = script_path + '.json'
            if os.path.exists(json_output_path):
                os.unlink(json_output_path)
    
    def parse_result(self, raw_result: Dict[str, Any]) -> TaskResultResponse:
        """解析 k6 结果"""
        metrics = raw_result.get('metrics', [])
        
        # 提取汇总指标
        total_requests = 0
        total_errors = 0
        response_times = []
        
        for metric in metrics:
            if metric.get('type') == 'Point':
                metric_name = metric.get('metric')
                value = metric.get('data', {}).get('value', 0)
                
                if metric_name == 'http_reqs':
                    total_requests += 1
                elif metric_name == 'http_req_failed' and value > 0:
                    total_errors += 1
                elif metric_name == 'http_req_duration':
                    response_times.append(value)
        
        # 计算统计指标
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        response_times_sorted = sorted(response_times)
        p95_index = int(len(response_times_sorted) * 0.95)
        p99_index = int(len(response_times_sorted) * 0.99)
        p95_response_time = response_times_sorted[p95_index] if response_times_sorted else 0
        p99_response_time = response_times_sorted[p99_index] if response_times_sorted else 0
        
        error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0
        
        # 解析持续时间
        duration_str = self.dsl.load.duration
        duration = self._parse_duration(duration_str)
        
        avg_rps = total_requests / duration if duration > 0 else 0
        
        return TaskResultResponse(
            task_id=self.task_id,
            status=TaskStatus.FINISHED if raw_result['returncode'] == 0 else TaskStatus.FAILED,
            total_requests=total_requests,
            total_errors=total_errors,
            duration=duration,
            avg_rps=avg_rps,
            max_rps=avg_rps,  # k6 不提供实时 RPS，使用平均值
            avg_response_time=avg_response_time,
            p95_response_time=p95_response_time,
            p99_response_time=p99_response_time,
            error_rate=error_rate,
            metrics=[],  # 简化版本，不返回时序数据
        )
    
    def _parse_duration(self, duration_str: str) -> int:
        """解析持续时间字符串为秒数"""
        duration_str = duration_str.strip().lower()
        
        if duration_str.endswith('s'):
            return int(duration_str[:-1])
        elif duration_str.endswith('m'):
            return int(duration_str[:-1]) * 60
        elif duration_str.endswith('h'):
            return int(duration_str[:-1]) * 3600
        else:
            return int(duration_str)
    
    def get_docker_command(self, script_path: str) -> list:
        """获取 k6 Docker 命令"""
        from ..config import config
        
        return [
            'docker', 'run', '--rm',
            '-v', f'{script_path}:/scripts/test.js',
            config.K6_IMAGE,
            'run', '/scripts/test.js'
        ]
