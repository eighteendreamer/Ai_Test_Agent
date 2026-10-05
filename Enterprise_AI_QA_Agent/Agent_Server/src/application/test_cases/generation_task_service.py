"""One fixed generation call per module, then an atomic reviewable draft receipt."""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from src.runtime.store import ContinuationLeaseLostError
from src.schemas.case_generation import IncrementalGenerationRequest, IncrementalGenerationScope, generation_hash
from src.schemas.case_generation_tasks import GenerationObject, GenerationTaskRequest
from src.schemas.case_management import TestCaseDraftCreateRequest, TestCaseVersionCreateRequest
from src.schemas.model_config import ModelInvocationResult, model_invocation_parameters

logger = logging.getLogger(__name__)


class GenerationCheckpointUnavailable(RuntimeError):
    """Unconfirmed write preserves original request/objects and forbids guessing a resend."""


class GenerationBudgetExceeded(ValueError):
    pass


async def confirmed_generation_write(operation, *, stage, identity):
    try:
        return await operation
    except (ContinuationLeaseLostError, ValueError, KeyError, TimeoutError, IntegrityError):
        raise
    except Exception as exc:
        logger.error("case_generation_checkpoint_unconfirmed", extra={"identity": identity,
            "stage": stage, "error_type": type(exc).__name__})
        raise GenerationCheckpointUnavailable("生成检查点回执尚不能确认，请按原请求只读回查") from exc


