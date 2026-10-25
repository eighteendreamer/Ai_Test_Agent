"""Native interrupt links and delivery facts in caller-owned transactions.

No upstream counterpart (checked: LangGraph 1.2.12 types/_loop/_runner,
PostgresSaver 3.1.2, integration plan §5): native pending writes do not carry
our business approval receipt. Never infer external effects from a missing
receipt. This journal does not authorize a continuation or settle a ToolJob.
"""
from __future__ import annotations

import json
import logging

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import sql

from src.runtime.postgres_runtime_owner import assert_runtime_owner, lock_owner_row, runtime_owner_table
from src.schemas.framework_approval import (
    FrameworkDecisionReceipt, FrameworkInterruptLink, approval_value_hash,
)


logger = logging.getLogger(__name__)
RESUME = "__resume__"
INTERRUPT = "__interrupt__"


class FrameworkApprovalRecoveryRequired(ValueError):
    """Persisted native and business identities disagree; do not resend blindly."""


def framework_approval_tables(settings) -> tuple[str, str]:
    base = settings.database.postgres_agent_task_table
    return f"{base}_interrupt", f"{base}_decision"


def initialize_framework_approvals(cur, settings) -> None:
    links, receipts = framework_approval_tables(settings)
    cur.execute(f"""CREATE TABLE IF NOT EXISTS public.{links} (
        approval_id TEXT PRIMARY KEY REFERENCES public.{settings.database.postgres_approval_table}(id) ON DELETE CASCADE,
        binding_id TEXT NOT NULL REFERENCES public.{runtime_owner_table(settings)}(binding_id) ON DELETE CASCADE,
        checkpoint_ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
        graph_task_id TEXT NOT NULL, resume_index INTEGER NOT NULL CHECK (resume_index >= 0),
        source_message_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, link JSONB NOT NULL,
        UNIQUE(binding_id, checkpoint_ns, source_message_id, tool_call_id),
        UNIQUE(binding_id, checkpoint_ns, checkpoint_id, graph_task_id, resume_index)
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS public.{receipts} (
        decision_id TEXT PRIMARY KEY,
        approval_id TEXT NOT NULL UNIQUE REFERENCES public.{links}(approval_id) ON DELETE CASCADE,
        decision JSONB NOT NULL, decision_hash TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','sending','delivered')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        delivered_at TIMESTAMPTZ, applied_at TIMESTAMPTZ, proof JSONB, last_error TEXT,
        CHECK ((state='delivered' AND delivered_at IS NOT NULL AND proof IS NOT NULL)
            OR (state IN ('queued','sending') AND delivered_at IS NULL AND proof IS NULL))
    )""")


