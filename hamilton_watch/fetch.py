"""HTTP 抓取层。

策略：
1. 直连目标站点（requests）；
2. 直连失败 / 被反爬拦截时，自动回退到公共阅读器代理 r.jina.ai，
   由代理服务器去抓取并返回可读文本 —— 这样即使本机所在网络访问不了
   韩国站点，云端 runner 依然能拿到内容。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
READER_PREFIX = "https://r.jina.ai/"

BLOCK_STATUS = {401, 403, 405, 406, 409, 418, 423, 429, 451}


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    text: str
    via: str  # direct | reader
    headers: dict

    @property
    def looks_blocked(self) -> bool:
        """只在「状态码异常」或「页面很小且含拦截提示」时判定为被拦截。

        Naver 等页面会在 JS 里出现 captcha/자동입력 방지 之类的字样，
        不能仅凭关键词就把 900KB 的正常页面误判成拦截页。
        """
        if self.status in BLOCK_STATUS:
            return True
        if self.status >= 400:
            return True
        if len(self.text) >= 20_000:
            return False
        lowered = self.text.lower()
        needles = (
            "access denied",
            "you have been blocked",
            "captcha",
            "cf-browser-verification",
            "자동입력 방지",
            "비정상적인 접근",
            "서비스 이용이 제한",
        )
        return any(n in lowered for n in needles)


def _decode(resp: requests.Response) -> str:
    content_type = resp.headers.get("content-type", "")
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    if match:
        try:
            return resp.content.decode(match.group(1), errors="replace")
        except LookupError:
            pass
    raw = resp.content
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, *, timeout: float = 25.0, retries: int = 2, use_reader: bool = True) -> None:
        self.timeout = timeout
        self.retries = retries
        self.use_reader = use_reader
        self._cache: dict[str, FetchResult] = {}
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8,zh-CN;q=0.7",
            }
        )

    def get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        reader_fallback: bool = True,
        force_reader: bool = False,
    ) -> FetchResult:
        if url in self._cache and not force_reader:
            return self._cache[url]

        if force_reader:
            result = self._via_reader(url)
            self._cache[url] = result
            return result

        last_error: Exception | None = None
        blocked: FetchResult | None = None

        for attempt in range(self.retries + 1):
            try:
                resp = self._session.get(url, timeout=self.timeout, headers=headers or {})
                result = FetchResult(
                    url=url,
                    final_url=resp.url,
                    status=resp.status_code,
                    text=_decode(resp),
                    via="direct",
                    headers=dict(resp.headers),
                )
                if result.looks_blocked or resp.status_code >= 500:
                    blocked = result
                    raise FetchError(f"HTTP {resp.status_code} (blocked or server error)")
                self._cache[url] = result
                return result
            except Exception as exc:  # noqa: BLE001 - 网络层任何异常都重试
                last_error = exc
                log.debug("direct fetch failed url=%s attempt=%s err=%s", url, attempt + 1, exc)
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))

        if reader_fallback and self.use_reader:
            try:
                result = self._via_reader(url)
                self._cache[url] = result
                return result
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        detail = f"{last_error}" if last_error else "unknown error"
        if blocked is not None:
            detail = f"HTTP {blocked.status}: {detail}"
        raise FetchError(f"无法获取 {url}: {detail}")

    def _via_reader(self, url: str) -> FetchResult:
        reader_url = READER_PREFIX + url
        resp = self._session.get(
            reader_url,
            timeout=max(self.timeout, 45.0),
            headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
        )
        if resp.status_code >= 400:
            raise FetchError(f"reader HTTP {resp.status_code} for {url}")
        text = _decode(resp)
        if not text.strip():
            raise FetchError(f"reader returned empty body for {url}")
        return FetchResult(
            url=url,
            final_url=url,
            status=resp.status_code,
            text=text,
            via="reader",
            headers=dict(resp.headers),
        )


def quote_query(value: str) -> str:
    return quote(value, safe="")
