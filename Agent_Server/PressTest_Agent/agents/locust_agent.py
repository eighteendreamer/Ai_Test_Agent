"""
Locust 压测引擎 Agent

适用场景：业务流程压测（登录/下单等复杂场景）
"""
from typing import Dict, Any
from ..models import EngineType, TaskResultResponse, TaskStatus
from .base import BaseEngine


class LocustAgent(BaseEngine):
    """Locust 压测引擎"""
    
    @property
    def engine_type(self) -> EngineType:
        return EngineType.LOCUST
    
    def generate_script(self) -> str:
        """生成 Locust Python 脚本"""
        req = self.dsl.request
        load = self.dsl.load
        
        script = f"""
from locust import HttpUser, task, between

class {self.dsl.name.replace('-', '_').title()}User(HttpUser):
    wait_time = between(1, {self.dsl.think_time or 2})
    
    @task
    def test_request(self):
        headers = {req.headers or {}}
        """
        
        method = req.method.lower()
        if method == 'get':
            script += f"""
        self.client.get("{req.url}", headers=headers)
        """
        elif method == 'post':
            script += f"""
        self.client.post("{req.url}", json={req.body}, headers=headers)
        """
        elif method == 'put':
            script += f"""
        self.client.put("{req.url}", json={req.body}, headers=headers)
        """
        elif method == 'delete':
            script += f"""
        self.client.delete("{req.url}", headers=headers)
        """
        
        return script
    
    def execute(self, script: str) -> Dict[str, Any]:
        """执行 Locust 脚本"""
        # TODO: 实现 Locust 执行逻辑
        # 需要启动 Locust master/worker，收集指标
        self.logger.warning("Locust 执行功能待实现")
        return {
            'status': 'not_implemented',
            'message': 'Locust execution not yet implemented'
        }
    
    def parse_result(self, raw_result: Dict[str, Any]) -> TaskResultResponse:
        """解析 Locust 结果"""
        # TODO: 解析 Locust 结果
        return TaskResultResponse(
            task_id=self.task_id,
            status=TaskStatus.FAILED,
            total_requests=0,
            total_errors=0,
            duration=0,
            avg_rps=0,
            max_rps=0,
            avg_response_time=0,
            p95_response_time=0,
            p99_response_time=0,
            error_rate=0,
            metrics=[],
        )
    
    def get_docker_command(self, script_path: str) -> list:
        """获取 Locust Docker 命令"""
        from ..config import config
        
        return [
            'docker', 'run', '--rm',
            '-v', f'{script_path}:/mnt/locust',
            '-p', f'{config.LOCUST_WEB_PORT}:8089',
            config.LOCUST_IMAGE,
            '-f', '/mnt/locust/locustfile.py',
            '--headless',
            '-u', str(self.dsl.load.vus),
            '-r', '10',  # spawn rate
            '--run-time', self.dsl.load.duration,
        ]
