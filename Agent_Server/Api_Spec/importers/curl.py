"""
cURL 命令解析器

解析 cURL 命令字符串，提取 HTTP 请求信息

作者: 程序员Eighteen
"""
import re
import json
import shlex
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, parse_qs
from .base import BaseImporter


class CurlImporter(BaseImporter):
    """cURL 命令解析器"""
    
    @staticmethod
    def detect(data: Any) -> float:
        """
        检测是否为 cURL 命令
        
        cURL 命令特征:
        - 字符串类型
        - 以 "curl" 开头
        """
        if not isinstance(data, str):
            return 0.0
        
        data_stripped = data.strip()
        
        # 检查是否以 curl 开头
        if data_stripped.startswith('curl '):
            return 1.0
        
        # 检查是否包含 curl 命令（可能有换行）
        if 'curl ' in data_stripped and ('-X ' in data_stripped or '--request' in data_stripped):
            return 0.8
        
        return 0.0
    
    def parse(self, data: str) -> Dict[str, Any]:
        """
        解析 cURL 命令
        
        Args:
            data: cURL 命令字符串
            
        Returns:
            标准化的接口数据
        """
        # 清理命令（移除换行符和多余空格）
        command = self._clean_command(data)
        
        # 解析命令
        parsed = self._parse_curl_command(command)
        
        # 构建接口数据
        endpoint = self._build_endpoint(parsed)
        
        return {
            'type': 'curl',
            'service_name': self._extract_service_name(parsed['url']),
            'version': '1.0',
            'endpoints': [endpoint]
        }
    
    def _clean_command(self, command: str) -> str:
        """清理 cURL 命令"""
        # 移除行尾的反斜杠和换行符
        command = re.sub(r'\\\s*\n\s*', ' ', command)
        # 移除多余空格
        command = re.sub(r'\s+', ' ', command)
        return command.strip()
    
    def _parse_curl_command(self, command: str) -> Dict[str, Any]:
        """解析 cURL 命令参数"""
        result = {
            'url': '',
            'method': 'GET',
            'headers': {},
            'data': None,
            'params': {}
        }
        
        try:
            # 使用 shlex 分割命令（处理引号）
            parts = shlex.split(command)
        except:
            # 如果 shlex 失败，使用简单分割
            parts = command.split()
        
        i = 0
        while i < len(parts):
            part = parts[i]
            
            # 跳过 curl 命令本身
            if part == 'curl':
                i += 1
                continue
            
            # 解析 URL（没有 - 前缀的参数）
            if not part.startswith('-') and not result['url']:
                result['url'] = part.strip('\'"')
                i += 1
                continue
            
            # 解析方法
            if part in ['-X', '--request']:
                if i + 1 < len(parts):
                    result['method'] = parts[i + 1].upper()
                    i += 2
                    continue
            
            # 解析请求头
            if part in ['-H', '--header']:
                if i + 1 < len(parts):
                    header = parts[i + 1]
                    if ':' in header:
                        key, value = header.split(':', 1)
                        result['headers'][key.strip()] = value.strip()
                    i += 2
                    continue
            
            # 解析数据
            if part in ['-d', '--data', '--data-raw', '--data-binary']:
                if i + 1 < len(parts):
                    result['data'] = parts[i + 1]
                    i += 2
                    continue
            
            # 解析 JSON 数据
            if part in ['--data-json']:
                if i + 1 < len(parts):
                    result['data'] = parts[i + 1]
                    i += 2
                    continue
            
            i += 1
        
        return result
    
    def _build_endpoint(self, parsed: Dict) -> Dict[str, Any]:
        """构建接口数据"""
        url = parsed['url']
        method = parsed['method']
        
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
        for key, value in parsed['headers'].items():
            # 过滤不重要的头
            if key.lower() not in ['user-agent', 'accept-encoding', 'connection']:
                headers.append({
                    'name': key,
                    'value': value,
                    'type': 'header'
                })
        
        # 处理请求体
        body_params = []
        request_body = None
        
        if parsed['data']:
            data = parsed['data']
            
            # 尝试解析为 JSON
            try:
                request_body = json.loads(data)
            except:
                # 尝试解析为表单数据
                if '&' in data or '=' in data:
                    for pair in data.split('&'):
                        if '=' in pair:
                            key, value = pair.split('=', 1)
                            body_params.append({
                                'name': key,
                                'value': value,
                                'type': 'form'
                            })
                else:
                    request_body = data
        
        # 合并所有参数
        all_params = query_params + headers + body_params
        params_json = None
        
        if all_params or request_body:
            if isinstance(request_body, dict):
                params_json = json.dumps({
                    'params': all_params,
                    'body': request_body
                }, ensure_ascii=False, indent=2)
            elif all_params:
                params_json = json.dumps(all_params, ensure_ascii=False, indent=2)
        
        # 生成摘要
        summary = self._generate_summary(method, path)
        
        return {
            'method': method,
            'path': path,
            'summary': summary,
            'description': f"从 cURL 命令导入的接口",
            'params': params_json,
            'success_example': None,
            'error_example': None,
            'notes': f"原始 URL: {url}"
        }
    
    def _extract_service_name(self, url: str) -> str:
        """从 URL 提取服务名称"""
        parsed = urlparse(url)
        if parsed.netloc:
            return f"{parsed.netloc} API"
        return "cURL Imported API"
    
    def _generate_summary(self, method: str, path: str) -> str:
        """根据 method 和 path 生成接口摘要"""
        parts = [p for p in path.split('/') if p and not p.startswith('{')]
        resource = parts[-1] if parts else 'resource'
        
        action_map = {
            'GET': '获取',
            'POST': '创建',
            'PUT': '更新',
            'PATCH': '修改',
            'DELETE': '删除'
        }
        action = action_map.get(method, '操作')
        
        return f"{action}{resource}"
