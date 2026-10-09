"""Preserve and remove only receipt objects from this batch's failed fixtures."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"
sys.path.insert(0, str(ROOT / "Agent_Server"))
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.core.config import get_settings


async def main():
    output = DOCS / "p0-receipt-process-failure-cleanup"
    output.mkdir(exist_ok=False)
    objects = ArtifactStorageService(get_settings())
    results = []
    try:
        for boundary in ("invoking", "response"):
            source = DOCS / "p0-receipt-process-recovery" / boundary / "postgres-finally.json"
            data = json.loads(source.read_bytes())
            assert len(data["postgres_test_run_attempt_table"]) == 1
            payload = data["postgres_test_run_attempt_table"][0]["record"]["checkpoint_payload"]
            correction = payload["receipt_correction"]
            prefix = (f"case-receipts/{payload['project_id']}/{payload['run_id']}/{payload['run_item_id']}/"
                f"{payload['execution_attempt_id']}/{correction['call_id']}/")
            for kind in ("request", "response"):
                ref = correction.get(kind)
                if ref is None:
                    continue
                key = ref["uri"].removeprefix("rustfs://").partition("/")[2]
                assert key == prefix + ref["sha256"] + "-" + kind + ".json"
                raw = (await objects.read_object_uri(ref["uri"]))["content"]
                assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
                filename = boundary + "-" + kind + ".json"
                (output / filename).write_bytes(raw)
                entry = {"source": str(source.relative_to(ROOT)), "kind": kind, "file": filename,
                    "reference": ref, "download_verified": True, "delete_not_found_status": None}
                results.append(entry)
                await objects.delete_object_uri(ref["uri"])
                try:
                    await objects.read_object_uri(ref["uri"])
                except RuntimeError as exc:
                    status = getattr(exc.__cause__, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
                    assert status == 404, "unexpected object verification failure"
                    entry["delete_not_found_status"] = status
                else:
                    raise AssertionError("owned failed-test receipt remains after deletion")
                (output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        assert len(results) == 3
        print(json.dumps({"downloaded_verified": len(results), "removed_and_404_verified": len(results)}))
    finally:
        await objects.close()


if __name__ == "__main__":
    asyncio.run(main())
