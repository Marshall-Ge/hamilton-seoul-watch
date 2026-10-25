"""Google News RSS 信息源。

优点：无需 API key、返回带发布时间的新闻、几乎不会被反爬。
缺点：收录有几分钟到几十分钟延迟，适合作为「开票新闻」的佐证。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

from ..models import Item, SourceResult
from ..timeutil import parse_rfc822
from .base import BaseSource, mentions_target, register


@register
class GoogleNewsSource(BaseSource):
    kind = "google_news"

    def collect(self) -> SourceResult:
        queries = list(self.options.get("queries") or ["해밀턴 티켓"])
        window_days = int(self.options.get("window_days") or 45)

        items: list[Item] = []
        errors: list[str] = []

        for query in queries:
            url = (
                "https://news.google.com/rss/search?q="
                + quote_plus(f"{query} when:{window_days}d")
                + "&hl=ko&gl=KR&ceid=KR:ko"
            )
            try:
                page = self.fetcher.get(url, headers={"Accept": "application/rss+xml,*/*"})
                parsed = self._parse(page.text)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{query}: {exc}")
                continue

            for item in parsed:
                if mentions_target(item.title):
                    items.append(item)

        if not items and errors:
            return self.failure("; ".join(errors))

        return self.result(_dedupe(items), errors=errors)

    def _parse(self, text: str) -> list[Item]:
        items: list[Item] = []
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return items

        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            published = parse_rfc822(node.findtext("pubDate"))
            source_node = node.find("source")
            publisher = (source_node.text or "").strip() if source_node is not None else ""
            description = (node.findtext("description") or "").strip()
            items.append(
                Item(
                    source=self.name,
                    title=title,
                    url=link,
                    snippet=_strip_html(description)[:400],
                    published=published,
                    kind="news",
                    publisher=publisher,
                )
            )
        return items


def _strip_html(value: str) -> str:
    if "<" not in value:
        return value
    from bs4 import BeautifulSoup

    return BeautifulSoup(value, "lxml").get_text(" ", strip=True)


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    result: list[Item] = []
    for item in items:
        key = (item.title or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
