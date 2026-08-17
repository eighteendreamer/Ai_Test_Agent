"""Security Command Profiles.

Defines structured command profiles for security tools.
Each profile maps to a specific tool invocation with controlled parameters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.application.security.finding_normalizer import FindingNormalizer
from src.application.security.result_parsers import get_parser_registry


RUNNER_SUCCESS_OPERATORS = ["equals", "not_equals"]
COUNT_ASSERTION_OPERATORS = ["equals", "not_equals", "lt", "lte", "gt", "gte"]
PARSED_FIELD_OPERATORS = [
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "lt",
    "lte",
    "gt",
    "gte",
]


@dataclass
class SecurityCommandProfile:
    """Defines a structured, controlled security tool invocation."""

    profile_key: str
    tool_name: str
    command_template: str          # Python str.format() template
    description: str = ""
    tool_family: str = ""          # network_recon / web_scan / service_audit / ...
    surface_types: list[str] = field(default_factory=list)
    allowed_arguments: list[str] = field(default_factory=list)
    timeout_seconds: int = 120
    risk_level: str = "low"        # info / low / medium / high / critical
    requires_approval: bool = False
    parser_key: str = ""           # key for result_parsers registry
    artifact_policy: str = "always"  # always / on_finding / never
    notes: str = ""


class SecurityCommandProfileRegistry:
    """Registry of all available security command profiles."""

    def __init__(self) -> None:
        self._profiles: dict[str, SecurityCommandProfile] = {}
        self._register_defaults()

    def register(self, profile: SecurityCommandProfile) -> None:
        self._profiles[profile.profile_key] = profile

    def get(self, profile_key: str) -> SecurityCommandProfile | None:
        return self._profiles.get(profile_key)

    def list_all(self) -> list[SecurityCommandProfile]:
        return list(self._profiles.values())

    def list_by_family(self, tool_family: str) -> list[SecurityCommandProfile]:
        return [p for p in self._profiles.values() if p.tool_family == tool_family]

    def list_by_surface(self, surface_type: str) -> list[SecurityCommandProfile]:
        return [p for p in self._profiles.values() if surface_type in p.surface_types]

    def verification_capabilities(self, profile_key: str) -> dict[str, Any]:
        profile = self.get(profile_key)
        if profile is None:
            raise KeyError(f"Unknown security command profile: {profile_key}")
        parser_registry = get_parser_registry()
        parsed_fields = parser_registry.required_fields(profile.parser_key)
        assertions: dict[str, list[str]] = {
            "runner_success": list(RUNNER_SUCCESS_OPERATORS),
        }
        if FindingNormalizer.supports_parser(profile.parser_key):
            assertions["finding_count"] = list(COUNT_ASSERTION_OPERATORS)
        if parsed_fields:
            assertions["parsed_field"] = list(PARSED_FIELD_OPERATORS)
        return {
            "structured_output": bool(parsed_fields),
            "parsed_fields": parsed_fields,
            "assertions": assertions,
        }

    def validate_assertion(
        self,
        profile_key: str,
        *,
        kind: str,
        target: str,
        operator: str,
    ) -> str | None:
        kind = str(kind or "").strip().lower()
        target = str(target or "").strip()
        operator = str(operator or "equals").strip().lower()
        capabilities = self.verification_capabilities(profile_key)
        operators = capabilities["assertions"].get(kind)
        if operators is None:
            return "unsupported_assertion_kind"
        if operator not in operators:
            return "unsupported_assertion_operator"
        if kind == "parsed_field" and target not in capabilities["parsed_fields"]:
            return "unsupported_assertion_target"
        return None

    def build_command(self, profile_key: str, arguments: dict[str, Any]) -> str | None:
        """Render the command template with provided arguments."""
        profile = self.get(profile_key)
        if profile is None:
            return None
        try:
            return profile.command_template.format(**arguments)
        except KeyError:
            return profile.command_template

    def _register_defaults(self) -> None:
        """Register all built-in Phase 1 profiles."""

        # ── Network Recon ──────────────────────────────────────────────

        self.register(SecurityCommandProfile(
            profile_key="nmap_tcp_basic",
            tool_name="nmap",
            command_template="nmap -sS -T4 --open -p 1-1000 {target} -oN -",
            description="基础 TCP SYN 扫描，扫描常用端口",
            tool_family="network_recon",
            surface_types=["network", "host"],
            allowed_arguments=["target"],
            timeout_seconds=120,
            risk_level="low",
            requires_approval=False,
            parser_key="nmap",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="nmap_service_detect",
            tool_name="nmap",
            command_template="nmap -sV -T4 --open -p {ports} {target} -oN -",
            description="服务版本探测",
            tool_family="network_recon",
            surface_types=["network", "host", "service"],
            allowed_arguments=["target", "ports"],
            timeout_seconds=180,
            risk_level="low",
            requires_approval=False,
            parser_key="nmap",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="nmap_full_scan",
            tool_name="nmap",
            command_template="nmap -sV -sC -T4 --open -p- {target} -oN -",
            description="全端口扫描含脚本检测",
            tool_family="network_recon",
            surface_types=["network", "host"],
            allowed_arguments=["target"],
            timeout_seconds=600,
            risk_level="medium",
            requires_approval=False,
            parser_key="nmap",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="nmap_os_detect",
            tool_name="nmap",
            command_template="nmap -O -T4 {target} -oN -",
            description="操作系统指纹识别",
            tool_family="network_recon",
            surface_types=["host"],
            allowed_arguments=["target"],
            timeout_seconds=120,
            risk_level="low",
            requires_approval=False,
            parser_key="nmap",
            artifact_policy="always",
        ))

        # ── Web Scanning ───────────────────────────────────────────────

        self.register(SecurityCommandProfile(
            profile_key="httpx_probe",
            tool_name="httpx",
            command_template=(
                "sh -lc '"
                "TARGET=\"$1\"; export TARGET; "
                "if httpx -h 2>&1 | grep -q -- \"-status-code\"; then "
                "httpx -u \"$TARGET\" -title -status-code -tech-detect -follow-redirects -silent; "
                "else "
                "python3 -c \"import os,re,urllib.request; "
                "u=os.environ.get(\\\"TARGET\\\", \\\"\\\"); "
                "req=urllib.request.Request(u, headers={{\\\"User-Agent\\\":\\\"Enterprise-AI-QA-Agent security-smoke\\\"}}); "
                "NoRaise=type(\\\"NoRaise\\\", (urllib.request.HTTPErrorProcessor,), "
                "{{\\\"http_response\\\": lambda self, request, response: response, "
                "\\\"https_response\\\": lambda self, request, response: response}}); "
                "r=urllib.request.build_opener(NoRaise).open(req, timeout=20); "
                "body=r.read(200000).decode(\\\"utf-8\\\", \\\"ignore\\\"); "
                "m=re.search(\\\"<title[^>]*>(.*?)</title>\\\", body, re.I|re.S); "
                "title=re.sub(r\\\"\\\\s+\\\", \\\" \\\", m.group(1)).strip() if m else \\\"\\\"; "
                "server=r.headers.get(\\\"server\\\", \\\"\\\"); "
                "print(\\\"%s [%s] [%s] [%s]\\\" % (r.geturl(), getattr(r, \\\"status\\\", 0), title, server))\"; "
                "fi"
                "' sh {target}"
            ),
            description="HTTP probe and technology fingerprint",
            tool_family="web_scan",
            surface_types=["web", "api"],
            allowed_arguments=["target"],
            timeout_seconds=60,
            risk_level="info",
            requires_approval=False,
            parser_key="httpx",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="whatweb_fingerprint",
            tool_name="whatweb",
            command_template="whatweb -a 3 {target}",
            description="Web application fingerprinting",
            tool_family="web_scan",
            surface_types=["web"],
            allowed_arguments=["target"],
            timeout_seconds=60,
            risk_level="info",
            requires_approval=False,
            parser_key="whatweb",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="http_headers_probe",
            tool_name="python3",
            command_template=(
                "sh -lc '"
                "TARGET=\"$1\"; export TARGET; "
                "python3 -c \"import json,os,urllib.request; "
                "u=os.environ.get(\\\"TARGET\\\", \\\"\\\"); "
                "req=urllib.request.Request(u, headers=dict([(\\\"User-Agent\\\",\\\"Enterprise-AI-QA-Agent security-smoke\\\")])); "
                "NoRaise=type(\\\"NoRaise\\\", (urllib.request.HTTPErrorProcessor,), "
                "dict(http_response=lambda self, request, response: response, "
                "https_response=lambda self, request, response: response)); "
                "r=urllib.request.build_opener(NoRaise).open(req, timeout=20); "
                "headers=dict((k.lower(), v) for k, v in r.headers.items()); "
                "security=[\\\"strict-transport-security\\\",\\\"content-security-policy\\\","
                "\\\"x-frame-options\\\",\\\"x-content-type-options\\\",\\\"referrer-policy\\\","
                "\\\"permissions-policy\\\"]; "
                "print(json.dumps(dict(url=r.geturl(), status_code=getattr(r, \\\"status\\\", 0), "
                "headers=headers, missing_security_headers=[h for h in security if h not in headers]), "
                "ensure_ascii=False))\""
                "' sh {target}"
            ),
            description="HTTP security header baseline probe",
            tool_family="web_scan",
            surface_types=["web", "api"],
            allowed_arguments=["target"],
            timeout_seconds=45,
            risk_level="info",
            requires_approval=False,
            parser_key="http_headers",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="ffuf_common_dirs",
            tool_name="ffuf",
            command_template=(
                "ffuf -u {target}/FUZZ -w {wordlist} "
                "-mc 200,301,302,403 -t 50 -timeout 10 -o {ffuf_output} -of json"
            ),
            description="常见目录爆破",
            tool_family="web_scan",
            surface_types=["web"],
            allowed_arguments=["target", "wordlist", "ffuf_output"],
            timeout_seconds=180,
            risk_level="low",
            requires_approval=False,
            parser_key="ffuf",
            artifact_policy="on_finding",
        ))

        self.register(SecurityCommandProfile(
            profile_key="gobuster_dirs",
            tool_name="gobuster",
            command_template=(
                "gobuster dir -u {target} "
                "-w {wordlist} "
                "-t 50 --timeout 10s -q"
            ),
            description="目录枚举（gobuster）",
            tool_family="web_scan",
            surface_types=["web"],
            allowed_arguments=["target", "wordlist"],
            timeout_seconds=180,
            risk_level="low",
            requires_approval=False,
            parser_key="gobuster",
            artifact_policy="on_finding",
        ))

        self.register(SecurityCommandProfile(
            profile_key="nikto_web_scan",
            tool_name="nikto",
            command_template="nikto -h {target} -ask no -nointeractive",
            description="Web 服务器漏洞扫描",
            tool_family="web_scan",
            surface_types=["web"],
            allowed_arguments=["target"],
            timeout_seconds=300,
            risk_level="medium",
            requires_approval=False,
            parser_key="nikto",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="nuclei_baseline",
            tool_name="nuclei",
            command_template="nuclei -u {target} -t {templates_dir} -severity low,medium,high,critical -silent -jsonl -duc",
            description="Nuclei baseline vulnerability scan",
            tool_family="web_scan",
            surface_types=["web", "api"],
            allowed_arguments=["target", "templates_dir"],
            timeout_seconds=300,
            risk_level="medium",
            requires_approval=False,
            parser_key="nuclei",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="nuclei_cve_scan",
            tool_name="nuclei",
            command_template="nuclei -u {target} -t {templates_dir} -tags cve -severity medium,high,critical -silent -jsonl -duc",
            description="Nuclei CVE 专项扫描",
            tool_family="web_scan",
            surface_types=["web", "api"],
            allowed_arguments=["target", "templates_dir"],
            timeout_seconds=300,
            risk_level="medium",
            requires_approval=False,
            parser_key="nuclei",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="sqlmap_readonly_probe",
            tool_name="sqlmap",
            command_template=(
                "sqlmap -u {target} --batch --level=1 --risk=1 "
                "--technique=B --no-cast --output-dir={sqlmap_output_dir}"
            ),
            description="SQL 注入只读探测（不修改数据）",
            tool_family="web_scan",
            surface_types=["web", "api"],
            allowed_arguments=["target", "sqlmap_output_dir"],
            timeout_seconds=300,
            risk_level="medium",
            requires_approval=False,
            parser_key="sqlmap",
            artifact_policy="on_finding",
            notes="只读模式，不执行写操作",
        ))

        # ── Service Audit ──────────────────────────────────────────────

        self.register(SecurityCommandProfile(
            profile_key="sslscan_tls_audit",
            tool_name="sslscan",
            command_template="sslscan --no-colour --connect-timeout=30 {target}",
            description="TLS/SSL configuration audit",
            tool_family="service_audit",
            surface_types=["web", "service"],
            allowed_arguments=["target"],
            timeout_seconds=120,
            risk_level="info",
            requires_approval=False,
            parser_key="sslscan",
            artifact_policy="always",
        ))

        self.register(SecurityCommandProfile(
            profile_key="searchsploit_lookup",
            tool_name="searchsploit",
            command_template="searchsploit {query} --json",
            description="ExploitDB 漏洞情报检索",
            tool_family="service_audit",
            surface_types=["host", "service", "web"],
            allowed_arguments=["query"],
            timeout_seconds=30,
            risk_level="info",
            requires_approval=False,
            parser_key="searchsploit",
            artifact_policy="on_finding",
        ))

        # ── Credential Attack (Phase 2) ────────────────────────────────

        self.register(SecurityCommandProfile(
            profile_key="hydra_basic_login",
            tool_name="hydra",
            command_template=(
                "hydra -L {userlist} -P {passlist} {target} {service} "
                "-t 4 -f -o {hydra_output}"
            ),
            description="基础凭证爆破（需审批）",
            tool_family="credential_attack",
            surface_types=["credential", "service"],
            allowed_arguments=["target", "service", "userlist", "passlist", "hydra_output"],
            timeout_seconds=300,
            risk_level="high",
            requires_approval=True,
            parser_key="hydra",
            artifact_policy="on_finding",
            notes="高风险操作，需要明确授权",
        ))

        # ── Traffic Analysis / Exploit Workbench (approval gated) ─────

        self.register(SecurityCommandProfile(
            profile_key="tcpdump_timed_capture",
            tool_name="tcpdump",
            command_template=(
                "timeout {duration_seconds} tcpdump -i any -nn -c {packet_count} host {target}"
            ),
            description="限时只读流量采样（需审批）",
            tool_family="traffic_analysis",
            surface_types=["network", "host", "service"],
            allowed_arguments=["target", "duration_seconds", "packet_count"],
            timeout_seconds=45,
            risk_level="medium",
            requires_approval=True,
            parser_key="tcpdump",
            artifact_policy="always",
            notes="只读限时抓包，不修改目标；需要明确授权。",
        ))

        self.register(SecurityCommandProfile(
            profile_key="searchsploit_exploit_lookup",
            tool_name="searchsploit",
            command_template="searchsploit {query} --json",
            description="ExploitDB 只读利用情报检索（需审批）",
            tool_family="exploit",
            surface_types=["host", "service", "web"],
            allowed_arguments=["query"],
            timeout_seconds=30,
            risk_level="low",
            requires_approval=True,
            parser_key="searchsploit",
            artifact_policy="on_finding",
            notes="仅检索公开利用信息，不运行 PoC；仍需明确授权。",
        ))

        self.register(SecurityCommandProfile(
            profile_key="msf_module_info",
            tool_name="msfconsole",
            command_template="msfconsole -q -x 'info {module_name}; exit'",
            description="Metasploit 模块元数据只读查询（需审批）",
            tool_family="exploit",
            surface_types=["host", "service", "web"],
            allowed_arguments=["module_name"],
            timeout_seconds=90,
            risk_level="medium",
            requires_approval=True,
            parser_key="",
            artifact_policy="always",
            notes="只允许 info 子命令，不设置目标或执行 exploit。",
        ))

        self.register(SecurityCommandProfile(
            profile_key="free_command",
            tool_name="controlled-command",
            command_template="{command}",
            description="受控自由安全命令（配置开启且需审批）",
            tool_family="general_scan",
            surface_types=["network", "host", "service", "web", "api"],
            allowed_arguments=["command", "target"],
            timeout_seconds=300,
            risk_level="high",
            requires_approval=True,
            parser_key="",
            artifact_policy="always",
            notes="仅在 security_runner_allow_free_command 开启后可用。",
        ))


# Module-level singleton
_registry: SecurityCommandProfileRegistry | None = None


def get_profile_registry() -> SecurityCommandProfileRegistry:
    global _registry
    if _registry is None:
        _registry = SecurityCommandProfileRegistry()
    return _registry


__all__ = [
    "SecurityCommandProfile",
    "SecurityCommandProfileRegistry",
    "get_profile_registry",
]
