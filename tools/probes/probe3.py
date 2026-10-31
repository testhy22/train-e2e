"""探针 3：日期区域 DOM 结构 + 触发方式。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
R: dict = {}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(8000)
    page.goto("https://trains.ctrip.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_load_state("load", timeout=20000)

    R["date_html"] = page.locator("div.search-date").first.evaluate("e => e.outerHTML")[:1200]

    # 页面里是否存在日历容器（含不可见的）
    R["calendar_like"] = page.eval_on_selector_all(
        "[class*=calendar], [class*=picker], [class*=month]",
        """els => els.map(e => ({cls: (e.className||'').toString().slice(0,60),
                               hid: e.offsetWidth===0 && e.offsetHeight===0,
                               text: (e.innerText||'').trim().slice(0,30)}))""",
    )[:12]

    # 尝试点「星期几」附近的箭头
    try:
        page.locator("div.search-date").first.click()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUT / "cal-attempt1.png"))
        R["after_click_class"] = page.locator("div.search-date").first.get_attribute("class")
        R["visible_month_like"] = page.eval_on_selector_all(
            "div, span, td",
            """els => els.filter(e => (e.offsetWidth||e.offsetHeight)
                    && /(\\d{1,2})月|下月|下个月/.test((e.innerText||'').trim())
                    && (e.innerText||'').trim().length < 40)
                 .slice(0,10).map(e => ({cls:(e.className||'').toString().slice(0,40),
                                         text:e.innerText.trim()}))""",
        )
    except Exception as exc:
        R["click_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"

    # 点日期文字本身
    try:
        page.get_by_text("2026-10-31", exact=False).first.click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "cal-attempt2.png"))
        R["attempt2_html_snippet"] = page.eval_on_selector_all(
            "body > div",
            """els => els.filter(e => (e.offsetWidth||e.offsetHeight) && (e.innerText||'').includes('月'))
                 .map(e => ({cls:(e.className||'').toString().slice(0,60),
                             text:(e.innerText||'').trim().replace(/\\n+/g,' / ').slice(0,120)}))
                 .slice(0,6)""",
        )
    except Exception as exc:
        R["click2_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"

    (OUT / "date.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3000])
    ctx.close()
    browser.close()
