"""Tencent Agent Mail provider adapter (CLI + OAuth).

Wraps agently-cli as the transport. All commands output JSON to stdout.
Send/reply/forward use the CLI built-in two-phase confirmation
(first call returns a confirmation_token; second call with that token commits).
This maps directly to the tool runtime waiting_approval mechanism.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.application.mail.contracts import (
    MailCapability,
    MailMessage,
    MailProviderAdapter,
    MailSendRequest,
    MailSendResult,
)
from src.core.config import get_settings
from src.infrastructure.redis_lock import RedisLockManager

if TYPE_CHECKING:
    from src.schemas.email_config import EmailConfigRecord


_AUTH_LOGIN_SESSIONS: dict[str, dict[str, Any]] = {}
_AUTH_LOGIN_LOCK = threading.Lock()
_PENDING_CONFIRMATIONS: dict[str, dict[str, Any]] = {}
_PENDING_CONFIRMATIONS_LOCK = threading.Lock()
_AUTH_URL_PATTERN = re.compile(r"https?://\S+")
_CONFIRMATION_TOKEN_PATTERN = re.compile(r"\bctk_[A-Za-z0-9_-]+\b")


class TencentAgentlyMailAdapter(MailProviderAdapter):
    """Full Agent Mailbox provider backed by agently-cli."""

    provider_key = "tencent_agently"
    display_name = "腾讯 Agent Mail"
    auth_type = "oauth_cli"

    def __init__(self, *, settings=None, auth_lock_manager=None) -> None:
        self._settings = settings or get_settings()
        self._auth_lock_manager = auth_lock_manager or RedisLockManager(
            self._settings.database.redis_url,
            ttl_seconds=self._settings.mail.agently_auth_lock_ttl_seconds,
            wait_seconds=self._settings.mail.agently_auth_lock_wait_seconds,
        )

    def capabilities(self) -> set[MailCapability]:
        return {
            MailCapability.SEND,
            MailCapability.LIST,
            MailCapability.READ,
            MailCapability.SEARCH,
            MailCapability.REPLY,
            MailCapability.FORWARD,
            MailCapability.TRASH,
            MailCapability.ATTACHMENTS,
        }

    # --- CLI helpers --------------------------------------------------------

    def _cli_path(self, record: "EmailConfigRecord") -> str:
        # The CLI is a backend deployment dependency. Never persist or trust a
        # workstation-specific executable path from mailbox configuration.
        resolved = self._resolve_cli_from_path("agently-cli")
        return resolved or "agently-cli"

    @staticmethod
    def _resolve_cli_from_path(cli_name: str) -> str | None:
        candidates = [cli_name]
        if os.name == "nt":
            candidates = [f"{cli_name}.cmd", f"{cli_name}.exe", f"{cli_name}.bat", cli_name]

        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None

    def _run_cli(
        self, record: "EmailConfigRecord", args: list[str], *, timeout: int = 30
    ) -> dict[str, Any]:
        if self._requires_auth_lock(record, args):
            with self._auth_lock_manager.acquire(self._auth_lock_key(record)):
                return self._run_cli_unlocked(record, args, timeout=timeout)
        return self._run_cli_unlocked(record, args, timeout=timeout)

    def _auth_lock_key(self, record: "EmailConfigRecord") -> str:
        return f"agent-mail:auth:{self.provider_key}:{record.id}"

    @staticmethod
    def _requires_auth_lock(record: "EmailConfigRecord", args: list[str]) -> bool:
        # auth status is a local metadata read and cannot rotate credentials.
        # Unsaved records have no stable distributed-lock identity.
        return record.id is not None and args[:2] != ["auth", "status"]

    def _run_cli_unlocked(
        self, record: "EmailConfigRecord", args: list[str], *, timeout: int = 30
    ) -> dict[str, Any]:
        cli = self._cli_path(record)
        cmd = [cli] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=self._cli_env(record),
            )
        except FileNotFoundError:
            raise RuntimeError(self._cli_not_found_message(cli))
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"agently-cli timed out after {timeout}s.")

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        payload = self._parse_cli_json(stdout) or self._parse_cli_json(stderr)

        if proc.returncode != 0:
            message = self._extract_cli_error_message(payload or {})
            detail = message or stderr.strip() or stdout.strip() or "No error output."
            raise RuntimeError(f"agently-cli error (exit {proc.returncode}): {detail}")

        if payload is None:
            raw_output = "\n".join(part.strip() for part in (stdout, stderr) if part and part.strip())
            token_match = _CONFIRMATION_TOKEN_PATTERN.search(raw_output)
            if token_match:
                return {
                    "ok": True,
                    "confirmation_required": True,
                    "confirmation_token": token_match.group(0),
                    "summary": raw_output,
                }
            if raw_output:
                return {"ok": True, "summary": raw_output}
            preview = raw_output[:500]
            raise RuntimeError(
                "agently-cli returned empty or non-JSON output"
                + (f": {preview}" if preview else ".")
            )
        return payload

    @staticmethod
    def _cli_env(record: "EmailConfigRecord") -> dict[str, str]:
        env = os.environ.copy()
        extra = record.extra_config or {}
        configured = str(extra.get("config_dir") or "").strip()
        if configured:
            config_dir = Path(configured).expanduser().resolve()
            config_dir.mkdir(parents=True, exist_ok=True)
            env["AGENTLY_CLI_CONFIG_DIR"] = str(config_dir)
        env.setdefault("AGENTLY_CLI_NO_UPDATE_NOTIFIER", "1")
        return env

    @staticmethod
    def _parse_cli_json(text: str | None) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        start = raw.find("{")
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(raw[start:])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _cli_not_found_message(cli: str) -> str:
        return (
            f"agently-cli not found at '{cli}'. "
            "Install @tencent-qqmail/agently-cli on the Agent Server and add it to PATH."
        )

    @staticmethod
    def _normalize_cli_payload(data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("data")
        if isinstance(payload, dict):
            return payload
        return data

    @staticmethod
    def _extract_cli_error_message(data: dict[str, Any]) -> str:
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
        return ""

    def _capability_values(self) -> list[str]:
        return sorted(cap.value for cap in self.capabilities())

    @staticmethod
    def _credential_ref(record: "EmailConfigRecord") -> str:
        return f"tencent_agently/config-{record.id}" if record.id is not None else ""

    @staticmethod
    def _is_reauth_error(error: object) -> bool:
        text = str(error or "").casefold()
        return any(
            marker in text
            for marker in (
                "exit 3",
                "authorization required",
                "refresh token is invalid",
                "refresh token has expired",
                "not_logged_in",
                "not logged in",
            )
        )

    def _auth_error_result(
        self, record: "EmailConfigRecord", error: object, *, action: str
    ) -> dict[str, Any]:
        reauth_required = self._is_reauth_error(error)
        return {
            "ok": False,
            "provider": self.provider_key,
            "capabilities": self._capability_values(),
            "action": action,
            "auth_state": "reauth_required" if reauth_required else "failed",
            "reauth_required": reauth_required,
            "credential_ref": self._credential_ref(record),
            "error": str(error),
        }

    @staticmethod
    def _extract_auth_url(text: str) -> str | None:
        match = _AUTH_URL_PATTERN.search(text)
        return match.group(0) if match else None

    def _start_auth_login_process(
        self, record: "EmailConfigRecord"
    ) -> dict[str, Any]:
        self._prune_auth_login_sessions()
        cli = self._cli_path(record)
        cmd = [cli, "auth", "login"]
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        auth_lock = None
        if record.id is not None:
            auth_lock = self._auth_lock_manager.acquire(self._auth_lock_key(record))
            auth_lock.__enter__()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                env=self._cli_env(record),
            )
        except FileNotFoundError:
            if auth_lock is not None:
                auth_lock.__exit__(None, None, None)
            raise RuntimeError(self._cli_not_found_message(cli))
        except Exception:
            if auth_lock is not None:
                auth_lock.__exit__(None, None, None)
            raise

        session_id = uuid.uuid4().hex
        session = {
            "ok": True,
            "provider": self.provider_key,
            "action": "auth_login",
            "session_id": session_id,
            "record_id": record.id,
            "cli_path": cli,
            "status": "starting",
            "auth_state": "authorizing",
            "authorization_url": None,
            "prompt": "请点击或复制以下链接在浏览器中完成授权：",
            "output_lines": [],
            "error": "",
            "exit_code": None,
            "started_at": time.time(),
            "completed_at": None,
            "_process": proc,
        }
        with _AUTH_LOGIN_LOCK:
            _AUTH_LOGIN_SESSIONS[session_id] = session

        def consume_output() -> None:
            lock_released = False

            def release_auth_lock() -> None:
                nonlocal lock_released
                if auth_lock is not None and not lock_released:
                    lock_released = True
                    auth_lock.__exit__(None, None, None)

            try:
                if proc.stdout is not None:
                    for raw_line in proc.stdout:
                        line = raw_line.rstrip("\r\n")
                        if not line:
                            continue
                        with _AUTH_LOGIN_LOCK:
                            current = _AUTH_LOGIN_SESSIONS.get(session_id)
                            if current is None:
                                continue
                            current["output_lines"].append(line)
                            url = self._extract_auth_url(line)
                            if url and not current.get("authorization_url"):
                                current["authorization_url"] = url
                                current["status"] = "authorization_url_ready"
                            elif (
                                current.get("status") == "starting"
                                and "请点击" in line
                            ):
                                current["status"] = "waiting_browser_login"

                            if "认证成功" in line or "Authorization successful" in line:
                                current["status"] = "credentials_saved"

                proc.wait()
                # Release the long-running OAuth lock before +me. whoami() then
                # acquires the same mailbox lock for the strong verification.
                release_auth_lock()
                verified_identity: dict[str, Any] | None = None
                verify_error = ""
                if proc.returncode == 0:
                    verified_identity = self.whoami(record)
                    if not verified_identity.get("ok"):
                        verify_error = str(
                            verified_identity.get("error") or "OAuth identity verification failed."
                        )
                with _AUTH_LOGIN_LOCK:
                    current = _AUTH_LOGIN_SESSIONS.get(session_id)
                    if current is None:
                        return
                    current["exit_code"] = proc.returncode
                    current["completed_at"] = time.time()
                    if proc.returncode == 0:
                        if verify_error:
                            current["ok"] = False
                            current["status"] = "failed"
                            current["error"] = verify_error
                            current["auth_state"] = (
                                "reauth_required"
                                if self._is_reauth_error(verify_error)
                                else "failed"
                            )
                        else:
                            current["status"] = "authorized"
                            current["auth_state"] = "authorized"
                            current["email"] = str(
                                (verified_identity or {}).get("email") or ""
                            )
                    else:
                        current["ok"] = False
                        current["status"] = "failed"
                        current["error"] = (
                            "\n".join(current.get("output_lines", [])[-5:])
                            or f"agently-cli error (exit {proc.returncode})"
                        )
                        current["auth_state"] = (
                            "reauth_required"
                            if proc.returncode == 3
                            else "failed"
                        )
            except Exception as exc:  # pragma: no cover - defensive guard
                with _AUTH_LOGIN_LOCK:
                    current = _AUTH_LOGIN_SESSIONS.get(session_id)
                    if current is None:
                        return
                    current["ok"] = False
                    current["status"] = "failed"
                    current["completed_at"] = time.time()
                    current["error"] = str(exc)
                    current["auth_state"] = "failed"
            finally:
                release_auth_lock()

        threading.Thread(target=consume_output, daemon=True).start()

        deadline = time.time() + 8
        while time.time() < deadline:
            snapshot = self.auth_login_session_status(record, session_id)
            if snapshot.get("authorization_url") or snapshot.get("status") in {
                "authorized",
                "completed",
                "failed",
            }:
                return snapshot
            time.sleep(0.1)
        return self.auth_login_session_status(record, session_id)

    @staticmethod
    def _prune_auth_login_sessions(max_age_seconds: int = 1800) -> None:
        cutoff = time.time() - max_age_seconds
        with _AUTH_LOGIN_LOCK:
            expired = [
                session_id
                for session_id, session in _AUTH_LOGIN_SESSIONS.items()
                if float(session.get("started_at") or 0) < cutoff
            ]
            for session_id in expired:
                _AUTH_LOGIN_SESSIONS.pop(session_id, None)

    def auth_login_session_status(
        self, record: "EmailConfigRecord", session_id: str
    ) -> dict[str, Any]:
        with _AUTH_LOGIN_LOCK:
            session = _AUTH_LOGIN_SESSIONS.get(session_id)
            if session is None:
                return {
                    "ok": False,
                    "provider": self.provider_key,
                    "action": "auth_login",
                    "session_id": session_id,
                    "error": "auth_login_session_not_found",
                }
            if session.get("record_id") != record.id:
                return {
                    "ok": False,
                    "provider": self.provider_key,
                    "action": "auth_login",
                    "session_id": session_id,
                    "error": "auth_login_session_mailbox_mismatch",
                }

            output_lines = list(session.get("output_lines") or [])
            return {
                "ok": bool(session.get("ok", True)),
                "provider": self.provider_key,
                "action": "auth_login",
                "session_id": session_id,
                "status": str(session.get("status") or "unknown"),
                "auth_state": str(session.get("auth_state") or "authorizing"),
                "reauth_required": session.get("auth_state") == "reauth_required",
                "email": str(session.get("email") or ""),
                "authorization_url": session.get("authorization_url"),
                "prompt": session.get("prompt"),
                "exit_code": session.get("exit_code"),
                "error": str(session.get("error") or ""),
                "output_lines": output_lines,
                "started_at": session.get("started_at"),
                "completed_at": session.get("completed_at"),
            }

    # --- send (two-phase) ---------------------------------------------------

    def send(
        self, record: "EmailConfigRecord", request: MailSendRequest
    ) -> MailSendResult:
        args = self._send_args(request)
        try:
            data = self._run_cli(record, args)
        except Exception:
            self._cleanup_confirmation_files({"args": args})
            raise
        result = self._build_send_result(record, data)
        if result.confirmation_required:
            result.recipient_count = result.recipient_count or len(request.recipients)
            result.confirmation_summary = self._send_confirmation_summary(request)
            self._remember_confirmation(result.confirmation_token, "send", args, record)
        else:
            self._cleanup_confirmation_files({"args": args})
        return result

    @staticmethod
    def _send_args(request: MailSendRequest) -> list[str]:
        args = ["message", "+send"]
        for r in request.recipients:
            args += ["--to", r]
        args += ["--subject", request.subject]
        body = request.content_html or request.content
        args += [
            "--body-file",
            TencentAgentlyMailAdapter._write_body_file(
                body,
                is_html=bool(request.content_html),
            ),
        ]
        if request.cc:
            for cc in request.cc:
                args += ["--cc", cc]
        if request.bcc:
            for bcc in request.bcc:
                args += ["--bcc", bcc]
        return args

    @staticmethod
    def _write_body_file(body: str, *, is_html: bool = False) -> str:
        root = Path("src/data/agently_mail_confirmations")
        root.mkdir(parents=True, exist_ok=True)
        suffix = ".html" if is_html else ".txt"
        path = root / f"body_{uuid.uuid4().hex}{suffix}"
        path.write_text(body, encoding="utf-8")
        return path.as_posix()

    def send_confirm(
        self,
        record: "EmailConfigRecord",
        confirmation_token: str,
    ) -> MailSendResult:
        """Phase 2: commit a prepared send with the confirmation token."""
        args = self._confirmation_args(confirmation_token, "send", record)
        data = self._run_cli(record, args)
        result = self._build_send_result(record, data)
        return self._finish_confirmation(confirmation_token, result)

    def _build_send_result(
        self, record: "EmailConfigRecord", data: dict
    ) -> MailSendResult:
        payload = self._normalize_cli_payload(data)
        confirmation_required = bool(payload.get("confirmation_required"))
        return MailSendResult(
            sent=not confirmation_required,
            provider=self.provider_key,
            from_email=str(payload.get("from") or record.sender_email or ""),
            recipient_count=int(payload.get("recipient_count") or 0),
            message_id=payload.get("message_id"),
            confirmation_required=confirmation_required,
            confirmation_token=str(payload.get("confirmation_token") or "") or None,
            confirmation_summary=str(payload.get("summary") or payload.get("confirmation_summary") or "") or None,
        )

    @staticmethod
    def _send_confirmation_summary(request: MailSendRequest) -> str:
        body = request.content_html or request.content
        body_preview = body if len(body) <= 240 else body[:240] + "..."
        return (
            f"Recipients: {', '.join(request.recipients)}\n"
            f"Subject: {request.subject}\n"
            f"Body: {body_preview}"
        )

    @staticmethod
    def _remember_confirmation(
        token: str | None,
        operation: str,
        args: list[str],
        record: "EmailConfigRecord",
    ) -> None:
        if not token:
            return
        cutoff = time.time() - 300
        with _PENDING_CONFIRMATIONS_LOCK:
            expired = [
                key
                for key, item in _PENDING_CONFIRMATIONS.items()
                if float(item.get("created_at") or 0) < cutoff
            ]
            for key in expired:
                item = _PENDING_CONFIRMATIONS.pop(key, None)
                TencentAgentlyMailAdapter._cleanup_confirmation_files(item)
            _PENDING_CONFIRMATIONS[token] = {
                "operation": operation,
                "args": list(args),
                "record_id": record.id,
                "created_at": time.time(),
            }

    @staticmethod
    def _confirmation_args(
        token: str,
        operation: str,
        record: "EmailConfigRecord",
    ) -> list[str]:
        with _PENDING_CONFIRMATIONS_LOCK:
            pending = _PENDING_CONFIRMATIONS.get(token)
        if not pending or time.time() - float(pending.get("created_at") or 0) > 300:
            raise RuntimeError(
                "Mail confirmation context is missing or expired. Prepare the message again."
            )
        if pending.get("operation") != operation:
            raise RuntimeError("Mail confirmation operation does not match the prepared request.")
        if pending.get("record_id") != record.id:
            raise RuntimeError("Mail confirmation does not belong to this mailbox configuration.")
        return [*list(pending.get("args") or []), "--confirmation-token", token]

    @staticmethod
    def _finish_confirmation(token: str, result: MailSendResult) -> MailSendResult:
        if result.confirmation_required or not result.sent:
            raise RuntimeError(
                "Agent Mail did not commit the confirmed operation; prepare it again before retrying."
            )
        with _PENDING_CONFIRMATIONS_LOCK:
            pending = _PENDING_CONFIRMATIONS.pop(token, None)
        TencentAgentlyMailAdapter._cleanup_confirmation_files(pending)
        return result

    @staticmethod
    def _cleanup_confirmation_files(pending: dict[str, Any] | None) -> None:
        if not pending:
            return
        args = list(pending.get("args") or [])
        for index, value in enumerate(args[:-1]):
            if value != "--body-file":
                continue
            path = Path(args[index + 1])
            try:
                if path.is_file() and "agently_mail_confirmations" in path.parts:
                    path.unlink()
            except OSError:
                pass

    # --- list ---------------------------------------------------------------

    def list_messages(
        self, record: "EmailConfigRecord", options: dict[str, Any] | None = None
    ) -> list[MailMessage]:
        opts = options or {}
        args = ["message", "+list"]
        if "limit" in opts:
            args += ["--limit", str(opts["limit"])]
        if "cursor" in opts:
            args += ["--cursor", opts["cursor"]]
        if "dir" in opts:
            args += ["--dir", opts["dir"]]
        if opts.get("is_unread"):
            args.append("--is-unread")
        if opts.get("has_attachments"):
            args.append("--has-attachments")
        if "after" in opts:
            args += ["--after", opts["after"]]
        if "before" in opts:
            args += ["--before", opts["before"]]

        data = self._run_cli(record, args)
        messages_raw = data.get("messages") or data.get("data") or []
        parsed_messages = []
        for m in messages_raw:
            if isinstance(m, str):
                try:
                    m = json.loads(m)
                except json.JSONDecodeError:
                    continue
            if isinstance(m, dict):
                parsed_messages.append(m)
        return [self._to_mail_message(m) for m in parsed_messages]

    # --- read ---------------------------------------------------------------

    def read_message(
        self, record: "EmailConfigRecord", message_id: str
    ) -> MailMessage:
        args = ["message", "+read", "--id", message_id]
        data = self._run_cli(record, args)
        msg_data = data.get("message") or data
        return self._to_mail_message(msg_data)

    # --- search -------------------------------------------------------------

    def search_messages(
        self,
        record: "EmailConfigRecord",
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[MailMessage]:
        opts = options or {}
        args = ["message", "+search", "--q", query]
        if "limit" in opts:
            args += ["--limit", str(opts["limit"])]
        if "cursor" in opts:
            args += ["--cursor", opts["cursor"]]
        if "from" in opts:
            args += ["--from", opts["from"]]
        if "to" in opts:
            args += ["--to", opts["to"]]
        if "dir" in opts:
            args += ["--dir", opts["dir"]]
        if "search_in" in opts:
            args += ["--search-in", opts["search_in"]]

        data = self._run_cli(record, args)
        messages_raw = data.get("messages") or data.get("data") or []
        parsed_messages = []
        for m in messages_raw:
            if isinstance(m, str):
                try:
                    m = json.loads(m)
                except json.JSONDecodeError:
                    continue
            if isinstance(m, dict):
                parsed_messages.append(m)
        return [self._to_mail_message(m) for m in parsed_messages]

    # --- reply (two-phase) --------------------------------------------------

    def reply(
        self,
        record: "EmailConfigRecord",
        message_id: str,
        request: MailSendRequest,
    ) -> MailSendResult:
        args = self._reply_args(message_id, request)
        try:
            data = self._run_cli(record, args)
        except Exception:
            self._cleanup_confirmation_files({"args": args})
            raise
        result = self._build_send_result(record, data)
        self._remember_confirmation(result.confirmation_token, "reply", args, record)
        if not result.confirmation_required:
            self._cleanup_confirmation_files({"args": args})
        return result

    @staticmethod
    def _reply_args(message_id: str, request: MailSendRequest) -> list[str]:
        args = ["message", "+reply", "--id", message_id]
        body = request.content_html or request.content
        args += [
            "--body-file",
            TencentAgentlyMailAdapter._write_body_file(
                body,
                is_html=bool(request.content_html),
            ),
        ]
        for cc in request.cc:
            args += ["--cc", cc]
        return args

    def reply_confirm(
        self,
        record: "EmailConfigRecord",
        confirmation_token: str,
    ) -> MailSendResult:
        """Phase 2 for reply."""
        args = self._confirmation_args(confirmation_token, "reply", record)
        data = self._run_cli(record, args)
        return self._finish_confirmation(confirmation_token, self._build_send_result(record, data))

    # --- forward (two-phase) ------------------------------------------------

    def forward(
        self,
        record: "EmailConfigRecord",
        message_id: str,
        request: MailSendRequest,
    ) -> MailSendResult:
        args = self._forward_args(message_id, request)
        try:
            data = self._run_cli(record, args)
        except Exception:
            self._cleanup_confirmation_files({"args": args})
            raise
        result = self._build_send_result(record, data)
        self._remember_confirmation(result.confirmation_token, "forward", args, record)
        if not result.confirmation_required:
            self._cleanup_confirmation_files({"args": args})
        return result

    @staticmethod
    def _forward_args(message_id: str, request: MailSendRequest) -> list[str]:
        args = ["message", "+forward", "--id", message_id]
        for recipient in request.recipients:
            args += ["--to", recipient]
        body = request.content_html or request.content
        if body:
            args += [
                "--body-file",
                TencentAgentlyMailAdapter._write_body_file(
                    body,
                    is_html=bool(request.content_html),
                ),
            ]
        args.append("--include-attachments")
        return args

    def forward_confirm(
        self,
        record: "EmailConfigRecord",
        confirmation_token: str,
    ) -> MailSendResult:
        """Phase 2 for forward."""
        args = self._confirmation_args(confirmation_token, "forward", record)
        data = self._run_cli(record, args)
        return self._finish_confirmation(confirmation_token, self._build_send_result(record, data))

    # --- trash (two-phase) --------------------------------------------------

    def trash(self, record: "EmailConfigRecord", message_id: str) -> MailSendResult:
        args = ["message", "+trash", "--id", message_id]
        data = self._run_cli(record, args)
        result = self._build_send_result(record, data)
        if result.confirmation_required:
            result.confirmation_summary = f"Move message {message_id} to trash."
            self._remember_confirmation(result.confirmation_token, "trash", args, record)
        return result

    def trash_confirm(
        self,
        record: "EmailConfigRecord",
        confirmation_token: str,
    ) -> MailSendResult:
        args = self._confirmation_args(confirmation_token, "trash", record)
        data = self._run_cli(record, args)
        return self._finish_confirmation(confirmation_token, self._build_send_result(record, data))

    # --- attachments --------------------------------------------------------

    def download_attachment(
        self, record: "EmailConfigRecord", message_id: str, attachment_id: str
    ) -> dict[str, Any]:
        args = [
            "attachment", "+download",
            "--msg", message_id,
            "--att", attachment_id,
        ]
        data = self._run_cli(record, args, timeout=60)
        return {
            "ok": True,
            "file_path": data.get("file_path", ""),
            "filename": data.get("filename", ""),
            "size": data.get("size", 0),
        }

    # --- status / auth ------------------------------------------------------

    def auth_status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            data = self._run_cli(record, ["auth", "status"])
        except RuntimeError as exc:
            return self._auth_error_result(record, exc, action="auth_status")

        if data.get("ok") is False:
            error = self._extract_cli_error_message(data) or "agently-cli auth status failed."
            return self._auth_error_result(record, error, action="auth_status")

        payload = self._normalize_cli_payload(data)
        status = str(payload.get("status") or "unknown")
        logged_in = bool(payload.get("logged_in"))
        token_status = str(payload.get("token_status") or "").strip() or None

        return {
            "ok": True,
            "provider": self.provider_key,
            "capabilities": self._capability_values(),
            "logged_in": logged_in,
            "auth_state": "checking" if logged_in else "reauth_required",
            "reauth_required": not logged_in,
            "auth_status": status,
            "token_status": token_status,
            "granted_at": payload.get("granted_at"),
            "expires_at": payload.get("expires_at"),
            "storage": payload.get("storage"),
            "app_id": payload.get("app_id"),
            "credential_ref": self._credential_ref(record),
            "message": self._extract_cli_error_message(data)
            or str(payload.get("message") or "").strip(),
        }

    def whoami(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            data = self._run_cli(record, ["+me"])
        except RuntimeError as exc:
            return self._auth_error_result(record, exc, action="whoami")

        if data.get("ok") is False:
            error = self._extract_cli_error_message(data) or "agently-cli identity check failed."
            return self._auth_error_result(record, error, action="whoami")

        payload = self._normalize_cli_payload(data)
        aliases = payload.get("aliases")
        alias_list = aliases if isinstance(aliases, list) else []
        primary_alias = next(
            (
                alias
                for alias in alias_list
                if isinstance(alias, dict) and alias.get("is_primary")
            ),
            alias_list[0] if alias_list else None,
        )
        primary_email = ""
        if isinstance(primary_alias, dict):
            primary_email = str(primary_alias.get("email") or "").strip()
        primary_email = primary_email or str(payload.get("email") or "").strip()
        if not primary_email:
            return self._auth_error_result(
                record,
                "agently-cli identity check returned no mailbox address.",
                action="whoami",
            )

        return {
            "ok": True,
            "provider": self.provider_key,
            "capabilities": self._capability_values(),
            "email": primary_email,
            "auth_state": "authorized",
            "reauth_required": False,
            "credential_ref": self._credential_ref(record),
            "primary_alias": primary_alias,
            "aliases": alias_list,
            "constraints": payload.get("constraints") or {},
            "rate_limits": payload.get("rate_limits") or {},
            "scopes": payload.get("scopes") or [],
        }

    def auth_login(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            return self._start_auth_login_process(record)
        except RuntimeError as exc:
            return self._auth_error_result(record, exc, action="auth_login")

    def status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        auth = self.auth_status(record)
        if not auth.get("ok"):
            return auth

        if not auth.get("logged_in"):
            return {
                "ok": False,
                "provider": self.provider_key,
                "capabilities": self._capability_values(),
                "auth_status": auth.get("auth_status", "unknown"),
                "logged_in": False,
                "auth_state": "reauth_required",
                "reauth_required": True,
                "credential_ref": self._credential_ref(record),
                "error": str(auth.get("auth_status") or "not_logged_in"),
            }

        me = self.whoami(record)
        if not me.get("ok"):
            return me

        return {
            "ok": True,
            "provider": self.provider_key,
            "capabilities": self._capability_values(),
            "auth_status": auth.get("auth_status", "unknown"),
            "logged_in": True,
            "auth_state": "authorized",
            "reauth_required": False,
            "credential_ref": self._credential_ref(record),
            "email": me.get("email", ""),
            "aliases": me.get("aliases", []),
            "constraints": me.get("constraints", {}),
            "rate_limits": me.get("rate_limits", {}),
            "scopes": me.get("scopes", []),
        }

    # --- normalization helpers ----------------------------------------------

    @staticmethod
    def _to_mail_message(raw: dict[str, Any]) -> MailMessage:
        return MailMessage(
            message_id=raw.get("message_id") or raw.get("id") or "",
            thread_id=raw.get("thread_id"),
            subject=raw.get("subject") or "",
            from_email=raw.get("from") or raw.get("from_email") or "",
            to=raw.get("to") or [],
            snippet=raw.get("snippet") or raw.get("preview") or "",
            body_text=raw.get("body_text") or raw.get("body") or "",
            body_html=raw.get("body_html") or "",
            received_at=raw.get("received_at") or raw.get("date") or None,
            attachments=raw.get("attachments") or [],
            raw=raw,
        )
