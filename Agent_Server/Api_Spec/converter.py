"""
格式转换器

将解析后的接口数据转换为标准 Markdown 格式

作者: 程序员Eighteen
"""
from typing import Dict, Any, List


def to_markdown(parsed_data: Dict[str, Any]) -> str:
    """
    将解析后的数据转换为 Markdown
    
    Args:
        parsed_data: {
            'service_name': str,
            'endpoints': List[Dict],
            'metadata': Dict
        }
    
    Returns:
        Markdown 格式的文档内容
    """
    service_name = parsed_data.get('service_name', 'API 文档')
    endpoints = parsed_data.get('endpoints', [])
    metadata = parsed_data.get('metadata', {})
    
    lines = []
    
    # 标题
    lines.append(f"# API 文档 - {service_name}")
    lines.append("")
    
    # 元数据
    if metadata:
        format_type = metadata.get('format', '')
        version = metadata.get('version', '')
        description = metadata.get('description', '')
        
        if format_type or version:
            lines.append(f"**来源**: {format_type} {version}".strip())
            lines.append("")
        
        if description:
            lines.append(f"**说明**: {description}")
            lines.append("")
    
    lines.append("---")
    lines.append("")
    
    # 接口列表
    for endpoint in endpoints:
        lines.extend(_endpoint_to_markdown(endpoint))
        lines.append("")
    
    return '\n'.join(lines)


def _endpoint_to_markdown(endpoint: Dict[str, Any]) -> List[str]:
    """将单个接口转换为 Markdown"""
    lines = []
    
    method = endpoint.get('method', 'GET')
    path = endpoint.get('path', '/')
    summary = endpoint.get('summary', '')
    description = endpoint.get('description')
    params = endpoint.get('params')
    success_example = endpoint.get('success_example')
    error_example = endpoint.get('error_example')
    notes = endpoint.get('notes')
    
    # 标题
    lines.append(f"## {method} {path}")
    lines.append("")
    
    # 功能描述
    if summary:
        lines.append(f"**功能**: {summary}")
        lines.append("")
    
    # 详细描述
    if description:
        lines.append(description)
        lines.append("")
    
    # 请求参数
    if params:
        lines.append("**请求参数**:")
        lines.append("")
        lines.append(params)
        lines.append("")
    
    # 成功响应
    if success_example:
        lines.append("**成功响应** (200):")
        lines.append("```json")
        lines.append(success_example)
        lines.append("```")
        lines.append("")
    
    # 错误响应
    if error_example:
        lines.append("**错误响应**:")
        lines.append("```json")
        lines.append(error_example)
        lines.append("```")
        lines.append("")
    
    # 说明
    if notes:
        lines.append("**说明**:")
        lines.append(notes)
        lines.append("")
    
    lines.append("---")
    
    return lines
