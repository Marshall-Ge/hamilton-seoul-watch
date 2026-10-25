"""数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    """一条来自某个信息源的候选信息。"""

    source: str
    title: str
    url: str
    snippet: str = ""
    published: datetime | None = None
    # 官方公告里给出的"开票时间"（如果有）
    open_at: datetime | None = None
    kind: str = "news"  # news | board | page | search
    publisher: str = ""
    raw_status: str = ""

    def text(self) -> str:
        return f"{self.title} {self.snippet}".strip()

    def key(self) -> str:
        return self.url or f"{self.source}:{self.title}"


@dataclass
class SourceResult:
    name: str
    kind: str
    ok: bool
    items: list[Item] = field(default_factory=list)
    error: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class Signal:
    level: str  # on_sale | announced | info
    item: Item
    reason: str = ""
    open_at: datetime | None = None
    venue: str = "unknown"  # seoul | busan | unknown
    mentions_target_date: bool = False


@dataclass
class Assessment:
    status: str  # on_sale | announced | unknown
    signals: list[Signal]
    items: list[Item]
    sources: list[SourceResult]

    @property
    def ok_sources(self) -> int:
        return sum(1 for s in self.sources if s.ok)

    @property
    def failed_sources(self) -> list[SourceResult]:
        return [s for s in self.sources if not s.ok]

    def top_signals(self, level: str | None = None, limit: int = 8) -> list[Signal]:
        rank = {"on_sale": 0, "announced": 1, "info": 2}
        result = [s for s in self.signals if level is None or s.level == level]
        result.sort(
            key=lambda s: (
                rank.get(s.level, 3),
                s.item.published is None,
                -(s.item.published.timestamp() if s.item.published else 0),
            )
        )
        return result[:limit]

    def best_open_at(self) -> datetime | None:
        candidates = [s.open_at for s in self.signals if s.open_at is not None]
        return min(candidates) if candidates else None

    def target_date_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.mentions_target_date]
