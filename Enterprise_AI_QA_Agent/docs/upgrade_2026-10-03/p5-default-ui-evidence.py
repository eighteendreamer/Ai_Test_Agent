"""Recover only these owned failed UI trials; export public model response fields."""
import asyncio
import hashlib
import json
from pathlib import Path

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.core.config import get_settings


async def main():
    root = Path(__file__).resolve().parent
    storage = ArtifactStorageService(get_settings())
    try:
        for label in ("positive", "negative"):
            directory = root / "p5-default-ui-live2" / label
            manifest = directory / "downloads-followup.json"
            assert not manifest.exists()
            detail = json.loads((directory / "final-run.json").read_bytes())
            project, run = detail["run"]["project_id"], detail["run"]["id"]
            rows = []
            for job in json.loads((directory / "tool-jobs.json").read_bytes()):
                assert job["metadata"]["project_id"] == project and job["metadata"]["run_id"] == run
                for artifact in job["output_payload"].get("artifacts", []):
                    uri = artifact.get("uri") or artifact.get("path")
                    if not uri or not uri.startswith("rustfs://"): continue
                    raw = (await storage.read_object_uri(uri))["content"]
                    path = directory / "browser-originals" / job["call_id"] / uri.rsplit("/", 1)[-1]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    assert not path.exists(); path.write_bytes(raw)
                    if artifact["type"] == "transcript" and job["input_payload"].get("action") == "command":
                        assert json.loads(raw)[-1]["stdout"] == job["output_payload"]["stdout"]
                    rows.append({"uri": uri, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                        "saved_relative_path": path.relative_to(directory).as_posix(), "call_id": job["call_id"],
                        "local_copy_comparison": "not_required_storage_may_delete_local_copy"})
            correction = detail["results"][0]["actual"].get("execution_checkpoint", {}).get("receipt_correction")
            if correction:
                for kind in ("request", "response"):
                    reference = correction[kind]; uri = reference["uri"]
                    assert f"/case-receipts/{project}/{run}/" in uri
                    raw = (await storage.read_object_uri(uri))["content"]
                    assert hashlib.sha256(raw).hexdigest() == reference["sha256"] and len(raw) == reference["size_bytes"]
                    path = directory / ("correction-" + kind + ".json")
                    if kind == "response":
                        value = json.loads(raw)
                        public = {k: value[k] for k in ("text", "tool_calls", "response_summary") if k in value}
                        path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf8")
                    else: path.write_bytes(raw)
                    rows.append({**reference, "uri": uri, "kind": kind, "original_hash_verified": True,
                        "saved_relative_path": path.relative_to(directory).as_posix(), "response_public_projection": kind == "response"})
            manifest.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf8")
            for row in rows: await storage.delete_object_uri(row["uri"])
            print(label + ": verified and cleaned owned references=" + str(len(rows)))
    finally:
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
