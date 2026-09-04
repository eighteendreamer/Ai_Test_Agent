from __future__ import annotations

from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.schemas.sponsor import SponsorPublic, SponsorRecord


DEFAULT_SPONSORS: tuple[SponsorRecord, ...] = (
    SponsorRecord(
        name="E-API",
        logo_file="e-api.png",
        website_url="https://api.ewo.so/",
        sponsor_type="中转站",
        sort_order=0,
        enabled=True,
    ),
)


class MySQLSponsorConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._table_exists(cur):
                    self._create_table(cur)
                    conn.commit()
                self._seed_default_sponsors(cur)
            conn.commit()

    def list_enabled(self) -> list[SponsorPublic]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, name, logo_file, website_url, sponsor_type, sort_order,
                           enabled, description, created_at, updated_at
                    FROM `{self._settings.database.sponsor_config_table}`
                    WHERE enabled=1
                    ORDER BY sort_order ASC, id ASC
                    """
                )
                rows = cur.fetchall()
        return [self._to_public(self._row_to_record(row)) for row in rows]

    def _table_exists(self, cur) -> bool:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.tables
            WHERE table_schema=%s AND table_name=%s
            """,
            (self._settings.database.mysql_database, self._settings.database.sponsor_config_table),
        )
        return bool(cur.fetchone()["total"])

    def _create_table(self, cur) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._settings.database.sponsor_config_table}` (
                `id` BIGINT NOT NULL AUTO_INCREMENT,
                `name` VARCHAR(120) NOT NULL,
                `logo_file` VARCHAR(255) NOT NULL,
                `website_url` VARCHAR(1024) NOT NULL,
                `sponsor_type` VARCHAR(64) NOT NULL DEFAULT '',
                `sort_order` INT NOT NULL DEFAULT 0,
                `enabled` TINYINT(1) NOT NULL DEFAULT 1,
                `description` TEXT NULL,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uniq_sponsor_name` (`name`)
            ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.database.mysql_charset}
            """
        )

    def _seed_default_sponsors(self, cur) -> None:
        cur.execute(
            f"SELECT COUNT(*) AS total FROM `{self._settings.database.sponsor_config_table}`"
        )
        if int(cur.fetchone()["total"]) > 0:
            return
        for sponsor in DEFAULT_SPONSORS:
            cur.execute(
                f"""
                INSERT INTO `{self._settings.database.sponsor_config_table}`
                (name, logo_file, website_url, sponsor_type, sort_order, enabled, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sponsor.name,
                    sponsor.logo_file,
                    sponsor.website_url,
                    sponsor.sponsor_type,
                    sponsor.sort_order,
                    int(sponsor.enabled),
                    sponsor.description,
                ),
            )

    def _row_to_record(self, row: dict | None) -> SponsorRecord:
        if row is None:
            raise KeyError("sponsor config row not found")
        return SponsorRecord(
            id=int(row["id"]),
            name=str(row["name"]).strip(),
            logo_file=str(row["logo_file"]).strip(),
            website_url=str(row["website_url"]).strip(),
            sponsor_type=str(row.get("sponsor_type") or "").strip(),
            sort_order=int(row.get("sort_order") or 0),
            enabled=bool(row.get("enabled")),
            description=_clean_optional(row.get("description")),
            created_at=_to_datetime(row.get("created_at")),
            updated_at=_to_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _to_public(record: SponsorRecord) -> SponsorPublic:
        logo_file = record.logo_file
        if logo_file.startswith("http://") or logo_file.startswith("https://"):
            logo_url = logo_file
        else:
            logo_url = f"/sponsors/{logo_file.lstrip('/')}"
        return SponsorPublic(
            id=int(record.id or 0),
            name=record.name,
            logo_url=logo_url,
            website_url=record.website_url,
            sponsor_type=record.sponsor_type,
            description=record.description,
        )

    def _connect(self):
        return mysql_raw_connection(self._settings)


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
