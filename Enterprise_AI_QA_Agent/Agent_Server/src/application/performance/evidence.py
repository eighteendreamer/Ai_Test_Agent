"""Preserve engine files before the runner's temporary directory is removed."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from src.core.request_context import get_request_context

logger = logging.getLogger(__name__)


async def store_execution_evidence(*, objects, command, script, result, workdir: Path,
                                   executed_command: list[str] | None = None) -> dict:
    # Existing counterparts: ArtifactStorageService and AgentCaseReceiptService.
    capture = {"status": "unavailable", "artifacts": [], "missing_artifacts": [], "error": ""}
    if objects is None or not objects.enabled:
        capture["error"] = "性能原件对象存储未配置或未启用"
        logger.warning("performance_evidence_unavailable", extra={"engine": script.engine})
        return capture
    scope = get_request_context()
    if scope is None or not scope.session_id:
        capture["error"] = "性能原件缺少所属会话，不能发布无法归属的工件"
        logger.warning("performance_evidence_owner_missing", extra={"engine": script.engine})
        return capture
    execution_id = uuid4().hex
    owner = {name: getattr(scope, name) for name in
        ("project_id", "session_id", "turn_id", "trace_id", "run_id", "run_item_id", "attempt_id")}
    prefix = f"performance-evidence/{scope.project_id or 'unbound'}/{scope.session_id}/{execution_id}"
    for name, content in (("stdout.log", result.stdout), ("stderr.log", result.stderr)):
        (workdir / name).write_bytes(content.encode("utf-8"))
    if not result.summary_json:
        capture["missing_artifacts"].append("engine_summary")
    files = sorted(path for path in workdir.rglob("*") if path.is_file())

    async def upload(path: Path) -> dict:
        if path.is_symlink() or not path.resolve().is_relative_to(workdir.resolve()):
            raise ValueError("引擎工件不在本次执行目录内")
        relative = path.relative_to(workdir).as_posix()
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(block)
                size += len(block)
        # Explicit execution and relative-file identity avoids basename collisions
        # between smoke/load runs and JMeter asset subdirectories.
        stored = await objects.store_uploaded_file(path=path, object_prefix=prefix,
            object_name=f"{prefix}/{digest.hexdigest()}/{relative}")
        artifact = {**stored, "type": "performance_engine_file", "label": relative,
            "filename": path.name, "sha256": digest.hexdigest(), "size_bytes": size,
            "evidence_schema": "performance-execution-v1", "execution_id": execution_id,
            "relative_path": relative, "owner": owner}
        if not str(artifact.get("path", "")).startswith("rustfs://"):
            raise RuntimeError("性能原件未取得持久对象地址")
        return artifact

    try:
        for path in files:
            capture["artifacts"].append(await upload(path))
        manifest = {"schema": "performance-execution-v1", "execution_id": execution_id,
            "owner": owner, "engine": script.engine, "image": command.image,
            "command": executed_command if executed_command is not None else command.command,
            "environment_names": sorted(command.env),
            "exit_code": result.exit_code, "timed_out": result.timed_out, "container_id": result.container_id,
            "missing_artifacts": capture["missing_artifacts"], "artifacts": capture["artifacts"]}
        path = workdir / "execution-manifest.json"
        path.write_bytes(json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8"))
        capture["artifacts"].append(await upload(path))
        capture["status"] = "partial" if capture["missing_artifacts"] else "saved"
        logger.info("performance_evidence_stored", extra={"execution_id": execution_id,
            "engine": script.engine, "evidence_status": capture["status"], "artifact_count": len(capture["artifacts"]),
            "missing_artifacts": capture["missing_artifacts"]})
    except Exception as exc:
        capture["status"] = "partial" if capture["artifacts"] else "unavailable"
        capture["error"] = f"性能原件保存失败: {exc}"
        logger.exception("performance_evidence_store_failed", extra={"execution_id": execution_id,
            "engine": script.engine, "stored_artifact_count": len(capture["artifacts"])})
    return capture
