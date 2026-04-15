"""
DOM 模式测试执行器

当 DomRichnessDetector 判断页面 DOM 丰富时，使用此执行器替代 browser-use Agent。
核心流程：
  1. 获取页面无障碍树快照（snapshot -i）
  2. 将快照 + 测试步骤发给 LLM，让 LLM 规划操作序列
  3. 通过 AgentBrowserClient 执行操作序列
  4. 每步执行后重新获取快照，验证状态
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from .agent_browser_client import AgentBrowserClient

logger = logging.getLogger(__name__)

# 单步最大重试次数
_MAX_STEP_RETRIES = 2
# 最大执行步数（防止无限循环）
_MAX_EXEC_STEPS = 50


class DomExecutor:
    """
    DOM 模式执行器

    接收测试用例，通过 agent-browser CLI + LLM 规划执行测试步骤。
    """

    def __init__(self, client: AgentBrowserClient):
        self.client = client

    async def execute_test_case(
        self,
        case: Dict,
        target_url: str,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> Dict[str, Any]:
        """
        执行单条测试用例（DOM 模式）。

        Args:
            case: 测试用例字典，含 title/steps/expected/test_data
            target_url: 目标页面 URL
            cancel_event: 取消信号

        Returns:
            {
                "status": "pass" | "fail" | "error" | "cancelled",
                "message": str,
                "duration": int,
                "steps": int,
                "mode": "dom",
            }
        """
        start = time.time()
        exec_steps = 0

        try:
            # 1. 导航到目标页面
            nav_result = await self.client.navigate(target_url)
            if not nav_result.get("success"):
                logger.warning(f"[DomExecutor] 导航失败: {nav_result.get('error')}")

            await asyncio.sleep(1.5)  # 等待页面加载

            # 2. 获取初始快照
            snapshot = await self.client.snapshot()
            if not snapshot.get("success"):
                return _error_result("获取页面快照失败", start)

            # 3. 用 LLM 规划操作序列
            plan = await self._plan_actions(case, snapshot, target_url)
            if not plan:
                return _error_result("LLM 规划操作序列失败", start)

            actions = plan.get("actions", [])
            logger.info(f"[DomExecutor] LLM 规划了 {len(actions)} 个操作")

            # 4. 逐步执行操作
            for i, action in enumerate(actions):
                if cancel_event and cancel_event.is_set():
                    return {
                        "status": "cancelled",
                        "message": "测试已被手动停止",
                        "duration": int(time.time() - start),
                        "steps": exec_steps,
                        "mode": "dom",
                    }

                if exec_steps >= _MAX_EXEC_STEPS:
                    logger.warning("[DomExecutor] 达到最大执行步数限制")
                    break

                exec_steps += 1
                action_type = action.get("type", "")
                logger.info(f"[DomExecutor] 步骤 {exec_steps}: {action_type} {action.get('target', '')}")

                step_ok = await self._execute_action(action)
                if not step_ok:
                    logger.warning(f"[DomExecutor] 步骤 {exec_steps} 执行失败，继续下一步")

                # 步骤间等待
                await asyncio.sleep(0.8)

            # 5. 获取最终快照，让 LLM 判断结果
            final_snapshot = await self.client.snapshot()
            final_url = await self.client.get_url()

            verdict = await self._judge_result(case, final_snapshot, final_url)
            status = "pass" if verdict.get("pass") else "fail"
            message = verdict.get("reason", "测试完成")

            logger.info(f"[DomExecutor] 执行完成: status={status}, steps={exec_steps}")
            return {
                "status": status,
                "message": message,
                "duration": int(time.time() - start),
                "steps": exec_steps,
                "mode": "dom",
            }

        except Exception as e:
            logger.error(f"[DomExecutor] 执行异常: {e}")
            return _error_result(str(e), start)

    async def _execute_action(self, action: Dict) -> bool:
        """执行单个操作，返回是否成功"""
        action_type = action.get("type", "")
        target = action.get("target", "")
        value = action.get("value", "")

        try:
            if action_type == "click":
                result = await self.client.click(target)
            elif action_type == "fill":
                result = await self.client.fill(target, value)
            elif action_type == "navigate":
                result = await self.client.navigate(target)
            elif action_type == "wait":
                result = await self.client.wait(value or "1000")
            elif action_type == "eval":
                result = await self.client.eval_js(value)
            else:
                logger.warning(f"[DomExecutor] 未知操作类型: {action_type}")
                return False

            return result.get("success", False)
        except Exception as e:
            logger.warning(f"[DomExecutor] 操作执行异常: {e}")
            return False

    async def _plan_actions(
        self, case: Dict, snapshot: Dict, target_url: str
    ) -> Optional[Dict]:
        """
        调用 LLM 规划操作序列。

        Returns:
            {"actions": [{"type": str, "target": str, "value": str}, ...]}
        """
        try:
            from llm.client import get_llm_client
            llm = get_llm_client()

            snapshot_text = _extract_snapshot_text(snapshot)
            steps = case.get("steps", [])
            if isinstance(steps, list):
                steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
            else:
                steps_text = str(steps)

            test_data = case.get("test_data", {})
            data_text = json.dumps(test_data, ensure_ascii=False) if test_data else "无"

            prompt = f"""你是一个 UI 测试自动化专家。根据页面的无障碍树快照和测试步骤，规划具体的操作序列。

