"""Opt-in real model-table reads; never initialize, edit or call a provider."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import os
import threading
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from src.application.orchestration.framework_submission_service import FrameworkReleaseSubmissionLoader
from src.core.config import DeepAgentsConfig
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.agents import AgentRegistry
from src.registry.models import ModelRegistry
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.model_config import ModelInvocationParameters
from tests.test_agent_task_dispatch import harness, payload
from tests.test_framework_model_admission import admission, counts
from tests.test_framework_release_binding import (
    current_source_release, formal_project, isolate_recovery_stores,
    prepare_formal_project, prepared_dispatcher,
)
from tests.test_framework_submission_loading import require_unlocked_session


class _ReadOnlyCursor:
    """Test-only audit of MySQLModelConfigStore.list_all's existing SELECT.

    This is not a production SQL policy. Results still come from the real
    cursor; the acceptance harness cannot initialize or mutate the model table.
    """
    def __init__(self, cursor, allowed, on_read):
        self._cursor, self._allowed, self._on_read = cursor, " ".join(allowed.split()), on_read

    def execute(self, statement, parameters=None):
        if parameters is not None or " ".join(statement.split()) != self._allowed:
            raise RuntimeError("Only the existing model SELECT is allowed")
        self._on_read()
        try:
            self._cursor.execute(statement)
        except Exception:
            raise RuntimeError("Read-only model query failed") from None
        return self

    def fetchall(self):
        try:
            return self._cursor.fetchall()
        except Exception:
            raise RuntimeError("Read-only model query failed") from None


SELECT = "SELECT id, model_name FROM `fixture_models` ORDER BY id ASC"


@pytest.mark.parametrize("statement", ["UPDATE fixture_models SET is_active=0", "DELETE FROM fixture_models",
    "DROP TABLE fixture_models", "CREATE TABLE fixture_models(id INT)", "ALTER TABLE fixture_models ADD x INT",
    "SELECT id, model_name FROM `fixture_models` ORDER BY id ASC; DELETE FROM fixture_models",
    "SELECT id, model_name INTO OUTFILE '/tmp/forbidden' FROM fixture_models", "SELECT 1"])
def test_mysql_acceptance_cursor_refuses_every_statement_except_the_existing_read(statement):
    calls = []
    raw = SimpleNamespace(execute=lambda *args: calls.append(args))
    cursor = _ReadOnlyCursor(raw, SELECT, lambda: calls.append("audit"))
    with pytest.raises(RuntimeError, match="Only the existing model SELECT is allowed"):
        cursor.execute(statement)
    assert calls == []


def test_mysql_acceptance_cursor_delegates_the_original_read_and_results_without_replacement():
    calls = []
    rows = [{"id": 1, "model_name": "synthetic-model"}]
    raw = SimpleNamespace(execute=lambda *args: calls.append(args), fetchall=lambda: rows)
    cursor = _ReadOnlyCursor(raw, SELECT, lambda: calls.append("audit"))
    cursor.execute("\n SELECT id, model_name\n FROM `fixture_models` ORDER BY id ASC\n")
    assert calls[0] == "audit" and len(calls) == 2
    assert cursor.fetchall() is rows


def test_mysql_acceptance_cursor_cannot_forward_parameters_or_private_driver_errors():
    def unavailable(*args):
        raise ValueError("PRIVATE_DRIVER_DETAIL")
    cursor = _ReadOnlyCursor(SimpleNamespace(execute=unavailable), SELECT, lambda: None)
    with pytest.raises(RuntimeError, match="Only the existing model SELECT is allowed"):
        cursor.execute(SELECT, ("PRIVATE_PARAMETER",))
    with pytest.raises(RuntimeError, match="Read-only model query failed") as error:
        cursor.execute(SELECT)
    assert "PRIVATE_DRIVER_DETAIL" not in str(error.value)
    cursor = _ReadOnlyCursor(SimpleNamespace(fetchall=unavailable), SELECT, lambda: None)
    with pytest.raises(RuntimeError, match="Read-only model query failed") as error:
        cursor.fetchall()
    assert "PRIVATE_DRIVER_DETAIL" not in str(error.value)


@pytest_asyncio.fixture
async def mysql_submission(admission, current_source_release, monkeypatch):
    """Read existing data through the real store; isolate only PG admission.

    The cursor wrapper limits this acceptance fixture, not production SQL.
    No initialize, model client, Worker, mutable result replacement or cache.
    """
    h = admission
    store = MySQLModelConfigStore(h.settings)
    connect = store._connect
    loop_thread = threading.get_ident()
    reads, remote_calls = [], []
    checks = SimpleNamespace(admission=False)
    allowed = (f"SELECT {store._SELECT_COLS} "
        f"FROM `{h.settings.database.llm_model_table}` ORDER BY id ASC")

    def on_read():
        assert threading.get_ident() != loop_thread, "MySQL material reads must leave the event loop"
        if checks.admission:
            require_unlocked_session(h)
            assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
        reads.append(threading.get_ident())

    @contextmanager
    def read_only_connect():
        try:
            with connect() as connection:
                @contextmanager
                def cursor():
                    with connection.cursor() as raw:
                        yield _ReadOnlyCursor(raw, allowed, on_read)
                yield SimpleNamespace(cursor=cursor)
        except AssertionError:
            raise
        except Exception:
            # A real connection error must not expose its driver URL/credentials.
            raise RuntimeError("Read-only model connection failed") from None

    def no_initialize():
        pytest.fail("Acceptance must not initialize or migrate the existing model table")

    def no_remote_http(*args, **kwargs):
        remote_calls.append(True)
        pytest.fail("Admission cannot send a provider or other remote HTTP request")

    async def no_remote_async_http(*args, **kwargs):
        no_remote_http()

    monkeypatch.setattr(store, "_connect", read_only_connect)
    monkeypatch.setattr(store, "initialize", no_initialize)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_remote_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_remote_async_http)

    def capture():
        records = store.list_all()
        wire = json.dumps([item.model_dump(mode="json") for item in records],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(wire.encode("utf-8")).digest()
        active = [item for item in records if item.is_active]
        if not active or "task_execution" not in active[0].applications:
            raise RuntimeError("Live model-table acceptance needs an active task model")
        snapshots = {item.key: ModelInvocationParameters.from_configuration(item) for item in active}
        private = [value for item in records for value in (
            item.api_key, item.oauth_refresh_token, item.api_base_url, *item.extra_headers.values()) if value]
        return digest, snapshots, active[0].key, private

    before, snapshots, default_key, private = await asyncio.to_thread(capture)

    def require_no_secrets(wire):
        clean = not any(value in wire for value in private)
        assert clean, "Private model configuration escaped into admission evidence"

    source_store, reference = current_source_release
    h.settings.deep_agents = DeepAgentsConfig(enabled=True, checkpoint_enabled=True, new_turns_enabled=True,
        release_store=source_store, release_reference=source_store / "fixture-reference.json",
        message_submission_policy=dict(checkpoint_namespace="mysql-acceptance:v2", max_calls=8,
            max_tokens=100_000, deadline_seconds=300))
    capabilities = FrameworkWorkerCapabilities.from_release(*current_source_release, deepagents_available=True)
    registry = ModelRegistry(store)
    loader = FrameworkReleaseSubmissionLoader.from_configuration(h.settings.deep_agents, capabilities,
        agent_registry=AgentRegistry(), model_registry=registry)
    dispatcher = prepared_dispatcher(h, current_source_release, None, loader=loader)
    try:
        yield SimpleNamespace(store=store, connection=read_only_connect, registry=registry, loader=loader,
            dispatcher=dispatcher, snapshots=snapshots, default_key=default_key, reads=reads, checks=checks,
            require_no_secrets=require_no_secrets, remote_calls=remote_calls)
    finally:
        # Restore only this fixture's connection port after failure injection.
        # Read again through the same constrained, real SELECT; never repair data.
        checks.admission = False
        monkeypatch.setattr(store, "_connect", read_only_connect)
        after = await asyncio.to_thread(capture)
        unchanged = before == after[0]
        assert unchanged, "Existing model-table projection changed during read-only acceptance"
        assert not remote_calls and h.runtime.calls == []


live_mysql = pytest.mark.skipif(os.getenv("RUN_LIVE_MYSQL_TESTS") != "1",
    reason="set RUN_LIVE_MYSQL_TESTS=1; reads the existing model table without initializing it")


@live_mysql
@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["explicit", "auto", "session_preference"])
async def test_real_mysql_configuration_is_frozen_at_http_admission_without_a_model_call(mysql_submission,
        admission, isolate_recovery_stores, current_source_release, formal_project, monkeypatch, caplog, selection):
    h, source = admission, mysql_submission
    h.sessions._project_service = formal_project.service
    session = await h.store.get_session(h.session_id)
    session.project_id = formal_project.project.id
    session.preferred_model = source.default_key if selection == "session_preference" else None
    await h.store.save_session(session)
    body = payload(key="real-mysql-" + selection, mode_key="code_review", agent_key="coordinator",
        model_key={"explicit": source.default_key, "auto": "auto", "session_preference": None}[selection]
        ).model_dump(mode="json")
    source.checks.admission = True
    read_count = len(source.reads)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        accepted = response.status_code == 202
        assert accepted, "Live MySQL-backed material was not admitted"
        assert len(source.reads) > read_count
        source.checks.admission = False
        task = await h.tasks.get(response.json()["task"]["id"])
        selected = task.runtime_binding
        snapshot = await h.calls.snapshot(selected)
        budget = snapshot.policy
        root = budget.root_configuration(selected.model_config_fingerprint)
        expected = source.snapshots[source.default_key]
        same_configuration = root == expected
        assert same_configuration, "The captured real configuration differs from its frozen nonsecret snapshot"
        assert selected.release_reference == current_source_release[1]
        assert selected.model_provider == expected.provider and selected.model_transport == expected.transport
        assert selected.checkpoint_namespace == "mysql-acceptance:v2"
        assert selected.model_config_fingerprint == expected.fingerprint
        assert budget.schema_version == 2 and budget.model_config_fingerprints == {root.key: expected.fingerprint}
        assert set(budget.model_configurations) == {root.key}
        assert budget.max_calls == 8 and budget.max_tokens == 100_000
        assert budget.deadline_at == selected.created_at + timedelta(seconds=300)
        assert task.project_id == formal_project.project.id and task.input == body
        assert snapshot.calls == [] and h.runtime.calls == []
        assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
        with postgres_connect(h.settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT stream,payload FROM {h.settings.database.postgres_task_outbox_table}")
            outbox = cursor.fetchone()
            cursor.execute(f"SELECT input_hash,input_payload FROM {h.settings.database.postgres_agent_task_table}")
            original = cursor.fetchone()
        assert original["input_hash"] == h.tasks._v2_input_hash(original["input_payload"], selected, budget)
        assert outbox["stream"] == h.settings.orchestration.agent_task_v2_stream
        assert outbox["payload"]["schema_version"] == 2
        assert outbox["payload"]["runtime_binding"] == selected.model_dump(mode="json")
        assert outbox["payload"]["project_id"] == formal_project.project.id
        current = await h.store.get_session(h.session_id)
        assert len(current.messages) == 1 and current.messages[0].metadata["turn_id"] == task.turn_id
        assert current.messages[0].metadata["context_eligible"] is False
        saved_reads = len(source.reads)
        attempts = []

        @contextmanager
        def unavailable():
            attempts.append(True)
            raise ConnectionError("PRIVATE_MODEL_SOURCE_FAILURE")
            yield  # pragma: no cover -- contextmanager failure before acquisition

        monkeypatch.setattr(source.store, "_connect", unavailable)
        retry = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        conflict = await client.post(f"/sessions/{h.session_id}/messages", json={**body, "content": "different"})
        assert retry.status_code == 202 and retry.json()["task"]["id"] == task.id
        assert conflict.status_code == 409 and attempts == [] and len(source.reads) == saved_reads
        failed = await client.post(f"/sessions/{h.session_id}/messages", json={**body,
            "metadata": {"client_message_id": "real-mysql-new-failure"}})
        assert failed.status_code == 400 and len(attempts) == 1
        assert failed.json()["detail"] == "Framework message material loading failed"
        assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
        assert await h.calls.snapshot(selected) == snapshot
        wire = (response.text + retry.text + conflict.text + failed.text + budget.model_dump_json()
            + json.dumps(outbox["payload"], ensure_ascii=False) + caplog.text)
        assert "PRIVATE_MODEL_SOURCE_FAILURE" not in wire
        source.require_no_secrets(wire)
        assert not source.remote_calls


@live_mysql
@pytest.mark.asyncio
async def test_real_mysql_connection_failure_has_no_partial_admission_and_same_intent_can_retry(mysql_submission,
        admission, isolate_recovery_stores, monkeypatch, caplog):
    h, source = admission, mysql_submission
    body = payload(key="real-mysql-recover-source", mode_key="code_review", agent_key="coordinator",
        model_key="auto").model_dump(mode="json")

    @contextmanager
    def unavailable():
        raise ConnectionError("PRIVATE_MODEL_SOURCE_FAILURE")
        yield  # pragma: no cover -- contextmanager failure before acquisition

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        monkeypatch.setattr(source.store, "_connect", unavailable)
        failed = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        assert failed.status_code == 400
        assert failed.json()["detail"] == "Framework message material loading failed"
        assert counts(h) == dict(tasks=0, owners=0, outbox=0, budgets=0, messages=0)
        monkeypatch.setattr(source.store, "_connect", source.connection)
        source.checks.admission = True
        response = await client.post(f"/sessions/{h.session_id}/messages", json=body)
        accepted = response.status_code == 202
        assert accepted, "Restored real MySQL read could not admit the original intent"
        source.checks.admission = False
        task = await h.tasks.get(response.json()["task"]["id"])
        snapshot = await h.calls.snapshot(task.runtime_binding)
        assert snapshot.calls == [] and h.runtime.calls == []
        assert counts(h) == dict(tasks=1, owners=1, outbox=1, budgets=1, messages=1)
        wire = failed.text + response.text + snapshot.policy.model_dump_json() + caplog.text
        assert "PRIVATE_MODEL_SOURCE_FAILURE" not in wire
        source.require_no_secrets(wire)
