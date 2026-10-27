"""No personal data or network: tests use synthetic fields and a fake page."""
import json
from pathlib import Path
import tempfile
import unittest

import fill_linkincubator as app


def sample():
    return {"schemaVersion": 1, "url": app.TARGET, "fields": [
        {"name": "projectInfo.name", "value": "示例项目", "kind": "text", "type": "text"},
        {"name": "projectInfo.stage", "value": "mvp", "kind": "select", "type": "text", "displayText": "示例阶段"},
        {"name": "other.applyReason", "value": "", "kind": "text", "type": "textarea"},
    ]}


class Locator:
    def __init__(self, page, key):
        self.page, self.key = page, key

    def count(self):
        return 5 if self.key == "steps" else (0 if self.key == self.page.missing else 1)

    def nth(self, index):
        return Locator(self.page, f"step:{index}")

    def click(self, **_):
        self.page.clicks.append(self.key)
        if self.key == "option":
            self.page.values["projectInfo.stage"] = "mvp"

    def wait_for(self, **_):
        pass

    def input_value(self):
        return self.page.values.get(self.key, "")

    def fill(self, value):
        self.page.values[self.key] = value


class Page:
    url = app.TARGET
    missing = None

    def __init__(self):
        self.values, self.clicks = {}, []

    def locator(self, selector):
        return Locator(self, "steps" if selector.startswith(".v-stepper") else selector.split('"')[1])

    def get_by_role(self, role, **_):
        if role != "option":
            raise AssertionError("Unexpected action")
        return Locator(self, "option")


class DraftTests(unittest.TestCase):
    def load(self, draft):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "draft.json"
            path.write_text(json.dumps(draft), encoding="utf-8")
            return app.load_draft(path)

    def test_load(self):
        self.assertEqual(len(self.load(sample())["fields"]), 3)

    def test_reject_wrong_origin_and_path(self):
        for url in ["https://www.linkincubator.com.evil.test/apply/c23", "http://www.linkincubator.com/apply/c23", "https://www.linkincubator.com/other"]:
            self.assertFalse(app.is_target(url))

    def test_reject_duplicate(self):
        draft = sample()
        draft["fields"].append(draft["fields"][0])
        with self.assertRaises(ValueError):
            self.load(draft)

    def test_reject_secret_field(self):
        draft = sample()
        draft["fields"][0]["type"] = "password"
        with self.assertRaises(ValueError):
            self.load(draft)

    def test_counter_warning_without_rewriting(self):
        draft = sample()
        draft["fields"][0].update(value="x" * 194, counter="194 / 100")
        self.assertTrue(app.review_warnings(draft))
        self.assertEqual(len(draft["fields"][0]["value"]), 194)

    def test_restore_text_select_empty_and_no_submit(self):
        page = Page()
        results = app.restore_fields(page, sample())
        self.assertEqual(page.values["projectInfo.name"], "示例项目")
        self.assertEqual(page.values["projectInfo.stage"], "mvp")
        self.assertEqual(results[-1]["status"], "empty_not_overwritten")
        self.assertTrue(all(click.startswith("step:") or click in {"option", "projectInfo.stage"} for click in page.clicks))

    def test_skip_already_matching(self):
        page = Page()
        page.values["projectInfo.name"] = "示例项目"
        self.assertEqual(app.restore_fields(page, sample())[0]["status"], "already_matches")

    def test_missing_field_is_reported(self):
        page = Page()
        page.missing = "projectInfo.name"
        self.assertEqual(app.restore_fields(page, sample())[0]["status"], "missing_or_ambiguous")

    def test_origin_change_stops_before_writing(self):
        page = Page()
        page.url = "https://example.org/"
        with self.assertRaises(RuntimeError):
            app.restore_fields(page, sample())
        self.assertFalse(page.values)

    def test_default_does_not_launch_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "draft.json"
            path.write_text(json.dumps(sample()), encoding="utf-8")
            self.assertEqual(app.main(["--data", str(path)]), 0)


if __name__ == "__main__":
    unittest.main()
