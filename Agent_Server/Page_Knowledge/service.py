"""
页面知识库核心服务

RAG 记忆层入口：
  1. 查询知识库（先查向量 → 命中则跳过页面分析）
  2. 存储新页面知识（抽象 → Embedding → 写入 Qdrant + MySQL）
  3. 版本检查（hash 比对 → diff → 自动更新）
  4. 知识老化管理
  5. 为任务树规划提供 RAG 上下文
"""
import asyncio
import copy
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from Page_Knowledge.schema import PageKnowledge
from Page_Knowledge.embedding import get_embedding_client
from Page_Knowledge.vector_store import get_vector_store, generate_point_id
from Page_Knowledge.diff_engine import DiffEngine, DiffResult

logger = logging.getLogger(__name__)

# ── 配置 ──
SIMILARITY_THRESHOLD = 0.82    # 相似度阈值：高于此值认为命中
KNOWLEDGE_MAX_AGE_DAYS = 30    # 知识老化：超过此天数需重新验证
FRESHNESS_HOURS = 4            # 新鲜度：4 小时内的知识直接复用


class PageKnowledgeService:


    @staticmethod
    async def lookup_by_env_urls(
        base_url: str,
        login_url: str = "",
        user_input: str = "",
    ) -> Optional[Dict]:
        """
        通过测试环境的 URL 查找知识库（精确匹配优先）。

        策略：
          1. 先用 login_url 精确匹配（知识库通常存的是登录页）
          2. 再用 base_url 精确匹配
          3. 都没命中 → 返回 None

        命中后直接返回完整知识，不做覆盖度计算。
        后续由 assess_sufficiency_with_llm 判断是否满足需求。
        """
        store = get_vector_store()

        async def _exact_lookup(url: str) -> Optional[Dict]:
            if not url:
                return None
            point_id = generate_point_id(url)
            existing = await asyncio.to_thread(store.get_by_id, point_id)
            if existing and existing.get("payload"):
                payload = existing["payload"]
                knowledge = PageKnowledge.from_dict(payload.get("knowledge", payload))
                is_fresh = PageKnowledgeService._is_fresh(knowledge)
                logger.info(f"[PageKB] 精确命中: {url}, 新鲜={is_fresh}")
                return {
                    "hit": True,
                    "knowledge": knowledge,
                    "matched_url": url,
                    "stale": not is_fresh,
                    "payload": payload,
                    "point_id": point_id,
                }
            return None

        # 优先 login_url（知识库通常以登录页为入口存储）
        result = await _exact_lookup(login_url)
        if result:
            return result

        # 再试 base_url
        result = await _exact_lookup(base_url)
        if result:
            return result

        logger.info(f"[PageKB] 未命中: login_url={login_url}, base_url={base_url}")
        return None

    @staticmethod
    async def assess_sufficiency_with_llm(
        knowledge: "PageKnowledge",
        user_input: str,
    ) -> Dict:
        """
        用 LLM 判断知识库内容是否满足用户需求。

        Returns:
            {
                "is_sufficient": True/False,
                "reason": "...",
                "missing_elements": ["..."],   # 缺失的功能/元素
                "matched_elements": ["..."],   # 已匹配的功能/元素
            }
        """
        from llm.client import get_llm_client

        # 构建知识库摘要（全文关键信息）
        k = knowledge
        kb_summary_parts = []
        if k.summary:
            kb_summary_parts.append(f"页面摘要：{k.summary}")
        if k.description:
            kb_summary_parts.append(f"详细描述：{k.description}")
        if k.page_sections:
            kb_summary_parts.append(f"功能区域：{', '.join(k.page_sections[:20])}")
        if k.buttons:
            kb_summary_parts.append(f"按钮/操作：{', '.join(str(b) for b in k.buttons[:30])}")
        if k.forms:
            for f in k.forms:
                fields_str = ', '.join(ff.label or ff.name for ff in f.fields[:10])
                kb_summary_parts.append(f"表单[{f.name}]：{fields_str}")
        if k.tables:
            for t in k.tables:
                kb_summary_parts.append(f"表格[{t.name}]：{', '.join(t.columns[:10])}")
        if k.links:
            kb_summary_parts.append(f"导航链接：{', '.join(k.links[:20])}")
        if k.tags:
            kb_summary_parts.append(f"标签：{', '.join(k.tags)}")

        kb_text = "\n".join(kb_summary_parts) if kb_summary_parts else "（知识库内容为空）"

        prompt = f"""你是一个测试需求分析专家。请判断以下知识库内容是否足以支持用户的测试需求。

【用户测试需求】
{user_input}

【知识库已有内容】
{kb_text}

请判断：
1. 知识库中是否包含用户需求涉及的页面元素、功能模块、操作入口？
2. 如果知识库内容足够，直接基于它生成测试用例即可，无需重新分析页面。
3. 如果知识库内容缺失关键功能，需要重新获取页面信息。

返回 JSON：
{{
    "is_sufficient": true/false,
    "reason": "判断理由（一句话）",
    "matched_elements": ["已匹配的功能/元素列表"],
    "missing_elements": ["缺失的功能/元素列表，为空则表示全部满足"]
}}"""

        try:
            llm = get_llm_client()
            response = await llm.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            result = llm.parse_json_response(response)
            result.setdefault("is_sufficient", False)
            result.setdefault("reason", "")
            result.setdefault("matched_elements", [])
            result.setdefault("missing_elements", [])
            logger.info(
                f"[PageKB] LLM 充分性判断: sufficient={result['is_sufficient']}, "
                f"reason={result['reason']}"
            )
            return result
        except Exception as e:
            logger.warning(f"[PageKB] LLM 充分性判断失败，默认不足: {e}")
            return {
                "is_sufficient": False,
                "reason": f"LLM 判断失败: {e}",
                "matched_elements": [],
                "missing_elements": [],
            }


    @staticmethod
    async def lookup(
        url: str,
        query_text: str = "",
        domain_filter: str = "",
    ) -> Optional[Dict]:
        """
        用户发起任务时调用：查询是否已有该 URL 的页面知识

        优先精确匹配 URL（确定性 ID），再用语义检索兜底

        Returns:
            命中时返回 { "hit": True, "knowledge": PageKnowledge, "source": "exact|semantic", "score": float }
            未命中返回 None
        """
        store = get_vector_store()
        embed_client = get_embedding_client()

        # ── 1.1 精确匹配 ──
        point_id = generate_point_id(url)
        existing = await asyncio.to_thread(store.get_by_id, point_id)
        if existing and existing.get("payload"):
            payload = existing["payload"]
            knowledge = PageKnowledge.from_dict(payload.get("knowledge", payload))

            # 新鲜度检查
            if PageKnowledgeService._is_fresh(knowledge):
                knowledge.last_accessed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # 异步更新 last_accessed（非阻塞）
                try:
                    await asyncio.to_thread(store.upsert, point_id, [], {
                        **payload,
                        "knowledge": {**payload.get("knowledge", payload), "last_accessed": knowledge.last_accessed}
                    })
                except Exception:
                    pass

                logger.info(f"[PageKB] ✅ 精确命中: {url}")
                return {
                    "hit": True,
                    "knowledge": knowledge,
                    "source": "exact",
                    "score": 1.0,
                    "stale": False,
                }
            else:
                # 知识已老化但仍存在，标记为 stale
                logger.info(f"[PageKB] ⏰ 知识已老化: {url}")
                return {
                    "hit": True,
                    "knowledge": knowledge,
                    "source": "exact",
                    "score": 1.0,
                    "stale": True,
                }

        # ── 1.2 语义检索 ──
        if not query_text:
            query_text = url

        try:
            query_vector = await embed_client.embed(query_text)

            filter_cond = {}
            if domain_filter:
                filter_cond["domain"] = domain_filter

            results = await asyncio.to_thread(
                store.search,
                query_vector,
                3,
                SIMILARITY_THRESHOLD,
                filter_cond if filter_cond else None,
            )

            if results:
                best = results[0]
                payload = best["payload"]
                knowledge = PageKnowledge.from_dict(payload.get("knowledge", payload))

                logger.info(
                    f"[PageKB] ✅ 语义命中: score={best['score']:.3f}, "
                    f"url={knowledge.url}"
                )
                return {
                    "hit": True,
                    "knowledge": knowledge,
                    "source": "semantic",
                    "score": best["score"],
                    "stale": not PageKnowledgeService._is_fresh(knowledge),
                }
        except Exception as e:
            logger.warning(f"[PageKB] 语义检索失败: {e}")

        logger.info(f"[PageKB] ❌ 未命中: {url}")
        return None

    # ═══════════════════════════════════════════════
    # 2. 存储页面知识
    # ═══════════════════════════════════════════════

    @staticmethod
    def normalize_capabilities_input(new_capabilities: Any) -> Dict:
        """把页面能力数据/最终摘要/直接 capabilities 统一归一到知识能力结构。"""
        if isinstance(new_capabilities, PageKnowledge):
            return new_capabilities.to_dict()
        if not isinstance(new_capabilities, dict):
            return {}

        capability_markers = {
            "forms", "tables", "buttons", "links", "page_sections", "summary",
            "description", "page_type", "module_name", "tags", "dynamic_elements",
        }

        def _is_capability_payload(payload: Any) -> bool:
            return isinstance(payload, dict) and any(key in payload for key in capability_markers)

        page_data = dict(new_capabilities.get("page_data") or {})
        session_summary = dict(new_capabilities.get("session_summary") or {})
        nested_session_summary = dict(page_data.get("session_summary") or {})

        candidates = [
            new_capabilities.get("distilled_knowledge"),
            page_data.get("distilled_knowledge"),
            session_summary.get("distilled_knowledge"),
            nested_session_summary.get("distilled_knowledge"),
            page_data,
            session_summary,
            nested_session_summary,
            new_capabilities,
        ]
        for candidate in candidates:
            if _is_capability_payload(candidate):
                return dict(candidate)
        return dict(new_capabilities)

    @staticmethod
    async def store(
        knowledge: PageKnowledge,
        db: Optional[Session] = None,
        project_id: int = None,
    ) -> Dict:
        """
        存储或更新页面知识到向量数据库 + MySQL

        流程：
          1. 刷新 hash
          2. 生成 Embedding
          3. 写入 Qdrant
          4. 写入 MySQL（PageKnowledgeRecord）
        """
        store = get_vector_store()
        embed_client = get_embedding_client()

        if project_id is None:
            raise ValueError("project_id is required when storing page knowledge")

        # 刷新签名和时间戳
        knowledge.refresh_hash()
        knowledge.last_accessed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 提取域名
        if not knowledge.domain and knowledge.url:
            try:
                knowledge.domain = urlparse(knowledge.url).netloc
            except Exception:
                pass

        # 生成 Embedding
        embedding_text = knowledge.build_embedding_text()
        vector = await embed_client.embed(embedding_text)

        # 写入 Qdrant
        point_id = generate_point_id(knowledge.url)
        payload = {
            "url": knowledge.url,
            "domain": knowledge.domain,
            "page_type": knowledge.page_type,
            "module_name": knowledge.module_name,
            "hash_signature": knowledge.hash_signature,
            "version": knowledge.version,
            "last_updated": knowledge.last_updated,
            "last_accessed": knowledge.last_accessed,
            "summary": knowledge.summary,
            "embedding_text": embedding_text,
            "knowledge": knowledge.to_dict(),  # 完整结构
            "project_id": project_id,
        }

        success = await asyncio.to_thread(store.upsert, point_id, vector, payload)

        # 写入 MySQL（可选）
        mysql_id = None
        if db and success:
            try:
                mysql_id = PageKnowledgeService._save_to_mysql(
                    db,
                    knowledge,
                    point_id,
                    project_id=project_id,
                )
            except Exception as e:
                logger.warning(f"[PageKB] MySQL 写入失败（不影响向量库）: {e}")

        logger.info(
            f"[PageKB] {'✅' if success else '❌'} 存储: {knowledge.url} "
            f"(hash={knowledge.hash_signature}, v{knowledge.version})"
        )

        return {
            "success": success,
            "point_id": point_id,
            "mysql_id": mysql_id,
            "hash_signature": knowledge.hash_signature,
            "version": knowledge.version,
        }

    # ═══════════════════════════════════════════════
    # 3. 版本检查 & 自动更新
    # ═══════════════════════════════════════════════

    @staticmethod
    async def check_and_update(
        url: str,
        new_capabilities: Dict,
        db: Optional[Session] = None,
        project_id: int | None = None,
    ) -> Dict:
        """
        版本检查机制（每次页面分析后调用）

        1. 从 new_capabilities 构建临时 PageKnowledge
        2. 计算新 hash
        3. 和知识库对比 → 如果不同 → diff → 更新

        Returns:
            { "action": "created" | "updated" | "unchanged",
              "diff": DiffResult | None,
              "knowledge": PageKnowledge }
        """
        normalized_capabilities = PageKnowledgeService.normalize_capabilities_input(new_capabilities)
        new_knowledge = PageKnowledge.from_capabilities(url, normalized_capabilities)

        # 查已有知识
        store = get_vector_store()
        point_id = generate_point_id(url)
        existing = await asyncio.to_thread(store.get_by_id, point_id)

        if not existing or not existing.get("payload"):
            # 全新页面 → 直接存储
            new_knowledge.version = 1
            result = await PageKnowledgeService.store(new_knowledge, db, project_id=project_id)
            return {
                "action": "created",
                "diff": None,
                "knowledge": new_knowledge,
                "store_result": result,
            }

        # 已有知识 → 比对 hash
        payload = existing["payload"]
        old_knowledge = PageKnowledge.from_dict(payload.get("knowledge", payload))
        old_hash = old_knowledge.hash_signature
        new_hash = new_knowledge.compute_hash()

        if old_hash == new_hash:
            # 无变化 → 仅更新访问时间
            old_knowledge.last_accessed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[PageKB] ⏩ 结构未变化: {url} (hash={old_hash})")
            return {
                "action": "unchanged",
                "diff": None,
                "knowledge": old_knowledge,
            }

        # 结构有变 → 执行 Diff
        diff_result = DiffEngine.compute_diff(old_knowledge, new_knowledge)
        logger.info(
            f"[PageKB] 🔄 结构变更: {url} "
            f"(severity={diff_result.severity}, changes={len(diff_result.changes)})"
        )

        # 智能合并：将新页面能力数据 merge 到旧知识上，而非全文覆盖
        # 旧知识中未被新数据覆盖到的部分会被保留，只补充缺失、更新已变化的部分
        merged_knowledge = PageKnowledgeService._merge_knowledge(old_knowledge, new_knowledge, diff_result)
        merged_knowledge.version = old_knowledge.version + 1
        result = await PageKnowledgeService.store(merged_knowledge, db, project_id=project_id)

        return {
            "action": "updated",
            "diff": diff_result,
            "knowledge": merged_knowledge,
            "store_result": result,
            "old_version": old_knowledge.version,
            "new_version": merged_knowledge.version,
            "merged_changes": len(diff_result.changes),
        }

    # ═══════════════════════════════════════════════
    # 4. RAG 上下文检索
    # ═══════════════════════════════════════════════

    @staticmethod
    async def retrieve_context(
        query: str,
        domain: str = "",
        limit: int = 3,
    ) -> List[Dict]:
        """
        为任务树规划提供 RAG 上下文

        当生成 L2 任务时，不要只用当前页面结构——
        同时检索历史页面知识作为参考

        Returns:
            [{ "url", "summary", "page_type", "score", "knowledge": PageKnowledge }, ...]
        """
        store = get_vector_store()
        embed_client = get_embedding_client()

        try:
            query_vector = await embed_client.embed(query)

            filter_cond = {}
            if domain:
                filter_cond["domain"] = domain

            results = await asyncio.to_thread(
                store.search,
                query_vector,
                limit,
                0.5,
                filter_cond if filter_cond else None,
            )

            contexts = []
            for r in results:
                payload = r["payload"]
                contexts.append({
                    "url": payload.get("url", ""),
                    "summary": payload.get("summary", ""),
                    "page_type": payload.get("page_type", ""),
                    "score": r["score"],
                    "module_name": payload.get("module_name", ""),
                    "knowledge": PageKnowledge.from_dict(
                        payload.get("knowledge", payload)
                    ),
                })
            return contexts
        except Exception as e:
            logger.warning(f"[PageKB] RAG 上下文检索失败: {e}")
            return []

    @staticmethod
    def build_rag_prompt_context(contexts: List[Dict]) -> str:
        """
        把 RAG 检索结果格式化为可嵌入提示词的文本

        用于插入到 L2/L3 规划提示词中
        """
        if not contexts:
            return ""

        lines = ["【历史页面知识参考】"]
        for i, ctx in enumerate(contexts, 1):
            k = ctx.get("knowledge")
            if not k:
                continue
            summary = k.summary if isinstance(k, PageKnowledge) else ctx.get("summary", "")
            url = k.url if isinstance(k, PageKnowledge) else ctx.get("url", "")
            lines.append(f"{i}. [{ctx.get('page_type', '')}] {url}")
            lines.append(f"   {summary}")
            if isinstance(k, PageKnowledge) and k.forms:
                forms_desc = ", ".join(f.name for f in k.forms[:5])
                lines.append(f"   表单: {forms_desc}")
            if isinstance(k, PageKnowledge) and k.tables:
                tables_desc = ", ".join(t.name for t in k.tables[:5])
                lines.append(f"   表格: {tables_desc}")
        lines.append("")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════
    # 5. 知识库管理
    # ═══════════════════════════════════════════════

    @staticmethod
    async def list_all(
        domain: str = "",
        page_type: str = "",
        limit: int = 100,
        project_id: int = None,
    ) -> List[Dict]:
        """列出知识库中所有页面知识"""
        store = get_vector_store()

        filter_cond = {}
        if domain:
            filter_cond["domain"] = domain
        if page_type:
            filter_cond["page_type"] = page_type
        if project_id is not None:
            filter_cond["project_id"] = project_id

        items = await asyncio.to_thread(
            store.scroll_all,
            filter_cond if filter_cond else None,
            limit,
        )

        result = []
        for item in items:
            payload = item.get("payload", {})
            knowledge_data = payload.get("knowledge", {})
            result.append({
                "id": item.get("id"),
                "url": payload.get("url", knowledge_data.get("url", "")),
                "domain": payload.get("domain", ""),
                "page_type": payload.get("page_type", ""),
                "module_name": payload.get("module_name", ""),
                "summary": payload.get("summary", ""),
                "hash_signature": payload.get("hash_signature", ""),
                "version": payload.get("version", 1),
                "last_updated": payload.get("last_updated", ""),
                "last_accessed": payload.get("last_accessed", ""),
            })

        return result

    @staticmethod
    async def get_detail(url: str) -> Optional[Dict]:
        """获取单个页面知识详情"""
        store = get_vector_store()
        point_id = generate_point_id(url)
        record = await asyncio.to_thread(store.get_by_id, point_id)

        if not record or not record.get("payload"):
            return None

        payload = record["payload"]
        knowledge = PageKnowledge.from_dict(payload.get("knowledge", payload))
        return {
            "id": record["id"],
            "knowledge": knowledge.to_dict(),
            "embedding_text": payload.get("embedding_text", ""),
            "hash_signature": payload.get("hash_signature", ""),
            "version": payload.get("version", 1),
        }

    @staticmethod
    async def delete_knowledge(url: str, db: Optional[Session] = None) -> bool:
        """删除指定 URL 的页面知识"""
        store = get_vector_store()
        point_id = generate_point_id(url)
        success = await asyncio.to_thread(store.delete, point_id)

        if db and success:
            try:
                PageKnowledgeService._delete_from_mysql(db, url)
            except Exception as e:
                logger.warning(f"[PageKB] MySQL 删除失败: {e}")

        return success

    @staticmethod
    def get_stats() -> Dict:
        """知识库统计"""
        store = get_vector_store()
        health = store.health_check()
        return {
            "total_pages": health.get("count", 0),
            "vector_store": health,
        }

    # ═══════════════════════════════════════════════
    # 6. 知识老化管理
    # ═══════════════════════════════════════════════

    @staticmethod
    async def find_stale_knowledge(
        max_age_days: int = KNOWLEDGE_MAX_AGE_DAYS,
    ) -> List[Dict]:
        """
        查找已老化的知识（超过 max_age_days 天未更新）

        用于定期重新验证页面结构
        """
        all_items = await PageKnowledgeService.list_all(limit=500)
        stale = []
        cutoff = datetime.now() - timedelta(days=max_age_days)

        for item in all_items:
            last_updated = item.get("last_updated", "")
            if last_updated:
                try:
                    dt = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
                    if dt < cutoff:
                        stale.append(item)
                except ValueError:
                    stale.append(item)
            else:
                stale.append(item)

        return stale

    # ═══════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════

    @staticmethod
    def _merge_knowledge(
        old: PageKnowledge,
        new: PageKnowledge,
        diff: "DiffResult",
    ) -> PageKnowledge:
        """
        智能合并两个版本的页面知识（surgical merge，而非全文覆盖）

        策略：
          - 新数据发现了旧知识没有的元素 → 补充进去（填充缺口）
          - 两边都有但属性有变化的元素   → 以新值更新（修复过时内容）
          - 旧知识有但新数据未发现的元素 → 保留（可能未覆盖，不视为删除）
          - 页面级布尔属性              → OR 合并（只增不减），auth_required 取新值
          - 文本描述 / 标题 / 类型      → 有新值就更新，无则保留旧值

        注意：
          旧知识中的元素不会因为「新数据没发现」而被删除。
          真正的删除只能通过 /knowledge/delete 接口手动触发，
          或在老化检测后由运营人员确认。
        """
        merged = copy.deepcopy(old)

        # ── 1. 合并 forms ─────────────────────────────────────────────
        old_form_map = {f.name: f for f in merged.forms}
        for new_form in new.forms:
            if new_form.name not in old_form_map:
                # 新增表单 → 直接追加
                merged.forms.append(copy.deepcopy(new_form))
                old_form_map[new_form.name] = merged.forms[-1]
            else:
                # 已有表单 → 做字段级 merge
                old_form = old_form_map[new_form.name]
                old_field_map = {f.name: f for f in old_form.fields}
                for new_field in new_form.fields:
                    if new_field.name not in old_field_map:
                        # 新增字段
                        old_form.fields.append(copy.deepcopy(new_field))
                        old_field_map[new_field.name] = old_form.fields[-1]
                    else:
                        # 已有字段 → 更新变化的属性
                        old_f = old_field_map[new_field.name]
                        old_f.field_type = new_field.field_type
                        old_f.required = new_field.required
                        if new_field.label:
                            old_f.label = new_field.label
                        if new_field.placeholder:
                            old_f.placeholder = new_field.placeholder
                        if new_field.options:
                            old_f.options = new_field.options
                # 更新提交按钮 / method（若新数据有值）
                if new_form.submit_button:
                    old_form.submit_button = new_form.submit_button
                if new_form.method:
                    old_form.method = new_form.method

        # ── 2. 合并 tables ────────────────────────────────────────────
        old_table_map = {t.name: t for t in merged.tables}
        for new_table in new.tables:
            if new_table.name not in old_table_map:
                # 新增表格
                merged.tables.append(copy.deepcopy(new_table))
                old_table_map[new_table.name] = merged.tables[-1]
            else:
                # 已有表格 → 合并列（取并集）
                old_table = old_table_map[new_table.name]
                old_cols = set(old_table.columns)
                for col in new_table.columns:
                    if col not in old_cols:
                        old_table.columns.append(col)
                        old_cols.add(col)
                # 功能标记：OR 合并（只要曾发现过，就保留）
                old_table.has_pagination = old_table.has_pagination or new_table.has_pagination
                old_table.has_search = old_table.has_search or new_table.has_search
                old_table.has_sort = old_table.has_sort or new_table.has_sort
                # 合并行操作（取并集）
                old_actions = set(old_table.row_actions)
                for action in new_table.row_actions:
                    if action not in old_actions:
                        old_table.row_actions.append(action)
                        old_actions.add(action)

        # ── 3. 合并 buttons（字符串集合取并集）───────────────────────
        old_btn_set = set(str(b) for b in merged.buttons)
        for btn in new.buttons:
            btn_str = str(btn)
            if btn_str not in old_btn_set:
                merged.buttons.append(btn_str)
                old_btn_set.add(btn_str)

        # ── 4. 合并 page_sections（取并集）────────────────────────────
        old_sec_set = set(merged.page_sections)
        for sec in new.page_sections:
            if sec not in old_sec_set:
                merged.page_sections.append(sec)
                old_sec_set.add(sec)

        # ── 5. 合并 links（取并集）────────────────────────────────────
        old_link_set = set(merged.links)
        for link in new.links:
            if link not in old_link_set:
                merged.links.append(link)
                old_link_set.add(link)

        # ── 6. 合并 dynamic_elements（取并集）─────────────────────────
        old_dyn_set = set(merged.dynamic_elements)
        for de in new.dynamic_elements:
            if de not in old_dyn_set:
                merged.dynamic_elements.append(de)
                old_dyn_set.add(de)

        # ── 7. 页面级布尔属性 ──────────────────────────────────────────
        # 功能标记：OR 合并（只要曾经发现过，就保留；避免因数据覆盖不全而误删能力）
        merged.has_file_upload = merged.has_file_upload or new.has_file_upload
        merged.has_export = merged.has_export or new.has_export
        merged.has_import = merged.has_import or new.has_import
        merged.has_search = merged.has_search or new.has_search
        merged.has_pagination = merged.has_pagination or new.has_pagination
        # 认证要求：取新值（当次的登录状态最可信）
        merged.auth_required = new.auth_required

        # ── 8. 文本描述更新 ────────────────────────────────────────────
        if new.summary:
            merged.summary = new.summary
        if new.description:
            merged.description = new.description
        if new.page_title:
            merged.page_title = new.page_title
        # page_type：非 mixed（模糊默认值）时才覆盖
        if new.page_type and new.page_type != "mixed":
            merged.page_type = new.page_type

        # ── 9. 合并 roles / tags / security_surface（取并集）──────────
        old_roles = set(merged.roles)
        for role in new.roles:
            if role not in old_roles:
                merged.roles.append(role)
                old_roles.add(role)

        old_tags = set(merged.tags)
        for tag in new.tags:
            if tag not in old_tags:
                merged.tags.append(tag)
                old_tags.add(tag)

        old_surfaces = set(merged.security_surface)
        for surface in new.security_surface:
            if surface not in old_surfaces:
                merged.security_surface.append(surface)
                old_surfaces.add(surface)

        # ── 10. 元数据 ─────────────────────────────────────────────────
        if new.module_name:
            merged.module_name = new.module_name
        if new.domain:
            merged.domain = new.domain

        logger.info(
            f"[PageKB] 🔀 智能合并完成: url={old.url} "
            f"forms={len(merged.forms)}, buttons={len(merged.buttons)}, "
            f"tables={len(merged.tables)}, sections={len(merged.page_sections)}, "
            f"diff_changes={len(diff.changes) if diff else 0}"
        )
        return merged

    @staticmethod
    def _is_fresh(knowledge: PageKnowledge) -> bool:
        """判断知识是否仍然新鲜（未过期）"""
        if not knowledge.last_updated:
            return False

        try:
            updated_dt = datetime.strptime(knowledge.last_updated, "%Y-%m-%d %H:%M:%S")
            # 超过老化天数 → 不新鲜
            if datetime.now() - updated_dt > timedelta(days=KNOWLEDGE_MAX_AGE_DAYS):
                return False
            return True
        except ValueError:
            return False

    @staticmethod
    def _save_to_mysql(
        db: Session,
        knowledge: PageKnowledge,
        point_id: str,
        project_id: int | None = None,
    ) -> Optional[int]:
        """写入 MySQL 元数据表"""
        from database.connection import PageKnowledgeRecord

        if project_id is None:
            raise ValueError("project_id is required when saving page knowledge")

        # 查找或创建（同一项目 + 同一 URL 才视为同一记录）
        record = db.query(PageKnowledgeRecord).filter_by(
            url=knowledge.url, project_id=project_id
        ).first()
        if record:
            record.project_id = project_id
            record.page_type = knowledge.page_type
            record.summary = knowledge.summary
            record.hash_signature = knowledge.hash_signature
            record.version = knowledge.version
            record.domain = knowledge.domain
            record.module_name = knowledge.module_name
            record.knowledge_json = json.dumps(knowledge.to_dict(), ensure_ascii=False)
            record.vector_point_id = point_id
            record.updated_at = datetime.now()
        else:
            record = PageKnowledgeRecord(
                project_id=project_id,
                url=knowledge.url,
                domain=knowledge.domain,
                page_type=knowledge.page_type,
                summary=knowledge.summary,
                module_name=knowledge.module_name,
                hash_signature=knowledge.hash_signature,
                version=knowledge.version,
                knowledge_json=json.dumps(knowledge.to_dict(), ensure_ascii=False),
                vector_point_id=point_id,
            )
            db.add(record)

        db.commit()
        return record.id

    @staticmethod
    def _delete_from_mysql(db: Session, url: str):
        """从 MySQL 删除"""
        from database.connection import PageKnowledgeRecord
        db.query(PageKnowledgeRecord).filter_by(url=url).delete()
        db.commit()
