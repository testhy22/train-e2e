"""探针 10：三种日期触发机制 + 用结果 URL 的 dDate 验证是否真正生效。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
CAL = ".widget-calendar-depart-wrap"
R: dict = {"trials": []}

TARGET = "2026-11-07"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(5000)

    def fresh():
        page.goto("https://trains.ctrip.com/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_load_state("load", timeout=20000)

    def trial(name, action):
        fresh()
        rec = {"name": name}
        try:
            action()
            page.wait_for_timeout(900)
            rec["cal_visible"] = page.locator(CAL).first.is_visible()
            rec["input_value"] = page.locator("#label-departDate").input_value()
            if rec["cal_visible"]:
                page.screenshot(path=str(OUT / f"cal-{name}.png"))
                rec["cells"] = page.locator(CAL).evaluate(
                    """e => [...e.querySelectorAll('*')].filter(x => {
                          const t=(x.innerText||'').trim(); const r=x.getBoundingClientRect();
                          return r.width>0 && r.height>0 && /^\\d{1,2}$/.test(t) && +t>=1 && +t<=31 && x.children.length===0;
                       }).slice(0,10).map(x => ({tag:x.tagName, cls:(x.className||'').toString().slice(0,50), text:x.innerText.trim()}))"""
                )
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:110]}"
        R["trials"].append(rec)

    # A：JS focus 输入框
    def a():
        page.evaluate("() => { const el = document.querySelector('#label-departDate'); el && el.focus(); }")
    trial("js-focus", a)

    # B：hover 后点日期区域
    def b():
        page.locator("div.search-date").first.hover()
        page.locator("div.search-date div.date").first.click()
    trial("hover-click", b)

    # C：JS 直接派发 click 事件
    def c():
        page.evaluate("() => { const el = document.querySelector('#label-departDate');"
                             " if (el) { el.dispatchEvent(new MouseEvent('click', {bubbles:true})); } }")
    trial("js-click", c)

    # 若任一方法打开了日历，尝试点选 11-07 并搜索验证
    opened = next((t for t in R["trials"] if t.get("cal_visible")), None)
    if opened:
        rec = {"name": f"select-{opened['name']}"}
        try:
            cell = page.locator(CAL).get_by_text("7", exact=True).first
            cell.click()
            page.wait_for_timeout(600)
            rec["input_after_pick"] = page.locator("#label-departDate").input_value()
            page.get_by_role("button", name="搜索").first.click(timeout=10000)
            page.wait_for_load_state("load", timeout=30000)
            page.wait_for_timeout(1200)
            m = re.search(r"[?&]dDate=(\d{4}-\d{2}-\d{2})", page.url)
            rec["result_dDate"] = m.group(1) if m else None
            rec["result_url"] = page.url[:160]
            page.screenshot(path=str(OUT / "cal-picked.png"))
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:140]}"
        R["select"] = rec

    (OUT / "trials.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3000])
    ctx.close()
    browser.close()
