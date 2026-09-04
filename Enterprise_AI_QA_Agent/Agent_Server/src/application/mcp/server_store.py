from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.config import Settings
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.schemas.integration import IntegrationRecord
from src.schemas.mcp_management import (
    MCPServerCreateRequest,
    MCPServerImportRequest,
    MCPServerImportResponse,
    MCPServerRecord,
    MCPServerTransport,
    MCPServerUpdateRequest,
)


class MCPServerStore:
    """Persistent store for external MCP Host servers.

    This is intentionally separate from API integrations. Replacing this JSON
    store with Postgres later should not affect Host/runtime code.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._data_dir = (Path(__file__).resolve().parents[2] / settings.data_dir / "mcp_servers").resolve()
        self._catalog_path = self._data_dir / "catalog.json"
        self._lock = asyncio.Lock()
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def list_servers(self) -> list[MCPServerRecord]:
        async with self._lock:
            catalog = self._load_catalog()
        items = [MCPServerRecord.model_validate(item) for item in catalog]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    async def get_server(self, server_id: str) -> MCPServerRecord:
        async with self._lock:
            catalog = self._load_catalog()
            item = self._find_item(catalog, server_id)
        return MCPServerRecord.model_validate(item)

    async def create_server(self, payload: MCPServerCreateRequest) -> MCPServerRecord:
        now = datetime.now(timezone.utc)
        config = self._config_for_create_payload(payload)
        transport = payload.transport or self._normalize_transport(config)
        record = MCPServerRecord(
            id=str(uuid4()),
            name=self._normalize_text(payload.name) or "",
            enabled=payload.enabled,
            transport=transport,
            purpose=self._purpose_for_payload(payload, config),
            config=config,
            supported_protocols=self._supported_protocols_for_payload(payload, config),
            description=self._purpose_for_payload(payload, config),
            project_name=self._normalize_text(payload.project_name),
            document_url=self._normalize_text(payload.document_url),
            endpoint_url=self._normalize_text(payload.endpoint_url) or self._normalize_text(config.get("endpoint_url") or config.get("url")),
            command=self._normalize_text(payload.command) or self._normalize_text(config.get("command")),
            args=self._normalize_string_list(payload.args or self._as_string_list(config.get("args"))),
            headers=self._normalize_string_map(payload.headers or self._as_string_map(config.get("headers"))),
            env=self._normalize_string_map(payload.env or self._as_string_map(config.get("env"))),
            cwd=self._normalize_text(payload.cwd) or self._normalize_text(config.get("cwd")),
            capabilities=self._normalize_string_list(payload.capabilities),
            provider_key=self._normalize_text(payload.provider_key),
            confirmed_at=payload.confirmed_at,
            metadata=dict(payload.metadata),
            created_at=now,
            updated_at=now,
        )
        self._validate_record(record)
        async with self._lock:
            catalog = self._load_catalog()
            catalog.append(record.model_dump(mode="json"))
            self._save_catalog(catalog)
        return record

    async def update_server(self, server_id: str, payload: MCPServerUpdateRequest) -> MCPServerRecord:
        async with self._lock:
            catalog = self._load_catalog()
            item = self._find_item(catalog, server_id)
            updated = dict(item)
            fields_set = payload.model_fields_set

            for field in (
                "name",
                "purpose",
                "description",
                "project_name",
                "document_url",
                "endpoint_url",
                "command",
                "cwd",
                "provider_key",
            ):
                if field in fields_set:
                    updated[field] = self._normalize_text(getattr(payload, field))

            for field in ("enabled", "transport", "confirmed_at"):
                if field in fields_set:
                    updated[field] = getattr(payload, field)

            if "args" in fields_set and payload.args is not None:
                updated["args"] = self._normalize_string_list(payload.args)
            if "headers" in fields_set and payload.headers is not None:
                updated["headers"] = self._normalize_string_map(payload.headers)
            if "env" in fields_set and payload.env is not None:
                updated["env"] = self._normalize_string_map(payload.env)
            if "capabilities" in fields_set and payload.capabilities is not None:
                updated["capabilities"] = self._normalize_string_list(payload.capabilities)
            if "metadata" in fields_set and payload.metadata is not None:
                updated["metadata"] = dict(payload.metadata)
            if "supported_protocols" in fields_set and payload.supported_protocols is not None:
                updated["supported_protocols"] = self._normalize_protocols(payload.supported_protocols)
            if "config" in fields_set and payload.config is not None:
                config = self._normalize_config(payload.config)
                updated["config"] = config
                updated["transport"] = payload.transport or self._normalize_transport(config)
                updated["purpose"] = self._purpose_for_update(payload, config, updated)
                updated["description"] = updated["purpose"]
                updated["supported_protocols"] = self._supported_protocols_for_payload(payload, config)
                updated["endpoint_url"] = self._normalize_text(config.get("endpoint_url") or config.get("url"))
                updated["command"] = self._normalize_text(config.get("command"))
                updated["args"] = self._normalize_string_list(self._as_string_list(config.get("args")))
                updated["headers"] = self._normalize_string_map(self._as_string_map(config.get("headers")))
                updated["env"] = self._normalize_string_map(self._as_string_map(config.get("env")))
                updated["cwd"] = self._normalize_text(config.get("cwd"))

            updated["updated_at"] = datetime.now(timezone.utc).isoformat()
            record = MCPServerRecord.model_validate(updated)
            self._validate_record(record)
            item.clear()
            item.update(record.model_dump(mode="json"))
            self._save_catalog(catalog)
        return record

    async def delete_server(self, server_id: str) -> dict[str, Any]:
        async with self._lock:
            catalog = self._load_catalog()
            self._find_item(catalog, server_id)
            catalog = [item for item in catalog if str(item.get("id") or "") != server_id]
            self._save_catalog(catalog)
        return {"ok": True, "deleted_id": server_id}

    async def import_servers(self, request: MCPServerImportRequest) -> MCPServerImportResponse:
        entries = self._extract_import_entries(request.payload)
        servers: list[MCPServerRecord] = []
        for name, config in entries:
            servers.append(await self.create_server(self._build_create_request(name, config)))
        return MCPServerImportResponse(servers=servers)

    def _build_create_request(self, name: str, config: dict[str, Any]) -> MCPServerCreateRequest:
        transport = self._normalize_transport(config)
        args = config.get("args") if isinstance(config.get("args"), list) else []
        return MCPServerCreateRequest(
            name=self._normalize_text(config.get("name")) or name,
            enabled=bool(config.get("enabled", True)),
            transport=transport,
            config=self._normalize_config(config),
            purpose=self._normalize_text(config.get("purpose") or config.get("description")),
            supported_protocols=self._protocols_from_config(config),
            description=self._normalize_text(config.get("purpose") or config.get("description")),
            project_name=self._normalize_text(config.get("project_name")),
            document_url=self._normalize_text(config.get("document_url")),
            endpoint_url=self._normalize_text(config.get("endpoint_url") or config.get("url")),
            command=self._normalize_text(config.get("command")),
            args=[str(item) for item in args],
            headers=self._normalize_string_map(config.get("headers") if isinstance(config.get("headers"), dict) else {}),
            env=self._normalize_string_map(config.get("env") if isinstance(config.get("env"), dict) else {}),
            cwd=self._normalize_text(config.get("cwd")),
            capabilities=self._normalize_string_list(
                [str(item) for item in config.get("capabilities", [])]
                if isinstance(config.get("capabilities"), list)
                else []
            ),
            provider_key=self._normalize_text(config.get("provider_key")),
            confirmed_at=None,
            metadata={"imported_from": "mcp_json"},
        )

    def _extract_import_entries(self, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if not isinstance(payload, dict):
            raise ValueError("MCP import payload must be a JSON object.")
        raw_servers = payload.get("mcpServers")
        if isinstance(raw_servers, dict):
            return self._server_entries(raw_servers)
        if any(key in payload for key in ("command", "url", "endpoint_url", "headers", "args")):
            name = self._normalize_text(payload.get("name")) or "imported-mcp"
            return [(name, payload)]
        return self._server_entries(payload)

    def _server_entries(self, raw: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        entries: list[tuple[str, dict[str, Any]]] = []
        for name, value in raw.items():
            if isinstance(value, dict):
                entries.append((str(name), value))
        if not entries:
            raise ValueError("No importable MCP server configs found.")
        return entries

    def _normalize_transport(self, config: dict[str, Any]) -> MCPServerTransport:
        hint = str(config.get("transport") or config.get("transport_type") or config.get("type") or "").strip().lower()
        if hint in {"stdio"} or config.get("command"):
            return "stdio"
        if hint == "sse":
            return "sse"
        if hint in {"http", "streamable-http", "streamable_http"} or config.get("url") or config.get("endpoint_url"):
            return "streamable_http"
        return "streamable_http"

    def _validate_record(self, record: MCPServerRecord) -> None:
        if not record.name:
            raise ValueError("MCP server name is required.")
        config = record.config or self._config_from_record_fields(record)
        transport = record.transport or self._normalize_transport(config)
        if transport == "stdio" and not self._normalize_text(config.get("command")):
            raise ValueError("stdio MCP server requires a command.")
        if transport == "streamable_http" and not self._normalize_text(config.get("endpoint_url") or config.get("url")):
            raise ValueError("Streamable HTTP MCP server requires endpoint_url.")
        if transport == "sse" and not self._normalize_text(config.get("endpoint_url") or config.get("url")):
            raise ValueError("SSE MCP server requires endpoint_url.")

    def _load_catalog(self) -> list[dict[str, Any]]:
        if not self._catalog_path.exists():
            return []
        try:
            raw = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return raw if isinstance(raw, list) else []

    def _save_catalog(self, catalog: list[dict[str, Any]]) -> None:
        self._catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_item(self, catalog: list[dict[str, Any]], server_id: str) -> dict[str, Any]:
        for item in catalog:
            if str(item.get("id") or "") == server_id:
                return item
        raise ValueError(f"MCP server '{server_id}' was not found.")

    def _normalize_text(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _normalize_string_list(self, values: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in values if str(item).strip()]
        return list(dict.fromkeys(normalized))

    def _normalize_string_map(self, value: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, item in value.items():
            normalized_key = str(key).strip()
            normalized_value = str(item).strip()
            if normalized_key and normalized_value:
                normalized[normalized_key] = normalized_value
        return normalized

    def _normalize_config(self, value: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return dict(make_json_safe(value))

    def _config_for_create_payload(self, payload: MCPServerCreateRequest) -> dict[str, Any]:
        config = self._normalize_config(payload.config)
        if config:
            return config
        return self._config_from_payload_fields(payload)

    def _purpose_for_payload(self, payload: MCPServerCreateRequest, config: dict[str, Any]) -> str | None:
        return self._normalize_text(payload.purpose or payload.description or config.get("purpose") or config.get("description"))

    def _purpose_for_update(self, payload: MCPServerUpdateRequest, config: dict[str, Any], current: dict[str, Any]) -> str | None:
        return self._normalize_text(
            payload.purpose
            or payload.description
            or config.get("purpose")
            or config.get("description")
            or current.get("purpose")
            or current.get("description")
        )

    def _supported_protocols_for_payload(self, payload: MCPServerCreateRequest | MCPServerUpdateRequest, config: dict[str, Any]) -> list[MCPServerTransport]:
        protocols = getattr(payload, "supported_protocols", None) or config.get("supported_protocols")
        normalized = self._normalize_protocols(protocols if isinstance(protocols, list) else [])
        return normalized or self._protocols_from_config(config)

    def _protocols_from_config(self, config: dict[str, Any]) -> list[MCPServerTransport]:
        return [self._normalize_transport(config)]

    def _normalize_protocols(self, values: list[Any]) -> list[MCPServerTransport]:
        protocols: list[MCPServerTransport] = []
        for value in values:
            protocol = str(value).strip().lower().replace("-", "_")
            if protocol == "http":
                protocol = "streamable_http"
            if protocol in {"stdio", "streamable_http", "sse"} and protocol not in protocols:
                protocols.append(protocol)  # type: ignore[arg-type]
        return protocols

    def _config_from_payload_fields(self, payload: MCPServerCreateRequest) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if payload.transport:
            config["transport"] = payload.transport
        if payload.endpoint_url:
            config["url"] = payload.endpoint_url
        if payload.command:
            config["command"] = payload.command
        if payload.args:
            config["args"] = list(payload.args)
        if payload.headers:
            config["headers"] = dict(payload.headers)
        if payload.env:
            config["env"] = dict(payload.env)
        if payload.cwd:
            config["cwd"] = payload.cwd
        return self._normalize_config(config)

    def _config_from_record_fields(self, record: MCPServerRecord) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if record.transport:
            config["transport"] = record.transport
        if record.transport == "stdio":
            config["command"] = record.command
            config["args"] = list(record.args)
            config["env"] = dict(record.env)
            if record.cwd:
                config["cwd"] = record.cwd
        else:
            config["url"] = record.endpoint_url
            config["headers"] = dict(record.headers)
        return self._normalize_config({key: value for key, value in config.items() if value not in (None, "", [], {})})

    def _as_string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def _as_string_map(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


class PostgresMCPServerStore:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def list_servers(self) -> list[MCPServerRecord]:
        return await asyncio.to_thread(self._list_servers_sync)

    async def get_server(self, server_id: str) -> MCPServerRecord:
        server = await asyncio.to_thread(self._get_server_sync, server_id)
        if server is None:
            raise ValueError(f"MCP server '{server_id}' was not found.")
        return server

    async def create_server(self, payload: MCPServerCreateRequest) -> MCPServerRecord:
        now = datetime.now(timezone.utc)
        config = _config_for_create_payload(payload)
        transport = payload.transport or _normalize_transport(config)
        record = MCPServerRecord(
            id=str(uuid4()),
            name=_normalize_text(payload.name) or "",
            enabled=payload.enabled,
            transport=transport,
            purpose=_purpose_for_payload(payload, config),
            config=config,
            supported_protocols=_supported_protocols_for_payload(payload, config),
            description=_purpose_for_payload(payload, config),
            project_name=_normalize_text(payload.project_name),
            document_url=_normalize_text(payload.document_url),
            endpoint_url=_normalize_text(payload.endpoint_url) or _normalize_text(config.get("endpoint_url") or config.get("url")),
            command=_normalize_text(payload.command) or _normalize_text(config.get("command")),
            args=_normalize_string_list(payload.args or _as_string_list(config.get("args"))),
            headers=_normalize_string_map(payload.headers or _as_string_map(config.get("headers"))),
            env=_normalize_string_map(payload.env or _as_string_map(config.get("env"))),
            cwd=_normalize_text(payload.cwd) or _normalize_text(config.get("cwd")),
            capabilities=_normalize_string_list(payload.capabilities),
            provider_key=_normalize_text(payload.provider_key),
            confirmed_at=payload.confirmed_at,
            metadata=dict(payload.metadata),
            created_at=now,
            updated_at=now,
        )
        _validate_record(record)
        return await asyncio.to_thread(self._save_record_sync, record)

    async def update_server(self, server_id: str, payload: MCPServerUpdateRequest) -> MCPServerRecord:
        current = await self.get_server(server_id)
        data = current.model_dump(mode="python")
        fields_set = payload.model_fields_set
        for field in (
            "name",
            "purpose",
            "description",
            "project_name",
            "document_url",
            "endpoint_url",
            "command",
            "cwd",
            "provider_key",
        ):
            if field in fields_set:
                data[field] = _normalize_text(getattr(payload, field))
        for field in ("enabled", "transport", "confirmed_at"):
            if field in fields_set:
                data[field] = getattr(payload, field)
        if "args" in fields_set and payload.args is not None:
            data["args"] = _normalize_string_list(payload.args)
        if "headers" in fields_set and payload.headers is not None:
            data["headers"] = _normalize_string_map(payload.headers)
        if "env" in fields_set and payload.env is not None:
            data["env"] = _normalize_string_map(payload.env)
        if "capabilities" in fields_set and payload.capabilities is not None:
            data["capabilities"] = _normalize_string_list(payload.capabilities)
        if "metadata" in fields_set and payload.metadata is not None:
            data["metadata"] = dict(payload.metadata)
        if "supported_protocols" in fields_set and payload.supported_protocols is not None:
            data["supported_protocols"] = _normalize_protocols(payload.supported_protocols)
        if "config" in fields_set and payload.config is not None:
            config = _normalize_config(payload.config)
            data["config"] = config
            data["transport"] = payload.transport or _normalize_transport(config)
            data["purpose"] = _purpose_for_update(payload, config, data)
            data["description"] = data["purpose"]
            data["supported_protocols"] = _supported_protocols_for_payload(payload, config)
            data["endpoint_url"] = _normalize_text(config.get("endpoint_url") or config.get("url"))
            data["command"] = _normalize_text(config.get("command"))
            data["args"] = _normalize_string_list(_as_string_list(config.get("args")))
            data["headers"] = _normalize_string_map(_as_string_map(config.get("headers")))
            data["env"] = _normalize_string_map(_as_string_map(config.get("env")))
            data["cwd"] = _normalize_text(config.get("cwd"))
        data["updated_at"] = datetime.now(timezone.utc)
        record = MCPServerRecord.model_validate(data)
        _validate_record(record)
        return await asyncio.to_thread(self._save_record_sync, record)

    async def delete_server(self, server_id: str) -> dict[str, Any]:
        deleted = await asyncio.to_thread(self._delete_server_sync, server_id)
        if not deleted:
            raise ValueError(f"MCP server '{server_id}' was not found.")
        return {"ok": True, "deleted_id": server_id}

    async def import_servers(self, request: MCPServerImportRequest) -> MCPServerImportResponse:
        parser = MCPServerStore(settings=self._settings)
        entries = parser._extract_import_entries(request.payload)
        servers: list[MCPServerRecord] = []
        for name, config in entries:
            servers.append(await self.create_server(parser._build_create_request(name, config)))
        return MCPServerImportResponse(servers=servers)

    async def migrate_legacy_integrations(self, integrations: list[IntegrationRecord]) -> int:
        count = 0
        existing_legacy_ids = {
            str(item.metadata.get("legacy_integration_id") or "")
            for item in await self.list_servers()
            if isinstance(item.metadata, dict)
        }
        for integration in integrations:
            if integration.kind != "mcp" or integration.id in existing_legacy_ids:
                continue
            transport = "stdio" if integration.transport == "stdio" else "streamable_http"
            await self.create_server(
                MCPServerCreateRequest(
                    name=integration.name,
                    enabled=integration.enabled,
                    transport=transport,
                    purpose=integration.description,
                    description=integration.description,
                    project_name=integration.project_name,
                    document_url=integration.document_url,
                    endpoint_url=integration.endpoint_url or integration.document_url,
                    command=integration.command,
                    headers=integration.headers,
                    env=integration.env,
                    config=_normalize_config(
                        {
                            **({"purpose": integration.description} if integration.description else {}),
                            **(
                                {
                                    "command": integration.command,
                                    "env": dict(integration.env),
                                }
                                if transport == "stdio"
                                else {
                                    "url": integration.endpoint_url or integration.document_url,
                                    "headers": dict(integration.headers),
                                }
                            )
                        }
                    ),
                    supported_protocols=[transport],
                    capabilities=integration.capabilities,
                    provider_key=str(integration.metadata.get("provider_key") or "") or None,
                    confirmed_at=None,
                    metadata={
                        **dict(integration.metadata),
                        "legacy_integration_id": integration.id,
                        "migrated_from": "integrations",
                    },
                )
            )
            count += 1
        return count

    def _initialize_sync(self) -> None:
        table = self._settings.database.postgres_mcp_server_table
        schema_name, table_name = _table_schema_and_name(table)
        with _postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        purpose TEXT NULL,
                        config JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        supported_protocols JSONB NOT NULL DEFAULT '[]'::jsonb,
                        confirmed_at TIMESTAMPTZ NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{{}}'::jsonb")
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS purpose TEXT NULL")
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS supported_protocols JSONB NOT NULL DEFAULT '[]'::jsonb")
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (schema_name, table_name),
                )
                existing_columns = {str(row["column_name"]) for row in cur.fetchall()}
                if {"description", "project_name", "document_url", "endpoint_url", "command", "args", "headers", "env", "cwd", "provider_key"}.issubset(existing_columns):
                    cur.execute(
                        f"""
                        UPDATE {table}
                        SET config = CASE
                            WHEN transport = 'stdio' THEN jsonb_strip_nulls(
                                jsonb_build_object(
                                    'transport', transport,
                                    'description', description,
                                    'project_name', project_name,
                                    'document_url', document_url,
                                    'command', command,
                                    'args', args,
                                    'env', env,
                                    'cwd', cwd,
                                    'provider_key', provider_key
                                )
                            )
                            ELSE jsonb_strip_nulls(
                                jsonb_build_object(
                                    'transport', transport,
                                    'description', description,
                                    'project_name', project_name,
                                    'document_url', document_url,
                                    'url', endpoint_url,
                                    'headers', headers,
                                    'provider_key', provider_key
                                )
                            )
                        END
                        WHERE config = '{{}}'::jsonb
                        """
                    )
                if "description" in existing_columns:
                    cur.execute(f"UPDATE {table} SET purpose = description WHERE purpose IS NULL AND description IS NOT NULL")
                if "transport" in existing_columns:
                    cur.execute(
                        f"""
                        UPDATE {table}
                        SET supported_protocols = CASE
                            WHEN transport = 'stdio' THEN '["stdio"]'::jsonb
                            WHEN transport = 'sse' THEN '["sse"]'::jsonb
                            ELSE '["streamable_http"]'::jsonb
                        END
                        WHERE supported_protocols = '[]'::jsonb
                        """
                    )
                if "capabilities" in existing_columns:
                    cur.execute(
                        f"""
                        UPDATE {table}
                        SET metadata = jsonb_set(metadata, '{{legacy_capabilities}}', capabilities, true)
                        WHERE capabilities <> '[]'::jsonb
                        """
                    )
                cur.execute(
                    f"""
                    ALTER TABLE {table}
                        DROP COLUMN IF EXISTS transport,
                        DROP COLUMN IF EXISTS description,
                        DROP COLUMN IF EXISTS project_name,
                        DROP COLUMN IF EXISTS document_url,
                        DROP COLUMN IF EXISTS endpoint_url,
                        DROP COLUMN IF EXISTS command,
                        DROP COLUMN IF EXISTS args,
                        DROP COLUMN IF EXISTS headers,
                        DROP COLUMN IF EXISTS env,
                        DROP COLUMN IF EXISTS cwd,
                        DROP COLUMN IF EXISTS provider_key,
                        DROP COLUMN IF EXISTS capabilities
                    """
                )
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_enabled ON {table} (enabled)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_updated ON {table} (updated_at DESC)")

    def _list_servers_sync(self) -> list[MCPServerRecord]:
        table = self._settings.database.postgres_mcp_server_table
        with _postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table} ORDER BY updated_at DESC")
                rows = cur.fetchall() or []
        return [_record_from_row(row) for row in rows]

    def _get_server_sync(self, server_id: str) -> MCPServerRecord | None:
        table = self._settings.database.postgres_mcp_server_table
        with _postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table} WHERE id = %s", (server_id,))
                row = cur.fetchone()
        return _record_from_row(row) if row else None

    def _save_record_sync(self, record: MCPServerRecord) -> MCPServerRecord:
        table = self._settings.database.postgres_mcp_server_table
        with _postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {table} (
                        id, name, enabled, purpose, config, supported_protocols,
                        confirmed_at, metadata, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        enabled = EXCLUDED.enabled,
                        purpose = EXCLUDED.purpose,
                        config = EXCLUDED.config,
                        supported_protocols = EXCLUDED.supported_protocols,
                        confirmed_at = EXCLUDED.confirmed_at,
                        metadata = EXCLUDED.metadata,
                        updated_at = EXCLUDED.updated_at
                    """,
                    _record_params(record),
                )
        return record

    def _delete_server_sync(self, server_id: str) -> bool:
        table = self._settings.database.postgres_mcp_server_table
        with _postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table} WHERE id = %s RETURNING id", (server_id,))
                return cur.fetchone() is not None


