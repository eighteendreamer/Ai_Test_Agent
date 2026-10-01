"""Measure seeded frontend stream processing in isolated browser pages."""

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-url", required=True)
    parser.add_argument("--fixed-url", default="http://127.0.0.1:5175")
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "baseline_commit": args.baseline_commit,
        "measured_at": datetime.now(UTC).isoformat(),
        "scope": (
            "headless Chromium, seeded decoded SSE callbacks, Pinia and ChatTimeline "
            "with production styles, 100 historical messages; JS/Vue DOM timing "
            "excludes network/providers and does not measure paint"
        ),
        "runs": {},
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        report["chromium_version"] = browser.version
        for name, url in [("baseline", args.baseline_url), ("fixed", args.fixed_url)]:
            page = browser.new_page()
            # Avoid starting the model/tool probe: this measures frontend CPU only.
            page.route("**/benchmark.html", lambda route: route.fulfill(
                status=200, content_type="text/html", body="<html><body></body></html>",
            ))
            page.goto(f"{url.rstrip('/')}/benchmark.html")
            page.evaluate("async () => { await import('/src/styles.css'); }")
            runs = []
            for repetition in range(3):
                result = page.evaluate(
                    "async () => await (await import('/e2e/assistant-stream-performance.ts'))"
                    ".benchmarkAssistantStream()",
                )
                runs.append(result)
                print(name, repetition + 1, json.dumps(result, ensure_ascii=False), flush=True)
            report["runs"][name] = runs
            page.close()
        browser.close()
    report["median_elapsed_ms"] = {
        name: {
            str(count): statistics.median(run["results"][index]["elapsed_ms"] for run in runs)
            for index, count in enumerate([100, 500, 1000])
        }
        for name, runs in report["runs"].items()
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep previous measurements intact; a failed run never overwrites evidence.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("median_elapsed_ms", json.dumps(report["median_elapsed_ms"]), flush=True)


if __name__ == "__main__":
    main()
