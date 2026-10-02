"""Cross-check feature evidence, local links and unchanged product source."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parents[1]
sys.path.insert(0, str(ROOT / "Agent_Server/scripts"))
from system_acceptance_inventory import product_fingerprint


def main():
    run = Path((DOCS / "current-run.txt").read_text(encoding="utf-8").strip())
    catalog = json.loads((DOCS / "functional_catalog.json").read_text(encoding="utf-8"))
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run / "final-records").glob("*.json"))]
    assert {f["id"] for f in catalog} == {r["id"] for r in records}
    evidence = {}
    for path in run.glob("gates/*/report.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        evidence[path.resolve()] = {c["nodeid"]: c for c in report["cases"]}
    references = 0
    for record in records:
        assert record["acceptance_status"] in {"partial", "catalog_only"}
        for case in record["cases"]:
            source = evidence[(run / "final-records" / case["evidence"]).resolve()][case["nodeid"]]
            assert case["outcome"] == source["outcome"] and case["phases"] == source["phases"]
            references += 1
    links = 0
    for path in DOCS.rglob("*.md"):
        content = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", content):
            if target.startswith(("https:", "http:", "#")):
                continue
            assert (path.parent / target).exists(), f"Broken link: {path}: {target}"
            links += 1
    baseline = json.loads((DOCS / "inventory.json").read_text(encoding="utf-8"))
    current = product_fingerprint()
    assert current["sha256"] == baseline["product_fingerprint"]["sha256"]
    assert json.loads((DOCS / "diagram-validation.json").read_text(encoding="utf-8"))["passed"]
    files = [p for p in DOCS.rglob("*") if p.is_file() and "__pycache__" not in p.parts
             and p.name != "delivery-verification.json"]
    files += list(ROOT.joinpath("Agent_Server/scripts").glob("*system_acceptance*.py"))
    report = {"verified_at_utc": datetime.now(UTC).isoformat(), "features": len(records),
              "case_references_checked": references, "local_links_checked": links,
              "product_source_unchanged": True, "status": "passed",
              "files": [{"path": p.relative_to(ROOT).as_posix(),
                         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(files)]}
    (DOCS / "delivery-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "files"}))


if __name__ == "__main__":
    main()