def _record_params(record: MCPServerRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.name,
        record.enabled,
        record.purpose,
        json.dumps(make_json_safe(record.config), ensure_ascii=False),
        json.dumps(make_json_safe(record.supported_protocols), ensure_ascii=False),
        record.confirmed_at,
        json.dumps(make_json_safe(record.metadata), ensure_ascii=False),
        record.created_at,
        record.updated_at,
    )


def _postgres_connect(settings: Settings):
    from src.infrastructure.postgres_runtime import postgres_connect

    return postgres_connect(settings)


def _table_schema_and_name(table: str) -> tuple[str, str]:
    if "." in table:
        schema_name, table_name = table.split(".", 1)
        return schema_name.strip('"'), table_name.strip('"')
    return "public", table.strip('"')


def _record_from_row(row: dict[str, Any]) -> MCPServerRecord:
    config = _normalize_config(row.get("config") or {})
    supported_protocols = _normalize_protocols(row.get("supported_protocols") if isinstance(row.get("supported_protocols"), list) else [])
    transport = (
        _normalize_transport({"transport": row.get("transport")})
        if row.get("transport")
        else (supported_protocols[0] if supported_protocols else _normalize_transport(config))
    )
    args = row.get("args") if isinstance(row.get("args"), list) else _as_string_list(config.get("args"))
    headers = row.get("headers") if isinstance(row.get("headers"), dict) else _as_string_map(config.get("headers"))
    env = row.get("env") if isinstance(row.get("env"), dict) else _as_string_map(config.get("env"))
    purpose = row.get("purpose") or row.get("description") or _normalize_text(config.get("purpose") or config.get("description"))
    return MCPServerRecord(
        id=row["id"],
        name=row["name"],
        enabled=bool(row.get("enabled", True)),
        transport=transport,
        purpose=purpose,
        config=config,
        supported_protocols=supported_protocols or _protocols_from_config(config),
        description=purpose,
        project_name=row.get("project_name") or _normalize_text(config.get("project_name")),
        document_url=row.get("document_url") or _normalize_text(config.get("document_url")),
        endpoint_url=row.get("endpoint_url") or _normalize_text(config.get("endpoint_url") or config.get("url")),
        command=row.get("command") or _normalize_text(config.get("command")),
        args=list(args or []),
        headers=dict(headers or {}),
        env=dict(env or {}),
        cwd=row.get("cwd") or _normalize_text(config.get("cwd")),
        capabilities=list(row.get("capabilities") or []),
        provider_key=row.get("provider_key") or _normalize_text(config.get("provider_key")),
        confirmed_at=ensure_utc_datetime(row.get("confirmed_at")),
        metadata=dict(row.get("metadata") or {}),
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.now(timezone.utc),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.now(timezone.utc),
    )


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_string_list(values: list[str]) -> list[str]:
    normalized = [str(item).strip() for item in values if str(item).strip()]
    return list(dict.fromkeys(normalized))