class GenerationTaskService:
    def __init__(self, *, store, sources, config, generator, cases, projects, documents, objects, stream):
        self._store, self._sources, self._config = store, sources, config
        self._generator, self._cases, self._projects = generator, cases, projects
        self._documents, self._objects, self._stream = documents, objects, stream

    async def initialize(self):
        await self._config.initialize()
        await self._store.initialize()

    async def _require_document(self, project_id, document_id):
        await self._projects.get(project_id)
        document = await self._documents.get_document(document_id)
        if document.project_id != project_id: raise KeyError("生成文档不属于当前项目")

    async def _object(self, value, prefix, filename):
        raw = value.model_dump_json().encode("utf-8")
        saved = await self._objects.store_uploaded_bytes(content=raw, filename=filename,
            content_type="application/json", object_prefix=f"case-generation/{prefix}/{uuid4()}")
        return GenerationObject(storage_uri=saved["uri"], sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))

    async def _read_object(self, reference, model):
        raw = (await self._objects.read_object_uri(reference.storage_uri))["content"]
        if len(raw) != reference.size_bytes or hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise ValueError("生成对象大小或SHA不一致")
        return model.model_validate_json(raw)

    async def submit(self, project_id, document_id, request):
        request = GenerationTaskRequest.model_validate_json(request.model_dump_json())
        await self._require_document(project_id, document_id)
        original = IncrementalGenerationRequest.model_validate(request.model_dump(exclude={"creation_key"}))
        digest = generation_hash(original.model_dump(mode="json"))
        prior = await self._store.get_group(project_id, document_id, creation_key=request.creation_key, request_hash=digest)
        if prior is not None:
            logger.info("case_generation_submission_replayed", extra={"group_id": prior.id})
            return prior
        if not self._stream:
            raise RuntimeError("持久生成的派发流尚未启用")
        config = await self._config.get(project_id)
        scope = await self._sources.capture(project_id, document_id, original, transport_options=config.payload.transport_options)
        reference = await self._object(scope, f"{project_id}/scopes", "scope.json")
        group = await confirmed_generation_write(self._store.submit_group(scope, reference, config,
            creation_key=request.creation_key, request_hash=digest, stream=self._stream), stage="submit", identity=request.creation_key)
        if group.scope != reference:
            try:
                if not await self._store.object_referenced(reference.storage_uri):
                    await self._objects.delete_object_uri(reference.storage_uri)
            except Exception as exc:
                logger.warning("case_generation_unused_object_cleanup_failed", extra={"group_id": group.id, "error_type": type(exc).__name__})
        return group

    async def read_group(self, project_id, document_id, group_id):
        await self._require_document(project_id, document_id)
        group = await self._store.get_group(project_id, document_id, group_id=group_id)
        if group is None: raise KeyError("当前文档没有该生成组")
        tasks = await self._store.list_modules(project_id, group.id)
        by_module = {task.module_id: task for task in tasks}
        if set(by_module) != set(group.module_ids) or len(tasks) != len(group.module_ids):
            raise ValueError("生成组模块清单与持久任务不同")
        return group, [by_module[module_id] for module_id in group.module_ids]

    async def read_scope(self, group):
        scope = await self._read_object(group.scope, IncrementalGenerationScope)
        if (scope.project_id != group.project_id or scope.document_id != group.document_id
            or scope.sha256 != group.scope_sha256 or [item.id for item in scope.modules] != group.module_ids
            or generation_hash(scope.request.model_dump(mode="json")) != group.request_hash):
            raise ValueError("生成组与原固定范围不同")
        if scope.authoring and scope.authoring.transport_options != group.config.payload.transport_options:
            raise ValueError("固定生成SDK选项与原策略不同")
        return scope

    @staticmethod
    def budget(module, policy):
        request, parameters = module.generation.invocation, module.generation.authoring.model_parameters
        contents = [request.system_prompt, *[message["content"] for message in request.messages]]
        if request.tools or any(not isinstance(content, str) for content in contents):
            raise GenerationBudgetExceeded("模块生成只接受固定文本，不执行工具")
        # Adapt existing impact_calls.estimate's UTF8 method, retaining full text.
        size = len(json.dumps({"system_prompt": request.system_prompt, "messages": request.messages},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        content_estimate = sum(len(content.encode("utf-8")) for content in contents)
        reserved = content_estimate + policy.framing_allowance_tokens + parameters["max_tokens"]
        if size > policy.max_input_bytes or reserved > parameters["context_window"]:
            raise GenerationBudgetExceeded("完整模块请求超过固定字节或估算上下文预算，未截断")
        return {"implementation_version": "generation-content-budget-v1", "method": "utf8_bytes_estimate",
            "canonical_input_bytes": size, "content_tokens_or_estimate": content_estimate,
            "framing_allowance_tokens": policy.framing_allowance_tokens,
            "output_reserved_tokens": parameters["max_tokens"], "reserved_estimated_tokens": reserved,
            "provider_token_measurement": "estimate"}

    async def _preflight(self, prepared):
        models = self._generator._models
        actual = await asyncio.to_thread(models.get_model_config, prepared.authoring.model_key)
        if actual is None or model_invocation_parameters(actual) != prepared.authoring.model_parameters:
            raise ValueError("模型参数已变化，不能续写原模块")
        runtime = await asyncio.to_thread(models.get_invocation_runtime_parameters, prepared.authoring.model_key,
            **({"transport_options": prepared.authoring.transport_options} if prepared.authoring.transport_options else {}))
        if runtime != prepared.authoring.runtime_parameters: raise ValueError("模型SDK/runtime已变化，不能续写原模块")

    async def _draft_entries(self, scope, group, module, response, batch):
        context = json.loads(module.generation.invocation.messages[0]["content"])["evidence_context"]
        provenance = {"group_id": group.id, "module_id": module.id, "scope_sha256": scope.sha256,
            "request_sha256": module.generation.sha256, "response": response.model_dump(mode="json"),
            "requirement_id": scope.requirement.requirement_id, "requirement_version_id": scope.requirement.id,
            "review_id": scope.review.id, "point_ids": [point.id for point in scope.requirement.points],
            "target_case_version_id": module.target.version_id if module.target else None,
            "deployment_status": "unknown"}
        payloads = []
        for case in batch.cases:
            content = case.model_dump(exclude={"case_key", "title", "priority", "case_type"})
            content["generation_metadata"] = {**content["generation_metadata"], "template": scope.template.model_dump(mode="json"), "incremental_generation": provenance}
            payloads.append(TestCaseDraftCreateRequest(**content, case_key=case.case_key, title=case.title,
                case_type=case.case_type, priority=case.priority, mode_key=module.generation.request.mode_key,
                source_refs=context["source_refs"], model_key=batch.model_key, prompt_version=batch.prompt_version,
                skill_versions=batch.skill_versions))
        if module.kind == "create":
            return await self._cases.prepare_drafts(project_id=scope.project_id, payloads=payloads)
        version = self._cases._build_version(case_id=module.target.case.id, version_number=1,
            payload=TestCaseVersionCreateRequest(**payloads[0].model_dump(exclude={"case_key", "title", "case_type", "priority", "mode_key"})),
            created_at=datetime.now(timezone.utc))
        return [(module.target.case, version)]

    async def execute(self, claim):
        current = await self._store.get(claim.task.project_id, claim.task.id)
        if current is None: raise KeyError("生成任务不存在")
        if current.status in {"completed", "failed"}: return current
        stage = "input_validation_failed"
        try:
            group = await self._store.get_group(claim.task.project_id, claim.task.document_id, group_id=claim.seed.group_id)
            if group is None or group.sha256 != claim.seed.group_sha256: raise ValueError("任务组依据不同")
            scope = await self.read_scope(group)
            module = next((item for item in scope.modules if item.id == claim.seed.module_id), None)
            if module is None or module.sha256 != claim.seed.module_sha256: raise ValueError("任务模块依据不同")
            checkpoint = current.checkpoint
            if module.kind == "retirement_candidate":
                return await confirmed_generation_write(self._store.commit_module(claim, module, [], scope_sha256=scope.sha256), stage="retirement", identity=current.id)
            if checkpoint and checkpoint.phase == "invoking":
                return await self._store.finish_failed(claim, error_code="call_outcome_unknown", message="原调用意图已存但没有持久响应；未重发")
            if checkpoint is None:
                await self._projects.require_active(current.project_id)
                stage = "input_budget_or_configuration_rejected"
                budget = self.budget(module, group.config.payload)
                await self._preflight(module.generation)
                remaining = await self._store.remaining_seconds(claim)
                if remaining <= 0: raise TimeoutError("原模块总期限已到")
                await confirmed_generation_write(self._store.begin_invocation(claim, budget), stage="invocation", identity=current.id)
                stage = "call_outcome_unknown"
                logger.info("case_generation_invoking", extra={"task_id": current.id, "module_id": module.id,
                    "attempt_id": claim.attempt_id, "request_sha256": module.generation.sha256, "budget": budget})
                async with asyncio.timeout(min(remaining, group.config.payload.call_timeout_seconds)):
                    result = await self._generator.invoke_frozen(module.generation)
                reference = await self._object(result, f"{current.project_id}/{current.id}/{claim.attempt_id}/responses", "response.json")
                checkpoint = await confirmed_generation_write(self._store.save_response(claim, reference), stage="response", identity=current.id)
            stage = "response_validation_rejected"
            if checkpoint is None or checkpoint.response is None: raise ValueError("模块没有持久原响应")
            result = await self._read_object(checkpoint.response, ModelInvocationResult)
            if checkpoint.response.size_bytes > group.config.payload.max_response_bytes:
                raise GenerationBudgetExceeded("原响应超过固定大小，已保存但未提交草稿")
            batch = self._sources.parse_module_response(module, result)
            stage = "draft_commit_rejected"
            entries = await self._draft_entries(scope, group, module, checkpoint.response, batch)
            return await confirmed_generation_write(self._store.commit_module(claim, module, entries,
                scope_sha256=scope.sha256), stage="draft_commit", identity=current.id)
        except (GenerationCheckpointUnavailable, ContinuationLeaseLostError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("case_generation_module_rejected", extra={"task_id": current.id,
                "error_code": stage, "error_type": type(exc).__name__})
            return await confirmed_generation_write(self._store.finish_failed(claim, error_code=stage,
                message=f"模块未提交草稿：{stage}；固定输入和已存响应保留"), stage="failure", identity=current.id)
