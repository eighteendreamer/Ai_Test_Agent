"""Path parameter binding shared by inline and Agent-worker API execution."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.precondition_resolver import PATH_PARAM_PATTERN


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
