"""
ApiFox 格式解析器

ApiFox 是国产 API 管理工具，格式类似 Postman 但有一些差异

作者: 程序员Eighteen
"""
import json
from typing import Dict, List, Any, Optional
from .base import BaseImporter


class ApiFoxImporter(BaseImporter):
    """ApiFox 格式解析器"""
    
    @staticmethod
    def detect(data: Dict[str, Any]) -> float:
        """
        检测是否为 ApiFox 格式
        
        ApiFox 格式特征:
        - 有 "apifoxProject" 字段
        - 或者有 "info" 且 info.name 包含 "apifox"
        - 或者有 "folders" 和 "apis" 字段
        """
        if not isinstance(data, dict):
            return 0.0
        
        score = 0.0
        
        # 明确的 ApiFox 标识
        if 'apifoxProject' in data:
            return 1.0
        
        # 检查 info 中的标识
        info = data.get('info', {})
        if isinstance(info, dict):
            name = info.get('name', '').lower()
            if 'apifox' in name:
                score += 0.5
        
        # 检查结构特征
        if 'folders' in data and 'apis' in data:
            score += 0.3
        
        # 检查是否有 apiCollection
        if 'apiCollection' in data:
            score += 0.2
        
        return min(score, 1.0)
    
    def parse(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析 ApiFox 文件
        
        Args:
            data: ApiFox JSON 数据
            
        Returns:
            标准化的接口数据
        """
        # 提取项目信息
        project_info = data.get('apifoxProject', data.get('info', {}))
        service_name = project_info.get('name', 'ApiFox API')
        version = project_info.get('version', '1.0.0')
        
        # 解析接口列表
        endpoints = []
        
        # ApiFox 可能有多种结构
        if 'apis' in data:
            # 新版 ApiFox 格式
            for api in data.get('apis', []):
                endpoint = self._parse_api(api)
                if endpoint:
                    endpoints.append(endpoint)
        elif 'apiCollection' in data:
            # 旧版 ApiFox 格式
            for item in data.get('apiCollection', []):
                endpoint = self._parse_collection_item(item)
                if endpoint:
                    endpoints.append(endpoint)
        elif 'item' in data:
            # Postman 兼容格式
            endpoints = self._parse_items(data.get('item', []))
        
        return {
            'type': 'apifox',
            'service_name': service_name,
            'version': version,
            'endpoints': endpoints
        }
    
    def _parse_api(self, api: Dict) -> Optional[Dict]:
        """解析 ApiFox API 对象"""
        try:
            # 提取基本信息
            method = api.get('method', 'GET').upper()
            path = api.get('path', '/')
            name = api.get('name', '')
            description = api.get('description', '')
            
            # 提取参数
            params = self._extract_params(api)
            
            # 提取响应示例
            success_example = self._extract_response(api)
            
            return {
                'method': method,
                'path': path,
                'summary': name,
                'description': description,
                'params': json.dumps(params, ensure_ascii=False, indent=2) if params else None,
                'success_example': success_example,
                'error_example': None,
                'notes': None
            }
        except Exception as e:
            print(f"[ApiFox] 解析 API 失败: {e}")
            return None
    
    def _parse_collection_item(self, item: Dict) -> Optional[Dict]:
        """解析 ApiFox Collection Item"""
        try:
            request = item.get('request', {})
            
            method = request.get('method', 'GET').upper()
            
            # 提取路径
            url = request.get('url', {})
            if isinstance(url, str):
                path = url
            elif isinstance(url, dict):
                path = url.get('path', '/')
                if isinstance(path, list):
                    path = '/' + '/'.join(path)
            else:
                path = '/'
            
            name = item.get('name', '')
            description = item.get('description', '')
            
            # 提取参数
            params = []
            
            # Query 参数
            if isinstance(url, dict):
                for param in url.get('query', []):
                    params.append({
                        'name': param.get('key', ''),
                        'value': param.get('value', ''),
                        'description': param.get('description', ''),
                        'type': 'query'
                    })
            
            # Header 参数
            for header in request.get('header', []):
                params.append({
                    'name': header.get('key', ''),
                    'value': header.get('value', ''),
                    'description': header.get('description', ''),
                    'type': 'header'
                })
            
            # Body 参数
            body = request.get('body', {})
            body_mode = body.get('mode', '')
            
            if body_mode == 'raw':
                raw_data = body.get('raw', '')
                if raw_data:
                    try:
                        body_json = json.loads(raw_data)
                        params.append({
                            'name': 'body',
                            'value': body_json,
                            'type': 'json'
                        })
                    except:
                        params.append({
                            'name': 'body',
                            'value': raw_data,
                            'type': 'raw'
                        })
            elif body_mode == 'formdata':
                for param in body.get('formdata', []):
                    params.append({
                        'name': param.get('key', ''),
                        'value': param.get('value', ''),
                        'description': param.get('description', ''),
                        'type': 'form'
                    })
            
            # 提取响应
            success_example = None
            responses = item.get('response', [])
            if responses:
                for resp in responses:
                    if resp.get('code') in [200, '200']:
                        body = resp.get('body', '')
                        if body:
                            try:
                                success_example = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
                            except:
                                success_example = body
                        break
            
            return {
                'method': method,
                'path': path,
                'summary': name,
                'description': description,
                'params': json.dumps(params, ensure_ascii=False, indent=2) if params else None,
                'success_example': success_example,
                'error_example': None,
                'notes': None
            }
        except Exception as e:
            print(f"[ApiFox] 解析 Collection Item 失败: {e}")
            return None
    
    def _parse_items(self, items: List) -> List[Dict]:
        """递归解析 item 列表（Postman 兼容格式）"""
        endpoints = []
        
        for item in items:
            if 'request' in item:
                # 这是一个接口
                endpoint = self._parse_collection_item(item)
                if endpoint:
                    endpoints.append(endpoint)
            elif 'item' in item:
                # 这是一个文件夹，递归处理
                endpoints.extend(self._parse_items(item['item']))
        
        return endpoints
    
    def _extract_params(self, api: Dict) -> List[Dict]:
        """提取 API 参数"""
        params = []
        
        # Query 参数
        for param in api.get('parameters', {}).get('query', []):
            params.append({
                'name': param.get('name', ''),
                'type': 'query',
                'required': param.get('required', False),
                'description': param.get('description', ''),
                'example': param.get('example', '')
            })
        
        # Path 参数
        for param in api.get('parameters', {}).get('path', []):
            params.append({
                'name': param.get('name', ''),
                'type': 'path',
                'required': param.get('required', True),
                'description': param.get('description', ''),
                'example': param.get('example', '')
            })
        
        # Header 参数
        for param in api.get('parameters', {}).get('header', []):
            params.append({
                'name': param.get('name', ''),
                'type': 'header',
                'required': param.get('required', False),
                'description': param.get('description', ''),
                'example': param.get('example', '')
            })
        
        # Body 参数
        request_body = api.get('requestBody', {})
        if request_body:
            content = request_body.get('content', {})
            for content_type, schema_info in content.items():
                schema = schema_info.get('schema', {})
                if schema:
                    params.append({
                        'name': 'body',
                        'type': 'json',
                        'content_type': content_type,
                        'schema': schema
                    })
        
        return params
    
    def _extract_response(self, api: Dict) -> Optional[str]:
        """提取响应示例"""
        responses = api.get('responses', {})
        
        # 优先查找 200 响应
        for status_code in ['200', '201', 200, 201]:
            if status_code in responses:
                response = responses[status_code]
                content = response.get('content', {})
                
                for content_type, schema_info in content.items():
                    if 'application/json' in content_type:
                        example = schema_info.get('example')
                        if example:
                            return json.dumps(example, ensure_ascii=False, indent=2)
                        
                        # 尝试从 schema 生成示例
                        schema = schema_info.get('schema', {})
                        if schema:
                            example = self._generate_example_from_schema(schema)
                            if example:
                                return json.dumps(example, ensure_ascii=False, indent=2)
        
        return None
    
    def _generate_example_from_schema(self, schema: Dict) -> Any:
        """从 schema 生成示例数据"""
        schema_type = schema.get('type', 'object')
        
        if schema_type == 'object':
            properties = schema.get('properties', {})
            example = {}
            for key, prop in properties.items():
                example[key] = self._generate_example_from_schema(prop)
            return example
        elif schema_type == 'array':
            items = schema.get('items', {})
            return [self._generate_example_from_schema(items)]
        elif schema_type == 'string':
            return schema.get('example', 'string')
        elif schema_type == 'integer':
            return schema.get('example', 0)
        elif schema_type == 'number':
            return schema.get('example', 0.0)
        elif schema_type == 'boolean':
            return schema.get('example', True)
        else:
            return None
