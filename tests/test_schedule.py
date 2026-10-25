from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from hamilton_watch.config import Config, MonitorConfig, NotifyConfig, ShowConfig
from hamilton_watch.models import Assessment, Item, Signal
from hamilton_watch.report import render_alert, render_digest, render_test
from hamilton_watch.run import _digest_due, decide
from hamilton_watch.state import State
from hamilton_watch.timeutil import KST

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=KST)


def make_config() -> Config:
    return Config(
        show=ShowConfig(target_date="2027-01-25"),
        monitor=MonitorConfig(digest_every_days=5, digest_hour_kst=10),
        notify=NotifyConfig(to="test@example.com"),
        sources=[],
        base_dir=Path("."),
        state_path=Path("state.json"),
    )


def make_assessment(status: str) -> Assessment:
    if status == "announced":
        open_at = NOW + timedelta(days=3)
    elif status == "on_sale":
        open_at = NOW - timedelta(days=1)
    else:
        open_at = None
    item = Item(
        source="sejong",
        title="뮤지컬 <해밀턴> 티켓 오픈 안내",
        url="https://example.com/1",
        published=NOW - timedelta(days=1),
        open_at=open_at,
        kind="board",
    )
    level = "on_sale" if status == "on_sale" else "announced" if status == "announced" else "info"
    return Assessment(status=status, signals=[Signal(level=level, item=item, open_at=open_at)], items=[item], sources=[])


class DigestScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_config()

    def test_startup_digest(self):
        state = State()
        self.assertTrue(_digest_due(self.cfg, state, "unknown", NOW, is_new=True))

    def test_not_due_after_four_days(self):
        state = State(created_at=NOW - timedelta(days=20), last_digest_at=NOW - timedelta(days=4))
        self.assertFalse(_digest_due(self.cfg, state, "unknown", NOW, is_new=False))

    def test_due_after_five_days(self):
        state = State(created_at=NOW - timedelta(days=20), last_digest_at=NOW - timedelta(days=5))
        self.assertTrue(_digest_due(self.cfg, state, "unknown", NOW, is_new=False))

    def test_digest_waits_for_morning(self):
        early = NOW.replace(hour=3)
        state = State(created_at=NOW - timedelta(days=20), last_digest_at=NOW - timedelta(days=6))
        self.assertFalse(_digest_due(self.cfg, state, "unknown", early, is_new=False))

    def test_no_digest_when_on_sale(self):
        state = State(created_at=NOW - timedelta(days=20), last_digest_at=NOW - timedelta(days=6))
        self.assertFalse(_digest_due(self.cfg, state, "on_sale", NOW, is_new=False))


class DecideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_config()

    def test_on_sale_alerts_once_then_waits(self):
        state = State(created_at=NOW - timedelta(days=10), last_status="unknown")
        assessment = make_assessment("on_sale")
        actions = decide(self.cfg, state, assessment, NOW, is_new=False, force_alert=False)
        self.assertEqual([a["kind"] for a in actions], ["alert"])

        state.mark_alert("on_sale", NOW)
        actions = decide(self.cfg, state, assessment, NOW + timedelta(hours=1), is_new=False, force_alert=False)
        self.assertEqual(actions, [])

        actions = decide(self.cfg, state, assessment, NOW + timedelta(hours=25), is_new=False, force_alert=False)
        self.assertEqual([a["kind"] for a in actions], ["alert"])

    def test_announced_alerts_on_transition(self):
        state = State(created_at=NOW - timedelta(days=10), last_status="unknown")
        assessment = make_assessment("announced")
        actions = decide(self.cfg, state, assessment, NOW, is_new=False, force_alert=False)
        self.assertEqual([a["kind"] for a in actions], ["alert"])

    def test_announced_does_not_repeat(self):
        state = State(created_at=NOW - timedelta(days=10), last_status="announced")
        state.last_digest_at = NOW - timedelta(days=1)
        state.mark_alert("announced", NOW - timedelta(hours=2))
        assessment = make_assessment("announced")
        actions = decide(self.cfg, state, assessment, NOW, is_new=False, force_alert=False)
        self.assertEqual(actions, [])


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_config()

    def test_alert_renders(self):
        for status in ("on_sale", "announced"):
            subject, text, html = render_alert(self.cfg, make_assessment(status), State(), NOW)
            self.assertIn("汉密尔顿", subject)
            self.assertIn("2027-01-25", text)
            self.assertIn("<html", html)

    def test_digest_renders(self):
        subject, text, html = render_digest(self.cfg, make_assessment("unknown"), State(), NOW, startup=True)
        self.assertIn("监控已启动", subject)
        self.assertIn("尚未公布", text)

    def test_test_email_renders(self):
        subject, text, html = render_test(self.cfg, NOW)
        self.assertIn("测试", subject)
        self.assertIn("test@example.com", text)
        self.assertIn("<html", html)


if __name__ == "__main__":
    unittest.main()
