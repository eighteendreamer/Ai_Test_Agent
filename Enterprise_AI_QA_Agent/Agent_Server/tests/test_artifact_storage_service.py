from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.artifacts.artifact_storage_service import ArtifactStorageService


class _ClientError(Exception):
    def __init__(self, status_code: int, code: str = "NotFound") -> None:
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status_code},
            "Error": {"Code": code},
        }
        super().__init__(f"S3 error {status_code}: {code}")


class _Body:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.closed = False

    def read(self) -> bytes:
        return self._content

    def close(self) -> None:
        self.closed = True


class _RustFSClient:
    exceptions = SimpleNamespace(ClientError=_ClientError)

    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def head_bucket(self, *, Bucket: str) -> None:
        if Bucket not in self.buckets:
            raise _ClientError(404, "NoSuchBucket")

    def create_bucket(self, *, Bucket: str) -> None:
        self.buckets.add(Bucket)

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, str],
    ) -> None:
        self.objects[(bucket, key)] = (Path(filename).read_bytes(), ExtraArgs["ContentType"])

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        content, content_type = self.objects[(Bucket, Key)]
        return {
            "Body": _Body(content),
            "ContentType": content_type,
            "ContentLength": len(content),
            "ETag": '"test-etag"',
        }

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


def _settings(tmp_path: Path, *, backend: str = "rustfs") -> SimpleNamespace:
    return SimpleNamespace(
        storage=SimpleNamespace(
            artifact_root_dir=str(tmp_path / "artifacts"),
            artifact_storage_backend=backend,
            artifact_keep_local_copy=True,
            rustfs_endpoint="127.0.0.1:9000",
            rustfs_access_key="rustfsadmin",
            rustfs_secret_key="rustfsadmin",
            rustfs_bucket="qa-agent",
            rustfs_secure=False,
        ),
    )


def test_rustfs_uploaded_bytes_round_trip_and_delete(tmp_path: Path) -> None:
    client = _RustFSClient()
    service = ArtifactStorageService(_settings(tmp_path))
    service._rustfs_client = lambda: client

    stored = asyncio.run(
        service.store_uploaded_bytes(
            content=b"report body",
            filename="report.txt",
            object_prefix="sessions/session-1",
            content_type="text/plain",
        )
    )

    assert stored == {
        "path": "rustfs://qa-agent/sessions/session-1/report.txt",
        "uri": "rustfs://qa-agent/sessions/session-1/report.txt",
        "storage_backend": "rustfs",
        "bucket": "qa-agent",
        "object_name": "sessions/session-1/report.txt",
        "content_type": "text/plain",
        "original_filename": "report.txt",
        "size_bytes": 11,
    }
    assert client.buckets == {"qa-agent"}

    loaded = asyncio.run(service.read_object_uri(stored["uri"]))
    assert loaded["content"] == b"report body"
    assert loaded["content_type"] == "text/plain"
    assert loaded["etag"] == "test-etag"

    asyncio.run(service.delete_object_uri(stored["uri"]))
    assert client.objects == {}


def test_rustfs_rewrites_file_artifact_and_rejects_minio_uri(tmp_path: Path) -> None:
    client = _RustFSClient()
    service = ArtifactStorageService(_settings(tmp_path))
    service._rustfs_client = lambda: client
    artifact = tmp_path / "result.json"
    artifact.write_text('{"ok": true}', encoding="utf-8")

    output = asyncio.run(
        service.store_output_artifacts(
            {"artifacts": [{"path": str(artifact), "label": "result"}]},
            session_id="session-1",
            turn_id="turn-1",
            tool_key="api-test",
        )
    )

    stored = output["artifacts"][0]
    assert stored["path"] == "rustfs://qa-agent/session-1/turn-1/api-test/result.json"
    assert stored["storage_backend"] == "rustfs"
    assert artifact.exists()

    with pytest.raises(ValueError, match="Unsupported artifact URI"):
        asyncio.run(service.read_object_uri("minio://qa-agent/result.json"))
    with pytest.raises(ValueError, match="Unsupported artifact URI"):
        asyncio.run(service.delete_object_uri("minio://qa-agent/result.json"))


def test_non_rustfs_backend_is_disabled(tmp_path: Path) -> None:
    service = ArtifactStorageService(_settings(tmp_path, backend="local"))

    assert service.enabled is False
    with pytest.raises(RuntimeError, match="not enabled"):
        asyncio.run(
            service.store_uploaded_bytes(
                content=b"data",
                filename="data.bin",
                object_prefix="test",
            )
        )


def test_rustfs_client_uses_configured_s3_endpoint(tmp_path: Path) -> None:
    service = ArtifactStorageService(_settings(tmp_path))

    client = service._rustfs_client()

    assert client.meta.endpoint_url == "http://127.0.0.1:9000"
    assert client.meta.config.signature_version == "s3v4"
    assert client.meta.config.s3 == {"addressing_style": "path"}
