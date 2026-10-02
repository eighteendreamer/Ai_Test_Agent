from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
import time
import traceback
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlencode

from src.application.compatibility.runner_journal import RunnerJournal

DEFAULT_BASE_URL = "http://127.0.0.1:1032"


def _json_request(base_url: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


class CompatibilityRunnerClient:
    def __init__(
        self,
        *,
        base_url: str,
        runner_id: str,
        name: str,
        capabilities: list[str],
        devices: list[str],
        max_parallel: int,
        poll_interval: float,
        executor: str,
        metadata: dict[str, Any] | None = None,
        state_file: str | Path | None = None,
    ) -> None:
        self.base_url = base_url
        self.runner_id = runner_id
        self.name = name
        self.capabilities = capabilities
        self.devices = devices
        self.max_parallel = max(1, max_parallel)
        self.poll_interval = max(1.0, poll_interval)
        self.executor = executor
        self.metadata = metadata or {}
        self.active_task_ids: list[str] = []
        self.journal = RunnerJournal(state_file, base_url=base_url, runner_id=runner_id)

    def close(self) -> None:
        self.journal.close()

    def register(self) -> None:
        record = _json_request(
            self.base_url,
            "/api/v1/compatibility/runners/register",
            method="POST",
            payload={
                "runner_id": self.runner_id,
                "name": self.name,
                "os": platform.platform(),
                "capabilities": self.capabilities,
                "devices": self.devices,
                "max_parallel": self.max_parallel,
                "metadata": {
                    **self.metadata,
                    "runner_kind": "compatibility_runner_client",
                    "executor": self.executor,
                    "python": platform.python_version(),
                    "assignment_fencing_version": 1,
                },
            },
        )
        print(f"registered runner={record.get('runner_id')} status={record.get('status')}")

    def heartbeat(self, status: str = "online") -> None:
        _json_request(
            self.base_url,
            f"/api/v1/compatibility/runners/{self.runner_id}/heartbeat",
            method="POST",
            payload={
                "status": status,
                "active_task_ids": self.active_task_ids,
                "metadata": {"last_seen_by": "compatibility_runner_client"},
            },
        )

    def poll(self) -> list[dict[str, Any]]:
        response = _json_request(
            self.base_url,
            f"/api/v1/compatibility/runners/{self.runner_id}/tasks/poll?limit={self.max_parallel}",
            method="POST",
        )
        return list(response.get("tasks") or [])

    def report(self, task_id: str, payload: dict[str, Any]) -> None:
        _json_request(
            self.base_url,
            f"/api/v1/compatibility/runners/{self.runner_id}/tasks/{task_id}/report",
            method="POST",
            payload=payload,
        )

    def upload_artifact(
        self,
        task_id: str,
        *,
        filename: str,
        content: bytes,
        artifact_type: str,
        label: str,
        mime_type: str,
        metadata: dict[str, Any] | None = None,
        assignment_id: str | None = None,
    ) -> dict[str, Any]:
        return _json_request(
            self.base_url,
            f"/api/v1/compatibility/runners/{self.runner_id}/tasks/{task_id}/artifacts/upload",
            method="POST",
            payload={
                "filename": filename,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "type": artifact_type,
                "label": label,
                "mime_type": mime_type,
                "metadata": metadata or {},
                "assignment_id": assignment_id,
            },
        )

    def execute_mode_calls(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise ValueError("Compatibility task is missing task_id.")
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        assignment_id = metadata.get("assignment_id")
        suffix = "?" + urlencode({"assignment_id": assignment_id}) if assignment_id else ""
        return _json_request(
            self.base_url,
            f"/api/v1/compatibility/runners/{self.runner_id}/tasks/{task_id}/mode-calls/execute{suffix}",
            method="POST",
        )

    def execute_task(self, task: dict[str, Any]) -> dict[str, Any]:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        mode_calls = task.get("mode_calls") if isinstance(task.get("mode_calls"), list) else []
        if self.executor == "dry-run":
            return self._dry_run_result(task, metadata, mode_calls)
        if self.executor == "server-mode-calls":
            return self._execute_server_mode_calls_task(task, metadata, mode_calls)
        if self.executor in {"auto", "http-smoke"} and self._is_web_task(metadata):
            return self._execute_http_smoke_task(task, metadata, mode_calls)
        return {
            "status": "failed",
            "result": {
                "summary": "No real executor is configured for this compatibility runner.",
                "environment_id": task.get("environment_id"),
                "planned_mode_calls": mode_calls,
            },
            "artifacts": [],
            "error": "executor_not_configured",
            "metadata": {"executor": "none"},
        }

    def _execute_server_mode_calls_task(
        self,
        task: dict[str, Any],
        metadata: dict[str, Any],
        mode_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        product_type = self._product_type(metadata)
        if product_type and product_type not in {"web", "h5"}:
            return {
                "status": "failed",
                "result": {
                    "summary": (
                        "server-mode-calls only executes center-system tool calls; "
                        f"{product_type} compatibility tasks require an external runner/provider executor."
                    ),
                    "environment_id": task.get("environment_id"),
                    "planned_mode_calls": [call.get("tool_key") for call in mode_calls if isinstance(call, dict)],
                    "product_type": product_type,
                },
                "artifacts": [],
                "error": "executor_requires_external_runner",
                "metadata": {
                    "executor": "server_mode_calls",
                    "unsupported_product_type": product_type,
                },
            }
        if not mode_calls:
            return {
                "status": "completed",
                "result": {
                    "summary": "Compatibility task has no planned mode calls.",
                    "environment_id": task.get("environment_id"),
                    "mode_call_results": [],
                },
                "artifacts": [],
                "error": None,
                "metadata": {"executor": "server_mode_calls", "mode_bridge_status": "completed"},
            }
        bridge_result = self.execute_mode_calls(task)
        bridge_status = str(bridge_result.get("status") or "").strip().lower()
        failed = bridge_status != "completed" or bridge_result.get("ok") is False
        bridge_summary = str(
            bridge_result.get("summary") or "Compatibility mode calls executed by center-system bridge."
        )
        return {
            "status": "failed" if failed else "completed",
            "result": {
                "summary": (
                    f"Compatibility mode bridge did not complete ({bridge_status or 'unknown'}): {bridge_summary}"
                    if failed
                    else bridge_summary
                ),
                "environment_id": task.get("environment_id"),
                "mode_call_count": bridge_result.get("mode_call_count", len(mode_calls)),
                "mode_call_results": bridge_result.get("mode_call_results") or [],
            },
            "artifacts": [],
            "error": bridge_result.get("error") or (f"mode_bridge_{bridge_status or 'unknown'}" if failed else None),
            "metadata": {
                "executor": "server_mode_calls",
                "mode_bridge_status": bridge_status or "completed",
            },
        }

    def run_once(self) -> int:
        self.heartbeat()
        for entry in self.journal.entries():
            if entry["phase"] in {"executing", "uploading"}:
                self.journal.update(entry["assignment"], "review", reason="unknown_" + entry["phase"])
                print(f"runner_recovery_review task={entry['task']['task_id']} phase={entry['phase']}")
        pending = [entry for entry in self.journal.entries() if entry["phase"] not in {"review", "conflict"}]
        if not pending:
            self.journal.enqueue(self.poll())
            pending = [entry for entry in self.journal.entries() if entry["phase"] not in {"review", "conflict"}]
        if not pending:
            print("no tasks")
            return 0
        processed = 0
        for entry in pending:
            task = entry["task"]
            task_id = task["task_id"]
            assignment = entry["assignment"]
            self.active_task_ids.append(task_id)
            try:
                self.heartbeat(status="busy")
                if entry["phase"] == "queued":
                    if (task.get("metadata") or {}).get("assignment_id") and not self._assignment_is_current(task):
                        self.journal.update(assignment, "conflict", reason="assignment_superseded")
                        print(f"runner_assignment_conflict task={task_id}")
                        continue
                    self.journal.update(assignment, "executing")
                    try:
                        payload = self.execute_task(task)
                    except (error.HTTPError, error.URLError, TimeoutError):
                        # A remote tool may have committed before transport failed.
                        raise
                    except Exception as exc:
                        payload = {
                            "status": "failed", "result": {"summary": str(exc)}, "artifacts": [],
                            "error": traceback.format_exc(), "metadata": {"executor": "compatibility_runner_client"},
                        }
                    metadata = task.get("metadata") or {}
                    if metadata.get("assignment_id"):
                        payload["assignment_id"] = metadata["assignment_id"]
                    self.journal.update(assignment, "executed", payload=payload)
                    entry["phase"], entry["payload"] = "executed", payload
                payload = entry["payload"]
                if entry["phase"] == "executed":
                    self.journal.update(assignment, "uploading")
                    uploaded = self._upload_execution_artifacts(task, payload)
                    if uploaded:
                        payload["artifacts"] = []
                        payload.setdefault("metadata", {})["uploaded_artifact_ids"] = [
                            item.get("artifact_id") for item in uploaded if isinstance(item, dict)
                        ]
                    self.journal.update(assignment, "report_ready", payload=payload)
                self.report(task_id, payload)
                self.journal.acknowledge(assignment)
                processed += 1
                print(f"reported task={task_id} status={payload.get('status')}")
            except error.HTTPError as exc:
                if exc.code not in {404, 409}:
                    raise
                self.journal.update(assignment, "conflict", reason=f"http_{exc.code}")
                print(f"runner_delivery_conflict task={task_id} http_status={exc.code}")
            finally:
                self.active_task_ids = [item for item in self.active_task_ids if item != task_id]
        self.heartbeat()
        return processed

    def _assignment_is_current(self, task: dict[str, Any]) -> bool:
        records = _json_request(self.base_url, "/api/v1/compatibility/tasks?" + urlencode({"runner_id": self.runner_id}))
        assignment = task["metadata"]["assignment_id"]
        return any(
            item.get("task_id") == task["task_id"] and item.get("status") == "assigned"
            and item.get("assigned_runner_id") == self.runner_id
            and (item.get("metadata") or {}).get("assignment_id") == assignment
            for item in records
        )

    def serve_forever(self) -> None:
        registered = False
        while True:
            try:
                if not registered:
                    self.register()
                    registered = True
                self.run_once()
            except (error.HTTPError, error.URLError, TimeoutError) as exc:
                print(f"runner loop error: {exc}")
                registered = False
            time.sleep(self.poll_interval)

    def _dry_run_result(
        self,
        task: dict[str, Any],
        metadata: dict[str, Any],
        mode_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        environment = metadata.get("environment") if isinstance(metadata.get("environment"), dict) else {}
        product = metadata.get("product") if isinstance(metadata.get("product"), dict) else {}
        return {
            "status": "completed",
            "result": {
                "summary": "Dry-run compatibility task accepted by runner.",
                "environment_id": task.get("environment_id"),
                "environment": environment,
                "product_name": product.get("name"),
                "case_count": len(task.get("case_ids") or []),
                "planned_mode_calls": [call.get("tool_key") for call in mode_calls if isinstance(call, dict)],
            },
            "artifacts": [],
            "metadata": {"executor": "dry_run"},
        }

    def _execute_http_smoke_task(
        self,
        task: dict[str, Any],
        metadata: dict[str, Any],
        mode_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        url = self._entrypoint_url(metadata)
        started = time.perf_counter()
        result: dict[str, Any] = {
            "url": url,
            "environment_id": task.get("environment_id"),
            "planned_mode_calls": [call.get("tool_key") for call in mode_calls if isinstance(call, dict)],
        }
        if not url:
            return {
                "status": "failed",
                "result": {**result, "summary": "HTTP smoke executor could not find a Web/H5 entrypoint URL."},
                "artifacts": [],
                "error": "missing_entrypoint_url",
                "metadata": {"executor": "http_smoke"},
            }
        try:
            req = request.Request(url, headers={"User-Agent": "CompatibilityRunner/1.0"})
            with request.urlopen(req, timeout=20) as response:
                body = response.read(4096)
                status_code = int(getattr(response, "status", 200))
                content_type = response.headers.get("content-type", "")
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            ok = 200 <= status_code < 400
            result.update(
                {
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "content_type": content_type,
                    "body_excerpt": body.decode("utf-8", errors="replace")[:1000],
                    "summary": f"HTTP smoke {'passed' if ok else 'failed'}: {status_code} in {duration_ms} ms.",
                }
            )
            return {
                "status": "completed" if ok else "failed",
                "result": result,
                "artifacts": [],
                "error": None if ok else f"http_status_{status_code}",
                "metadata": {"executor": "http_smoke"},
            }
        except error.HTTPError as exc:
            body = exc.read(4096)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            result.update(
                {
                    "status_code": exc.code,
                    "duration_ms": duration_ms,
                    "content_type": exc.headers.get("content-type", ""),
                    "body_excerpt": body.decode("utf-8", errors="replace")[:1000],
                    "summary": f"HTTP smoke failed: {exc.code} in {duration_ms} ms.",
                }
            )
            return {
                "status": "failed",
                "result": result,
                "artifacts": [],
                "error": f"http_status_{exc.code}",
                "metadata": {"executor": "http_smoke"},
            }
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            result.update(
                {
                    "duration_ms": duration_ms,
                    "summary": "HTTP smoke failed before receiving a response.",
                }
            )
            return {
                "status": "failed",
                "result": result,
                "artifacts": [],
                "error": traceback.format_exc(),
                "metadata": {"executor": "http_smoke"},
            }

    def _upload_execution_artifacts(self, task: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            return []
        summary = str((payload.get("result") or {}).get("summary") or "")
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        artifact = self.upload_artifact(
            task_id,
            filename=f"{task_id}_runner_log.json",
            content=json.dumps(
                {
                    "task_id": task_id,
                    "status": payload.get("status"),
                    "summary": summary,
                    "environment_id": task.get("environment_id"),
                    "result": payload.get("result") or {},
                    "planned_mode_calls": (payload.get("result") or {}).get("planned_mode_calls", []),
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
            artifact_type="runner_log",
            label="Runner execution log",
            mime_type="application/json",
            metadata={"preview": summary[:240]},
            assignment_id=metadata.get("assignment_id"),
        )
        return [artifact]

    def _is_web_task(self, metadata: dict[str, Any]) -> bool:
        return self._product_type(metadata) in {"web", "h5"}

    def _product_type(self, metadata: dict[str, Any]) -> str:
        manifest = metadata.get("product_access_manifest") if isinstance(metadata.get("product_access_manifest"), dict) else {}
        product = metadata.get("product") if isinstance(metadata.get("product"), dict) else {}
        return str(manifest.get("product_type") or product.get("product_type") or "").strip()

    def _entrypoint_url(self, metadata: dict[str, Any]) -> str:
        manifest = metadata.get("product_access_manifest") if isinstance(metadata.get("product_access_manifest"), dict) else {}
        entrypoint = manifest.get("entrypoint") if isinstance(manifest.get("entrypoint"), dict) else {}
        product = metadata.get("product") if isinstance(metadata.get("product"), dict) else {}
        url = str(entrypoint.get("url") or product.get("entrypoint") or "").strip()
        return url if url.startswith(("http://", "https://")) else ""


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_metadata_json(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--metadata-json must be valid JSON: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SystemExit("--metadata-json must decode to a JSON object.")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Compatibility testing runner client.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Enterprise AI QA Agent server URL. (default: %(default)s)",
    )
    parser.add_argument("--runner-id", default=f"compat-runner-{platform.node() or 'local'}")
    parser.add_argument("--name", default="Local Compatibility Runner")
    parser.add_argument("--capabilities", default="local_browser,playwright,chrome,edge,firefox")
    parser.add_argument("--devices", default="")
    parser.add_argument(
        "--metadata-json",
        default="",
        help='Additional runner metadata as JSON, e.g. {"os_version":"14","browser_versions":{"chrome":"120.0"}}.',
    )
    parser.add_argument("--max-parallel", type=int, default=1)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="Register and process one polling cycle.")
    parser.add_argument("--state-file", help="Local SQLite recovery journal; use a stable private local path.")
    parser.add_argument(
        "--executor",
        choices=["dry-run", "http-smoke", "server-mode-calls", "auto", "none"],
        default="dry-run",
        help=(
            "Task executor. auto currently runs HTTP smoke for Web/H5 and fails unsupported product types; "
            "server-mode-calls asks the center system to execute the task's approved mode_calls."
        ),
    )
    parser.add_argument("--no-dry-run", action="store_true", help="Deprecated alias for --executor none.")
    args = parser.parse_args()
    identity = json.dumps([args.base_url.rstrip("/"), args.runner_id], ensure_ascii=False).encode("utf-8")
    state_file = args.state_file or Path(".compatibility-runner") / (hashlib.sha256(identity).hexdigest()[:20] + ".sqlite3")

    client = CompatibilityRunnerClient(
        base_url=args.base_url,
        runner_id=args.runner_id,
        name=args.name,
        capabilities=_split_csv(args.capabilities),
        devices=_split_csv(args.devices),
        max_parallel=args.max_parallel,
        poll_interval=args.poll_interval,
        executor="none" if args.no_dry_run else args.executor,
        metadata=_parse_metadata_json(args.metadata_json),
        state_file=state_file,
    )
    try:
        if args.once:
            client.register()
            client.run_once()
        else:
            client.serve_forever()
    finally:
        client.close()


if __name__ == "__main__":
    main()
