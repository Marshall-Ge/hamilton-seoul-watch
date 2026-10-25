"""监控状态持久化。

state.json 会随仓库提交，保证 GitHub Actions 每次运行都能记住：
上次发邮件时间、上次成功时间、已见过的链接、连续失败次数等。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .timeutil import iso, parse_dt

MAX_SEEN = 400


@dataclass
class State:
    version: int = 1
    created_at: datetime | None = None
    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    last_status: str = "unknown"
    status_since: datetime | None = None
    last_digest_at: datetime | None = None
    last_alert_at: dict[str, datetime | None] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {"runs": 0, "digests": 0, "alerts": 0, "failures": 0})
    seen: dict[str, str] = field(default_factory=dict)
    last_snapshot: dict[str, Any] = field(default_factory=dict)
    consecutive_failures: int = 0

    # ---------- 便捷方法 ----------
    def last_alert(self, kind: str) -> datetime | None:
        return self.last_alert_at.get(kind)

    def mark_alert(self, kind: str, when: datetime) -> None:
        self.last_alert_at[kind] = when
        self.stats["alerts"] = self.stats.get("alerts", 0) + 1

    def remember(self, key: str, when: datetime) -> bool:
        """记录一个链接，返回是否是第一次见到。"""
        if key in self.seen:
            return False
        self.seen[key] = iso(when) or ""
        if len(self.seen) > MAX_SEEN:
            # 删除最早的一批
            items = sorted(self.seen.items(), key=lambda kv: kv[1])
            self.seen = dict(items[-MAX_SEEN:])
        return True

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": iso(self.created_at),
            "last_check_at": iso(self.last_check_at),
            "last_success_at": iso(self.last_success_at),
            "last_status": self.last_status,
            "status_since": iso(self.status_since),
            "last_digest_at": iso(self.last_digest_at),
            "last_alert_at": {k: iso(v) for k, v in self.last_alert_at.items()},
            "stats": self.stats,
            "seen": self.seen,
            "last_snapshot": self.last_snapshot,
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "State":
        state = cls()
        state.version = int(raw.get("version", 1))
        state.created_at = parse_dt(raw.get("created_at"))
        state.last_check_at = parse_dt(raw.get("last_check_at"))
        state.last_success_at = parse_dt(raw.get("last_success_at"))
        state.last_status = str(raw.get("last_status", "unknown"))
        state.status_since = parse_dt(raw.get("status_since"))
        state.last_digest_at = parse_dt(raw.get("last_digest_at"))
        state.last_alert_at = {k: parse_dt(v) for k, v in (raw.get("last_alert_at") or {}).items()}
        state.stats = {k: int(v) for k, v in (raw.get("stats") or {}).items()}
        state.stats.setdefault("runs", 0)
        state.stats.setdefault("digests", 0)
        state.stats.setdefault("alerts", 0)
        state.stats.setdefault("failures", 0)
        state.seen = dict(raw.get("seen") or {})
        state.last_snapshot = dict(raw.get("last_snapshot") or {})
        state.consecutive_failures = int(raw.get("consecutive_failures", 0))
        return state

    def is_new(self) -> bool:
        return self.created_at is None


def load_state(path: Path) -> tuple[State, bool]:
    if not path.exists():
        return State(), True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return State.from_dict(raw), False
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return State(), True


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
