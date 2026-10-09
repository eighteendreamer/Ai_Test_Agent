"""K6 engine adapter.

Translates PerfPlan into k6 JavaScript, builds container commands,
and parses k6 summary JSON into unified RawMetrics.
"""
from __future__ import annotations

import json
import re
import socket
from typing import Any
from urllib.parse import urlparse, urlunparse

from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfTarget, RampStage
from .engine_adapter import EngineCommand, PerfEngineAdapter, RawMetrics, RunOptions, ScriptArtifact, metric_number
from .request_validation import correlation_bindings


class K6EngineAdapter:
    """Default engine adapter using grafana/k6."""

    engine_key: str = "k6"
    default_image: str = "grafana/k6:latest"

    def __init__(
        self,
        image: str = "",
        rewrite_localhost: bool = True,
        docker_host_alias: str = "host.docker.internal",
        local_host_ips: set[str] | None = None,
    ):
        self._image = image or self.default_image
        self._rewrite_localhost = rewrite_localhost
        self._docker_host_alias = docker_host_alias
        self._local_host_ips = local_host_ips if local_host_ips is not None else self._detect_local_host_ips()

    def build_script(self, plan: PerfPlan) -> ScriptArtifact:
        if not plan.smoke.expect_status or any(isinstance(s, bool) or not 100 <= s <= 599 for s in plan.smoke.expect_status):
            raise ValueError("性能预期状态必须包含明确的HTTP状态码")
        lines: list[str] = []
        lines.append('import http from "k6/http";')
        lines.append('import { check, sleep } from "k6";')

        if plan.data_params:
            lines.append('import { SharedArray } from "k6/data";')
            lines.append('import papaparse from "https://jslib.k6.io/papaparse/5.1.1/index.js";')

        lines.append("")
        lines.append(f"const __expectedStatus = {json.dumps(plan.smoke.expect_status)};")
        lines.append("const __responseCallback = http.expectedStatuses(...__expectedStatus);")

        # Data CSV loading
        for dp in plan.data_params:
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", dp.name)
            lines.append(f'const {safe_name}Data = new SharedArray("{dp.name}", function () {{')
            lines.append(f'  return papaparse.parse(open("/work/data/{dp.name}.csv"), {{ header: true }}).data;')
            lines.append("});")
            lines.append("")

        # Options block. Smoke uses a tiny closed model so k6 does not combine
        # --vus/--iterations with a load scenario from the generated script.
        lines.append("const __smoke = __ENV.QA_PERF_SMOKE === \"true\";")
        lines.append("const __smokeVus = Number(__ENV.QA_PERF_SMOKE_VUS || 1);")
        lines.append("const __smokeIterations = Number(__ENV.QA_PERF_SMOKE_ITERATIONS || 3);")
        lines.append("")
        lines.append("const __loadOptions = {")
        lines.extend(self._build_options_block(plan))
        lines.append("};")
        lines.append("")
        lines.append('const __summaryTrendStats = ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"];')
        lines.append("export const options = __smoke")
        lines.append("  ? { vus: __smokeVus, iterations: __smokeIterations, summaryTrendStats: __summaryTrendStats }")
        lines.append("  : { ...__loadOptions, summaryTrendStats: __summaryTrendStats };")
        lines.append("")

        # Default function
        lines.append("export default function () {")
        lines.extend(self._build_default_function(plan))
        lines.append("}")

        script_content = "\n".join(lines)
        data_files: dict[str, str] = {}
        for dp in plan.data_params:
            if dp.ref:
                data_files[f"data/{dp.name}.csv"] = dp.ref

        return ScriptArtifact(
            engine="k6",
            script_content=script_content,
            filename="script.js",
            data_files=data_files,
        )

    def run_command(self, script: ScriptArtifact, run_opts: RunOptions) -> EngineCommand:
        cmd = ["k6", "run", "--summary-export", run_opts.summary_export_path]
        env = dict(run_opts.environment)
        if run_opts.is_smoke:
            env["QA_PERF_SMOKE"] = "true"
            env["QA_PERF_SMOKE_VUS"] = str(run_opts.smoke_vus)
            env["QA_PERF_SMOKE_ITERATIONS"] = str(run_opts.smoke_iterations)
        cmd.extend(run_opts.extra_args)
        cmd.append(f"/work/{script.filename}")

        return EngineCommand(
            image=self._image,
            command=cmd,
            workdir="/work",
            env=env,
        )

    def parse_results(self, summary_json: str) -> RawMetrics:
        data = json.loads(summary_json)
        if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
            raise ValueError("Invalid k6 summary: expected metrics object")

        metrics = data.get("metrics", {})

        http_reqs = self._metric_values(metrics.get("http_reqs", {}))
        duration_values = self._metric_values(metrics.get("http_req_duration", {}))
        failed_values = self._metric_values(metrics.get("http_req_failed", {}))

        count = metric_number(http_reqs, "count")
        samples = int(count) if count is not None else None
        rate = metric_number(http_reqs, "rate")
        error_rate = metric_number(failed_values, "rate" if "rate" in failed_values else "value")

        thresholds = {}
        if "thresholds" in data:
            for k, v in data["thresholds"].items():
                thresholds[k] = v
        for name, metric in metrics.items():
            if not isinstance(metric, dict):
                continue
            for expression, result in metric.get("thresholds", {}).items():
                # k6 summary-wrapper.js: legacy booleans are !threshold.ok.
                if isinstance(result, bool):
                    thresholds[f"{name}:{expression}"] = {"ok": not result}
                elif isinstance(result, dict) and isinstance(result.get("ok"), bool):
                    thresholds[f"{name}:{expression}"] = result

        return RawMetrics(
            samples=samples,
            throughput_tps=rate,
            avg_ms=metric_number(duration_values, "avg"),
            min_ms=metric_number(duration_values, "min"),
            max_ms=metric_number(duration_values, "max"),
            p50_ms=metric_number(duration_values, "med"),
            p90_ms=metric_number(duration_values, "p(90)"),
            p95_ms=metric_number(duration_values, "p(95)"),
            p99_ms=metric_number(duration_values, "p(99)"),
            error_rate=error_rate,
            error_count=int(samples * error_rate) if samples is not None and error_rate is not None else None,
            thresholds=thresholds,
            raw_data=data,
        )

    @staticmethod
    def _metric_values(metric: Any) -> dict[str, Any]:
        if not isinstance(metric, dict):
            return {}
        values = metric.get("values")
        if isinstance(values, dict):
            return values
        return metric

    def build_smoke_options(self, plan: PerfPlan) -> RunOptions:
        return RunOptions(
            timeout_seconds=60,
            is_smoke=True,
            smoke_vus=plan.smoke.vus,
            smoke_iterations=plan.smoke.iterations,
        )

    def rewrite_target_url(self, url: str) -> str:
        if not self._rewrite_localhost:
            return url
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host.lower() not in {"localhost", "127.0.0.1", "::1"} and host not in self._local_host_ips:
            return url

        netloc = self._docker_host_alias
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _build_options_block(self, plan: PerfPlan) -> list[str]:
        lines: list[str] = []
        wl = plan.workload

        if wl.model == "open":
            lines.append("  scenarios: {")
            lines.append("    load: {")
            if wl.mode == "constant_arrival_rate":
                lines.append('      executor: "constant-arrival-rate",')
                lines.append(f"      rate: {wl.target_rate_rps or 50},")
                lines.append('      timeUnit: "1s",')
                lines.append(f"      duration: \"{wl.hold_seconds}s\",")
                lines.append(f"      preAllocatedVUs: {wl.virtual_users or 100},")
                lines.append(f"      maxVUs: {plan.limits.max_vus},")
            else:
                lines.append('      executor: "ramping-arrival-rate",')
                lines.append('      timeUnit: "1s",')
                lines.append(f"      preAllocatedVUs: {wl.virtual_users or 100},")
                lines.append(f"      maxVUs: {plan.limits.max_vus},")
                lines.append("      stages: [")
                for stage in wl.ramp_stages:
                    lines.append(f'        {{ target: {stage.target}, duration: "{stage.duration}" }},')
                if not wl.ramp_stages:
                    target_rps = wl.target_rate_rps or 50
                    lines.append(f'        {{ target: {target_rps // 2}, duration: "{wl.hold_seconds // 3}s" }},')
                    lines.append(f'        {{ target: {target_rps}, duration: "{wl.hold_seconds // 3}s" }},')
                    lines.append(f'        {{ target: {target_rps}, duration: "{wl.hold_seconds // 3}s" }},')
                lines.append("      ],")
            lines.append("    },")
            lines.append("  },")
        else:
            if wl.mode == "ramping_vus" or wl.ramp_stages:
                lines.append("  stages: [")
                for stage in wl.ramp_stages:
                    lines.append(f'    {{ target: {stage.target}, duration: "{stage.duration}" }},')
                if not wl.ramp_stages:
                    vus = wl.virtual_users or 50
                    lines.append(f'    {{ target: {vus // 2}, duration: "{wl.hold_seconds // 3}s" }},')
                    lines.append(f'    {{ target: {vus}, duration: "{wl.hold_seconds // 3}s" }},')
                    lines.append(f'    {{ target: {vus}, duration: "{wl.hold_seconds // 3}s" }},')
                lines.append("  ],")
            else:
                lines.append(f"  vus: {wl.virtual_users or 50},")
                lines.append(f'  duration: "{wl.hold_seconds}s",')

        # Thresholds from SLA
        thresholds: dict[str, list[str]] = {}
        if plan.sla.p95_ms:
            thresholds.setdefault("http_req_duration", []).append(f"p(95)<{str(plan.sla.p95_ms).removesuffix('.0')}")
        if plan.sla.p99_ms:
            thresholds.setdefault("http_req_duration", []).append(f"p(99)<{str(plan.sla.p99_ms).removesuffix('.0')}")
        if plan.sla.error_rate is not None:
            thresholds["http_req_failed"] = [f"rate<{plan.sla.error_rate}"]

        if thresholds:
            lines.append("  thresholds: {")
            for metric, checks in thresholds.items():
                formatted = ", ".join(
                    f'{{ threshold: "{c}", abortOnFail: true, delayAbortEval: "10s" }}'
                    for c in checks
                )
                lines.append(f"    \"{metric}\": [{formatted}],")
            lines.append("  },")

        return lines

    def _build_default_function(self, plan: PerfPlan) -> list[str]:
        lines: list[str] = []
        indent = "  "
        bindings = correlation_bindings(plan)
        if bindings:
            lines.append(f"{indent}const __correlations = Object.create(null);")

        # Data variable selection
        for dp in plan.data_params:
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", dp.name)
            if dp.uniqueness == "per_iteration":
                lines.append(f"{indent}const {safe_name} = {safe_name}Data[__ITER % {safe_name}Data.length];")
            else:
                lines.append(f"{indent}const {safe_name} = {safe_name}Data[Math.floor(Math.random() * {safe_name}Data.length)];")

        # HTTP requests for each target
        for i, target in enumerate(plan.targets):
            url = self.rewrite_target_url(target.url)
            var_name = f"res{i}" if len(plan.targets) > 1 else "res"
            lines.append(f"{indent}const __headers{i} = {json.dumps(target.headers)};")
            required = []
            for binding in bindings:
                if binding["destination"] != i:
                    continue
                number, correlation = binding["index"], binding["correlation"]
                lines.append(f"{indent}const __bound{number} = __correlations[{number}];")
                lines.append(f'{indent}const __canBind{number} = ["string", "number", "boolean"].includes(typeof __bound{number}) && __bound{number} !== "";')
                lines.append(f'{indent}check(__canBind{number}, {{ "qa_dependency_{number}": (v) => v === true }});')
                lines.append(f"{indent}if (__canBind{number}) __headers{i}[{json.dumps(correlation.into_header)}] = {json.dumps(correlation.header_prefix or '')} + String(__bound{number});")
                required.append(f"__canBind{number}")
            target_indent = indent
            lines.append(f"{indent}if ({' && '.join(required) or 'true'}) {{")
            indent += "  "

            if target.method.upper() == "GET":
                lines.append(f'{indent}const {var_name} = http.get({json.dumps(url)}, {{')
            else:
                body = self._build_body_expr(target, plan)
                lines.append(f'{indent}const {var_name} = http.{target.method.lower()}({json.dumps(url)}, {body}, {{')

            # Headers
            lines.append(f"{indent}  headers: __headers{i},")
            lines.append(f"{indent}  responseCallback: __responseCallback,")
            lines.append(f"{indent}}});")
            lines.append("")

            # Checks
            lines.append(f'{indent}check({var_name}, {{')
            lines.append(f'{indent}  ["qa_status_{i}_" + {var_name}.status]: (r) => __expectedStatus.includes(r.status),')
            lines.append(f"{indent}}});")

            # Correlations (extract from response)
            for binding in bindings:
                if binding["source"] != i:
                    continue
                number, correlation = binding["index"], binding["correlation"]
                lines.append(f"{indent}let __value{number};")
                lines.append(f"{indent}let __parsed{number} = false;")
                lines.append(f"{indent}try {{")
                lines.append(f"{indent}  __value{number} = {var_name}.json({json.dumps(correlation.from_path)});")
                lines.append(f"{indent}  __parsed{number} = true;")
                lines.append(f"{indent}}} catch (_) {{")
                lines.append(f'{indent}  check(false, {{ "qa_json_parse_{number}": (v) => v === true }});')
                lines.append(f"{indent}}}")
                lines.append(f'{indent}const __extracted{number} = __parsed{number} && __expectedStatus.includes({var_name}.status) && __value{number} !== undefined && __value{number} !== null && __value{number} !== "";')
                lines.append(f'{indent}check(__extracted{number}, {{ "qa_extract_{number}": (v) => v === true }});')
                lines.append(f"{indent}if (__extracted{number}) __correlations[{number}] = __value{number};")

            indent = target_indent
            lines.append(f"{indent}}}")
            lines.append("")

        # Think time
        if plan.workload.think_time_ms > 0:
            lines.append(f"{indent}sleep({plan.workload.think_time_ms / 1000});")

        return lines

    def _build_body_expr(self, target: PerfTarget, plan: PerfPlan) -> str:
        if target.body_template is None:
            return "null"
        if isinstance(target.body_template, str):
            # Request data must not become template-literal JavaScript. This
            # preserves backslashes, backticks and literal ${...} content.
            return json.dumps(target.body_template)
        return f"JSON.stringify({json.dumps(target.body_template)})"

    @staticmethod
    def _detect_local_host_ips() -> set[str]:
        ips = {"127.0.0.1", "::1"}
        hostnames = {"localhost"}
        try:
            hostnames.add(socket.gethostname())
            hostnames.add(socket.getfqdn())
        except OSError:
            pass

        for hostname in hostnames:
            try:
                for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
                    if family in {socket.AF_INET, socket.AF_INET6} and sockaddr:
                        ips.add(str(sockaddr[0]))
            except OSError:
                continue

        return ips
