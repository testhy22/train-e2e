"""探针 9：找出能稳定唤起日历的触发源，并导出 widget 内部单元格结构。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
R: dict = {"candidates": []}
CAL = ".widget-calendar-depart-wrap"

CANDIDATES = [
    ("div.search-date div.date", "css"),
    ("#label-departDate", "force"),
    ("text=2026-10-31", "text"),
    ("div.search-date", "css"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(5000)

    for selector, mode in CANDIDATES:
        page.goto("https://trains.ctrip.com/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_load_state("load", timeout=20000)
        entry = {"selector": selector, "mode": mode}
        try:
            loc = page.locator(selector).first if mode != "text" else page.get_by_text("2026-10-31", exact=False).first
            loc.click(force=(mode == "force"), timeout=5000)
            page.locator(CAL).wait_for(state="visible", timeout=4000)
            entry["opened"] = True
            entry["cal_box"] = page.locator(CAL).bounding_box()
        except Exception as exc:
            entry["opened"] = False
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:90]}"

        if entry.get("opened"):
            page.screenshot(path=str(OUT / "cal-widget.png"))
            entry["month_title"] = page.locator(CAL).evaluate(
                """e => [...e.querySelectorAll('*')].filter(x => /^\\s*\\d{4}\\s*年\\s*\\d{1,2}\\s*月\\s*$/.test((x.innerText||'').trim()))
                     .slice(0,4).map(x => ({cls:(x.className||'').toString().slice(0,50), text:x.innerText.trim()}))"""
            )
            entry["day_cells"] = page.locator(CAL).evaluate(
                """e => [...e.querySelectorAll('*')].filter(x => {
                      const t=(x.innerText||'').trim(); const r=x.getBoundingClientRect();
                      return r.width>0 && r.height>0 && /^\\d{1,2}$/.test(t) && +t>=1 && +t<=31 && x.children.length===0;
                   }).slice(0,8).map(x => ({tag:x.tagName, cls:(x.className||'').toString().slice(0,60),
                                            data:JSON.stringify(x.dataset).slice(0,60), text:x.innerText.trim()}))"""
            )
            entry["cal_html"] = page.locator(CAL).evaluate("e => e.outerHTML")[:2000]
            R["candidates"].append(entry)
            break
        R["candidates"].append(entry)

    (OUT / "widget.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3400])
    ctx.close()
    browser.close()
