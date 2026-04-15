"""
Markdown 接口文件解析器

从 Markdown 文件中提取接口信息（method, path, summary, params, examples）
支持多种常见 Markdown 接口文档格式

作者: 程序员Eighteen
"""
import re
import json
from typing import List, Dict, Any, Optional, Tuple


def parse_api_markdown(content: str) -> Dict[str, Any]:
    """
    解析 Markdown 接口文档，提取 endpoints

    支持的格式：
    1. 标题中直接写 `GET /path` 
    2. 正文中用 `- **路径**: /path` + `- **方法**: GET` 的 key-value 格式
    3. Markdown 表格中的 路径/方法 列
    4. 全文正则扫描兜底

    Returns:
        {
            "endpoints": [{ method, path, summary, description, params, 
                           success_example, error_example, notes }],
            "warnings": []
        }
    """
    endpoints = []
    warnings = []

    # 策略1: 按标题分段，在每段中提取接口信息
    section_endpoints = _parse_by_sections(content)
    if section_endpoints:
        endpoints.extend(section_endpoints)

    # 策略2: 从表格中提取
    if not endpoints:
        table_endpoints = _parse_from_tables(content)
        if table_endpoints:
            endpoints.extend(table_endpoints)

    # 策略3: 全文正则扫描兜底
    if not endpoints:
        scan_endpoints, scan_warnings = _scan_endpoints_from_text(content)
        endpoints.extend(scan_endpoints)
        warnings.extend(scan_warnings)

    # 去重
    endpoints = _deduplicate(endpoints)

    if not endpoints:
        warnings.append("未能从文档中解析出任何接口信息")

    return {"endpoints": endpoints, "warnings": warnings}


def _parse_by_sections(content: str) -> List[Dict[str, Any]]:
    """按标题分段解析"""
    endpoints = []

    # 按 ## 或 ### 分段
    # re.split 带捕获组结果: [前文, level, title, body, level, title, body, ...]
    sections = re.split(r'^(#{1,4})\s+(.+)$', content, flags=re.MULTILINE)

    # 从 index 1 开始，每 3 个为一组: (level, title, body)
    i = 1
    while i + 2 < len(sections):
        level = sections[i]       # '##' or '###'
        title = sections[i + 1].strip()
        body = sections[i + 2]
        i += 3

        # 尝试从标题提取 method+path
        mp = _extract_method_path_inline(title)
        if mp:
            ep = _build_endpoint(mp[0], mp[1], title, body)
            endpoints.append(ep)
            continue

        # 尝试从正文提取 key-value 格式的 method+path
        mp = _extract_method_path_kv(body)
        if mp:
            ep = _build_endpoint(mp[0], mp[1], title, body)
            endpoints.append(ep)
            continue

    return endpoints


def _extract_method_path_inline(text: str) -> Optional[Tuple[str, str]]:
    """从文本中提取内联的 HTTP method + path，如 `GET /v1/xxx`"""
    pattern = r'\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+[`]?(/[^\s`\)\]]*)'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).upper(), match.group(2)
    return None