class FrameworkApprovalJournal:
    def __init__(self, settings, *, serde=None):
        self._settings = settings
        self._serde = serde or JsonPlusSerializer()
        links, receipts = framework_approval_tables(settings)
        self._links, self._receipts = f"public.{links}", f"public.{receipts}"

    def _native_rows(self, cur, binding, link_config, task_id):
        cur.execute(sql.SQL("SELECT idx, channel, type, blob, task_path FROM {} "
            "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s AND task_id=%s "
            "AND channel IN (%s,%s) ORDER BY idx").format(sql.Identifier(
                self._settings.deep_agents.checkpoint_schema, "checkpoint_writes")),
            (binding.thread_id, link_config["checkpoint_ns"], link_config["checkpoint_id"],
             task_id, RESUME, INTERRUPT))
        return {row["channel"]: {**row, "value": self._serde.loads_typed((row["type"], bytes(row["blob"])))}
            for row in cur.fetchall()}

    @staticmethod
    def _namespace(binding, logical):
        return binding.checkpoint_namespace + ("|" + logical if logical else "")

    @staticmethod
    def _assert_business_link(binding, link, approval):
        metadata = (approval or {}).get("metadata") or {}
        if (approval is None or approval["session_id"] != binding.session_id
            or approval["tool_key"] != link.tool_key
            or metadata.get("tool_job_id") != link.tool_job_id
            or metadata.get("turn_id") != binding.turn_id or metadata.get("call_id") != link.tool_call_id
            or metadata.get("approval_scope_hash") != link.scope_hash
            or approval_value_hash(metadata.get("arguments")) != approval_value_hash(link.arguments)):
            raise FrameworkApprovalRecoveryRequired("Interrupt link does not match the business approval")

    def link_interrupt_in_transaction(self, cur, owner, link: FrameworkInterruptLink):
        assert_runtime_owner(cur, self._settings, owner, schema="public.")
        binding = owner.binding
        if link.binding_id != binding.binding_id or link.tool_schema_version != binding.tool_schema_version:
            raise FrameworkApprovalRecoveryRequired("Interrupt link does not match the runtime binding")
        cur.execute(f"SELECT * FROM public.{self._settings.database.postgres_approval_table} "
            "WHERE id=%s FOR UPDATE", (link.approval_id,))
        approval = cur.fetchone()
        self._assert_business_link(binding, link, approval)
        cur.execute(f"SELECT link FROM {self._links} WHERE approval_id=%s", (link.approval_id,))
        saved = cur.fetchone()
        if saved is not None:
            if FrameworkInterruptLink.model_validate(saved["link"]) != link:
                raise FrameworkApprovalRecoveryRequired("An approval cannot change its native interrupt identity")
            return link
        location = {"checkpoint_ns": self._namespace(binding, link.checkpoint_namespace),
            "checkpoint_id": link.checkpoint_id}
        rows = self._native_rows(cur, binding, location, link.graph_task_id)
        interruption = rows.get(INTERRUPT)
        resumes = rows.get(RESUME, {}).get("value", [])
        if (interruption is None or interruption["task_path"] != link.task_path
            or not isinstance(resumes, list) or len(resumes) != link.resume_index):
            raise FrameworkApprovalRecoveryRequired("Interrupt task or resume position is not durably pending")
        matches = [item for item in interruption["value"] if item.id == link.interrupt_id]
        payload = matches[0].value if len(matches) == 1 else None
        if (not isinstance(payload, dict) or payload.get("type") != "tool_approval"
            or payload.get("tool_call_id") != link.tool_call_id or payload.get("name") != link.tool_name
            or approval_value_hash(payload.get("args")) != approval_value_hash(link.arguments)):
            raise FrameworkApprovalRecoveryRequired("Persisted tool interrupt does not match its approval link")
        cur.execute(f"""INSERT INTO {self._links} (approval_id,binding_id,checkpoint_ns,
            checkpoint_id,graph_task_id,resume_index,source_message_id,tool_call_id,link)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
            (link.approval_id, binding.binding_id, location["checkpoint_ns"], link.checkpoint_id,
             link.graph_task_id, link.resume_index, link.source_message_id, link.tool_call_id,
             json.dumps(link.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)))
        if cur.rowcount != 1:
            raise FrameworkApprovalRecoveryRequired("Native call is already linked to a different approval")
        logger.info("framework_interrupt_linked", extra={"binding_id": binding.binding_id,
            "approval_id": link.approval_id, "graph_task_id": link.graph_task_id,
            "resume_index": link.resume_index})
        return link

    def queue_decision_in_transaction(self, cur, binding, approval_id, *, decision_id):
        """Record an already-decided approval in its caller's submission transaction.

        Admission, actor authorization, task and outbox creation belong to the
        v2 submission service; this method never changes the human decision.
        """
        cur.execute(f"SELECT id FROM public.{self._settings.database.postgres_session_table} "
            "WHERE id=%s FOR UPDATE", (binding.session_id,))
        if cur.fetchone() is None:
            raise FrameworkApprovalRecoveryRequired("Approval Session does not exist")
        lock_owner_row(cur, self._settings, binding)
        cur.execute(f"SELECT * FROM public.{self._settings.database.postgres_approval_table} "
            "WHERE id=%s FOR UPDATE", (approval_id,))
        approval = cur.fetchone()
        cur.execute(f"SELECT link FROM {self._links} WHERE approval_id=%s AND binding_id=%s",
            (approval_id, binding.binding_id))
        linked = cur.fetchone()
        if approval is None or approval["session_id"] != binding.session_id or linked is None:
            raise FrameworkApprovalRecoveryRequired("Decision has no matching native approval link")
        self._assert_business_link(binding, FrameworkInterruptLink.model_validate(linked["link"]), approval)
        if approval["status"] not in {"approved", "denied"}:
            raise FrameworkApprovalRecoveryRequired("A pending approval has no decision to deliver")
        decision = ({"type": "approve"} if approval["status"] == "approved" else
            {"type": "reject", "message": approval.get("decision_note") or "Human denied execution."})
        existing = self.get_receipt_in_transaction(cur, approval_id)
        if existing is not None:
            if existing.decision["type"] != decision["type"]:
                raise FrameworkApprovalRecoveryRequired("A recorded decision cannot be reversed")
            return existing
        cur.execute(f"INSERT INTO {self._receipts} (decision_id,approval_id,decision,decision_hash) "
            "VALUES (%s,%s,%s::jsonb,%s)",
            (decision_id, approval_id, json.dumps(decision, ensure_ascii=False), approval_value_hash(decision)))
        logger.info("framework_decision_queued", extra={"binding_id": binding.binding_id,
            "approval_id": approval_id, "decision_id": decision_id})
        return self.get_receipt_in_transaction(cur, approval_id)

    def get_receipt_in_transaction(self, cur, approval_id):
        cur.execute(f"SELECT * FROM {self._receipts} WHERE approval_id=%s", (approval_id,))
        row = cur.fetchone()
        return FrameworkDecisionReceipt.model_validate(row) if row is not None else None

    def prepare_resume_in_transaction(self, cur, owner, storage_config, writes, task_id):
        """Keep cumulative native resume prefixes monotonic for linked tasks.

        In 1.2.12 interrupt() emits each consumed prefix into the SAME task
        batch. Saver 3.1.2 uses DO NOTHING for mixed-channel batches, which can
        keep an earlier prefix (or an earlier invocation's prefix). The owner
        lock lets us validate compatible prefixes and persist their longest
        value using the official saver's special-channel upsert. No other
        channel, unlinked task, serializer or checkpoint behavior is changed.
        """
        incoming = [value for channel, value in writes if channel == RESUME]
        if not incoming:
            return None
        location = storage_config["configurable"]
        cur.execute(f"SELECT approval_id FROM {self._links} WHERE binding_id=%s "
            "AND checkpoint_ns=%s AND checkpoint_id=%s AND graph_task_id=%s LIMIT 1",
            (owner.binding.binding_id, location["checkpoint_ns"], location["checkpoint_id"], task_id))
        if cur.fetchone() is None:
            return None
        saved = self._native_rows(cur, owner.binding, location, task_id).get(RESUME)
        prefixes = [*incoming, *([saved["value"]] if saved is not None else [])]
        if not all(isinstance(value, list) for value in prefixes):
            raise FrameworkApprovalRecoveryRequired("Unsupported native task resume write format")
        longest = max(prefixes, key=len)
        if any(approval_value_hash(value) != approval_value_hash(longest[:len(value)]) for value in prefixes):
            raise FrameworkApprovalRecoveryRequired("Native task resume prefixes conflict")
        return longest

    def reconcile_delivery_in_transaction(self, cur, owner, approval_id):
        """Repair a projection from a saved fact, without invoking the graph."""
        assert_runtime_owner(cur, self._settings, owner, schema="public.")
        cur.execute(f"SELECT link, checkpoint_ns FROM {self._links} WHERE approval_id=%s AND binding_id=%s",
            (approval_id, owner.binding.binding_id))
        row = cur.fetchone()
        if row is None:
            raise FrameworkApprovalRecoveryRequired("Reconciliation has no matching interrupt link")
        link = FrameworkInterruptLink.model_validate(row["link"])
        location = {"thread_id": owner.binding.thread_id, "checkpoint_ns": row["checkpoint_ns"],
            "checkpoint_id": link.checkpoint_id}
        saved = self._native_rows(cur, owner.binding, location, link.graph_task_id).get(RESUME)
        if saved is not None:
            self.record_delivery_in_transaction(cur, owner, {"configurable": location},
                [(RESUME, saved["value"])], link.graph_task_id, link.task_path, evidence_kind="reconciled")
        return self.get_receipt_in_transaction(cur, approval_id)

    def record_delivery_in_transaction(self, cur, owner, storage_config, writes, task_id, task_path,
                                       *, evidence_kind="atomic_write"):
        incoming = [value for channel, value in writes if channel == RESUME]
        if not incoming:
            return
        location = storage_config["configurable"]
        cur.execute(f"SELECT l.link, r.* FROM {self._links} l JOIN {self._receipts} r "
            "ON r.approval_id=l.approval_id WHERE l.binding_id=%s AND l.checkpoint_ns=%s "
            "AND l.checkpoint_id=%s AND l.graph_task_id=%s ORDER BY l.resume_index FOR UPDATE OF r",
            (owner.binding.binding_id, location["checkpoint_ns"], location["checkpoint_id"], task_id))
        candidates = list(cur.fetchall())
        if not candidates:
            return
        if len(incoming) != 1 or not isinstance(incoming[0], list):
            raise FrameworkApprovalRecoveryRequired("Unsupported native task resume write format")
        rows = self._native_rows(cur, owner.binding, location, task_id)
        saved = rows.get(RESUME)
        for row in candidates:
            link = FrameworkInterruptLink.model_validate(row["link"])
            index = link.resume_index
            if index >= len(incoming[0]):
                continue  # Replayed consumed prefixes need not include later interrupts.
            if (link.task_path != task_path or approval_value_hash(incoming[0][index]) != row["decision_hash"]
                or saved is None or saved["task_path"] != task_path or not isinstance(saved["value"], list)
                or index >= len(saved["value"]) or approval_value_hash(saved["value"][index]) != row["decision_hash"]):
                raise FrameworkApprovalRecoveryRequired("Native resume write does not prove the recorded decision")
            proof = {"format_version": link.format_version, "thread_id": owner.binding.thread_id,
                "checkpoint_ns": location["checkpoint_ns"], "checkpoint_id": location["checkpoint_id"],
                "graph_task_id": task_id, "task_path": task_path, "channel": RESUME,
                "write_index": saved["idx"], "interrupt_id": link.interrupt_id,
                "resume_index": index, "decision_hash": row["decision_hash"], "owner_epoch": owner.owner_epoch,
                "evidence_kind": evidence_kind}
            if row["state"] == "delivered":
                # A replacement owner can observe/replay the SAME durable fact.
                # Preserve the first committing epoch/time as its proof.
                if {k: v for k, v in row["proof"].items() if k not in {"owner_epoch", "evidence_kind"}} != {
                    k: v for k, v in proof.items() if k not in {"owner_epoch", "evidence_kind"}}:
                    raise FrameworkApprovalRecoveryRequired("A delivery proof cannot change its identity")
                continue
            self._save_proof(cur, row["decision_id"], proof)
            logger.info("framework_decision_delivered", extra={"binding_id": owner.binding.binding_id,
                "decision_id": row["decision_id"], "graph_task_id": task_id, "owner_epoch": owner.owner_epoch})

    def _save_proof(self, cur, decision_id, proof):
        cur.execute(f"UPDATE {self._receipts} SET state='delivered', delivered_at=clock_timestamp(), "
            "proof=%s::jsonb, last_error=NULL WHERE decision_id=%s AND state IN ('queued','sending')",
            (json.dumps(proof, ensure_ascii=False), decision_id))
