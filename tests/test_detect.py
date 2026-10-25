from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from hamilton_watch.config import Config, MonitorConfig, NotifyConfig, ShowConfig, SourceConfig
from hamilton_watch.detect import assess, classify
from hamilton_watch.fetch import FetchResult
from hamilton_watch.models import Item
from hamilton_watch.sources.naver import NaverSearchSource
from hamilton_watch.sources.page import PageSource
from hamilton_watch.sources.sejong import SejongBoardSource
from hamilton_watch.timeutil import KST, find_dates

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=KST)


def make_config() -> Config:
    return Config(
        show=ShowConfig(target_date="2027-01-25"),
        monitor=MonitorConfig(),
        notify=NotifyConfig(to="test@example.com"),
        sources=[],
        base_dir=Path("."),
        state_path=Path("state.json"),
    )


class FakeFetcher:
    def __init__(self, body: str):
        self.body = body

    def get(self, url: str, **kwargs) -> FetchResult:
        return FetchResult(url=url, final_url=url, status=200, text=self.body, via="fake", headers={})


def make_item(title: str, snippet: str = "", *, url: str = "https://blog.naver.com/x/1", published=None, kind="news") -> Item:
    return Item(source="test", title=title, url=url, snippet=snippet, published=published, kind=kind)


class DetectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_config()

    def test_open_date_in_future_is_announced(self):
        item = make_item("뮤지컬 '해밀턴' 11월 5일 티켓 오픈", published=NOW - timedelta(days=1))
        signal = classify(item, self.cfg, NOW)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.level, "announced")
        self.assertEqual(signal.open_at.month, 11)
        self.assertEqual(signal.open_at.day, 5)

    def test_on_sale_keywords(self):
        item = make_item("해밀턴 티켓 오픈…예매 시작", published=NOW)
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "on_sale")

    def test_speculation_is_not_alert(self):
        item = make_item(
            "뮤지컬 해밀턴 내한 공연 일정",
            "캐스팅 발표와 함께 예매 시작 소식도 빠르게 전해질 가능성이 높습니다",
        )
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "info")

    def test_question_mark_is_weak(self):
        item = make_item("해밀턴 부산 1차 티켓팅 예매 오픈 ?", "티켓팅 시작하던데요")
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "info")

    def test_busan_only_is_info(self):
        item = make_item("해밀턴 부산 공연 티켓 오픈", published=NOW)
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "info")
        self.assertEqual(signal.venue, "busan")

    def test_foreign_hamilton_ignored(self):
        item = make_item("런던 해밀턴 티켓 오픈", published=NOW)
        self.assertIsNone(classify(item, self.cfg, NOW))

    def test_ticket_site_appearance_is_announced(self):
        item = make_item(
            "뮤지컬 해밀턴",
            url="https://tickets.interpark.com/goods/26001234",
            kind="search",
        )
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "announced")

    def test_target_date_mention(self):
        item = make_item("해밀턴 1월 25일 공연 티켓 오픈 안내", published=NOW)
        signal = classify(item, self.cfg, NOW)
        self.assertTrue(signal.mentions_target_date)

    def test_bad_speculation_does_not_alert_overall(self):
        items = [
            make_item("해밀턴 내한 확정", "예매 일정은 추후 발표 예정입니다"),
            make_item("해밀턴 예매 방법 정리", "티켓 오픈을 기다리는 분들"),
        ]
        assessment = assess(items, [], self.cfg, NOW)
        self.assertEqual(assessment.status, "unknown")


class SejongBoardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_config()

    def _collect(self, fixture: str):
        body = (FIXTURES / fixture).read_text(encoding="utf-8")
        source = SejongBoardSource(
            SourceConfig(type="sejong_board", name="sejong", options={"board_url": "https://example.com"}),
            FakeFetcher(body),
            self.cfg.show,
        )
        return source.collect()

    def test_future_board_post(self):
        result = self._collect("sejong_board_future.html")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertIn("해밀턴", item.title)
        self.assertIsNotNone(item.open_at)
        self.assertEqual(item.open_at.day, 5)
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "announced")

    def test_past_board_post(self):
        result = self._collect("sejong_board_past.html")
        item = result.items[0]
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "on_sale")

    def test_other_shows_ignored(self):
        result = self._collect("sejong_board_future.html")
        self.assertTrue(all("해밀턴" in item.title for item in result.items))

    def test_markdown_fallback(self):
        body = (FIXTURES / "sejong_board_reader.md").read_text(encoding="utf-8")
        source = SejongBoardSource(
            SourceConfig(type="sejong_board", name="sejong", options={"board_url": "https://example.com"}),
            FakeFetcher(body),
            self.cfg.show,
        )
        result = source.collect()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertIn("해밀턴", item.title)
        self.assertEqual((item.open_at.month, item.open_at.day, item.open_at.hour), (10, 14, 12))
        signal = classify(item, self.cfg, NOW)
        self.assertEqual(signal.level, "on_sale")


class PageSourceTests(unittest.TestCase):
    def test_page_with_booking_button(self):
        cfg = make_config()
        html = "<html><body><h1>뮤지컬 해밀턴 - 세종문화회관</h1><button>예매하기</button></body></html>"
        source = PageSource(
            SourceConfig(
                type="page",
                name="goods",
                options={"urls": ["https://tickets.interpark.com/goods/1"], "require_keyword": "해밀턴"},
            ),
            FakeFetcher(html),
            cfg.show,
        )
        result = source.collect()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.items), 1)
        signal = classify(result.items[0], cfg, NOW)
        self.assertEqual(signal.level, "on_sale")


class NaverMarkdownTests(unittest.TestCase):
    def test_markdown_links(self):
        cfg = make_config()
        md = """Title: 해밀턴 예매
Markdown Content:
[뮤지컬 해밀턴 티켓 오픈 안내](https://blog.naver.com/abc/1)
[뮤지컬 해밀턴 내한 확정](https://blog.naver.com/abc/2)
"""
        source = NaverSearchSource(
            SourceConfig(type="naver_search", name="naver", options={"queries": ["해밀턴"], "limit": 10}),
            FakeFetcher(md),
            cfg.show,
        )
        items = source._parse(md, limit=10)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "뮤지컬 해밀턴 티켓 오픈 안내")
        signal = classify(items[0], cfg, NOW)
        self.assertEqual(signal.level, "announced")


class DateTests(unittest.TestCase):
    def test_various_formats(self):
        dates = find_dates("2027-01-25 / 2027.1.25 / 1월 25일 / 1.25", NOW)
        self.assertEqual(len(dates), 1)
        self.assertEqual((dates[0].year, dates[0].month, dates[0].day), (2027, 1, 25))
        self.assertEqual(len(find_dates("11월 5일 그리고 12월 20일", NOW)), 2)

    def test_year_rollover(self):
        dates = find_dates("12월 30일", NOW)
        self.assertEqual(dates[0].year, 2026)


if __name__ == "__main__":
    unittest.main()
