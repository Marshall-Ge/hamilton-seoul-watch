"""Naver 综合搜索信息源。

Naver 是韩国最大的门户，开票消息通常先出现在新闻 / 博客里。
这里抓取搜索结果里的标题与摘要，用来发现「티켓 오픈（开票）」类消息。
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..models import Item, SourceResult
from .base import BaseSource, mentions_target, register

SKIP_HOSTS = (
    "ader.naver.com",
    "search.naver.com",
    "nid.naver.com",
    "help.naver.com",
    "dict.naver.com",
    "map.naver.com",
    "shopping.naver.com",
    "m.search.naver.com",
    "pan.naver.com",
)

TITLE_SUFFIXES = ("새 창 열림", "새창", "바로가기")


@register
class NaverSearchSource(BaseSource):
    kind = "naver_search"

    def collect(self) -> SourceResult:
        queries = list(self.options.get("queries") or ["해밀턴 예매"])
        limit = int(self.options.get("limit") or 20)

        items: list[Item] = []
        errors: list[str] = []

        for query in queries:
            url = (
                "https://search.naver.com/search.naver?where=nexearch&sm=tab_jum&query="
                + quote_plus(query)
            )
            try:
                page = self.fetcher.get(url)
                parsed = self._parse(page.text, limit=limit)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{query}: {exc}")
                continue
            for item in parsed:
                if mentions_target(item.title) or mentions_target(item.snippet):
                    items.append(item)

        if not items and errors:
            return self.failure("; ".join(errors))

        return self.result(_dedupe(items)[:limit], errors=errors)

    def _parse(self, html: str, *, limit: int) -> list[Item]:
        if "Markdown Content:" in html or ("<html" not in html.lower() and "](" in html):
            return _parse_markdown(html, limit=limit)

        soup = BeautifulSoup(html, "lxml")
        grouped: dict[str, dict] = {}

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not href.startswith("http"):
                continue
            if any(host in href for host in SKIP_HOSTS):
                continue

            text = _clean(anchor.get_text(" ", strip=True))
            if not text or len(text) < 4:
                continue

            slot = grouped.setdefault(href, {"texts": [], "published": None})
            slot["texts"].append(text)
            if slot["published"] is None:
                published = _nearby_time(anchor)
                if published:
                    slot["published"] = published

        items: list[Item] = []
        for href, slot in grouped.items():
            texts = [t for t in slot["texts"] if t]
            if not texts:
                continue
            title = min(
                (t for t in texts if len(t) >= 8),
                key=len,
                default=max(texts, key=len),
            )
            snippet = max(texts, key=len)
            if title == snippet:
                snippet = ""
            items.append(
                Item(
                    source=self.name,
                    title=title[:200],
                    url=href,
                    snippet=snippet[:400],
                    published=slot["published"],
                    kind="search",
                    publisher=_host(href),
                )
            )
            if len(items) >= limit * 3:
                break

        # 保留页面顺序（相关度），最多 limit*3 条交给上层筛选
        return items


def _parse_markdown(text: str, *, limit: int) -> list[Item]:
    """阅读器代理返回的是 Markdown，用 [标题](链接) 形式兜底解析。"""
    import re as _re

    items: list[Item] = []
    seen: set[str] = set()
    pattern = _re.compile(r"\[([^\]]{4,200})\]\((https?://[^)\s]+)\)")
    for match in pattern.finditer(text):
        title = _clean(match.group(1))
        url = match.group(2)
        if any(host in url for host in SKIP_HOSTS):
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append(
            Item(
                source="naver",
                title=title,
                url=url,
                snippet="",
                kind="search",
                publisher=_host(url),
            )
        )
        if len(items) >= limit * 3:
            break
    return items


def _clean(text: str) -> str:
    for suffix in TITLE_SUFFIXES:
        text = text.replace(suffix, " ")
    return re.sub(r"\s+", " ", text).strip()


def _host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url)
    return match.group(1) if match else ""


def _nearby_time(anchor) -> "datetime | None":
    """Naver 结果里常见「3시간 전 / 2일 전 / 2026.10.12.」等相对时间。"""
    import datetime as _dt

    from ..timeutil import KST, now_utc

    block = anchor
    for _ in range(4):
        block = block.parent
        if block is None:
            return None
        text = block.get_text(" ", strip=True)
        if len(text) > 60:
            break

    text = block.get_text(" ", strip=True) if block else ""
    now = now_utc().astimezone(KST)

    match = re.search(r"(\d+)\s*분\s*전", text)
    if match:
        return now - _dt.timedelta(minutes=int(match.group(1)))
    match = re.search(r"(\d+)\s*시간\s*전", text)
    if match:
        return now - _dt.timedelta(hours=int(match.group(1)))
    match = re.search(r"(\d+)\s*일\s*전", text)
    if match:
        return now - _dt.timedelta(days=int(match.group(1)))
    match = re.search(r"(20\d{2})\.(\d{2})\.(\d{2})", text)
    if match:
        try:
            return _dt.datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=KST)
        except ValueError:
            return None
    return None


def _dedupe(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    result: list[Item] = []
    for item in items:
        key = item.url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