def _extract_method_path_kv(text: str) -> Optional[Tuple[str, str]]:
    """
    从 key-value 格式中提取 method + path
    支持：
    - **路径**: `/v1/xxx`
    - **方法**: `GET`
    或：
    - 路径: /v1/xxx
    - 方法: GET
    """
    path = None
    method = None

    # 匹配路径
    path_patterns = [
        r'[\*\*]*路径[\*\*]*\s*[:：]\s*[`]?(/[^\s`\)]*)',
        r'[\*\*]*[Pp]ath[\*\*]*\s*[:：]\s*[`]?(/[^\s`\)]*)',
        r'[\*\*]*URL[\*\*]*\s*[:：]\s*[`]?(/[^\s`\)]*)',
        r'[\*\*]*接口[\*\*]*\s*[:：]\s*[`]?(/[^\s`\)]*)',
        r'[\*\*]*[Ee]ndpoint[\*\*]*\s*[:：]\s*[`]?(/[^\s`\)]*)',
    ]
    for p in path_patterns:
        m = re.search(p, text)
        if m:
            path = m.group(1)
            break

    # 匹配方法
    method_patterns = [
        r'[\*\*]*方法[\*\*]*\s*[:：]\s*[`]?(GET|POST|PUT|DELETE|PATCH)',
        r'[\*\*]*[Mm]ethod[\*\*]*\s*[:：]\s*[`]?(GET|POST|PUT|DELETE|PATCH)',
        r'[\*\*]*请求方式[\*\*]*\s*[:：]\s*[`]?(GET|POST|PUT|DELETE|PATCH)',
        r'[\*\*]*请求方法[\*\*]*\s*[:：]\s*[`]?(GET|POST|PUT|DELETE|PATCH)',
        r'[\*\*]*[Tt]ype[\*\*]*\s*[:：]\s*[`]?(GET|POST|PUT|DELETE|PATCH)',
    ]
    for p in method_patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            method = m.group(1).upper()
            break

    if path and method:
        return method, path
    return None


def _parse_from_tables(content: str) -> List[Dict[str, Any]]:
    """从 Markdown 表格中提取接口信息"""
    endpoints = []

    # 匹配表格行
    table_pattern = r'^\|(.+)\|$'
    lines = content.split('\n')

    header_indices = {}
    in_table = False

    for line in lines:
        line = line.strip()
        if not re.match(table_pattern, line):
            in_table = False
            header_indices = {}
            continue

        cells = [c.strip() for c in line.split('|')[1:-1]]

        # 跳过分隔行
        if all(re.match(r'^[-:]+$', c) for c in cells):
            continue

        # 检测表头
        if not in_table:
            for idx, cell in enumerate(cells):
                cell_lower = cell.lower().strip()
                if cell_lower in ['路径', 'path', 'url', '接口', 'endpoint', '接口路径']:
                    header_indices['path'] = idx
                elif cell_lower in ['方法', 'method', '请求方式', '请求方法', 'http方法']:
                    header_indices['method'] = idx
                elif cell_lower in ['功能', 'summary', '描述', '说明', 'description', '接口说明', '接口描述']:
                    header_indices['summary'] = idx

            if 'path' in header_indices and 'method' in header_indices:
                in_table = True
            continue

        # 解析数据行
        if in_table and header_indices:
            path_idx = header_indices.get('path')
            method_idx = header_indices.get('method')
            summary_idx = header_indices.get('summary')

            if path_idx is not None and method_idx is not None:
                if path_idx < len(cells) and method_idx < len(cells):
                    path = cells[path_idx].strip('` ')
                    method = cells[method_idx].strip('` ').upper()
                    summary = cells[summary_idx].strip('` ') if summary_idx is not None and summary_idx < len(cells) else ''

                    if path.startswith('/') and method in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'):
                        endpoints.append({
                            "method": method,
                            "path": path,
                            "summary": summary,
                            "description": None,
                            "params": None,
                            "success_example": None,
                            "error_example": None,
                            "notes": None
                        })

    return endpoints


def _build_endpoint(method: str, path: str, title: str, body: str) -> Dict[str, Any]:
    """从标题和正文构建完整的 endpoint 信息"""
    summary = _clean_summary(title, method, path)

    # 提取参数描述
    params = _extract_section(body, ['参数', 'param', 'request', '请求参数'])
    # 提取请求体（JSON 代码块）
    request_body = _extract_code_block(body, ['请求体', 'request body', 'body', '请求参数'])
    # 如果 params 为空但有请求体代码块，用请求体作为 params
    if not params and request_body:
        params = request_body
    # 提取成功响应
    success_example = _extract_code_block(body, ['成功', 'success', '正常响应', '响应示例', '返回示例'])
    # 提取错误响应
    error_example = _extract_code_block(body, ['失败', 'error', 'fail', '异常', '错误响应'])
    # 提取说明
    notes = _extract_section(body, ['说明', 'note', '备注', '注意'])
    # 提取功能描述
    desc = _extract_kv_value(body, ['功能', 'description', '描述', '接口说明'])

    return {
        "method": method,
        "path": path,
        "summary": summary or desc or '',
        "description": desc,
        "params": params,
        "success_example": success_example,
        "error_example": error_example,
        "notes": notes
    }


