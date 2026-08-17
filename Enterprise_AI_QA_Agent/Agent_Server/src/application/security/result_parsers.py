"""Security Tool Result Parsers.

Parse raw CLI output from security tools into structured data.
"""
from __future__ import annotations

import json
import re
from typing import Any


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseSecurityParser:
    """Base class for security tool output parsers."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        raise NotImplementedError


def evaluate_parser_success(
    parser_key: str,
    parsed: dict[str, Any],
    *,
    exit_code: int | None,
    timed_out: bool,
) -> tuple[bool, str]:
    """Require positive protocol evidence for response-oriented profiles."""
    if timed_out:
        return False, "execution_timeout"
    if exit_code != 0:
        return False, f"exit_code={exit_code}"
    if parser_key in {"httpx", "whatweb"}:
        results = parsed.get("results")
        if not isinstance(results, list) or not results:
            return False, "target_response_not_observed"
    if parser_key == "http_headers":
        status_code = parsed.get("status_code")
        if not isinstance(status_code, int) or not 100 <= status_code <= 599:
            return False, "target_response_not_observed"
    return True, ""


# ---------------------------------------------------------------------------
# Nmap
# ---------------------------------------------------------------------------

class NmapParser(BaseSecurityParser):
    """Parse nmap text output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        hosts: list[dict[str, Any]] = []
        open_ports: list[dict[str, Any]] = []
        current_host = ""

        for line in raw_output.splitlines():
            line = line.strip()

            # Host line
            host_match = re.match(r"Nmap scan report for (.+)", line)
            if host_match:
                current_host = host_match.group(1).strip()
                hosts.append({"host": current_host, "ports": []})
                continue

            # Open port line: 80/tcp   open  http   Apache httpd 2.4.41
            port_match = re.match(
                r"(\d+)/(tcp|udp)\s+(open|filtered|closed)\s+(\S+)(?:\s+(.+))?", line
            )
            if port_match and current_host:
                port_info = {
                    "port": int(port_match.group(1)),
                    "protocol": port_match.group(2),
                    "state": port_match.group(3),
                    "service": port_match.group(4),
                    "version": (port_match.group(5) or "").strip(),
                    "host": current_host,
                }
                open_ports.append(port_info)
                if hosts:
                    hosts[-1]["ports"].append(port_info)

        return {
            "tool": "nmap",
            "hosts": hosts,
            "open_ports": open_ports,
            "host_count": len(hosts),
            "open_port_count": len(open_ports),
        }


# ---------------------------------------------------------------------------
# Httpx
# ---------------------------------------------------------------------------

class HttpxParser(BaseSecurityParser):
    """Parse httpx output (one JSON per line or plain text)."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        results: list[dict[str, Any]] = []

        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            # Try JSON line
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    results.append({
                        "url": obj.get("url") or obj.get("input", ""),
                        "status_code": obj.get("status-code") or obj.get("status_code"),
                        "title": obj.get("title", ""),
                        "technologies": obj.get("tech") or obj.get("technologies") or [],
                        "content_length": obj.get("content-length"),
                        "webserver": obj.get("webserver", ""),
                    })
                    continue
                except json.JSONDecodeError:
                    pass
            # Plain text: https://example.com [200] [Title] [Apache]
            plain_match = re.match(r"(https?://\S+)\s+\[(\d+)\](?:\s+\[([^\]]+)\])?(?:\s+\[([^\]]+)\])?", line)
            if plain_match:
                results.append({
                    "url": plain_match.group(1),
                    "status_code": int(plain_match.group(2)),
                    "title": plain_match.group(3) or "",
                    "technologies": [plain_match.group(4)] if plain_match.group(4) else [],
                })

        return {
            "tool": "httpx",
            "results": results,
            "live_count": len(results),
        }


# ---------------------------------------------------------------------------
# WhatWeb
# ---------------------------------------------------------------------------

class WhatwebParser(BaseSecurityParser):
    """Parse whatweb output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        results: list[dict[str, Any]] = []

        for line in raw_output.splitlines():
            # WhatWeb enables ANSI styling by default in the Kali image. The
            # control sequences split the URL/status tokens and previously
            # made a reachable target look semantically unreachable.
            line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
            if not line or line.startswith("#"):
                continue
            # URL [status] [tech1, tech2, ...]
            url_match = re.match(r"(https?://\S+)\s+\[(\d+)\s+[^\]]+\]\s*(.*)", line)
            if url_match:
                url = url_match.group(1)
                status = int(url_match.group(2))
                tech_raw = url_match.group(3)
                technologies = [t.strip() for t in re.split(r",\s*(?=[A-Z])", tech_raw) if t.strip()]
                results.append({
                    "url": url,
                    "status_code": status,
                    "technologies": technologies,
                })

        return {
            "tool": "whatweb",
            "results": results,
        }


