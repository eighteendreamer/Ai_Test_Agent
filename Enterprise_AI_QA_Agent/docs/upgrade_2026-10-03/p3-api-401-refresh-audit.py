"""Audit the local HTTP evidence for the explicit 401 refresh gap."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
suite = ET.parse(directory / "p3-api-401-refresh-gap.xml").getroot().find("testsuite")
assert (int(suite.get("tests", "0")), int(suite.get("failures", "0")),
        int(suite.get("skipped", "0"))) == (1, 0, 0)
facts = json.loads((directory / "p3-api-401-refresh-gap" / "verification.json").read_text(encoding="utf8"))
assert facts["automatic_refresh"] is False
assert facts["initial_401_requests"] == 1
assert facts["explicit_refresh_and_replay_requests"] == 2
assert facts["write_401_replay_requests"] == 1
assert facts["external_ai_calls"] == 0
print("401 refresh gap and write replay guard evidence verified; explicit refresh remains manual.")
