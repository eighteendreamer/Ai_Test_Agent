"""
Browser-Use 测试执行服务

提供使用 browser-use 执行测试的核心功能

作者: 程序员Eighteen
"""
import asyncio
import json
import time
import os
import re
import uuid
import subprocess
import platform
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session
from dotenv import load_dotenv

# 加载环境变量 - .env 文件在 Agent_Server 目录下
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)


def find_chrome_path() -> Optional[str]:
    """查找 Chrome 浏览器路径"""
    system = platform.system()
    
    if system == 'Windows':
        paths = [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        ]
    elif system == 'Darwin':  # macOS
        paths = [
            '/Applications/Google Chrome.src/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.src/Contents/MacOS/Chromium',
        ]
    else:  # Linux
        paths = [
            '/usr/bin/google-chrome',
            '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium',
            '/usr/bin/chromium-browser',
        ]
    
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def check_chrome_running() -> bool:
    """检查是否有 Chrome 进程在运行"""
    try:
        if platform.system() == 'Windows':
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq chrome.exe'], 
                                  capture_output=True, text=True)
            return 'chrome.exe' in result.stdout.lower()
        else:
            result = subprocess.run(['pgrep', '-x', 'chrome'], capture_output=True)
            return result.returncode == 0
    except Exception:
        return False


def kill_chrome_processes() -> bool:
    """关闭所有 Chrome 进程"""
    try:
        if platform.system() == 'Windows':
            # 使用 taskkill 关闭 Chrome 进程
            result = subprocess.run(
                ['taskkill', '/F', '/IM', 'chrome.exe', '/T'],
                capture_output=True, text=True
            )
            return result.returncode == 0
        else:
            subprocess.run(['pkill', '-9', 'chrome'], capture_output=True)
            return True
    except Exception:
        return False


def create_temp_user_data_dir() -> str:
    """创建临时用户数据目录"""
    import tempfile
    temp_dir = tempfile.mkdtemp(prefix='browser_use_')
    return temp_dir


async def start_chrome_with_debugging(chrome_path: str, headless: bool = False) -> tuple[subprocess.Popen, int, str]:
    """
    手动启动 Chrome 并启用远程调试
    
    Returns:
        (process, port, user_data_dir)
    """
    import socket
    import tempfile
    import aiohttp
    
    # 找一个可用端口
    def find_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]
    
    port = find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix='chrome_debug_')
    
    # 构建启动参数
    args = [
        chrome_path,
        f'--remote-debugging-port={port}',
        f'--user-data-dir={user_data_dir}',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-background-networking',
        '--disable-client-side-phishing-detection',
        '--disable-default-apps',
        '--disable-extensions',
        '--disable-hang-monitor',
        '--disable-popup-blocking',
        '--disable-prompt-on-repost',
        '--disable-sync',
        '--disable-translate',
        '--metrics-recording-only',
        '--safebrowsing-disable-auto-update',
        '--password-store=basic',
    ]
    
    if headless:
        args.append('--headless=new')
    
    # 启动 Chrome
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    # 等待 CDP 端口准备好，获取 WebSocket URL
    http_url = f'http://127.0.0.1:{port}'
    max_wait = 30
    start_time = asyncio.get_event_loop().time()
    
    while asyncio.get_event_loop().time() - start_time < max_wait:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'{http_url}/json/version', timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ws_url = data.get('webSocketDebuggerUrl')
                        if ws_url:
                            print(f"[Chrome] ✅ CDP 准备就绪: {ws_url}")
                            # 返回 WebSocket URL，而不是 HTTP URL
                            return process, port, user_data_dir, ws_url
        except Exception:
            pass
        await asyncio.sleep(0.2)
    
    # 超时，关闭进程
    process.terminate()
    raise TimeoutError(f"Chrome 启动超时，CDP 端口 {port} 未响应")

# 截图保存目录
BUG_IMG_SAVE_PATH = Path(os.getenv('BUG_IMG_PATH', '../save_floder/bug_img'))
BUG_IMG_SAVE_PATH.mkdir(parents=True, exist_ok=True)


def generate_batch_id(mode: str = 'single') -> str:
    """生成执行批次号"""
    prefix = 'SINGLE' if mode == 'single' else 'BATCH'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_id = uuid.uuid4().hex[:8].upper()
    return f"{prefix}_{timestamp}_{unique_id}"


