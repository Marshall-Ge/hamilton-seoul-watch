"""信息源基类与注册表。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..config import SourceConfig
from ..fetch import Fetcher
from ..models import Item, SourceResult

log = logging.getLogger(__name__)

TARGET_PATTERNS = ("해밀턴", "hamilton", "汉密尔顿")


def mentions_target(text: str) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in TARGET_PATTERNS)


class BaseSource(ABC):
    kind = "base"

    def __init__(self, config: SourceConfig, fetcher: Fetcher, show) -> None:
        self.config = config
        self.options = config.options
        self.fetcher = fetcher
        self.show = show
        self.name = config.name or config.type

    @abstractmethod
    def collect(self) -> SourceResult: ...

    def result(self, items: list[Item], **meta) -> SourceResult:
        return SourceResult(name=self.name, kind=self.kind, ok=True, items=items, meta=meta)

    def failure(self, error: Exception | str) -> SourceResult:
        message = f"{error}"
        log.warning("source failed name=%s error=%s", self.name, message)
        return SourceResult(name=self.name, kind=self.kind, ok=False, error=message[:400])


_REGISTRY: dict[str, type[BaseSource]] = {}


def register(cls: type[BaseSource]) -> type[BaseSource]:
    _REGISTRY[cls.kind] = cls
    return cls


def build_source(config: SourceConfig, fetcher: Fetcher, show) -> BaseSource:
    if config.type not in _REGISTRY:
        raise ValueError(f"未知的信息源类型: {config.type}")
    return _REGISTRY[config.type](config, fetcher, show)
