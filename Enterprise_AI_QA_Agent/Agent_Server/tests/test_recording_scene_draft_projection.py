"""Actual draft projection must carry the scene meaning and its alias evidence."""
import json
import os
from pathlib import Path

FILE=Path(os.getenv("SCENE_DRAFT_PROJECTION_FILE",str(Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p5-recording-scene-product-complete/formal-draft.json")))


def test_saved_alias_has_the_recorded_locator_basis():
    draft=json.loads(FILE.read_text(encoding="utf-8"))["version"]
    alias=draft["test_data"]["scene"]["aliases"][0]
    matching=next(s for s in draft["steps"] if s["data"]["seq"]==alias["source_seq"])
    assert alias.get("target")==matching["data"]["event"]["target"]
    assert alias.get("page")==matching["data"]["event"]["page"]
