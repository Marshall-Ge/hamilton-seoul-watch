"""信息源集合。导入即完成注册。"""

from .base import BaseSource, build_source, mentions_target
from .google_news import GoogleNewsSource
from .naver import NaverSearchSource
from .page import PageSource
from .sejong import SejongBoardSource

__all__ = [
    "BaseSource",
    "build_source",
    "mentions_target",
    "GoogleNewsSource",
    "NaverSearchSource",
    "PageSource",
    "SejongBoardSource",
]
