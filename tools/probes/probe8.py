"""探针 8：日历 widget 内部结构 + 关闭方式 + 日期填写的可行路径。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
R: dict = {}
CAL = ".widget-calendar-depart-wrap"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(6000)
    page.goto("https://trains.ctrip.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_load_state("load", timeout=20000)

    page.get_by_text("2026-10-31", exact=False).first.click()
    page.locator(CAL).wait_for(state="visible", timeout=6000)
    page.screenshot(path=str(OUT / "cal-widget.png"))

    R["cal_html"] = page.locator(CAL).evaluate("e => e.outerHTML")[:2200]
    R["cal_box"] = page.locator(CAL).bounding_box()

    R["day_cells"] = page.locator(CAL).evaluate(
        """e => [...e.querySelectorAll('*')].filter(x => {
              const t=(x.innerText||'').trim(); const r=x.getBoundingClientRect();
              return r.width>0 && r.height>0 && /^\\d{1,2}$/.test(t) && +t>=1 && +t<=31 && x.children.length===0;
           }).slice(0,8).map(x => ({tag:x.tagName, cls:(x.className||'').toString(),
                                    data:JSON.stringify(x.dataset), text:x.innerText.trim()}))"""
    )
    R["month_title"] = page.locator(CAL).evaluate(
        """e => [...e.querySelectorAll('*')].filter(x => /^\\s*\\d{4}\\s*年\\s*\\d{1,2}\\s*月\\s*$/.test((x.innerText||'').trim()))
             .slice(0,4).map(x => ({cls:(x.className||'').toString(), text:x.innerText.trim()}))"""
    )

    # 关闭：点遮罩
    page.locator(".widget-calendar-mask").click(force=True)
    page.wait_for_timeout(400)
    R["mask_visible_after_close"] = page.locator(".widget-calendar-mask").first.is_visible()

    # 隐藏 input 填日期 → 点搜索 → 看 dDate
    try:
        page.locator("#label-departDate").fill("2026-11-07", force=True)
        R["input_after_fill"] = page.locator("#label-departDate").input_value()
    except Exception as exc:
        R["input_after_fill"] = f"ERR {type(exc).__name__}: {str(exc)[:120]}"

    page.get_by_role("button", name="搜索").first.click(timeout=10000)
    page.wait_for_load_state("load", timeout=30000)
    page.wait_for_timeout(1200)
    m = re.search(r"[?&]dDate=(\d{4}-\d{2}-\d{2})", page.url)
    R["result_url"] = page.url
    R["result_dDate"] = m.group(1) if m else None
    page.screenshot(path=str(OUT / "after-fill.png"), full_page=False)

    (OUT / "widget.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3200])
    ctx.close()
    browser.close()
