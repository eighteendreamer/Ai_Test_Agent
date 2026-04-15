"""
页面知识库 API 路由

提供：
  - 知识库查询/浏览/统计
  - 手动录入/删除
  - 健康检查
  - 老化知识查询
"""
import asyncio
import logging
import time
from typing import Optional, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Body, Query
from sqlalchemy.orm import Session

from database.connection import (
    get_db,
    resolve_project_context,
    get_active_project_by_id,
    QdrantCollectionConfig,
    OneclickSession,
    TestEnvironment,
)
from Page_Knowledge.service import PageKnowledgeService
from Page_Knowledge.vector_store import get_vector_store
from Page_Knowledge.runtime_config import (
    apply_page_knowledge_runtime_config,
    get_active_page_knowledge_config,
)
from Page_Knowledge.schema import PageKnowledge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["页面知识库"])


# ── 请求/响应模型 ──

class LookupRequest(BaseModel):
    url: str
    query_text: str = ""
    domain: str = ""


class StoreRequest(BaseModel):
    url: str
    capabilities: dict
    project_id: Optional[int] = None


class DeleteRequest(BaseModel):
    url: str


class RetrieveContextRequest(BaseModel):
    query: str
    domain: str = ""
    limit: int = 3


# ── 接口 ──

@router.get("/knowledge/stats")
async def knowledge_stats(db: Session = Depends(get_db)):
    """知识库统计信息"""
    try:
        import asyncio
        from database.connection import PageKnowledgeRecord
        store = get_vector_store()
        health = await asyncio.to_thread(store.health_check)
        # MySQL 真实记录数
        total_records = db.query(PageKnowledgeRecord).count()
        # Qdrant 向量数
        vector_count = health.get("count", 0) if health.get("status") == "healthy" else 0
        # 简单老化统计（30天内未更新）
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=30)
        stale_count = db.query(PageKnowledgeRecord).filter(
            PageKnowledgeRecord.updated_at < cutoff
        ).count()
        qdrant_healthy = health.get("status") == "healthy"
        return {
            "success": True,
            "data": {
                "total_records": total_records,
                "vector_count": vector_count,
                "stale_count": stale_count,
                "qdrant_healthy": qdrant_healthy,
                "qdrant_detail": health,
            }
        }
    except Exception as e:
        logger.error(f"[PageKB API] 统计失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/knowledge/health")
def knowledge_health():
    """向量数据库健康检查"""
    try:
        store = get_vector_store()
        health = store.health_check()
        return {"success": True, "data": health}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/knowledge/lookup")
async def knowledge_lookup(req: LookupRequest):
    """查询页面知识（精确 + 语义检索）"""
    try:
        result = await PageKnowledgeService.lookup(
            url=req.url,
            query_text=req.query_text,
            domain_filter=req.domain,
        )
        if result:
            knowledge = result["knowledge"]
            return {
                "success": True,
                "data": {
                    "hit": True,
                    "source": result["source"],
                    "score": result["score"],
                    "stale": result.get("stale", False),
                    "knowledge": knowledge.to_dict() if isinstance(knowledge, PageKnowledge) else knowledge,
                }
            }
        return {"success": True, "data": {"hit": False}}
    except Exception as e:
        logger.error(f"[PageKB API] lookup 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge/store")
async def knowledge_store(req: StoreRequest, db: Session = Depends(get_db)):
    """手动存储页面知识"""
    try:
        # 获取默认项目
        project = resolve_project_context(db, req.project_id)
        if not project:
            return {"success": False, "message": "没有可用的项目，请先创建并启用一个项目"}

        knowledge = PageKnowledge.from_capabilities(req.url, req.capabilities)
        result = await PageKnowledgeService.store(knowledge, db, project_id=project.id)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"[PageKB API] store 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge/check-update")
