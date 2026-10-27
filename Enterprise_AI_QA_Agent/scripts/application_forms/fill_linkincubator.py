"""Restore a private draft through form controls; never submit or upload.

Selectors: observed c23 DOM on 2026-10-27. API: Playwright Python Locator docs.
Private field values belong outside the repository and are never logged here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

TARGET = "https://www.linkincubator.com/apply/c23"
STEPS = ("projectInfo", "progress", "idea", "founder", "other")
FIELD_NAME = re.compile(r"^(projectInfo|progress|idea|founder|other)\.[A-Za-z]+$")


def is_target(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme, parsed.netloc, parsed.path.rstrip("/")) == (
        "https", "www.linkincubator.com", "/apply/c23"
    )


def load_draft(path: Path) -> dict:
    draft = json.loads(path.read_text(encoding="utf-8-sig"))
    if draft.get("schemaVersion") != 1 or not is_target(draft.get("url", "")):
        raise ValueError("资料版本或申请地址不匹配")
    fields = draft.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("资料中缺少字段清单")
    seen = set()
    for field in fields:
        name = field.get("name", "")
        if not FIELD_NAME.fullmatch(name) or name in seen:
            raise ValueError("存在不支持或重复的字段名")
        seen.add(name)
        if field.get("kind") not in {"text", "select"} or not isinstance(field.get("value"), str):
            raise ValueError("字段类型或值格式不正确")
        if field.get("type") in {"password", "file", "hidden"}:
            raise ValueError("资料不能包含密码、文件输入或隐藏字段")
    return draft


def review_warnings(draft: dict) -> list[str]:
    warnings = list(draft.get("reviewRequired", []))
    for field in draft["fields"]:
        limit = field.get("maxLength")
        counter = re.search(r"/\s*(\d+)", field.get("counter") or "")
        if limit is None and counter:
            limit = int(counter.group(1))
        if limit is not None and len(field["value"]) > limit:
            warnings.append(f"{field['name']}: 已保存原文超出页面字数提示，需人工精简")
        if field.get("required") and not field["value"]:
            warnings.append(f"{field['name']}: 必填但为空")
    return warnings


def restore_fields(page, draft: dict) -> list[dict]:
    """Use only named controls and the five observed stepper items.

    No upstream counterpart (checked: repo scripts, current c23 DOM, Playwright
    Locator API): this site-specific field mapping is kept separate from data.
    """
    results = []
    for index, prefix in enumerate(STEPS):
        if not is_target(page.url):
            raise RuntimeError("页面离开目标申请地址，已停止填写")
        steps = page.locator(".v-stepper-header .v-stepper-item")
        if steps.count() != 5:
            raise RuntimeError("网站步骤结构已变化，需重新核对映射")
        steps.nth(index).click()
        for field in draft["fields"]:
            if not field["name"].startswith(prefix + "."):
                continue
            name, value = field["name"], field["value"]
            if not value:
                results.append({"field": name, "status": "empty_not_overwritten"})
                continue
            if not is_target(page.url):
                raise RuntimeError("页面离开目标申请地址，已停止填写")
            locator = page.locator(f'[name="{name}"]')
            try:
                if locator.count() != 1:
                    results.append({"field": name, "status": "missing_or_ambiguous"})
                    continue
                locator.wait_for(state="visible", timeout=5000)
                if locator.input_value() == value:
                    results.append({"field": name, "status": "already_matches"})
                    continue
                if field["kind"] == "select":
                    locator.click()
                    label = field.get("displayText") or value
                    page.get_by_role("option", name=label, exact=True).click(timeout=5000)
                else:
                    locator.fill(value)
                status = "filled" if locator.input_value() == value else "verification_mismatch"
                results.append({"field": name, "status": status})
            except Exception as error:
                # Playwright errors can include filled values: don't print the message.
                results.append({"field": name, "status": "failed", "errorType": type(error).__name__})
                page.keyboard.press("Escape")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="默认只检查本地资料；--fill 打开浏览器辅助填写，不提交")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--fill", action="store_true")
    parser.add_argument("--channel", choices=("chrome", "msedge", "chromium"), default="msedge")
    args = parser.parse_args(argv)
    draft = load_draft(args.data)
    print(f"本地字段 {len(draft['fields'])} 个，已有内容 {sum(bool(f['value']) for f in draft['fields'])} 个")
    for warning in review_warnings(draft):
        print("待复核：" + warning)
    if not args.fill:
        print("仅检查本地文件，未打开浏览器、未传输资料。")
        return 0
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False, channel=None if args.channel == "chromium" else args.channel
        )
        try:
            page = browser.new_page()
            page.goto(TARGET, wait_until="domcontentloaded")
            print("请在新浏览器窗口手动登录并进入申请表；不要把密码或验证码发给助手。")
            input("页面准备好后按回车：")
            if not is_target(page.url):
                raise RuntimeError("当前未处于目标申请页")
            print("即将向 linkincubator.com 填入本地项目、履历、联系方式等申请资料；网站可能自动保存。")
            if input("确认传输并填写请输入 FILL，其余输入取消：").strip() != "FILL":
                print("已取消，未填入资料。")
                return 0
            results = restore_fields(page, draft)
            report = args.data.resolve().parent / "fill-report.json"
            report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            failures = sum(r["status"] in {"failed", "missing_or_ambiguous", "verification_mismatch"} for r in results)
            print(f"字段处理结束，需人工处理 {failures} 项。报告只含字段名及状态。")
            print("视频请自行上传；推荐人指示见私有资料。脚本没有提交操作。")
            input("请在浏览器复核；需提交请本人操作。按回车关闭临时浏览器：")
            return 1 if failures else 0
        finally:
            # No persistent profile, cookies, credentials or storage_state export.
            browser.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as error:
        print("操作未完成：" + type(error).__name__ + "；请核对资料文件、网站结构或浏览器环境。")
        raise SystemExit(1)
