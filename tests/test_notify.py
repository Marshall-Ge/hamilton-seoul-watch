from __future__ import annotations

import unittest
from unittest import mock

from hamilton_watch.config import NotifyConfig
from hamilton_watch.notify import NotifyError, send_email


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=45, context=None):
        self.host = host
        self.port = port
        self.message = None
        self.logged_in = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.message = message


class NotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSMTP.instances = []

    def _cfg(self, **kwargs) -> NotifyConfig:
        base = dict(
            provider="smtp",
            to="marshall_gefexiang@163.com",
            username="marshall_gefexiang@163.com",
            password="auth-code",
            smtp_host="smtp.163.com",
            smtp_port=465,
            smtp_ssl=True,
        )
        base.update(kwargs)
        return NotifyConfig(**base)

    def test_smtp_message_structure(self):
        with mock.patch("smtplib.SMTP_SSL", FakeSMTP):
            provider = send_email(self._cfg(), "测试主题", "纯文本内容", "<p>HTML 内容</p>")
        self.assertEqual(provider, "smtp")
        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.host, "smtp.163.com")
        self.assertEqual(smtp.port, 465)
        self.assertEqual(smtp.logged_in, ("marshall_gefexiang@163.com", "auth-code"))
        message = smtp.message
        self.assertEqual(message["To"], "marshall_gefexiang@163.com")
        self.assertIn("marshall_gefexiang@163.com", message["From"])
        self.assertEqual(str(message["Subject"]), "测试主题")

    def test_missing_password_raises(self):
        with self.assertRaises(NotifyError) as ctx:
            send_email(self._cfg(password=""), "s", "t", "<p>h</p>")
        self.assertIn("MAIL_PASSWORD", str(ctx.exception))

    def test_missing_recipient_raises(self):
        with self.assertRaises(NotifyError) as ctx:
            send_email(self._cfg(to=""), "s", "t", "<p>h</p>")
        self.assertIn("MAIL_TO", str(ctx.exception))

    def test_none_provider(self):
        self.assertEqual(send_email(self._cfg(provider="none"), "s", "t", "<p>h</p>"), "none")


if __name__ == "__main__":
    unittest.main()
