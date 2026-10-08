"""Recover only artifacts/tables proven to belong to this turn's failed fixtures."""
import asyncio
import hashlib
import json
import re
from pathlib import Path

from src.core.config import get_settings
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.infrastructure.postgres_runtime import postgres_connect

ROOT = Path(__file__).resolve().parent


async def main():
    settings = get_settings()
    objects = ArtifactStorageService(settings)
    graph = RecordingGraphStore(settings)
    report = {"scope": "only current owned failed fixture IDs", "screenshots": [], "tables": [], "graph": []}
    try:
        # Screenshot references come from these fixtures' own original HTTP replies.
        for name in ("p0-recording-embedded-visible-capture", "p0-recording-embedded-verified"):
            folder = ROOT / name
            session = json.loads((folder / "pg-session-final.json").read_text(encoding="utf-8"))
            for source in folder.glob("screenshot-[0-9]*.json"):
                uri = json.loads(source.read_text())["ref"]
                assert uri.startswith("rustfs://") and f"/recordings/{session['id']}/" in uri
                already_recovered = folder / "screenshot-storage.json"
                if already_recovered.exists():
                    # This previous teardown removed its screenshot before its later KeyError.
                    raw = (folder / "screenshot-rustfs.png").read_bytes()
                    fixed = json.loads(already_recovered.read_text())
                    assert fixed["uri"] == uri and fixed["sha256"] == hashlib.sha256(raw).hexdigest()
                    report["screenshots"].append({"uri": uri, "already_removed": True, "sha256": fixed["sha256"]})
                else:
                    destination = ROOT / f"p0-recording-embedded-recovered-{session['id']}.png"
                    # The first attempt preserved/deleted this object before its
                    # parameter-less LIKE query failed. Keep that original byte copy.
                    if destination.exists():
                        raw = destination.read_bytes()
                        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
                    else:
                        raw = (await objects.read_object_uri(uri))["content"]
                        destination.write_bytes(raw)
                        await objects.delete_object_uri(uri)
                    report["screenshots"].append({"uri": uri, "removed": True, "file": destination.name,
                        "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)})

        # One test completed assertions but its cleanup glob included screenshot-storage.json.
        session = json.loads((ROOT / "p0-recording-embedded-verified/pg-session-shutdown.json").read_text())
        with postgres_connect(settings) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE %s", ("up_rs_%",))
            candidates = [row["tablename"] for row in cursor.fetchall()]
            for table in candidates:
                if not re.fullmatch(r"up_rs_[0-9a-f]{12}", table):
                    continue
                cursor.execute(f'SELECT id, project_id FROM "{table}" WHERE id=%s', (session["id"],))
                match = cursor.fetchone()
                if match is None:
                    continue
                assert match["project_id"] == session["project_id"]
                cursor.execute(f'SELECT count(*) AS count FROM "{table}"')
                assert cursor.fetchone()["count"] == 1
                events = table.replace("up_rs_", "up_re_", 1)
                cursor.execute(f'SELECT DISTINCT recording_id FROM "{events}"')
                assert {row["recording_id"] for row in cursor.fetchall()} <= {session["id"]}
                cursor.execute(f'DROP TABLE "{events}"')
                cursor.execute(f'DROP TABLE "{table}"')
                report["tables"].append({"session_table": table, "event_table": events, "recording_id": session["id"]})
        assert len(report["tables"]) == 1
        await asyncio.to_thread(graph._provider.execute_write, "MATCH (n {project_id: $project_id}) DETACH DELETE n", {"project_id": session["project_id"]})
        rows = await asyncio.to_thread(graph._provider.execute, "MATCH (n {project_id: $project_id}) RETURN count(n) AS count", {"project_id": session["project_id"]})
        assert rows[0]["count"] == 0
        report["graph"].append({"project_id": session["project_id"], "remaining_nodes": 0})
        (ROOT / "p0-recording-embedded-cleanup.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Owned failed-fixture cleanup verified; unrelated tables and objects untouched.")
    finally:
        await graph.close()
        await objects.close()


asyncio.run(main())
