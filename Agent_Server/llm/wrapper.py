"""
LLM 包装器模块

提供 LLM 包装器，解决与 browser-use 和 LangChain 的兼容性问题

作者: 程序员Eighteen
版本: 1.0
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _find_matching_brace(text: str) -> int:
    """
    找到与第一个 '{' 匹配的 '}' 的位置索引。
    正确处理 JSON 字符串中的转义字符和嵌套括号。
    返回 -1 表示未找到匹配。
    
    移植自 browser-use 0.11.1 的 _find_matching_brace()
    """
    if not text or text[0] != '{':
        return -1

    depth = 0
    in_string = False
    escape_next = False
    i = 0

    while i < len(text):
        ch = text[i]

        if escape_next:
            escape_next = False
            i += 1
            continue

        if ch == '\\' and in_string:
            escape_next = True
            i += 1
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
        elif not in_string:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i

        i += 1

    return -1


def _clean_llm_json_output(text: str) -> str:
    """
    清理 LLM 返回的非标准 JSON 输出
    
    移植自 browser-use 0.11.1 的 _clean_llm_json_output()
    
    处理：
    1. 剥离 <think>...</think> 推理标签（包括嵌在 JSON 字段值内的）
    2. 剥离 markdown 代码块包裹
    3. 修复不规范的 JSON 转义字符
    4. 用括号匹配提取完整 JSON 对象（解决 Extra data / trailing characters）
    """
    cleaned = text.strip()

    # 1. 剥离 <think>...</think>（贪婪匹配，可能跨多行）
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', cleaned).strip()

    # 2. 剥离 markdown 代码块
    md_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', cleaned)
    if md_match:
        cleaned = md_match.group(1).strip()

    # 3. 如果不是以 { 开头，找到第一个 {
    if cleaned and cleaned[0] != '{':
        idx = cleaned.find('{')
        if idx >= 0:
            cleaned = cleaned[idx:]

    # 4. 修复不规范的转义
    cleaned = cleaned.replace("\\'", "'")

    # 5. 用括号匹配提取完整 JSON 对象
    if cleaned.startswith('{'):
        end_idx = _find_matching_brace(cleaned)
        if end_idx > 0 and end_idx < len(cleaned) - 1:
            extracted = cleaned[:end_idx + 1]
            logger.debug(
                f"[_clean_llm_json_output] 截断 trailing characters: "
                f"原始 {len(cleaned)} → 提取 {len(extracted)} 字符"
            )
            cleaned = extracted

    return cleaned


class _WrapperResponse:
    """
    LLMWrapper 的返回值，兼容 browser-use 的 ChatInvokeCompletion 接口。
    
    browser-use Agent 通过 response.completion 获取 LLM 解析结果，
    通过 response.usage 获取 token 统计（可选）。
    """
    __slots__ = ('completion', 'usage', 'stop_reason', 'thinking', 'redacted_thinking')

    def __init__(self, completion, usage=None, stop_reason=None):
        self.completion = completion
        self.usage = usage
        self.stop_reason = stop_reason
        self.thinking = None
        self.redacted_thinking = None


class LLMWrapper:
    """
    LLM 包装器
    
    解决以下问题:
    1. Pydantic v2 与 browser-use token tracking 的兼容性
    2. browser-use 的 output_format 参数处理
    3. 不同模型输出格式的统一处理
    """
    
    def __init__(self, llm, action_aliases=None):
        """
        Args:
            llm: 原始的 LLM 实例（LangChain 或 browser-use 格式）
            action_aliases: 可选的 provider 特定 action 别名映射 dict
                           key=模型返回的名称, value=browser-use 实际名称
        """
        object.__setattr__(self, '_llm', llm)
        object.__setattr__(self, '_original_ainvoke', llm.ainvoke if hasattr(llm, 'ainvoke') else None)

        # 合并别名映射：默认 + provider 特定
        merged_aliases = dict(self.DEFAULT_ACTION_ALIASES)
        if action_aliases:
            merged_aliases.update(action_aliases)
        object.__setattr__(self, '_action_aliases', merged_aliases)

        # 智能推断 provider
        llm_class_name = llm.__class__.__name__
        if 'OpenAI' in llm_class_name:
            provider = 'openai'
        elif 'Anthropic' in llm_class_name or 'Claude' in llm_class_name:
            provider = 'anthropic'
        elif 'Google' in llm_class_name or 'Gemini' in llm_class_name:
            provider = 'google'
        elif 'Ollama' in llm_class_name:
            provider = 'ollama'
        elif 'DeepSeek' in llm_class_name:
            provider = 'deepseek'
        else:
            provider = 'unknown'

        object.__setattr__(self, 'provider', provider)

        # 获取 model 名称
        if hasattr(llm, 'model_name'):
            model = llm.model_name
        elif hasattr(llm, 'model'):
            model = llm.model
        else:
            model = 'unknown'

        object.__setattr__(self, 'model', model)

    
    @property
    def name(self) -> str:
        """兼容 browser-use BaseChatModel 协议"""
        return str(self.model)
    
    def _convert_messages(self, messages):
        """
        转换消息格式为 LangChain 格式

        将各种消息类型（browser-use 自定义类型、dict、LangChain 类型）
        统一转换为 LangChain 消息，确保底层 langchain_openai.ChatOpenAI 能正确处理。
        """
        from langchain_core.messages import (
            SystemMessage as LCSystemMessage,
            HumanMessage as LCHumanMessage,
            AIMessage as LCAIMessage,
        )
        
        converted = []
        for msg in messages:
            # 已经是 LangChain 消息，直接使用
            if isinstance(msg, (LCSystemMessage, LCHumanMessage, LCAIMessage)):
                converted.append(msg)
                continue
            
            # 处理 browser-use 自定义消息类型或其他带 role/content 属性的对象
            if hasattr(msg, 'role') and hasattr(msg, 'content'):
                role = str(msg.role).lower()
                # browser-use 消息的 content 可能是 str 或 list[ContentPart]
                # 提取纯文本
                content = msg.content
                if isinstance(content, list):
                    # 从 ContentPart 列表中提取文本
                    parts = []
                    for part in content:
                        if hasattr(part, 'text'):
                            parts.append(part.text)
                        elif hasattr(part, 'type') and part.type == 'text':
                            parts.append(getattr(part, 'text', str(part)))
                        else:
                            parts.append(str(part))
                    content = '\n'.join(parts)
                
                if role == 'system':
                    converted.append(LCSystemMessage(content=content))
                elif role in ('user', 'human'):
                    converted.append(LCHumanMessage(content=content))
                elif role in ('assistant', 'ai'):
                    converted.append(LCAIMessage(content=content))
                else:
                    logger.warning(f"[LLMWrapper] 未知消息角色: {role}，默认为 HumanMessage")
                    converted.append(LCHumanMessage(content=content))
            elif isinstance(msg, dict):
                # dict 格式消息
                role = msg.get('role', 'user').lower()
                content = msg.get('content', '')
                if role == 'system':
                    converted.append(LCSystemMessage(content=content))
                elif role in ('user', 'human'):
                    converted.append(LCHumanMessage(content=content))
                elif role in ('assistant', 'ai'):
                    converted.append(LCAIMessage(content=content))
                else:
                    converted.append(LCHumanMessage(content=content))
            else:
                # 未知类型，原样传递
                converted.append(msg)
        
        return converted
    
    async def ainvoke(self, messages, output_format=None, **kwargs):
        """
        异步调用，兼容 browser-use 的 output_format 参数

        browser-use Agent 调用 llm.ainvoke(messages, output_format=AgentOutput)
        并期望返回值有 .completion 属性（ChatInvokeCompletion 格式）。
        """
        converted_messages = self._convert_messages(messages)
        
        # 处理 structured output
        if output_format is not None:
            logger.debug(f"[LLMWrapper] 处理 structured output: {output_format.__name__}")
            
            # 判断目标类型是否包含 action 字段（区分 AgentOutput 和 JudgementResult 等）
            _has_action_field = hasattr(output_format, 'model_fields') and 'action' in output_format.model_fields
            
            # 添加 JSON 格式要求到最后一条消息
            if converted_messages:
                last_msg = converted_messages[-1]
                if hasattr(last_msg, 'content'):
                    try:
                        if hasattr(output_format, 'model_json_schema'):
                            schema = output_format.model_json_schema()
                        elif hasattr(output_format, 'schema'):
                            schema = output_format.schema()
                        else:
                            schema = {}
                        
                        schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
                        
                        from langchain_core.messages import HumanMessage
                        new_content = f"{last_msg.content}\n\nPlease respond with a valid JSON object matching this schema:\n{schema_str}"
                        converted_messages[-1] = HumanMessage(content=new_content)
                        
                    except Exception as e:
                        logger.warning(f"[LLMWrapper] 无法获取 schema: {e}")
            
            # 调用原始 LLM
            response = await self._original_ainvoke(converted_messages, **kwargs)
            
            # 解析响应
            try:
                raw_content = response.content
                
                # 使用统一的 JSON 清理（移植自 browser-use 0.11.1）
                # 处理: <think> 标签（包括嵌在 JSON 字段值内的）、markdown、trailing chars
                content = _clean_llm_json_output(raw_content)
                
                # 尝试解析 JSON，失败则尝试修复截断
                data = self._try_parse_json(content)
                
                # 仅对包含 action 字段的类型（AgentOutput）做 action 格式修正
                if _has_action_field:
                    data = self._fix_action_format(data)
                
                # 构造 output_format 对象
                result = output_format(**data)
                logger.debug(f"[LLMWrapper] 成功构造 {output_format.__name__} 对象")
                
                # 返回 ChatInvokeCompletion 兼容格式
                return _WrapperResponse(completion=result)
                
            except json.JSONDecodeError as je:
                logger.error(f"[LLMWrapper] JSON 解析失败: {je}")
                logger.error(f"[LLMWrapper] 原始响应: {raw_content[:500]}")
                raise
            except Exception as e:
                logger.error(f"[LLMWrapper] 构造 {output_format.__name__} 失败: {e}")
                logger.error(f"[LLMWrapper] 原始响应: {raw_content[:500]}")
                raise
        else:
            response = await self._original_ainvoke(converted_messages, **kwargs)
            # 无 output_format 时，browser-use 期望 response.completion 为字符串
            content = response.content if hasattr(response, 'content') else str(response)
            return _WrapperResponse(completion=content)
    
    def _try_parse_json(self, content: str) -> dict:
        """
        尝试解析 JSON，失败时自动修复截断/格式问题
        
        Qwen3.5 等大模型常见问题：
        - JSON 被 max_tokens 截断，如 "action": [{ 就结束了
        - 尾部多余逗号
        - 未闭合的括号
        """
        # 1. 直接尝试
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # 2. 移除尾部逗号
        fixed = re.sub(r',\s*([}\]])', r'\1', content)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        # 3. 用括号匹配提取完整 JSON 对象（解决 Extra data）
        if content.strip().startswith('{'):
            end_idx = _find_matching_brace(content.strip())
            if end_idx > 0:
                extracted = content.strip()[:end_idx + 1]
                try:
                    return json.loads(extracted)
                except json.JSONDecodeError:
                    # 提取后仍失败，尝试修复尾部逗号
                    extracted_fixed = re.sub(r',\s*([}\]])', r'\1', extracted)
                    try:
                        return json.loads(extracted_fixed)
                    except json.JSONDecodeError:
                        pass
        
        # 4. 修复截断的 JSON（补全缺失的括号）
        patched = fixed.rstrip()
        if patched.endswith(','):
            patched = patched[:-1]
        
        truncated = self._truncate_to_valid_json(patched)
        if truncated:
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass
        
        # 5. 暴力补全括号
        open_braces = patched.count('{') - patched.count('}')
        open_brackets = patched.count('[') - patched.count(']')
        if open_braces > 0 or open_brackets > 0:
            patched += ']' * max(0, open_brackets) + '}' * max(0, open_braces)
            patched = re.sub(r',\s*([}\]])', r'\1', patched)
            try:
                return json.loads(patched)
            except json.JSONDecodeError:
                pass
        
        # 6. 全部失败，抛出原始错误
        return json.loads(content)
    
    @staticmethod
    def _truncate_to_valid_json(text: str) -> Optional[str]:
        """
        将截断的 JSON 修复为有效 JSON
        
        策略：找到最后一个完整的 key-value 对，截断后面的不完整部分，
        然后补全括号。
        
        例如:
        {"thinking": "...", "action": [{   → {"thinking": "...", "action": []}
        {"thinking": "...", "next_goal": " → {"thinking": "..."}
        """
        if not text or not text.startswith('{'):
            return None
        
        # 从后往前找到最后一个完整的 value 结束标记
        # 完整 value 结束于: "string", number, true, false, null, }, ]
        # 不完整的情况: 截断在字符串中间、对象/数组中间
        
        best_pos = -1
        depth_brace = 0
        depth_bracket = 0
        in_string = False
        escape_next = False
        
        for i in range(len(text)):
            ch = text[i]
            
            if escape_next:
                escape_next = False
                continue
            
            if ch == '\\' and in_string:
                escape_next = True
                continue
            
            if ch == '"' and not escape_next:
                in_string = not in_string
                if not in_string:
                    # 字符串结束，可能是一个完整的 value
                    # 检查这是否是一个 value（前面有冒号）或 key
                    # 简单策略：记录位置，后续判断
                    best_pos = i
                continue
            
            if in_string:
                continue
            
            if ch == '{':
                depth_brace += 1
            elif ch == '}':
                depth_brace -= 1
                best_pos = i
            elif ch == '[':
                depth_bracket += 1
            elif ch == ']':
                depth_bracket -= 1
                best_pos = i
            elif ch in '0123456789':
                best_pos = i
            elif ch == 'e' and i >= 3 and text[i-3:i+1] in ('true', 'alse'):
                best_pos = i
            elif ch == 'l' and i >= 3 and text[i-3:i+1] == 'null':
                best_pos = i
        
        if best_pos <= 0:
            return None
        
        # 截断到 best_pos
        truncated = text[:best_pos + 1].rstrip()
        
        # 去掉尾部逗号
        if truncated.endswith(','):
            truncated = truncated[:-1]
        
        # 补全括号
        open_braces = truncated.count('{') - truncated.count('}')
        open_brackets = truncated.count('[') - truncated.count(']')
        
        if open_brackets > 0:
            truncated += ']' * open_brackets
        if open_braces > 0:
            truncated += '}' * open_braces
        
        # 清理尾部逗号（补全括号后可能出现 ,] 或 ,}）
        truncated = re.sub(r',\s*([}\]])', r'\1', truncated)
        
        return truncated
    
    # browser-use 0.11.1 注册的合法 action 名称（函数名即 action 名）
    VALID_BROWSER_ACTIONS = {
        # 导航
        'search', 'navigate', 'go_back',
        # 交互
        'click', 'input', 'upload_file', 'send_keys',
        # 滚动
        'scroll', 'find_text',
        # 标签页
        'switch_tab', 'close_tab',
        # 提取 & 截图
        'extract', 'screenshot',
        # 下拉框
        'get_dropdown_options', 'select_dropdown_option',
        # 文件系统
        'save_file', 'replace_file', 'read_file',
        # JS
        'run_javascript',
        # 完成 & 等待
        'done', 'wait',
    }

    # 通用别名映射：不同模型可能用不同名称指代同一个 action
    # key = 模型返回的名称, value = browser-use 实际的 action 名称
    DEFAULT_ACTION_ALIASES = {
        # scroll 变体
        'scroll_down': 'scroll',
        'scroll_up': 'scroll',
        'scroll_page': 'scroll',
        # extract 变体
        'extract_content': 'extract',
        'extract_text': 'extract',
        'extract_data': 'extract',
        'extract_page': 'extract',
        # click 变体
        'click_element': 'click',
        # input 变体
        'input_text': 'input',
        'type_text': 'input',
        'fill': 'input',
        # navigate 变体
        'go_to_url': 'navigate',
        'goto': 'navigate',
        'open_url': 'navigate',
        # tab 变体
        'open_tab': 'navigate',
        'new_tab': 'navigate',
        # find_text 变体
        'scroll_to_text': 'find_text',
        # select 变体
        'select_option': 'select_dropdown_option',
        # done 变体
        'finish': 'done',
        'complete': 'done',
        # run_javascript 变体
        'evaluate': 'run_javascript',
        'execute_js': 'run_javascript',
        'eval': 'run_javascript',
        'execute_javascript': 'run_javascript',
    }

    def _fix_action_format(self, data):
        """
        修正 LLM 输出的 action 格式错误（仅用于 AgentOutput）
        
        处理:
        1. 别名转换: scroll_down → scroll, extract_content → extract 等
        2. 参数格式修正: {'wait': 3} → {'wait': {'seconds': 3}}
        3. 缺少 action 字段 → 补充 wait action
        4. action 为空列表 → 补充 wait action
        5. 未知 action 类型 → 放行（让 browser-use 自己处理，可能是 MCP 注册的自定义 action）
        """
        if 'action' not in data:
            logger.warning(
                f"[LLMWrapper] AgentOutput 缺少 action 字段，补充默认 wait action。"
                f"原始 keys: {list(data.keys())}"
            )
            data['action'] = [{"wait": {"seconds": 3}}]
            return data
        
        actions = data.get('action', [])
        if not isinstance(actions, list):
            return data
        
        if len(actions) == 0:
            logger.warning("[LLMWrapper] action 为空列表（可能因 JSON 截断），补充 wait action")
            data['action'] = [{"wait": {"seconds": 3}}]
            return data
        
        fixed_actions = []
        for action in actions:
            if not isinstance(action, dict):
                fixed_actions.append(action)
                continue
            
            fixed_action = {}
            for key, value in action.items():
                # 1. 别名转换
                actual_key = self._action_aliases.get(key, key)
                if actual_key != key:
                    logger.debug(f"[LLMWrapper] action 别名转换: '{key}' → '{actual_key}'")
                    
                    # scroll_down / scroll_up 需要转换参数
                    if key == 'scroll_down' and actual_key == 'scroll':
                        if not isinstance(value, dict):
                            value = {}
                        value['down'] = True
                    elif key == 'scroll_up' and actual_key == 'scroll':
                        if not isinstance(value, dict):
                            value = {}
                        value['down'] = False
                    # open_tab / new_tab 需要设置 new_tab=True
                    elif key in ('open_tab', 'new_tab') and actual_key == 'navigate':
                        if isinstance(value, dict):
                            value['new_tab'] = True
                        elif isinstance(value, str):
                            value = {'url': value, 'new_tab': True}
                    
                    key = actual_key

                # 2. 修正 wait 格式
                if key == 'wait':
                    if isinstance(value, (int, float)):
                        fixed_action['wait'] = {'seconds': int(value)}
                        logger.debug(f"[LLMWrapper] 修正 wait 格式: {value} -> {{'seconds': {int(value)}}}")
                    elif isinstance(value, dict) and 'seconds' in value:
                        fixed_action['wait'] = value
                    else:
                        fixed_action['wait'] = {'seconds': 3}
                
                # 3. 修正 input 格式
                elif key == 'input':
                    if isinstance(value, dict):
                        fixed_input = value.copy()
                        if 'value' in fixed_input and 'text' not in fixed_input:
                            fixed_input['text'] = fixed_input.pop('value')
                            logger.debug(f"[LLMWrapper] 修正 input 格式: 'value' -> 'text'")
                        fixed_action['input'] = fixed_input
                    else:
                        fixed_action['input'] = value
                
                # 4. 其他 action 直接放行（包括 browser-use 原生的和 MCP 自定义的）
                else:
                    fixed_action[key] = value
            
            if fixed_action:
                fixed_actions.append(fixed_action)
        
        # 如果所有 action 都为空（极端情况），补充 wait
        if not fixed_actions and actions:
            logger.warning(
                f"[LLMWrapper] 所有 action 处理后为空，补充 wait。原始 actions: {actions}"
            )
            fixed_actions = [{"wait": {"seconds": 3}}]
        
        data['action'] = fixed_actions
        return data
    
    def __getattr__(self, name):
        """委托属性访问到原始 LLM"""
        if name in ['provider', 'model', '_original_ainvoke']:
            return object.__getattribute__(self, name)
        return getattr(self._llm, name)
    
    def __setattr__(self, name, value):
        """允许动态设置属性"""
        if name in ['_llm', '_original_ainvoke']:
            object.__setattr__(self, name, value)
        else:
            object.__setattr__(self, name, value)
    
    def __repr__(self):
        return f"LLMWrapper(provider={self.provider}, model={self.model}, llm={self._llm})"


def wrap_llm(llm, action_aliases=None) -> LLMWrapper:
    """
    包装 LLM 实例
    
    Args:
        llm: 原始 LLM 实例
        action_aliases: 可选的 provider 特定 action 别名映射
    
    Returns:
        LLMWrapper 实例
    """
    if isinstance(llm, LLMWrapper):
        return llm
    return LLMWrapper(llm, action_aliases=action_aliases)
