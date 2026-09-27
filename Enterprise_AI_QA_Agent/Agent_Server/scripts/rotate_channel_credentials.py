from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.fernet import Fernet

from src.core.config import Settings
from src.infrastructure.channel_config_store import ChannelCredentialCodec
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection


def _legacy_codec(raw_key: str) -> ChannelCredentialCodec:
    raw_key = raw_key.strip()
    if not raw_key:
        raise ValueError("CHANNEL_CREDENTIAL_LEGACY_KEY is required for rotation.")
    try:
        Fernet(raw_key.encode("utf-8"))
        key = raw_key
    except (TypeError, ValueError):
        key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest()).decode("ascii")
    return ChannelCredentialCodec(key)


def rotate_channel_credentials(settings: Settings, legacy_key: str, *, apply: bool) -> dict[str, int]:
    new_codec = ChannelCredentialCodec(settings.database.channel_credential_encryption_key)
    if not new_codec.available:
        raise ValueError("A new CHANNEL_CREDENTIAL_ENCRYPTION_KEY is required for rotation.")
    legacy_codec = _legacy_codec(legacy_key)
    result = {"encrypted_rows": 0, "already_current": 0, "to_rotate": 0, "rotated": 0}
    table = settings.database.channel_config_table

    with mysql_raw_connection(settings) as conn:
        with conn.cursor() as cur:
            lock = " FOR UPDATE" if apply else ""
            cur.execute(f"SELECT id, credential_ciphertext FROM `{table}` WHERE credential_ciphertext IS NOT NULL{lock}")
            rows = cur.fetchall()
            updates: list[tuple[int, str, str]] = []
            for row in rows:
                config_id = int(row["id"])
                ciphertext = str(row["credential_ciphertext"])
                result["encrypted_rows"] += 1
                try:
                    new_codec.decrypt(ciphertext)
                except ValueError:
                    try:
                        credentials = legacy_codec.decrypt(ciphertext)
                    except ValueError as exc:
                        raise ValueError(f"Channel config {config_id} cannot be decrypted by either key; no rows changed.") from exc
                    new_ciphertext = new_codec.encrypt(credentials)
                    if new_codec.decrypt(new_ciphertext) != credentials:
                        raise ValueError(f"Channel config {config_id} failed rotation verification; no rows changed.")
                    updates.append((config_id, ciphertext, new_ciphertext))
                else:
                    result["already_current"] += 1
            result["to_rotate"] = len(updates)
            if apply:
                for config_id, old_ciphertext, new_ciphertext in updates:
                    cur.execute(
                        f"UPDATE `{table}` SET credential_ciphertext=%s "
                        "WHERE id=%s AND credential_ciphertext=%s",
                        (new_ciphertext, config_id, old_ciphertext),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError(f"Channel config {config_id} changed during rotation; transaction rolled back.")
                    result["rotated"] += 1
        if apply:
            conn.commit()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or rotate legacy MySQL channel credentials to a Fernet key.")
    parser.add_argument("--apply", action="store_true", help="Write all verified replacements in one transaction.")
    args = parser.parse_args()
    result = rotate_channel_credentials(
        Settings(), os.environ.get("CHANNEL_CREDENTIAL_LEGACY_KEY", ""), apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
