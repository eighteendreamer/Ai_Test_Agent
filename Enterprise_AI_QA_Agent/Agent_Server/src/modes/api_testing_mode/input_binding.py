"""Actual response binding shared by inline and Agent-worker API execution."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import logging
from typing import Any
from urllib.parse import urlsplit

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.precondition_resolver import PATH_PARAM_PATTERN

logger = logging.getLogger("uvicorn.error.api_testing_mode.coordinator")
_MISSING = object()


def apply_input_bindings(task: ApiTestTask, outputs: Mapping[str, Any]) -> list[str]:
    """Apply declared bindings atomically; report facts missing before dispatch."""
    candidate = task.model_copy(deep=True)
    gaps = []
    for binding in task.input_bindings:
        if binding.source_task_id:
            value = extract_path_value(outputs[binding.source_task_id], binding.source_path) if binding.source_task_id in outputs else _MISSING
        else:
            value = _MISSING
        if value is _MISSING and binding.literal_value is not None:
            value = binding.literal_value
        # An empty legacy binding without a source or literal did nothing.
        if not binding.source_task_id and binding.literal_value is None:
            continue
        reason = "source_output_missing" if value is _MISSING else inject_binding_value(candidate, binding.target_path, value)
        if reason:
            gaps.append(f"{binding.source_task_id or 'literal'}:{binding.source_path} -> {binding.target_path} ({reason})")
            logger.warning("api_input_binding_unresolved", extra={"task_id": task.task_id,
                "source_task_id": binding.source_task_id, "source_path": binding.source_path,
                "target_path": binding.target_path, "reason": reason})
    if gaps:
        return gaps
    bind_path_parameters(candidate, outputs)
    for field in ("path", "full_url", "request_body", "request_query", "request_headers"):
        setattr(task, field, getattr(candidate, field))
    if task.input_bindings:
        logger.info("api_input_bindings_applied", extra={"task_id": task.task_id,
            "binding_count": len(task.input_bindings)})
    return []


def extract_path_value(value: Any, path: str) -> Any:
    """Read the existing dot/bracket source syntax without losing present null."""
    for part in path.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return deepcopy(value)


def inject_binding_value(task: ApiTestTask, target: str, value: Any) -> str:
    """Keep the existing target aliases; headers and URL paths need scalars."""
    if target.startswith(("url.path.", "path.")):
        parameter = target.split(".")[-1]
        if not parameter or value is None or isinstance(value, (dict, list, tuple)):
            return "path_value_not_scalar"
        substitute_path_parameter(task, parameter, value)
        return ""
    prefix, separator, field = target.partition(".")
    if not separator or not field:
        return "target_path_unsupported"
    if prefix in {"body", "request_body"}:
        if task.request_body is None:
            task.request_body = {}
        _set_nested(task.request_body, field, value)
    elif prefix == "query":
        task.request_query[field] = deepcopy(value)
    elif prefix == "request_query":
        _set_nested(task.request_query, field, value)
    elif prefix == "request_headers":
        if value is None or isinstance(value, (dict, list, tuple)):
            return "header_value_not_scalar"
        text = str(value)
        if any(c in field for c in ("\r", "\n", ":")) or "\r" in text or "\n" in text:
            return "header_value_invalid"
        task.request_headers[field] = text
    else:
        return "target_path_unsupported"
    return ""


def _set_nested(root: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = root
    for part in parts[:-1]:
        if not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = deepcopy(value)


def substitute_path_parameter(task: ApiTestTask, parameter: str, value: Any) -> None:
    for field in ("full_url", "path"):
        text = getattr(task, field)
        if text:
            setattr(task, field, PATH_PARAM_PATTERN.sub(
                lambda match: str(value) if (match.group(1) or match.group(2)) == parameter else match.group(0),
                text,
            ))


def bind_path_parameters(task: ApiTestTask, outputs: Mapping[str, Any]) -> None:
    parameters = [match.group(1) or match.group(2)
                  for match in PATH_PARAM_PATTERN.finditer(_path_text(task))]
    for parameter in parameters:
        source = task.path_parameter_sources.get(parameter)
        # Old persisted tasks lack parameter-level provenance. Preserve their
        # existing dependency search; new plans bind only the chosen producer.
        source_ids = [source] if source else task.depends_on
        for source_id in source_ids:
            output = outputs.get(source_id)
            if not isinstance(output, dict):
                continue
            value = _parameter_value(output, parameter)
            if value is not None:
                substitute_path_parameter(task, parameter, value)
                break


def unresolved_path_parameters(task: ApiTestTask) -> list[str]:
    """Return placeholders that remain after planned input binding.

    Callers must fail before opening an HTTP connection. Sending ``{id}``
    would turn a planning gap into a misleading business result.
    """
    text = _path_text(task)
    return list(dict.fromkeys(
        match.group(1) or match.group(2)
        for match in PATH_PARAM_PATTERN.finditer(text or "")
    ))


def _path_text(task: ApiTestTask) -> str:
    """Inspect only the URL path; ``:8000`` is a port, not a placeholder."""
    # Match ApiTaskExecutor's effective request URL, including restored tasks
    # whose display path differs from the address that will actually be sent.
    raw = task.full_url or task.path or ""
    return urlsplit(raw).path


def _parameter_value(output: dict[str, Any], parameter: str) -> Any:
    if parameter in output:
        return output[parameter]
    if parameter.lower().endswith("id") and "id" in output:
        return output["id"]
    for container in ("data", "result"):
        nested = output.get(container)
        if isinstance(nested, dict):
            if parameter in nested:
                return nested[parameter]
            if parameter.lower().endswith("id") and "id" in nested:
                return nested["id"]
    return None
