"""
Swagger/OpenAPI 导入器

解析 Swagger 2.0 和 OpenAPI 3.0+ 格式

作者: 程序员Eighteen
"""
import json
import yaml
from typing import Dict, Any, List, Optional
from .base import BaseImporter, Endpoint


class OpenAPIImporter(BaseImporter):
    """Swagger/OpenAPI 导入器"""
    
    @staticmethod
    def detect(content: Any) -> float:
        """检测是否为 Swagger/OpenAPI 格式"""
        if not isinstance(content, dict):
            return 0.0
        
        # 检查 OpenAPI 3.x
        if 'openapi' in content:
            version = str(content.get('openapi', ''))
            if version.startswith('3.'):
                return 0.95
            return 0.8
        
        # 检查 Swagger 2.0
        if 'swagger' in content:
            version = str(content.get('swagger', ''))
            if version.startswith('2.'):
                return 0.95
            return 0.8
        
        # 检查是否有 paths 字段（可能是 OpenAPI 但缺少版本号）
        if 'paths' in content and 'info' in content:
            return 0.6
        
        return 0.0
    
    def parse(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """解析 Swagger/OpenAPI"""
        info = content.get('info', {})
        service_name = info.get('title', 'API')
        
        # 判断版本
        is_openapi3 = 'openapi' in content
        
        endpoints = []
        paths = content.get('paths', {})
        
        for path, methods in paths.items():
            for method, spec in methods.items():
                # 跳过非 HTTP 方法的字段（如 parameters, servers 等）
                if method.lower() not in ('get', 'post', 'put', 'delete', 'patch', 'head', 'options'):
                    continue
                
                endpoint = self._parse_operation(
                    method, path, spec, content, is_openapi3
                )
                if endpoint:
                    endpoints.append(endpoint)
        
        return {
            'service_name': service_name,
            'endpoints': [ep.to_dict() for ep in endpoints],
            'metadata': {
                'format': 'openapi' if is_openapi3 else 'swagger',
                'version': content.get('openapi') or content.get('swagger'),
                'description': info.get('description', '')
            }
        }
    
    def _parse_operation(
        self, 
        method: str, 
        path: str, 
        spec: Dict, 
        root: Dict,
        is_openapi3: bool
    ) -> Optional[Endpoint]:
        """解析单个操作"""
        
        # 提取 summary 和 description
        summary = spec.get('summary', '')
        description = spec.get('description')
        
        # 提取参数
        params = self._extract_params(spec, root, is_openapi3)
        
        # 提取响应示例
        success_example, error_example = self._extract_responses(spec, root, is_openapi3)
        
        # 提取 notes
        notes = self._extract_notes(spec)
        
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
    
    def _extract_params(self, spec: Dict, root: Dict, is_openapi3: bool) -> Optional[str]:
        """提取参数"""
        params_parts = []
        
        # Path/Query/Header 参数
        parameters = spec.get('parameters', [])
        if parameters:
            for param in parameters:
                # 处理 $ref
                if '$ref' in param:
                    param = self._resolve_ref(param['$ref'], root)
                
                name = param.get('name', '')
                in_type = param.get('in', '')
                required = param.get('required', False)
                desc = param.get('description', '')
                
                req_str = '(必填)' if required else '(选填)'
                params_parts.append(f"- `{name}` ({in_type}): {desc} {req_str}")
        
        # Request Body (OpenAPI 3.0)
        if is_openapi3:
            request_body = spec.get('requestBody', {})
            if request_body:
                # 处理 $ref
                if '$ref' in request_body:
                    request_body = self._resolve_ref(request_body['$ref'], root)
                
                content = request_body.get('content', {})
                
                # 优先处理 application/json
                if 'application/json' in content:
                    schema = content['application/json'].get('schema', {})
                    body_desc = self._schema_to_description(schema, root)
                    if body_desc:
                        params_parts.append("\n**请求体** (JSON):")
                        params_parts.append("```json")
                        params_parts.append(body_desc)
                        params_parts.append("```")
        
        # Request Body (Swagger 2.0)
        else:
            for param in parameters:
                if param.get('in') == 'body':
                    schema = param.get('schema', {})
                    body_desc = self._schema_to_description(schema, root)
                    if body_desc:
                        params_parts.append("\n**请求体** (JSON):")
                        params_parts.append("```json")
                        params_parts.append(body_desc)
                        params_parts.append("```")
        
        return '\n'.join(params_parts) if params_parts else None
    
    def _extract_responses(self, spec: Dict, root: Dict, is_openapi3: bool) -> tuple:
        """提取响应示例"""
        success_example = None
        error_example = None
        
        responses = spec.get('responses', {})
        
        for status_code, response in responses.items():
            # 处理 $ref
            if '$ref' in response:
                response = self._resolve_ref(response['$ref'], root)
            
            code = int(status_code) if status_code.isdigit() else 0
            
            # 提取示例
            example = None
            
            if is_openapi3:
                content = response.get('content', {})
                if 'application/json' in content:
                    json_content = content['application/json']
                    
                    # 优先使用 example
                    if 'example' in json_content:
                        example = json_content['example']
                    # 其次使用 examples
                    elif 'examples' in json_content:
                        examples = json_content['examples']
                        if examples:
                            first_example = list(examples.values())[0]
                            example = first_example.get('value')
                    # 最后从 schema 生成
                    elif 'schema' in json_content:
                        schema = json_content['schema']
                        example = self._schema_to_example(schema, root)
            else:
                # Swagger 2.0
                if 'examples' in response:
                    examples = response['examples']
                    if 'application/json' in examples:
                        example = examples['application/json']
                elif 'schema' in response:
                    schema = response['schema']
                    example = self._schema_to_example(schema, root)
            
            # 格式化示例
            if example:
                try:
                    if isinstance(example, str):
                        example = json.loads(example)
                    formatted = json.dumps(example, ensure_ascii=False, indent=2)
                except:
                    formatted = str(example)
                
                # 2xx 为成功响应
                if 200 <= code < 300 and not success_example:
                    success_example = formatted
                # 4xx/5xx 为错误响应
                elif (400 <= code < 600) and not error_example:
                    error_example = formatted
        
        return success_example, error_example
    
    def _schema_to_description(self, schema: Dict, root: Dict, indent: int = 0) -> str:
        """将 schema 转换为参数描述"""
        # 处理 $ref
        if '$ref' in schema:
            schema = self._resolve_ref(schema['$ref'], root)
        
        schema_type = schema.get('type', 'object')
        
        if schema_type == 'object':
            properties = schema.get('properties', {})
            required = schema.get('required', [])
            
            lines = ['{']
            for prop_name, prop_schema in properties.items():
                is_required = prop_name in required
                desc = prop_schema.get('description', '')
                prop_type = prop_schema.get('type', 'string')
                
                req_str = ' (必填)' if is_required else ''
                desc_str = f' // {desc}{req_str}' if desc or is_required else ''
                
                lines.append(f'  "{prop_name}": "<{prop_type}>"{desc_str}')
            lines.append('}')
            
            return '\n'.join(lines)
        
        return '{}'
    
    def _schema_to_example(self, schema: Dict, root: Dict) -> Any:
        """从 schema 生成示例数据"""
        # 处理 $ref
        if '$ref' in schema:
            schema = self._resolve_ref(schema['$ref'], root)
        
        # 如果有 example，直接返回
        if 'example' in schema:
            return schema['example']
        
        schema_type = schema.get('type', 'object')
        
        if schema_type == 'object':
            properties = schema.get('properties', {})
            example = {}
            for prop_name, prop_schema in properties.items():
                example[prop_name] = self._schema_to_example(prop_schema, root)
            return example
        
        elif schema_type == 'array':
            items = schema.get('items', {})
            return [self._schema_to_example(items, root)]
        
        elif schema_type == 'string':
            return schema.get('example', 'string')
        
        elif schema_type == 'integer':
            return schema.get('example', 0)
        
        elif schema_type == 'number':
            return schema.get('example', 0.0)
        
        elif schema_type == 'boolean':
            return schema.get('example', True)
        
        return None
    
    def _resolve_ref(self, ref: str, root: Dict) -> Dict:
        """解析 $ref 引用"""
        # $ref 格式: #/components/schemas/User 或 #/definitions/User
        parts = ref.split('/')
        current = root
        
        for part in parts:
            if part == '#':
                continue
            current = current.get(part, {})
        
        return current
    
    def _extract_notes(self, spec: Dict) -> Optional[str]:
        """提取备注信息"""
        notes_parts = []
        
        # 标签
        tags = spec.get('tags', [])
        if tags:
            notes_parts.append(f"标签: {', '.join(tags)}")
        
        # 是否废弃
        if spec.get('deprecated'):
            notes_parts.append("⚠️ 该接口已废弃")
        
        # 安全要求
        security = spec.get('security', [])
        if security:
            notes_parts.append(f"需要认证")
        
        return '\n'.join(notes_parts) if notes_parts else None
