"""Keep the current component probe and restore its tracked historical evidence."""
import hashlib
import json
from pathlib import Path
import subprocess

workspace = Path(__file__).resolve().parents[2]
root = workspace / "docs/upgrade_2026-10-03"
target = root / "p1-current2-component"
target.mkdir(exist_ok=True)
for filename in ("p1-import-browser-json.json", "p1-import-browser-rows.json", "p1-import-browser-split.json", "p1-import-browser.png"):
    path = root / filename
    (target / filename).write_bytes(path.read_bytes())
    original = subprocess.run(["git", "show", f"HEAD:Enterprise_AI_QA_Agent/docs/upgrade_2026-10-03/{filename}"], cwd=workspace, capture_output=True, check=True)
    path.write_bytes(original.stdout)
    assert path.read_bytes() == original.stdout
protected = json.loads((root / "p5-default-ui-protected.json").read_text(encoding="utf-8"))
for name, digest in protected.items():
    assert hashlib.sha256((workspace / name).read_bytes()).hexdigest() == digest, name
print("Saved current component artifacts separately; historical and protected bytes verified.")