class BrowserUseService:
    """Browser-Use 测试执行服务"""
    
    @staticmethod
    async def test_browser_connection() -> Dict[str, Any]:
        """
        测试浏览器连接是否正常
        
        Returns:
            测试结果
        """
        chrome_process = None
        try:
            try:
                from browser_use import BrowserSession
            except ImportError:
                # 兼容旧版本
                from browser_use.browser import BrowserSession
            
            # 检测 Chrome 路径
            chrome_path = os.getenv('BROWSER_PATH', '').strip() or find_chrome_path()
            headless = os.getenv('HEADLESS', 'false').lower() == 'true'
            
            print(f"[BrowserUse] 🔍 测试浏览器连接...")
            print(f"[BrowserUse] 📍 Chrome路径: {chrome_path or '自动检测'}")
            print(f"[BrowserUse] 🔧 Headless模式: {headless}")
            
            if not chrome_path:
                return {
                    "success": False,
                    "message": "未找到 Chrome 浏览器",
                    "suggestions": [
                        "1. 确保已安装 Chrome 或 Edge 浏览器",
                        "2. 在 .env 中设置 BROWSER_PATH 指向浏览器可执行文件",
                    ]
                }
            
            # 手动启动 Chrome
            print(f"[BrowserUse] 🚀 手动启动 Chrome...")
            chrome_process, port, temp_dir, ws_url = await start_chrome_with_debugging(
                chrome_path, headless
            )
            cdp_url = ws_url
            print(f"[BrowserUse] ✅ Chrome 已启动，WebSocket URL: {cdp_url}")
            
            # 创建 BrowserSession 连接到已启动的 Chrome
            browser_session = BrowserSession(cdp_url=cdp_url)
            
            # 尝试启动连接
            print(f"[BrowserUse] 🔗 连接到 Chrome...")
            await browser_session.start()
            
            print(f"[BrowserUse] ✅ 浏览器连接成功!")
            print(f"[BrowserUse] 📊 WebSocket URL: {browser_session.cdp_url}")
            
            # 关闭浏览器
            await browser_session.stop()
            print(f"[BrowserUse] ✓ BrowserSession 已关闭")
            
            # 关闭 Chrome 进程
            chrome_process.terminate()
            chrome_process.wait(timeout=5)
            print(f"[BrowserUse] ✓ Chrome 进程已关闭")
            
            # 清理临时目录
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            
            return {
                "success": True,
                "message": "浏览器连接测试成功",
                "data": {
                    "chrome_path": chrome_path,
                    "headless": headless,
                    "cdp_url": cdp_url,
                    "ws_url": browser_session.cdp_url
                }
            }
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"[BrowserUse] ❌ 浏览器连接测试失败: {e}")
            print(f"[BrowserUse] 错误详情:\n{error_trace}")
            
            # 清理 Chrome 进程
            if chrome_process:
                try:
                    chrome_process.terminate()
                except Exception:
                    pass
            
            return {
                "success": False,
                "message": f"浏览器连接测试失败: {str(e)}",
                "error_details": error_trace,
                "suggestions": [
                    "1. 确保已安装 Chrome 或 Edge 浏览器",
                    "2. 在 .env 中设置 BROWSER_PATH 指向浏览器可执行文件",
                    "3. 尝试关闭所有 Chrome 进程后重试",
                    "4. 检查是否有防火墙或安全软件阻止",
                ]
            }
    
    @staticmethod
    async def execute_test_with_browser_use(
        test_case_id: int,
        db: Session,
        headless: bool = None,
        max_steps: int = None,
        use_vision: bool = None,
        max_actions: int = None,
        skip_report: bool = False,
        task_id: int = None
    ) -> Dict[str, Any]:
        """
        使用 browser-use Agent 执行测试
        
        Args:
            test_case_id: 测试用例 ID
            db: 数据库会话
            headless: 无头模式
            max_steps: 最大执行步数
            use_vision: 是否启用视觉
            max_actions: 每步最大动作数
        
        Returns:
            执行结果
        """
        from database.connection import TestCase, ExecutionBatch, TestRecord
        
        # 从环境变量读取默认配置
        if headless is None:
            headless = os.getenv('HEADLESS', 'false').lower() == 'true'
        if max_steps is None:
            max_steps = int(os.getenv('MAX_STEPS', '100'))
        if use_vision is None:
            use_vision = os.getenv('LLM_USE_VISION', 'false').lower() == 'true'
        if max_actions is None:
            max_actions = int(os.getenv('MAX_ACTIONS', '10'))
        
        # 获取测试用例
        test_case = db.query(TestCase).filter(TestCase.id == test_case_id).first()
        
        if not test_case:
            return {
                "success": False,
                "message": f"测试用例 ID {test_case_id} 不存在"
            }
        
        # 构建任务描述
        task_description = BrowserUseService._build_task_description(test_case)
        
        start_time = time.time()
        
        # 创建执行记录
        is_batch = skip_report  # skip_report=True 表示批量模式
        batch_id = generate_batch_id('batch' if is_batch else 'single')
        
        execution_batch = ExecutionBatch(
            execution_case_id=test_case_id,
            batch=batch_id
        )
        db.add(execution_batch)
        db.flush()
        
        test_record = TestRecord(
            batch_id=execution_batch.id,
            test_case_id=test_case_id,
            execution_mode='批量' if is_batch else '单量',
            total_cases=1,
            passed_cases=0,
            failed_cases=0,
            execution_log=json.dumps({"status": "执行中"}, ensure_ascii=False),
            status="running",
            duration=0,
            test_steps=0
        )
        db.add(test_record)
        db.commit()
        db.refresh(test_record)
        
        print(f"[BrowserUse] ✓ 创建执行记录: {batch_id}, ID: {test_record.id}")
        
        try:
            # 获取激活的 LLM
            from llm import get_active_llm_config, get_active_browser_use_llm
            from llm.config import supports_structured_output
            
            llm_config = get_active_llm_config()
            provider = llm_config['provider']
            model_name = llm_config['model_name']
            
            print(f"[BrowserUse] 🔧 使用模型: {model_name} (provider: {provider})")
            
            # 获取 Browser-Use LLM
            llm = get_active_browser_use_llm()
            
            # 导入 browser-use
            try:
                from browser_use import Agent, BrowserSession, BrowserProfile
                from browser_use.tools.service import Tools
            except ImportError:
                return {
                    "success": False,
                    "message": "browser-use 库未安装，请运行: pip install browser-use"
                }
            
            # 浏览器配置参数
            chrome_path = os.getenv('BROWSER_PATH', '').strip()
            disable_security = os.getenv('DISABLE_SECURITY', 'false').lower() == 'true'
            
            # 如果没有指定 Chrome 路径，自动检测
            if not chrome_path:
                chrome_path = find_chrome_path()
                if chrome_path:
                    print(f"[BrowserUse] 🔍 自动检测到浏览器: {chrome_path}")
                else:
                    print(f"[BrowserUse] ⚠️ 未找到 Chrome/Edge 浏览器，将使用 browser-use 默认检测")
            
            print(f"[BrowserUse] 🔧 浏览器配置: headless={headless}, disable_security={disable_security}")
            print(f"[BrowserUse] 🔧 Chrome路径: {chrome_path if chrome_path else '自动检测'}")
            
            # 使用手动启动模式
            use_manual_chrome = os.getenv('USE_MANUAL_CHROME', 'true').lower() == 'true'
            chrome_process = None
            cdp_url = None
            
            if use_manual_chrome and chrome_path:
                print(f"[BrowserUse] 🚀 手动启动 Chrome 并启用 CDP...")
                try:
                    chrome_process, port, temp_dir, ws_url = await start_chrome_with_debugging(
                        chrome_path, headless
                    )
                    # 使用 WebSocket URL，避免 browser-use 再次请求 /json/version
                    cdp_url = ws_url
                    print(f"[BrowserUse] ✅ Chrome 已启动，WebSocket URL: {cdp_url}")
                except Exception as e:
                    print(f"[BrowserUse] ⚠️ 手动启动 Chrome 失败: {e}")
                    print(f"[BrowserUse] ℹ️ 回退到 browser-use 自动启动模式")
                    cdp_url = None
            
            # 创建 BrowserSession
            if cdp_url:
                # 使用已启动的 Chrome
                browser_session = BrowserSession(
                    cdp_url=cdp_url,
                    # 设置等待时间
                    minimum_wait_page_load_time=0.5,
                    wait_between_actions=0.3,
                )
            else:
                # 让 browser-use 自己启动 Chrome
                browser_session = BrowserSession(
                    headless=headless,
                    disable_security=disable_security,
                    executable_path=chrome_path if chrome_path else None,
                    enable_default_extensions=os.getenv("BROWSER_USE_ENABLE_DEFAULT_EXTENSIONS", "false").lower() == "true",
                    # 设置等待时间
                    minimum_wait_page_load_time=0.5,
                    wait_between_actions=0.3,
                )
            
            tools = Tools()
            
            print(f"[BrowserUse] ✓ BrowserSession 创建成功")
            
            # 获取系统提示词
            from Api_request.prompts import BROWSER_USE_CHINESE_SYSTEM
            
            # 创建 Agent
            agent = Agent(
                task=task_description,
                llm=llm,
                browser_session=browser_session,
                tools=tools,
                use_vision=use_vision,
                max_actions_per_step=max_actions,
                extend_system_message=BROWSER_USE_CHINESE_SYSTEM,
            )
            
            print(f"[BrowserUse] 🚀 开始执行: {test_case.title}")

            # 执行测试（带重试）
            max_retries = 3
            last_error = None
            history = None

            for attempt in range(max_retries):
                # 检查是否已被外部停止
                if task_id is not None:
                    from Test_Tools.task_manager import get_task_manager
                    _tm = get_task_manager()
                    if _tm.should_stop(task_id):
                        print(f"[BrowserUse] ⛔ 任务 {task_id} 已被停止，中止执行")
                        return {
                            "success": False,
                            "message": "任务已被用户停止"
                        }
                try:
                    if attempt > 0:
                        print(f"[BrowserUse] 🔄 重试第 {attempt + 1} 次...")
                        # 关闭之前的 Chrome 进程
                        if chrome_process:
                            try:
                                chrome_process.terminate()
                                chrome_process.wait(timeout=5)
                            except Exception:
                                pass
                            chrome_process = None
                        
                        await asyncio.sleep(2)
                        
                        # 重新启动 Chrome
                        cdp_url = None
                        if use_manual_chrome and chrome_path:
                            try:
                                chrome_process, port, temp_dir, ws_url = await start_chrome_with_debugging(
                                    chrome_path, headless
                                )
                                cdp_url = ws_url
                                print(f"[BrowserUse] ✅ Chrome 重新启动，WebSocket URL: {cdp_url}")
                            except Exception as e:
                                print(f"[BrowserUse] ⚠️ 重新启动 Chrome 失败: {e}")
                        
                        # 重新创建 BrowserSession
                        if cdp_url:
                            browser_session = BrowserSession(
                                cdp_url=cdp_url,
                                minimum_wait_page_load_time=0.5,
                                wait_between_actions=0.3,
                            )
                        else:
                            browser_session = BrowserSession(
                                headless=headless,
                                disable_security=disable_security,
                                executable_path=chrome_path if chrome_path else None,
                                enable_default_extensions=os.getenv("BROWSER_USE_ENABLE_DEFAULT_EXTENSIONS", "false").lower() == "true",
                                minimum_wait_page_load_time=0.5,
                                wait_between_actions=0.3,
                            )
                        
                        agent = Agent(
                            task=task_description,
                            llm=llm,
                            browser_session=browser_session,
                            tools=tools,
                            use_vision=use_vision,
                            max_actions_per_step=max_actions,
                            extend_system_message=BROWSER_USE_CHINESE_SYSTEM,
                        )
                    
                    history = await agent.run(max_steps=max_steps)
                    break  # 成功执行，退出重试循环
                    
                except json.JSONDecodeError as e:
                    last_error = e
                    print(f"[BrowserUse] ⚠️ JSON解析错误 (尝试 {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        # 尝试关闭可能残留的浏览器
                        try:
                            await browser_session.stop()
                        except Exception:
                            pass
                        continue
                    raise
                except Exception as e:
                    error_str = str(e).lower()
                    # 检查是否是可重试的错误
                    if any(err in error_str for err in ['cdp', 'connection', 'timeout', 'json']):
                        last_error = e
                        print(f"[BrowserUse] ⚠️ 浏览器连接错误 (尝试 {attempt + 1}/{max_retries}): {e}")
                        if attempt < max_retries - 1:
                            try:
                                await browser_session.stop()
                            except Exception:
                                pass
                            continue
                    raise
            
            if history is None:
                raise last_error or Exception("执行失败，未知错误")
            
            # 处理执行结果
            execution_time = int(time.time() - start_time)
            execution_result = BrowserUseService._process_execution_result(
                history, test_case, execution_time
            )
            
            # 更新执行记录
            test_record.passed_cases = 1 if execution_result["status"] == 'pass' else 0
            test_record.failed_cases = 1 if execution_result["status"] in ('fail', 'error') else 0
            test_record.execution_log = json.dumps({
                "history": execution_result["history"]
            }, ensure_ascii=False)
            test_record.status = execution_result["status"]
            test_record.error_message = execution_result.get("error_message", "")
            test_record.duration = execution_time
            test_record.test_steps = execution_result["total_steps"]
            
            db.commit()
            
            print(f"[BrowserUse] {'✅ 成功' if execution_result['status'] == 'pass' else '❌ 失败'}")
            print(f"[BrowserUse] 📊 执行 {execution_result['total_steps']} 步，耗时 {execution_time} 秒")
            
            # 自动生成测试报告（批量模式下跳过，由批量方法统一生成）
            report_data = None
            if not skip_report:
                try:
                    print(f"[BrowserUse] 📝 正在生成测试报告...")
                    from Build_Report.service import TestReportService
                    report_result = await TestReportService.generate_report(
                        test_result_ids=[test_record.id],
                        db=db,
                        format_type="markdown",
                        project_id=getattr(test_case, 'project_id', None)
                    )
                    if report_result.get("success"):
                        report_data = report_result.get("data")
                        print(f"[BrowserUse] ✅ 测试报告已生成，报告ID: {report_data.get('report_id')}")
                    else:
                        print(f"[BrowserUse] ⚠️ 报告生成失败: {report_result.get('message')}")
                except Exception as report_error:
                    print(f"[BrowserUse] ⚠️ 报告生成异常: {str(report_error)}")
            
            return {
                "success": True,
                "message": f"测试执行{'成功' if execution_result['status'] == 'pass' else '失败'}",
                "data": {
                    "result_id": test_record.id,
                    "status": execution_result["status"],
                    "total_steps": execution_result["total_steps"],
                    "history": execution_result["history"],
                    "duration": execution_time,
                    "report": report_data
                }
            }
        
        except Exception as e:
            import traceback
            error_msg = str(e)
            error_trace = traceback.format_exc()
            
            print(f"[BrowserUse] ❌ 错误: {error_msg}")
            
            # 更新执行记录为失败
            execution_time = int(time.time() - start_time)
            test_record.status = "error"
            test_record.error_message = error_msg
            test_record.duration = execution_time
            test_record.execution_log = json.dumps({
                "error": error_msg,
                "trace": error_trace
            }, ensure_ascii=False)
            
            db.commit()
            
            return {
                "success": False,
                "message": f"测试执行失败: {error_msg}",
                "error_details": error_trace
            }
    
    @staticmethod
    def _build_task_description(test_case) -> str:
        """构建任务描述"""
        steps_list = json.loads(test_case.steps) if test_case.steps else []
        test_data = test_case.test_data or {}
        
        formatted_steps = "\n".join([
            f"{i+1}. {step}" for i, step in enumerate(steps_list)
        ])
        
        formatted_data = "\n".join([
            f"- {key}: {value}" for key, value in test_data.items()
        ])
        
        # 尝试提取目标 URL
        target_url = test_data.get('url') or test_data.get('target_url') or test_data.get('网址')
        
        if not target_url and steps_list:
            url_match = re.search(r'https?://[^\s]+', steps_list[0])
            if url_match:
                target_url = url_match.group(0)
        
        url_instruction = f"\n⚠️ 首先访问目标网址：{target_url}\n" if target_url else ""
        
        task = f"""
【测试任务】
标题：{test_case.title}
{url_instruction}
【前置条件】
{test_case.precondition or '无'}

【执行步骤】
{formatted_steps}

【预期结果】
{test_case.expected}

【测试数据】
{formatted_data if formatted_data else '无'}

【重要提示】
1. 立即开始执行，不要停留在空白页面
2. 严格按照步骤顺序执行
3. 完成所有步骤后调用 done 动作
"""
        return task.strip()
    
    @staticmethod
    def _process_execution_result(history, test_case, execution_time: int) -> Dict[str, Any]:
        """处理执行结果"""
        history_data = {
            "total_steps": history.number_of_steps(),
            "steps": [],
            "final_state": {
                "url": "",
                "success": False
            }
        }
        
        # 提取 Agent 自身的 done 动作中的 success 字段
        agent_reported_success = None
        
        for i, h in enumerate(history.history):
            step_data = {
                "step_number": i + 1,
                "timestamp": datetime.now().isoformat(),
                "url": h.state.url if h.state else '',
                "title": h.state.title if h.state else '',
            }
            
            if h.model_output:
                step_data["thinking"] = h.model_output.current_state.thinking
                step_data["next_goal"] = h.model_output.current_state.next_goal
                
                step_data["actions"] = []
                for action in h.model_output.action:
                    action_dict = action.model_dump(exclude_none=True)
                    step_data["actions"].append(action_dict)
                    
                    # 检查 done 动作中 Agent 自己报告的 success
                    if 'done' in action_dict:
                        done_data = action_dict['done']
                        if isinstance(done_data, dict) and 'success' in done_data:
                            agent_reported_success = done_data['success']
            else:
                step_data["actions"] = []
            
            history_data["steps"].append(step_data)
        
        is_done = history.is_done()
        is_successful = history.is_successful()
        
        # 综合判定：优先使用 Agent 自身的判定结果
        # browser-use 的 judge 可能因为 Toast 消失过快等原因误判
        # 如果 Agent 明确报告了 success=False，则尊重该判定
        if agent_reported_success is not None:
            final_success = agent_reported_success
        else:
            final_success = is_done and (is_successful is not False)
        
        final_url = history.urls()[-1] if history.urls() else ""
        
        history_data["final_state"]["url"] = final_url
        history_data["final_state"]["success"] = final_success
        
        # 记录 judge 与 agent 的判定差异（用于调试）
        judge_agent_diff = ""
        if agent_reported_success is not None and is_successful is not None:
            if agent_reported_success != is_successful:
                judge_agent_diff = f"Agent判定: {'通过' if agent_reported_success else '失败'}, Judge判定: {'通过' if is_successful else '失败'}"
                print(f"[BrowserUse] ⚠️ 判定差异 - {judge_agent_diff}")
        
        return {
            "status": "pass" if final_success else "fail",
            "error_message": "" if final_success else (judge_agent_diff or "测试未完成或失败"),
            "total_steps": history_data["total_steps"],
            "history": history_data,
            "final_url": final_url
        }
    
    @staticmethod
    async def execute_batch_test_cases(
        test_case_ids: list,
        db: Session,
        headless: bool = None,
        max_steps: int = None,
        use_vision: bool = None,
        max_actions: int = None
    ) -> Dict[str, Any]:
        """批量执行测试用例"""
        # 逐个执行，但不让单条执行生成报告
        results = []
        all_record_ids = []
        
        for test_case_id in test_case_ids:
            result = await BrowserUseService.execute_test_with_browser_use(
                test_case_id=test_case_id,
                db=db,
                headless=headless,
                max_steps=max_steps,
                use_vision=use_vision,
                max_actions=max_actions,
                skip_report=True
            )
            results.append(result)
            
            # 收集执行记录 ID
            if result.get('data', {}).get('result_id'):
                all_record_ids.append(result['data']['result_id'])
        
        # 统计结果
        success_count = sum(1 for r in results if r.get('data', {}).get('status') == 'pass')
        fail_count = len(results) - success_count
        
        # 生成批量汇总报告
        batch_report_data = None
        if all_record_ids:
            try:
                print(f"[BrowserUse] 📝 正在生成批量汇总报告...")
                from Build_Report.service import TestReportService
                # 取第一个用例的 project_id 作为报告归属
                first_case_id = test_case_ids[0] if test_case_ids else None
                batch_project_id = None
                if first_case_id:
                    from database.connection import ExecutionCase
                    first_case = db.query(ExecutionCase).filter(ExecutionCase.id == first_case_id).first()
                    batch_project_id = getattr(first_case, 'project_id', None) if first_case else None
                batch_report_result = await TestReportService.generate_report(
                    test_result_ids=all_record_ids,
                    db=db,
                    format_type="markdown",
                    execution_mode="批量",
                    project_id=batch_project_id
                )
                if batch_report_result.get("success"):
                    batch_report_data = batch_report_result.get("data")
                    print(f"[BrowserUse] ✅ 批量汇总报告已生成，报告ID: {batch_report_data.get('id')}")
                else:
                    print(f"[BrowserUse] ⚠️ 批量汇总报告生成失败: {batch_report_result.get('message')}")
            except Exception as report_error:
                print(f"[BrowserUse] ⚠️ 批量汇总报告生成异常: {str(report_error)}")
        
        return {
            "success": True,
            "message": f"批量执行完成: {success_count} 成功, {fail_count} 失败",
            "data": {
                "results": results,
                "summary": {
                    "total": len(results),
                    "passed": success_count,
                    "failed": fail_count
                },
                "batch_report": batch_report_data
            }
        }
