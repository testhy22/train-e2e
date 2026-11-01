"""只读探测 42：酒店 / 火车票的日历有没有同样的「回不去」问题。

机票已证实：日历起始月份跟着**当前选中日期**走，选了 12-01 之后重开只有
`12月 + 次年1月`，不翻月就选不到 11-08。机票的 `select_date` 已补翻月。

酒店（`HotelSearchPage._pick_day`）和火车票（`TrainSearchPage.select_date`）都是
「按年月标题找块 → 块内点日」，**没有翻月逻辑**。跑同样的「先远后近」序列，
看它们是真能自动回到当月，还是会等块超时：
  · 能回到当月 ⇒ 说明它们的日历每次重开都重置，不必改；
  · 超时/点不中 ⇒ 同一个坑，得补。
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
from pages.hotel_search_page import HotelSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_cal_others" / datetime.now().strftime("%Y%m%d-%H%M%S")
FAR = date(2026, 12, 1)
NEAR = date(2026, 11, 8)


def log(msg: str) -> None:
    print(msg, flush=True)


def month_heads(page) -> list[str]:
    """把当前渲染出来的月份标题抓出来（不依赖具体 POM），只为观察。"""
    return page.evaluate(
        """() => {
            const vis = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 3 && r.height > 3 && s.display !== 'none'
                    && s.visibility !== 'hidden';
            };
            const out = [];
            for (const el of document.querySelectorAll('*')) {
                if (!vis(el)) continue;
                const t = (el.innerText || '').trim();
                if (el.children.length === 0 && /^\\d{4}年\\d{1,2}月$/.test(t)) out.push(t);
            }
            return [...new Set(out)];
        }"""
    )


def check_hotel(page, facts: dict) -> None:
    hotel = HotelSearchPage(page)
    hotel.open()
    page.wait_for_timeout(800)
    log(f"[酒店] 打开首页，月份标题 {month_heads(page)}")

    hotel.set_checkin(FAR)
    log(f"[酒店] 选完入住 {FAR}，表单={hotel.form_text()[:80]!r} 月份标题 {month_heads(page)}")

    # 用「点外面」关（酒店自己的 close_calendar 是 Esc + 断言，实测这里关不掉）
    page.mouse.click(20, 20)
    page.wait_for_timeout(600)
    facts["hotel_closed_by_click"] = page.locator(hotel.CAL_DAYS).count() == 0
    log(f"[酒店] 点(20,20) 关面板 → 已关={facts['hotel_closed_by_click']}")

    # 重开日历，看它从哪个月开始（这一步决定要不要补翻月）
    hotel.page.locator("label", has_text="入住").first.click()
    page.wait_for_timeout(900)
    facts["hotel_months_reopen"] = month_heads(page)
    log(f"[酒店] 重开日历，月份标题 {facts['hotel_months_reopen']}")
    page.mouse.click(20, 20)
    page.wait_for_timeout(500)

    # 再选近期（要回到 11 月）
    try:
        hotel.set_checkin(NEAR)
        page.mouse.click(20, 20)
        page.wait_for_timeout(500)
        facts["hotel_near_ok"] = True
        facts["hotel_form_after_near"] = hotel.form_text()[:120]
        log(f"[酒店] 选 {NEAR} 未抛异常，表单={facts['hotel_form_after_near']!r}")
    except Exception as exc:
        facts["hotel_near_ok"] = False
        facts["hotel_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        log(f"[酒店] 选 {NEAR} 失败：{facts['hotel_error']}")
    page.screenshot(path=str(OUT / "hotel.png"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            base_url=settings.base_url(),
            no_viewport=True,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            check_hotel(page, facts)
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
