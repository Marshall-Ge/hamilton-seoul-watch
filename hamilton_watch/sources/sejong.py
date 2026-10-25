"""首尔世宗文化会馆「开票公告」板。

这是最权威的信号源之一：场馆会把每场演出的开票时间挂在这里，
标题形如「뮤지컬 <해밀턴> 티켓 오픈 안내」，并附带开票日期时间。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Item, SourceResult
from ..timeutil import parse_dt
from .base import BaseSource, mentions_target, register

BASE = "https://www.sejongpac.or.kr"
DEFAULT_BOARD = "https://www.sejongpac.or.kr/portal/bbs/B0000049/list.do?menuNo=200440"


@register
class SejongBoardSource(BaseSource):
    kind = "sejong_board"

    def collect(self) -> SourceResult:
        board_urls = [self.options.get("board_url") or DEFAULT_BOARD]
        board_urls += list(self.options.get("extra_board_urls") or [])

        items: list[Item] = []
        errors: list[str] = []

        for board_url in board_urls:
            try:
                page = self.fetcher.get(board_url, headers={"Referer": BASE})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{board_url}: {exc}")
                continue

            if "Markdown Content:" in page.text or "<tr" not in page.text.lower():
                parsed = self._parse_markdown(page.text, board_url)
                if not parsed:
                    errors.append(f"{board_url}: 未找到公告列表（页面结构可能变化）")
                items.extend(parsed)
                continue

            soup = BeautifulSoup(page.text, "lxml")
            rows = soup.select("table tbody tr")
            if not rows:
                errors.append(f"{board_url}: 未找到公告列表（页面结构可能变化）")
                continue

            for row in rows:
                item = self._parse_row(row, board_url)
                if item is not None:
                    items.append(item)

        if not items and errors:
            return self.failure("; ".join(errors))

        return self.result(items, boards=board_urls, errors=errors)

    def _parse_markdown(self, text: str, board_url: str) -> list[Item]:
        """阅读器代理返回的 Markdown 表格兜底解析。"""
        items: list[Item] = []
        pattern = re.compile(
            r"^\|\s*\d+\s*\|\s*\[([^\]]+)\]\((https?://[^)]+)\)\s*\|\s*"
            r"(\d{4}-\d{2}-\d{2})\s*\|\s*(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2})\s*\|\s*([^|]*)\|",
            re.MULTILINE,
        )
        for match in pattern.finditer(text):
            title = match.group(1).strip()
            if not mentions_target(title):
                continue
            items.append(
                Item(
                    source=self.name,
                    title=title,
                    url=match.group(2),
                    snippet=f"{match.group(3)} / {match.group(4)} / {match.group(5).strip()}",
                    published=parse_dt(match.group(3)),
                    open_at=parse_dt(match.group(4)),
                    kind="board",
                    publisher="世宗文化会馆",
                    raw_status=match.group(5).strip(),
                )
            )
        return items

    def _parse_row(self, row, board_url: str) -> Item | None:
        link = row.select_one("td.link a") or row.select_one("a[href*='view.do']")
        if link is None:
            return None

        title = link.get_text(" ", strip=True)
        if not mentions_target(title):
            return None

        cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
        posted = None
        open_at = None
        status_text = ""

        for cell in cells:
            if not cell:
                continue
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cell):
                posted = posted or parse_dt(cell)
            elif re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}$", cell):
                open_at = open_at or parse_dt(cell)
            elif cell in {"종료", "진행", "예정", "오픈", "마감"}:
                status_text = cell
            elif cell in {"진행중", "오픈예정"}:
                status_text = cell

        span = row.select_one("span[class^='stat_']")
        if span is not None and not status_text:
            status_text = span.get_text(" ", strip=True)

        href = urljoin(board_url, link.get("href", ""))
        return Item(
            source=self.name,
            title=title,
            url=href,
            snippet=" / ".join(c for c in cells if c),
            published=posted,
            open_at=open_at,
            kind="board",
            publisher="世宗文化会馆",
            raw_status=status_text,
        )
