"""Immutable compact values from each original reliable tool return."""
import asyncio
import json

from src.application.runtime.case_action_binding import _digest, CaseActionBinding, validate_action_admission_receipt
from src.application.runtime.case_effect_evidence import capture_returned_evidence, INTENT_KEY
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_identifiers import derived_tool_table
from src.runtime.postgres_case_operations import PostgresCaseOperations


class PostgresCaseEffectEvidence:
    def __init__(self, settings):
        self._settings = settings
        self._operations = PostgresCaseOperations(settings)
        self.table = derived_tool_table(settings.database.postgres_tool_job_table, "case_eff")

    async def initialize(self):
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    origin_job_id TEXT PRIMARY KEY REFERENCES {self._settings.database.postgres_tool_job_table}(id),
                    record JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        await asyncio.to_thread(create)

    def capture(self, cur, job, output, observation):
        record = capture_returned_evidence(job, output, observation)
        if record is None:
            return
        # No upstream counterpart (checked: original output observation/settlement,
        # logical-operation index and Agent trajectory course): store compact
        # original values with the return transaction, not a mutable result summary.
        cur.execute(f"INSERT INTO {self.table}(origin_job_id,record) VALUES(%s,%s::jsonb)",
            (job.id, json.dumps(record, ensure_ascii=False, allow_nan=False)))

    async def list_run_evidence(self, project_id, run_id, plan_id):
        def read():
            from src.runtime.postgres_tool_job_store import _job_from_row
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""SELECT j.*, o.record AS operation_record, e.record AS returned_evidence
                    FROM {self._operations.table} o
                    JOIN {self._settings.database.postgres_tool_job_table} j ON j.id=o.origin_job_id
                    LEFT JOIN {self.table} e ON e.origin_job_id=j.id
                    WHERE o.record->'scope'->>'project_id'=%s AND o.record->'scope'->>'run_id'=%s
                      AND o.record->'scope'->>'plan_id'=%s ORDER BY j.created_at,j.id""", (project_id,run_id,plan_id))
                records = []
                for row in cur.fetchall():
                    job = _job_from_row(row)
                    self._operations.validate_origin(row["operation_record"], job)
                    binding = CaseActionBinding.model_validate(job.metadata["case_action_binding"])
                    validate_action_admission_receipt(job, binding, required=True)
                    evidence = row["returned_evidence"]
                    if evidence is not None:
                        intent = job.metadata[INTENT_KEY]
                        if (evidence.get("sha256") != _digest({k:v for k,v in evidence.items() if k != "sha256"})
                            or intent.get("sha256") != _digest({k:v for k,v in intent.items() if k != "sha256"})
                            or evidence.get("origin_job_id") != job.id or evidence.get("binding") != binding.model_dump(mode="json")
                            or evidence.get("intent_sha256") != intent["sha256"]
                            or evidence.get("operation_id") != row["operation_record"]["operation_id"]
                            or evidence.get("observation") != job.metadata.get("tool_output_observation")):
                            raise ValueError("固定对象证据、原返回观察、意图或逻辑操作不同")
                    records.append({"operation": row["operation_record"], "origin_job": job.model_dump(mode="json"),
                        "returned_evidence": evidence,
                        "evidence_state": ("not_declared" if INTENT_KEY not in job.metadata else
                            "awaiting_reliable_return" if evidence is None else "captured"),
                        "returned_evidence_available": evidence is not None})
                return records
        return await asyncio.to_thread(read)
