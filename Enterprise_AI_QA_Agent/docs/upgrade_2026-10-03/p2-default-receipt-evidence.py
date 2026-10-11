"""Retain public recovery facts and verify owned receipt originals after live runs."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.core.config import get_settings

ROOT = Path(__file__).resolve().parent
TEMP = Path(r"C:\Users\32734\AppData\Local\Temp\pytest-of-32734")


def save(directory, name, value):
    (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    # Exact known-owned fixture directories, never enumerate unrelated databases.
    for number, trial in [(1524, 5)]:
        source = TEMP / f"pytest-{number}" / "test_default_model_receipt_cor0"
        target = ROOT / f"p2-default-receipt-process-live{trial}"
        snapshot = json.loads((source / "postgres-finally.json").read_bytes())
        public = {key: rows for key, rows in snapshot.items() if key != "postgres_snapshot_table"}
        public["public_snapshots"] = [{key: row.get(key) for key in ("id", "session_id", "version", "stage")}
            | {key: row.get("graph_state", {}).get(key) for key in
                ("model_response_text", "final_response", "model_response_summary", "model_call_count", "max_iterations")}
            for row in snapshot.get("postgres_snapshot_table", [])]
        save(target, "postgres-public-facts.json", public)
        for path in source.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "health.md"}:
                shutil.copyfile(path, target / path.name)
    objects = ArtifactStorageService(get_settings())
    try:
        for name in ("p2-default-minimal-case-live7", "p2-default-receipt-process-live5"):
            directory = ROOT / name
            run = json.loads((directory / ("final-run.json" if "minimal" in name else "last-run.json")).read_bytes())
            actual = run["results"][0]["actual"]
            correction = actual.get("receipt_correction") or actual["execution_checkpoint"]["receipt_correction"]
            observations = []
            for kind in ("request", "response"):
                ref = correction.get(kind)
                if ref is None:
                    continue
                raw = (await objects.read_object_uri(ref["uri"]))["content"]
                assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
                if kind == "request":
                    (directory / "receipt-request-original.json").write_bytes(raw)
                else:
                    value = json.loads(raw)
                    save(directory, "receipt-response-public.json", {key: value.get(key) for key in
                        ("text", "tool_calls", "response_summary")})
                observations.append({"kind": kind, **ref, "download_hash_and_size_verified": True,
                    "raw_response_private_fields_exported": False})
            save(directory, "receipt-downloads.json", observations)
    finally:
        await objects.close()


if __name__ == "__main__":
    asyncio.run(main())
