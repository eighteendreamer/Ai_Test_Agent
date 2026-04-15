"""
Embedding 向量生成模块

使用 SiliconFlow API（Qwen/Qwen3-Embedding-4B）生成语义向量

配置来源：
  - 优先使用环境变量
  - 回退到内置默认值

向量化内容：页面结构摘要文本（不向量化整个 DOM）
"""
import os
import logging
import hashlib
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Embedding 配置 ──────────────────────────
EMBEDDING_API_KEY = os.getenv(
    "EMBEDDING_API_KEY",
    "sk-bdizvndprpoykyssjfjfiizvgvntemsysnngfdhyasnrjgxv"
)
EMBEDDING_BASE_URL = os.getenv(
    "EMBEDDING_BASE_URL",
    "https://api.siliconflow.cn/v1/embeddings"
)
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "Qwen/Qwen3-Embedding-4B"
)
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))

# 缓存：避免对相同文本反复 Embedding
_embed_cache: dict = {}
_MAX_CACHE_SIZE = 500


class EmbeddingClient:
    """Embedding 客户端封装"""

    def __init__(
        self,
        api_key: str = EMBEDDING_API_KEY,
        base_url: str = EMBEDDING_BASE_URL,
        model: str = EMBEDDING_MODEL,
        dimension: int = EMBEDDING_DIMENSION,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.dimension = dimension

    async def embed(self, text: str) -> List[float]:
        """生成单条文本的 Embedding 向量"""
        if not text.strip():
            return [0.0] * self.dimension

        # 缓存检查
        cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()
        if cache_key in _embed_cache:
            return _embed_cache[cache_key]

        vector = await self._call_api([text])
        if vector and len(vector) > 0:
            result = vector[0]
            # 写入缓存
            if len(_embed_cache) < _MAX_CACHE_SIZE:
                _embed_cache[cache_key] = result
            return result

        logger.warning("[Embedding] API 返回空向量，使用零向量")
        return [0.0] * self.dimension

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成 Embedding 向量"""
        if not texts:
            return []

        # 过滤空文本
        processed = []
        indices = []
        results = [None] * len(texts)

        for i, t in enumerate(texts):
            if not t.strip():
                results[i] = [0.0] * self.dimension
            else:
                cache_key = hashlib.md5(t.encode("utf-8")).hexdigest()
                if cache_key in _embed_cache:
                    results[i] = _embed_cache[cache_key]
                else:
                    processed.append(t)
                    indices.append(i)

        if processed:
            vectors = await self._call_api(processed)
            for idx, vec in zip(indices, vectors):
                results[idx] = vec
                cache_key = hashlib.md5(processed[indices.index(idx)].encode("utf-8")).hexdigest()
                if len(_embed_cache) < _MAX_CACHE_SIZE:
                    _embed_cache[cache_key] = vec

        # 填充失败的位置
        for i in range(len(results)):
            if results[i] is None:
                results[i] = [0.0] * self.dimension

        return results

    async def _call_api(self, texts: List[str]) -> List[List[float]]:
        """调用 Embedding API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self.base_url, json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()

                embeddings = data.get("data", [])
                # 按 index 排序（API 可能乱序返回）
                embeddings.sort(key=lambda x: x.get("index", 0))
                vectors = [e["embedding"] for e in embeddings]

                # 验证维度
                if vectors and len(vectors[0]) != self.dimension:
                    actual_dim = len(vectors[0])
                    logger.info(
                        f"[Embedding] 实际维度 {actual_dim}，更新本地配置（原 {self.dimension}）"
                    )
                    self.dimension = actual_dim

                return vectors

        except httpx.HTTPStatusError as e:
            logger.error(f"[Embedding] API 状态码错误: {e.response.status_code} - {e.response.text[:500]}")
            return [[0.0] * self.dimension] * len(texts)
        except Exception as e:
            logger.error(f"[Embedding] 请求失败: {e}")
            return [[0.0] * self.dimension] * len(texts)


# 全局单例
_client: Optional[EmbeddingClient] = None


def get_embedding_client() -> EmbeddingClient:
    global _client
    if _client is None:
        _client = EmbeddingClient()
    return _client


def reload_embedding_client(config: dict) -> None:
    """用 DB 中的配置重建 EmbeddingClient 单例"""
    global _client
    _client = EmbeddingClient(
        api_key=config.get("embedding_api_key") or EMBEDDING_API_KEY,
        base_url=config.get("embedding_api_url") or EMBEDDING_BASE_URL,
        model=config.get("embedding_model") or EMBEDDING_MODEL,
        dimension=int(config.get("vector_size", EMBEDDING_DIMENSION)),
    )
    logger.info(f"[Embedding] 客户端已重载: model={_client.model} dim={_client.dimension}")
