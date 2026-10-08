"""RecorderSessionService：录制会话权威状态机与编排（方案 4.1/4.2，P0-6）。

职责：
- 生命周期 launch → ready → active ↔ paused → finalizing → completed/failed
  （destroy → discarded），迁移合法性校验，非法迁移拒绝并记日志；
- 事件消费循环：driver.on_recorder_event → 攒批（20 条 / 0.5s）→ PG 落库；
  paused 期间事件丢弃计数（recorder.js 暂停后不再 emit，仅网络残留）；
- stop：停采集 → flush → RecordingGraphStore.finalize 固化 → completed；
- destroy：关驱动 + PG 标 discarded，不写图谱（保留审计行）。

内存态为操作权威（即时性），PG 为持久层；进程重启后运行时丢失，未终态
会话的控制操作将拒绝（P0 边界，见 _require_runtime）。

状态机单测见 tests/test_recorder_session_service.py。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.recorder.drivers import (
    BrowserDriver,
    DriverRegistry,
    EmbeddedBridge,
    build_default_registry,
)
from src.application.recorder.drivers.base import CaptureCheckpoint
from src.core.config import Settings
from src.core.request_context import bind_request_context
from src.infrastructure.recording_store import PostgresRecordingStore
from src.schemas.recording import (
    RecorderEvent,
    RecordingControlAction,
    RecordingCreateRequest,
    RecordingSession,
    RecordingStatus,
    RecordingFinishSettings,
)

logger = logging.getLogger(__name__)

# 合法状态迁移表（方案 4.1/4.2）：action → {当前状态 → 目标状态}
_CONTROL_TRANSITIONS: dict[RecordingControlAction, dict[RecordingStatus, RecordingStatus]] = {
    RecordingControlAction.start: {RecordingStatus.ready: RecordingStatus.active},
    RecordingControlAction.pause: {RecordingStatus.active: RecordingStatus.paused},
    RecordingControlAction.resume: {RecordingStatus.paused: RecordingStatus.active},
    RecordingControlAction.stop: {
        RecordingStatus.ready: RecordingStatus.finalizing,
        RecordingStatus.active: RecordingStatus.finalizing,
        RecordingStatus.paused: RecordingStatus.finalizing,
    },
    RecordingControlAction.destroy: {
        RecordingStatus.launching: RecordingStatus.discarded,
        RecordingStatus.ready: RecordingStatus.discarded,
        RecordingStatus.active: RecordingStatus.discarded,
        RecordingStatus.paused: RecordingStatus.discarded,
    },
}

_CONSUME_BATCH_SIZE = 20
_CONSUME_FLUSH_INTERVAL = 0.5
_DB_RETRY_DELAYS = (0.2, 0.4, 0.8)
_DETACH_DELAY_SECONDS = 30.0
_LAUNCH_READY_TIMEOUT = 60.0


@dataclass
class _SessionRuntime:
    """会话执行的进程内协调状态；持久会话与证据仍以 PG 为准。"""

    session: RecordingSession
    driver: BrowserDriver
    status: RecordingStatus
    transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    consume_task: asyncio.Task[None] | None = None
    ready_task: asyncio.Task[None] | None = None
    closed_task: asyncio.Task[None] | None = None
    stop_task: asyncio.Task | None = None
    pause_task: asyncio.Task | None = None
    capture_draining: bool = False
    parse_failures: int = 0
    dropped_while_paused: int = 0
    dropped_on_db_error: int = 0
    buffered_events: int = 0  # 消费循环内未落库数（诊断用）
    metadata: dict[str, Any] = field(default_factory=dict)


class RecorderSessionService:
    """录制会话编排：控制状态机 + 事件落库 + 固化触发。"""

    def __init__(
        self,
        settings: Settings,
        store: PostgresRecordingStore,
        graph_store: RecordingGraphStore,
        *,
        registry: DriverRegistry | None = None,
        bridge: EmbeddedBridge | None = None,
        finish_config_service=None,
        capture_config_service=None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._graph_store = graph_store
        self._bridge = bridge or EmbeddedBridge()
        self._registry = registry or build_default_registry(self._bridge, settings=settings)
        self._runtimes: dict[str, _SessionRuntime] = {}
        self._launches: set[asyncio.Task] = set()
        self._closed = False
        self._shutdown_task: asyncio.Task | None = None
        self._finish_configs = finish_config_service
        self._capture_configs = capture_config_service

    # ------------------------------------------------------------ 查询

    def runtime_status(self, recording_id: str) -> RecordingStatus | None:
        runtime = self._runtimes.get(recording_id)
        return runtime.status if runtime else None

    def runtime_stats(self, recording_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(recording_id)
        if runtime is None:
            return {}
        return {
            "status": runtime.status.value,
            "dropped_while_paused": runtime.dropped_while_paused,
            "dropped_on_db_error": runtime.dropped_on_db_error,
            "buffered_events": runtime.buffered_events,
        }

    async def get_session(self, recording_id: str) -> RecordingSession | None:
        return await self._store.get_session(recording_id)

    async def list_sessions(
        self, project_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[RecordingSession]:
        return await self._store.list_sessions(project_id, limit, offset)

    async def count_sessions(self, project_id: str | None = None) -> int:
        return await self._store.count_sessions(project_id)

    async def get_events(self, recording_id: str) -> list[RecorderEvent]:
        return await self._store.get_events(recording_id)

    # ------------------------------------------------------------ launch

    async def launch(
        self,
        request: RecordingCreateRequest,
        *,
        ready_timeout: float = _LAUNCH_READY_TIMEOUT,
    ) -> RecordingSession:
        if self._closed:
            raise RuntimeError("Recorder service is shutting down")
        operation = asyncio.create_task(self._launch(request, ready_timeout=ready_timeout))
        self._launches.add(operation)
        try:
            return await operation
        finally:
            self._launches.discard(operation)

    async def _launch(self, request: RecordingCreateRequest, *, ready_timeout: float) -> RecordingSession:
        """创建会话：PG 落 launching → 驱动 open(navigate 指令) → 后台等登记。"""
        # kind 解析兼容 enum 与裸 str（绕过 pydantic 校验的直赋值）
        kind_raw = request.driver.kind
        kind = kind_raw.value if hasattr(kind_raw, "value") else str(kind_raw)
        if not self._registry.is_registered(kind):
            raise ValueError(
                f"driver kind not available: {kind} "
                f"(registered: {', '.join(self._registry.kinds())})"
            )

        finish_settings = (await self._finish_configs.capture(request.project_id)
            if self._finish_configs is not None else {"scope_key": "internal_default", "revision": 0,
                "payload": RecordingFinishSettings().model_dump(mode="json")})
        session = RecordingSession(
            project_id=request.project_id,
            name=request.name,
            entry_url=request.entry_url,
            driver_kind=request.driver.kind,
            status=RecordingStatus.launching,
            session_id=request.session_id,
            approval_id=request.approval_id,
            metadata={key: value for key, value in request.metadata.items() if key not in (
                "browser_allocation_id", "browser_lease_namespace", "browser_host_id")},
        )
        session.metadata["finish_settings"] = finish_settings
        # No policy on a historical/internal session means the legacy recorder.
        session.metadata.pop("capture_settings", None)
        if self._capture_configs is not None:
            session.metadata["capture_settings"] = await self._capture_configs.capture(request.project_id)
        driver = self._registry.create(request.driver, recording_id=session.id)
        try:
            if "capture_settings" in session.metadata:
                driver.configure_capture(session.metadata["capture_settings"]["payload"])
            session.metadata.update(driver.launch_metadata())
            created = await self._store.create_session(session)
        except BaseException:
            logger.exception("recording_session_creation_failed", extra={"recording_id": session.id})
            try:
                await driver.close()
            except Exception:
                logger.exception("recording_unstarted_driver_close_failed", extra={"recording_id": session.id})
            finally:
                # No open/navigate command has been sent, so no client needs a
                # delayed close receipt for this failed launch.
                if driver.kind == "embedded":
                    self._bridge.detach(session.id)
            raise

        runtime = _SessionRuntime(session=created, driver=driver, status=RecordingStatus.launching)
        self._runtimes[created.id] = runtime

        try:
            # Standalone recordings own their recording session ID; Agent-created
            # recordings retain the originating Session/Turn/Attempt context.
            with bind_request_context(project_id=created.project_id, session_id=created.session_id or created.id):
                await driver.open(created.entry_url, viewport=request.driver.viewport)
                await driver.inject_recorder()
        except BaseException:
            logger.exception("recording launch failed on driver open: recording_id=%s", created.id)
            try:
                await self._shutdown_driver(runtime)
            finally:
                await self._update(runtime, RecordingStatus.failed)
            raise

        runtime.ready_task = asyncio.create_task(
            self._await_ready(runtime, timeout=ready_timeout),
            name=f"recording-ready-{created.id}",
        )
        if callable(getattr(driver, "wait_closed", None)):
            runtime.closed_task = asyncio.create_task(self._watch_driver_closed(runtime),
                name=f"recording-closed-{created.id}")
        logger.info(
            "recording launched: recording_id=%s project_id=%s url=%s driver=%s",
            created.id, created.project_id, created.entry_url, created.driver_kind.value,
        )
        return created

    async def _watch_driver_closed(self, runtime: _SessionRuntime) -> None:
        try:
            await runtime.driver.wait_closed()
            if runtime.status in (RecordingStatus.launching, RecordingStatus.ready,
                                  RecordingStatus.active, RecordingStatus.paused):
                await self._update(runtime, RecordingStatus.failed,
                    finalize_metrics={"failure_reason": "browser_closed"})
                await self._cancel_task(runtime.consume_task)
                await self._cancel_task(runtime.ready_task)
                await self._shutdown_driver(runtime)
                logger.warning("recording_browser_closed", extra={"recording_id": runtime.session.id})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("recording_browser_close_recovery_failed", extra={"recording_id": runtime.session.id})

    async def close(self) -> None:
        """Stop all owned recording drivers before the shared Harness shuts down."""
        self._closed = True
        if self._shutdown_task is None or (self._shutdown_task.done() and self._shutdown_task.exception() is not None):
            self._shutdown_task = asyncio.create_task(self._close())
        await asyncio.shield(self._shutdown_task)

    async def _close(self) -> None:
        launches = list(self._launches)
        for task in launches:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*launches, return_exceptions=True)

        async def close_runtime(runtime):
            if runtime.pause_task is not None and not runtime.pause_task.done():
                await asyncio.shield(runtime.pause_task)
            if runtime.stop_task is not None and not runtime.stop_task.done():
                await asyncio.shield(runtime.stop_task)
            await self._cancel_task(runtime.closed_task)
            await self._cancel_task(runtime.ready_task)
            await self._cancel_task(runtime.consume_task)
            await self._shutdown_driver(runtime)
            if runtime.status not in (RecordingStatus.completed, RecordingStatus.failed, RecordingStatus.discarded):
                await self._update(runtime, RecordingStatus.failed,
                    finalize_metrics={"failure_reason": "server_shutdown"})

        results = await asyncio.gather(*(close_runtime(runtime) for runtime in self._runtimes.values()),
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _await_ready(self, runtime: _SessionRuntime, timeout: float) -> None:
        """后台等待驱动就绪（embedded = Electron 登记）→ ready；超时 → failed。"""
        recording_id = runtime.session.id
        try:
            ready = await runtime.driver.wait_ready(timeout=timeout)
        except Exception:
            logger.exception("recording wait_ready error: recording_id=%s", recording_id)
            ready = False
        if runtime.status != RecordingStatus.launching:
            return  # 期间已被 destroy/stop，保持其目标状态
        if ready:
            await self._update(runtime, RecordingStatus.ready)
            logger.info("recording ready: recording_id=%s", recording_id)
        else:
            logger.error("recording launch timeout, marking failed: recording_id=%s", recording_id)
            try:
                await self._update(runtime, RecordingStatus.failed)
            finally:
                await self._shutdown_driver(runtime)

    # ------------------------------------------------------------ control

    async def control(
        self, recording_id: str, action: RecordingControlAction
    ) -> RecordingSession:
        """控制条指令统一入口：迁移校验 → 内存占位（防并发重复迁移）→ 驱动动作
        → PG 持久化；动作异常时回滚内存状态。"""
        runtime = self._require_runtime(recording_id)
        async with runtime.transition_lock:
            if runtime.pause_task is not None and not runtime.pause_task.done():
                logger.warning("recording_capture_control_in_progress", extra={
                    "recording_id": recording_id, "action": action.value})
                raise ValueError("Recording capture control in progress")
            current = runtime.status
            allowed = _CONTROL_TRANSITIONS[action]
            target = allowed.get(current)
            if target is None:
                logger.warning(
                    "illegal recording control transition rejected: recording_id=%s action=%s current=%s",
                    recording_id, action.value, current.value,
                )
                raise ValueError(
                    f"illegal transition: action={action.value} current={current.value}"
                )

            runtime.status = target  # 占位与 PG 回填串行；驱动操作仍在锁外。
        try:
            if action is RecordingControlAction.start:
                return await self._do_start(runtime)
            if action is RecordingControlAction.pause:
                runtime.capture_draining = True
                runtime.pause_task = asyncio.create_task(self._do_pause(runtime),
                    name=f"recording-pause-{recording_id}")
                return await asyncio.shield(runtime.pause_task)
            if action is RecordingControlAction.resume:
                return await self._do_resume(runtime)
            if action is RecordingControlAction.stop:
                runtime.stop_task = asyncio.create_task(self._do_stop(runtime),
                    name=f"recording-stop-{recording_id}")
                return await asyncio.shield(runtime.stop_task)
            return await self._do_destroy(runtime)
        except Exception:
            runtime.status = runtime.session.status  # 保留数据库返回的恢复终态，禁止迟到操作复活。
            raise

    async def _do_start(self, runtime: _SessionRuntime) -> RecordingSession:
        await runtime.driver.set_capture_enabled(True)
        result = await self._update(runtime, RecordingStatus.active, started_at=True)
        runtime.consume_task = asyncio.create_task(
            self._consume_loop(runtime), name=f"recording-consume-{runtime.session.id}"
        )
        logger.info("recording started: recording_id=%s", runtime.session.id)
        return result

    async def _do_pause(self, runtime: _SessionRuntime) -> RecordingSession:
        receipt = None
        try:
            policy = RecordingFinishSettings.model_validate(runtime.session.metadata["finish_settings"]["payload"])
            async with asyncio.timeout(policy.timeout_seconds):
                receipt = await runtime.driver.pause_capture()
            events = await self._store.get_events(runtime.session.id)
            self._verify_capture_receipt(runtime, receipt, events, operation="pause")
            metrics = dict(runtime.session.finalize_metrics)
            metrics["pause_receipts"] = [*metrics.get("pause_receipts", []), receipt]
            result = await self._update(runtime, RecordingStatus.paused, finalize_metrics=metrics)
            logger.info("recording_capture_paused", extra={
                "recording_id": runtime.session.id, "request_id": receipt.get("request_id"),
                "next_seq": receipt["next_seq"]})
            return result
        except Exception as exc:
            return await self._capture_failed(runtime, exc, receipt, operation="pause")
        finally:
            runtime.capture_draining = False

    async def _do_resume(self, runtime: _SessionRuntime) -> RecordingSession:
        await runtime.driver.set_capture_enabled(True)
        result = await self._update(runtime, RecordingStatus.active)
        logger.info("recording resumed: recording_id=%s", runtime.session.id)
        return result

    async def _do_stop(self, runtime: _SessionRuntime) -> RecordingSession:
        """停止并固化：finalizing → (事件 flush + Memgraph finalize) → completed。"""
        recording_id = runtime.session.id
        await self._update(runtime, RecordingStatus.finalizing)
        finish = None
        try:
            policy = RecordingFinishSettings.model_validate(runtime.session.metadata["finish_settings"]["payload"])
            if runtime.consume_task is None:
                runtime.consume_task = asyncio.create_task(self._consume_loop(runtime))
            async with asyncio.timeout(policy.timeout_seconds):
                finish = await runtime.driver.finish_capture()
                await asyncio.shield(runtime.consume_task)
            await self._cancel_task(runtime.ready_task)
            events = await self._store.get_events(recording_id)
            self._verify_capture_receipt(runtime, finish, events, operation="stop")
            result = await self._graph_store.finalize(runtime.session, events)
            metrics = {
                "finalize": result.get("metrics", {}),
                "integrity": result.get("integrity", {}),
                "finish_receipt": finish,
                "pause_receipts": runtime.session.finalize_metrics.get("pause_receipts", []),
            }
            if result.get("status") != "success":
                raise RuntimeError(f"graph finalize failed: {result.get('reason') or result.get('status')}")
        except Exception as exc:
            return await self._capture_failed(runtime, exc, finish, operation="stop")

        logger.info(
            "recording finalized: recording_id=%s actions=%s",
            recording_id, metrics.get("finalize", {}).get("action_vertices"),
        )
        completed = await self._update(
            runtime, RecordingStatus.completed, finalize_metrics=metrics
        )
        await self._shutdown_driver(runtime)
        return completed

    @staticmethod
    def _verify_capture_receipt(runtime, receipt, events, *, operation):
        expected, saved = set(receipt["published_seqs"]), {event.seq for event in events}
        missing = sorted(expected - saved)
        receipt["missing_seqs"] = missing
        browser_start = receipt["next_seq"] - receipt["emitted_count"]
        browser_saved_count = sum(browser_start <= seq < receipt["next_seq"] for seq in saved)
        receipt["browser_saved_count"] = browser_saved_count
        watermark_missing = browser_start < 0 or browser_saved_count != receipt["emitted_count"]
        receipt["pg_verified"] = not missing and not watermark_missing
        if receipt["protocol"] == "recording-scopes-v2":
            documents = [scope["document_id"] for scope in receipt["scopes"]]
            if not documents or len(documents) != len(set(documents)) or any(
                event.page.get("recording_scope", {}).get("document_id") not in documents for event in events):
                raise RuntimeError("Recording document scope coverage could not be confirmed")
            for scope in receipt["scopes"]:
                actual = [event.page.get("recording_scope", {}) for event in events
                    if event.page.get("recording_scope", {}).get("document_id") == scope["document_id"]]
                expected_local = set(range(scope["next_seq"])) if isinstance(scope["next_seq"], int) else None
                scope["pg_verified"] = bool(scope["confirmed"] and expected_local is not None
                    and {row.get("local_seq") for row in actual} == expected_local and len(actual) == len(expected_local)
                    and all(all(row.get(key) == scope.get(key) for key in (
                        "page_id", "frame_id", "parent_frame_id", "frame_path", "document_url")) for row in actual))
                if not scope["pg_verified"]:
                    raise RuntimeError("Recording document local delivery could not be confirmed")
        if (receipt["protocol"] not in ("recording-finish-v1", "recording-scopes-v2") or receipt.get("operation", "stop") != operation or
            receipt["pending"] or receipt["frames_unconfirmed"] or receipt["transport_dropped"] or
            receipt["channel_dropped"] or missing or watermark_missing or runtime.parse_failures or
            runtime.dropped_on_db_error):
            raise RuntimeError("Recording capture delivery/integrity could not be confirmed")

    async def _capture_failed(self, runtime, exc, receipt, *, operation):
        logger.exception("recording_capture_checkpoint_failed", extra={
            "recording_id": runtime.session.id, "operation": operation})
        error = f"{type(exc).__name__}: {exc}"
        runtime.metadata["finalize_error"] = error
        await self._cancel_task(runtime.consume_task)
        await self._cancel_task(runtime.ready_task)
        prefix = "finish" if operation == "stop" else "pause"
        failed = await self._update(runtime, RecordingStatus.failed, finalize_metrics={
            **runtime.session.finalize_metrics, "finalize_error": error,
            "failure_reason": f"{prefix}_timeout" if isinstance(exc, TimeoutError) else f"{prefix}_unconfirmed",
            f"{prefix}_receipt": receipt, "parse_failures": runtime.parse_failures,
            "dropped_on_db_error": runtime.dropped_on_db_error})
        await self._shutdown_driver(runtime)
        return failed

    async def _do_destroy(self, runtime: _SessionRuntime) -> RecordingSession:
        """销毁：丢弃未固化数据，关驱动，PG 标 discarded（审计保留）。"""
        recording_id = runtime.session.id
        await self._cancel_task(runtime.consume_task)  # 丢弃不冲刷：destroy 已占位 discarded
        await self._cancel_task(runtime.ready_task)
        runtime.consume_task = None
        runtime.ready_task = None
        await self._shutdown_driver(runtime)
        discarded = await self._store.discard_session(recording_id)
        if discarded is not None:
            runtime.session = discarded
            runtime.status = discarded.status
        logger.info(
            "recording destroyed: recording_id=%s dropped_while_paused=%s dropped_on_db_error=%s",
            recording_id, runtime.dropped_while_paused, runtime.dropped_on_db_error,
        )
        return runtime.session

    # ------------------------------------------------------------ 事件消费循环

    async def _consume_loop(self, runtime: _SessionRuntime) -> None:
        """驱动事件流 → 攒批（20 条 / 0.5s）→ PG 落库（重试 3 次）。"""
        recording_id = runtime.session.id
        stream = await runtime.driver.on_recorder_event()
        buffer: list[RecorderEvent] = []
        try:
            while True:
                event = None
                try:
                    event = await asyncio.wait_for(
                        stream.__anext__(), timeout=_CONSUME_FLUSH_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # wait_for 超时取消会终止 async generator（CancelledError
                    # 穿透 iterate 协程）；重建迭代器继续消费——EventChannel
                    # 的队列在 channel 实例上，重建不丢未消费事件。
                    stream = await runtime.driver.on_recorder_event()
                except StopAsyncIteration:
                    break

                if event is not None:
                    if isinstance(event, CaptureCheckpoint):
                        await self._flush_buffer(runtime, buffer)
                        if not event.completed.done():
                            event.completed.set_result(None)
                        continue
                    if runtime.status not in (RecordingStatus.active, RecordingStatus.finalizing) and not runtime.capture_draining:
                        runtime.dropped_while_paused += 1
                        logger.warning(
                            "recorder event dropped (not active): recording_id=%s status=%s seq=%s",
                            recording_id, runtime.status.value, event.get("seq"),
                        )
                        continue
                    try:
                        buffer.append(RecorderEvent.model_validate(event))
                    except Exception:
                        runtime.parse_failures += 1
                        logger.exception(
                            "recorder event parse failed: recording_id=%s payload=%s",
                            recording_id, str(event)[:200],
                        )
                        continue

                if buffer and (len(buffer) >= _CONSUME_BATCH_SIZE or event is None):
                    await self._flush_buffer(runtime, buffer)
            if buffer:
                await self._flush_buffer(runtime, buffer)
        except asyncio.CancelledError:
            # 停止（finalizing/completed）前冲刷已收事件；destroy 已占位
            # discarded，丢弃 buffer 不落库（保留审计语义：销毁不产生数据）。
            if buffer and runtime.status is not RecordingStatus.discarded:
                await self._flush_buffer(runtime, buffer)
            raise

    async def _flush_buffer(self, runtime: _SessionRuntime, buffer: list[RecorderEvent]) -> None:
        if not buffer:
            return
        runtime.buffered_events = len(buffer)
        batch, buffer[:] = buffer[:], []
        recording_id = runtime.session.id
        for attempt, delay in enumerate(_DB_RETRY_DELAYS, start=1):
            try:
                ack = await self._store.append_events(recording_id, batch)
                runtime.buffered_events = 0
                logger.debug(
                    "recording events flushed: recording_id=%s accepted=%s duplicates=%s",
                    recording_id, ack.accepted, ack.duplicates,
                )
                return
            except Exception:
                logger.exception(
                    "recording event flush failed (attempt %s/%s): recording_id=%s count=%s",
                    attempt, len(_DB_RETRY_DELAYS), recording_id, len(batch),
                )
                if attempt < len(_DB_RETRY_DELAYS):
                    await asyncio.sleep(delay)
        runtime.dropped_on_db_error += len(batch)
        logger.error(
            "recording events dropped after retries: recording_id=%s count=%s total_dropped=%s",
            recording_id, len(batch), runtime.dropped_on_db_error,
        )

    # ------------------------------------------------------------ 内部工具

    def _require_runtime(self, recording_id: str) -> _SessionRuntime:
        runtime = self._runtimes.get(recording_id)
        if runtime is None:
            raise ValueError(
                f"recording runtime not available: {recording_id} "
                "(unknown id or server restarted)"
            )
        return runtime

    async def _update(
        self,
        runtime: _SessionRuntime,
        status: RecordingStatus,
        *,
        started_at: bool = False,
        finalize_metrics: dict[str, Any] | None = None,
    ) -> RecordingSession:
        """内存态 + PG 双写；PG 失败抛出（调用方决定后续）。"""
        async with runtime.transition_lock:
            updated = await self._store.update_status(
                runtime.session.id,
                status,
                started_at=datetime.now(timezone.utc) if started_at else None,
                ended_at=datetime.now(timezone.utc)
                if status in (RecordingStatus.completed, RecordingStatus.discarded, RecordingStatus.failed)
                else None,
                finalize_metrics=finalize_metrics,
            )
            runtime.status = updated.status if updated is not None else status
            if updated is not None:
                runtime.session = updated
                if updated.status != status:
                    logger.warning("recording_state_update_rejected", extra={
                        "recording_id": runtime.session.id, "requested_status": status.value,
                        "actual_status": updated.status.value,
                    })
                    raise ValueError(f"Recording is already {updated.status.value}; cannot change to {status.value}")
            return runtime.session

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # consume 循环内部已处理落库错误；此处兜底记录非预期异常
            logger.exception("recording background task error during cancel")

    async def _shutdown_driver(self, runtime: _SessionRuntime) -> None:
        """关驱动（embedded → close 指令）并延迟 detach（等 Electron 拉取指令）。"""
        recording_id = runtime.session.id
        if runtime.closed_task is not asyncio.current_task():
            await self._cancel_task(runtime.closed_task)
        try:
            await runtime.driver.close()
        except Exception:
            logger.exception("recording driver close error: recording_id=%s", recording_id)
        if runtime.driver.kind == "embedded" and hasattr(self._bridge, "detach"):
            asyncio.create_task(
                self._delayed_detach(recording_id),
                name=f"recording-detach-{recording_id}",
            )

    async def _delayed_detach(self, recording_id: str) -> None:
        await asyncio.sleep(_DETACH_DELAY_SECONDS)
        detached = self._bridge.detach(recording_id)
        if detached:
            logger.info("recording bridge detached: recording_id=%s", recording_id)
