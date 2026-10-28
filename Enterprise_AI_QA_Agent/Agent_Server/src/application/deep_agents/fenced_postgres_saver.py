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
from src.application.deep_agents.path_ordered_postgres_saver import PathOrderedPostgresSaver
from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.framework_approval_journal import FrameworkApprovalJournal
from src.schemas.runtime_binding import RuntimeCheckpointSource


logger = logging.getLogger(__name__)


class BoundPostgresReader(ThreadedPostgresSaver):
    """Share official reads and physical namespace mapping, never owner authority."""

    def __init__(self, pool, *, source, serde=None):
        super().__init__(pool, serde=serde)
        self.runtime_source = RuntimeCheckpointSource.model_validate(source)

    def _storage_config(self, config):
        configurable = dict((config or {}).get("configurable", {}))
        binding = self.runtime_source.binding
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
        prefix = self.runtime_source.binding.checkpoint_namespace
        namespace = configurable["checkpoint_ns"]
        if namespace != prefix and not namespace.startswith(prefix + "|"):
            raise ValueError("Stored checkpoint namespace is outside the runtime binding")
        configurable["checkpoint_ns"] = namespace[len(prefix) + 1:] if namespace != prefix else ""
        return {**config, "configurable": configurable}

    def _graph_tuple(self, value):
        return value._replace(config=self._graph_config(value.config),
            parent_config=self._graph_config(value.parent_config)) if value is not None else None

    def put(self, config, checkpoint, metadata, new_versions):
        self._reject_write("put")

    def put_writes(self, config, writes, task_id, task_path=""):
        self._reject_write("put_writes")

    def get_tuple(self, config):
        return self._graph_tuple(PathOrderedPostgresSaver(self.conn, serde=self.serde).get_tuple(self._storage_config(config)))

    def get_interrupt_task(self, config, task_id):
        self._reject_write("get_interrupt_task")

    async def aget_interrupt_task(self, config, task_id):
        return await asyncio.to_thread(self.get_interrupt_task, config, task_id)

    def list(self, config, *, filter=None, before=None, limit=None):
        storage_config = self._storage_config(config)
        all_namespaces = "checkpoint_ns" not in (config or {}).get("configurable", {})
        if all_namespaces:
            storage_config["configurable"].pop("checkpoint_ns")
        if limit == 0:
            return
        count = 0
        with closing(PathOrderedPostgresSaver(self.conn, serde=self.serde).list(
            storage_config, filter=filter,
            before=self._storage_config(before) if before is not None else None,
            limit=None if all_namespaces else limit)) as iterator:
            for value in iterator:
                namespace = value.config["configurable"]["checkpoint_ns"]
                prefix = self.runtime_source.binding.checkpoint_namespace
                if namespace != prefix and not namespace.startswith(prefix + "|"):
                    continue
                yield self._graph_tuple(value)
                count += 1
                if limit is not None and count >= limit:
                    return

    def get_delta_channel_history(self, *, config, channels):
        return PathOrderedPostgresSaver(self.conn, serde=self.serde).get_delta_channel_history(
            config=self._storage_config(config), channels=channels)

    async def aget_delta_channel_history(self, *, config, channels):
        return await asyncio.to_thread(self.get_delta_channel_history, config=config, channels=channels)

    def setup(self):
        raise PermissionError("Checkpoint setup belongs to deployment initialization")

    def delete_thread(self, thread_id):
        raise PermissionError("Runtime owners cannot delete checkpoint threads")

    def _reject_write(self, operation):
        logger.warning("framework_checkpoint_readonly_write_rejected", extra={"operation": operation,
            "binding_id": self.runtime_source.binding.binding_id, "task_id": self.runtime_source.task_id})
        raise PermissionError("A source-only checkpoint reader cannot write or consume approvals")


class FencedPostgresSaver(BoundPostgresReader):
    def __init__(self, pool, *, settings, owner, serde=None, event_prefix=None):
        super().__init__(pool, source=owner.model_dump(exclude={"owner_token"}), serde=serde)
        self.runtime_owner = owner  # Immutable snapshot, never read a new token from context.
        self._settings = settings
        self._approval_journal = FrameworkApprovalJournal(settings, serde=self.serde)
        self._event_prefix = event_prefix

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
        from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY, checkpoint_event_prefix

        # Only this captured writer may attach the server receipt. Never carry
        # caller/config/ancestor metadata forward as a receipt for a new write.
        discarded_sources = []
        for name in ("metadata", "configurable"):
            if CHECKPOINT_PREFIX_KEY in (config.get(name) or {}):
                discarded_sources.append(name)
        if CHECKPOINT_PREFIX_KEY in (metadata or {}):
            discarded_sources.append("checkpoint_metadata")
        if discarded_sources:
            logger.debug("framework_checkpoint_event_prefix_metadata_discarded", extra={
                "binding_id": self.runtime_owner.binding.binding_id,
                "task_id": self.runtime_owner.task_id,
                "metadata_sources": tuple(discarded_sources),
            })
        config = {**config, **{name: {key: value for key, value in (config.get(name) or {}).items()
            if key != CHECKPOINT_PREFIX_KEY} for name in ("metadata", "configurable")}}
        metadata = {key: value for key, value in metadata.items() if key != CHECKPOINT_PREFIX_KEY}
        storage_config = self._storage_config(config)
        with self._write_transaction("put") as saver:
            captured = (self._event_prefix() if self._event_prefix is not None
                and not config["configurable"].get("checkpoint_ns") else None)
            result = saver.put(storage_config, checkpoint, metadata, new_versions)
            if captured is not None:
                # Use the stored official codec: JSONB map order and blob
                # reconstruction differ from the in-memory checkpoint input.
                # Both official puts and the ACK audit are in this SAME owner
                # transaction; any failure rolls back the first put as well.
                original = saver.get_tuple(result)
                with saver.conn.cursor() as cur:
                    prefix = checkpoint_event_prefix(cur, self._settings, self.runtime_owner,
                        *captured, checkpoint=original, serde=self.serde)
                saver.put(storage_config, checkpoint, {**metadata, CHECKPOINT_PREFIX_KEY: prefix.model_dump(mode="json")}, {})
                logger.debug("framework_checkpoint_event_prefix_bound", extra={
                    "binding_id": self.runtime_owner.binding.binding_id, "checkpoint_id": checkpoint["id"],
                    "task_id": self.runtime_owner.task_id, "event_count": len(prefix.events)})
        return self._graph_config(result)

    def put_writes(self, config, writes, task_id, task_path=""):
        storage_config = self._storage_config(config)
        with self._write_transaction("put_writes") as saver:
            with saver.conn.cursor() as cur:
                resume = self._approval_journal.prepare_resume_in_transaction(cur, self.runtime_owner,
                    storage_config, writes, task_id)
            saver.put_writes(storage_config, writes, task_id, task_path)
            if resume is not None:
                saver.put_writes(storage_config, [("__resume__", resume)], task_id, task_path)
            with saver.conn.cursor() as cur:
                self._approval_journal.record_delivery_in_transaction(cur, self.runtime_owner,
                    storage_config, [("__resume__", resume)] if resume is not None else writes, task_id, task_path)

    def get_interrupt_task(self, config, task_id):
        storage_config = self._storage_config(config)
        with self._write_transaction("read_interrupt_task") as saver, saver.conn.cursor() as cur:
            return self._approval_journal.pending_task_in_transaction(
                cur, self.runtime_owner.binding, storage_config, task_id)
