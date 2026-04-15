"""
Postman Collection 导入器

解析 Postman Collection v2.0/v2.1 格式

作者: 程序员Eighteen
"""
import json
import re
from typing import Dict, Any, List, Optional
from .base import BaseImporter, Endpoint


class PostmanImporter(BaseImporter):
    """Postman Collection 导入器"""
    
    @staticmethod
    def detect(content: Any) -> float:
        """检测是否为 Postman Collection 格式"""
        if not isinstance(content, dict):
            return 0.0
        
        # 检查特征字段
        has_info = 'info' in content
        has_item = 'item' in content
        
        if has_info and has_item:
            info = content.get('info', {})
            # 检查 schema 字段
            schema = info.get('schema', '')
            if 'postman' in schema.lower():
                return 0.95
            return 0.8
        
        return 0.0
    
    def parse(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """解析 Postman Collection"""
        info = content.get('info', {})
        service_name = info.get('name', 'Postman API')
        
        endpoints = []
        items = content.get('item', [])
        
        # 递归解析 item（支持文件夹嵌套）
        self._parse_items(items, endpoints)
        
        return {
            'service_name': service_name,
            'endpoints': [ep.to_dict() for ep in endpoints],
            'metadata': {
                'format': 'postman',
                'version': info.get('schema', ''),
                'description': info.get('description', '')
            }
        }
    
    def _parse_items(self, items: List[Dict], endpoints: List[Endpoint], folder_path: str = ''):
        """递归解析 items"""
        for item in items:
            # 如果是文件夹，递归处理
            if 'item' in item and 'request' not in item:
                folder_name = item.get('name', '')
                new_path = f"{folder_path}/{folder_name}" if folder_path else folder_name
                self._parse_items(item['item'], endpoints, new_path)
            
            # 如果是请求
            elif 'request' in item:
                endpoint = self._parse_request(item, folder_path)
                if endpoint:
                    endpoints.append(endpoint)
    
    def _parse_request(self, item: Dict, folder_path: str = '') -> Optional[Endpoint]:
        """解析单个请求"""
        request = item.get('request', {})
        
        # 提取 method
        method = request.get('method', 'GET')
        
        # 提取 URL
        url = request.get('url', {})
        if isinstance(url, str):
            path = self._extract_path_from_url(url)
        elif isinstance(url, dict):
            path = self._build_path_from_url_object(url)
        else:
            return None
        
        # 提取 summary
        summary = item.get('name', '')
        if folder_path:
            summary = f"[{folder_path}] {summary}"
        
        # 提取 description
        description = item.get('description') or request.get('description')
        
        # 提取请求参数
        params = self._extract_params(request)
        
        # 提取响应示例
        success_example, error_example = self._extract_responses(item.get('response', []))
        
        # 提取 notes
        notes = self._extract_notes(request)
        
        return Endpoint(
            method=method,
            path=path,
            summary=summary,
            description=description,
            params=params,
            success_example=success_example,
            error_example=error_example,
            notes=notes
        )
    
    def _extract_path_from_url(self, url: str) -> str:
        """从 URL 字符串提取路径"""
        # 移除协议和域名
        match = re.search(r'https?://[^/]+(/[^\?#]*)', url)
        if match:
            return match.group(1)
        
        # 如果没有协议，尝试提取路径部分
        if url.startswith('/'):
            return url.split('?')[0].split('#')[0]
        
        return '/' + url.split('?')[0].split('#')[0]
    
    def _build_path_from_url_object(self, url: Dict) -> str:
        """从 URL 对象构建路径"""
        # Postman URL 对象格式
        path_parts = url.get('path', [])
        if isinstance(path_parts, list):
            path = '/' + '/'.join(str(p) for p in path_parts if p)
        else:
            path = str(path_parts)
        
        # 处理路径变量 {{var}}
        path = re.sub(r'\{\{([^}]+)\}\}', r'{\1}', path)
        
        return path or '/'
    
    def _extract_params(self, request: Dict) -> Optional[str]:
        """提取请求参数"""
        params_parts = []
        
        # Query 参数
        url = request.get('url', {})
        if isinstance(url, dict):
            query = url.get('query', [])
            if query:
                params_parts.append("**Query 参数**:")
                for q in query:
                    key = q.get('key', '')
                    desc = q.get('description', '')
                    params_parts.append(f"- `{key}`: {desc if desc else '(无说明)'}")
        
        # Header 参数
        headers = request.get('header', [])
        if headers:
            params_parts.append("\n**Headers**:")
            for h in headers:
                key = h.get('key', '')
                desc = h.get('description', '')
                if key.lower() not in ('content-type', 'user-agent'):
                    params_parts.append(f"- `{key}`: {desc if desc else '(无说明)'}")
        
        # Body 参数
        body = request.get('body', {})
        if body:
            mode = body.get('mode', '')
            
            if mode == 'raw':
                raw_content = body.get('raw', '')
                if raw_content:
                    params_parts.append("\n**请求体** (JSON):")
                    params_parts.append("```json")
                    params_parts.append(raw_content)
                    params_parts.append("```")
            
            elif mode == 'formdata':
                formdata = body.get('formdata', [])
                if formdata:
                    params_parts.append("\n**Form Data**:")
                    for f in formdata:
                        key = f.get('key', '')
                        desc = f.get('description', '')
                        params_parts.append(f"- `{key}`: {desc if desc else '(无说明)'}")
            
            elif mode == 'urlencoded':
                urlencoded = body.get('urlencoded', [])
                if urlencoded:
                    params_parts.append("\n**URL Encoded**:")
                    for u in urlencoded:
                        key = u.get('key', '')
                        desc = u.get('description', '')
                        params_parts.append(f"- `{key}`: {desc if desc else '(无说明)'}")
        
        return '\n'.join(params_parts) if params_parts else None
    
    def _extract_responses(self, responses: List[Dict]) -> tuple:
        """提取响应示例"""
        success_example = None
        error_example = None
        
        for resp in responses:
            code = resp.get('code', 0)
            body = resp.get('body', '')
            
            if not body:
                continue
            
            # 尝试格式化 JSON
            try:
                parsed = json.loads(body)
                formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
            except:
                formatted = body
            
            # 2xx 为成功响应
            if 200 <= code < 300 and not success_example:
                success_example = formatted
            # 4xx/5xx 为错误响应
            elif (400 <= code < 600) and not error_example:
                error_example = formatted
        
        return success_example, error_example
    
    def _extract_notes(self, request: Dict) -> Optional[str]:
        """提取备注信息"""
        notes_parts = []
        
        # Auth 信息
        auth = request.get('auth', {})
        if auth:
            auth_type = auth.get('type', '')
            if auth_type:
                notes_parts.append(f"认证方式: {auth_type}")
        
        # 其他说明
        # ...
        
        return '\n'.join(notes_parts) if notes_parts else None
