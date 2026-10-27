"""Pinned Postgres read compatibility for upstream langgraph issue #8382.

Reference: https://github.com/langchain-ai/langgraph/pull/8544
Live writes use task-path order, not hashed task-ID order. Postgres 3.1.2
already stores paths, but drops them on read. Keep official serialization,
storage, and the public BaseCheckpointSaver ancestor walk; only correct the
pending-write ordering. No application-side Delta reducer or schema migration.

The default ancestor walk costs more queries than the pinned optimized SQL.
On a separately approved dependency upgrade, run the replay/conformance suite
and remove this compatibility class once upstream supplies the same contract.
"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver


class PathOrderedPostgresSaver(PostgresSaver):
    # Match the pinned SELECT row shape, adding existing columns for Python
    # sorting so correctness does not depend on the database's collation.
    SELECT_SQL = PostgresSaver.SELECT_SQL.replace(
        "cw.blob] order by cw.task_id, cw.idx",
        "cw.blob, cw.task_path::bytea, cw.idx::text::bytea] order by cw.task_id, cw.idx",
    )

    def _load_writes(self, writes):
        ordered = sorted(writes or [], key=lambda row: (
            row[4].decode(), row[0].decode(), int(row[5].decode()),
        ))
        return super()._load_writes([row[:4] for row in ordered])

    # The pinned optimized PostgreSQL history query independently sorts by ID.
    # The official base walk instead preserves get_tuple's corrected order,
    # along with fork ancestry, per-channel seeds, and target-write exclusion.
    get_delta_channel_history = BaseCheckpointSaver.get_delta_channel_history
