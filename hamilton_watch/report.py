"""邮件内容渲染（纯文本 + HTML）。"""

from __future__ import annotations

import html as html_lib
from datetime import datetime

from .config import Config
from .models import Assessment, Signal
from .state import State
from .timeutil import days_since, human_cst, human_date, human_kst, next_digest_at, to_kst

LINK_SEJONG = "https://www.sejongpac.or.kr/portal/bbs/B0000049/list.do?menuNo=200440"
LINK_INTERPARK = "https://tickets.interpark.com/search?keyword=%ED%95%B4%EB%B0%80%ED%84%B4"
LINK_MELON = "https://ticket.melon.com/search/index.htm?keyword=%ED%95%B4%EB%B0%80%ED%84%B4"
LINK_NAVER = "https://search.naver.com/search.naver?query=%ED%95%B4%EB%B0%80%ED%84%B4+%EC%98%88%EB%A7%A4"
LINK_GNEWS = "https://news.google.com/search?q=%ED%95%B4%EB%B0%80%ED%84%B4+%ED%8B%B0%EC%BC%93&hl=ko&gl=KR&ceid=KR:ko"

STATUS_LABEL = {
    "on_sale": "已开票",
    "announced": "已公布开票时间（尚未开票）",
    "unknown": "尚未开票",
}

STATUS_COLOR = {"on_sale": "#c0392b", "announced": "#b9770e", "unknown": "#2c3e50"}


def _e(value: str) -> str:
    return html_lib.escape(value or "", quote=True)


def _days_text(state: State, now: datetime) -> str:
    if not state.created_at:
        return "0"
    return f"{max(0, int(days_since(state.created_at, now)))}"


def _signal_line(signal: Signal) -> str:
    item = signal.item
    meta = []
    if item.published:
        meta.append(f"{to_kst(item.published):%Y-%m-%d}")
    if item.publisher:
        meta.append(item.publisher)
    meta_text = " · ".join(meta)
    return f"[{signal.level}] {item.title}" + (f"（{meta_text}）" if meta_text else "")


def _evidence_text(assessment: Assessment, limit: int) -> list[str]:
    lines: list[str] = []
    for signal in assessment.top_signals(limit=limit):
        item = signal.item
        date = f"{to_kst(item.published):%m-%d}" if item.published else "--"
        lines.append(f"- {date}｜{signal.reason}｜{item.title}\n  {item.url}")
    return lines


def _sources_text(assessment: Assessment) -> list[str]:
    lines = []
    for source in assessment.sources:
        if source.ok:
            lines.append(f"- ✅ {source.name}: {source.count} 条")
        else:
            lines.append(f"- ⚠️ {source.name}: 失败（{source.error[:120]}）")
    return lines


# --------------------------------------------------------------------------- #
# 告警邮件（已开票 / 公布开票时间）
# --------------------------------------------------------------------------- #
def render_alert(cfg: Config, assessment: Assessment, state: State, now: datetime) -> tuple[str, str, str]:
    status = assessment.status
    target = cfg.show.target_date_dt
    when = human_date(target)
    open_at = assessment.best_open_at()

    if status == "on_sale":
        subject = f"【已开票】汉密尔顿 首尔 {target:%m/%d}｜立即抢票"
        headline = "开票了！"
        intro = (
            f"监测到《汉密尔顿》首尔站（目标场次 {when}）已开票。"
            "请立刻打开下方链接选座购票。"
        )
    elif open_at is not None:
        subject = f"【开票时间公布】汉密尔顿 首尔 {target:%m/%d}"
        headline = "开票时间已公布"
        intro = (
            f"官方/新闻已公布《汉密尔顿》首尔站的开票时间："
            f"{human_kst(open_at)}（{human_cst(open_at)}）。"
            "现在还不能买，但请提前注册好票务账号、准备好支付方式。"
        )
    else:
        subject = f"【开票信号】汉密尔顿 首尔 {target:%m/%d}｜请确认"
        headline = "发现开票相关信号"
        intro = (
            "监测到官方公告或票务网站出现了《汉密尔顿》首尔站的开票动作，"
            "但未能解析出确定的开票时间。请点击下方链接人工确认是否已经开票。"
        )

    evidence = _evidence_text(assessment, cfg.monitor.max_items_per_email)
    sources = _sources_text(assessment)

    text = "\n".join(
        [
            headline,
            "",
            intro,
            "",
            f"目标：{cfg.show.title}",
            f"场次：{when}",
            f"场馆：{cfg.show.venue}",
            f"演出档期：{cfg.show.run_period}",
            "",
            "证据：",
            *(evidence or ["- 无"]),
            "",
            "购票/查询链接：",
            f"- 世宗文化会馆开票公告：{LINK_SEJONG}",
            f"- Interpark 搜索：{LINK_INTERPARK}",
            f"- Melon Ticket 搜索：{LINK_MELON}",
            f"- Naver 搜索：{LINK_NAVER}",
            "",
            "监控源状态：",
            *sources,
            "",
            f"检查时间：{human_kst(now)}（{human_cst(now)}）",
            "本邮件由云端定时任务自动发送，不需要你的电脑开机。",
        ]
    )

    html = _wrap_html(
        cfg=cfg,
        status=status,
        headline=headline,
        intro=intro,
        assessment=assessment,
        state=state,
        now=now,
        extra_block=_alert_extra_block(status, open_at),
        footer_note="本邮件由云端定时任务自动发送，不依赖你的电脑或本地网络。",
    )
    return subject, text, html


