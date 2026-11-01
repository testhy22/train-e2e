"""验证性探针 41：日历「先选远期、再选近期」能不能真的拨回去。

这是 e2e 第五轮那个失败的最小复现：
  · 第 3 组选 2026-12-01 → 第 4 组要 2026-11-08；
  · 老实现只按「11月」找月份块，而日历此时从 12 月起渲染，块不存在 → 静默不改 →
    提交时用上一组的日期。

本探针用**页面对象本身**（`FlightSearchPage.select_date`）跑这段序列，直接把
「翻月前可见月份 / 翻月后可见月份 / 最终 input 值」打出来。过了，就说明
`select_date` 真的处理了反方向翻月，而不是靠运气。
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.flight_search_page import FlightSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_flights_calpage" / datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}
    state = settings.auth_state_path("www")

    # 远期 → 近期，故意制造「需要往回翻」的局面
    far = date(2026, 12, 1)
    near = date(2026, 11, 8)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            storage_state=str(state) if state.exists() else None,
            permissions=[],
            no_viewport=True,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            flight = FlightSearchPage(page)
            flight.open()
            flight.choose_one_way()

            # --- 第 1 步：选远期 ---
            box = page.locator(flight.DATE_BOX).first
            box.click()
            page.wait_for_timeout(900)
            facts["months_before_far"] = flight.shown_months()
            log(f"[1] 选 {far} 前，日历可见 {facts['months_before_far']}")
            box.click()
            page.wait_for_timeout(300)
            value_far = flight.select_date(far)
            facts["value_far"] = value_far
            log(f"    选完 → input={value_far!r}")

            # --- 第 2 步：重开日历，看它从哪个月开始 ---
            box.click()
            page.wait_for_timeout(900)
            facts["months_reopen"] = flight.shown_months()
            log(f"[2] 重开日历，可见 {facts['months_reopen']}（老实现就是死在这里）")
            box.click()
            page.wait_for_timeout(300)

            # --- 第 3 步：选近期（必须往回翻） ---
            value_near = flight.select_date(near)
            facts["value_near"] = value_near
            facts["ok"] = value_near == near.strftime("%Y-%m-%d")
            log(f"[3] 选 {near} → input={value_near!r}  ok={facts['ok']}")

            # --- 第 4 步：日历必须已经收起，否则会挡住搜索按钮 ---
            modal = page.locator(flight.CAL_MODAL)
            facts["modal_visible_after"] = (
                modal.count() > 0 and modal.first.is_visible()
            )
            log(f"[4] 日历仍可见={facts['modal_visible_after']}")

            page.screenshot(path=str(OUT / "01_after.png"))
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0 if facts["ok"] and not facts["modal_visible_after"] else 1
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
