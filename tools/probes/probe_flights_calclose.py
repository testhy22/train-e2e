"""只读探测 39：点完日期后，日历到底怎么关？

探针 38 的两个反直觉结论：
  · 点日格后 input 已变成 `2026-12-16`，但 `div.calendar-modal` **仍然可见**；
  · `Escape` **关不掉它**（按完仍可见）。

而 e2e 里 3 组通过、1 组失败，说明现有 `close_calendar()` 的兜底（鼠标点 20,20）
其实是生效的。这里要把「哪种关法既有效又零副作用」测清楚：
  A. 鼠标点 (20,20) 空白
  B. 再点一次日期输入框（很多日历是 toggle）
  C. 点日历外、页面内的一块中性区域（表单标题栏右侧空白）
  D. 向 document 派发一次 mousedown（合成事件）

每次都要回读：日历是否不可见 + URL 有没有被动过 + input 值有没有被清掉。
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

OUT = settings.REPORTS_DIR / "probe_flights_calclose" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"


def log(msg: str) -> None:
    print(msg, flush=True)


def state(page, box) -> dict:
    modal = page.locator("div.calendar-modal")
    opened = modal.count() > 0 and modal.first.is_visible()
    return {
        "modal_open": opened,
        "url": page.url,
        "input": box.input_value(),
        "month_blocks": page.locator("div.date-picker:visible").count(),
    }


def open_calendar(page, box) -> None:
    box.click()
    page.wait_for_timeout(1000)


def pick_day(page, target: date) -> None:
    block = page.locator("div.date-picker").filter(
        has=page.locator(f"span.month:text-is('{target.month}月')")
    ).first
    block.locator("span.date-d").filter(
        has_text=__import__("re").compile(rf"^{target.day}$")
    ).first.click()
    page.wait_for_timeout(900)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}
    target = date.today() + timedelta(days=40)

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
            page.goto(HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            box = page.locator("input[placeholder='yyyy-mm-dd']").first

            # ---------- A：鼠标点 (20,20) ----------
            open_calendar(page, box)
            pick_day(page, target)
            before = state(page, box)
            page.mouse.click(20, 20)
            page.wait_for_timeout(700)
            after = state(page, box)
            facts["A_click_20_20"] = {"before": before, "after": after}
            log(f"[A] 点(20,20)：前 {before} → 后 {after}")
            page.screenshot(path=str(OUT / "A_after_20_20.png"))

            # ---------- B：再点一次日期框 ----------
            if after["modal_open"]:
                pass
            else:
                open_calendar(page, box)
            before = state(page, box)
            box.click()
            page.wait_for_timeout(700)
            after = state(page, box)
            facts["B_click_box_again"] = {"before": before, "after": after}
            log(f"[B] 再点日期框：前 {before} → 后 {after}")
            page.screenshot(path=str(OUT / "B_after_reclick.png"))

            # ---------- C：点页面中性区 ----------
            if after["modal_open"]:
                modal_box = page.locator("div.calendar-modal").first.bounding_box()
                log(f"    modal bbox={modal_box}")
                # 模态框上方 40px 处，仍在页面内容区
                y = max(120, (modal_box or {"y": 300})["y"] - 40)
                page.mouse.click(700, y)
                page.wait_for_timeout(700)
                after = state(page, box)
                facts["C_click_above_modal"] = {"click": [700, y], "after": after}
                log(f"[C] 点模态框上方 (700,{y})：后 {after}")
                page.screenshot(path=str(OUT / "C_after_above.png"))

            # ---------- D：合成 mousedown ----------
            if after["modal_open"]:
                disp = page.evaluate(
                    """() => {
                        const ev = new MouseEvent('mousedown', {bubbles: true, cancelable: true});
                        document.body.dispatchEvent(ev);
                        return document.querySelector('div.calendar-modal') ? 'still-in-dom' : 'removed';
                    }"""
                )
                page.wait_for_timeout(700)
                after = state(page, box)
                facts["D_synthetic_mousedown"] = {"js": disp, "after": after}
                log(f"[D] 合成 mousedown：{disp} → 后 {after}")

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
