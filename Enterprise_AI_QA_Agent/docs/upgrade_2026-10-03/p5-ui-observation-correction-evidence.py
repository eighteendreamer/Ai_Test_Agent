"""Verify and clean only this batch's owned correction objects; export public fields."""
import asyncio
import hashlib
import json
from pathlib import Path

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.core.config import get_settings


async def main():
    root = Path(__file__).resolve().parent / "p5-ui-observation-default5"
    directories = [root / name for name in ("positive", "negative")]
    assert all((p / "final-run.json").exists() and not (p / "correction-downloads.json").exists() for p in directories)
    storage = ArtifactStorageService(get_settings())
    try:
        for directory in directories:
            detail = json.loads((directory / "final-run.json").read_bytes())
            project, run = detail["run"]["project_id"], detail["run"]["id"]
            rows = []
            for result in detail["results"]:
                correction = result["actual"].get("execution_checkpoint", {}).get("receipt_correction")
                if not correction: continue
                for kind in ("request", "response"):
                    reference = correction.get(kind)
                    if not reference: continue
                    uri = reference["uri"]
                    assert f"/case-receipts/{project}/{run}/" in uri
                    raw = (await storage.read_object_uri(uri))["content"]
                    assert hashlib.sha256(raw).hexdigest() == reference["sha256"] and len(raw) == reference["size_bytes"]
                    path = directory / ("correction-" + kind + ".json")
                    assert not path.exists()
                    if kind == "response":
                        value = json.loads(raw)
                        public = {k: value[k] for k in ("text", "tool_calls", "response_summary") if k in value}
                        path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf8")
                    else: path.write_bytes(raw)
                    rows.append({**reference, "uri": uri, "kind": kind, "original_hash_verified": True,
                        "saved_relative_path": path.name, "response_public_projection": kind == "response"})
            (directory / "correction-downloads.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf8")
            for row in rows: await storage.delete_object_uri(row["uri"])
            print(directory.name + ": verified and cleaned owned correction objects=" + str(len(rows)))
    finally:
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
