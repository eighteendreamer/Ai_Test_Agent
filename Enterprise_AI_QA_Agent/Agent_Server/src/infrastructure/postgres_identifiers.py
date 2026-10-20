"""PostgreSQL relation names shared by ToolJob tables and their evidence tables."""
from __future__ import annotations

import hashlib
import logging
import re

from sqlalchemy.dialects.postgresql import dialect


logger = logging.getLogger(__name__)
_preparer = dialect().identifier_preparer
_part = r'"(?:[^"\x00]|"")+"|[A-Za-z_\x80-\U0010ffff][A-Za-z_0-9$\x80-\U0010ffff]*'
_relation = re.compile(rf'\s*(?:{_part})\s*(?:\.\s*(?:{_part})\s*)*')
_token = re.compile(_part)


def postgres_relation_parts(value: str) -> tuple[str, ...]:
    # Same case/quoting rules as Alembic 0002._identifier, with full-input
    # validation before using a configuration identifier in runtime SQL.
    # PostgreSQL 17, 4.1.1: quoted dots stay within a name, and "" escapes ".
    if not isinstance(value, str) or not _relation.fullmatch(value):
        raise ValueError(f"Invalid PostgreSQL relation identifier: {value!r}")
    return tuple(part[1:-1].replace('""', '"') if part.startswith('"') else part.lower()
        for part in _token.findall(value))


def postgres_relation_sql(parts: tuple[str, ...]) -> str:
    return '.'.join(_preparer.quote(part) for part in parts)


def postgres_index_name(table: str, suffix: str) -> str:
    # CREATE INDEX names are unqualified; PostgreSQL puts the index in the
    # table's schema. Match the names already frozen in Alembic 0002.
    return _preparer.quote(f"idx_{postgres_relation_parts(table)[-1]}_{suffix}")


def _derived_table_name(source_relation: str, suffix: str) -> str:
    parts = postgres_relation_parts(source_relation)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", source_relation):
        # Preserve the exact original names, including hashes of uppercase
        # configuration spellings. Existing immutable evidence must stay found.
        table = source_relation[:25] + f"_{suffix}_" + hashlib.sha256(source_relation.encode()).hexdigest()[:16]
    else:
        canonical = postgres_relation_sql(parts)
        prefix = re.sub(r"[^a-z0-9_]", "_", parts[-1].lower())[:25]
        name = prefix + f"_{suffix}_" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
        table = postgres_relation_sql((*parts[:-1], name))
    return table


def derived_postgres_table(source_relation: str, suffix: str) -> str:
    """Return a stable, schema-aware relation name derived from another relation."""
    table = _derived_table_name(source_relation, suffix)
    logger.debug("postgres_derived_table_resolved", extra={"source_relation": source_relation,
        "derived_relation": table, "table_role": suffix})
    return table


def derived_tool_table(jobs: str, suffix: str) -> str:
    table = _derived_table_name(jobs, suffix)
    logger.debug("postgres_tool_evidence_table_resolved", extra={"source_table": jobs,
        "derived_table": table, "table_role": suffix})
    return table
