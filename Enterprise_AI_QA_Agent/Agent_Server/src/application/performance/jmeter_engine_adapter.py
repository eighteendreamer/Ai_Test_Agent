"""JMeter engine adapter.

Translates PerfPlan into JMeter .jmx XML, builds container commands,
and parses JMeter statistics.json into unified RawMetrics.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from src.modes.performance_testing_mode.plan_state import PerfPlan
from .engine_adapter import EngineCommand, RawMetrics, RunOptions, ScriptArtifact, metric_number


class JMeterEngineAdapter:
    """Adapter using a Dockerized JMeter runtime in non-GUI mode."""

    engine_key: str = "jmeter"
    default_image: str = "alpine/jmeter:5.6.3"

    def __init__(
        self,
        image: str = "",
        rewrite_localhost: bool = True,
        docker_host_alias: str = "host.docker.internal",
    ):
        self._image = image or self.default_image
        self._rewrite_localhost = rewrite_localhost
        self._docker_host_alias = docker_host_alias

    def build_script(self, plan: PerfPlan) -> ScriptArtifact:
        lines: list[str] = []
        targets = plan.targets or []
        wl = plan.workload

        # XML prolog
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append('<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">')
        lines.append("  <hashTree>")

        # TestPlan node
        plan_name = _esc_xml(plan.title or "Performance Test Plan")
        lines.append(f'    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="{plan_name}" enabled="true">')
        lines.append('      <stringProp name="TestPlan.comments"></stringProp>')
        lines.append('      <boolProp name="TestPlan.functional_mode">false</boolProp>')
        lines.append('      <boolProp name="TestPlan.tearDown_on_shutdown">true</boolProp>')
        lines.append('      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>')
        lines.append('      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">')
        lines.append('        <collectionProp name="Arguments.arguments"/>')
        lines.append("      </elementProp>")
        lines.append('      <stringProp name="TestPlan.user_define_classpath"></stringProp>')
        lines.append("    </TestPlan>")
        lines.append("    <hashTree>")

        # ThreadGroup per target (or single group with all targets)
        for i, target in enumerate(targets):
            self._append_thread_group(lines, plan, target, i)

        # ResultCollector for summary export
        lines.append('      <ResultCollector guiclass="SummaryCollector" testclass="ResultCollector" testname="Summary Report" enabled="true">')
        lines.append('        <boolProp name="ResultCollector.error_logging">false</boolProp>')
        lines.append('        <objProp><name>saveConfig</name><value class="SampleSaveConfiguration">')
        lines.append('          <time>true</time><latency>true</latency><timestamp>true</timestamp>')
        lines.append('          <success>true</success><label>true</label><code>true</code>')
        lines.append('          <message>true</message><threadName>true</threadName>')
        lines.append('          <dataType>true</dataType><encoding>false</encoding>')
        lines.append('          <assertions>true</assertions><bytes>true</bytes><sentBytes>true</sentBytes>')
        lines.append('        </value></objProp>')
        lines.append('        <stringProp name="filename">/work/statistics.json</stringProp>')
        lines.append("      </ResultCollector>")
        lines.append("      <hashTree/>")

        lines.append("    </hashTree>")
        lines.append("  </hashTree>")
        lines.append("</jmeterTestPlan>")

        script_content = "\n".join(lines)
        data_files: dict[str, str] = {}
        for dp in plan.data_params:
            if dp.ref:
                data_files[f"data/{dp.name}.csv"] = dp.ref

        return ScriptArtifact(
            engine="jmeter",
            script_content=script_content,
            filename="test.jmx",
            data_files=data_files,
        )

    def run_command(self, script: ScriptArtifact, run_opts: RunOptions) -> EngineCommand:
        cmd = [
            "jmeter", "-n", "-t", f"/work/{script.filename}",
            "-l", "/work/result.jtl",
            "-e", "-o", "/work/report",
            "-j", "/work/jmeter.log",
        ]

        if run_opts.is_smoke:
            cmd.extend(["-Jqa_perf.smoke=true"])

        env = dict(run_opts.environment)
        for k, v in run_opts.environment.items():
            cmd.extend([f"-J{k}={v}"])

        return EngineCommand(
            image=self._image,
            command=cmd,
            workdir="/work",
            env=env,
        )

    def parse_results(self, summary_json: str) -> RawMetrics:
        data = json.loads(summary_json)
        if not isinstance(data, dict) or not isinstance(data.get("Total"), dict):
            raise ValueError("Invalid JMeter summary: expected Total object")
        total = data["Total"]
        count = metric_number(total, "sampleCount")
        samples = int(count) if count is not None else None
        throughput = metric_number(total, "throughput")
        error_pct = metric_number(total, "errorPct")
        error_count = metric_number(total, "errorCount")

        return RawMetrics(
            samples=samples,
            throughput_tps=throughput,
            avg_ms=metric_number(total, "meanResTime"),
            min_ms=metric_number(total, "minResTime"),
            max_ms=metric_number(total, "maxResTime"),
            p50_ms=metric_number(total, "medianResTime"),
            p90_ms=metric_number(total, "pct1ResTime"),
            p95_ms=metric_number(total, "pct2ResTime"),
            p99_ms=metric_number(total, "pct3ResTime"),
            error_rate=error_pct / 100.0 if error_pct is not None else None,
            error_count=int(error_count) if error_count is not None else None,
            raw_data=data,
        )

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
        if host.lower() not in {"localhost", "127.0.0.1", "::1"}:
            return url
        netloc = self._docker_host_alias
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _append_thread_group(self, lines: list[str], plan: PerfPlan, target: Any, index: int) -> None:
        wl = plan.workload
        url = self.rewrite_target_url(target.url)
        parsed_url = urlparse(url)
        host = parsed_url.hostname or "localhost"
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        protocol = parsed_url.scheme or "http"
        path = parsed_url.path or "/"
        if parsed_url.query:
            path = f"{path}?{parsed_url.query}"

        tg_name = f"ThreadGroup-{target.name}-{index}" if target.name else f"ThreadGroup-{index}"
        sampler_name = _esc_xml(target.name or f"Sampler-{index}")

        # Closed model: use ThreadGroup.num_threads
        # Open model: use ConstantThroughputTimer
        vus = wl.virtual_users or 50
        ramp = max(1, wl.hold_seconds // 6) if wl.hold_seconds else 10
        duration = wl.hold_seconds or 60

        lines.append(f'      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="{tg_name}" enabled="true">')
        lines.append('        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>')
        lines.append('        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">')
        lines.append('          <boolProp name="LoopController.continue_forever">false</boolProp>')
        lines.append('          <intProp name="LoopController.loops">-1</intProp>')
        lines.append("        </elementProp>")
        lines.append(f'        <stringProp name="ThreadGroup.num_threads">{vus}</stringProp>')
        lines.append(f'        <stringProp name="ThreadGroup.ramp_time">{ramp}</stringProp>')
        lines.append('        <boolProp name="ThreadGroup.scheduler">true</boolProp>')
        lines.append(f'        <stringProp name="ThreadGroup.duration">{duration}</stringProp>')
        lines.append('        <stringProp name="ThreadGroup.delay"></stringProp>')
        lines.append('        <boolProp name="ThreadGroup.same_user_on_next_iteration">true</boolProp>')
        lines.append("      </ThreadGroup>")
        lines.append("      <hashTree>")

        # HTTPSampler
        lines.append(f'        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{sampler_name}" enabled="true">')
        lines.append('          <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">')
        lines.append('            <collectionProp name="Arguments.arguments"/>')
        lines.append("          </elementProp>")
        lines.append(f'          <stringProp name="HTTPSampler.domain">{host}</stringProp>')
        lines.append(f'          <stringProp name="HTTPSampler.port">{port}</stringProp>')
        lines.append(f'          <stringProp name="HTTPSampler.protocol">{protocol}</stringProp>')
        lines.append('          <stringProp name="HTTPSampler.contentEncoding"></stringProp>')
        lines.append(f'          <stringProp name="HTTPSampler.path">{_esc_xml(path)}</stringProp>')
        lines.append(f'          <stringProp name="HTTPSampler.method">{target.method.upper()}</stringProp>')
        lines.append('          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>')
        lines.append('          <boolProp name="HTTPSampler.auto_redirects">false</boolProp>')
        lines.append('          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>')
        lines.append('          <boolProp name="HTTPSampler.DO_MULTIPART_POST">false</boolProp>')
        lines.append('          <stringProp name="HTTPSampler.embedded_url_re"></stringProp>')
        lines.append('          <stringProp name="HTTPSampler.connect_timeout"></stringProp>')
        lines.append('          <stringProp name="HTTPSampler.response_timeout"></stringProp>')
        lines.append("        </HTTPSamplerProxy>")
        lines.append("        <hashTree>")

        # Header Manager
        if target.headers:
            lines.append('          <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="HTTP Header Manager" enabled="true">')
            lines.append('            <collectionProp name="HeaderManager.headers">')
            for hk, hv in target.headers.items():
                lines.append(f'              <elementProp name="{_esc_xml(hk)}" elementType="Header">')
                lines.append(f'                <stringProp name="Header.name">{_esc_xml(hk)}</stringProp>')
                lines.append(f'                <stringProp name="Header.value">{_esc_xml(hv)}</stringProp>')
                lines.append("              </elementProp>")
            lines.append("            </collectionProp>")
            lines.append("          </HeaderManager>")
            lines.append("          <hashTree/>")

        # CSV DataSet Config
        for dp in plan.data_params:
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", dp.name)
            lines.append(f'          <CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV Data - {safe_name}" enabled="true">')
            lines.append(f'            <stringProp name="filename">/work/data/{dp.name}.csv</stringProp>')
            lines.append(f'            <stringProp name="variableNames">{safe_name}</stringProp>')
            lines.append('            <boolProp name="ignoreFirstLine">true</boolProp>')
            lines.append('            <stringProp name="delimiter">,</stringProp>')
            lines.append('            <boolProp name="quotedData">false</boolProp>')
            lines.append('            <boolProp name="recycle">true</boolProp>')
            lines.append('            <boolProp name="stopThread">false</boolProp>')
            lines.append("          </CSVDataSet>")
            lines.append("          <hashTree/>")

        # SyncTimer (collection point) - add if spike or high concurrency
        if wl.mode == "spike" or vus > 100:
            group_size = min(vus, 500)
            lines.append(f'          <SyncTimer guiclass="TestBeanGUI" testclass="SyncTimer" testname="Sync Timer" enabled="true">')
            lines.append(f'            <intProp name="groupSize">{group_size}</intProp>')
            lines.append('            <longProp name="timeoutInMs">10000</longProp>')
            lines.append("          </SyncTimer>")
            lines.append("          <hashTree/>")

        # ConstantThroughputTimer for open model
        if wl.model == "open" and wl.target_rate_rps:
            target_cph = wl.target_rate_rps * 3600
            lines.append(f'          <ConstantThroughputTimer guiclass="TestBeanGUI" testclass="ConstantThroughputTimer" testname="Throughput Timer" enabled="true">')
            lines.append(f'            <stringProp name="throughput">{target_cph}</stringProp>')
            lines.append('            <stringProp name="calcMode">1</stringProp>')
            lines.append("          </ConstantThroughputTimer>")
            lines.append("          <hashTree/>")

        # Response Assertion from SLA
        if plan.sla.p95_ms or plan.sla.error_rate is not None:
            lines.append('          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Response Assertion" enabled="true">')
            lines.append('            <collectionProp name="Asserion.test_strings"/>')
            lines.append('            <stringProp name="Assertion.test_field">Assertion.response_code</stringProp>')
            lines.append('            <boolProp name="Assertion.assume_success">false</boolProp>')
            lines.append('            <intProp name="Assertion.test_type">2</intProp>')
            lines.append("          </ResponseAssertion>")
            lines.append("          <hashTree/>")

        # Think time
        if wl.think_time_ms > 0:
            tt_seconds = wl.think_time_ms / 1000.0
            lines.append(f'          <ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Think Time" enabled="true">')
            lines.append(f'            <stringProp name="ConstantTimer.delay">{tt_seconds * 1000:.0f}</stringProp>')
            lines.append('            <stringProp name="ConstantTimer.random">0</stringProp>')
            lines.append("          </ConstantTimer>")
            lines.append("          <hashTree/>")

        lines.append("        </hashTree>")
        lines.append("      </hashTree>")


def _esc_xml(text: str) -> str:
    """Escape a string for safe inclusion in XML attribute/text."""
    return (text
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
