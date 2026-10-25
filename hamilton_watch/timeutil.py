"""时区与日期处理工具。"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
CST = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DOTTED = re.compile(r"(?<!\d)(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\d)")
_SHORT_DOTTED = re.compile(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)")


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    """解析常见日期时间字符串，返回带时区的 datetime（KST 视为本地）。"""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None

    # ISO 8601（含 Z / +09:00）
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt
    except ValueError:
        pass

    # 2026-10-15 14:00 / 2026.10.15 14:00 / 2026-10-15
    m = re.match(
        r"^(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})일?"
        r"(?:[ T]+(\d{1,2}):(\d{2}))?",
        value,
    )
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        try:
            return datetime(year, month, day, hour, minute, tzinfo=KST)
        except ValueError:
            return None

    # 10월 15일 14:00（无年份，按 KST 当年/跨年推断）
    m = re.match(r"^(\d{1,2})\s*월\s*(\d{1,2})\s*일(?:\s*(\d{1,2}):(\d{2}))?", value)
    if m:
        now = now_utc().astimezone(KST)
        month, day = int(m.group(1)), int(m.group(2))
        hour = int(m.group(3) or 0)
        minute = int(m.group(4) or 0)
        year = now.year
        try:
            dt = datetime(year, month, day, hour, minute, tzinfo=KST)
        except ValueError:
            return None
        if dt < now - timedelta(days=180):
            try:
                dt = dt.replace(year=year + 1)
            except ValueError:
                return None
        return dt

    return None


def parse_rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def date_from_parts(year: int, month: int, day: int, *, hour: int = 0, minute: int = 0) -> datetime | None:
    try:
        return datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None


def infer_year(month: int, day: int, now: datetime | None = None) -> int:
    now = now or now_utc()
    kst = now.astimezone(KST)
    year = kst.year
    candidate = date_from_parts(year, month, day)
    if candidate is not None and candidate < kst - timedelta(days=180):
        return year + 1
    return year


def find_dates(text: str, now: datetime | None = None) -> list[datetime]:
    """从文本中提取日期（KST 零点），按出现顺序去重。"""
    now = now or now_utc()
    found: list[datetime] = []
    seen: set[tuple[int, int, int]] = set()

    for m in _DOTTED.finditer(text):
        dt = date_from_parts(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt and (dt.year, dt.month, dt.day) not in seen:
            seen.add((dt.year, dt.month, dt.day))
            found.append(dt)

    for m in _MONTH_DAY.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        dt = date_from_parts(infer_year(month, day, now), month, day)
        if dt and (dt.year, dt.month, dt.day) not in seen:
            seen.add((dt.year, dt.month, dt.day))
            found.append(dt)

    for m in _SHORT_DOTTED.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        # 排除 "1.25" 这种被 _DOTTED 已覆盖的四位年份场景，只接受合理的月日
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        dt = date_from_parts(infer_year(month, day, now), month, day)
        if dt and (dt.year, dt.month, dt.day) not in seen:
            seen.add((dt.year, dt.month, dt.day))
            found.append(dt)

    return found


def human_kst(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    kst = to_kst(dt)
    weekdays = "一二三四五六日"
    return f"{kst:%Y-%m-%d}（周{weekdays[kst.weekday()]}）{kst:%H:%M} KST"


def human_date(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    kst = to_kst(dt)
    weekdays = "一二三四五六日"
    return f"{kst:%Y-%m-%d}（周{weekdays[kst.weekday()]}）"


def human_cst(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    cst = dt.astimezone(CST)
    return f"{cst:%Y-%m-%d %H:%M} 北京时间"


def days_since(dt: datetime | None, now: datetime | None = None) -> float:
    if dt is None:
        return float("inf")
    now = now or now_utc()
    return (now - dt).total_seconds() / 86400.0


def hours_since(dt: datetime | None, now: datetime | None = None) -> float:
    if dt is None:
        return float("inf")
    now = now or now_utc()
    return (now - dt).total_seconds() / 3600.0


def target_date_from_str(value: str) -> datetime:
    dt = parse_dt(value)
    if dt is None:
        raise ValueError(f"无法解析日期: {value!r}")
    return dt


def next_digest_at(last_digest: datetime | None, every_days: int, hour_kst: int) -> datetime:
    base = last_digest or now_utc()
    kst = to_kst(base) + timedelta(days=every_days)
    kst = kst.replace(hour=hour_kst, minute=0, second=0, microsecond=0)
    return kst.astimezone(UTC)


def at_hour_kst(dt: datetime, hour_kst: int) -> datetime:
    kst = to_kst(dt).replace(hour=hour_kst, minute=0, second=0, microsecond=0)
    return kst.astimezone(UTC)


def start_of_day_kst(dt: datetime) -> datetime:
    kst = to_kst(dt)
    return datetime.combine(kst.date(), time(0, 0), tzinfo=KST)
