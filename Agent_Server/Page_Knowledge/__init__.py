"""
页面知识库（Page Knowledge Base）— RAG 记忆层

架构：
  向量数据库(Qdrant) + 元数据存储(MySQL) + 版本控制(hash_signature)

核心能力：
  1. 页面能力结构存储（不存原始 HTML）
  2. 语义检索（Embedding + 向量相似度）
  3. 结构变更检测（hash → diff → 自动更新）
  4. 知识老化管理（过期重验证）
"""
from Page_Knowledge.service import PageKnowledgeService

__all__ = ["PageKnowledgeService"]