# --------------------------------------------------------------------------- #
# 5 天进度邮件
# --------------------------------------------------------------------------- #
def render_digest(
    cfg: Config, assessment: Assessment, state: State, now: datetime, *, startup: bool = False
) -> tuple[str, str, str]:
    target = cfg.show.target_date_dt
    status = assessment.status
    day_no = _days_text(state, now)
    next_digest = next_digest_at(now, cfg.monitor.digest_every_days, cfg.monitor.digest_hour_kst)

    if startup:
        subject = f"【监控已启动】汉密尔顿 首尔 {target:%m/%d} 票务监控"
        headline = "监控已启动"
        intro = (
            "云端监控已经跑起来了。接下来每 5–10 分钟检查一次韩国票务/公告源，"
            "一旦开票会立刻单独发邮件；如果一直没开票，每 5 天给你发一次进度。"
        )
    elif status == "announced":
        subject = f"【进度】汉密尔顿 首尔 {target:%m/%d}：已公布开票时间，尚未开票"
        headline = "已公布开票时间，尚未开票"
        intro = "官方已公布开票时间，但还没到开票时刻。下面是目前掌握的情况。"
    else:
        subject = f"【进度】汉密尔顿 首尔 {target:%m/%d}：尚未开票（第 {day_no} 天）"
        headline = "尚未开票"
        intro = (
            f"截至 {human_cst(now)}，没有发现《汉密尔顿》首尔站开票的确定消息。"
            "监控仍在正常运行，开票后会立即单独通知你。"
        )

    open_at = assessment.best_open_at()
    evidence = _evidence_text(assessment, cfg.monitor.max_items_per_email)
    sources = _sources_text(assessment)

    text = "\n".join(
        [
            headline,
            "",
            intro,
            "",
            f"目标：{cfg.show.title}",
            f"场次：{human_date(target)}",
            f"场馆：{cfg.show.venue}",
            f"演出档期：{cfg.show.run_period}",
            f"开票时间：{human_kst(open_at) if open_at else '尚未公布'}",
            "",
            "最近的相关消息：",
            *(evidence or ["- 暂无新的相关消息"]),
            "",
            "监控源状态：",
            *sources,
            "",
            f"已连续监控：{day_no} 天（自 {human_date(state.created_at)} 起）",
            f"本次检查：{human_kst(now)}（{human_cst(now)}）",
            f"下次进度邮件：{human_cst(next_digest)} 左右",
            "",
            "查询链接：",
            f"- 世宗文化会馆开票公告：{LINK_SEJONG}",
            f"- Interpark 搜索：{LINK_INTERPARK}",
            f"- Melon Ticket 搜索：{LINK_MELON}",
            f"- Naver 搜索：{LINK_NAVER}",
        ]
    )

    html = _wrap_html(
        cfg=cfg,
        status=status,
        headline=headline,
        intro=intro,
        assessment=assessment,
        state=state,
        now=now,
        extra_block=_digest_extra_block(cfg, state, now, day_no, next_digest, open_at),
        footer_note="本邮件由云端定时任务自动发送；如果不再需要，直接在 GitHub 里停用 workflow 即可。",
    )
    return subject, text, html


