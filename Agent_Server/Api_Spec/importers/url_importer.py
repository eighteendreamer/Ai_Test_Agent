"""
URL 导入统一入口

从 URL 导入 API 文档的统一接口

作者: 程序员Eighteen
"""
from typing import Dict, Any
from .url_detector import URLDetector
from .url_fetcher import URLFetcher
from .openapi import OpenAPIImporter
from .postman import PostmanImporter


class URLImporter:
    """URL 导入器（统一入口）"""
    
    @staticmethod
    async def import_from_url(url: str) -> Dict[str, Any]:
        """
        从 URL 导入 API 文档
        
        Args:
            url: API 文档 URL
            
        Returns:
            {
                'type': 'swagger_json' | 'markdown' | ...,
                'service_name': str,
                'endpoints': List[Dict],
                'metadata': Dict,
                'content': str (仅 markdown 类型)
            }
        """
        # 1. 检测 URL 类型
        doc_type, confidence = await URLDetector.detect_with_request(url)
        
        print(f"[URLImporter] 检测到文档类型: {doc_type} (置信度: {confidence})")
        
        if confidence < 0.5:
            raise Exception(f"无法识别的 URL 类型，置信度过低: {confidence}")
        
        # 2. 根据类型获取内容并解析
        if doc_type == 'swagger_json':
            content = await URLFetcher.fetch_swagger_json(url)
            importer = OpenAPIImporter()
            parsed = importer.parse(content)
            parsed['type'] = 'swagger_json'
            return parsed
        
        elif doc_type == 'swagger_yaml':
            content = await URLFetcher.fetch_swagger_yaml(url)
            importer = OpenAPIImporter()
            parsed = importer.parse(content)
            parsed['type'] = 'swagger_yaml'
            return parsed
        
        elif doc_type == 'swagger_ui':
            content = await URLFetcher.fetch_swagger_from_ui(url)
            importer = OpenAPIImporter()
            parsed = importer.parse(content)
            parsed['type'] = 'swagger_ui'
            return parsed
        
        elif doc_type == 'redoc':
            content = await URLFetcher.fetch_from_redoc(url)
            importer = OpenAPIImporter()
            parsed = importer.parse(content)
            parsed['type'] = 'redoc'
            return parsed
        
        elif doc_type == 'postman':
            content = await URLFetcher.fetch_swagger_json(url)  # Postman 也是 JSON
            importer = PostmanImporter()
            parsed = importer.parse(content)
            parsed['type'] = 'postman'
            return parsed
        
        elif doc_type == 'markdown':
            content = await URLFetcher.fetch_markdown(url)
            # Markdown 直接返回内容，不需要解析
            return {
                'type': 'markdown',
                'content': content,
                'service_name': 'Imported API',
                'endpoints': [],
                'metadata': {
                    'format': 'markdown',
                    'source_url': url
                }
            }
        
        else:
            # 未知类型，尝试作为 JSON 解析
            try:
                content = await URLFetcher.fetch_swagger_json(url)
                
                # 尝试识别是 Swagger 还是 Postman
                if 'swagger' in content or 'openapi' in content:
                    importer = OpenAPIImporter()
                    parsed = importer.parse(content)
                    parsed['type'] = 'swagger_json'
                    return parsed
                
                elif 'info' in content and 'item' in content:
                    importer = PostmanImporter()
                    parsed = importer.parse(content)
                    parsed['type'] = 'postman'
                    return parsed
                
                else:
                    raise Exception(f"无法识别的 JSON 格式")
            
            except Exception as e:
                raise Exception(f"不支持的文档类型或无法解析: {str(e)}")
