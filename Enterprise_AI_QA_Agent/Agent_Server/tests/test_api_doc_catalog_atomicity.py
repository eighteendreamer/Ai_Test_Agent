from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.application.documents.api_doc_store import JsonApiDocStore
from src.schemas.api_docs import ApiDocRecord


def _run(awaitable):
    return asyncio.run(awaitable)


def _record(doc_id: str = "doc-1", *, title: str = "Original") -> ApiDocRecord:
    now = datetime.now(UTC)
    return ApiDocRecord(
        id=doc_id,
        title=title,
        filename="openapi.json",
        storage_uri=f"memory://{doc_id}",
        uploaded_at=now,
        updated_at=now,
    )


def test_malformed_catalog_is_reported_instead_of_loading_as_empty(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text('[{"id":', encoding="utf-8")
    store = JsonApiDocStore(catalog)

    with pytest.raises(ValueError, match="catalog.json"):
        _run(store.initialize())

    assert _run(store.list()) == []


def test_invalid_item_rejects_entire_catalog_instead_of_partial_load(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps([_record().model_dump(mode="json"), {"id": "incomplete"}]),
        encoding="utf-8",
    )
    store = JsonApiDocStore(catalog)

    with pytest.raises(ValueError, match="item 1"):
        _run(store.initialize())

    assert _run(store.list()) == []


def test_update_never_overwrites_open_catalog_file(tmp_path: Path, monkeypatch) -> None:
    catalog = tmp_path / "catalog.json"
    store = JsonApiDocStore(catalog)
    _run(store.create(_record()))
    original_write_text = Path.write_text

    def fail_direct_overwrite(path: Path, data: str, *args, **kwargs):
        if path == catalog:
            with path.open("w", encoding="utf-8") as output:
                output.write(data[:10])
            raise OSError("simulated interrupted direct write")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_direct_overwrite)
    _run(store.update(_record(title="Updated")))

    assert json.loads(catalog.read_text(encoding="utf-8"))[0]["title"] == "Updated"
    restarted = JsonApiDocStore(catalog)
    _run(restarted.initialize())
    assert _run(restarted.get("doc-1")).title == "Updated"


@pytest.mark.parametrize(
    ("operation", "expected_ids"),
    [
        ("create", ["doc-1", "doc-2"]),
        ("update", ["doc-1"]),
        ("delete", []),
    ],
)
def test_failed_replacement_preserves_disk_and_memory(
    tmp_path: Path, monkeypatch, operation: str, expected_ids: list[str]
) -> None:
    catalog = tmp_path / "catalog.json"
    store = JsonApiDocStore(catalog)
    _run(store.create(_record()))
    original_bytes = catalog.read_bytes()

    def fail_replace(source, destination):
        assert Path(destination) == catalog
        pending = json.loads(Path(source).read_text(encoding="utf-8"))
        assert sorted(item["id"] for item in pending) == expected_ids
        if operation == "update":
            assert pending[0]["title"] == "Updated"
        assert catalog.read_bytes() == original_bytes
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        if operation == "create":
            _run(store.create(_record("doc-2")))
        elif operation == "update":
            _run(store.update(_record(title="Updated")))
        else:
            _run(store.delete("doc-1"))

    assert catalog.read_bytes() == original_bytes
    assert _run(store.get("doc-1")).title == "Original"
    assert _run(store.get("doc-2")) is None
    assert list(tmp_path.glob(".catalog.json.*.tmp")) == []
