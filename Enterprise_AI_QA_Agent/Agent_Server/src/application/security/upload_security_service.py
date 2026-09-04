from __future__ import annotations

from dataclasses import asdict, dataclass, field
import io
import mimetypes
from pathlib import Path
import re
from typing import Any
import zipfile

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.core.config import Settings


@dataclass(frozen=True)
class UploadSecurityFinding:
    category: str
    severity: str
    score: int
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadSecurityReport:
    profile: str
    filename: str
    declared_content_type: str
    detected_content_type: str
    extension: str
    size_bytes: int
    risk_score: int
    decision: str
    findings: list[UploadSecurityFinding] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "filename": self.filename,
            "declared_content_type": self.declared_content_type,
            "detected_content_type": self.detected_content_type,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "risk_score": self.risk_score,
            "decision": self.decision,
            "findings": [asdict(item) for item in self.findings],
        }


class UploadSecurityError(ValueError):
    def __init__(self, message: str, report: UploadSecurityReport) -> None:
        super().__init__(message)
        self.report = report


class UploadSecurityService:
    DANGEROUS_EXTENSIONS = {
        ".exe",
        ".dll",
        ".com",
        ".bat",
        ".cmd",
        ".msi",
        ".ps1",
        ".vbs",
        ".js",
        ".jar",
        ".scr",
        ".reg",
        ".hta",
        ".sh",
    }
    MACRO_EXTENSIONS = {".docm", ".xlsm", ".pptm", ".xlsb"}
    CHAT_ATTACHMENT_EXTENSIONS = {
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".md",
        ".json",
        ".png",
        ".pdf",
        ".txt",
        ".csv",
        ".xml",
        ".yaml",
        ".yml",
        ".html",
    }
    API_DOC_EXTENSIONS = {".json", ".yaml", ".yml", ".md", ".txt", ".log", ".pdf"}
    SKILL_EXTENSIONS = {".md", ".zip"}
    MALICIOUS_PATTERNS = [
        (re.compile(r"<script\b", re.IGNORECASE), "Detected embedded script tag."),
        (re.compile(r"\beval\s*\(", re.IGNORECASE), "Detected eval() pattern."),
        (re.compile(r"\bexec\s*\(", re.IGNORECASE), "Detected exec() pattern."),
        (re.compile(r"\bsystem\s*\(", re.IGNORECASE), "Detected system() pattern."),
        (re.compile(r"cmd\.exe", re.IGNORECASE), "Detected Windows command shell reference."),
        (re.compile(r"/bin/(?:ba)?sh", re.IGNORECASE), "Detected shell execution reference."),
    ]
    SENSITIVE_PATTERNS = [
        (re.compile(r"sk-[A-Za-z0-9]{20,}"), "Detected OpenAI-style API key."),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "Detected AWS access key id."),
        (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "Detected Google API key."),
        (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "Detected JWT token."),
        (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "Detected private key material."),
        (re.compile(r"(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^\s'\";]{6,}", re.IGNORECASE), "Detected sensitive credential-like assignment."),
    ]
    PDF_DANGEROUS_PATTERNS = [
        (re.compile(rb"/JavaScript\b"), "PDF contains JavaScript action."),
        (re.compile(rb"/OpenAction\b"), "PDF contains OpenAction trigger."),
        (re.compile(rb"/JS\b"), "PDF contains /JS action."),
    ]
    DANGEROUS_CONTENT_TYPES = {
        "application/x-dosexec",
        "application/x-msdownload",
        "application/x-executable",
        "application/x-elf",
        "application/x-mach-binary",
        "application/java-archive",
        "application/x-sh",
    }
    ZIP_STRUCTURE_EXTENSIONS = {".zip", ".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
    ZIP_DANGEROUS_ENTRY_EXTENSIONS = DANGEROUS_EXTENSIONS | MACRO_EXTENSIONS

    def __init__(self, *, settings: Settings, artifact_storage_service: ArtifactStorageService) -> None:
        self._settings = settings
        self._artifact_storage_service = artifact_storage_service

    async def secure_store_upload(
        self,
        *,
        content: bytes,
        filename: str,
        object_prefix: str,
        profile: str,
        source: str,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        size_bytes = len(content)
        declared_content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        detected_content_type = self._detect_content_type(content, filename, declared_content_type)
        temp_result = await self._artifact_storage_service.store_uploaded_bytes(
            content=content,
            filename=filename,
            object_prefix=f"{object_prefix}/temp",
            content_type=detected_content_type,
            bucket_name=self._settings.storage.rustfs_upload_temp_bucket,
        )
        report = self._scan_bytes(
            content=content,
            filename=filename,
            profile=profile,
            declared_content_type=declared_content_type,
            detected_content_type=detected_content_type,
            size_bytes=size_bytes,
        )

        if report.decision == "allow":
            final_result = await self._artifact_storage_service.move_object_uri(
                temp_result["uri"],
                bucket_name=self._settings.storage.rustfs_upload_safe_bucket,
                object_name=f"{object_prefix}/safe/{Path(filename).name}",
            )
            return {
                **final_result,
                "security_report": report.to_metadata(),
                "upload_source": source,
            }

        if report.decision == "quarantine":
            await self._artifact_storage_service.move_object_uri(
                temp_result["uri"],
                bucket_name=self._settings.storage.rustfs_upload_quarantine_bucket,
                object_name=f"{object_prefix}/quarantine/{Path(filename).name}",
            )
            raise UploadSecurityError(
                self._format_error_message("quarantined", report),
                report=report,
            ) from None

        await self._artifact_storage_service.delete_object_uri(temp_result["uri"])
        raise UploadSecurityError(self._format_error_message("rejected", report), report=report)

    def _scan_bytes(
        self,
        *,
        content: bytes,
        filename: str,
        profile: str,
        declared_content_type: str,
        detected_content_type: str,
        size_bytes: int,
    ) -> UploadSecurityReport:
        findings: list[UploadSecurityFinding] = []
        extension = Path(filename).suffix.lower()
        allowed_extensions = self._allowed_extensions(profile)
        text_preview = self._decode_text_preview(content, filename, detected_content_type, max_chars=200_000)

        if size_bytes > self._settings.security.upload_scan_max_bytes:
            findings.append(
                UploadSecurityFinding(
                    category="file_size",
                    severity="high",
                    score=80,
                    message=f"File exceeds the upload limit of {self._settings.security.upload_scan_max_bytes} bytes.",
                    metadata={"size_bytes": size_bytes},
                )
            )

        if extension not in allowed_extensions:
            findings.append(
                UploadSecurityFinding(
                    category="extension",
                    severity="high",
                    score=80,
                    message=f"Extension '{extension or '(none)'}' is not allowed for profile '{profile}'.",
                    metadata={"allowed_extensions": sorted(allowed_extensions)},
                )
            )

        if extension in self.DANGEROUS_EXTENSIONS:
            findings.append(
                UploadSecurityFinding(
                    category="dangerous_extension",
                    severity="high",
                    score=90,
                    message=f"Executable or script extension '{extension}' is blocked.",
                )
            )
        if extension in self.MACRO_EXTENSIONS:
            findings.append(
                UploadSecurityFinding(
                    category="macro_extension",
                    severity="high",
                    score=80,
                    message=f"Macro-enabled Office extension '{extension}' is blocked.",
                )
            )
        if detected_content_type in self.DANGEROUS_CONTENT_TYPES:
            findings.append(
                UploadSecurityFinding(
                    category="dangerous_content_type",
                    severity="high",
                    score=90,
                    message=f"Detected blocked content type '{detected_content_type}'.",
                )
            )

        if text_preview:
            findings.extend(self._scan_malicious_text(text_preview))
            findings.extend(self._scan_sensitive_text(text_preview))

        if detected_content_type == "application/pdf" or extension == ".pdf":
            findings.extend(self._scan_pdf_structure(content))

        if extension in self.ZIP_STRUCTURE_EXTENSIONS or detected_content_type == "application/zip":
            findings.extend(self._scan_zip_structure(content))

        risk_score = sum(item.score for item in findings)
        decision = "allow"
        if risk_score >= self._settings.security.upload_scan_high_risk_threshold:
            decision = "reject"
        elif risk_score >= self._settings.security.upload_scan_medium_risk_threshold:
            decision = "quarantine"

        return UploadSecurityReport(
            profile=profile,
            filename=filename,
            declared_content_type=declared_content_type,
            detected_content_type=detected_content_type,
            extension=extension,
            size_bytes=size_bytes,
            risk_score=risk_score,
            decision=decision,
            findings=findings,
        )

    def _scan_malicious_text(self, text: str) -> list[UploadSecurityFinding]:
        findings: list[UploadSecurityFinding] = []
        for pattern, message in self.MALICIOUS_PATTERNS:
            if pattern.search(text):
                findings.append(
                    UploadSecurityFinding(
                        category="malicious_pattern",
                        severity="medium",
                        score=30,
                        message=message,
                    )
                )
        return findings

    def _scan_sensitive_text(self, text: str) -> list[UploadSecurityFinding]:
        findings: list[UploadSecurityFinding] = []
        for pattern, message in self.SENSITIVE_PATTERNS:
            if pattern.search(text):
                findings.append(
                    UploadSecurityFinding(
                        category="sensitive_data",
                        severity="medium",
                        score=40,
                        message=message,
                    )
                )
        return findings

    def _scan_pdf_structure(self, content: bytes) -> list[UploadSecurityFinding]:
        findings: list[UploadSecurityFinding] = []
        for pattern, message in self.PDF_DANGEROUS_PATTERNS:
            if pattern.search(content):
                findings.append(
                    UploadSecurityFinding(
                        category="pdf_structure",
                        severity="high",
                        score=40,
                        message=message,
                    )
                )
        return findings

    def _scan_zip_structure(self, content: bytes) -> list[UploadSecurityFinding]:
        findings: list[UploadSecurityFinding] = []
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for member in archive.infolist():
                    member_name = member.filename.replace("\\", "/")
                    member_name_lower = member_name.lower()
                    member_extension = Path(member_name).suffix.lower()
                    if member_name.startswith("/") or ".." in Path(member_name).parts:
                        findings.append(
                            UploadSecurityFinding(
                                category="zip_structure",
                                severity="high",
                                score=80,
                                message="Zip archive contains path traversal entries.",
                                metadata={"entry": member.filename},
                            )
                        )
                    if member_extension in self.ZIP_DANGEROUS_ENTRY_EXTENSIONS:
                        findings.append(
                            UploadSecurityFinding(
                                category="zip_dangerous_entry",
                                severity="high",
                                score=80,
                                message=f"Zip archive contains blocked entry '{member.filename}'.",
                            )
                        )
                    if member_name_lower.endswith("vbaProject.bin".lower()):
                        findings.append(
                            UploadSecurityFinding(
                                category="zip_macro_payload",
                                severity="high",
                                score=80,
                                message=f"Zip archive contains macro payload '{member.filename}'.",
                            )
                        )
        except Exception:
            findings.append(
                UploadSecurityFinding(
                    category="zip_structure",
                    severity="high",
                    score=80,
                    message="Zip archive could not be parsed safely.",
                )
            )
        return findings

    def _allowed_extensions(self, profile: str) -> set[str]:
        if profile == "chat_attachment":
            return self.CHAT_ATTACHMENT_EXTENSIONS
        if profile == "api_document":
            return self.API_DOC_EXTENSIONS
        if profile == "skill_package":
            return self.SKILL_EXTENSIONS
        return self.CHAT_ATTACHMENT_EXTENSIONS | self.API_DOC_EXTENSIONS | self.SKILL_EXTENSIONS

    def _detect_content_type(self, content: bytes, filename: str, declared_content_type: str) -> str:
        extension = Path(filename).suffix.lower()
        try:
            import magic  # type: ignore

            detected = str(magic.from_buffer(content, mime=True) or "").strip()
            if detected:
                return detected
        except Exception:
            pass

        if content.startswith(b"MZ"):
            return "application/x-dosexec"
        if content.startswith(b"\x7fELF"):
            return "application/x-elf"
        if content.startswith((b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")):
            return "application/x-mach-binary"
        if content.startswith(b"%PDF-"):
            return "application/pdf"
        if content.startswith(b"PK\x03\x04"):
            if extension in {".docx"}:
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if extension in {".xlsx"}:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if extension in {".pptx"}:
                return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            if extension in {".docm"}:
                return "application/vnd.ms-word.document.macroEnabled.12"
            if extension in {".xlsm"}:
                return "application/vnd.ms-excel.sheet.macroEnabled.12"
            if extension in {".pptm"}:
                return "application/vnd.ms-powerpoint.presentation.macroEnabled.12"
            return "application/zip"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if self._decode_text_preview(content, filename, declared_content_type, max_chars=4096):
            return declared_content_type if declared_content_type.startswith("text/") else "text/plain"
        return declared_content_type or "application/octet-stream"

    def _decode_text_preview(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        *,
        max_chars: int,
    ) -> str:
        text_like = (
            content_type.startswith("text/")
            or content_type in {"application/json", "application/xml", "application/yaml", "application/x-yaml"}
            or Path(filename).suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml", ".xml", ".csv", ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css"}
        )
        if not text_like:
            return ""
        sample = content[: max_chars * 2]
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"):
            try:
                return sample.decode(encoding)[:max_chars]
            except UnicodeDecodeError:
                continue
        return ""

    def _format_error_message(self, action: str, report: UploadSecurityReport) -> str:
        if action == "quarantined":
            status_text = "已隔离"
            guidance = self._build_user_guidance(action, report)
            reasons = self._format_reason_list(report)
            if reasons and guidance:
                return f"上传未通过安全校验，文件{status_text}。原因：{reasons}。建议：{guidance}"
            if reasons:
                return f"上传未通过安全校验，文件{status_text}。原因：{reasons}"
            if guidance:
                return f"上传未通过安全校验，文件{status_text}。建议：{guidance}"
            return f"上传未通过安全校验，文件{status_text}。"

        status_text = "已拒绝"
        guidance = self._build_user_guidance(action, report)
        reasons = self._format_reason_list(report)
        if reasons and guidance:
            return f"该文件不允许上传，系统已拒绝处理。原因：{reasons}。建议：{guidance}"
        if reasons:
            return f"该文件不允许上传，系统已拒绝处理。原因：{reasons}"
        if guidance:
            return f"该文件不允许上传，系统已拒绝处理。建议：{guidance}"
        return f"该文件不允许上传，系统已拒绝处理。"

    def _format_reason_list(self, report: UploadSecurityReport) -> str:
        reasons: list[str] = []
        for finding in report.findings:
            reason = self._translate_finding_message(finding.message, report.extension)
            if reason and reason not in reasons:
                reasons.append(reason)
            if len(reasons) >= 3:
                break
        return "；".join(reasons)

    def _translate_finding_message(self, message: str, extension: str) -> str:
        normalized = message.lower()
        if "private key" in normalized:
            return "检测到私钥内容"
        if "openai-style api key" in normalized:
            return "检测到疑似 API Key"
        if "aws access key" in normalized or "google api key" in normalized or "jwt token" in normalized:
            return "检测到敏感凭证信息"
        if "credential-like assignment" in normalized:
            return "检测到敏感凭证配置"
        if "script tag" in normalized:
            return "检测到脚本标签内容"
        if "eval()" in normalized or "exec()" in normalized or "system()" in normalized:
            return "检测到高风险脚本调用"
        if "command shell reference" in normalized or "shell execution reference" in normalized:
            return "检测到命令执行相关内容"
        if "macro-enabled office extension" in normalized:
            return f"不支持上传宏文档 {extension or ''}".strip()
        if "executable or script extension" in normalized:
            return f"不支持上传可执行或脚本文件 {extension or ''}".strip()
        if "not allowed for profile" in normalized:
            return f"当前入口不支持该文件类型 {extension or ''}".strip()
        if "blocked content type" in normalized:
            return "检测到可执行文件特征"
        if "pdf contains javascript action" in normalized or "pdf contains openaction trigger" in normalized or "pdf contains /js action" in normalized:
            return "PDF 包含脚本或自动动作"
        if "path traversal" in normalized:
            return "压缩包包含异常路径"
        if "zip archive contains blocked entry" in normalized:
            return "压缩包内包含受限文件"
        if "zip archive contains macro payload" in normalized:
            return "压缩包内包含宏脚本"
        if "zip archive could not be parsed safely" in normalized:
            return "压缩包结构异常"
        if "file exceeds the upload limit" in normalized:
            return "文件大小超过限制"
        return ""

    def _build_user_guidance(self, action: str, report: UploadSecurityReport) -> str:
        reasons = [item.message.lower() for item in report.findings]
        extension = report.extension or "该文件"

        if any("private key" in reason for reason in reasons):
            return "请移除私钥、密钥或其他敏感凭证后重新上传"
        if any(
            token in reason
            for reason in reasons
            for token in (
                "openai-style api key",
                "aws access key",
                "google api key",
                "jwt token",
                "credential-like assignment",
            )
        ):
            return "请先删除或脱敏敏感凭证信息，再重新上传"
        if any("macro-enabled office extension" in reason for reason in reasons):
            return f"请改为上传不含宏的 Office 文档，例如 .docx 或 .xlsx，而不是 {extension}"
        if any(
            token in reason
            for reason in reasons
            for token in (
                "executable or script extension",
                "blocked content type",
            )
        ):
            return f"请改为上传文档、图片、文本或接口文件，不要上传 {extension} 这类可执行文件"
        if any("not allowed for profile" in reason for reason in reasons):
            return "请确认当前上传入口支持该文件类型，或更换为受支持的文件格式"
        if any("file exceeds the upload limit" in reason for reason in reasons):
            max_size_mb = max(1, round(self._settings.security.upload_scan_max_bytes / (1024 * 1024)))
            return f"请压缩文件或拆分后重试，当前限制约为 {max_size_mb} MB"
        if any("zip archive" in reason for reason in reasons):
            return "请检查压缩包内容，移除异常路径、宏脚本或受限文件后重新上传"
        if any("pdf contains" in reason for reason in reasons):
            return "请移除 PDF 中的脚本或自动动作后重新上传"
        if any(
            token in reason
            for reason in reasons
            for token in (
                "script tag",
                "eval()",
                "exec()",
                "system()",
                "command shell reference",
                "shell execution reference",
            )
        ):
            if action == "quarantined":
                return "请删除高风险脚本或命令执行内容后重新上传"
            return "请不要上传包含脚本执行或命令执行内容的文件"
        return ""
