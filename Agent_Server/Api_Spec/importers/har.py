"""
HAR (HTTP Archive) 格式解析器

HAR 是浏览器导出的网络请求记录格式，包含完整的 HTTP 请求和响应信息

作者: 程序员Eighteen
"""
import json
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse, parse_qs
from .base import BaseImporter


class HARImporter(BaseImporter):
    """HAR 文件解析器"""
    
    @staticmethod
    def detect(data: Dict[str, Any]) -> float:
        """
        检测是否为 HAR 格式
        
        HAR 格式特征:
        - 顶层有 "log" 字段
        - log 中有 "entries" 数组
        - log 中有 "version" 字段
        """
        if not isinstance(data, dict):
            return 0.0
        
        if 'log' not in data:
            return 0.0
        
        log = data['log']
        if not isinstance(log, dict):
            return 0.0
        
        score = 0.0
        
        # 必须有 entries
        if 'entries' in log and isinstance(log['entries'], list):
            score += 0.6
        
        # 有 version 字段
        if 'version' in log:
            score += 0.2
        
        # 有 creator 字段
        if 'creator' in log:
            score += 0.1
        
        # 有 pages 字段
        if 'pages' in log:
            score += 0.1
        
        return min(score, 1.0)
    
    def parse(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析 HAR 文件
        
        Args:
            data: HAR JSON 数据
            
        Returns:
            标准化的接口数据
        """
        log = data.get('log', {})
        entries = log.get('entries', [])
        
        # 提取服务名称
        service_name = self._extract_service_name(log, entries)
        
        # 解析每个请求
        endpoints = []
        seen = set()  # 去重
        
        for entry in entries:
            try:
                endpoint = self._parse_entry(entry)
                if endpoint:
                    # 使用 method + path 去重
                    key = f"{endpoint['method']}:{endpoint['path']}"
                    if key not in seen:
                        seen.add(key)
                        endpoints.append(endpoint)
            except Exception as e:
                print(f"[HAR] 解析 entry 失败: {e}")
                continue
        
        return {
            'type': 'har',
            'service_name': service_name,
            'version': log.get('version', '1.2'),
            'endpoints': endpoints
        }
    
    def _extract_service_name(self, log: Dict, entries: List) -> str:
        """从 HAR 中提取服务名称"""
        # 尝试从 creator 获取
        creator = log.get('creator', {})
        if creator.get('name'):
            return f"{creator['name']} API"
        
        # 尝试从第一个请求的域名获取
        if entries:
            first_url = entries[0].get('request', {}).get('url', '')
            if first_url:
                parsed = urlparse(first_url)
                if parsed.netloc:
                    return f"{parsed.netloc} API"
        
        return "HAR Imported API"
    
    def _parse_entry(self, entry: Dict) -> Optional[Dict]:
        """解析单个 HAR entry"""
        request = entry.get('request', {})
        response = entry.get('response', {})
        
        # 提取 URL 和方法
        url = request.get('url', '')
        method = request.get('method', 'GET').upper()
        
        if not url:
            return None
        
        # 解析 URL
        parsed_url = urlparse(url)
        path = parsed_url.path or '/'
        
        # 提取查询参数
        query_params = []
        if parsed_url.query:
            parsed_qs = parse_qs(parsed_url.query)
            for key, values in parsed_qs.items():
                query_params.append({
                    'name': key,
                    'value': values[0] if values else '',
                    'type': 'query'
                })
        
        # 提取请求头
        headers = []
        for header in request.get('headers', []):
            name = header.get('name', '')
            # 过滤掉一些不重要的头
            if name.lower() not in ['cookie', 'user-agent', 'accept-encoding', 'connection']:
                headers.append({
                    'name': name,
                    'value': header.get('value', ''),
                    'type': 'header'
                })
        
        # 提取请求体
        post_data = request.get('postData', {})
        body_params = []
        request_body = None
        
        if post_data:
            mime_type = post_data.get('mimeType', '')
            text = post_data.get('text', '')
            
            if 'json' in mime_type and text:
                try:
                    request_body = json.loads(text)
                except:
                    request_body = text
            elif 'form' in mime_type:
                # 表单数据
                for param in post_data.get('params', []):
                    body_params.append({
                        'name': param.get('name', ''),
                        'value': param.get('value', ''),
                        'type': 'form'
                    })
            else:
                request_body = text
        
        # 合并所有参数
        all_params = query_params + headers + body_params
        params_json = json.dumps(all_params, ensure_ascii=False, indent=2) if all_params else None
        
        # 如果有 JSON body，添加到参数说明中
        if request_body and isinstance(request_body, dict):
            params_json = json.dumps({
                'params': all_params,
                'body': request_body
            }, ensure_ascii=False, indent=2)
        
        # 提取响应
        success_example = None
        status = response.get('status', 0)
        
        if 200 <= status < 300:
            content = response.get('content', {})
            text = content.get('text', '')
            mime_type = content.get('mimeType', '')
            
            if text and 'json' in mime_type:
                try:
                    success_example = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except:
                    success_example = text
        
        # 生成摘要（从 URL 路径推断）
        summary = self._generate_summary(method, path)
        
        return {
            'method': method,
            'path': path,
            'summary': summary,
            'description': f"从 HAR 导入的接口 (状态码: {status})",
            'params': params_json,
            'success_example': success_example,
            'error_example': None,
            'notes': f"原始 URL: {url}"
        }
    
    def _generate_summary(self, method: str, path: str) -> str:
        """根据 method 和 path 生成接口摘要"""
        # 提取路径最后一段作为资源名
        parts = [p for p in path.split('/') if p and not p.startswith('{')]
        resource = parts[-1] if parts else 'resource'
        
        # 根据 method 生成动作
        action_map = {
            'GET': '获取',
            'POST': '创建',
            'PUT': '更新',
            'PATCH': '修改',
            'DELETE': '删除'
        }
        action = action_map.get(method, '操作')
        
        return f"{action}{resource}"
