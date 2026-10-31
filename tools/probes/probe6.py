"""探针 6：按文本反查日历的真实宿主（frame / 元素 / 祖先链）。"""

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

    page.get_by_text("2026-10-31", exact=False).first.click()
    page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / "cal-open2.png"))

    R["frames"] = [{"name": f.name, "url": f.url[:90]} for f in page.frames]

    hit = page.get_by_text("2026年10月", exact=False)
    R["month_hits"] = hit.count()
    if hit.count():
        el = hit.first
        R["month_el"] = {
            "tag": el.evaluate("e => e.tagName"),
            "cls": el.get_attribute("class"),
            "box": el.bounding_box(),
            "chain": el.evaluate(
                """e => { let p=[], n=e;
                     while(n && n.tagName !== 'HTML'){ p.unshift(n.tagName + (n.id ? '#'+n.id : '') + (n.className && n.className.toString() ? '.'+n.className.toString().split(' ').slice(0,2).join('.') : '')); n = n.parentElement; }
                     return p.join(' > '); }"""
            ),
            "parent_html": el.evaluate("e => { let n = e.parentElement; for (let i=0;i<3 && n && n.parentElement;i++) n = n.parentElement; return n ? n.outerHTML.slice(0,1500) : ''; }"),
        }
        # 从宿主往上找「日历容器」，再数里面的日期单元格
        R["cells_in_host"] = el.evaluate(
            """e => { let n = e; for (let i=0;i<6 && n; i++) n = n.parentElement;
                 if (!n) return [];
                 return [...n.querySelectorAll('*')].filter(x => {
                     const t=(x.innerText||'').trim(); const r=x.getBoundingClientRect();
                     return r.width>0 && r.height>0 && /^\\d{1,2}$/.test(t) && +t>=1 && +t<=31;
                 }).slice(0,12).map(x => ({tag:x.tagName, cls:(x.className||'').toString().slice(0,50), text:x.innerText.trim()})); }"""
        )

    (OUT / "cal3.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3000])
    ctx.close()
    browser.close()
