"""
URL 内容获取器

从各种 URL 获取 API 文档内容

作者: 程序员Eighteen
"""
import aiohttp
import json
import re
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup


class URLFetcher:
    """URL 内容获取器"""
    
    @staticmethod
    async def fetch_swagger_json(url: str) -> Dict[str, Any]:
        """获取 Swagger JSON"""
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {url}")
                return await resp.json()
    
    @staticmethod
    async def fetch_swagger_yaml(url: str) -> Dict[str, Any]:
        """获取 Swagger YAML"""
        import yaml
        
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {url}")
                text = await resp.text()
                return yaml.safe_load(text)
    
    @staticmethod
    async def fetch_swagger_from_ui(url: str) -> Dict[str, Any]:
        """
        从 Swagger UI 页面提取 JSON URL 并获取内容
        
        Swagger UI 通常会在页面中配置 JSON 文档的 URL
        """
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {url}")
                html = await resp.text()
        
        # 解析 HTML 找到 swagger.json 的 URL
        soup = BeautifulSoup(html, 'html.parser')
        
        # 方法1: 查找 <script> 中的 url 配置
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'SwaggerUIBundle' in script.string:
                # 提取 url: "xxx" 或 url: 'xxx'
                match = re.search(r'url:\s*["\']([^"\']+)["\']', script.string)
                if match:
                    json_url = match.group(1)
                    # 处理相对路径
                    json_url = URLFetcher._resolve_url(url, json_url)
                    return await URLFetcher.fetch_swagger_json(json_url)
        
        # 方法2: 常见的默认路径
        from urllib.parse import urlparse, urljoin
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        common_paths = [
            '/swagger.json',
            '/openapi.json',
            '/v2/api-docs',
            '/v3/api-docs',
            '/swagger/v1/swagger.json',
            '/swagger/v2/swagger.json',
            '/api/swagger.json',
        ]
        
        for path in common_paths:
            try:
                json_url = urljoin(base_url, path)
                return await URLFetcher.fetch_swagger_json(json_url)
            except Exception:
                continue
        
        raise Exception("无法从 Swagger UI 页面提取 API 文档 URL")
    
    @staticmethod
    async def fetch_from_redoc(url: str) -> Dict[str, Any]:
        """
        从 Redoc 页面提取 OpenAPI JSON URL 并获取内容
        
        Redoc 通常会在页面中配置 spec-url
        """
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {url}")
                html = await resp.text()
        
        # 解析 HTML 找到 spec-url
        soup = BeautifulSoup(html, 'html.parser')
        
        # 方法1: 查找 <redoc> 标签的 spec-url 属性
        redoc_tag = soup.find('redoc')
        if redoc_tag and redoc_tag.get('spec-url'):
            json_url = redoc_tag['spec-url']
            json_url = URLFetcher._resolve_url(url, json_url)
            return await URLFetcher.fetch_swagger_json(json_url)
        
        # 方法2: 查找 <script> 中的 Redoc.init 配置
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'Redoc.init' in script.string:
                # 提取 specUrl: "xxx" 或 'xxx'
                match = re.search(r'specUrl:\s*["\']([^"\']+)["\']', script.string)
                if match:
                    json_url = match.group(1)
                    json_url = URLFetcher._resolve_url(url, json_url)
                    return await URLFetcher.fetch_swagger_json(json_url)
        
        # 方法3: 常见的默认路径
        from urllib.parse import urlparse, urljoin
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        common_paths = [
            '/openapi.json',
            '/swagger.json',
            '/v2/api-docs',
            '/v3/api-docs',
            '/api/openapi.json',
            '/api/swagger.json',
        ]
        
        for path in common_paths:
            try:
                json_url = urljoin(base_url, path)
                return await URLFetcher.fetch_swagger_json(json_url)
            except Exception:
                continue
        
        raise Exception("无法从 Redoc 页面提取 API 文档 URL")
    
    @staticmethod
    async def fetch_markdown(url: str) -> str:
        """获取 Markdown 内容"""
        # GitHub raw URL 转换
        if 'github.com' in url and '/blob/' in url:
            url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
        
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {url}")
                return await resp.text()
    
    @staticmethod
    def _resolve_url(base_url: str, relative_url: str) -> str:
        """解析相对 URL"""
        from urllib.parse import urlparse, urljoin
        
        # 如果是绝对 URL，直接返回
        if relative_url.startswith('http://') or relative_url.startswith('https://'):
            return relative_url
        
        # 如果是相对路径
        if relative_url.startswith('/'):
            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}{relative_url}"
        
        # 相对于当前目录
        return urljoin(base_url, relative_url)