# --------------------------------------------------------------------------- #
# 健康告警
# --------------------------------------------------------------------------- #
def render_health(cfg: Config, assessment: Assessment, state: State, now: datetime) -> tuple[str, str, str]:
    subject = "【监控异常】汉密尔顿首尔票务监控连续抓取失败"
    failed = "、".join(s.name for s in assessment.failed_sources) or "全部信息源"
    intro = (
        f"最近一次成功检查是 {human_kst(state.last_success_at)}，"
        f"当前失败的信息源：{failed}。监控可能已经「瞎了」，请检查 GitHub Actions 日志。"
    )
    text = "\n".join(
        [
            "监控异常",
            "",
            intro,
            "",
            "各信息源状态：",
            *_sources_text(assessment),
            "",
            f"本次检查：{human_kst(now)}",
        ]
    )
    html = _wrap_html(
        cfg=cfg,
        status="unknown",
        headline="监控异常",
        intro=intro,
        assessment=assessment,
        state=state,
        now=now,
        extra_block="",
        footer_note="请到 GitHub 仓库的 Actions 页面查看运行日志。",
    )
    return subject, text, html


def render_test(cfg: Config, now: datetime) -> tuple[str, str, str]:
    subject = "【测试】汉密尔顿首尔票务监控邮件通道正常"
    intro = "这是一封测试邮件。收到它说明邮件通道配置正确，监控结果可以正常送达。"
    text = "\n".join(
        [
            "邮件通道测试",
            "",
            intro,
            "",
            f"收件人：{cfg.notify.to}",
            f"发送通道：{cfg.notify.provider}",
            f"发送时间：{human_cst(now)}",
        ]
    )
    html = f"""<!doctype html><html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f5f7;padding:24px">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;padding:28px">
<h2 style="margin:0 0 12px;color:#1f2937">邮件通道测试成功 ✅</h2>
<p style="color:#374151;line-height:1.7">{_e(intro)}</p>
<table style="font-size:14px;color:#4b5563;line-height:1.8">
<tr><td>收件人</td><td>{_e(cfg.notify.to)}</td></tr>
<tr><td>发送通道</td><td>{_e(cfg.notify.provider)}</td></tr>
<tr><td>发送时间</td><td>{_e(human_cst(now))}</td></tr>
</table>
</div></body></html>"""
    return subject, text, html


# --------------------------------------------------------------------------- #
# HTML 组件
# --------------------------------------------------------------------------- #
def _alert_extra_block(status: str, open_at: datetime | None) -> str:
    if status == "on_sale":
        return (
            '<div style="background:#fdecea;border-left:4px solid #c0392b;padding:14px 16px;'
            'border-radius:6px;margin:18px 0;color:#7f1d1d;font-size:14px;line-height:1.7">'
            "<b>现在就去买。</b>热门场次（尤其周末/晚场）通常几分钟内售罄；"
            "建议同时打开 Interpark 和 Melon 两个窗口，并提前登录。</div>"
        )
    if status == "announced" and open_at is not None:
        return (
            '<div style="background:#fef5e7;border-left:4px solid #b9770e;padding:14px 16px;'
            'border-radius:6px;margin:18px 0;color:#7d4e0d;font-size:14px;line-height:1.7">'
            f"<b>开票时间：{_e(human_kst(open_at))}</b><br>"
            f"{_e(human_cst(open_at))}。建议提前 10 分钟登录票务网站，并准备好支付方式。</div>"
        )
    return ""


def _digest_extra_block(
    cfg: Config,
    state: State,
    now: datetime,
    day_no: str,
    next_digest: datetime,
    open_at: datetime | None,
) -> str:
    rows = [
        ("已连续监控", f"{day_no} 天"),
        ("开始监控", human_date(state.created_at)),
        ("开票时间", human_kst(open_at) if open_at else "尚未公布"),
        ("下次进度邮件", human_cst(next_digest)),
    ]
    cells = "".join(
        f'<tr><td style="padding:7px 0;color:#6b7280;font-size:13px">{_e(k)}</td>'
        f'<td style="padding:7px 0;color:#111827;font-size:13px;text-align:right">{_e(v)}</td></tr>'
        for k, v in rows
    )
    return (
        '<table style="width:100%;border-collapse:collapse;margin:18px 0;'
        'border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb">'
        f"{cells}</table>"
    )


