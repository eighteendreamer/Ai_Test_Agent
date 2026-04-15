from __future__ import annotations

import hashlib
import math

import httpx

from src.core.config import Settings


class EmbeddingService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._vector_size = settings.embedding_vector_size

    async def embed_text(self, text: str) -> list[float]:
        content = (text or "").strip()
        if not content:
            return [0.0] * self._vector_size

        provider = (self._settings.embedding_provider or "local_hash").strip().lower()
        if provider == "local_hash":
            return self._hash_embedding(content)
        if provider in {"openai", "openai_compatible"}:
            return await self._openai_compatible_embedding(content)
        return self._hash_embedding(content)

    async def _openai_compatible_embedding(self, text: str) -> list[float]:
        if not self._settings.embedding_base_url or not self._settings.embedding_model:
            return self._hash_embedding(text)

        headers = {"Content-Type": "application/json"}
        if self._settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {self._settings.embedding_api_key}"
        payload = {
            "model": self._settings.embedding_model,
            "input": text,
        }
        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = await client.post(
                    self._settings.embedding_base_url.rstrip("/") + "/embeddings",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return self._hash_embedding(text)

        body = response.json()
        data = body.get("data") or []
        if not data:
            return self._hash_embedding(text)

        vector = data[0].get("embedding") or []
        if not isinstance(vector, list) or not vector:
            return self._hash_embedding(text)
        return self._normalize_vector([float(item) for item in vector], self._vector_size)

    def _hash_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self._vector_size
        tokens = [token for token in text.lower().split() if token]
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for index in range(self._vector_size):
                byte = digest[index % len(digest)]
                vector[index] += (byte / 255.0) - 0.5
        return self._normalize_vector(vector, self._vector_size)

    def _normalize_vector(self, vector: list[float], expected_size: int) -> list[float]:
        padded = list(vector[:expected_size])
        if len(padded) < expected_size:
            padded.extend([0.0] * (expected_size - len(padded)))
        norm = math.sqrt(sum(item * item for item in padded))
        if norm <= 0:
            return [0.0] * expected_size
        return [item / norm for item in padded]
