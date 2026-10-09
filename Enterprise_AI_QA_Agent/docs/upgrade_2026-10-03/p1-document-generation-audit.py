"""Ordinary-document receipts, immutable object bytes and preserved prior evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/upgrade_2026-10-03"


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--sync", action="store_true"); args = parser.parse_args()
    historical, protected, manifests = {}, {}, {}
    for name in ("p5-perf-typed-validation.json", "p5-perf-body-validation.json", "p5-jmeter-dependencies-validation.json",
        "p5-perf-project-validation.json", "p5-jmeter-report-validation.json"):
        path = DOCS / name; value = read(path)
        historical.update(value["artifact_hashes"]); protected.update(value["protected_files_sha256"])
        manifests[name] = sha(path)
    for relative, expected in {**historical, **protected}.items(): assert sha(ROOT/relative) == expected, relative
    plans = [DOCS / "PLAN.md", Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
        Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
    if args.sync:
        previous = subprocess.check_output(["git", "show", "HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/PLAN.md"], cwd=ROOT).decode().replace("\r\n", "\n")
        current = plans[0].read_text(encoding="utf-8")
        for path in plans[1:]: assert path.read_text(encoding="utf-8") in {previous, current}, path
        for path in plans: path.write_bytes(current.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert all(p.read_bytes() == plans[0].read_bytes() for p in plans)
    tests = {}
    for path in DOCS.glob("p1-document-generation-*.xml"):
        suite = ET.parse(path).getroot().find("testsuite")
        failures = int(suite.get("failures"))+int(suite.get("errors")); skipped = int(suite.get("skipped"))
        tests[path.stem] = {"passed":int(suite.get("tests"))-failures-skipped,"failed":failures,"skipped":skipped,"seconds":float(suite.get("time"))}
    assert tests["p1-document-generation-before"]["failed"] == 1
    assert read(DOCS/"p1-document-generation-before/last-http.json")["status"] == 404
    for name in ("verified", "product", "formats"):
        assert tests["p1-document-generation-"+name]["passed"] == 1 and tests["p1-document-generation-"+name]["failed"] == 0
    assert tests["p1-document-generation-contract"]["passed"] == 25 and tests["p1-document-generation-contract"]["skipped"] == 1
    assert tests["p1-document-generation-incremental-regression"]["passed"] == 4
    assert tests["p1-document-generation-api-regression"]["passed"] == 3
    assert read(DOCS/"p1-document-generation-compile.json")["exit_code"] == 0
    frontend = (DOCS/"p1-document-generation-frontend.log").read_text(encoding="utf-8-sig")
    assert "67 passed (67)" in frontend
    observations, downloads = {}, 0
    for directory in (DOCS/"p1-document-generation-verified", DOCS/"p1-document-generation-product", DOCS/"p1-document-generation-formats"):
        value = read(directory/"summary.json")
        assert [t["status"] for t in value["report"]["modules"]] == ["completed", "failed", "completed"]
        assert value["child"]["status"] == "completed" and value["retry"]["retry"]["strategy"] == "saved_response"
        # Earlier product summary recorded the combined post-browser total in
        # this field; its successful test asserted 3 before the browser started.
        assert value["terminal_replay_model_calls"] == (6 if directory.name == "p1-document-generation-product" else 3)
        scope = value["fixed_scope"]
        assert scope["implementation_version"] == "document-generation-scope-v1" and "requirement" not in scope
        assert "".join(scope["text"][s["start_char"]:s["end_char"]] for s in scope["segments"]) == scope["text"]
        for index, ref in enumerate(value["downloaded_objects"]):
            raw = (directory/f"object-{index}.json").read_bytes()
            assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
            downloads += 1
        if (directory/"product.json").exists():
            product = read(directory/"product.json")
            assert product["page_errors"] == [] and product["lost_reply_recovered_without_post"]
            assert all(t["status"] == "completed" for t in product["report"]["modules"])
        observations[directory.name] = {"http_calls":len(value["physical_http_requests"]), "downloaded_objects":len(value["downloaded_objects"])}
    formats = read(DOCS/"p1-document-generation-formats/summary.json")
    assert len(formats["physical_http_requests"]) == 10 and len(formats["downloaded_objects"]) == 14
    assert [len(f["fixed_scope"]["modules"]) for f in formats["formats"]] == [3,1]
    assert all(t["status"] == "completed" for f in formats["formats"] for t in f["report"]["modules"])
    files = [DOCS/"P1_DOCUMENT_GENERATION_RESEARCH.md"]
    for path in DOCS.glob("p1-document-generation-*"):
        if path.name == "p1-document-generation-validation.json": continue
        files.extend([p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path])
    exposed = re.compile(r'(?i)"(?:api_key|authorization|password|access_token)"\s*:\s*"(?!\*|redact|<|$)[^"\s]+"')
    for path in files:
        if path.suffix in {".json", ".stdout", ".stderr", ".log"}:
            assert not exposed.search(path.read_text(encoding="utf-8-sig", errors="replace")), path
    value = {"tests":tests,"observations":observations,"actual_object_downloads_checked":downloads,
        "historical_artifacts_checked":len(historical),"historical_manifest_sha256":manifests,
        "protected_files_sha256":protected,"plans_sha256":sha(plans[0]),
        "artifact_hashes":{str(p.relative_to(ROOT)).replace("\\","/"):sha(p) for p in sorted(set(files))},
        "remaining":"default Step/business quality/long document capacity and broader P0-P5; goal remains incomplete"}
    (DOCS/"p1-document-generation-validation.json").write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"tests":tests,"objects":downloads,"old_artifacts":len(historical),"new_artifacts":len(files),"plans_equal":True}, ensure_ascii=False))


if __name__ == "__main__": main()
