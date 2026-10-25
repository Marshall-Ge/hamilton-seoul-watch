"""把抓取到的原始信息判定为「已开票 / 已公布开票时间 / 仅提及」。

判定原则（宁可在邮件里多说一句，也不要把「未开票」误报成「已开票」）：
- 世宗文化会馆公告板自带「开票日期时间」，以它为准；
- 新闻 / 博客标题里同时出现「해밀턴」+「티켓 오픈 / 예매 오픈 / 예매 시작」
  且没有「예정 / 임박 / 추후」等弱化词时，才升级为开票信号；
- 标题里带明确未来日期 → 只算「已公布开票时间」，发预告邮件；
- 只提到演出、没有开票动作 → 只进 5 天进度邮件，不单独告警。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .config import Config
from .models import Assessment, Item, Signal
from .timeutil import KST, find_dates, now_utc

OPEN_PATTERNS = [
    re.compile(r"티켓\s*오픈"),
    re.compile(r"예매\s*오픈"),
    re.compile(r"티켓팅"),
    re.compile(r"예매\s*시작"),
    re.compile(r"판매\s*시작"),
    re.compile(r"오픈\s*안내"),
    re.compile(r"예매\s*오픈일"),
    re.compile(r"부킹\s*오픈"),
]

WEAK_PATTERNS = [
    re.compile(r"예정"),
    re.compile(r"추후"),
    re.compile(r"미정"),
    re.compile(r"임박"),
    re.compile(r"초읽기"),
    re.compile(r"앞두고"),
    re.compile(r"예고"),
    re.compile(r"공지\s*예정"),
    re.compile(r"D\s*-\s*\d+"),
    re.compile(r"곧"),
    re.compile(r"가능성"),
    re.compile(r"전망"),
    re.compile(r"예상"),
    re.compile(r"듯"),
    re.compile(r"않을까"),
    re.compile(r"아마"),
    re.compile(r"하던데"),
    re.compile(r"\?"),
]

SALE_PATTERNS = [
    re.compile(r"예매\s*시작"),
    re.compile(r"판매\s*시작"),
    re.compile(r"티켓팅\s*시작"),
    re.compile(r"판매\s*중"),
    re.compile(r"오픈했"),
    re.compile(r"오늘\s*오픈"),
    re.compile(r"지금\s*오픈"),
    re.compile(r"돌입"),
]

BUSAN_PATTERN = re.compile(r"부산|드림씨어터|dream\s*theatre", re.I)
SEOUL_PATTERN = re.compile(r"서울|세종|sejong", re.I)
# 韩国演出相关词
KOREA_PATTERN = re.compile(r"내한|한국|서울|부산|세종|인터파크|멜론|예매처")
# 海外场次 / 旅行类信息：如果同时没有韩国相关词，直接忽略
FOREIGN_PATTERN = re.compile(r"뉴욕|런던|브로드웨이|웨스트엔드|west\s*end|broadway|new\s*york|london", re.I)
TRAVEL_PATTERN = re.compile(r"항공권|호텔|여행|투어|flight|airport", re.I)

# 票务网站的商品页
TICKET_HOSTS = ("interpark.com", "melon.com", "yes24.com", "sejongpac.or.kr", "ticketlink.co.kr")


def _first_open_match(text: str) -> re.Match | None:
    matches = [m for pattern in OPEN_PATTERNS for m in pattern.finditer(text)]
    if not matches:
        return None
    return min(matches, key=lambda m: m.start())


def _has_weak(text: str) -> bool:
    return any(pattern.search(text) for pattern in WEAK_PATTERNS)


def _has_sale(text: str) -> bool:
    return any(pattern.search(text) for pattern in SALE_PATTERNS)


def detect_venue(text: str) -> str:
    busan = bool(BUSAN_PATTERN.search(text))
    seoul = bool(SEOUL_PATTERN.search(text))
    if seoul:
        return "seoul"
    if busan:
        return "busan"
    return "unknown"


def _open_date_near(text: str, match: re.Match, now: datetime) -> datetime | None:
    # 韩语里日期常在「티켓 오픈」之前（11월 5일 티켓 오픈），所以前后都要看
    window = text[max(0, match.start() - 45) : match.start() + 90]
    dates = find_dates(window, now)
    if not dates:
        return None
    # 优先取离「开票」最近、且不早于 90 天前的日期
    plausible = [d for d in dates if d >= now - timedelta(days=90)]
    return plausible[0] if plausible else None


def _mentions_target_date(text: str, target: datetime, now: datetime) -> bool:
    for date in find_dates(text, now):
        if date.month == target.month and date.day == target.day:
            return True
    return False


def classify(item: Item, cfg: Config, now: datetime | None = None) -> Signal | None:
    now = now or now_utc()
    text = item.text()
    if not text.strip():
        return None
    if "해밀턴" not in text and "hamilton" not in text.lower():
        return None
    korea = bool(KOREA_PATTERN.search(text))
    if item.kind not in {"board", "page"}:
        if not korea and (FOREIGN_PATTERN.search(text) or TRAVEL_PATTERN.search(text)):
            return None
        # 没有任何韩国/演出上下文、也不是票务网站链接的，忽略
        if not korea and not any(host in item.url for host in TICKET_HOSTS):
            if not re.search(r"뮤지컬|공연|티켓|예매|캐스팅", text):
                return None

    if item.published is not None and item.published < now - timedelta(days=cfg.monitor.news_window_days):
        return None

    venue = detect_venue(text)
    target = cfg.show.target_date_dt
    mentions_target = _mentions_target_date(text, target, now)
    base = dict(venue=venue, mentions_target_date=mentions_target)

    # 0) 只看釜山场的消息不参与首尔告警
    if venue == "busan":
        return Signal(level="info", item=item, reason="提到釜山场（非首尔目标）", **base)

    # 1) 官方公告自带开票时间：最可信
    if item.open_at is not None:
        if item.open_at <= now:
            return Signal(
                level="on_sale",
                item=item,
                reason=f"官方公告的开票时间已到（{item.open_at.astimezone(KST):%m-%d %H:%M} KST）",
                open_at=item.open_at,
                **base,
            )
        return Signal(
            level="announced",
            item=item,
            reason=f"官方公告了开票时间（{item.open_at.astimezone(KST):%m-%d %H:%M} KST）",
            open_at=item.open_at,
            **base,
        )

    # 2) 指定页面监控：以页面上的按钮/标记为准
    if item.kind == "page":
        if item.raw_status == "open":
            return Signal(level="on_sale", item=item, reason="商品页出现「예매하기 / 예매중」等购票按钮", **base)
        if item.raw_status == "pre":
            return Signal(level="announced", item=item, reason="商品页显示「开票预告」", **base)
        return Signal(level="info", item=item, reason="页面提到该演出，但未发现购票按钮", **base)

    # 3) 官方公告出现了开票通知但没解析到时间
    if item.kind == "board":
        return Signal(level="announced", item=item, reason="官方开票公告板出现该演出", **base)

    # 5) 票务网站出现该演出的商品页
    if any(host in item.url for host in TICKET_HOSTS):
        return Signal(
            level="announced",
            item=item,
            reason="票务网站已出现该演出页面（可能已开放或即将开放购票）",
            **base,
        )

    # Naver 综合搜索的「摘要」噪声很大（常混入猜测性博客），只看标题；
    # 有发布时间的新闻才允许用摘要辅助判断。
    scan = item.title if item.kind == "search" else text
    open_match = _first_open_match(scan)
    if open_match is None:
        return Signal(level="info", item=item, reason="仅提及演出，未发现开票动作", **base)

    weak = _has_weak(text)
    open_at = _open_date_near(scan, open_match, now)

    if open_at is not None:
        if open_at <= now:
            return Signal(level="on_sale", item=item, reason="新闻中出现已到期的开票日期", open_at=open_at, **base)
        return Signal(level="announced", item=item, reason="新闻中公布了未来开票日期", open_at=open_at, **base)

    if weak:
        return Signal(level="info", item=item, reason="提到开票但仍是「预计/临近」状态，日期未定", **base)

    if _has_sale(scan):
        return Signal(level="on_sale", item=item, reason="新闻标题显示开票已开始", **base)

    if item.published is not None and item.published >= now - timedelta(days=3):
        return Signal(level="on_sale", item=item, reason="近 3 天内出现开票消息", **base)

    # 有开票动作、但既没有日期也没有「开始」字样：只有标题本身在说开票，
    # 才按「发现开票信号」处理；只是摘要里顺带一提的，不打扰。
    if _first_open_match(item.title) is None:
        return Signal(level="info", item=item, reason="摘要提到开票，但标题未确认", **base)
    return Signal(level="announced", item=item, reason="出现开票相关标题，但时间未确认", **base)


def assess(items: list[Item], sources, cfg: Config, now: datetime | None = None) -> Assessment:
    now = now or now_utc()
    signals: list[Signal] = []
    seen: set[tuple[str, str]] = set()

    for item in items:
        signal = classify(item, cfg, now)
        if signal is None:
            continue
        key = (signal.item.key(), signal.level)
        if key in seen:
            continue
        seen.add(key)
        signals.append(signal)

    signals.sort(key=lambda s: _level_rank(s.level))

    has_on_sale = any(s.level == "on_sale" for s in signals)
    has_announced = any(s.level == "announced" for s in signals)
    status = "on_sale" if has_on_sale else ("announced" if has_announced else "unknown")

    return Assessment(status=status, signals=signals, items=items, sources=list(sources))


def _level_rank(level: str) -> int:
    return {"on_sale": 0, "announced": 1, "info": 2}.get(level, 3)
