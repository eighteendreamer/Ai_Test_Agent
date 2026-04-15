"""
URL 类型检测器

检测 API 文档 URL 的类型

作者: 程序员Eighteen
"""
import re
from typing import Tuple


class URLDetector:
    """URL 类型检测器"""
    
    # URL 模式匹配规则
    PATTERNS = {
        'swagger_json': [
            r'swagger\.json$',
            r'openapi\.json$',
            r'/v\d+/api-docs$',
            r'/api/swagger\.json$',
            r'/swagger/v\d+/swagger\.json$',
        ],
        'swagger_yaml': [
            r'swagger\.ya?ml$',
            r'openapi\.ya?ml$',
            r'/api/swagger\.ya?ml$',
        ],
        'swagger_ui': [
            r'swagger-ui',
            r'/swagger/',
            r'/api-docs',
            r'/doc\.html',
        ],
        'redoc': [
            r'/redoc',
            r'redoc\.html',
            r'/api/redoc',
        ],
        'apifox': [
            r'apifox\.cn/apidoc/shared',
            r'apifox\.com/apidoc/shared',
        ],
        'postman': [
            r'documenter\.getpostman\.com',
            r'postman\.com/collections',
        ],
        'yapi': [
            r'yapi\.',
            r'/project/\d+/interface',
        ],
        'markdown': [
            r'\.md$',
            r'github\.com/.+\.md',
            r'gitlab\.com/.+\.md',
            r'raw\.githubusercontent\.com',
        ],
    }
    
    @classmethod
    def detect(cls, url: str) -> Tuple[str, float]:
        """
        检测 URL 类型（基于 URL 模式）
        
        Args:
            url: API 文档 URL
            
        Returns:
            (type, confidence) - 类型和置信度 (0.0-1.0)
        """
        url_lower = url.lower()
        
        for doc_type, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url_lower):
                    return doc_type, 0.9
        
        return 'unknown', 0.0
    
    @classmethod
    async def detect_with_request(cls, url: str) -> Tuple[str, float]:
        """
        通过 HTTP 请求检测类型（更准确）
        
        Args:
            url: API 文档 URL
            
        Returns:
            (type, confidence) - 类型和置信度
        """
        import aiohttp
        
        # 先尝试 URL 模式匹配
        url_type, url_confidence = cls.detect(url)
        if url_confidence >= 0.9:
            return url_type, url_confidence
        
        # 发送 HTTP 请求检测
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 先发送 HEAD 请求
                async with session.head(url, allow_redirects=True) as resp:
                    content_type = resp.headers.get('Content-Type', '').lower()
                    
                    # 根据 Content-Type 判断
                    if 'application/json' in content_type:
                        # 获取部分内容判断是否是 Swagger
                        async with session.get(url) as get_resp:
                            text = await get_resp.text()
                            text_lower = text.lower()
                            
                            if '"swagger"' in text_lower or '"openapi"' in text_lower:
                                return 'swagger_json', 0.95
                            elif '"info"' in text_lower and '"item"' in text_lower:
                                return 'postman', 0.9
                            return 'json', 0.7
                    
                    elif 'yaml' in content_type or 'text/plain' in content_type:
                        return 'swagger_yaml', 0.8
                    
                    elif 'text/html' in content_type:
                        # HTML 页面，可能是 Swagger UI 或 Redoc
                        async with session.get(url) as get_resp:
                            html = await get_resp.text()
                            html_lower = html.lower()
                            
                            if 'redoc' in html_lower:
                                return 'redoc', 0.9
                            elif 'swagger' in html_lower or 'openapi' in html_lower:
                                return 'swagger_ui', 0.85
                            return 'html', 0.5
                    
                    elif 'text/markdown' in content_type:
                        return 'markdown', 0.9
        
        except Exception as e:
            print(f"[URLDetector] HTTP 检测失败: {e}")
        
        # 如果 HTTP 检测失败，返回 URL 模式匹配结果
        return url_type if url_confidence > 0 else 'unknown', url_confidence
