"""只读探针 48：数一次「城市输入」触发了几次联想请求。

为什么要有这个数：方案里要求「减少无效请求，别在清空 / 反复退格时放大联想接口」。
项目现在的输入路径是 `fill("")` 一次清空 + 逐字符输入、**没有退格**，
所以理论上请求数 = 城市字数。这个探针把「理论上」变成实测值。

只输入，不提交搜索。
用法：HEADED=1 python tools/probes/probe_suggest_requests.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.hotel_search_page import HotelSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_suggest_requests" / datetime.now().strftime("%Y%m%d-%H%M%S")
CITY = "上海"  # 两个字，便于看清「每字一次」
API_HINT = "gaHotelSearchEngine"


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    calls: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            no_viewport=True,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            page.on("request", lambda r: calls.append(r.url) if API_HINT in r.url else None)

            hotel = HotelSearchPage(page)
            hotel.open()
            calls.clear()
            log(f"输入 {CITY}（含一次 fill('') 清空）")
            hotel.set_destination(CITY)
            total = sum(1 for u in calls if API_HINT in u)
            log(f"联想请求 {total} 次")
            for u in calls:
                if API_HINT in u:
                    log(f"  {u[:110]}")
            facts = {"city": CITY, "suggest_requests": total, "urls": [u[:120] for u in calls]}
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT / 'facts.json'}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