async def knowledge_check_update(req: StoreRequest, db: Session = Depends(get_db)):
    """版本检查并自动更新"""
    try:
        project = resolve_project_context(db, req.project_id)
        if not project:
            return {"success": False, "message": "没有可用的项目，请先创建并启用一个项目"}
        result = await PageKnowledgeService.check_and_update(
            url=req.url,
            new_capabilities=req.capabilities,
            db=db,
            project_id=project.id,
        )
        diff_data = None
        if result.get("diff"):
            diff_data = result["diff"].to_dict()

        return {
            "success": True,
            "data": {
                "action": result["action"],
                "diff": diff_data,
                "version": result["knowledge"].version,
                "hash": result["knowledge"].hash_signature,
                "merged_changes": result.get("merged_changes", 0),
            }
        }
    except Exception as e:
        logger.error(f"[PageKB API] check-update 失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/knowledge/list")
async def knowledge_list(domain: str = "", page_type: str = "", limit: int = 100, project_id: int = None, db: Session = Depends(get_db)):
    """列出所有页面知识"""
    try:
        from database.connection import get_active_project_by_id

        # 如果未指定项目，使用默认项目
        if project_id is None:
            project = resolve_project_context(db, project_id)
            if not project:
                return {"success": True, "data": {"items": [], "total": 0}}
            project_id = project.id
        else:
            # 验证指定的项目是否启用（不报错，只是过滤）
            project = resolve_project_context(db, project_id)
            if not project:
                return {"success": True, "data": {"items": [], "total": 0}}

        items = await PageKnowledgeService.list_all(
            domain=domain, page_type=page_type, limit=limit, project_id=project.id,
        )
        return {"success": True, "data": {"items": items, "total": len(items)}}
    except Exception as e:
        logger.error(f"[PageKB API] list 失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/knowledge/detail")
async def knowledge_detail(url: str):
    """获取单个页面知识详情"""
    try:
        detail = await PageKnowledgeService.get_detail(url)
        if detail:
            return {"success": True, "data": detail}
        return {"success": False, "message": "未找到该 URL 的知识记录"}
    except Exception as e:
        logger.error(f"[PageKB API] detail 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge/delete")
async def knowledge_delete(req: DeleteRequest, db: Session = Depends(get_db)):
    """删除指定 URL 的知识"""
    try:
        success = await PageKnowledgeService.delete_knowledge(req.url, db)
        return {"success": success, "message": "已删除" if success else "删除失败"}
    except Exception as e:
        logger.error(f"[PageKB API] delete 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge/retrieve-context")
async def knowledge_retrieve_context(req: RetrieveContextRequest):
    """RAG 上下文检索（给任务规划用）"""
    try:
        contexts = await PageKnowledgeService.retrieve_context(
            query=req.query, domain=req.domain, limit=req.limit,
        )
        items = []
        for ctx in contexts:
            k = ctx.get("knowledge")
            items.append({
                "url": ctx.get("url", ""),
                "summary": ctx.get("summary", ""),
                "page_type": ctx.get("page_type", ""),
                "module_name": ctx.get("module_name", ""),
                "score": ctx.get("score", 0),
                "knowledge": k.to_dict() if isinstance(k, PageKnowledge) else None,
            })
        return {"success": True, "data": {"items": items, "total": len(items)}}
    except Exception as e:
        logger.error(f"[PageKB API] retrieve-context 失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/knowledge/stale")
async def knowledge_stale(max_age_days: int = 30):
    """查找已老化的知识"""
    try:
        items = await PageKnowledgeService.find_stale_knowledge(max_age_days)
        return {"success": True, "data": {"items": items, "total": len(items)}}
    except Exception as e:
        logger.error(f"[PageKB API] stale 失败: {e}")
        return {"success": False, "message": str(e)}


# ── Collection 配置管理 ──

_DEFAULT_CONFIG = {
    "collection_name": "page_knowledge",
    "vector_size": 1024,
    "distance": "Cosine",
    "qdrant_host": "localhost",
    "qdrant_port": 6333,
    "embedding_model": "Qwen/Qwen3-Embedding-4B",
    "embedding_api_url": "https://api.siliconflow.cn/v1/embeddings",
    "embedding_api_key": "",
}


