"""Keep the official checkpoint write and application owner lock in one transaction.

No upstream counterpart (checked: PostgresSaver 3.1.2 and integration plan §6):
LangGraph does not validate our Session lease. Serialization/pipeline/delta
storage are delegated to the pinned official saver on the SAME connection.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager, closing

from langgraph.checkpoint.postgres import PostgresSaver

from src.application.deep_agents.threaded_postgres_saver import ThreadedPostgresSaver
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.store import ContinuationLeaseLostError


logger = logging.getLogger(__name__)


class FencedPostgresSaver(ThreadedPostgresSaver):
    def __init__(self, pool, *, settings, owner, serde=None):
        super().__init__(pool, serde=serde)
        self.runtime_owner = owner  # Immutable snapshot, never read a new token from context.
        self._settings = settings

    def _storage_config(self, config):
        configurable = dict((config or {}).get("configurable", {}))
        binding = self.runtime_owner.binding
        if configurable.get("thread_id", binding.thread_id) != binding.thread_id:
            raise ValueError("Checkpoint thread does not match the runtime owner")
        namespace = configurable.get("checkpoint_ns", "")
        # LangGraph 1.2.12 _loop resets a top-level checkpoint_ns to ''. Prefix
        # physical storage, keeping the graph's logical root/nested paths intact.
        configurable.update(thread_id=binding.thread_id, checkpoint_ns=(
            binding.checkpoint_namespace + ("|" + namespace if namespace else "")))
        return {**(config or {}), "configurable": configurable}

    def _graph_config(self, config):
        if config is None:
            return None
        configurable = dict(config["configurable"])
        prefix = self.runtime_owner.binding.checkpoint_namespace
        namespace = configurable["checkpoint_ns"]
        if namespace != prefix and not namespace.startswith(prefix + "|"):
            raise ValueError("Stored checkpoint namespace is outside the runtime binding")
        configurable["checkpoint_ns"] = namespace[len(prefix) + 1:] if namespace != prefix else ""
        return {**config, "configurable": configurable}

    def _graph_tuple(self, value):
        return value._replace(config=self._graph_config(value.config),
            parent_config=self._graph_config(value.parent_config)) if value is not None else None

    @contextmanager
    def _write_transaction(self, operation):
        owner = self.runtime_owner
        try:
            with self.conn.connection() as connection, connection.transaction():
                with connection.cursor() as cur:
                    configure_runtime_owner_transaction(cur, self._settings)
                    assert_runtime_owner(cur, self._settings, owner, schema="public.")
                yield PostgresSaver(connection, serde=self.serde)
        except Exception as exc:
            logger.warning("framework_checkpoint_write_rejected" if isinstance(exc, ContinuationLeaseLostError)
                else "framework_checkpoint_write_failed", extra={
                    "operation": operation, "binding_id": owner.binding.binding_id,
                    "owner_epoch": owner.owner_epoch, "attempt_id": owner.attempt_id,
                    "worker_id": owner.worker_id, "error_type": type(exc).__name__,
                })
            raise

    def put(self, config, checkpoint, metadata, new_versions):
        storage_config = self._storage_config(config)
        with self._write_transaction("put") as saver:
            result = saver.put(storage_config, checkpoint, metadata, new_versions)
        return self._graph_config(result)

    def put_writes(self, config, writes, task_id, task_path=""):
        storage_config = self._storage_config(config)
        with self._write_transaction("put_writes") as saver:
            saver.put_writes(storage_config, writes, task_id, task_path)

    def get_tuple(self, config):
        return self._graph_tuple(PostgresSaver(self.conn, serde=self.serde).get_tuple(self._storage_config(config)))

    def list(self, config, *, filter=None, before=None, limit=None):
        storage_config = self._storage_config(config)
        all_namespaces = "checkpoint_ns" not in (config or {}).get("configurable", {})
        if all_namespaces:
            storage_config["configurable"].pop("checkpoint_ns")
        if limit == 0:
            return
        count = 0
        with closing(PostgresSaver(self.conn, serde=self.serde).list(
            storage_config, filter=filter,
            before=self._storage_config(before) if before is not None else None,
            limit=None if all_namespaces else limit)) as iterator:
            for value in iterator:
                namespace = value.config["configurable"]["checkpoint_ns"]
                prefix = self.runtime_owner.binding.checkpoint_namespace
                if namespace != prefix and not namespace.startswith(prefix + "|"):
                    continue
                yield self._graph_tuple(value)
                count += 1
                if limit is not None and count >= limit:
                    return

    def get_delta_channel_history(self, *, config, channels):
        return PostgresSaver(self.conn, serde=self.serde).get_delta_channel_history(
            config=self._storage_config(config), channels=channels)

    async def aget_delta_channel_history(self, *, config, channels):
        return await asyncio.to_thread(self.get_delta_channel_history, config=config, channels=channels)

    def setup(self):
        raise PermissionError("Checkpoint setup belongs to deployment initialization")

    def delete_thread(self, thread_id):
        raise PermissionError("Runtime owners cannot delete checkpoint threads")