## 当前页面快照（无障碍树）
{snapshot_text}

## 测试用例
标题: {case.get('title', '')}
目标地址: {target_url}
测试步骤:
{steps_text}
预期结果: {case.get('expected', '')}
测试数据: {data_text}

## 操作类型说明
- click: 点击元素，target 为 @eN ref（如 @e1）或 CSS 选择器
- fill: 填写输入框，target 为 ref 或选择器，value 为填写内容
- navigate: 导航到 URL，target 为完整 URL
- wait: 等待，value 为毫秒数（如 "2000"）或选择器
- eval: 执行 JS，value 为 JS 代码

## 要求
1. 优先使用快照中的 @eN ref（如 @e1, @e2）作为 target，比 CSS 选择器更可靠
2. 每个操作只做一件事
3. 点击提交/登录按钮后，添加 wait 2000ms 等待响应
4. 返回 JSON 格式

请返回如下 JSON（不要包含任何其他内容）：
{{
  "actions": [
    {{"type": "click", "target": "@e1", "value": ""}},
    {{"type": "fill", "target": "@e2", "value": "admin"}},
    {{"type": "wait", "target": "", "value": "2000"}}
  ]
}}"""

            response = await llm.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            return llm.parse_json_response(response)

        except Exception as e:
            logger.error(f"[DomExecutor] LLM 规划失败: {e}")
            return None

    async def _judge_result(
        self, case: Dict, final_snapshot: Dict, final_url: str
    ) -> Dict:
        """
        调用 LLM 判断测试结果。

        Returns:
            {"pass": bool, "reason": str}
        """
        try:
            from llm.client import get_llm_client
            llm = get_llm_client()

            snapshot_text = _extract_snapshot_text(final_snapshot)

            prompt = f"""根据测试执行后的页面状态，判断测试是否通过。

## 测试用例
标题: {case.get('title', '')}
预期结果: {case.get('expected', '')}

## 执行后页面状态
当前 URL: {final_url}
页面快照（无障碍树）:
{snapshot_text}

## 判断规则
- 实际结果符合预期结果 → pass: true
- 实际结果不符合预期结果 → pass: false
- 如果预期是"登录失败/提示错误"，而页面确实显示了错误提示 → pass: true

请返回 JSON（不要包含其他内容）：
{{"pass": true, "reason": "判断理由"}}"""

            response = await llm.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
            )
            result = llm.parse_json_response(response)
            return result if result else {"pass": False, "reason": "LLM 判断失败"}

        except Exception as e:
            logger.warning(f"[DomExecutor] LLM 判断失败: {e}")
            return {"pass": False, "reason": f"结果判断异常: {e}"}


def _extract_snapshot_text(snapshot: Dict) -> str:
    """从 snapshot 结果中提取文本内容"""
    if not snapshot or not snapshot.get("success"):
        return "（快照获取失败）"
    data = snapshot.get("data", {})
    if isinstance(data, dict):
        return data.get("snapshot", str(data))[:3000]
    return str(data)[:3000]


def _error_result(message: str, start: float) -> Dict:
    return {
        "status": "error",
        "message": message,
        "duration": int(time.time() - start),
        "steps": 0,
        "mode": "dom",
    }
