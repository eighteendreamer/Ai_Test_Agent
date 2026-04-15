"""
Page Knowledge runtime configuration helpers.

Synchronizes the active DB-backed Qdrant/embedding configuration into the
process-local VectorStore and EmbeddingClient singletons.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from database.connection import SessionLocal, QdrantCollectionConfig
from Page_Knowledge.embedding import reload_embedding_client
from Page_Knowledge.vector_store import apply_config_to_store

logger = logging.getLogger(__name__)

_DEFAULT_RUNTIME_CONFIG = {
    "collection_name": "page_knowledge",
    "vector_size": 1024,
    "distance": "Cosine",
    "qdrant_host": "localhost",
    "qdrant_port": 6333,
    "embedding_model": "Qwen/Qwen3-Embedding-4B",
    "embedding_api_url": "https://api.siliconflow.cn/v1/embeddings",
    "embedding_api_key": "",
}


def _config_to_dict(cfg: QdrantCollectionConfig) -> dict:
    return {
        "id": cfg.id,
        "collection_name": cfg.collection_name,
        "vector_size": cfg.vector_size,
        "distance": cfg.distance,
        "qdrant_host": cfg.qdrant_host,
        "qdrant_port": cfg.qdrant_port,
        "embedding_model": cfg.embedding_model,
        "embedding_api_url": cfg.embedding_api_url,
        "embedding_api_key": cfg.embedding_api_key,
        "is_active": cfg.is_active,
        "created_at": str(cfg.created_at) if cfg.created_at else None,
        "updated_at": str(cfg.updated_at) if cfg.updated_at else None,
    }


def get_active_page_knowledge_config(db: Session) -> dict:
    cfg = db.query(QdrantCollectionConfig).filter_by(is_active=1).order_by(
        QdrantCollectionConfig.id.desc()
    ).first()
    if cfg:
        return _config_to_dict(cfg)
    return dict(_DEFAULT_RUNTIME_CONFIG)


def apply_page_knowledge_runtime_config(config: dict) -> dict:
    """
    Apply the given config to in-process VectorStore and EmbeddingClient
    singletons. Returns the normalized config for convenience.
    """
    normalized = dict(_DEFAULT_RUNTIME_CONFIG)
    normalized.update(config or {})
    apply_config_to_store(normalized)
    reload_embedding_client(normalized)
    logger.info(
        "[PageKB Runtime] Active config applied: collection=%s dim=%s model=%s",
        normalized.get("collection_name"),
        normalized.get("vector_size"),
        normalized.get("embedding_model"),
    )
    return normalized


def load_page_knowledge_runtime_config(db: Optional[Session] = None) -> dict:
    """
    Load the active config from DB and apply it to runtime singletons.

    When no DB session is provided, this helper opens and closes a temporary
    SessionLocal automatically.
    """
    owns_session = db is None
    if db is None:
        db = SessionLocal()

    try:
        config = get_active_page_knowledge_config(db)
        return apply_page_knowledge_runtime_config(config)
    finally:
        if owns_session:
            db.close()