def _normalize_string_map(value: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, item in value.items():
        normalized_key = str(key).strip()
        normalized_value = str(item).strip()
        if normalized_key and normalized_value:
            normalized[normalized_key] = normalized_value
    return normalized


def _normalize_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return dict(make_json_safe(value))


def _config_for_create_payload(payload: MCPServerCreateRequest) -> dict[str, Any]:
    config = _normalize_config(payload.config)
    if config:
        return config
    return _config_from_payload_fields(payload)


def _purpose_for_payload(payload: MCPServerCreateRequest, config: dict[str, Any]) -> str | None:
    return _normalize_text(payload.purpose or payload.description or config.get("purpose") or config.get("description"))


def _purpose_for_update(payload: MCPServerUpdateRequest, config: dict[str, Any], current: dict[str, Any]) -> str | None:
    return _normalize_text(
        payload.purpose
        or payload.description
        or config.get("purpose")
        or config.get("description")
        or current.get("purpose")
        or current.get("description")
    )


def _supported_protocols_for_payload(payload: MCPServerCreateRequest | MCPServerUpdateRequest, config: dict[str, Any]) -> list[MCPServerTransport]:
    protocols = getattr(payload, "supported_protocols", None) or config.get("supported_protocols")
    normalized = _normalize_protocols(protocols if isinstance(protocols, list) else [])
    return normalized or _protocols_from_config(config)


def _protocols_from_config(config: dict[str, Any]) -> list[MCPServerTransport]:
    return [_normalize_transport(config)]


def _normalize_protocols(values: list[Any]) -> list[MCPServerTransport]:
    protocols: list[MCPServerTransport] = []
    for value in values:
        protocol = str(value).strip().lower().replace("-", "_")
        if protocol == "http":
            protocol = "streamable_http"
        if protocol in {"stdio", "streamable_http", "sse"} and protocol not in protocols:
            protocols.append(protocol)  # type: ignore[arg-type]
    return protocols


def _config_from_payload_fields(payload: MCPServerCreateRequest) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if payload.transport:
        config["transport"] = payload.transport
    if payload.endpoint_url:
        config["url"] = payload.endpoint_url
    if payload.command:
        config["command"] = payload.command
    if payload.args:
        config["args"] = list(payload.args)
    if payload.headers:
        config["headers"] = dict(payload.headers)
    if payload.env:
        config["env"] = dict(payload.env)
    if payload.cwd:
        config["cwd"] = payload.cwd
    return _normalize_config(config)


def _config_from_record_fields(record: MCPServerRecord) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if record.transport:
        config["transport"] = record.transport
    if record.transport == "stdio":
        config["command"] = record.command
        config["args"] = list(record.args)
        config["env"] = dict(record.env)
        if record.cwd:
            config["cwd"] = record.cwd
    else:
        config["url"] = record.endpoint_url
        config["headers"] = dict(record.headers)
    return _normalize_config({key: value for key, value in config.items() if value not in (None, "", [], {})})


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _as_string_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_transport(config: dict[str, Any]) -> MCPServerTransport:
    hint = str(config.get("transport") or config.get("transport_type") or config.get("type") or "").strip().lower().replace("-", "_")
    if hint in {"stdio"} or config.get("command"):
        return "stdio"
    if hint == "sse":
        return "sse"
    if hint in {"http", "streamable_http"} or config.get("url") or config.get("endpoint_url"):
        return "streamable_http"
    return "streamable_http"


def _validate_record(record: MCPServerRecord) -> None:
    if not record.name:
        raise ValueError("MCP server name is required.")
    config = record.config or _config_from_record_fields(record)
    transport = record.transport or _normalize_transport(config)
    if transport == "stdio" and not _normalize_text(config.get("command")):
        raise ValueError("stdio MCP server requires a command.")
    if transport == "streamable_http" and not _normalize_text(config.get("endpoint_url") or config.get("url")):
        raise ValueError("Streamable HTTP MCP server requires endpoint_url.")
    if transport == "sse" and not _normalize_text(config.get("endpoint_url") or config.get("url")):
        raise ValueError("SSE MCP server requires endpoint_url.")