class CollectionConfigRequest(BaseModel):
    collection_name: str = "page_knowledge"
    vector_size: int = 1024
    distance: str = "Cosine"
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    embedding_model: str = "Qwen/Qwen3-Embedding-4B"
    embedding_api_url: str = "https://api.siliconflow.cn/v1/embeddings"
    embedding_api_key: str = ""


class CollectionCreateRequest(BaseModel):
    force: bool = False


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


@router.get("/knowledge/collection-config")
def get_collection_config(db: Session = Depends(get_db)):
    """获取当前 Qdrant Collection 配置（若未配置则返回默认值）"""
    try:
        cfg = db.query(QdrantCollectionConfig).filter_by(is_active=1).order_by(
            QdrantCollectionConfig.id.desc()
        ).first()
        if cfg:
            return {"success": True, "data": _config_to_dict(cfg)}
        return {"success": True, "data": _DEFAULT_CONFIG, "is_default": True}
    except Exception as e:
        logger.error(f"[PageKB API] get_collection_config 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge/collection-config")
def save_collection_config(req: CollectionConfigRequest, db: Session = Depends(get_db)):
    """保存 Collection 配置到数据库（覆盖当前活跃配置）"""
    try:
        cfg = db.query(QdrantCollectionConfig).filter_by(is_active=1).order_by(
            QdrantCollectionConfig.id.desc()
        ).first()
        if cfg:
            cfg.collection_name = req.collection_name
            cfg.vector_size = req.vector_size
            cfg.distance = req.distance
            cfg.qdrant_host = req.qdrant_host
            cfg.qdrant_port = req.qdrant_port
            cfg.embedding_model = req.embedding_model
            cfg.embedding_api_url = req.embedding_api_url
            cfg.embedding_api_key = req.embedding_api_key
        else:
            cfg = QdrantCollectionConfig(
                collection_name=req.collection_name,
                vector_size=req.vector_size,
                distance=req.distance,
                qdrant_host=req.qdrant_host,
                qdrant_port=req.qdrant_port,
                embedding_model=req.embedding_model,
                embedding_api_url=req.embedding_api_url,
                embedding_api_key=req.embedding_api_key,
                is_active=1,
            )
            db.add(cfg)
        db.commit()
        db.refresh(cfg)
        active_config = _config_to_dict(cfg)
        apply_page_knowledge_runtime_config(active_config)
        logger.info(f"[PageKB API] 已保存 Collection 配置: {req.collection_name}")
        return {"success": True, "data": active_config, "message": "配置已保存并已热更新"}
    except Exception as e:
        logger.error(f"[PageKB API] save_collection_config 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge/collection-create")
async def create_collection(req: CollectionCreateRequest, db: Session = Depends(get_db)):
    """应用当前配置，创建/初始化 Qdrant Collection（force=True 时先删除再重建）"""
    try:
        import asyncio
        # 加载 DB 配置并热更新单例
        config_dict = get_active_page_knowledge_config(db)
        apply_page_knowledge_runtime_config(config_dict)

        store = get_vector_store()

        if req.force:
            # 强制删除旧 collection 再重建
            def _force_recreate():
                try:
                    client = store._get_client()
                    collections = [c.name for c in client.get_collections().collections]
                    if store.collection_name in collections:
                        client.delete_collection(store.collection_name)
                        logger.info(f"[PageKB API] 已删除旧 Collection: {store.collection_name}")
                except Exception as e:
                    logger.warning(f"[PageKB API] 删除旧 Collection 失败（忽略）: {e}")

            await asyncio.to_thread(_force_recreate)

        ok = await asyncio.to_thread(store.ensure_collection)
        if ok:
            health = await asyncio.to_thread(store.health_check)
            return {
                "success": True,
                "message": f"Collection '{store.collection_name}' 初始化成功",
                "data": health,
            }
        else:
            return {
                "success": False,
                "message": f"Collection 初始化失败: {store._last_error or '未知错误'}",
            }
    except Exception as e:
        logger.error(f"[PageKB API] create_collection 失败: {e}")
        return {"success": False, "message": str(e)}

