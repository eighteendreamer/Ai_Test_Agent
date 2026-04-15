"""
邮件发送统一调度器 - 工厂模式

新增服务商只需在此文件中：
  1. 实现一个 _send_via_xxx(config, to_email, subject, html_body) 函数
  2. 在 _PROVIDER_MAP 中注册

调用方统一使用：
    from Email_manage.sender import dispatch_send
    dispatch_send(email_config, to_email, subject, html_body)

作者: 程序员Eighteen
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.connection import EmailConfig


# ============================================================
# 各服务商发送实现
# ============================================================

def _send_via_smtp(config: "EmailConfig", to_email: str, subject: str, html_body: str):
    """SMTP 自定义服务器（STARTTLS）"""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_host = config.smtp_host
    smtp_port = config.smtp_port or 587
    smtp_username = config.smtp_username or config.sender_email
    smtp_password = config.api_key

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.sender_email
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_username, smtp_password)
        server.sendmail(config.sender_email, [to_email], msg.as_string())


def _send_via_resend(config: "EmailConfig", to_email: str, subject: str, html_body: str):
    """Resend 平台"""
    import resend

    resend.api_key = config.api_key
    resend.Emails.send({
        "from": config.sender_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    })


def _send_via_aliyun(config: "EmailConfig", to_email: str, subject: str, html_body: str):
    """阿里云 DirectMail HTTP API"""
    import hashlib
    import hmac
    import base64
    import urllib.parse
    import uuid
    import requests
    from datetime import datetime, timezone

    params = {
        "Action": "SingleSendMail",
        "AccountName": config.sender_email,
        "ReplyToAddress": "false",
        "AddressType": "1",
        "ToAddress": to_email,
        "Subject": subject,
        "HtmlBody": html_body,
        "Format": "JSON",
        "Version": "2015-11-23",
        "AccessKeyId": config.api_key,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    sorted_params = sorted(params.items())
    query_string = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)
    string_to_sign = "POST&%2F&" + urllib.parse.quote(query_string, safe="")

    sign_key = (config.secret_key + "&").encode("utf-8")
    signature = base64.b64encode(
        hmac.new(sign_key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")
    params["Signature"] = signature

    resp = requests.post("https://dm.aliyuncs.com/", data=params, timeout=10)
    result = resp.json()
    if resp.status_code != 200 or "Code" in result:
        error_msg = result.get("Message", result.get("Code", "未知错误"))
        raise Exception(f"阿里云邮件发送失败: {error_msg}")


def _send_via_cybermail(config: "EmailConfig", to_email: str, subject: str, html_body: str):
    """CyberMail (mail.cyberpersons.com) — 主机端口固定，只需用户名和密码"""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    CYBERMAIL_HOST = "mail.cyberpersons.com"
    CYBERMAIL_PORT = 587

    smtp_username = config.smtp_username or config.sender_email
    smtp_password = config.api_key

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.sender_email
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(CYBERMAIL_HOST, CYBERMAIL_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_username, smtp_password)
        server.sendmail(config.sender_email, [to_email], msg.as_string())


# ============================================================
# 服务商注册表
# 新增服务商：只需在此处加一行，无需修改任何调用方
# ============================================================
_PROVIDER_MAP = {
    "resend":     _send_via_resend,
    "aliyun":     _send_via_aliyun,
    "smtp":       _send_via_smtp,
    "cybermail":  _send_via_cybermail,
    # 示例：未来接入 SendGrid
    # "sendgrid": _send_via_sendgrid,
    # 示例：未来接入腾讯云 SES
    # "tencent": _send_via_tencent,
}


# ============================================================
# 统一调度入口（调用方只需使用此函数）
# ============================================================

def dispatch_send(
    config: "EmailConfig",
    to_email: str,
    subject: str,
    html_body: str,
) -> None:
    """
    根据 email_config.provider 自动选择发送实现。

    Args:
        config:   数据库中的 EmailConfig ORM 对象
        to_email: 实际收件人地址（已处理测试模式替换）
        subject:  邮件主题
        html_body: HTML 格式正文

    Raises:
        ValueError: provider 未注册
        Exception:  发送过程中的网络/认证等错误
    """
    provider = (config.provider or "resend").lower()
    sender_fn = _PROVIDER_MAP.get(provider)
    if sender_fn is None:
        supported = ", ".join(_PROVIDER_MAP.keys())
        raise ValueError(
            f"不支持的邮件服务商: '{provider}'。"
            f"已支持: {supported}。"
            f"请在 Email_manage/sender.py 的 _PROVIDER_MAP 中注册新服务商。"
        )
    sender_fn(config, to_email, subject, html_body)