def _wrap_html(
    *,
    cfg: Config,
    status: str,
    headline: str,
    intro: str,
    assessment: Assessment,
    state: State,
    now: datetime,
    extra_block: str,
    footer_note: str,
) -> str:
    color = STATUS_COLOR.get(status, "#2c3e50")
    label = STATUS_LABEL.get(status, status)
    target = cfg.show.target_date_dt
    signals = assessment.top_signals(limit=cfg.monitor.max_items_per_email)

    cards = []
    for signal in signals:
        item = signal.item
        date_text = f"{to_kst(item.published):%Y-%m-%d}" if item.published else ""
        meta = " · ".join(x for x in [date_text, item.publisher] if x)
        cards.append(
            '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;margin:10px 0">'
            f'<div style="font-size:12px;color:{color};font-weight:600;margin-bottom:4px">{_e(signal.reason)}</div>'
            f'<a href="{_e(item.url)}" style="color:#1d4ed8;text-decoration:none;font-size:14px;'
            f'line-height:1.5;font-weight:600">{_e(item.title)}</a>'
            + (f'<div style="font-size:12px;color:#9ca3af;margin-top:4px">{_e(meta)}</div>' if meta else "")
            + "</div>"
        )
    if not cards:
        cards.append('<p style="color:#6b7280;font-size:14px">暂时没有新的相关消息。</p>')

    source_rows = "".join(
        f'<tr><td style="padding:4px 0;font-size:12px;color:{"#065f46" if s.ok else "#b91c1c"}">'
        f'{"✅" if s.ok else "⚠️"} {_e(s.name)}</td>'
        f'<td style="padding:4px 0;font-size:12px;color:#6b7280;text-align:right">'
        f'{s.count if s.ok else _e(s.error[:60])}</td></tr>'
        for s in assessment.sources
    )

    buttons = "".join(
        f'<a href="{_e(url)}" style="display:inline-block;margin:4px 6px 4px 0;padding:9px 14px;'
        f'background:#111827;color:#fff;text-decoration:none;border-radius:6px;font-size:13px">{_e(name)}</a>'
        for name, url in [
            ("世宗开票公告", LINK_SEJONG),
            ("Interpark", LINK_INTERPARK),
            ("Melon Ticket", LINK_MELON),
            ("Naver 搜索", LINK_NAVER),
        ]
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(headline)}</title></head>
<body style="margin:0;padding:24px 12px;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif">
<div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)">
  <div style="background:{color};padding:22px 26px">
    <div style="color:rgba(255,255,255,.85);font-size:12px;letter-spacing:1px">{_e(label)}</div>
    <div style="color:#fff;font-size:24px;font-weight:700;margin-top:6px">{_e(headline)}</div>
    <div style="color:rgba(255,255,255,.9);font-size:13px;margin-top:8px">{_e(cfg.show.title)}｜目标场次 {_e(human_date(target))}</div>
  </div>
  <div style="padding:24px 26px">
    <p style="margin:0 0 14px;color:#374151;font-size:14px;line-height:1.75">{_e(intro)}</p>
    {extra_block}
    <div style="margin:16px 0 8px;font-size:13px;font-weight:700;color:#111827">证据与最新消息</div>
    {''.join(cards)}
    <div style="margin:22px 0 8px;font-size:13px;font-weight:700;color:#111827">购票 / 查询入口</div>
    <div>{buttons}</div>
    <div style="margin:22px 0 8px;font-size:13px;font-weight:700;color:#111827">监控源状态</div>
    <table style="width:100%;border-collapse:collapse">{source_rows}</table>
    <div style="margin-top:20px;padding-top:14px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;line-height:1.7">
      场馆：{_e(cfg.show.venue)}<br>
      演出档期：{_e(cfg.show.run_period)}<br>
      检查时间：{_e(human_kst(now))}（{_e(human_cst(now))}）<br>
      {_e(footer_note)}
    </div>
  </div>
</div>
</body></html>"""
