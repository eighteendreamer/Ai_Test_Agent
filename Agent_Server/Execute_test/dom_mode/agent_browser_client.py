"""
agent-browser CLI 客户端封装

通过 subprocess 调用 agent-browser 二进制，支持 batch 模式批量执行命令。
agent-browser 是一个 Rust 编写的 headless browser CLI，通过 CDP 协议控制 Chrome。
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# agent-browser 可执行文件路径（优先从环境变量读取）
_AGENT_BROWSER_BIN = os.getenv("AGENT_BROWSER_BIN", "agent-browser")


def _find_binary() -> str:
    """查找 agent-browser 可执行文件"""
    # 1. 环境变量指定
    env_path = os.getenv("AGENT_BROWSER_BIN", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2. PATH 中查找
    found = shutil.which("agent-browser")
    if found:
        return found

    # 3. 项目内 bin 目录（相对路径）
    project_bin = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..", "agent-browser", "bin", "agent-browser"
    )
    if os.path.isfile(project_bin):
        return os.path.normpath(project_bin)

    # 4. Windows 可执行文件后缀
    for suffix in [".exe", ".cmd"]:
        candidate = shutil.which(f"agent-browser{suffix}")
        if candidate:
            return candidate

    return "agent-browser"  # 最终回退，让系统自己找


class AgentBrowserClient:
    """
    agent-browser CLI 客户端

    封装 subprocess 调用，提供：
    - run_command(): 执行单条命令
    - run_batch(): 批量执行命令（避免进程启动开销）
    - snapshot(): 获取无障碍树
    - get_cdp_url(): 获取当前 CDP WebSocket URL
    """

    def __init__(
        self,
        cdp_url: Optional[str] = None,
        cdp_port: Optional[int] = None,
        session: Optional[str] = None,
        timeout: int = 30,
    ):
        """
        Args:
            cdp_url:  连接已有 Chrome 的 CDP WebSocket URL
            cdp_port: 连接已有 Chrome 的 CDP 端口（与 cdp_url 二选一）
            session:  agent-browser session 名称（隔离多实例）
            timeout:  单条命令超时秒数
        """
        self.binary = _find_binary()
        self.cdp_url = cdp_url
        self.cdp_port = cdp_port
        self.session = session
        self.timeout = timeout
        logger.info(f"[AgentBrowser] 使用二进制: {self.binary}")

    def _base_args(self) -> List[str]:
        """构建基础参数（CDP 连接 + session）"""
        args = [self.binary]
        if self.cdp_url:
            args += ["--cdp", self.cdp_url]
        elif self.cdp_port:
            args += ["--cdp", str(self.cdp_port)]
        if self.session:
            args += ["--session", self.session]
        return args

    async def run_command(self, *cmd_args: str, json_output: bool = True) -> Dict[str, Any]:
        """
        执行单条 agent-browser 命令。

        Args:
            *cmd_args: 命令参数，如 ("snapshot", "-i") 或 ("click", "@e1")
            json_output: 是否追加 --json 标志

        Returns:
            解析后的 JSON 结果，或 {"success": False, "error": "..."} 
        """
        args = self._base_args() + list(cmd_args)
        if json_output and "--json" not in args:
            args.append("--json")

        logger.debug(f"[AgentBrowser] 执行: {' '.join(args)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.warning(
                    f"[AgentBrowser] 命令失败 (exit={proc.returncode}): {stderr_text}"
                )
                return {"success": False, "error": stderr_text or f"exit code {proc.returncode}"}

            if not stdout_text:
                return {"success": True, "data": None}

            if json_output:
                try:
                    return json.loads(stdout_text)
                except json.JSONDecodeError:
                    return {"success": True, "data": stdout_text}

            return {"success": True, "data": stdout_text}

        except asyncio.TimeoutError:
            logger.error(f"[AgentBrowser] 命令超时 ({self.timeout}s): {' '.join(args)}")
            return {"success": False, "error": f"命令超时 ({self.timeout}s)"}
        except FileNotFoundError:
            logger.error(f"[AgentBrowser] 找不到可执行文件: {self.binary}")
            return {"success": False, "error": f"agent-browser 未安装或路径错误: {self.binary}"}
        except Exception as e:
            logger.error(f"[AgentBrowser] 执行异常: {e}")
            return {"success": False, "error": str(e)}

    async def run_batch(self, commands: List[List[str]], bail_on_error: bool = False) -> Dict[str, Any]:
        """
        批量执行命令（通过 stdin 传入 JSON 数组）。

        Args:
            commands: 命令列表，每条命令是字符串列表，如 [["open", "https://..."], ["snapshot", "-i"]]
            bail_on_error: 遇到错误是否立即停止

        Returns:
            {"success": bool, "results": [...], "error": str | None}
        """
        args = self._base_args() + ["batch", "--json"]
        if bail_on_error:
            args.append("--bail")

        commands_json = json.dumps(commands, ensure_ascii=False)
        logger.debug(f"[AgentBrowser] batch 执行 {len(commands)} 条命令")

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=commands_json.encode("utf-8")),
                timeout=self.timeout * len(commands),
            )
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.warning(f"[AgentBrowser] batch 失败: {stderr_text}")
                return {"success": False, "error": stderr_text, "results": []}

            if not stdout_text:
                return {"success": True, "results": []}

            try:
                result = json.loads(stdout_text)
                return result if isinstance(result, dict) else {"success": True, "results": result}
            except json.JSONDecodeError:
                return {"success": True, "results": [], "raw": stdout_text}

        except asyncio.TimeoutError:
            return {"success": False, "error": "batch 执行超时", "results": []}
        except FileNotFoundError:
            return {"success": False, "error": f"agent-browser 未安装: {self.binary}", "results": []}
        except Exception as e:
            return {"success": False, "error": str(e), "results": []}

    async def snapshot(self, interactive_only: bool = True, compact: bool = True) -> Dict[str, Any]:
        """
        获取当前页面的无障碍树快照。

        Args:
            interactive_only: 只返回交互元素（-i）
            compact: 移除空结构元素（-c）

        Returns:
            {"success": bool, "data": {"snapshot": str, "refs": {...}}}
        """
        args = ["snapshot"]
        if interactive_only:
            args.append("-i")
        if compact:
            args.append("-c")
        return await self.run_command(*args)

    async def navigate(self, url: str) -> Dict[str, Any]:
        """导航到指定 URL"""
        return await self.run_command("open", url)

    async def click(self, ref_or_selector: str) -> Dict[str, Any]:
        """点击元素（支持 @eN ref 或 CSS 选择器）"""
        return await self.run_command("click", ref_or_selector)

    async def fill(self, ref_or_selector: str, value: str) -> Dict[str, Any]:
        """填写输入框"""
        return await self.run_command("fill", ref_or_selector, value)

    async def get_text(self, ref_or_selector: str) -> Dict[str, Any]:
        """获取元素文本"""
        return await self.run_command("get", "text", ref_or_selector)

    async def get_url(self) -> str:
        """获取当前页面 URL"""
        result = await self.run_command("get", "url")
        if result.get("success"):
            data = result.get("data", "")
            return data if isinstance(data, str) else str(data)
        return ""

    async def eval_js(self, script: str) -> Dict[str, Any]:
        """执行 JavaScript"""
        return await self.run_command("eval", script)

    async def wait(self, ms_or_selector: str) -> Dict[str, Any]:
        """等待（毫秒数或选择器可见）"""
        return await self.run_command("wait", str(ms_or_selector))

    @classmethod
    def from_cdp_url(cls, cdp_url: str, **kwargs) -> "AgentBrowserClient":
        """从 CDP WebSocket URL 创建客户端"""
        return cls(cdp_url=cdp_url, **kwargs)

    @classmethod
    def from_cdp_port(cls, port: int, **kwargs) -> "AgentBrowserClient":
        """从 CDP 端口创建客户端"""
        return cls(cdp_port=port, **kwargs)