# ---------------------------------------------------------------------------
# HTTP Headers
# ---------------------------------------------------------------------------

class HttpHeadersParser(BaseSecurityParser):
    """Parse controlled HTTP header probe output."""

    SECURITY_HEADERS = [
        "strict-transport-security",
        "content-security-policy",
        "x-frame-options",
        "x-content-type-options",
        "referrer-policy",
        "permissions-policy",
    ]

    def parse(self, raw_output: str) -> dict[str, Any]:
        parsed: dict[str, Any] | None = None
        for line in raw_output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("headers"), dict):
                parsed = candidate
                break

        if parsed is None:
            parsed = self._parse_header_block(raw_output)

        headers = {
            str(key).lower(): str(value)
            for key, value in (parsed.get("headers") or {}).items()
            if str(key).strip()
        }
        missing = parsed.get("missing_security_headers")
        if not isinstance(missing, list):
            missing = [header for header in self.SECURITY_HEADERS if header not in headers]

        return {
            "tool": "http_headers",
            "url": str(parsed.get("url") or ""),
            "status_code": parsed.get("status_code"),
            "headers": headers,
            "header_count": len(headers),
            "missing_security_headers": [str(item).lower() for item in missing],
            "present_security_headers": [header for header in self.SECURITY_HEADERS if header in headers],
        }

    def _parse_header_block(self, raw_output: str) -> dict[str, Any]:
        headers: dict[str, str] = {}
        status_code: int | None = None
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            status_match = re.match(r"HTTP/\S+\s+(\d+)", line, flags=re.IGNORECASE)
            if status_match:
                status_code = int(status_match.group(1))
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip():
                headers[key.strip().lower()] = value.strip()
        return {"status_code": status_code, "headers": headers}


# ---------------------------------------------------------------------------
# Ffuf
# ---------------------------------------------------------------------------

class FfufParser(BaseSecurityParser):
    """Parse ffuf JSON output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        # Try to parse as JSON
        try:
            data = json.loads(raw_output)
            results = data.get("results") or []
            findings = [
                {
                    "url": r.get("url", ""),
                    "status": r.get("status"),
                    "length": r.get("length"),
                    "words": r.get("words"),
                    "lines": r.get("lines"),
                    "input": r.get("input", {}).get("FUZZ", ""),
                }
                for r in results
            ]
            return {
                "tool": "ffuf",
                "findings": findings,
                "finding_count": len(findings),
            }
        except (json.JSONDecodeError, AttributeError):
            pass

        # Plain text fallback
        findings = []
        for line in raw_output.splitlines():
            line = line.strip()
            # [Status: 200, Size: 1234, Words: 56, Lines: 78] /path
            match = re.search(r"\[Status:\s*(\d+).*?\]\s+(\S+)", line)
            if match:
                findings.append({
                    "status": int(match.group(1)),
                    "url": match.group(2),
                })

        return {
            "tool": "ffuf",
            "findings": findings,
            "finding_count": len(findings),
        }


# ---------------------------------------------------------------------------
# Nikto
# ---------------------------------------------------------------------------

class NiktoParser(BaseSecurityParser):
    """Parse nikto text output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        target = ""

        for line in raw_output.splitlines():
            line = line.strip()
            if line.startswith("+ Target IP:") or line.startswith("+ Target Host:"):
                target = line.split(":", 1)[-1].strip()
            elif line.startswith("+ ") and "OSVDB" not in line and len(line) > 5:
                # Finding line
                finding_text = line[2:].strip()
                if finding_text and not finding_text.startswith("Target") and not finding_text.startswith("Start"):
                    findings.append({
                        "description": finding_text,
                        "target": target,
                    })

        return {
            "tool": "nikto",
            "target": target,
            "findings": findings,
            "finding_count": len(findings),
        }


# ---------------------------------------------------------------------------
# Nuclei
# ---------------------------------------------------------------------------

