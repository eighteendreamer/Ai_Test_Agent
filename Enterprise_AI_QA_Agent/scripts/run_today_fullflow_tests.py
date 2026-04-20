from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "Agent_Server"
FRONTEND_DIR = ROOT_DIR / "agent_web"
REPORT_DIR = BACKEND_DIR / "test_reports"
REPORT_PATH = REPORT_DIR / "today_fullflow_report.json"

BACKEND_TESTS = [
    BACKEND_DIR / "tests" / "test_provider_profiles.py",
    BACKEND_DIR / "tests" / "test_model_adapters.py",
    BACKEND_DIR / "tests" / "test_settings_service.py",
    BACKEND_DIR / "tests" / "test_model_invoker.py",
]


@dataclass
class StepResult:
    name: str
    command: list[str]
    cwd: str
    ok: bool
    returncode: int
    duration_ms: int
    stdout: str
    stderr: str


def _console_safe(value: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding, errors="replace")


def run_step(name: str, command: list[str], cwd: Path) -> StepResult:
    started_at = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    return StepResult(
        name=name,
        command=command,
        cwd=str(cwd),
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        duration_ms=duration_ms,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def print_step(result: StepResult) -> None:
    status = "PASS" if result.ok else "FAIL"
    print(f"[{status}] {result.name} ({result.duration_ms} ms)")
    print(_console_safe(f"  cwd: {result.cwd}"))
    print(_console_safe(f"  cmd: {' '.join(result.command)}"))
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if stdout.strip():
        print("  stdout:")
        for line in stdout.strip().splitlines():
            print(_console_safe(f"    {line}"))
    if stderr.strip():
        print("  stderr:")
        for line in stderr.strip().splitlines():
            print(_console_safe(f"    {line}"))


def ensure_report_dir() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def write_report(results: list[StepResult]) -> None:
    ensure_report_dir()
    payload = {
        "python_executable": sys.executable,
        "root_dir": str(ROOT_DIR),
        "backend_dir": str(BACKEND_DIR),
        "frontend_dir": str(FRONTEND_DIR),
        "generated_at_epoch_ms": int(time.time() * 1000),
        "steps": [asdict(item) for item in results],
        "ok": all(item.ok for item in results),
    }
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_commands() -> list[tuple[str, list[str], Path]]:
    pytest_command = [
        sys.executable,
        "-m",
        "pytest",
        *[str(path) for path in BACKEND_TESTS],
    ]
    import_command = [
        sys.executable,
        "-c",
        "from src.main import app; print('ok')",
    ]
    compile_command = [
        sys.executable,
        "-m",
        "compileall",
        "src",
        "tests",
    ]
    frontend_build_command = ["cmd", "/c", "npm", "run", "build"] if os.name == "nt" else ["npm", "run", "build"]

    return [
        ("backend_pytest", pytest_command, BACKEND_DIR),
        ("backend_import_main", import_command, BACKEND_DIR),
        ("backend_compileall", compile_command, BACKEND_DIR),
        ("frontend_build", frontend_build_command, FRONTEND_DIR),
    ]


def main() -> int:
    print("Today full-flow regression")
    print(f"Python: {sys.executable}")
    print(f"Project: {ROOT_DIR}")

    results: list[StepResult] = []
    for name, command, cwd in build_commands():
        result = run_step(name, command, cwd)
        results.append(result)
        print_step(result)

    write_report(results)
    print(f"\nReport: {REPORT_PATH}")

    if all(item.ok for item in results):
        print("All regression steps passed.")
        return 0

    print("Regression failed. See report for details.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
