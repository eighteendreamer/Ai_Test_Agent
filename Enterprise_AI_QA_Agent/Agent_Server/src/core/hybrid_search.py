"""D4 Hybrid Search: BM25 + vector scoring replacing crude token counting.

The current pgvector memory search uses simple token overlap for text scoring.
This module provides a proper hybrid scorer that combines:
  - BM25 text scoring (term frequency + inverse document frequency)
  - Vector cosine similarity (semantic embedding distance)
  - Recency bonus (newer memories score higher)

The final score is a weighted combination: alpha * bm25 + beta * vector + gamma * recency.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class HybridSearchResult:
    """A single search result with decomposed scores."""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    bm25_score: float = 0.0
    vector_score: float = 0.0
    recency_score: float = 0.0
    final_score: float = 0.0


class BM25Scorer:
    """Okapi BM25 text scorer.

    Computes term-frequency weighted scores with inverse document frequency.
    Suitable for keyword-based retrieval from memory/knowledge bases.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._doc_count = 0
        self._avg_doc_length = 0.0
        self._doc_lengths: list[int] = []
        self._term_doc_freq: dict[str, int] = {}

    def index(self, documents: list[str]) -> None:
        """Build BM25 index from a list of documents."""
        self._doc_count = len(documents)
        self._doc_lengths = []
        self._term_doc_freq = {}

        for doc in documents:
            tokens = self._tokenize(doc)
            self._doc_lengths.append(len(tokens))
            unique_terms = set(tokens)
            for term in unique_terms:
                self._term_doc_freq[term] = self._term_doc_freq.get(term, 0) + 1

        total_length = sum(self._doc_lengths)
        self._avg_doc_length = total_length / max(self._doc_count, 1)

    def score(self, query: str, document: str, doc_index: int = 0) -> float:
        """Score a single query-document pair."""
        if self._doc_count == 0:
            return 0.0

        query_tokens = self._tokenize(query)
        doc_tokens = self._tokenize(document)
        doc_length = len(doc_tokens)
        term_freq: dict[str, int] = {}
        for t in doc_tokens:
            term_freq[t] = term_freq.get(t, 0) + 1

        total_score = 0.0
        for qt in query_tokens:
            if qt not in term_freq:
                continue
            tf = term_freq[qt]
            df = self._term_doc_freq.get(qt, 0)
            idf = math.log((self._doc_count - df + 0.5) / (df + 0.5) + 1.0)
            numerator = tf * (self._k1 + 1)
            denominator = tf + self._k1 * (1 - self._b + self._b * doc_length / max(self._avg_doc_length, 1))
            total_score += idf * numerator / denominator

        return total_score

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace + lowercase tokenizer."""
        return text.lower().split()


class HybridSearchScorer:
    """Combined BM25 + vector + recency scorer.

    Weights:
      - alpha: BM25 text score weight (default 0.4)
      - beta: vector cosine similarity weight (default 0.4)
      - gamma: recency bonus weight (default 0.2)
    """

    def __init__(
        self,
        alpha: float = 0.4,
        beta: float = 0.4,
        gamma: float = 0.2,
        recency_half_life_days: float = 30.0,
    ) -> None:
        self._alpha = alpha
        self._beta = beta
        self._gamma = gamma
        self._recency_half_life_seconds = recency_half_life_days * 86400

    def score(
        self,
        query: str,
        content: str,
        *,
        vector_similarity: float = 0.0,
        created_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HybridSearchResult:
        """Score a single document against a query."""
        bm25 = BM25Scorer()
        bm25.index([content])
        bm25_score = bm25.score(query, content)
        bm25_normalized = min(bm25_score / 5.0, 1.0)

        vector_normalized = max(0.0, min(vector_similarity, 1.0))

        recency_score = 0.0
        if created_at is not None:
            age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
            recency_score = math.exp(-0.693 * age_seconds / max(self._recency_half_life_seconds, 1))

        final = (
            self._alpha * bm25_normalized
            + self._beta * vector_normalized
            + self._gamma * recency_score
        )

        return HybridSearchResult(
            content=content,
            metadata=metadata or {},
            bm25_score=bm25_normalized,
            vector_score=vector_normalized,
            recency_score=recency_score,
            final_score=final,
        )

    def rank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[HybridSearchResult]:
        """Score and rank a list of candidates.

        Each candidate dict should have:
          - content: str
          - vector_similarity: float (optional, default 0)
          - created_at: datetime (optional)
          - metadata: dict (optional)
        """
        results = []
        for c in candidates:
            result = self.score(
                query,
                c.get("content", ""),
                vector_similarity=c.get("vector_similarity", 0.0),
                created_at=c.get("created_at"),
                metadata=c.get("metadata"),
            )
            results.append(result)

        results.sort(key=lambda r: r.final_score, reverse=True)
        return results
