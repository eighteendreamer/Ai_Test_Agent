"""Read original PG worker results when the transcript was never committed."""
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.projects.project_store import PostgresProjectStore
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.domain.models import SessionRecord
from src.modes.api_testing_mode.campaign_state import EndpointCandidate, ExecutionPolicy
from src.modes.api_testing_mode.capability_mapper import CapabilityMapper
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
from src.modes.api_testing_mode.dependency_planner import DependencyPlanner
from src.modes.api_testing_mode.precondition_resolver import PreconditionAnalysis
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.project import ProjectRecord
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall
from src.registry.tools import ToolRegistry
from test_api_worker_auth_refresh_live import ControlledWorkerLauncher, auth_target
from test_execution_worker_cli import isolated_runtime

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_API_WORKER_RESULT_RECOVERY") != "1",
    reason="actual isolated PG/MySQL/Redis/RustFS and HTTP; controlled child launcher")]


async def test_pg_worker_result_survives_missing_transcript_and_rebuilt_parent(tmp_path):
    evidence = Path(os.environ["QA_API_WORKER_RESULT_RECOVERY_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    facts = {"external_ai_calls": 0, "independent_agent_worker": False,
        "full_api_acceptance": False, "full_goal_complete": False}
    with auth_target() as (base, events):
        try:
            async with isolated_runtime(tmp_path) as (settings, _, _, objects, _):
                projects = PostgresProjectStore(settings)
                await projects.initialize()
                sessions = PostgresSessionStore(settings)
                await sessions.initialize()
                jobs = ToolJobService(PostgresToolJobStore(settings))
                await jobs.initialize()
                references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
                await references.initialize()
                now = datetime.now(UTC)
                project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="owned-worker-readback",
                    name="Owned worker result recovery", created_at=now, updated_at=now))
                parent = SessionRecord(id=str(uuid4()), title="Owned parent", project_id=project.id,
                    status=SessionStatus.running, session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now)
                await sessions.save_session(parent)
                context = {"session_id": parent.id, "turn_id": str(uuid4())}
                runtime = ToolRuntimeService(tool_job_service=jobs, session_store=sessions, settings=settings,
                    api_credential_reference_service=references)

                class NoTranscriptLauncher(ControlledWorkerLauncher):
                    async def dispatch(self, payload, context):
                        records = []
                        for worker in payload["workers"]:
                            arguments = json.loads(worker["prompt"].split("Tool arguments:\n", 1)[1].split(
                                "\n\nAfter the tool", 1)[0])
                            identity = str(uuid4())
                            metadata = {"parent_session_id": parent.id, "parent_turn_id": context["turn_id"],
                                "task_id": worker["task_id"], "dispatch_role": "api_execution_worker"}
                            await self.store.mutate_metadata(parent.id, lambda stored:
                                stored.setdefault("worker_dispatches", []).append({**metadata, "child_session_id": identity}))
                            child = SessionRecord(id=identity, title="Owned child without transcript", project_id=project.id,
                                status=SessionStatus.running, session_mode=SessionMode.background_task,
                                runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
                                metadata=metadata)
                            await self.store.save_session(child)
                            execution_context = ToolExecutionContext(session_id=identity, turn_id=str(uuid4()), trace_id=str(uuid4()),
                                user_message="执行已有明确认证任务", normalized_input="执行已有明确认证任务", context_bundle={})
                            call = ModelToolCall(id=str(uuid4()), name="api-test-runner", arguments=arguments)
                            result = await self.tool_runtime.execute(ToolRegistry().get(call.name), call, execution_context)
                            self.runs.append((call, execution_context, result))
                            child.status = SessionStatus.completed
                            await self.store.save_session(child)
                            assert (await self.store.get_session(identity)).messages == []
                            records.append({"task_id": worker["task_id"], "child_session_id": identity, "status": "completed"})
                        return {"ok": True, "workers": records}

                launcher = NoTranscriptLauncher(sessions, runtime)
                graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many([
                    EndpointCandidate(endpoint_id=key, method=method, path=path, full_url=base + path)
                    for key, method, path in [("login", "POST", "/login"),
                        ("refresh", "POST", "/auth/refresh"), ("profile", "GET", "/profile")]]),
                    preconditions=PreconditionAnalysis())
                coordinator = ApiSubagentCoordinator(pool=ApiTaskPool(graph.tasks), policy=ExecutionPolicy(max_retries=0),
                    coordinator_runtime_service=launcher, session_store=sessions, parent_context=context,
                    credential_manager=CredentialManager(), credential_reference_service=references,
                    tool_job_service=jobs)
                results = await coordinator.run_all()
                facts.update({"events": events, "response_statuses": [t.response_status for t in results],
                    "statuses": [t.status for t in results], "job_count": len(launcher.runs)})
                assert [t.response_status for t in results] == [200, 200, 200], facts
                assert all(t.status == "completed" for t in results)
                assert results[1].auth_ref == results[2].auth_ref != results[0].auth_ref
                initial_http_count = len(events)

                # Recreate stores, credential service and coordinator. A saved
                # running parent task must read the already-completed child Job.
                reader = PostgresSessionStore(settings)
                reader_jobs = ToolJobService(PostgresToolJobStore(settings))
                reader_refs = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=reader)
                restored_tasks = [t.model_copy(deep=True) for t in results]
                for task in restored_tasks:
                    task.status = "running"
                    task.response_body = None
                    task.response_status = None
                restored = ApiSubagentCoordinator(pool=ApiTaskPool(restored_tasks), policy=ExecutionPolicy(max_retries=0),
                    coordinator_runtime_service=launcher, session_store=reader, parent_context=context,
                    credential_manager=CredentialManager(), credential_reference_service=reader_refs,
                    tool_job_service=reader_jobs)
                restored_results = await restored.run_all()
                facts["restored_statuses"] = [t.status for t in restored_results]
                assert all(t.status == "completed" for t in restored_results)
                assert restored_results[-1].response_body == results[-1].response_body
                assert restored_results[1].auth_ref == restored_results[2].auth_ref != restored_results[0].auth_ref
                assert restored._credential_manager.get(restored_results[1].auth_ref) is not None
                assert len(events) == initial_http_count == 3
                assert await restored.run_all() == restored_results
                assert len(events) == initial_http_count

                # No transcript was written; the settled Job remains authoritative.
                child = await reader.get_session(results[-1].worker_session_id)
                assert child.messages == []
                stored_job = await reader_jobs.get_job(launcher.runs[-1][2].job_id)
                facts["authoritative_job_status"] = stored_job.status.value
                facts["restored_extra_http"] = len(events) - initial_http_count
                from src.modes.api_testing_mode.worker_result_reader import ApiWorkerResultReader
                from src.runtime.postgres_api_credential_store import ApiCredentialReferenceError
                result_reader = ApiWorkerResultReader(tool_job_service=reader_jobs, credential_reference_service=reader_refs)
                with pytest.raises(ApiCredentialReferenceError, match="scope_invalid"):
                    await result_reader.read(session=child, task=results[-1],
                        parent_context={**context, "session_id": str(uuid4())})
                facts["foreign_parent_rejected"] = True
                children = [await reader.get_session(task.worker_session_id) for task in results]
                facts["missing_transcripts"] = all(child.messages == [] for child in children)
                assert facts["missing_transcripts"]
                original_jobs = [await reader_jobs.get_job(record.job_id) for _, _, record in launcher.runs]
                raw_public = json.dumps({"jobs": [job.model_dump(mode="json") for job in original_jobs],
                    "children": [{"id": child.id, "project_id": child.project_id, "status": child.status.value,
                        "metadata": child.metadata, "message_count": len(child.messages)} for child in children]},
                    ensure_ascii=False, indent=2)
                secrets = [coordinator._credential_manager.get(task.auth_ref).token for task in results if task.auth_ref]
                assert all(secret not in raw_public for secret in secrets if secret)
                (evidence / "postgres-readback.json").write_text(raw_public, encoding="utf-8")
        finally:
            facts["events"] = events
            (evidence / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
