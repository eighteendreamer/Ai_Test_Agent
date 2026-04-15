"""
PressTest Agent 配置管理

从环境变量加载配置
"""
import os
from typing import Optional


class PressTestConfig:
    """PressTest Agent 配置类"""
    
    # ============================================================
    # Docker & Runtime Configuration
    # ============================================================
    USE_DOCKER: bool = os.getenv('PRESSTEST_USE_DOCKER', 'true').lower() == 'true'
    DOCKER_NETWORK: str = os.getenv('PRESSTEST_DOCKER_NETWORK', 'presstest-network')
    ARTIFACT_PATH: str = os.getenv('PRESSTEST_ARTIFACT_PATH', '../save_floder/presstest')
    
    # ============================================================
    # Engine Configuration
    # ============================================================
    # k6 配置
    K6_IMAGE: str = os.getenv('PRESSTEST_K6_IMAGE', 'grafana/k6:latest')
    K6_TIMEOUT: int = int(os.getenv('PRESSTEST_K6_TIMEOUT', '600'))
    
    # Locust 配置
    LOCUST_IMAGE: str = os.getenv('PRESSTEST_LOCUST_IMAGE', 'locustio/locust:latest')
    LOCUST_TIMEOUT: int = int(os.getenv('PRESSTEST_LOCUST_TIMEOUT', '600'))
    LOCUST_WEB_PORT: int = int(os.getenv('PRESSTEST_LOCUST_WEB_PORT', '8089'))
    
    # JMeter 配置
    JMETER_IMAGE: str = os.getenv('PRESSTEST_JMETER_IMAGE', 'justb4/jmeter:latest')
    JMETER_TIMEOUT: int = int(os.getenv('PRESSTEST_JMETER_TIMEOUT', '600'))
    
    # ============================================================
    # Agent Execution Configuration
    # ============================================================
    MAX_CONCURRENT_TASKS: int = int(os.getenv('PRESSTEST_MAX_CONCURRENT_TASKS', '5'))
    TASK_TIMEOUT: int = int(os.getenv('PRESSTEST_TASK_TIMEOUT', '3600'))
    
    # ============================================================
    # Load Test Limits
    # ============================================================
    MAX_VUS: int = int(os.getenv('PRESSTEST_MAX_VUS', '10000'))
    MAX_DURATION: int = int(os.getenv('PRESSTEST_MAX_DURATION', '3600'))
    MAX_RPS: int = int(os.getenv('PRESSTEST_MAX_RPS', '100000'))
    
    # ============================================================
    # Monitoring Configuration
    # ============================================================
    PROMETHEUS_ENABLED: bool = os.getenv('PRESSTEST_PROMETHEUS_ENABLED', 'false').lower() == 'true'
    PROMETHEUS_URL: Optional[str] = os.getenv('PRESSTEST_PROMETHEUS_URL') or None
    GRAFANA_ENABLED: bool = os.getenv('PRESSTEST_GRAFANA_ENABLED', 'false').lower() == 'true'
    GRAFANA_URL: Optional[str] = os.getenv('PRESSTEST_GRAFANA_URL') or None
    
    # ============================================================
    # Real-time Push Configuration
    # ============================================================
    REALTIME_PUSH_ENABLED: bool = os.getenv('PRESSTEST_REALTIME_PUSH_ENABLED', 'true').lower() == 'true'
    
    @classmethod
    def get_docker_config(cls) -> dict:
        """获取 Docker 配置"""
        return {
            'use_docker': cls.USE_DOCKER,
            'network': cls.DOCKER_NETWORK,
            'artifact_path': cls.ARTIFACT_PATH,
        }
    
    @classmethod
    def get_engine_config(cls, engine: str) -> dict:
        """获取引擎配置"""
        configs = {
            'k6': {
                'image': cls.K6_IMAGE,
                'timeout': cls.K6_TIMEOUT,
            },
            'locust': {
                'image': cls.LOCUST_IMAGE,
                'timeout': cls.LOCUST_TIMEOUT,
                'web_port': cls.LOCUST_WEB_PORT,
            },
            'jmeter': {
                'image': cls.JMETER_IMAGE,
                'timeout': cls.JMETER_TIMEOUT,
            },
        }
        return configs.get(engine, {})
    
    @classmethod
    def validate(cls) -> list:
        """验证配置"""
        errors = []
        
        # 检查路径
        if not os.path.exists(cls.ARTIFACT_PATH):
            try:
                os.makedirs(cls.ARTIFACT_PATH, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create artifact path {cls.ARTIFACT_PATH}: {e}")
        
        # 检查限制
        if cls.MAX_VUS <= 0:
            errors.append("PRESSTEST_MAX_VUS must be positive")
        
        if cls.MAX_DURATION <= 0:
            errors.append("PRESSTEST_MAX_DURATION must be positive")
        
        return errors


# 全局配置实例
config = PressTestConfig()
