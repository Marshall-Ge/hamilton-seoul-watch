"""通用页面监控信息源。

用于盯一个具体页面（例如 Interpark 商品页、Melon 商品页）：
- 页面里出现关键词 → 记一条信息；
- 关键词附近的文本里出现「예매하기 / 예매중」等标记 → 视为已开票；
- 出现「오픈 예정」等标记 → 视为已公布开票时间。

页面内容哈希会记录在 meta 里，便于发现「页面有更新但规则没命中」的情况。
"""

from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup

from ..models import Item, SourceResult
from .base import BaseSource, register

DEFAULT_OPEN_MARKERS = ["예매하기", "예매중", "예매 가능", "BOOK NOW", "Book Now", "좌석 선택"]
DEFAULT_PRE_MARKERS = ["예매 오픈 예정", "오픈 예정", "오픈예정", "준비중", "COMING SOON"]


@register
class PageSource(BaseSource):
    kind = "page"

    def collect(self) -> SourceResult:
        urls = list(self.options.get("urls") or [])
        if not urls:
            return self.failure("未配置 urls")

        keyword = str(self.options.get("require_keyword") or "해밀턴")
        open_markers = list(self.options.get("open_markers") or DEFAULT_OPEN_MARKERS)
        pre_markers = list(self.options.get("pre_markers") or DEFAULT_PRE_MARKERS)

        items: list[Item] = []
        errors: list[str] = []
        hashes: dict[str, str] = {}
        found_any = False

        force_reader = bool(self.options.get("force_reader", False))

        for url in urls:
            try:
                page = self.fetcher.get(url, force_reader=force_reader)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: {exc}")
                continue

            text = _visible_text(page.text)
            hashes[url] = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]

            index = text.find(keyword)
            if index < 0:
                continue
            found_any = True

            snippet = text[max(0, index - 200) : index + 400]
            snippet = re.sub(r"\s+", " ", snippet).strip()

            open_hit = next((m for m in open_markers if m in snippet), "")
            pre_hit = next((m for m in pre_markers if m in snippet), "")

            if open_hit:
                note = f"页面出现开票标记「{open_hit}」"
            elif pre_hit:
                note = f"页面出现预告标记「{pre_hit}」"
            else:
                note = "页面提到该演出，但未发现开票标记"

            items.append(
                Item(
                    source=self.name,
                    title=f"{keyword} @ {url}",
                    url=page.final_url or url,
                    snippet=f"{note}｜{snippet[:280]}",
                    kind="page",
                    publisher=_host(url),
                    raw_status="open" if open_hit else ("pre" if pre_hit else "neutral"),
                )
            )

        if not items and errors and not found_any:
            return self.failure("; ".join(errors))

        return self.result(items, hashes=hashes, errors=errors)

    def check(self) -> SourceResult:
        return self.collect()


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url)
    return match.group(1) if match else ""
