"""
平台客户端工厂
根据 platform_id 和配置信息，实例化对应的平台客户端
"""
import json
from typing import Any

from Project_manage.platforms.zentao.client import ZentaoClient
from Project_manage.platforms.pingcode.client import PingCodeClient
from Project_manage.platforms.jira.client import JiraClient
from Project_manage.platforms.tapd.client import TapdClient
from Project_manage.platforms.ones.client import OnesClient
from Project_manage.platforms.yunxiao.client import YunxiaoClient
from Project_manage.platforms.worktile.client import WorktileClient
from Project_manage.platforms.asana.client import AsanaClient
from Project_manage.platforms.clickup.client import ClickUpClient


def build_client(platform_id: str, config: dict) -> Any:
    """
    根据 platform_id 构建对应客户端实例。

    config 字段说明（来自 ProjectPlatformConfig）:
        base_url, account, api_token, api_version, extra_config (JSON str)
    """
    extra: dict = {}
    if config.get("extra_config"):
        try:
            extra = json.loads(config["extra_config"])
        except (ValueError, TypeError):
            extra = {}

    pid = platform_id.lower()

    if pid == "zentao":
        return ZentaoClient(
            base_url=config["base_url"],
            account=config.get("account", ""),
            password=extra.get("password", ""),
        )

    if pid == "pingcode":
        return PingCodeClient(
            client_id=config.get("account", ""),
            client_secret=config.get("api_token", ""),
            access_token=extra.get("access_token"),
        )

    if pid == "jira":
        return JiraClient(
            base_url=config["base_url"],
            account=config.get("account", ""),
            api_token=config.get("api_token", ""),
            api_version=config.get("api_version", "3"),
        )

    if pid == "tapd":
        return TapdClient(
            api_user=config.get("account", ""),
            api_password=config.get("api_token", ""),
        )

    if pid == "ones":
        return OnesClient(
            base_url=config["base_url"],
            account=config.get("account", ""),
            password=extra.get("password", ""),
            api_token=config.get("api_token", ""),
        )

    if pid == "yunxiao":
        return YunxiaoClient(
            api_token=config.get("api_token", ""),
            organization_id=extra.get("organization_id", ""),
        )

    if pid == "worktile":
        return WorktileClient(
            client_id=config.get("account", ""),
            client_secret=config.get("api_token", ""),
            access_token=extra.get("access_token"),
        )

    if pid == "asana":
        return AsanaClient(api_token=config.get("api_token", ""))

    if pid == "clickup":
        return ClickUpClient(api_token=config.get("api_token", ""))

    raise ValueError(f"不支持的平台: {platform_id}")


def test_platform_connection(platform_id: str, config: dict) -> dict:
    """构建客户端并执行连接测试，自动关闭连接"""
    client = build_client(platform_id, config)
    try:
        return client.test_connection()
    finally:
        client.close()
