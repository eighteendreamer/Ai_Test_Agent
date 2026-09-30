from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.core.config import Settings
from src.domain.models import SessionRecord
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.store import InMemorySessionStore
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _drop_tables


async def _exercise_stale_save_after_dispatch_claim(store) -> None:
    now = datetime.now(timezone.utc)
    session_id = f"claim-snapshot-{uuid4().hex}"
    session = SessionRecord(
        id=session_id,
        title="coordinator dispatch claim snapshot",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        created_at=now,
        updated_at=now,
    )
    await store.save_session(session)
    stale = deepcopy(await store.get_session(session_id))
    request_fingerprint = "f" * 64
    assert await store.claim_coordinator_dispatch(
        session_id,
        "turn-1",
        "task-1",
        "worker-a",
        request_fingerprint,
    )

    stale.title = "updated preferences"
    await store.save_session(stale)

    persisted = await store.get_session(session_id)
    assert persisted is not None
    assert persisted.title == "updated preferences"
    assert len(persisted.metadata.get("coordinator_dispatch_claims", [])) == 1
    assert (
        persisted.metadata["coordinator_dispatch_claims"][0]["request_fingerprint"]
        == request_fingerprint
    )
    assert not await store.claim_coordinator_dispatch(
        session_id,
        "turn-1",
        "task-1",
        "worker-b",
        request_fingerprint,
    )


@pytest.mark.asyncio
async def test_memory_stale_session_save_preserves_dispatch_claim() -> None:
    await _exercise_stale_save_after_dispatch_claim(InMemorySessionStore())


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use local PostgreSQL",
)
@pytest.mark.asyncio
async def test_postgres_stale_session_save_preserves_dispatch_claim() -> None:
    suffix = uuid4().hex[:10]
    tables = {
        "postgres_session_table": f"live_claim_session_{suffix}",
        "postgres_message_table": f"live_claim_message_{suffix}",
        "postgres_event_table": f"live_claim_event_{suffix}",
        "postgres_snapshot_table": f"live_claim_snapshot_{suffix}",
        "postgres_approval_table": f"live_claim_approval_{suffix}",
    }
    settings = Settings()
    settings = settings.model_copy(update={
        "database": settings.database.model_copy(update=tables),
    })
    store = PostgresSessionStore(settings)
    try:
        await store.initialize()
        await _exercise_stale_save_after_dispatch_claim(store)
    finally:
        _drop_tables(settings, list(reversed(tables.values())))
