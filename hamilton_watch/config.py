"""配置加载。

优先级：环境变量 > config.toml > 内置默认值。
敏感信息（邮箱授权码、API key）只应放在环境变量 / GitHub Secrets 里。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .timeutil import target_date_from_str


@dataclass
class ShowConfig:
    title: str = "汉密尔顿（Hamilton）韩国首尔"
    target_date: str = "2027-01-25"
    venue: str = "首尔 世宗文化会馆 大剧场（세종문화회관 대극장）"
    run_period: str = "2027-01-21 ~ 2027-03-20"
    note: str = ""

    @property
    def target_date_dt(self):
        return target_date_from_str(self.target_date)


@dataclass
class MonitorConfig:
    digest_every_days: int = 5
    digest_hour_kst: int = 10
    re_alert_hours: int = 24
    announced_alert_hours: int = 24
    startup_email: bool = True
    state_file: str = "state.json"
    max_items_per_email: int = 8
    failure_alert_after_hours: int = 6
    failure_alert_cooldown_hours: int = 24
    news_window_days: int = 45


@dataclass
class NotifyConfig:
    provider: str = "smtp"  # smtp | resend | formsubmit | none
    to: str = ""
    from_name: str = "汉密尔顿首尔票务监控"
    username: str = ""
    password: str = ""
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_ssl: bool = True
    resend_from: str = "onboarding@resend.dev"
    resend_api_key: str = ""

    @property
    def from_address(self) -> str:
        return self.username or self.to


@dataclass
class SourceConfig:
    type: str
    name: str = ""
    enabled: bool = True
    options: dict = field(default_factory=dict)


@dataclass
class Config:
    show: ShowConfig
    monitor: MonitorConfig
    notify: NotifyConfig
    sources: list[SourceConfig]
    base_dir: Path
    state_path: Path

    def public_summary(self) -> dict:
        return {
            "show": self.show.title,
            "target_date": self.show.target_date,
            "digest_every_days": self.monitor.digest_every_days,
            "provider": self.notify.provider,
            "to": _mask_email(self.notify.to),
            "sources": [s.name or s.type for s in self.sources if s.enabled],
        }


def _mask_email(value: str) -> str:
    if "@" not in value:
        return value
    name, domain = value.split("@", 1)
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    value = value.strip() if isinstance(value, str) else ""
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | Path = "config.toml") -> Config:
    path = Path(path)
    raw: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)

    show_raw = raw.get("show", {})
    monitor_raw = raw.get("monitor", {})
    notify_raw = raw.get("notify", {})

    show = ShowConfig(
        title=show_raw.get("title", ShowConfig.title),
        target_date=str(show_raw.get("target_date", ShowConfig.target_date)),
        venue=show_raw.get("venue", ShowConfig.venue),
        run_period=show_raw.get("run_period", ShowConfig.run_period),
        note=show_raw.get("note", ""),
    )

    monitor = MonitorConfig(
        digest_every_days=int(monitor_raw.get("digest_every_days", 5)),
        digest_hour_kst=int(monitor_raw.get("digest_hour_kst", 10)),
        re_alert_hours=int(monitor_raw.get("re_alert_hours", 24)),
        announced_alert_hours=int(monitor_raw.get("announced_alert_hours", 24)),
        startup_email=bool(monitor_raw.get("startup_email", True)),
        state_file=str(monitor_raw.get("state_file", "state.json")),
        max_items_per_email=int(monitor_raw.get("max_items_per_email", 8)),
        failure_alert_after_hours=int(monitor_raw.get("failure_alert_after_hours", 6)),
        failure_alert_cooldown_hours=int(monitor_raw.get("failure_alert_cooldown_hours", 24)),
        news_window_days=int(monitor_raw.get("news_window_days", 45)),
    )

    notify = NotifyConfig(
        provider=_env("MAIL_PROVIDER", str(notify_raw.get("provider", "smtp"))).lower(),
        to=_env("MAIL_TO", str(notify_raw.get("to", ""))),
        from_name=_env("MAIL_FROM_NAME", str(notify_raw.get("from_name", NotifyConfig.from_name))),
        username=_env("MAIL_USERNAME", str(notify_raw.get("username", ""))),
        password=_env("MAIL_PASSWORD", str(notify_raw.get("password", ""))),
        smtp_host=_env("SMTP_HOST", str(notify_raw.get("smtp_host", "smtp.163.com"))),
        smtp_port=int(_env("SMTP_PORT", str(notify_raw.get("smtp_port", 465)))),
        smtp_ssl=_env_bool("SMTP_SSL", bool(notify_raw.get("smtp_ssl", True))),
        resend_from=_env("RESEND_FROM", str(notify_raw.get("resend_from", "onboarding@resend.dev"))),
        resend_api_key=_env("RESEND_API_KEY", str(notify_raw.get("resend_api_key", ""))),
    )

    sources: list[SourceConfig] = []
    for entry in raw.get("source", []):
        if not isinstance(entry, dict) or "type" not in entry:
            continue
        options = {k: v for k, v in entry.items() if k not in {"type", "name", "enabled"}}
        sources.append(
            SourceConfig(
                type=str(entry["type"]),
                name=str(entry.get("name") or entry["type"]),
                enabled=bool(entry.get("enabled", True)),
                options=options,
            )
        )

    if not sources:
        sources = default_sources(monitor.news_window_days)

    base_dir = path.resolve().parent
    state_path = Path(_env("STATE_FILE", monitor.state_file))
    if not state_path.is_absolute():
        state_path = base_dir / state_path

    return Config(show=show, monitor=monitor, notify=notify, sources=sources, base_dir=base_dir, state_path=state_path)


def default_sources(window_days: int = 45) -> list[SourceConfig]:
    return [
        SourceConfig(
            type="sejong_board",
            name="sejong",
            options={
                "board_url": "https://www.sejongpac.or.kr/portal/bbs/B0000049/list.do?menuNo=200440",
                "extra_board_urls": [],
            },
        ),
        SourceConfig(
            type="google_news",
            name="gnews",
            options={
                "queries": ["해밀턴 티켓", "해밀턴 예매", "해밀턴 내한"],
                "window_days": window_days,
            },
        ),
        SourceConfig(
            type="naver_search",
            name="naver",
            options={
                "queries": ["해밀턴 예매", "해밀턴 티켓 오픈"],
                "limit": 20,
            },
        ),
        SourceConfig(
            type="page",
            name="interpark_goods",
            enabled=False,
            options={
                "urls": ["https://tickets.interpark.com/goods/00000000"],
                "require_keyword": "해밀턴",
                "open_markers": ["예매하기", "예매중", "BOOK NOW", "좌석 선택"],
                "pre_markers": ["예매 오픈 예정", "오픈 예정", "준비중"],
            },
        ),
    ]
