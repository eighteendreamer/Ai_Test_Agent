"""Decision-local diagnostics never grant execution or change lease policy."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
import logging

import pytest
from pydantic import ValidationError

from src.core.log_format import configure_logging
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_runtime_owner import assert_runtime_owner, runtime_owner_table
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, start
from tests.test_framework_model_admission import admission
from tests.test_framework_message_stages import selected
from tests.test_runtime_binding_persistence import make_binding


class ObservedCursor:
    def __init__(self, cursor):
        self.cursor, self.statements, self.observed_at = cursor, [], None

    def execute(self, statement, params=None):
        self.statements.append(statement)
        return self.cursor.execute(statement, params)

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None and "now" in row:
            self.observed_at = row["now"]
        return row


@pytest.mark.asyncio
@pytest.mark.parametrize("changed,reason", [("session_expired", "session_lease_inactive"),
    ("owner_expired", "owner_lease_inactive"), ("token", "session_token_mismatch"),
    ("epoch", "owner_epoch_mismatch"), ("state", "owner_state_mismatch"),
    ("binding", "owner_binding_mismatch"), ("task_id", "owner_task_mismatch"),
    ("attempt_id", "owner_attempt_mismatch"), ("worker_id", "owner_worker_mismatch")])
async def test_real_guard_logs_the_original_locked_decision_without_requery_or_mutation(checkpoint_harness, caplog, changed, reason):
    h = checkpoint_harness
    await start(h)
    owner = h.owner.runtime_owner
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed in {"session_expired", "token"}:
            key, expression = (("turn_lease_expires_at", "to_jsonb(clock_timestamp()-INTERVAL '1 second')")
                if changed == "session_expired" else ("turn_lease_token", "to_jsonb('private-stale-lease'::text)"))
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set(metadata,"
                f"'{{{key}}}',{expression}) WHERE id=%s", (h.session_id,))
        elif changed == "owner_expired":
            cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET expires_at=clock_timestamp()-INTERVAL '1 second' "
                "WHERE binding_id=%s", (h.binding.binding_id,))
        elif changed == "epoch":
            owner = owner.model_copy(update={"owner_epoch": owner.owner_epoch+1})
        elif changed == "binding":
            owner = owner.model_copy(update={"binding": owner.binding.model_copy(update={"prompt_version": "changed"})})
        elif changed in {"task_id", "attempt_id", "worker_id"}:
            owner = owner.model_copy(update={changed: "stale-"+getattr(owner, changed)})
        else:
            cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET execution_state='interrupted' WHERE binding_id=%s",
                (h.binding.binding_id,))
        cur.execute(f"SELECT metadata FROM {h.settings.database.postgres_session_table} WHERE id=%s", (h.session_id,))
        session_before = dict(cur.fetchone())
        cur.execute(f"SELECT * FROM {runtime_owner_table(h.settings)} WHERE binding_id=%s", (h.binding.binding_id,))
        owner_before = dict(cur.fetchone())
        observed = ObservedCursor(cur)
        with caplog.at_level(logging.WARNING, logger="src.runtime.postgres_runtime_owner"), pytest.raises(
                ContinuationLeaseLostError, match="^Framework execution owner is no longer active$"):
            assert_runtime_owner(observed, h.settings, owner)
        record, = [r for r in caplog.records if r.message == "framework_runtime_owner_rejected"]
        assert reason in record.authority_rejections.split(",")
        assert record.authority_observed_at == observed.observed_at.isoformat()
        assert record.owner_lease_expires_at == owner_before["expires_at"].isoformat()
        assert record.owner_execution_state == owner_before["execution_state"]
        assert record.session_lease_expires_at == datetime.fromisoformat(session_before["metadata"]["turn_lease_expires_at"]).isoformat()
        assert record.authority_check_seconds >= record.authority_lock_seconds >= 0
        assert record.task_id == owner.task_id and record.binding_id == h.binding.binding_id
        assert record.attempt_id == owner.attempt_id and record.owner_epoch == owner.owner_epoch
        assert len(observed.statements) == 4 and all(sql.lstrip().startswith("SELECT") for sql in observed.statements)
        assert "FOR UPDATE" in observed.statements[0] and "FOR UPDATE" in observed.statements[1]
        assert observed.statements[2] == "SELECT clock_timestamp() AS now"
        assert not any(owner.owner_token in str(value) or "private-stale-lease" in str(value) for value in record.__dict__.values())
        cur.execute(f"SELECT metadata FROM {h.settings.database.postgres_session_table} WHERE id=%s", (h.session_id,))
        assert dict(cur.fetchone()) == session_before
        cur.execute(f"SELECT * FROM {runtime_owner_table(h.settings)} WHERE binding_id=%s", (h.binding.binding_id,))
        assert dict(cur.fetchone()) == owner_before


@pytest.mark.parametrize("missing", ["session", "owner"])
def test_missing_original_has_diagnostics_without_logging_secret_rows(caplog, missing):
    owner = RuntimeExecutionOwner(binding=make_binding("session", "turn"), owner_epoch=1,
        owner_token="private-owner-token", task_id="task", attempt_id="attempt", worker_id="worker")
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    expiry = now+timedelta(seconds=120)
    session = {"metadata": {"turn_lease_expires_at": expiry.isoformat(), "turn_lease_turn_id": "turn",
        "turn_lease_token": owner.owner_token, "private_context": "private-message"}}
    saved = {**owner.model_dump(exclude={"binding"}), "expires_at": expiry, "execution_state": "running",
        "runtime_binding": owner.binding.model_dump(mode="json")}
    rows = iter([None if missing == "session" else session, None if missing == "owner" else saved,
        {"now": now}, {"active": missing != "session", "session_expires_at": None if missing == "session" else expiry}])

    class Cursor:
        def execute(self, *args):
            pass

        def fetchone(self):
            return next(rows)

    with caplog.at_level(logging.WARNING, logger="src.runtime.postgres_runtime_owner"), pytest.raises(ContinuationLeaseLostError):
        assert_runtime_owner(Cursor(), type("Settings", (), {"database": type("DB", (), {
            "postgres_session_table": "sessions", "postgres_agent_task_table": "tasks"})()})(), owner)
    record, = [r for r in caplog.records if r.message == "framework_runtime_owner_rejected"]
    assert f"{missing}_missing" in record.authority_rejections.split(",")
    assert record.authority_observed_at == now.isoformat()
    assert "private-" not in caplog.text
    assert not any("private-" in str(value) for value in record.__dict__.values())


@pytest.mark.parametrize("scalar_rejection", [False, True])
def test_diagnostics_preserve_original_codec_error_and_scalar_short_circuit(caplog, scalar_rejection):
    owner = RuntimeExecutionOwner(binding=make_binding("session", "turn"), owner_epoch=1,
        owner_token="private-owner-token", task_id="task", attempt_id="attempt", worker_id="worker")
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    expiry = now+timedelta(seconds=120)
    rows = iter([{"metadata": {"turn_lease_expires_at": expiry.isoformat(), "turn_lease_turn_id": "turn",
        "turn_lease_token": owner.owner_token}},
        {**owner.model_dump(exclude={"binding"}), "expires_at": expiry, "execution_state": "running",
            "owner_epoch": 2 if scalar_rejection else 1, "runtime_binding": {"private": "invalid-codec-input"}},
        {"now": now}, {"active": True, "session_expires_at": expiry}])

    class Cursor:
        def execute(self, *args):
            pass

        def fetchone(self):
            return next(rows)

    with caplog.at_level(logging.WARNING, logger="src.runtime.postgres_runtime_owner"), pytest.raises(
            ContinuationLeaseLostError if scalar_rejection else ValidationError):
        assert_runtime_owner(Cursor(), type("Settings", (), {"database": type("DB", (), {
            "postgres_session_table": "sessions", "postgres_agent_task_table": "tasks"})()})(), owner)
    records = [r for r in caplog.records if r.message == "framework_runtime_owner_rejected"]
    assert len(records) == int(scalar_rejection)
    if scalar_rejection:
        assert records[0].authority_rejections == "owner_epoch_mismatch"
    assert not any("private-" in str(value) or "invalid-codec-input" in str(value)
        for record in records for value in record.__dict__.values())


@pytest.mark.asyncio
async def test_valid_owner_has_no_rejection_and_no_new_query(checkpoint_harness, caplog):
    h = checkpoint_harness
    await start(h)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        observed = ObservedCursor(cur)
        assert_runtime_owner(observed, h.settings, h.owner.runtime_owner)
        assert len(observed.statements) == 4
    assert not [r for r in caplog.records if r.message == "framework_runtime_owner_rejected"]


@pytest.mark.asyncio
async def test_lock_wait_diagnostic_uses_post_lock_database_decision(checkpoint_harness, caplog):
    from src.application.deep_agents import fenced_postgres_saver as module
    from langgraph.checkpoint.base import empty_checkpoint
    from tests.test_framework_checkpoint_fencing import config

    h = checkpoint_harness
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        with caplog.at_level(logging.WARNING, logger=module.assert_runtime_owner.__module__):
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=metadata || "
                    "jsonb_build_object('turn_lease_expires_at', to_jsonb(clock_timestamp()+INTERVAL '150 milliseconds')) "
                    "WHERE id=%s", (h.session_id,))
                cur.execute("SELECT pg_backend_pid() AS pid")
                blocker = cur.fetchone()["pid"]
                write = asyncio.create_task(saver.aput(config(h), empty_checkpoint(), {}, {}))
                try:
                    # Observe the real blocked SQL, not merely a thread start.
                    for _ in range(100):
                        with postgres_connect(h.settings) as observer, observer.cursor() as probe:
                            probe.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                "WHERE %s=ANY(pg_blocking_pids(pid))) AS blocked", (blocker,))
                            blocked = probe.fetchone()["blocked"]
                        if blocked:
                            break
                        await asyncio.sleep(.01)
                    assert blocked, "Official saver did not enter the real Session row-lock wait"
                    await asyncio.sleep(.2)
                    assert not write.done()
                finally:
                    # Release only this fixture's lock even if observation fails.
                    connection.commit()
                    with pytest.raises(ContinuationLeaseLostError):
                        await asyncio.wait_for(write, 3)
            record, = [r for r in caplog.records if r.message == "framework_runtime_owner_rejected"]
            assert record.authority_rejections == "session_lease_inactive"
            assert datetime.fromisoformat(record.authority_observed_at) > datetime.fromisoformat(record.session_lease_expires_at)
            assert record.authority_lock_seconds > 0 and record.authority_check_seconds >= record.authority_lock_seconds
        assert await saver.aget_tuple(config(h)) is None


@pytest.mark.asyncio
async def test_nonexpired_predecessor_recovery_is_observable_and_does_not_create_a_successor(admission, caplog):
    h = admission
    task = await selected(h)
    before = await h.tasks.list_session(h.session_id)
    with caplog.at_level(logging.INFO, logger="src.runtime.framework_message_recovery"):
        assert await h.tasks.ensure_framework_message_recovery(task.id) is None
    record, = [r for r in caplog.records if r.message == "framework_message_recovery_not_admitted"]
    assert record.recovery_reason == "predecessor_not_expired" and record.task_status == "running"
    assert record.task_id == task.id and record.recovery_source == "message_recovery"
    assert await h.tasks.list_session(h.session_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed,reason", [("session", "session_lease_active"),
    ("task_id", "owner_task_mismatch"), ("attempt_id", "owner_attempt_mismatch")])
async def test_inactive_predecessor_mismatch_is_visible_without_creating_or_mutating_tasks(admission, caplog, changed, reason):
    h = admission
    task = await selected(h)
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    assert (await h.tasks.claim(task.id, worker_id="expiry-observer", expected_binding=h.binding)).status == "recovery_required"
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed == "session":
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=metadata || "
                "jsonb_build_object('turn_lease_expires_at', to_jsonb(clock_timestamp()+INTERVAL '120 seconds')) "
                "WHERE id=%s", (h.session_id,))
        else:
            cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET {changed}=%s WHERE binding_id=%s",
                ("another-"+changed, h.binding.binding_id))
    before = await h.tasks.list_session(h.session_id)
    with caplog.at_level(logging.INFO, logger="src.runtime.framework_message_recovery"):
        assert await h.tasks.ensure_framework_message_recovery(task.id) is None
    record, = [r for r in caplog.records if r.message == "framework_message_recovery_predecessor_rejected"]
    assert record.authority_rejections == reason and record.recovery_reason == "predecessor_not_inactive"
    assert record.task_status == "recovery_required" and record.recovery_phase == "submitted"
    assert record.task_id == task.id and record.binding_id == h.binding.binding_id
    caught, = [r for r in caplog.records if r.message == "framework_message_recovery_not_admitted"]
    assert caught.recovery_reason == "predecessor_rejected" and caught.error_type == "FrameworkMessageStageRecoveryRequired"
    assert await h.tasks.list_session(h.session_id) == before


def test_configured_formatter_renders_only_selected_authority_and_delivery_diagnostics(monkeypatch):
    output = io.StringIO()
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [logging.StreamHandler(output)])
    configure_logging(root.level)
    expected = {"task_id": "task", "binding_id": "binding", "owner_epoch": 2,
        "authority_rejections": "session_lease_inactive", "authority_observed_at": "2026-11-01T00:00:00+00:00",
        "session_lease_expires_at": None, "owner_lease_expires_at": "2026-11-01T00:01:00+00:00",
        "owner_execution_state": "running", "expected_owner_states": "running",
        "authority_lock_seconds": .1, "authority_check_seconds": .2,
        "delivery_message_id": "1-0", "delivery_stream": "test-stream", "retry_count": 0,
        "not_before_ms": 0, "recovery_reason": "predecessor_not_expired"}
    logging.getLogger("src.runtime.postgres_runtime_owner").warning("diagnostic_probe", extra={**deepcopy(expected),
        "owner_token": "private-token", "metadata": {"secret": "private-body"}, "reason": "private-exception"})
    rendered = output.getvalue()
    assert json.loads(rendered.split(" diagnostics=", 1)[1]) == expected
    assert "private-" not in rendered
