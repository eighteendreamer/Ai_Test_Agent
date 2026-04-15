"""
一键测试 - Skills 管理

Skills 存储在 MinIO 上（路径: skills/{owner}_{repo}.md）
数据库 skills 表存储索引信息
LLM 执行时通过工具从 MinIO 查找并加载相关 Skills
"""
import io
import json
import os
import re
import logging
import hashlib
import httpx
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session

from database.connection import Skill
from Api_Spec.minio_client import get_minio_client, get_bucket_name, ensure_bucket

logger = logging.getLogger(__name__)

# GitHub API
SKILLS_GITHUB_RAW = "https://raw.githubusercontent.com"
SKILLS_REGISTRY_API = "https://api.github.com"

# MinIO 中 Skills 的前缀
SKILLS_PREFIX = "skills/"


class SkillManager:
    """Skills 管理器 - MinIO 存储"""

    # ============ MinIO 操作 ============

    @staticmethod
    def _upload_to_minio(slug: str, content: str) -> Dict:
        """上传 Skill 内容到 MinIO"""
        try:
            client, bucket = ensure_bucket()
            # 对象 key: skills/owner_repo.md
            safe_name = slug.replace("/", "_")
            object_key = f"{SKILLS_PREFIX}{safe_name}.md"

            content_bytes = content.encode("utf-8")
            content_hash = hashlib.sha256(content_bytes).hexdigest()

            result = client.put_object(
                bucket,
                object_key,
                io.BytesIO(content_bytes),
                length=len(content_bytes),
                content_type="text/markdown; charset=utf-8",
            )

            logger.info(f"[SkillManager] ✅ 上传到 MinIO: {object_key}")
            return {
                "success": True,
                "bucket": bucket,
                "object_key": object_key,
                "etag": result.etag if result else "",
                "file_hash": content_hash,
                "file_size": len(content_bytes),
            }
        except Exception as e:
            logger.error(f"[SkillManager] ❌ MinIO 上传失败: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    def _download_from_minio(object_key: str) -> Optional[str]:
        """从 MinIO 下载 Skill 内容"""
        try:
            client = get_minio_client()
            bucket = get_bucket_name()
            response = client.get_object(bucket, object_key)
            content = response.read().decode("utf-8")
            response.close()
            response.release_conn()
            return content
        except Exception as e:
            logger.error(f"[SkillManager] ❌ MinIO 下载失败 ({object_key}): {e}")
            return None

    @staticmethod
    def _delete_from_minio(object_key: str) -> bool:
        """从 MinIO 删除 Skill"""
        try:
            client = get_minio_client()
            bucket = get_bucket_name()
            client.remove_object(bucket, object_key)
            logger.info(f"[SkillManager] ✅ MinIO 删除: {object_key}")
            return True
        except Exception as e:
            logger.error(f"[SkillManager] ❌ MinIO 删除失败: {e}")
            return False

    # ============ CRUD ============

    @staticmethod
    def list_skills(db: Session, category: str = None, active_only: bool = False) -> List[Dict]:
        """获取已安装的 Skills 列表"""
        query = db.query(Skill)
        if category:
            query = query.filter(Skill.category == category)
        if active_only:
            query = query.filter(Skill.is_active == 1)
        skills = query.order_by(Skill.created_at.desc()).all()
        return [
            {
                "id": s.id,
                "name": s.name,
                "slug": s.slug,
                "source": s.source,
                "version": s.version,
                "description": s.description,
                "category": s.category,
                "author": s.author,
                "is_active": s.is_active,
                "install_count": s.install_count,
                "minio_key": json.loads(s.config).get("minio_key", "") if s.config else "",
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in skills
        ]

    @staticmethod
    def _infer_category(content: str) -> str:
        """根据内容推断 Skill 分类"""
        content_lower = content.lower()
        if any(kw in content_lower for kw in ["test", "testing", "qa", "测试", "assert", "验证"]):
            return "testing"
        elif any(kw in content_lower for kw in ["browser", "web", "selenium", "playwright", "浏览器", "页面"]):
            return "browser"
        elif any(kw in content_lower for kw in ["api", "http", "rest", "接口", "request"]):
            return "api"
        return "general"

    @staticmethod
    def _save_skill_to_db(
        db: Session, name: str, slug: str, source: str,
        description: str, content: str, minio_result: Dict, author: str = ""
    ) -> Skill:
        """保存 Skill 到数据库（公共逻辑）"""
        category = SkillManager._infer_category(content)
        summary = content[:500].replace("\n", " ").strip()

        skill = Skill(
            name=name,
            slug=slug,
            source=source,
            version="latest",
            description=description or summary[:200],
            category=category,
            content=summary,
            config=json.dumps({
                "minio_bucket": minio_result["bucket"],
                "minio_key": minio_result["object_key"],
                "file_hash": minio_result["file_hash"],
                "file_size": minio_result["file_size"],
            }, ensure_ascii=False),
            author=author,
            is_active=1,
            install_count=0,
        )
        db.add(skill)
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    async def install_skill(db: Session, slug: str) -> Dict:
        """
        从 GitHub 安装 Skill: 下载 → MinIO 存储 → 数据库索引
        slug 格式: owner/repo
        支持通过 .env 中 GITHUB_PROXY 配置代理
        """
        try:
            parts = slug.strip().split("/")
            if len(parts) != 2:
                return {"success": False, "message": f"无效的 Skill 标识: {slug}，格式应为 owner/repo"}

            owner, repo = parts

            existing = db.query(Skill).filter(Skill.slug == slug).first()
            if existing:
                return {"success": False, "message": f"Skill '{slug}' 已安装"}

            # 代理配置（从 .env 读取，支持无法直连 GitHub 的环境）
            proxy = os.getenv("GITHUB_PROXY", "").strip() or None
            transport = httpx.AsyncHTTPTransport(proxy=proxy) if proxy else None

            async with httpx.AsyncClient(timeout=30, transport=transport) as client:
                description = ""
                try:
                    repo_resp = await client.get(f"{SKILLS_REGISTRY_API}/repos/{owner}/{repo}")
                    if repo_resp.status_code == 200:
                        description = repo_resp.json().get("description", "")
                except Exception:
                    pass  # 获取描述失败不影响安装

                content = ""
                for filename in ["skill.md", "SKILL.md", "README.md", "readme.md"]:
                    for branch in ["main", "master"]:
                        raw_url = f"{SKILLS_GITHUB_RAW}/{owner}/{repo}/{branch}/{filename}"
                        try:
                            resp = await client.get(raw_url)
                            if resp.status_code == 200:
                                content = resp.text
                                break
                        except Exception:
                            continue
                    if content:
                        break

                if not content:
                    return {
                        "success": False,
                        "message": f"无法从 GitHub 获取 {slug} 的内容。如果网络不通，请使用「上传文件」方式安装。"
                    }

            # 上传到 MinIO
            minio_result = SkillManager._upload_to_minio(slug, content)
            if not minio_result.get("success"):
                return {"success": False, "message": f"MinIO 存储失败: {minio_result.get('message')}"}

            skill = SkillManager._save_skill_to_db(
                db, name=repo, slug=slug,
                source=f"https://github.com/{slug}",
                description=description, content=content,
                minio_result=minio_result, author=owner,
            )

            logger.info(f"[SkillManager] ✅ Skill '{slug}' 安装成功 → MinIO: {minio_result['object_key']}")
            return {
                "success": True,
                "message": f"Skill '{slug}' 安装成功",
                "data": {
                    "id": skill.id, "name": skill.name,
                    "category": skill.category, "minio_key": minio_result["object_key"],
                }
            }

        except Exception as e:
            logger.error(f"[SkillManager] ❌ 安装 Skill 失败: {e}")
            return {"success": False, "message": f"安装失败: {str(e)}"}

    @staticmethod
    def install_skill_from_file(
        db: Session, filename: str, content: bytes, skill_name: str = "", description: str = ""
    ) -> Dict:
        """
        手动上传 .md 文件安装 Skill → MinIO 存储 → 数据库索引
        """
        try:
            # 解码内容
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                text_content = content.decode("gbk", errors="replace")

            if not text_content.strip():
                return {"success": False, "message": "文件内容为空"}

            # 生成名称
            name = skill_name.strip() or os.path.splitext(filename)[0]
            slug = f"local/{name}"

            # 检查重复
            existing = db.query(Skill).filter(Skill.slug == slug).first()
            if existing:
                return {"success": False, "message": f"Skill '{name}' 已存在，请先卸载旧版本"}

            # 上传到 MinIO
            minio_result = SkillManager._upload_to_minio(slug, text_content)
            if not minio_result.get("success"):
                return {"success": False, "message": f"MinIO 存储失败: {minio_result.get('message')}"}

            skill = SkillManager._save_skill_to_db(
                db, name=name, slug=slug,
                source=f"upload://{filename}",
                description=description, content=text_content,
                minio_result=minio_result, author="local",
            )

            logger.info(f"[SkillManager] ✅ Skill '{name}' 上传安装成功 → MinIO: {minio_result['object_key']}")
            return {
                "success": True,
                "message": f"Skill '{name}' 安装成功",
                "data": {
                    "id": skill.id, "name": skill.name,
                    "category": skill.category, "minio_key": minio_result["object_key"],
                }
            }

        except Exception as e:
            logger.error(f"[SkillManager] ❌ 上传安装失败: {e}")
            return {"success": False, "message": f"安装失败: {str(e)}"}

    @staticmethod
    def uninstall_skill(db: Session, skill_id: int) -> Dict:
        """卸载 Skill: 删除 MinIO 文件 + 数据库记录"""
        skill = db.query(Skill).filter(Skill.id == skill_id).first()
        if not skill:
            return {"success": False, "message": "Skill 不存在"}

        # 从 MinIO 删除
        if skill.config:
            try:
                config = json.loads(skill.config) if isinstance(skill.config, str) else skill.config
                minio_key = config.get("minio_key", "")
                if minio_key:
                    SkillManager._delete_from_minio(minio_key)
            except Exception as e:
                logger.warning(f"[SkillManager] MinIO 删除警告: {e}")

        name = skill.name
        db.delete(skill)
        db.commit()
        return {"success": True, "message": f"Skill '{name}' 已卸载"}

    @staticmethod
    def toggle_skill(db: Session, skill_id: int, active: bool) -> Dict:
        """启用/禁用 Skill"""
        skill = db.query(Skill).filter(Skill.id == skill_id).first()
        if not skill:
            return {"success": False, "message": "Skill 不存在"}
        skill.is_active = 1 if active else 0
        db.commit()
        return {"success": True, "message": f"Skill '{skill.name}' 已{'启用' if active else '禁用'}"}

    @staticmethod
    def get_skill_detail(db: Session, skill_id: int) -> Optional[Dict]:
        """获取 Skill 详情（从 MinIO 加载全文内容）"""
        skill = db.query(Skill).filter(Skill.id == skill_id).first()
        if not skill:
            return None

        # 从 MinIO 读取全文
        full_content = ""
        if skill.config:
            try:
                config = json.loads(skill.config) if isinstance(skill.config, str) else skill.config
                minio_key = config.get("minio_key", "")
                if minio_key:
                    full_content = SkillManager._download_from_minio(minio_key) or ""
            except Exception as e:
                logger.warning(f"[SkillManager] 读取 MinIO 内容失败: {e}")
                full_content = skill.content or ""

        return {
            "id": skill.id,
            "name": skill.name,
            "slug": skill.slug,
            "source": skill.source,
            "description": skill.description,
            "category": skill.category,
            "content": full_content or skill.content or "",
            "author": skill.author,
            "is_active": skill.is_active,
            "created_at": skill.created_at.isoformat() if skill.created_at else None,
        }

    # ============ LLM 工具：智能查找 & 加载 Skills ============

    @staticmethod
    def find_relevant_skills(db: Session, task_description: str, top_k: int = 3) -> List[Dict]:
        """
        根据任务描述，从数据库中查找最相关的 Skills
        LLM 执行时调用此方法作为"工具"
        返回匹配的 Skill 列表（含 MinIO key，不含全文）

        改进：支持中英文关键词匹配、分词匹配、分类权重
        """
        active_skills = db.query(Skill).filter(Skill.is_active == 1).all()
        if not active_skills:
            return []

        # 关键词匹配打分
        task_lower = task_description.lower()

        # 简单中文分词（按标点和空格分割）
        import re as _re
        task_words = set(_re.split(r'[\s,，。、；;：:！!？?\-\(\)（）\[\]【】]+', task_lower))
        task_words = {w for w in task_words if len(w) >= 2}

        scored = []
        for s in active_skills:
            score = 0
            # 名称匹配（精确包含）
            if s.name and s.name.lower() in task_lower:
                score += 10
            # 名称部分匹配
            elif s.name:
                name_lower = s.name.lower()
                for word in task_words:
                    if word in name_lower or name_lower in word:
                        score += 5
                        break

            # 分类匹配（加权）
            category_keywords = {
                "testing": ["测试", "test", "验证", "校验", "断言", "assert", "用例", "case"],
                "browser": ["浏览器", "页面", "browser", "web", "dom", "元素", "点击", "click"],
                "api": ["接口", "api", "http", "rest", "请求", "request", "响应"],
                "login": ["登录", "login", "认证", "auth", "密码", "password"],
            }
            if s.category:
                kws = category_keywords.get(s.category, [])
                matched = sum(1 for kw in kws if kw in task_lower)
                score += matched * 3

            # 描述关键词匹配
            desc = (s.description or "").lower()
            summary = (s.content or "").lower()
            for word in task_words:
                if word in desc:
                    score += 2
                if word in summary:
                    score += 1

            if score > 0:
                config = {}
                if s.config:
                    try:
                        config = json.loads(s.config) if isinstance(s.config, str) else s.config
                    except Exception:
                        pass
                scored.append({
                    "id": s.id,
                    "name": s.name,
                    "slug": s.slug,
                    "category": s.category,
                    "description": s.description,
                    "minio_key": config.get("minio_key", ""),
                    "score": score,
                })

        # 按分数排序
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    @staticmethod
    def load_skill_content(db: Session, skill_id: int) -> Optional[str]:
        """
        从 MinIO 加载单个 Skill 的全文内容
        LLM 执行时调用此方法获取 Skill 便签内容
        """
        skill = db.query(Skill).filter(Skill.id == skill_id).first()
        if not skill:
            return None

        if skill.config:
            try:
                config = json.loads(skill.config) if isinstance(skill.config, str) else skill.config
                minio_key = config.get("minio_key", "")
                if minio_key:
                    content = SkillManager._download_from_minio(minio_key)
                    if content:
                        return content
            except Exception as e:
                logger.warning(f"[SkillManager] MinIO 加载失败: {e}")

        # 降级：返回数据库中的摘要
        return skill.content or ""

    @staticmethod
    def load_skills_as_notes(db: Session, skill_ids: List[int] = None, task: str = None) -> str:
        """
        以"便签"形式加载 Skills 内容，用于注入 LLM prompt

        优先级：
        1. 如果指定了 skill_ids，直接加载这些
        2. 如果提供了 task，智能匹配相关 Skills
        3. 否则加载所有激活的 Skills
        """
        skills_to_load = []

        if skill_ids:
            # 指定的 Skills
            for sid in skill_ids:
                skill = db.query(Skill).filter(Skill.id == sid, Skill.is_active == 1).first()
                if skill:
                    skills_to_load.append(skill)
        elif task:
            # 智能匹配
            relevant = SkillManager.find_relevant_skills(db, task, top_k=3)
            for r in relevant:
                skill = db.query(Skill).filter(Skill.id == r["id"]).first()
                if skill:
                    skills_to_load.append(skill)
        else:
            # 所有激活的
            skills_to_load = db.query(Skill).filter(Skill.is_active == 1).all()

        if not skills_to_load:
            return ""

        # 构建便签格式
        notes = []
        notes.append("=" * 60)
        notes.append("📌 Skills 便签 (来自 MinIO)")
        notes.append("=" * 60)

        for skill in skills_to_load:
            # 从 MinIO 加载全文
            content = SkillManager.load_skill_content(db, skill.id)
            if not content:
                continue

            notes.append(f"\n📎 [{skill.name}] ({skill.category or 'general'})")
            if skill.description:
                notes.append(f"   {skill.description}")
            notes.append("-" * 40)
            # 限制单个 Skill 内容长度，避免 prompt 过长
            if len(content) > 4000:
                content = content[:4000] + "\n... (内容已截断)"
            notes.append(content)
            notes.append("")

        notes.append("=" * 60)
        return "\n".join(notes)

    # ============ 搜索 ============

    @staticmethod
    async def search_skills(query: str) -> Dict:
        """搜索 skills.sh 上的 Skills（通过 GitHub API）"""
        try:
            search_query = f"{query} skill in:name,description,readme"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{SKILLS_REGISTRY_API}/search/repositories",
                    params={"q": search_query, "per_page": 20, "sort": "stars"}
                )
                if resp.status_code != 200:
                    return {"success": False, "message": "搜索失败", "items": []}

                data = resp.json()
                items = [
                    {
                        "slug": item["full_name"],
                        "name": item["name"],
                        "description": item.get("description", ""),
                        "stars": item.get("stargazers_count", 0),
                        "author": item["owner"]["login"],
                        "url": item["html_url"],
                    }
                    for item in data.get("items", [])
                ]
                return {"success": True, "items": items, "total": data.get("total_count", 0)}
        except Exception as e:
            return {"success": False, "message": str(e), "items": []}
