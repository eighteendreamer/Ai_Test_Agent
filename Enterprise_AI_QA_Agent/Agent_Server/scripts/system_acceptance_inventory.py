"""Read source facts for an acceptance run; never load application settings/data."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "Agent_Server"


def git(*arguments):
    result = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def product_fingerprint():
    paths = [*SERVER.joinpath("src").rglob("*.py"), *SERVER.joinpath("alembic").rglob("*.py")]
    web = ROOT / "agent_web"
    paths += [path for folder in ("src", "electron") for path in web.joinpath(folder).rglob("*")
              if path.is_file() and path.suffix in {".ts", ".vue", ".js", ".mjs", ".css"}]
    paths += [SERVER / "pyproject.toml", SERVER / "alembic.ini", web / "package.json", web / "package-lock.json", web / "vite.config.ts"]
    entries = [{"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted(paths) if "__pycache__" not in path.parts and path.is_file()]
    return {"sha256": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(), "files": entries}


def inventory():
    routes = []
    for path in sorted(SERVER.joinpath("src/api/routes").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefixes = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "APIRouter":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = next((kw.value.value for kw in node.value.keywords
                                                   if kw.arg == "prefix" and isinstance(kw.value, ast.Constant)), "")
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr
                if method not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                router = getattr(decorator.func.value, "id", "")
                routes.append({"module": path.stem, "router": router, "method": method.upper(),
                               "path": prefixes.get(router, "") + decorator.args[0].value,
                               "source": path.relative_to(ROOT).as_posix(), "line": node.lineno})
    main = ast.parse(SERVER.joinpath("src/main.py").read_text(encoding="utf-8"))
    included = [node.args[0].id for node in ast.walk(main)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router" and node.args and isinstance(node.args[0], ast.Name)]
    web_source = ROOT.joinpath("agent_web/src/router/index.ts").read_text(encoding="utf-8")
    web_routes = re.findall(r'path:\s*"([^"]+)"', web_source)
    modes = []
    for path in sorted(SERVER.joinpath("src/modes").glob("*/manifest.py")):
        text = path.read_text(encoding="utf-8")
        modes.append({"module": path.parent.name, "source": path.relative_to(ROOT).as_posix(),
                      "has_runtime_module": path.with_name("runtime.py").exists(),
                      "manifest_sha256": hashlib.sha256(text.encode()).hexdigest()})
    checklist = ROOT.joinpath("系统问题清单_2026-09-20.md").read_text(encoding="utf-8")
    body = checklist.split("## 一、P0", 1)[1].split("## 五、", 1)[0]
    findings = []
    for line in body.splitlines():
        match = re.match(r"\|\s*(P[012]-\d+)\s*\|\s*([^|]+)\|", line)
        if match:
            findings.append({"id": match[1], "current_heading": match[2].strip(), "full_row": line})
    return {"captured_at_utc": datetime.now(UTC).isoformat(), "commit": git("rev-parse", "HEAD"),
            "registered_router_count": len(included), "registered_routers": included,
            "route_definitions": routes, "web_paths": web_routes, "mode_manifests": modes,
            "skills_on_disk": len(list(SERVER.joinpath("src/SKILLS").glob("*/SKILL.md"))),
            "test_files": sorted(path.name for path in SERVER.joinpath("tests").glob("test_*.py")),
            "findings": findings, "product_fingerprint": product_fingerprint(),
            "limitations": ["AST counts describe source, not running deployment or successful functions.",
                            "has_runtime_module is not proof a mode is usable; inspect tool/runtime wiring."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps({"commit": result["commit"], "routers": result["registered_router_count"],
                      "route_definitions": len(result["route_definitions"]), "web_paths": len(result["web_paths"]),
                      "modes": len(result["mode_manifests"]), "skills_on_disk": result["skills_on_disk"]}))
    for row in result["findings"]:
        heading = row["current_heading"]
        if any(word in heading for word in ("部分", "未完成", "待", "未统一", "未验证", "未关闭", "条件风险", "待定性")):
            print(row["id"], heading[:110])


if __name__ == "__main__":
    main()
