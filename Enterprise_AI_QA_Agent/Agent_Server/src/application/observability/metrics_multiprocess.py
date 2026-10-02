"""Atomic process snapshots for the project's removable-label metric registry.

No upstream counterpart (checked: metrics.py, api_doc_store.py atomic writes,
Prometheus client_python multiprocess docs): the upstream collector cannot clear
labels; our existing global gauge snapshots must retain that contract.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import psutil

logger = logging.getLogger(__name__)
SNAPSHOT_VERSION = 1
PUBLISH_INTERVAL_SECONDS = 1.0


class MetricSnapshotError(RuntimeError):
    """A shared snapshot is unreadable or conflicts with the registry contract."""


class ProcessSnapshots:
    def __init__(self, directory: str | Path, snapshot: Callable[[], list[dict[str, Any]]]) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pid = os.getpid()
        self.created_at = psutil.Process(self.pid).create_time()
        self._path = self.directory / f"metrics-{self.pid}-{uuid4().hex}.json"
        self._snapshot = snapshot
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self.publish()
        self._thread = threading.Thread(target=self._loop, name="metrics-publisher", daemon=True)
        self._thread.start()
        atexit.register(self.close)
        logger.info("metrics_multiprocess_started", extra={"interval_seconds": PUBLISH_INTERVAL_SECONDS})

    def publish(self, *, active: bool = True) -> None:
        if os.getpid() != self.pid:
            raise MetricSnapshotError("Metrics publishers require independently spawned workers.")
        with self._write_lock:
            payload = {
                "version": SNAPSHOT_VERSION,
                "pid": self.pid,
                "created_at": self.created_at,
                "active": active and not self._stop.is_set(),
                "metrics": self._snapshot(),
            }
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.directory, prefix=".metrics-", suffix=".tmp", delete=False,
                ) as output:
                    temporary = Path(output.name)
                    json.dump(payload, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self._path)
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning("metrics_snapshot_temp_cleanup_failed", extra={"error_type": type(exc).__name__})

    def _loop(self) -> None:
        while not self._stop.wait(PUBLISH_INTERVAL_SECONDS):
            try:
                self.publish()
            except (OSError, MetricSnapshotError) as exc:
                logger.error("metrics_snapshot_publish_failed", extra={"error_type": type(exc).__name__})

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            logger.error("metrics_publisher_stop_timeout")
            return
        try:
            self.publish(active=False)
        except (OSError, MetricSnapshotError) as exc:
            logger.error("metrics_snapshot_retire_failed", extra={"error_type": type(exc).__name__})
        logger.info("metrics_multiprocess_stopped")

    def read(self) -> list[tuple[bool, list[dict[str, Any]]]]:
        result = []
        alive: dict[tuple[int, float], bool] = {}
        for path in sorted(self.directory.glob("metrics-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if (payload["version"] != SNAPSHOT_VERSION or not isinstance(payload["metrics"], list)
                        or type(payload["active"]) is not bool or type(payload["pid"]) is not int
                        or payload["pid"] <= 0):
                    raise ValueError("Unsupported metric snapshot format.")
                identity = (int(payload["pid"]), float(payload["created_at"]))
                if identity not in alive:
                    try:
                        process = psutil.Process(identity[0])
                        alive[identity] = process.create_time() == identity[1] and process.status() != psutil.STATUS_ZOMBIE
                    except psutil.NoSuchProcess:
                        alive[identity] = False
                    except psutil.AccessDenied as exc:
                        # Do not silently discard a live worker when OS inspection is denied.
                        raise MetricSnapshotError("Worker liveness cannot be determined.") from exc
                result.append((payload["active"] and alive[identity], payload["metrics"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise MetricSnapshotError("Shared metric snapshot is invalid.") from exc
        return result
