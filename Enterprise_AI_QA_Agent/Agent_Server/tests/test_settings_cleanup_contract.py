from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.settings import router as settings_router


class CleanupStore:
    def __init__(self) -> None:
        self.deleted_cutoffs: list[datetime | None] = []

    async def count_sessions(self, after: datetime | None = None) -> int:
        return 1 if after is not None else 3

    async def get_session_date_range(self) -> dict[str, datetime | None]:
        return {"oldest": None, "newest": None}

    async def delete_sessions_before(self, cutoff: datetime | None) -> int:
        self.deleted_cutoffs.append(cutoff)
        return 3 if cutoff is None else 2


def _client() -> tuple[TestClient, CleanupStore]:
    app = FastAPI()
    app.include_router(settings_router, prefix="/api/v1")
    store = CleanupStore()
    app.state.store = store
    return TestClient(app), store


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "backup", "dry_run": False, "time_range_days": 0, "confirm": True},
        {"action": "cleanup", "dry_run": False, "confirm": True},
        {"action": "cleanup", "dry_run": False, "time_range_days": None, "confirm": True},
        {"action": "cleanup", "dry_run": False, "time_range_days": -1, "confirm": True},
    ],
)
def test_cleanup_rejects_ambiguous_or_wrong_scope_before_deletion(payload: dict) -> None:
    client, store = _client()

    response = client.post("/api/v1/settings/data/cleanup", json=payload)

    assert response.status_code == 422
    assert store.deleted_cutoffs == []


def test_cleanup_requires_separate_full_scope_confirmation() -> None:
    client, store = _client()

    response = client.post("/api/v1/settings/data/cleanup", json={
        "action": "cleanup", "dry_run": False, "time_range_days": 0, "confirm": True,
    })

    assert response.status_code == 400
    assert store.deleted_cutoffs == []


def test_cleanup_explicit_full_scope_and_positive_range_remain_available() -> None:
    client, store = _client()

    full = client.post("/api/v1/settings/data/cleanup", json={
        "action": "cleanup", "dry_run": False, "time_range_days": 0,
        "confirm": True, "confirm_all_sessions": True,
    })
    partial = client.post("/api/v1/settings/data/cleanup", json={
        "action": "cleanup", "dry_run": False, "time_range_days": 30, "confirm": True,
    })

    assert full.status_code == 200
    assert partial.status_code == 200
    assert full.json()["affected_count"] == 3
    assert partial.json()["affected_count"] == 2
    assert store.deleted_cutoffs[0] is None
    assert store.deleted_cutoffs[1] is not None