class NucleiParser(BaseSecurityParser):
    """Parse nuclei JSON output (one JSON per line)."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []

        for line in raw_output.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
                info = obj.get("info") or {}
                findings.append({
                    "template_id": obj.get("template-id") or obj.get("templateID", ""),
                    "name": info.get("name", ""),
                    "severity": info.get("severity", "info"),
                    "description": info.get("description", ""),
                    "url": obj.get("matched-at") or obj.get("host", ""),
                    "type": obj.get("type", ""),
                    "tags": info.get("tags") or [],
                    "reference": info.get("reference") or [],
                    "cve": self._extract_cve(info),
                })
            except json.JSONDecodeError:
                continue

        return {
            "tool": "nuclei",
            "findings": findings,
            "finding_count": len(findings),
            "critical_count": sum(1 for f in findings if f["severity"] == "critical"),
            "high_count": sum(1 for f in findings if f["severity"] == "high"),
            "medium_count": sum(1 for f in findings if f["severity"] == "medium"),
        }

    def _extract_cve(self, info: dict) -> str:
        classification = info.get("classification") or {}
        cve_ids = classification.get("cve-id") or []
        if isinstance(cve_ids, list) and cve_ids:
            return cve_ids[0]
        if isinstance(cve_ids, str):
            return cve_ids
        return ""


# ---------------------------------------------------------------------------
# Sqlmap
# ---------------------------------------------------------------------------

class SqlmapParser(BaseSecurityParser):
    """Parse sqlmap output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        vulnerable = False
        injections: list[dict[str, Any]] = []
        dbms = ""

        for line in raw_output.splitlines():
            line = line.strip()
            if "is vulnerable" in line.lower() or "sqlmap identified" in line.lower():
                vulnerable = True
            if "back-end DBMS:" in line:
                dbms = line.split(":", 1)[-1].strip()
            # Parameter injection line
            param_match = re.search(r"Parameter:\s+(.+?)\s+\((.+?)\)", line)
            if param_match:
                injections.append({
                    "parameter": param_match.group(1),
                    "type": param_match.group(2),
                })

        return {
            "tool": "sqlmap",
            "vulnerable": vulnerable,
            "dbms": dbms,
            "injections": injections,
            "injection_count": len(injections),
        }


# ---------------------------------------------------------------------------
# Hydra
# ---------------------------------------------------------------------------

class HydraParser(BaseSecurityParser):
    """Parse hydra output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        credentials: list[dict[str, Any]] = []

        for line in raw_output.splitlines():
            line = line.strip()
            # [port][service] host: user  password: pass
            cred_match = re.search(
                r"\[(\d+)\]\[(\w+)\]\s+host:\s+(\S+)\s+login:\s+(\S+)\s+password:\s+(\S+)",
                line,
            )
            if cred_match:
                credentials.append({
                    "port": int(cred_match.group(1)),
                    "service": cred_match.group(2),
                    "host": cred_match.group(3),
                    "username": cred_match.group(4),
                    "password": cred_match.group(5),
                })

        return {
            "tool": "hydra",
            "credentials_found": credentials,
            "credential_count": len(credentials),
        }


# ---------------------------------------------------------------------------
# SSLScan
# ---------------------------------------------------------------------------

class SslscanParser(BaseSecurityParser):
    """Parse sslscan output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        issues: list[str] = []
        protocols: list[str] = []
        ciphers: list[str] = []

        for line in raw_output.splitlines():
            line = line.strip()
            # Enabled protocols
            if re.match(r"(SSLv2|SSLv3|TLSv1\.0|TLSv1\.1)\s+enabled", line, re.IGNORECASE):
                protocols.append(line)
                issues.append(f"弱协议已启用: {line}")
            # Weak ciphers
            if "RC4" in line or "DES" in line or "NULL" in line or "EXPORT" in line:
                ciphers.append(line)
                issues.append(f"弱加密套件: {line}")

        return {
            "tool": "sslscan",
            "issues": issues,
            "weak_protocols": protocols,
            "weak_ciphers": ciphers,
            "issue_count": len(issues),
        }


# ---------------------------------------------------------------------------
# Searchsploit
# ---------------------------------------------------------------------------

