"""Audit the existing additive model-store initializer without exposing values."""
from hashlib import sha256
import json
from pathlib import Path

from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.model_config import model_invocation_parameters


def digest(rows):
    return sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":")).encode()).hexdigest()


def main():
    directory = Path(__file__).with_name("p2-default-current-migration")
    directory.mkdir(exist_ok=False)
    store = MySQLModelConfigStore(get_settings())
    table = store._settings.database.llm_model_table
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        columns = [row["Field"] for row in cur.fetchall()]
        cur.execute(f"SELECT * FROM `{table}` ORDER BY id")
        before = [dict(row) for row in cur.fetchall()]
        # The initializer has older normalization steps. Require them to be
        # already satisfied before using it against current configuration.
        cur.execute(f"SELECT COUNT(*) AS total FROM `{table}` WHERE applications IS NULL "
            "OR JSON_LENGTH(applications) <> 1 OR transport IS NULL OR TRIM(transport)='' "
            "OR (is_active=1 AND active_application IS NULL "
            "AND JSON_CONTAINS(applications, JSON_QUOTE('task_execution')))")
        assert cur.fetchone()["total"] == 0, "Earlier normalization requires separate review"
    report = {"table": table, "before_columns": columns, "row_count": len(before),
        "before_rows_sha256": digest(before), "values_saved": False}
    path = directory / "facts.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for attempt in range(2):
        store.initialize()
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'request_profile'")
            profile_column = cur.fetchone()
            assert profile_column and profile_column["Type"].lower() == "json"
            cur.execute(f"SELECT * FROM `{table}` ORDER BY id")
            after = [dict(row) for row in cur.fetchall()]
        original_projection = [{key: row[key] for key in columns} for row in after]
        assert original_projection == before, "Existing model configuration changed"
        if "request_profile" not in columns:
            assert all(row["request_profile"] is None for row in after)
        report[f"initializer_{attempt + 1}_old_rows_equal"] = True
        report[f"initializer_{attempt + 1}_projection_sha256"] = digest(original_projection)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["default_parameters"] = model_invocation_parameters(store.get_default_active())
    report["request_profile_added"] = "request_profile" not in columns
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
