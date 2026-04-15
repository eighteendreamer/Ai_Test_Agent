"""
测试用例模板库

提供常见测试场景的用例模板，LLM 只需填充参数即可生成稳定的测试用例

模板类型：
- 登录功能
- 表单提交
- 搜索功能
- CRUD 操作
- 文件上传/下载
- 数据导入/导出
- 权限验证
- 异常场景
"""
from typing import Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class TestCaseTemplate:
    """测试用例模板"""
    template_id: str
    name: str
    category: str  # login/form/search/crud/file/permission/exception
    description: str
    priority: str = "3"
    case_type: str = "功能测试"
    stage: str = "系统测试"
    
    # 模板字段（需要 LLM 填充）
    required_params: List[str] = field(default_factory=list)
    optional_params: List[str] = field(default_factory=list)
    
    # 固定结构
    precondition_template: str = ""
    steps_template: List[str] = field(default_factory=list)
    expected_template: str = ""
    
    def fill(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        使用参数填充模板生成测试用例
        
        Args:
            params: 参数字典，如 {"module": "用户管理", "username_field": "账号", ...}
        
        Returns:
            完整的测试用例字典
        """
        # 验证必填参数
        missing = [p for p in self.required_params if p not in params]
        if missing:
            raise ValueError(f"缺少必填参数: {', '.join(missing)}")
        
        # 填充前置条件
        precondition = self.precondition_template.format(**params)
        
        # 填充测试步骤
        steps = [step.format(**params) for step in self.steps_template]
        
        # 填充预期结果
        expected = self.expected_template.format(**params)
        
        # 生成标题
        title = self.name.format(**params)
        
        return {
            "module": params.get("module", ""),
            "title": title,
            "precondition": precondition,
            "steps": steps,
            "expected": expected,
            "keywords": params.get("keywords", ""),
            "priority": params.get("priority", self.priority),
            "case_type": params.get("case_type", self.case_type),
            "stage": params.get("stage", self.stage),
            "test_data": params.get("test_data", {}),
        }


# ============================================
# 登录功能模板
# ============================================

LOGIN_NORMAL = TestCaseTemplate(
    template_id="login_001",
    name="{module} - 正常登录",
    category="login",
    description="验证使用正确的账号密码能够成功登录系统",
    priority="1",
    required_params=["module", "login_url", "username_field", "password_field", "submit_button", "valid_username", "valid_password"],
    optional_params=["success_indicator"],
    precondition_template="1. 系统已部署并可访问\n2. 测试账号 {valid_username} 已创建且状态正常",
    steps_template=[
        "打开登录页面: {login_url}",
        "在 {username_field} 输入: {valid_username}",
        "在 {password_field} 输入: {valid_password}",
        "点击 {submit_button}",
    ],
    expected_template="成功登录系统，跳转到首页或工作台",
)

LOGIN_WRONG_PASSWORD = TestCaseTemplate(
    template_id="login_002",
    name="{module} - 错误密码登录",
    category="login",
    description="验证使用错误密码无法登录，并提示错误信息",
    priority="2",
    required_params=["module", "login_url", "username_field", "password_field", "submit_button", "valid_username"],
    optional_params=["error_message"],
    precondition_template="1. 系统已部署并可访问\n2. 测试账号 {valid_username} 已创建",
    steps_template=[
        "打开登录页面: {login_url}",
        "在 {username_field} 输入: {valid_username}",
        "在 {password_field} 输入: WrongPassword123!",
        "点击 {submit_button}",
    ],
    expected_template="登录失败，显示错误提示（如：密码错误、账号或密码不正确等）",
)

LOGIN_EMPTY_USERNAME = TestCaseTemplate(
    template_id="login_003",
    name="{module} - 空账号登录",
    category="login",
    description="验证账号为空时无法登录",
    priority="2",
    required_params=["module", "login_url", "username_field", "password_field", "submit_button"],
    precondition_template="系统已部署并可访问",
    steps_template=[
        "打开登录页面: {login_url}",
        "在 {username_field} 留空",
        "在 {password_field} 输入任意密码",
        "点击 {submit_button}",
    ],
    expected_template="登录失败，提示账号不能为空或显示表单验证错误",
)

LOGIN_EMPTY_PASSWORD = TestCaseTemplate(
    template_id="login_004",
    name="{module} - 空密码登录",
    category="login",
    description="验证密码为空时无法登录",
    priority="2",
    required_params=["module", "login_url", "username_field", "password_field", "submit_button", "valid_username"],
    precondition_template="测试账号 {valid_username} 已创建",
    steps_template=[
        "打开登录页面: {login_url}",
        "在 {username_field} 输入: {valid_username}",
        "在 {password_field} 留空",
        "点击 {submit_button}",
    ],
    expected_template="登录失败，提示密码不能为空或显示表单验证错误",
)

LOGIN_SQL_INJECTION = TestCaseTemplate(
    template_id="login_005",
    name="{module} - SQL注入测试",
    category="login",
    description="验证登录接口对SQL注入攻击的防护",
    priority="1",
    case_type="安全测试",
    required_params=["module", "login_url", "username_field", "password_field", "submit_button"],
    precondition_template="系统已部署并可访问",
    steps_template=[
        "打开登录页面: {login_url}",
        "在 {username_field} 输入: admin' OR '1'='1",
        "在 {password_field} 输入: ' OR '1'='1",
        "点击 {submit_button}",
    ],
    expected_template="登录失败，系统正确处理特殊字符，不发生SQL注入漏洞",
)

# ============================================
# 表单提交模板
# ============================================

FORM_SUBMIT_NORMAL = TestCaseTemplate(
    template_id="form_001",
    name="{module} - {form_name}正常提交",
    category="form",
    description="验证表单填写正确数据后能够成功提交",
    priority="1",
    required_params=["module", "form_name", "form_url", "submit_button"],
    optional_params=["field_list", "success_message"],
    precondition_template="1. 已登录系统\n2. 有权限访问 {form_name}",
    steps_template=[
        "打开表单页面: {form_url}",
        "填写所有必填字段（正确格式）",
        "点击 {submit_button}",
    ],
    expected_template="表单提交成功，显示成功提示或跳转到列表页",
)

FORM_REQUIRED_FIELD_EMPTY = TestCaseTemplate(
    template_id="form_002",
    name="{module} - {form_name}必填项为空",
    category="form",
    description="验证必填字段为空时无法提交表单",
    priority="2",
    required_params=["module", "form_name", "form_url", "required_field", "submit_button"],
    precondition_template="1. 已登录系统\n2. 有权限访问 {form_name}",
    steps_template=[
        "打开表单页面: {form_url}",
        "将 {required_field} 留空",
        "填写其他字段",
        "点击 {submit_button}",
    ],
    expected_template="表单提交失败，提示 {required_field} 不能为空",
)

FORM_INVALID_FORMAT = TestCaseTemplate(
    template_id="form_003",
    name="{module} - {form_name}格式校验",
    category="form",
    description="验证字段格式校验功能",
    priority="2",
    required_params=["module", "form_name", "form_url", "field_name", "invalid_value", "submit_button"],
    precondition_template="1. 已登录系统\n2. 有权限访问 {form_name}",
    steps_template=[
        "打开表单页面: {form_url}",
        "在 {field_name} 输入无效格式: {invalid_value}",
        "填写其他字段",
        "点击 {submit_button}",
    ],
    expected_template="表单提交失败，提示 {field_name} 格式不正确",
)

# ============================================
# 搜索功能模板
# ============================================

SEARCH_NORMAL = TestCaseTemplate(
    template_id="search_001",
    name="{module} - 正常搜索",
    category="search",
    description="验证使用关键词能够搜索到匹配的结果",
    priority="1",
    required_params=["module", "search_url", "search_field", "search_button", "keyword"],
    precondition_template="1. 已登录系统\n2. 数据库中存在包含关键词 {keyword} 的数据",
    steps_template=[
        "打开搜索页面: {search_url}",
        "在 {search_field} 输入: {keyword}",
        "点击 {search_button}",
    ],
    expected_template="显示包含关键词 {keyword} 的搜索结果列表",
)

SEARCH_EMPTY = TestCaseTemplate(
    template_id="search_002",
    name="{module} - 空关键词搜索",
    category="search",
    description="验证空关键词搜索的处理",
    priority="2",
    required_params=["module", "search_url", "search_field", "search_button"],
    precondition_template="已登录系统",
    steps_template=[
        "打开搜索页面: {search_url}",
        "在 {search_field} 留空",
        "点击 {search_button}",
    ],
    expected_template="显示所有数据或提示请输入搜索关键词",
)

SEARCH_NO_RESULT = TestCaseTemplate(
    template_id="search_003",
    name="{module} - 无结果搜索",
    category="search",
    description="验证搜索不存在的关键词时的提示",
    priority="2",
    required_params=["module", "search_url", "search_field", "search_button"],
    precondition_template="已登录系统",
    steps_template=[
        "打开搜索页面: {search_url}",
        "在 {search_field} 输入不存在的关键词: XYZ_NOT_EXIST_12345",
        "点击 {search_button}",
    ],
    expected_template="显示无搜索结果或提示未找到匹配数据",
)

# ============================================
# CRUD 操作模板
# ============================================

CRUD_CREATE = TestCaseTemplate(
    template_id="crud_001",
    name="{module} - 新增{entity}",
    category="crud",
    description="验证新增功能",
    priority="1",
    required_params=["module", "entity", "create_url", "submit_button"],
    precondition_template="1. 已登录系统\n2. 有新增{entity}的权限",
    steps_template=[
        "打开新增页面: {create_url}",
        "填写所有必填字段",
        "点击 {submit_button}",
    ],
    expected_template="新增成功，{entity}保存到数据库，显示成功提示",
)

CRUD_UPDATE = TestCaseTemplate(
    template_id="crud_002",
    name="{module} - 编辑{entity}",
    category="crud",
    description="验证编辑功能",
    priority="1",
    required_params=["module", "entity", "edit_button", "submit_button"],
    precondition_template="1. 已登录系统\n2. 数据库中存在可编辑的{entity}",
    steps_template=[
        "在列表页找到目标{entity}",
        "点击 {edit_button}",
        "修改部分字段内容",
        "点击 {submit_button}",
    ],
    expected_template="编辑成功，{entity}信息更新，显示成功提示",
)

CRUD_DELETE = TestCaseTemplate(
    template_id="crud_003",
    name="{module} - 删除{entity}",
    category="crud",
    description="验证删除功能",
    priority="1",
    required_params=["module", "entity", "delete_button", "confirm_button"],
    precondition_template="1. 已登录系统\n2. 数据库中存在可删除的{entity}",
    steps_template=[
        "在列表页找到目标{entity}",
        "点击 {delete_button}",
        "在确认对话框点击 {confirm_button}",
    ],
    expected_template="删除成功，{entity}从列表中移除，数据库记录删除或标记为已删除",
)

CRUD_VIEW = TestCaseTemplate(
    template_id="crud_004",
    name="{module} - 查看{entity}详情",
    category="crud",
    description="验证详情查看功能",
    priority="2",
    required_params=["module", "entity", "view_button"],
    precondition_template="1. 已登录系统\n2. 数据库中存在{entity}数据",
    steps_template=[
        "在列表页找到目标{entity}",
        "点击 {view_button}",
    ],
    expected_template="显示{entity}的完整详细信息",
)

# ============================================
# 文件操作模板
# ============================================

FILE_UPLOAD = TestCaseTemplate(
    template_id="file_001",
    name="{module} - 文件上传",
    category="file",
    description="验证文件上传功能",
    priority="1",
    required_params=["module", "upload_url", "file_input", "upload_button"],
    optional_params=["allowed_formats", "max_size"],
    precondition_template="1. 已登录系统\n2. 有文件上传权限",
    steps_template=[
        "打开上传页面: {upload_url}",
        "点击 {file_input} 选择文件",
        "选择符合要求的文件",
        "点击 {upload_button}",
    ],
    expected_template="文件上传成功，显示上传成功提示或文件列表",
)

FILE_UPLOAD_INVALID_FORMAT = TestCaseTemplate(
    template_id="file_002",
    name="{module} - 上传不支持的文件格式",
    category="file",
    description="验证文件格式校验",
    priority="2",
    required_params=["module", "upload_url", "file_input", "upload_button", "invalid_format"],
    precondition_template="1. 已登录系统\n2. 有文件上传权限",
    steps_template=[
        "打开上传页面: {upload_url}",
        "点击 {file_input} 选择文件",
        "选择 {invalid_format} 格式的文件",
        "点击 {upload_button}",
    ],
    expected_template="上传失败，提示不支持该文件格式",
)

FILE_DOWNLOAD = TestCaseTemplate(
    template_id="file_003",
    name="{module} - 文件下载",
    category="file",
    description="验证文件下载功能",
    priority="1",
    required_params=["module", "download_button"],
    precondition_template="1. 已登录系统\n2. 存在可下载的文件",
    steps_template=[
        "在文件列表找到目标文件",
        "点击 {download_button}",
    ],
    expected_template="文件下载成功，浏览器开始下载文件",
)

# ============================================
# 权限验证模板
# ============================================

PERMISSION_NO_ACCESS = TestCaseTemplate(
    template_id="permission_001",
    name="{module} - 无权限访问",
    category="permission",
    description="验证无权限用户无法访问受保护的功能",
    priority="1",
    case_type="权限测试",
    required_params=["module", "protected_url", "no_permission_user"],
    precondition_template="1. 使用无权限账号 {no_permission_user} 登录\n2. 该账号没有访问 {module} 的权限",
    steps_template=[
        "尝试访问: {protected_url}",
    ],
    expected_template="访问被拒绝，显示无权限提示或跳转到403页面",
)

PERMISSION_ROLE_CHECK = TestCaseTemplate(
    template_id="permission_002",
    name="{module} - 角色权限验证",
    category="permission",
    description="验证不同角色的权限隔离",
    priority="1",
    case_type="权限测试",
    required_params=["module", "role", "restricted_action"],
    precondition_template="使用 {role} 角色账号登录",
    steps_template=[
        "尝试执行 {restricted_action}",
    ],
    expected_template="{role} 角色无法执行 {restricted_action}，显示权限不足提示",
)

# ============================================
# 模板库管理
# ============================================

class TemplateLibrary:
    """测试用例模板库"""
    
    def __init__(self):
        self.templates: Dict[str, TestCaseTemplate] = {}
        self._register_default_templates()
    
    def _register_default_templates(self):
        """注册默认模板"""
        templates = [
            # 登录
            LOGIN_NORMAL, LOGIN_WRONG_PASSWORD, LOGIN_EMPTY_USERNAME,
            LOGIN_EMPTY_PASSWORD, LOGIN_SQL_INJECTION,
            # 表单
            FORM_SUBMIT_NORMAL, FORM_REQUIRED_FIELD_EMPTY, FORM_INVALID_FORMAT,
            # 搜索
            SEARCH_NORMAL, SEARCH_EMPTY, SEARCH_NO_RESULT,
            # CRUD
            CRUD_CREATE, CRUD_UPDATE, CRUD_DELETE, CRUD_VIEW,
            # 文件
            FILE_UPLOAD, FILE_UPLOAD_INVALID_FORMAT, FILE_DOWNLOAD,
            # 权限
            PERMISSION_NO_ACCESS, PERMISSION_ROLE_CHECK,
        ]
        
        for template in templates:
            self.templates[template.template_id] = template
    
    def get_template(self, template_id: str) -> TestCaseTemplate:
        """获取指定模板"""
        return self.templates.get(template_id)
    
    def get_templates_by_category(self, category: str) -> List[TestCaseTemplate]:
        """获取指定分类的所有模板"""
        return [t for t in self.templates.values() if t.category == category]
    
    def list_all_templates(self) -> List[Dict[str, Any]]:
        """列出所有模板"""
        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "required_params": t.required_params,
                "optional_params": t.optional_params,
            }
            for t in self.templates.values()
        ]
    
    def register_template(self, template: TestCaseTemplate):
        """注册自定义模板"""
        self.templates[template.template_id] = template


# 全局模板库实例
_template_library = TemplateLibrary()


def get_template_library() -> TemplateLibrary:
    """获取模板库实例"""
    return _template_library
