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
from .request_validation import correlation_bindings
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

        # One ordered ThreadGroup preserves response correlations between targets.
        if targets:
            self._append_thread_group(lines, plan, targets)

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
            correlations=list(plan.correlations),
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
            cmd.extend([f"-Jqa_perf.smoke_iterations={run_opts.smoke_iterations}",
                        f"-Jqa_perf.smoke_vus={run_opts.smoke_vus}",
                        "-Jqa_perf.scheduler=false"])

        if getattr(script, "correlations", None):
            cmd.extend(["-Jsample_variables=" + ",".join(
                name for i, _ in enumerate(script.correlations)
                for name in (f"qa_extract_{i}", f"qa_dependency_{i}")
            )])

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

    def _append_thread_group(self, lines: list[str], plan: PerfPlan, targets: list[Any]) -> None:
        wl = plan.workload
        vus = wl.virtual_users or 50
        ramp = max(1, wl.hold_seconds // 6) if wl.hold_seconds else 10
        duration = wl.hold_seconds or 60
        lines.append(f'      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Performance-journey" enabled="true">')
        lines.append('        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>')
        lines.append('        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">')
        lines.append('          <boolProp name="LoopController.continue_forever">false</boolProp>')
        lines.append('          <stringProp name="LoopController.loops">${__P(qa_perf.smoke_iterations,-1)}</stringProp>')
        lines.append('        </elementProp>')
        lines.append(f'        <stringProp name="ThreadGroup.num_threads">${{__P(qa_perf.smoke_vus,{vus})}}</stringProp>')
        lines.append(f'        <stringProp name="ThreadGroup.ramp_time">{ramp}</stringProp>')
        lines.append('        <stringProp name="ThreadGroup.scheduler">${__P(qa_perf.scheduler,true)}</stringProp>')
        lines.append(f'        <stringProp name="ThreadGroup.duration">${{__P(qa_perf.duration,{duration})}}</stringProp>')
        lines.append('        <stringProp name="ThreadGroup.delay"></stringProp>')
        lines.append('        <boolProp name="ThreadGroup.same_user_on_next_iteration">true</boolProp>')
        lines.append('      </ThreadGroup>')
        lines.append('      <hashTree>')
        for dp in plan.data_params:
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", dp.name)
            lines.append(f'        <CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV Data - {safe_name}" enabled="true">')
            lines.append(f'          <stringProp name="filename">/work/data/{_esc_xml(dp.name)}.csv</stringProp><stringProp name="variableNames">{safe_name}</stringProp><boolProp name="ignoreFirstLine">true</boolProp><stringProp name="delimiter">,</stringProp><boolProp name="recycle">true</boolProp><boolProp name="stopThread">false</boolProp>')
            lines.append('        </CSVDataSet><hashTree/>')
        if wl.model == "open" and wl.target_rate_rps:
            target_cph = wl.target_rate_rps * 3600
            lines.append('        <ConstantThroughputTimer guiclass="TestBeanGUI" testclass="ConstantThroughputTimer" testname="Throughput Timer" enabled="true">')
            lines.append(f'          <stringProp name="throughput">{target_cph}</stringProp><stringProp name="calcMode">1</stringProp>')
            lines.append('        </ConstantThroughputTimer><hashTree/>')
        for i, target in enumerate(targets):
            self._append_sampler(lines, plan, target, i, indent="        ")
        lines.append('      </hashTree>')

    def _append_sampler(self, lines: list[str], plan: PerfPlan, target: Any, index: int, *, indent: str) -> None:
        url = self.rewrite_target_url(target.url)
        parsed_url = urlparse(url)
        host = parsed_url.hostname or "localhost"
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        protocol = parsed_url.scheme or "http"
        path = parsed_url.path or "/"
        if parsed_url.query:
            path = f"{path}?{parsed_url.query}"
        bindings = correlation_bindings(plan)
        destination_bindings = [b for b in bindings if b["destination"] == index]
        source_bindings = [b for b in bindings if b["source"] == index]
        sampler_name = _esc_xml(target.name or f"Sampler-{index}")
        lines.append(f'{indent}<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{sampler_name}" enabled="true">')
        lines.append(f'{indent}  <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true"><collectionProp name="Arguments.arguments"/></elementProp>')
        lines.append(f'{indent}  <stringProp name="HTTPSampler.domain">{_esc_xml(host)}</stringProp>')
        lines.append(f'{indent}  <stringProp name="HTTPSampler.port">{port}</stringProp>')
        lines.append(f'{indent}  <stringProp name="HTTPSampler.protocol">{_esc_xml(protocol)}</stringProp>')
        lines.append(f'{indent}  <stringProp name="HTTPSampler.contentEncoding"></stringProp>')
        lines.append(f'{indent}  <stringProp name="HTTPSampler.path">{_esc_xml(path)}</stringProp>')
        lines.append(f'{indent}  <stringProp name="HTTPSampler.method">{_esc_xml(target.method.upper())}</stringProp>')
        lines.append(f'{indent}  <boolProp name="HTTPSampler.follow_redirects">true</boolProp><boolProp name="HTTPSampler.auto_redirects">false</boolProp><boolProp name="HTTPSampler.use_keepalive">true</boolProp><boolProp name="HTTPSampler.DO_MULTIPART_POST">false</boolProp>')
        lines.append(f'{indent}  <stringProp name="HTTPSampler.embedded_url_re"></stringProp><stringProp name="HTTPSampler.connect_timeout"></stringProp><stringProp name="HTTPSampler.response_timeout"></stringProp>')
        lines.append(f'{indent}</HTTPSamplerProxy>')
        lines.append(f'{indent}<hashTree>')
        if plan.workload.think_time_ms > 0:
            lines.append(f'{indent}  <ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Think Time" enabled="true"><stringProp name="ConstantTimer.delay">{plan.workload.think_time_ms}</stringProp></ConstantTimer><hashTree/>')
        vus = plan.workload.virtual_users or 50
        if plan.workload.mode == "spike" or vus > 100:
            lines.append(f'{indent}  <SyncTimer guiclass="TestBeanGUI" testclass="SyncTimer" testname="Sync Timer" enabled="true"><intProp name="groupSize">{min(vus, 500)}</intProp><longProp name="timeoutInMs">10000</longProp></SyncTimer><hashTree/>')
        headers = dict(target.headers)
        for binding in destination_bindings:
            corr = binding["correlation"]
            headers[corr.into_header] = (corr.header_prefix or "") + '${qa_corr_' + str(binding["index"]) + '}'
        if headers:
            lines.append(f'{indent}  <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="HTTP Header Manager" enabled="true"><collectionProp name="HeaderManager.headers">')
            for hk, hv in headers.items():
                lines.append(f'{indent}    <elementProp name="{_esc_xml(hk)}" elementType="Header"><stringProp name="Header.name">{_esc_xml(hk)}</stringProp><stringProp name="Header.value">{_esc_xml(hv)}</stringProp></elementProp>')
            lines.append(f'{indent}  </collectionProp></HeaderManager><hashTree/>')
        expected = plan.smoke.expect_status or []
        if expected:
            lines.append(f'{indent}  <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Expected status" enabled="true">')
            lines.append(f'{indent}    <collectionProp name="Asserion.test_strings">')
            for n, status in enumerate(expected):
                lines.append(f'{indent}      <stringProp name="status{index}_{n}">{status}</stringProp>')
            test_type = 8 | (32 if len(expected) > 1 else 0)
            lines.append(f'{indent}    </collectionProp><stringProp name="Assertion.custom_message">Unexpected HTTP status</stringProp><stringProp name="Assertion.test_field">Assertion.response_code</stringProp><boolProp name="Assertion.assume_success">true</boolProp><intProp name="Assertion.test_type">{test_type}</intProp>')
            lines.append(f'{indent}  </ResponseAssertion><hashTree/>')
        for binding in source_bindings:
            corr = binding["correlation"]
            ref = f'qa_corr_{binding["index"]}'
            lines.append(f'{indent}  <JMESPathExtractor guiclass="JMESPathExtractorGui" testclass="JMESPathExtractor" testname="Extract {ref}" enabled="true"><stringProp name="JMESExtractor.referenceName">{ref}</stringProp><stringProp name="JMESExtractor.jmesPathExpr">{_esc_xml(corr.from_path)}</stringProp><stringProp name="JMESExtractor.defaultValue"></stringProp><stringProp name="JMESExtractor.matchNumber">1</stringProp></JMESPathExtractor><hashTree/>')
            self._append_script_element(lines, indent, "JSR223PostProcessor", f"Observe extraction {binding['index']}",
                f"def value = vars.get('{ref}'); vars.put('qa_extract_{binding['index']}', "
                "String.valueOf(value != null && !value.isEmpty() && value != 'null'))")
        for binding in destination_bindings:
            self._append_script_element(lines, indent, "JSR223PreProcessor", f"Observe header binding {binding['index']}",
                f"vars.put('qa_dependency_{binding['index']}', "
                f"String.valueOf(vars.get('qa_extract_{binding['index']}') == 'true'))")
        lines.append(f'{indent}</hashTree>')

    @staticmethod
    def _append_script_element(lines, indent, kind, name, script):
        # JMeter's standard sample_variables stores boolean facts only. Values
        # used for request headers remain in the thread-local variable store.
        lines.append(f'{indent}  <{kind} guiclass="TestBeanGUI" testclass="{kind}" testname="{name}" enabled="true"><stringProp name="cacheKey">true</stringProp><stringProp name="filename"></stringProp><stringProp name="parameters"></stringProp><stringProp name="script">{_esc_xml(script)}</stringProp><stringProp name="scriptLanguage">groovy</stringProp></{kind}><hashTree/>')

def _esc_xml(text: str) -> str:
    """Escape a string for safe inclusion in XML attribute/text."""
    return (text
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
