"""主流程：抓取 → 判定 → 决定发什么邮件 → 更新状态。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from .config import Config, load_config
from .detect import assess
from .fetch import Fetcher
from .models import Assessment
from .notify import NotifyError, send_email
from .report import render_alert, render_digest, render_health, render_test
from .sources import build_source
from .state import State, load_state, save_state
from .timeutil import days_since, hours_since, human_kst, now_utc, to_kst

log = logging.getLogger("hamilton_watch")


def collect(cfg: Config) -> tuple[list, list]:
    fetcher = Fetcher()
    sources = []
    items = []
    for source_cfg in cfg.sources:
        if not source_cfg.enabled:
            continue
        try:
            source = build_source(source_cfg, fetcher, cfg.show)
        except Exception as exc:  # noqa: BLE001
            log.exception("无法初始化信息源 %s", source_cfg.type)
            from .models import SourceResult

            sources.append(SourceResult(name=source_cfg.name, kind=source_cfg.type, ok=False, error=str(exc)))
            continue

        try:
            result = source.collect()
        except Exception as exc:  # noqa: BLE001
            log.exception("信息源 %s 抓取异常", source.name)
            from .models import SourceResult

            result = SourceResult(name=source.name, kind=source.kind, ok=False, error=str(exc))
        sources.append(result)
        items.extend(result.items)
        log.info(
            "source=%s ok=%s items=%d%s",
            result.name,
            result.ok,
            result.count,
            "" if result.ok else f" error={result.error[:160]}",
        )
    return items, sources


def _status_changed(state: State, status: str) -> bool:
    return state.last_status != status


def _digest_due(cfg: Config, state: State, status: str, now: datetime, *, is_new: bool) -> bool:
    if status == "on_sale":
        return False
    every_days = cfg.monitor.digest_every_days
    if is_new:
        return cfg.monitor.startup_email
    last = state.last_digest_at
    if last is None:
        due = True
    else:
        due = days_since(last, now) >= every_days
    if not due:
        return False
    kst = to_kst(now)
    return kst.hour >= cfg.monitor.digest_hour_kst


def decide(
    cfg: Config, state: State, assessment: Assessment, now: datetime, *, is_new: bool, force_alert: bool
) -> list[dict]:
    status = assessment.status
    actions: list[dict] = []

    if status == "on_sale":
        last_alert = state.last_alert("on_sale")
        if force_alert or last_alert is None or hours_since(last_alert, now) >= cfg.monitor.re_alert_hours:
            actions.append({"kind": "alert", "level": "on_sale"})
        return actions

    if status == "announced":
        last_alert = state.last_alert("announced")
        changed = _status_changed(state, "announced")
        if force_alert or (
            changed
            and (last_alert is None or hours_since(last_alert, now) >= cfg.monitor.announced_alert_hours)
        ):
            actions.append({"kind": "alert", "level": "announced"})
        elif _digest_due(cfg, state, status, now, is_new=is_new):
            actions.append({"kind": "digest"})
        return actions

    if _digest_due(cfg, state, status, now, is_new=is_new):
        actions.append({"kind": "digest"})
    return actions


def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    now = now_utc()
    kst = to_kst(now)

    if args.mode == "status":
        return _print_status(cfg)

    if args.mode == "test-email":
        subject, text, html = render_test(cfg, now)
        if args.dry_run:
            print(subject)
            print(text)
            return 0
        try:
            provider = send_email(cfg.notify, subject, text, html)
        except NotifyError as exc:
            log.error("测试邮件发送失败: %s", exc)
            return 1
        print(f"✅ 测试邮件已通过 {provider} 发送到 {cfg.notify.to}")
        return 0

    if not any(source.enabled for source in cfg.sources):
        log.error("config.toml 里没有启用任何信息源，监控无法工作")
        return 1

    state, is_new = load_state(cfg.state_path)
    if is_new:
        state.created_at = now
        state.last_status = "unknown"
        state.status_since = now

    state.stats["runs"] = state.stats.get("runs", 0) + 1

    log.info(
        "run mode=%s dry_run=%s now=%s state_new=%s",
        args.mode,
        args.dry_run,
        kst.strftime("%Y-%m-%d %H:%M KST"),
        is_new,
    )

    items, sources = collect(cfg)
    assessment = assess(items, sources, cfg, now)
    ok = assessment.ok_sources
    log.info("assessment status=%s items=%d ok_sources=%d", assessment.status, len(items), ok)

    for item in items:
        state.remember(item.key(), now)

    # 所有源都失败 → 健康告警（优先于普通进度邮件）
    all_failed = ok == 0 and bool(sources)
    if all_failed:
        state.consecutive_failures += 1
        state.stats["failures"] = state.stats.get("failures", 0) + 1
        last_health = state.last_alert("health")
        stale = state.last_success_at is None or hours_since(state.last_success_at, now) >= cfg.monitor.failure_alert_after_hours
        cooldown_ok = last_health is None or hours_since(last_health, now) >= cfg.monitor.failure_alert_cooldown_hours
        if stale and cooldown_ok:
            subject, text, html = render_health(cfg, assessment, state, now)
            if not args.dry_run:
                try:
                    provider = send_email(cfg.notify, subject, text, html)
                    state.mark_alert("health", now)
                    log.info("health alert sent via %s", provider)
                except NotifyError as exc:
                    log.error("健康告警发送失败: %s", exc)
                    return 1
            else:
                _print_preview(subject, text)
            _save(cfg, state, now, force=True, persist=not args.dry_run)
            return 0
        log.warning("所有信息源均失败（连续 %d 次），未到告警阈值或仍在冷却期", state.consecutive_failures)
        _save(cfg, state, now, force=False, persist=not args.dry_run)
        return 0

    if ok > 0:
        state.last_success_at = now
        state.consecutive_failures = 0

    actions = decide(cfg, state, assessment, now, is_new=is_new, force_alert=args.force_alert)
    if (
        args.mode == "digest"
        and assessment.status != "on_sale"
        and not any(a["kind"] == "digest" for a in actions)
    ):
        actions.append({"kind": "digest"})

    sent_any = False
    for action in actions:
        if action["kind"] == "alert":
            subject, text, html = render_alert(cfg, assessment, state, now)
            kind = "on_sale" if assessment.status == "on_sale" else "announced"
        else:
            subject, text, html = render_digest(
                cfg, assessment, state, now, startup=is_new and state.last_digest_at is None
            )
            kind = "digest"

        if args.dry_run:
            _print_preview(subject, text)
            sent_any = True
        else:
            try:
                provider = send_email(cfg.notify, subject, text, html)
            except NotifyError as exc:
                log.error("邮件发送失败: %s", exc)
                _save(cfg, state, now, force=True, persist=not args.dry_run)
                return 1
            log.info("sent kind=%s via=%s subject=%s", kind, provider, subject)
            sent_any = True

        if kind == "digest":
            state.last_digest_at = now
            state.stats["digests"] = state.stats.get("digests", 0) + 1
        else:
            state.mark_alert(kind, now)
            state.last_digest_at = now  # 告警邮件本身已包含当前状态，避免紧接着再发进度邮件

    if _status_changed(state, assessment.status):
        state.last_status = assessment.status
        state.status_since = now

    if not sent_any:
        log.info("no email needed (digest not due / alert already sent)")

    state.last_snapshot = {
        "status": assessment.status,
        "top_titles": [s.item.title for s in assessment.top_signals(limit=5)],
        "checked_at": now.isoformat(),
    }

    _save(cfg, state, now, force=sent_any or is_new, persist=not args.dry_run)
    return 0


def _save(cfg: Config, state: State, now: datetime, *, force: bool, persist: bool) -> None:
    """按需写 state.json：有邮件/状态变化立即写，否则最多 6 小时写一次。"""
    checkpoint_due = state.last_check_at is None or hours_since(state.last_check_at, now) >= 6
    state.last_check_at = now
    if not persist:
        return
    if force or checkpoint_due:
        save_state(cfg.state_path, state)
        log.info("state saved -> %s", cfg.state_path)


def _print_preview(subject: str, text: str) -> None:
    print("=" * 72)
    print("DRY-RUN 邮件预览")
    print("Subject:", subject)
    print("-" * 72)
    print(text)
    print("=" * 72)


def _print_status(cfg: Config) -> int:
    state, is_new = load_state(cfg.state_path)
    print(f"配置: {cfg.public_summary()}")
    if is_new:
        print("状态文件不存在：尚未运行过")
        return 0
    print(f"创建时间: {human_kst(state.created_at)}")
    print(f"上次检查: {human_kst(state.last_check_at)}")
    print(f"上次成功: {human_kst(state.last_success_at)}")
    print(f"当前状态: {state.last_status}（自 {human_kst(state.status_since)}）")
    print(f"上次进度邮件: {human_kst(state.last_digest_at)}")
    print(f"累计运行: {state.stats.get('runs', 0)} 次，邮件: {state.stats.get('digests', 0)} 封进度 / {state.stats.get('alerts', 0)} 封告警")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hamilton-watch", description="汉密尔顿首尔票务监控")
    parser.add_argument(
        "mode",
        nargs="?",
        default="check",
        choices=["check", "digest", "test-email", "status"],
        help="check=正常检查；digest=强制发进度邮件；test-email=测试邮件通道；status=查看状态",
    )
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印，不发邮件、不写状态")
    parser.add_argument("--force-alert", action="store_true", help="忽略冷却时间强制发告警")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出调试日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        log.exception("运行失败")
        return 1