class SearchsploitParser(BaseSecurityParser):
    """Parse searchsploit JSON output."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        try:
            data = json.loads(raw_output)
            exploits = data.get("RESULTS_EXPLOIT") or []
            shellcodes = data.get("RESULTS_SHELLCODE") or []
            results = [
                {
                    "title": e.get("Title", ""),
                    "path": e.get("Path", ""),
                    "type": e.get("Type", ""),
                    "platform": e.get("Platform", ""),
                    "date": e.get("Date", ""),
                    "edb_id": e.get("EDB-ID", ""),
                }
                for e in exploits
            ]
            return {
                "tool": "searchsploit",
                "exploits": results,
                "shellcodes": shellcodes,
                "exploit_count": len(results),
            }
        except (json.JSONDecodeError, AttributeError):
            # Plain text fallback
            results = []
            for line in raw_output.splitlines():
                if "|" in line and not line.startswith("-") and not line.startswith("="):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 2:
                        results.append({"title": parts[0], "path": parts[1]})
            return {
                "tool": "searchsploit",
                "exploits": results,
                "exploit_count": len(results),
            }


class TcpdumpParser(BaseSecurityParser):
    """Parse bounded textual tcpdump output without retaining packet payloads."""

    def parse(self, raw_output: str) -> dict[str, Any]:
        packet_lines = [
            line.strip()
            for line in raw_output.splitlines()
            if line.strip() and not line.lower().startswith("tcpdump:")
        ]
        endpoints: set[str] = set()
        for line in packet_lines:
            endpoints.update(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:\.\d+)?\b", line))
        return {
            "tool": "tcpdump",
            "packet_count": len(packet_lines),
            "endpoints": sorted(endpoints)[:50],
            "sample": packet_lines[:20],
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class SecurityResultParserRegistry:
    """Registry of all security tool output parsers."""

    def __init__(self) -> None:
        specs: dict[str, tuple[BaseSecurityParser, tuple[str, ...]]] = {
            "nmap": (
                NmapParser(),
                ("tool", "hosts", "open_ports", "host_count", "open_port_count"),
            ),
            "httpx": (HttpxParser(), ("tool", "results", "live_count")),
            "whatweb": (WhatwebParser(), ("tool", "results")),
            "http_headers": (
                HttpHeadersParser(),
                (
                    "tool",
                    "url",
                    "status_code",
                    "headers",
                    "header_count",
                    "missing_security_headers",
                    "present_security_headers",
                ),
            ),
            "ffuf": (FfufParser(), ("tool", "findings", "finding_count")),
            "gobuster": (FfufParser(), ("tool", "findings", "finding_count")),
            "nikto": (
                NiktoParser(),
                ("tool", "target", "findings", "finding_count"),
            ),
            "nuclei": (
                NucleiParser(),
                (
                    "tool",
                    "findings",
                    "finding_count",
                    "critical_count",
                    "high_count",
                    "medium_count",
                ),
            ),
            "sqlmap": (
                SqlmapParser(),
                ("tool", "vulnerable", "dbms", "injections", "injection_count"),
            ),
            "hydra": (HydraParser(), ("tool", "credentials_found", "credential_count")),
            "sslscan": (
                SslscanParser(),
                ("tool", "issues", "weak_protocols", "weak_ciphers", "issue_count"),
            ),
            "searchsploit": (
                SearchsploitParser(),
                ("tool", "exploits", "exploit_count"),
            ),
            "tcpdump": (TcpdumpParser(), ("tool", "packet_count", "endpoints", "sample")),
        }
        self._parsers = {key: spec[0] for key, spec in specs.items()}
        self._required_fields = {key: spec[1] for key, spec in specs.items()}

    def parse(self, parser_key: str, raw_output: str) -> dict[str, Any]:
        """Parse raw tool output using the registered parser."""
        parser = self._parsers.get(parser_key)
        if parser is None:
            return {
                "tool": parser_key,
                "raw": raw_output[:2000],
                "parse_error": f"No parser registered for '{parser_key}'",
            }
        try:
            return parser.parse(raw_output)
        except Exception as e:
            return {
                "tool": parser_key,
                "raw": raw_output[:2000],
                "parse_error": str(e),
            }

    def has_parser(self, parser_key: str) -> bool:
        return parser_key in self._parsers

    def required_fields(self, parser_key: str) -> list[str]:
        if parser_key not in self._parsers:
            return []
        return list(self._required_fields[parser_key])


# Module-level singleton
_parser_registry: SecurityResultParserRegistry | None = None


def get_parser_registry() -> SecurityResultParserRegistry:
    global _parser_registry
    if _parser_registry is None:
        _parser_registry = SecurityResultParserRegistry()
    return _parser_registry


__all__ = [
    "evaluate_parser_success",
    "BaseSecurityParser",
    "NmapParser",
    "HttpxParser",
    "WhatwebParser",
    "HttpHeadersParser",
    "FfufParser",
    "NiktoParser",
    "NucleiParser",
    "SqlmapParser",
    "HydraParser",
    "SslscanParser",
    "SearchsploitParser",
    "SecurityResultParserRegistry",
    "get_parser_registry",
]