def _extract_kv_value(text: str, keys: List[str]) -> Optional[str]:
    """提取 key-value 格式的值"""
    for key in keys:
        pattern = rf'[\*\*]*{key}[\*\*]*\s*[:：]\s*(.+?)(?:\n|$)'
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip('`')
    return None


def _extract_section(text: str, keywords: List[str]) -> Optional[str]:
    """提取某个关键词段落的内容"""
    for kw in keywords:
        # 匹配 `- **关键词**:` 后面的内容块
        pattern = rf'[\*\*]*{kw}[\*\*]*\s*[:：]\s*\n((?:\s+[-*].*\n?)+)'
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()

        # 匹配标题后的内容
        pattern2 = rf'#+\s*.*{kw}.*\n([\s\S]*?)(?=\n#+|\Z)'
        m2 = re.search(pattern2, text, re.IGNORECASE)
        if m2:
            content = m2.group(1).strip()
            if content:
                return content[:2000]
    return None


def _extract_code_block(text: str, keywords: List[str]) -> Optional[str]:
    """提取关键词附近的代码块"""
    for kw in keywords:
        # 找到关键词位置
        idx = text.lower().find(kw.lower())
        if idx == -1:
            continue
        # 在关键词后找最近的代码块
        after = text[idx:]
        code_match = re.search(r'```(?:json)?\s*\n([\s\S]*?)```', after)
        if code_match:
            return code_match.group(1).strip()
    return None


def _clean_summary(title: str, method: str = '', path: str = '') -> str:
    """清理标题作为 summary"""
    cleaned = title
    # 去掉序号前缀
    cleaned = re.sub(r'^\d+[\.\)、]\s*', '', cleaned)
    # 去掉 method/path
    if method:
        cleaned = re.sub(rf'\b{method}\b', '', cleaned, flags=re.IGNORECASE)
    if path:
        cleaned = cleaned.replace(path, '')
    cleaned = re.sub(r'[`#*]', '', cleaned).strip(' -—:：/')
    return cleaned or title


def _scan_endpoints_from_text(content: str) -> Tuple[List[Dict], List[str]]:
    """全文扫描提取 endpoints（兜底策略）"""
    endpoints = []
    warnings = []

    # 策略A: 匹配 `METHOD /path` 模式
    pattern = r'\b(GET|POST|PUT|DELETE|PATCH)\s+[`]?(/[^\s`\)\]]+)'
    matches = re.findall(pattern, content, re.IGNORECASE)

    seen = set()
    for method, path in matches:
        key = f"{method.upper()} {path}"
        if key not in seen:
            seen.add(key)
            endpoints.append({
                "method": method.upper(),
                "path": path,
                "summary": "",
                "description": None,
                "params": None,
                "success_example": None,
                "error_example": None,
                "notes": None
            })

    # 策略B: 匹配 key-value 格式
    if not endpoints:
        kv_result = _extract_method_path_kv(content)
        if kv_result:
            endpoints.append({
                "method": kv_result[0],
                "path": kv_result[1],
                "summary": "",
                "description": None,
                "params": None,
                "success_example": None,
                "error_example": None,
                "notes": None
            })

    if not endpoints:
        warnings.append("未找到可识别的接口定义模式")

    return endpoints, warnings


def _deduplicate(endpoints: List[Dict]) -> List[Dict]:
    """去重，保留信息最丰富的版本"""
    seen = {}
    for ep in endpoints:
        key = f"{ep['method']} {ep['path']}"
        if key not in seen:
            seen[key] = ep
        else:
            # 保留信息更多的
            existing = seen[key]
            for field in ['summary', 'description', 'params', 'success_example', 'error_example', 'notes']:
                if not existing.get(field) and ep.get(field):
                    existing[field] = ep[field]
    return list(seen.values())
