"""只读探针 49：复现「共享 page 下火车票选日不生效」。

现象（2026-11-07，e2e 共享会话）：
    连续三次都栽在火车票选日期，且**单跑不复现**：
      11-15 → 落成 11-17；11-22 → 落成 11-10（该格显示「预约」）；11-14 → 落成 11-17。
    点击目标本身是对的（日志里点的是 `14`），说明不是「点错格子」。

本探针按 e2e 的顺序复刻：先开酒店首页（模拟前一个模块），再开火车票首页，
选好城市后点日期，并在四个时刻读 `#label-departDate` 的值：
    A 打开日历前   B 点完日期格立刻   C 1 秒后   D 关掉日历后
每步截图，用来判断「是没点进去」还是「点进去又被改回去」。

不提交搜索。
用法：HEADED=1 python tools/probes/probe_train_date_shared.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.train_search_page import TrainSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_train_date_shared" / datetime.now().strftime("%Y%m%d-%H%M%S")
TARGET = (date.today() + timedelta(days=7)).isoformat()


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {"target": TARGET}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            no_viewport=True,
            storage_state=str(settings.AUTH_STATE_FILE),
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            train = TrainSearchPage(page)

            # 复刻 e2e：先经过酒店首页（同一个 tab），再开火车票
            page.goto("https://www.ctrip.com", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            facts["hotel_title"] = page.title()[:40]
            log(f"先到酒店首页：{facts['hotel_title']!r}")

            train.open()
            train.select_city("depart", "上海")
            train.select_city("arrive", "北京")
            box = page.locator(TrainSearchPage.DATE_INPUT["depart"])

            facts["A_before_open"] = box.input_value().strip()
            log(f"A 打开日历前 input={facts['A_before_open']!r}")

            # 走页面对象自己的路径开日历（与 e2e 一致），然后手工点日
            block = train._date_block("depart")
            train.human.click(block)
            page.locator(f"{TrainSearchPage.CALENDAR['depart']} "
                         f"{TrainSearchPage.CALENDAR_READY}").first.wait_for(state="visible")
            page.screenshot(path=str(OUT / "01_calendar_open.png"))

            train._goto_month(f"{TARGET[:4]}年 {int(TARGET[5:7])}月", "depart")
            cells = page.locator(TrainSearchPage.CALENDAR["depart"]).locator(
                "ul.date-bd li:has(strong:text-is('%d'))" % int(TARGET[8:])
            )
            facts["cells"] = cells.count()
            log(f"目标格命中 {facts['cells']} 个")
            if facts["cells"]:
                cells.first.click()
                facts["B_after_click"] = box.input_value().strip()
                log(f"B 点完立刻 input={facts['B_after_click']!r}")
                page.wait_for_timeout(1000)
                facts["C_after_1s"] = box.input_value().strip()
                log(f"C 1 秒后 input={facts['C_after_1s']!r}")
                page.screenshot(path=str(OUT / "02_after_click.png"))
            train._close_calendar()
            facts["D_after_close"] = box.input_value().strip()
            facts["D_text"] = train._date_block("depart").inner_text().replace("\n", " ")[:40]
            log(f"D 关日历后 input={facts['D_after_close']!r} 块文本={facts['D_text']!r}")
            page.screenshot(path=str(OUT / "03_closed.png"))

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT / 'facts.json'}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
