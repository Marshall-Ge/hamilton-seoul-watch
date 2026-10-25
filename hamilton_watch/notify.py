"""邮件发送：支持 163 SMTP / Resend / FormSubmit / 仅日志。"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

import requests

from .config import NotifyConfig

log = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    pass


def send_email(cfg: NotifyConfig, subject: str, text: str, html: str) -> str:
    if not cfg.to:
        raise NotifyError(
            "未配置收件人：请在 GitHub Secrets 里添加 MAIL_TO（例如 marshall_gefxiang@163.com），"
            "或在 config.toml 的 [notify] 里填写 to。"
        )
    provider = (cfg.provider or "smtp").lower()
    if provider == "none":
        log.warning("MAIL_PROVIDER=none，仅记录邮件内容：\n%s\n%s", subject, text)
        return "none"
    if provider == "smtp":
        return _send_smtp(cfg, subject, text, html)
    if provider == "resend":
        return _send_resend(cfg, subject, text, html)
    if provider == "formsubmit":
        return _send_formsubmit(cfg, subject, text, html)
    raise NotifyError(f"未知的邮件通道: {provider}")


def _build_message(cfg: NotifyConfig, subject: str, text: str, html: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((cfg.from_name, cfg.from_address))
    message["To"] = cfg.to
    message["Date"] = formatdate(localtime=True)
    domain = cfg.from_address.split("@")[-1] if "@" in cfg.from_address else "localhost"
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def _send_smtp(cfg: NotifyConfig, subject: str, text: str, html: str) -> str:
    if not cfg.username or not cfg.password:
        raise NotifyError(
            "缺少 SMTP 账号或授权码：请设置环境变量 MAIL_USERNAME（完整邮箱地址）和 "
            "MAIL_PASSWORD（163 邮箱的「SMTP 授权码」，不是登录密码）。"
        )
    message = _build_message(cfg, subject, text, html)

    try:
        if cfg.smtp_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=45, context=context) as server:
                server.login(cfg.username, cfg.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=45) as server:
                server.ehlo()
                if server.has_extn("STARTTLS"):
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(cfg.username, cfg.password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise NotifyError(
            "SMTP 登录失败（535）：请确认 MAIL_PASSWORD 是 163 邮箱的「SMTP 授权码」，"
            "并已在 163 邮箱设置中开启 SMTP 服务。"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"SMTP 发送失败：{exc}") from exc

    return "smtp"


def _send_resend(cfg: NotifyConfig, subject: str, text: str, html: str) -> str:
    if not cfg.resend_api_key:
        raise NotifyError("缺少 RESEND_API_KEY")
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {cfg.resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": cfg.resend_from,
            "to": [cfg.to],
            "subject": subject,
            "text": text,
            "html": html,
        },
        timeout=30,
    )
    if response.status_code >= 300:
        raise NotifyError(f"Resend 发送失败 HTTP {response.status_code}: {response.text[:300]}")
    return "resend"


FORM_ORIGIN = "https://hamilton-seoul-watch.example.com"


def _send_formsubmit(cfg: NotifyConfig, subject: str, text: str, html: str) -> str:
    """FormSubmit 无需注册即可把表单内容转发到邮箱（首次需在收件箱点一次激活链接）。

    FormSubmit 会检查请求来源，所以这里带上 Origin/Referer，
    否则服务端请求会被当成「本地 HTML 文件」拒绝。
    """
    response = requests.post(
        f"https://formsubmit.co/ajax/{cfg.to}",
        json={
            "name": cfg.from_name,
            "_subject": subject,
            "message": text,
            "_template": "box",
            "_captcha": "false",
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": FORM_ORIGIN,
            "Referer": FORM_ORIGIN + "/",
        },
        timeout=30,
    )
    if response.status_code >= 300:
        raise NotifyError(
            f"FormSubmit 发送失败 HTTP {response.status_code}: {response.text[:300]}。"
            "首次使用请先在收件箱点击 FormSubmit 的激活邮件。"
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    message = str(payload.get("message", ""))
    if "activation" in message.lower():
        raise NotifyError(
            "FormSubmit 还需要激活：请到 163 邮箱找到 FormSubmit 发来的 "
            "「Activate Form」邮件并点击链接，然后重新运行 test-email。"
        )
    if payload.get("success") in {"false", False}:
        raise NotifyError(f"FormSubmit 返回失败：{payload}")
    return "formsubmit"
