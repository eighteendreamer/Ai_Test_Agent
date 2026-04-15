"""
基于模板的测试用例生成器

使用 模板 + LLM 混合方式生成测试用例：
1. LLM 分析页面能力，选择合适的模板
2. LLM 提取参数填充模板
3. 生成稳定、结构化的测试用例

优势：
- 稳定性高：用例结构固定，不会出现格式错误
- 效率高：LLM 只需填充参数，不需要生成完整用例
- 可维护：模板集中管理，易于更新和扩展
"""
import logging
from typing import Dict, List, Any, Optional
from Build_Use_case.templates import get_template_library, TestCaseTemplate

logger = logging.getLogger(__name__)


class TemplateBasedGenerator:
    """基于模板的测试用例生成器"""
    
    def __init__(self):
        self.template_lib = get_template_library()
    
    async def generate_from_page_capabilities(
        self,
        page_capabilities: Dict[str, Any],
        user_intent: str = "",
        llm_client = None,
    ) -> List[Dict[str, Any]]:
        """
        从页面能力生成测试用例
        
        Args:
            page_capabilities: 页面能力字典（来自 PageKnowledge 或探索结果）
            user_intent: 用户意图描述
            llm_client: LLM 客户端
        
        Returns:
            测试用例列表
        """
        if not llm_client:
            from llm import get_llm_client
            llm_client = get_llm_client()
        
        # 1. 分析页面类型，选择合适的模板
        template_selection = await self._select_templates(
            page_capabilities, user_intent, llm_client
        )
        
        if not template_selection or not template_selection.get("templates"):
            logger.warning("[TemplateGen] 未选择到合适的模板，降级为纯 LLM 生成")
            return []
        
        # 2. 为每个模板提取参数
        test_cases = []
        for template_info in template_selection["templates"]:
            template_id = template_info.get("template_id")
            template = self.template_lib.get_template(template_id)
            
            if not template:
                logger.warning(f"[TemplateGen] 模板 {template_id} 不存在")
                continue
            
            # 提取参数
            params = await self._extract_params(
                template, page_capabilities, template_info, llm_client
            )
            
            if not params:
                logger.warning(f"[TemplateGen] 模板 {template_id} 参数提取失败")
                continue
            
            # 填充模板生成用例
            try:
                test_case = template.fill(params)
                test_cases.append(test_case)
                logger.info(f"[TemplateGen] ✅ 生成用例: {test_case['title']}")
            except Exception as e:
                logger.error(f"[TemplateGen] 模板填充失败: {e}")
                continue
        
        return test_cases
    
    async def _select_templates(
        self,
        page_capabilities: Dict[str, Any],
        user_intent: str,
        llm_client,
    ) -> Optional[Dict]:
        """
        使用 LLM 分析页面能力，选择合适的模板
        
        Returns:
            {
                "templates": [
                    {"template_id": "login_001", "reason": "页面包含登录表单"},
                    {"template_id": "login_002", "reason": "需要测试错误密码场景"},
                    ...
                ]
            }
        """
        # 构建模板列表提示
        all_templates = self.template_lib.list_all_templates()
        template_list_text = "\n".join([
            f"- {t['template_id']}: {t['name']} ({t['category']}) - {t['description']}"
            for t in all_templates
        ])
        
        # 构建页面能力摘要
        page_type = page_capabilities.get("page_type", "")
        forms = page_capabilities.get("forms", [])
        tables = page_capabilities.get("tables", [])
        buttons = page_capabilities.get("buttons", [])
        has_search = page_capabilities.get("has_search", False)
        has_file_upload = page_capabilities.get("has_file_upload", False)
        auth_required = page_capabilities.get("auth_required", True)
        
        page_summary = f"""
页面类型: {page_type}
表单数量: {len(forms)}
表格数量: {len(tables)}
按钮数量: {len(buttons)}
是否需要登录: {auth_required}
是否有搜索: {has_search}
是否有文件上传: {has_file_upload}
"""
        
        if forms:
            form_names = [f.get("name", "") for f in forms]
            page_summary += f"\n表单列表: {', '.join(form_names)}"
        
        if tables:
            table_names = [t.get("name", "") for t in tables]
            page_summary += f"\n表格列表: {', '.join(table_names)}"
        
        prompt = f"""
你是一个测试用例设计专家。请根据页面能力和用户意图，从模板库中选择合适的测试用例模板。

【用户意图】
{user_intent or "全面测试该页面的所有功能"}

【页面能力】
{page_summary}

【可用模板库】
{template_list_text}

【任务】
1. 分析页面能力，判断适用哪些测试场景
2. 从模板库中选择 3-10 个最合适的模板
3. 优先选择高优先级（priority=1）的模板
4. 覆盖正常场景和异常场景

【输出格式】
返回 JSON 格式：
{{
    "templates": [
        {{
            "template_id": "login_001",
            "reason": "页面包含登录表单，需要测试正常登录"
        }},
        {{
            "template_id": "login_002",
            "reason": "需要测试错误密码场景"
        }}
    ]
}}

【注意】
- 只选择与页面能力匹配的模板
- 如果页面没有登录表单，不要选择登录模板
- 如果页面没有搜索功能，不要选择搜索模板
- 确保 template_id 在模板库中存在
"""
        
        try:
            response = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            result = llm_client.parse_json_response(response)
            logger.info(f"[TemplateGen] 选择了 {len(result.get('templates', []))} 个模板")
            return result
        except Exception as e:
            logger.error(f"[TemplateGen] 模板选择失败: {e}")
            return None
    
    async def _extract_params(
        self,
        template: TestCaseTemplate,
        page_capabilities: Dict[str, Any],
        template_info: Dict,
        llm_client,
    ) -> Optional[Dict[str, Any]]:
        """
        使用 LLM 从页面能力中提取模板所需的参数
        
        Returns:
            参数字典，如 {"module": "用户管理", "username_field": "账号", ...}
        """
        # 构建页面能力详情
        page_detail = self._build_page_detail(page_capabilities)
        
        # 构建参数提取提示
        required_params_text = "\n".join([
            f"- {p}: (必填)" for p in template.required_params
        ])
        optional_params_text = "\n".join([
            f"- {p}: (可选)" for p in template.optional_params
        ])
        
        prompt = f"""
你是一个测试用例参数提取专家。请从页面能力中提取测试用例模板所需的参数。

【模板信息】
模板ID: {template.template_id}
模板名称: {template.name}
模板描述: {template.description}

【需要提取的参数】
必填参数:
{required_params_text}

可选参数:
{optional_params_text}

【页面能力详情】
{page_detail}

【任务】
1. 从页面能力中提取所有必填参数
2. 尽可能提取可选参数
3. 如果页面中没有某个参数对应的元素，使用合理的默认值或占位符

【输出格式】
返回 JSON 格式的参数字典：
{{
    "module": "用户管理",
    "login_url": "http://example.com/login",
    "username_field": "账号",
    "password_field": "密码",
    "submit_button": "登录",
    "valid_username": "test_user",
    "valid_password": "Test123!",
    "keywords": "登录,认证",
    ...
}}

【注意】
- 所有必填参数必须提供
- URL 参数使用页面的实际 URL
- 字段名称使用页面中的实际文本
- 测试数据使用合理的示例值
"""
        
        try:
            response = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            params = llm_client.parse_json_response(response)
            
            # 验证必填参数
            missing = [p for p in template.required_params if p not in params]
            if missing:
                logger.warning(f"[TemplateGen] 缺少必填参数: {missing}")
                return None
            
            logger.info(f"[TemplateGen] 提取参数成功: {list(params.keys())}")
            return params
        except Exception as e:
            logger.error(f"[TemplateGen] 参数提取失败: {e}")
            return None
    
    def _build_page_detail(self, page_capabilities: Dict[str, Any]) -> str:
        """构建页面能力详情文本"""
        lines = []
        
        # 基本信息
        lines.append(f"页面URL: {page_capabilities.get('url', 'N/A')}")
        lines.append(f"页面标题: {page_capabilities.get('page_title', 'N/A')}")
        lines.append(f"页面类型: {page_capabilities.get('page_type', 'N/A')}")
        lines.append(f"页面摘要: {page_capabilities.get('summary', 'N/A')}")
        lines.append("")
        
        # 表单
        forms = page_capabilities.get("forms", [])
        if forms:
            lines.append("【表单列表】")
            for i, form in enumerate(forms, 1):
                lines.append(f"{i}. {form.get('name', '未命名表单')}")
                fields = form.get("fields", [])
                if fields:
                    lines.append("   字段:")
                    for field in fields:
                        field_name = field.get("name", "")
                        field_label = field.get("label", "")
                        field_type = field.get("field_type", field.get("type", ""))
                        required = "必填" if field.get("required") else "可选"
                        lines.append(f"   - {field_label or field_name} ({field_type}, {required})")
                submit_btn = form.get("submit_button", "")
                if submit_btn:
                    lines.append(f"   提交按钮: {submit_btn}")
            lines.append("")
        
        # 表格
        tables = page_capabilities.get("tables", [])
        if tables:
            lines.append("【表格列表】")
            for i, table in enumerate(tables, 1):
                lines.append(f"{i}. {table.get('name', '未命名表格')}")
                columns = table.get("columns", [])
                if columns:
                    lines.append(f"   列: {', '.join(columns)}")
                actions = table.get("row_actions", [])
                if actions:
                    lines.append(f"   操作: {', '.join(actions)}")
            lines.append("")
        
        # 按钮
        buttons = page_capabilities.get("buttons", [])
        if buttons:
            lines.append(f"【按钮列表】")
            lines.append(", ".join(str(b) for b in buttons[:20]))
            lines.append("")
        
        # 功能标记
        features = []
        if page_capabilities.get("auth_required"):
            features.append("需要登录")
        if page_capabilities.get("has_search"):
            features.append("有搜索功能")
        if page_capabilities.get("has_file_upload"):
            features.append("有文件上传")
        if page_capabilities.get("has_export"):
            features.append("有导出功能")
        if page_capabilities.get("has_pagination"):
            features.append("有分页")
        
        if features:
            lines.append(f"【功能特性】")
            lines.append(", ".join(features))
            lines.append("")
        
        return "\n".join(lines)


# 全局生成器实例
_generator = None


def get_template_generator() -> TemplateBasedGenerator:
    """获取模板生成器实例"""
    global _generator
    if _generator is None:
        _generator = TemplateBasedGenerator()
    return _generator
