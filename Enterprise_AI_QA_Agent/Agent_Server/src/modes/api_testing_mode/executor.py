"""Execute a single API test task: send HTTP request and evaluate assertions."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import httpx

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.contracts import TASK_COMPLETED, TASK_FAILED
from src.modes.api_testing_mode.credential_manager import CredentialManager


class ApiTaskExecutor:
    """Convert an ``ApiTestTask`` into an HTTP call and evaluate assertions."""

    def __init__(
        self,
        *,
        credential_manager: CredentialManager,
        timeout_seconds: float = 30.0,
        auth_token_field: str = "access_token",
    ) -> None:
        self._credential_manager = credential_manager
        self._timeout_seconds = timeout_seconds
        self._auth_token_field = auth_token_field

    async def execute(self, task: ApiTestTask) -> ApiTestTask:
        """Execute the task and populate result fields in-place."""
        if self._restore_completed_request(task):
            return task
        task.started_at = datetime.now(timezone.utc).isoformat()
        task.attempts += 1

        url = task.full_url or task.path
        if not url:
            task.status = TASK_FAILED
            task.last_error = "No URL or path configured for this task."
            task.completed_at = datetime.now(timezone.utc).isoformat()
            return task

        headers = self._build_headers(task)
        body = task.request_body or None
        params = task.request_query or None

        start_time = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=task.timeout_seconds or self._timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.request(
                    method=task.method.upper(),
                    url=url,
                    headers=headers,
                    params=params,
                    json=body if body else None,
                )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            task.duration_ms = round(elapsed_ms, 2)
            task.response_status = response.status_code
            task.response_headers = dict(response.headers)
            task.response_body = self._parse_response_body(response)
        except httpx.TimeoutException:
            task.status = TASK_FAILED
            task.last_error = f"Request timed out after {task.timeout_seconds}s"
            task.duration_ms = (time.perf_counter() - start_time) * 1000.0
            task.completed_at = datetime.now(timezone.utc).isoformat()
            return task
        except httpx.HTTPError as exc:
            task.status = TASK_FAILED
            task.last_error = f"HTTP error: {exc}"
            task.duration_ms = (time.perf_counter() - start_time) * 1000.0
            task.completed_at = datetime.now(timezone.utc).isoformat()
            return task
        except Exception as exc:
            task.status = TASK_FAILED
            task.last_error = f"Unexpected error: {exc}"
            task.duration_ms = (time.perf_counter() - start_time) * 1000.0
            task.completed_at = datetime.now(timezone.utc).isoformat()
            return task

        # Evaluate assertions.
        check_results = self._evaluate_assertions(task)
        task.check_results = check_results
        all_passed = all(item.get("passed") for item in check_results)
        task.status = TASK_COMPLETED if all_passed else TASK_FAILED
        if not all_passed:
            failed_names = [item.get("name", "") for item in check_results if not item.get("passed")]
            task.last_error = f"Assertions failed: {', '.join(failed_names)}"

        # Dynamic login: if this is an auth task and succeeded, extract token.
        if task.execution_mode == "auth" and task.status == TASK_COMPLETED:
            self._handle_dynamic_login(task)

        task.completed_at = datetime.now(timezone.utc).isoformat()
        return task

    def _restore_completed_request(self, task: ApiTestTask) -> bool:
        """Reuse only a checkpoint with complete, matching request evidence."""
        checkpoint = task.execution_checkpoint
        completed = checkpoint.get("completed_request") if isinstance(checkpoint, dict) else None
        if not isinstance(completed, dict):
            return False
        if completed.get("idempotency_key") != task.idempotency_key:
            return False
        if completed.get("status") != "completed":
            return False
        if not isinstance(completed.get("response_status"), int):
            return False
        if "response_body" not in completed or not isinstance(completed.get("check_results"), list):
            return False
        task.status = TASK_COMPLETED
        task.response_status = completed["response_status"]
        task.response_headers = dict(completed.get("response_headers") or {})
        task.response_body = deepcopy(completed["response_body"])
        task.check_results = deepcopy(completed["check_results"])
        task.duration_ms = float(completed.get("duration_ms") or 0)
        task.last_error = ""
        task.completed_at = str(completed.get("completed_at") or datetime.now(timezone.utc).isoformat())
        return True

    # ------------------------------------------------------------------
    # Dynamic login
    # ------------------------------------------------------------------

    def _handle_dynamic_login(self, task: ApiTestTask) -> None:
        """After a successful auth task, extract token and register credentials."""
        response_body = task.response_body
        if not isinstance(response_body, dict):
            return
        session = self._credential_manager.create_from_login_response(
            response_body=response_body,
            token_field=self._auth_token_field,
            login_endpoint=f"{task.method} {task.path}",
        )
        # Update the task's auth_ref so downstream tasks can reference it.
        if session.credential_session_id:
            task.auth_ref = session.credential_session_id

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    def _build_headers(self, task: ApiTestTask) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if task.request_body:
            headers["Content-Type"] = "application/json"
        # Merge credential headers.
        if task.auth_ref:
            cred_headers = self._credential_manager.build_request_headers(task.auth_ref)
            headers.update(cred_headers)
        # Merge task-level headers (override).
        if task.request_headers:
            headers.update(task.request_headers)
        return headers

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response_body(self, response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                return response.json()
            except Exception:
                return response.text[:8000]
        return response.text[:8000]

    # ------------------------------------------------------------------
    # Assertion evaluation
    # ------------------------------------------------------------------

    def _evaluate_assertions(self, task: ApiTestTask) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for assertion_data in task.assertions:
            kind = assertion_data.get("kind") if isinstance(assertion_data, dict) else getattr(assertion_data, "kind", "")
            expected = assertion_data.get("expected") if isinstance(assertion_data, dict) else getattr(assertion_data, "expected", None)
            path = assertion_data.get("path", "") if isinstance(assertion_data, dict) else getattr(assertion_data, "path", "")
            description = assertion_data.get("description", "") if isinstance(assertion_data, dict) else getattr(assertion_data, "description", "")

            result = self._check_assertion(
                kind=kind,
                expected=expected,
                path=path,
                description=description,
                task=task,
            )
            results.append(result)

        # If no explicit assertions, add a default status check.
        if not results:
            status = task.response_status or 0
            passed = 200 <= status < 300
            results.append(
                {
                    "name": "default_status_check",
                    "kind": "status_code_range",
                    "passed": passed,
                    "expected": "2xx",
                    "actual": status,
                    "description": "Default: expect 2xx response status.",
                }
            )
        return results

    def _check_assertion(
        self,
        *,
        kind: str,
        expected: Any,
        path: str,
        description: str,
        task: ApiTestTask,
    ) -> dict[str, Any]:
        status = task.response_status or 0
        body = task.response_body

        if kind == "status_code":
            passed = status == expected
            return {
                "name": f"status_code={expected}",
                "kind": kind,
                "passed": passed,
                "expected": expected,
                "actual": status,
                "description": description or f"Expect status {expected}",
            }

        if kind == "status_code_range":
            expected_list = expected if isinstance(expected, list) else [200, 201, 204]
            passed = status in expected_list
            return {
                "name": f"status_in_{expected_list}",
                "kind": kind,
                "passed": passed,
                "expected": expected_list,
                "actual": status,
                "description": description or f"Expect status in {expected_list}",
            }

        if kind == "json_field_present":
            value, exists = self._lookup_path(body, path)
            return {
                "name": f"field_present:{path}",
                "kind": kind,
                "passed": exists,
                "expected": "present",
                "actual": value if exists else None,
                "description": description or f"Expect field '{path}' present",
            }

        if kind == "json_field_equals":
            value, exists = self._lookup_path(body, path)
            passed = exists and value == expected
            return {
                "name": f"field_equals:{path}",
                "kind": kind,
                "passed": passed,
                "expected": expected,
                "actual": value if exists else None,
                "description": description or f"Expect field '{path}' == {expected}",
            }

        if kind == "json_field_in":
            value, exists = self._lookup_path(body, path)
            expected_set = expected if isinstance(expected, list) else [expected]
            passed = exists and value in expected_set
            return {
                "name": f"field_in:{path}",
                "kind": kind,
                "passed": passed,
                "expected": expected_set,
                "actual": value if exists else None,
                "description": description or f"Expect field '{path}' in {expected_set}",
            }

        if kind == "header_present":
            header_name = path or str(expected or "")
            actual = task.response_headers.get(header_name) or task.response_headers.get(header_name.lower())
            passed = actual is not None
            return {
                "name": f"header_present:{header_name}",
                "kind": kind,
                "passed": passed,
                "expected": "present",
                "actual": actual,
                "description": description or f"Expect header '{header_name}' present",
            }

        if kind == "body_contains":
            body_str = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body or "")
            search_text = str(expected or "")
            passed = search_text in body_str
            return {
                "name": f"body_contains:{search_text[:30]}",
                "kind": kind,
                "passed": passed,
                "expected": search_text,
                "actual": body_str[:200],
                "description": description or f"Expect body contains '{search_text}'",
            }

        if kind == "response_time_ms":
            max_ms = float(expected or 5000)
            passed = task.duration_ms <= max_ms
            return {
                "name": f"response_time<={max_ms}ms",
                "kind": kind,
                "passed": passed,
                "expected": max_ms,
                "actual": task.duration_ms,
                "description": description or f"Expect response within {max_ms}ms",
            }

        return {
            "name": f"unknown_assertion:{kind}",
            "kind": kind,
            "passed": False,
            "expected": expected,
            "actual": None,
            "description": description or f"Unknown assertion kind '{kind}' is not executable.",
        }

    def _lookup_path(self, obj: Any, path: str) -> tuple[Any, bool]:
        if not path or obj is None:
            return obj, obj is not None
        parts = path.replace("[", ".").replace("]", "").split(".")
        current = obj
        for part in parts:
            if not part:
                continue
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    return None, False
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                except (IndexError, ValueError):
                    return None, False
            else:
                return None, False
        return current, True


__all__ = ["ApiTaskExecutor"]
