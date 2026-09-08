from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
from typing import Any, Iterator

from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.application.observability.trace_context import TraceContext
from src.core.config import LangSmithConfig


logger = logging.getLogger(__name__)


@dataclass
class TraceScope:
    """Small business-facing handle; LangSmith types do not leave this module."""

    _run: Any
    _redactor: OutputSafetyPolicy
    _capture_outputs: bool = False

    def reference(self) -> dict[str, str]:
        """Return non-sensitive identifiers for local event/snapshot correlation."""
        reference: dict[str, str] = {}
        for key, attribute in (
            ("run_id", "id"),
            ("trace_id", "trace_id"),
            ("dotted_order", "dotted_order"),
        ):
            value = getattr(self._run, attribute, "")
            if value:
                reference[key] = str(value)
        get_url = getattr(self._run, "get_url", None)
        if callable(get_url):
            try:
                url = get_url()
            except Exception:  # pragma: no cover - SDK/network specific
                url = ""
            if url:
                reference["url"] = str(url)
        return reference

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        if not self._capture_outputs:
            return
        sanitized = self._redactor.sanitize_for_audit(outputs)
        try:
            self._run.add_outputs(sanitized)
        except Exception:  # pragma: no cover - SDK/network specific
            logger.exception("langsmith_trace_output_failed")


class LangSmithObservabilityAdapter:
    """Best-effort LangSmith bridge; failures never stop the business runtime."""

    def __init__(
        self,
        config: LangSmithConfig,
        *,
        environment: str = "development",
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._environment = str(environment or "development")
        self._redactor = OutputSafetyPolicy()
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._config.enabled and self._config.tracing_mode != "off"

    @contextmanager
    def trace_turn(
        self,
        context: TraceContext,
        *,
        inputs: dict[str, Any] | None = None,
    ) -> Iterator[TraceScope | None]:
        if not self.enabled:
            yield None
            return

        # ``errors_only`` deliberately avoids opening a successful Run.  The
        # exception is recorded after the business callback unwinds, so the
        # observability path cannot affect the callback's control flow.
        if self._config.tracing_mode == "errors_only":
            try:
                yield None
            except BaseException as exc:
                self._record_error_trace(context, inputs=inputs, exception=exc)
                raise
            return

        try:
            import langsmith as ls

            client = self._get_client(ls)
            metadata = self._redactor.sanitize_for_audit(
                {
                    **context.metadata(),
                    "environment": self._environment,
                }
            )
            safe_inputs = self._redactor.sanitize_for_audit(inputs or {})
            if not self._config.capture_inputs:
                safe_inputs = {"trace_id": context.trace_id, "turn_id": context.turn_id}
            run_cm = ls.trace(
                name="enterprise_ai_qa_agent.turn",
                run_type="chain",
                inputs=safe_inputs,
                project_name=self._config.project,
                tags=context.tags,
                metadata=metadata,
                client=client,
                exceptions_to_handle=(Exception,),
            )
            run = run_cm.__enter__()
            tracing_cm = ls.tracing_context(
                project_name=self._config.project,
                tags=context.tags,
                metadata=metadata,
                parent=run,
                enabled=True,
                client=client,
            )
            tracing_cm.__enter__()
        except Exception:
            logger.exception(
                "langsmith_trace_start_failed",
                extra={"trace_id": context.trace_id, "turn_id": context.turn_id},
            )
            yield None
            return

        scope = TraceScope(run, self._redactor, self._config.capture_outputs)
        try:
            yield scope
        except BaseException as exc:
            self._safe_exit(tracing_cm, exc)
            self._safe_exit(run_cm, exc)
            raise
        else:
            self._safe_exit(tracing_cm, None)
            self._safe_exit(run_cm, None)

    @contextmanager
    def trace_node(
        self,
        context: TraceContext,
        *,
        node_name: str,
        inputs: dict[str, Any] | None = None,
    ) -> Iterator[None]:
        """Create a child span while a turn trace is active."""
        if not self.enabled or self._config.tracing_mode == "errors_only":
            yield
            return
        try:
            import langsmith as ls

            client = self._get_client(ls)
            metadata = self._redactor.sanitize_for_audit(context.metadata())
            safe_inputs = self._redactor.sanitize_for_audit(inputs or {})
            if not self._config.capture_inputs:
                safe_inputs = {"trace_id": context.trace_id, "turn_id": context.turn_id}
            trace_cm = ls.trace(
                name=f"enterprise_ai_qa_agent.node.{node_name}",
                run_type="chain",
                inputs=safe_inputs,
                project_name=self._config.project,
                tags=[*context.tags, f"node:{node_name}"],
                metadata={**metadata, "node_name": node_name},
                client=client,
                exceptions_to_handle=(Exception,),
            )
        except Exception:
            logger.exception(
                "langsmith_node_trace_failed",
                extra={"trace_id": context.trace_id, "node_name": node_name},
            )
            yield
            return
        with trace_cm:
            yield

    def context_from_mapping(self, value: dict[str, Any]) -> TraceContext:
        return TraceContext(
            session_id=value.get("session_id", ""),
            turn_id=value.get("turn_id", ""),
            trace_id=value.get("trace_id", ""),
            parent_trace_id=value.get("parent_trace_id", ""),
            mode_key=value.get("mode_key", "default"),
            agent_key=value.get("agent_key", ""),
            environment=self._environment,
        )

    def _get_client(self, langsmith_module: Any) -> Any:
        if self._client is not None:
            return self._client
        api_key = os.getenv(self._config.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(
                f"LangSmith tracing is enabled but {self._config.api_key_env} is not configured"
            )
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout_ms": self._config.timeout_ms,
            "tracing_sampling_rate": self._config.sample_rate,
        }
        if self._config.endpoint.strip():
            kwargs["api_url"] = self._config.endpoint.strip()
        workspace_id = os.getenv(self._config.workspace_id_env, "").strip()
        if workspace_id:
            kwargs["workspace_id"] = workspace_id
        self._client = langsmith_module.Client(**kwargs)
        return self._client

    def _record_error_trace(
        self,
        context: TraceContext,
        *,
        inputs: dict[str, Any] | None,
        exception: BaseException,
    ) -> None:
        try:
            import langsmith as ls

            client = self._get_client(ls)
            metadata = self._redactor.sanitize_for_audit(
                {**context.metadata(), "environment": self._environment}
            )
            safe_inputs = self._redactor.sanitize_for_audit(inputs or {})
            if not self._config.capture_inputs:
                safe_inputs = {"trace_id": context.trace_id, "turn_id": context.turn_id}
            trace_cm = ls.trace(
                name="enterprise_ai_qa_agent.turn.error",
                run_type="chain",
                inputs=safe_inputs,
                project_name=self._config.project,
                tags=[*context.tags, "outcome:error"],
                metadata=metadata,
                client=client,
                exceptions_to_handle=(Exception,),
            )
            run = trace_cm.__enter__()
            self._safe_exit(trace_cm, exception)
            logger.info(
                "langsmith_error_trace_recorded",
                extra={"trace_id": context.trace_id, "turn_id": context.turn_id},
            )
        except Exception:
            logger.exception(
                "langsmith_error_trace_failed",
                extra={"trace_id": context.trace_id, "turn_id": context.turn_id},
            )

    @staticmethod
    def _safe_exit(manager: Any, exception: BaseException | None) -> None:
        try:
            if exception is None:
                manager.__exit__(None, None, None)
            else:
                manager.__exit__(type(exception), exception, exception.__traceback__)
        except Exception:  # pragma: no cover - SDK/network specific
            logger.exception("langsmith_trace_finish_failed")
