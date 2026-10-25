"""生成 README 中的邮件效果预览图（需要本机安装 Chrome）。

用法：python docs/make_preview.py
产物：docs/alert_on_sale.png、docs/alert_announced.png、docs/digest.png
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hamilton_watch.config import NotifyConfig, load_config  # noqa: E402
from hamilton_watch.models import Assessment, Item, Signal, SourceResult  # noqa: E402
from hamilton_watch.report import render_alert, render_digest  # noqa: E402
from hamilton_watch.state import State  # noqa: E402
from hamilton_watch.timeutil import KST  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main() -> int:
    cfg = load_config(ROOT / "config.toml")
    cfg.notify = NotifyConfig(provider="none", to="marshall_gefexiang@163.com")
    now = datetime(2026, 12, 3, 10, 0, tzinfo=KST)

    board = Item(
        source="sejong",
        title="뮤지컬 <해밀턴> 티켓 오픈 안내",
        url="https://www.sejongpac.or.kr/portal/bbs/B0000049/view.do?nttId=50300&menuNo=200440",
        published=now - timedelta(days=1),
        open_at=now + timedelta(days=2, hours=4),
        kind="board",
        publisher="世宗文化会馆",
    )
    news = Item(
        source="gnews",
        title="뮤지컬 '해밀턴' 서울 공연 12월 5일 티켓 오픈 - 연합뉴스",
        url="https://news.google.com/",
        published=now - timedelta(hours=5),
        kind="news",
        publisher="연합뉴스",
    )

    def assessment(status: str) -> Assessment:
        if status == "on_sale":
            first = Signal(
                level="on_sale",
                item=board,
                reason="官方公告的开票时间已到（12-05 14:00 KST）",
                open_at=now - timedelta(days=1),
            )
        else:
            first = Signal(
                level="announced",
                item=board,
                reason="官方公告了开票时间（12-05 14:00 KST）",
                open_at=now + timedelta(days=2, hours=4),
            )
        second = Signal(level="info", item=news, reason="仅提及演出，未发现开票动作")
        return Assessment(
            status=status,
            signals=[first, second],
            items=[board, news],
            sources=[
                SourceResult("sejong", "sejong_board", True, [board]),
                SourceResult("gnews", "google_news", True, [news]),
            ],
        )

    state = State(
        created_at=now - timedelta(days=12),
        last_digest_at=now - timedelta(days=5),
        stats={"runs": 320, "digests": 2, "alerts": 0, "failures": 0},
    )

    outputs = []
    for name, status, render in [
        ("alert_on_sale", "on_sale", lambda a: render_alert(cfg, a, state, now)),
        ("alert_announced", "announced", lambda a: render_alert(cfg, a, state, now)),
        ("digest", "announced", lambda a: render_digest(cfg, a, state, now, startup=False)),
    ]:
        _, _, html = render(assessment(status))
        html_path = ROOT / "docs" / f"{name}.html"
        html_path.write_text(html, encoding="utf-8")
        subprocess.run(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--window-size=760,1400",
                f"--screenshot={ROOT / 'docs' / (name + '.png')}",
                f"file://{html_path}",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        outputs.append(name)
    print("generated:", ", ".join(outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
