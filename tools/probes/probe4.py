"""探针 4：日历面板内部结构 —— 月份标题、翻月按钮、日期单元格。"""

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
    page.wait_for_timeout(1000)
    page.screenshot(path=str(OUT / "cal-open.png"))

    panel = page.locator("body > div").filter(has_text="2026年").first
    R["panel_cls"] = panel.get_attribute("class")
    R["panel_html"] = panel.evaluate("e => e.outerHTML")[:1800]

    # 日期单元格候选：文本是 1..31 的可见元素及其 class / data-*
    R["cells"] = page.eval_on_selector_all(
        "div, span, td, li",
        """els => els.filter(e => {
              const t = (e.innerText||'').trim();
              return (e.offsetWidth||e.offsetHeight) && /^\\d{1,2}$/.test(t)
                     && Number(t) >= 1 && Number(t) <= 31 && e.children.length === 0;
            })
            .slice(0, 14)
            .map(e => ({tag: e.tagName, cls: (e.className||'').toString().slice(0,40),
                        data: JSON.stringify(e.dataset).slice(0,80), text: e.innerText.trim()}))""",
    )

    # 月份标题与翻月按钮
    R["month_headers"] = page.eval_on_selector_all(
        "div, span, strong, th",
        """els => els.filter(e => (e.offsetWidth||e.offsetHeight)
                    && /^\\s*\\d{4}\\s*年\\s*\\d{1,2}\\s*月\\s*$/.test((e.innerText||'').trim()))
                 .slice(0,6).map(e => ({tag:e.tagName, cls:(e.className||'').toString().slice(0,40),
                                        text:e.innerText.trim()}))""",
    )
    R["nav_like"] = page.eval_on_selector_all(
        "[class*=prev], [class*=next], [class*=arrow], i",
        """els => els.filter(e => (e.offsetWidth||e.offsetHeight)
                    && /(prev|next|arrow|left|right)/i.test((e.className||'').toString()))
                 .slice(0,8).map(e => ({cls:(e.className||'').toString().slice(0,50)}))""",
    )

    (OUT / "cal.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3200])
    ctx.close()
    browser.close()
