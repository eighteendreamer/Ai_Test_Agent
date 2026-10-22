"""Read public task entries from their authoritative tables, with one paged query.

Uses the existing Session projection and domain public DTOs. No task inputs,
checkpoints, lease details or private Session metadata leave this projection.
"""
from __future__ import annotations

import asyncio
import re
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_session_store import _task_pool_session_from_row
from src.schemas.case_planning_task import CasePlanningTaskRecord
from src.schemas.document_differences import DocumentDifferenceTaskRecord
from src.schemas.task_pool import (
    TaskPoolEntry, TaskPoolKind, TaskPoolState, TaskPoolPlanningEntry, TaskPoolSessionEntry,
    TaskPoolDocumentDifferenceEntry, TaskPoolDocumentImpactEntry, TaskPoolDocumentImpactSummary,
    TaskPoolGenerationEntry, TaskPoolGenerationSummary,
)


class TaskPoolEntriesStore(Protocol):
    async def list_entries(self, *, limit: int, offset: int, kind: TaskPoolKind,
                           state: TaskPoolState, query: str, project_id: str | None) -> list[TaskPoolEntry]: ...


class PostgresTaskPoolStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions = settings.database.postgres_session_table
        self._tasks = settings.database.postgres_case_planning_task_table
        self._projects = settings.database.postgres_project_table
        self._docs = settings.database.postgres_api_doc_table
        self._differences = self._docs + "_diff_tasks"
        self._impacts = self._docs + "_impact_tasks"
        self._generations = self._docs + "_case_gen_tasks"
        self._retries = self._docs + "_case_gen_retries"
        for name in (self._sessions, self._tasks, self._projects, self._docs, self._differences, self._impacts,
                     self._generations, self._retries):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
                raise ValueError("Invalid task-pool table identifier")

    async def list_entries(self, *, limit: int, offset: int, kind: TaskPoolKind,
                           state: TaskPoolState, query: str, project_id: str | None) -> list[TaskPoolEntry]:
        return await asyncio.to_thread(self._list, limit, offset, kind, state, query, project_id)

    def _list(self, limit, offset, kind, state, query, project_id):
        filters, parameters = [], []
        if kind != "all":
            filters.append("e.kind=%s"); parameters.append(kind)
        if state != "all":
            statuses = ["queued", "running", "waiting_approval"] if state == "active" else [state]
            filters.append("e.status=ANY(%s)"); parameters.append(statuses)
        if project_id:
            filters.append("e.project_id=%s"); parameters.append(project_id)
        if query:
            # Literal substring search: %, _ and quotes have no SQL pattern meaning.
            filters.append("strpos(lower(concat_ws(' ',e.search_text,p.name)),%s)>0")
            parameters.append(query.lower())
        where = " AND ".join(filters) or "TRUE"
        parameters.extend([limit, offset])
        # Whitelist the public domain DTO, never serialize the entire task row.
        difference_fields = ",".join(f"'{name}',d.{name}" for name in DocumentDifferenceTaskRecord.model_fields)
        impact_fields = ",".join(f"'{name}',i.{name}" for name in TaskPoolDocumentImpactSummary.model_fields)
        generation_fields = ",".join(f"'{name}',g.{name}" for name in TaskPoolGenerationSummary.model_fields
                                     if name not in {"retry", "result_kind"})
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"""
                WITH entries AS (
                    SELECT 'session'::text AS kind,id,title,status,project_id,updated_at,
                           concat_ws(' ',id,title,mode_key,selected_agent,metadata->>'parent_session_id') AS search_text
                    FROM {self._sessions}
                    WHERE mode_key='code_review' OR session_mode='background_task'
                    UNION ALL
                    SELECT 'case_planning',id::text,'用例规划',status,project_id,updated_at,
                           concat_ws(' ',id::text,creation_key,phase,message,'用例规划')
                    FROM {self._tasks}
                    UNION ALL
                    SELECT 'document_difference',d.id::text,
                           COALESCE(a.record->>'title','文档比较'),d.status,d.project_id,d.updated_at,
                           concat_ws(' ',d.id::text,d.creation_key,d.phase,d.message,d.document_id::text,
                                     d.old_snapshot_id::text,d.new_snapshot_id::text,a.record->>'title','文档比较')
                    FROM {self._differences} d
                    LEFT JOIN {self._docs} a ON a.id=d.document_id AND a.project_id=d.project_id
                    UNION ALL
                    SELECT 'document_impact',i.id::text,
                           COALESCE(a.record->>'title','文档影响分析'),i.status,i.project_id,i.updated_at,
                           concat_ws(' ',i.id::text,i.creation_key,i.phase,i.message,i.document_id::text,
                                     i.difference_snapshot_id::text,a.record->>'title','文档影响分析')
                    FROM {self._impacts} i
                    LEFT JOIN {self._docs} a ON a.id=i.document_id AND a.project_id=i.project_id
                    UNION ALL
                    SELECT 'case_generation',g.id::text,
                           COALESCE(a.record->>'title','用例增量生成'),g.status,g.project_id,g.updated_at,
                           concat_ws(' ',g.id::text,g.creation_key,g.phase,g.message,g.document_id::text,
                                     g.group_id::text,g.module_id::text,r.creation_key,r.parent_task_id::text,
                                     a.record->>'title','用例增量生成')
                    FROM {self._generations} g
                    LEFT JOIN {self._docs} a ON a.id=g.document_id AND a.project_id=g.project_id
                    LEFT JOIN {self._retries} r ON r.task_id=g.id AND r.project_id=g.project_id AND r.document_id=g.document_id
                ), page AS (
                    SELECT e.kind,e.id,e.title,e.status,e.project_id,e.updated_at,p.name AS project_name
                    FROM entries e LEFT JOIN {self._projects} p ON p.id=e.project_id
                    WHERE {where}
                    ORDER BY e.updated_at DESC,e.kind,e.id LIMIT %s OFFSET %s
                )
                SELECT page.*,COALESCE(s.created_at,t.created_at,d.created_at,i.created_at,g.created_at) AS created_at,
                       s.session_mode,s.runtime_mode,s.mode_key,s.selected_agent,
                       COALESCE(s.metadata->'worker_dispatches','[]'::jsonb) AS worker_dispatches,
                       COALESCE(s.metadata->>'parent_session_id','') AS parent_session_id,
                       t.creation_key,t.phase,t.message,t.attempt_no,t.plan_id,
                       COALESCE((t.checkpoint->>'next_round')::integer,0) AS round_count,
                       t.started_at,t.deadline_at,t.completed_at,
                       CASE WHEN jsonb_typeof(t.seed->'run_revision')='object' THEN
                           CASE WHEN jsonb_typeof(t.checkpoint->'run_revision_outcome')='object'
                               THEN t.checkpoint->'run_revision_outcome'
                               ELSE jsonb_build_object('run_id',t.seed->'run_revision'->>'run_id',
                                   'status','pending','message','等待候选规划及运行修订确认')
                           END
                       END AS run_revision,
                       CASE WHEN d.id IS NOT NULL THEN jsonb_build_object({difference_fields}) END AS difference_task,
                       CASE WHEN i.id IS NOT NULL THEN jsonb_build_object({impact_fields}) END AS impact_task,
                       CASE WHEN g.id IS NOT NULL THEN jsonb_build_object({generation_fields},
                           'result_kind',g.checkpoint->'receipt'->>'kind',
                           'retry',CASE WHEN r.id IS NOT NULL THEN jsonb_build_object(
                               'id',r.id,'parent_task_id',r.parent_task_id,'creation_key',r.creation_key,
                               'strategy',r.record->>'strategy') END) END AS generation_task
                FROM page
                LEFT JOIN {self._sessions} s ON page.kind='session' AND s.id=page.id
                LEFT JOIN {self._tasks} t ON page.kind='case_planning' AND t.id::text=page.id
                LEFT JOIN {self._differences} d ON page.kind='document_difference' AND d.id::text=page.id
                LEFT JOIN {self._impacts} i ON page.kind='document_impact' AND i.id::text=page.id
                LEFT JOIN {self._generations} g ON page.kind='case_generation' AND g.id::text=page.id
                LEFT JOIN {self._retries} r ON r.task_id=g.id AND r.project_id=g.project_id AND r.document_id=g.document_id
                ORDER BY page.updated_at DESC,page.kind,page.id
            """, tuple(parameters))
            rows = cur.fetchall()
        result = []
        for row in rows:
            if row["kind"] == "session":
                result.append(TaskPoolSessionEntry(project_name=row["project_name"],
                    session=_task_pool_session_from_row(row)))
            elif row["kind"] == "document_difference":
                result.append(TaskPoolDocumentDifferenceEntry(project_name=row["project_name"],
                    document_title=row["title"], task=DocumentDifferenceTaskRecord.model_validate(row["difference_task"])))
            elif row["kind"] == "document_impact":
                result.append(TaskPoolDocumentImpactEntry(project_name=row["project_name"],
                    document_title=row["title"], task=TaskPoolDocumentImpactSummary.model_validate(row["impact_task"])))
            elif row["kind"] == "case_generation":
                result.append(TaskPoolGenerationEntry(project_name=row["project_name"],
                    document_title=row["title"], task=TaskPoolGenerationSummary.model_validate(row["generation_task"])))
            else:
                public = {name: row[name] for name in CasePlanningTaskRecord.model_fields}
                public["project_id"] = str(public["project_id"])
                public["plan_id"] = str(public["plan_id"]) if public["plan_id"] else None
                result.append(TaskPoolPlanningEntry(project_name=row["project_name"],
                    task=CasePlanningTaskRecord.model_validate(public)))
        return result
