from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from hamilton_watch.models import Item, SourceResult
from hamilton_watch.run import run
from hamilton_watch.timeutil import now_utc

CONFIG = """
[show]
title = "汉密尔顿 Hamilton（韩国首尔）"
target_date = "2027-01-25"
venue = "世宗文化会馆"
run_period = "2027-01-21 ~ 2027-03-20"

[monitor]
digest_every_days = 5
digest_hour_kst = 10
startup_email = true
state_file = "state.json"

[notify]
provider = "none"
to = "test@example.com"
"""


class RunFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "config.toml").write_text(CONFIG, encoding="utf-8")
        self.args = argparse.Namespace(
            mode="check",
            config=str(self.dir / "config.toml"),
            dry_run=False,
            force_alert=False,
            verbose=False,
        )
        self.state_path = self.dir / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, items: list[Item]) -> int:
        result = SourceResult(name="sejong", kind="board", ok=True, items=items)
        with mock.patch("hamilton_watch.run.collect", return_value=(items, [result])):
            return run(self.args)

    def test_first_run_sends_startup_and_saves_state(self):
        self.assertEqual(self._run([]), 0)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["stats"]["digests"], 1)
        self.assertIsNotNone(state["last_digest_at"])
        self.assertEqual(state["last_status"], "unknown")

    def test_second_run_is_quiet(self):
        self._run([])
        before = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._run([])
        after = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(before["stats"]["digests"], after["stats"]["digests"])

    def test_on_sale_sends_alert_and_records_it(self):
        item = Item(
            source="sejong",
            title="뮤지컬 <해밀턴> 티켓 오픈 안내",
            url="https://example.com/1",
            published=now_utc() - timedelta(days=1),
            open_at=now_utc() - timedelta(hours=1),
            kind="board",
        )
        self.assertEqual(self._run([item]), 0)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["last_status"], "on_sale")
        self.assertIn("on_sale", state["last_alert_at"])
        self.assertEqual(state["stats"]["alerts"], 1)


if __name__ == "__main__":
    unittest.main()
