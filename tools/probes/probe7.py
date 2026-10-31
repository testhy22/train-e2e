"""探针 7：验证日历是否在闭合 shadow DOM；测试隐藏日期输入框能否改日期。"""

from __future__ import annotations

import json
import re
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

    # 1) shadow root 统计
    R["shadow"] = page.evaluate(
        """() => { const all=[...document.querySelectorAll('*')];
             const open = all.filter(e=>e.shadowRoot);
             return {total: all.length, open_shadow: open.length,
                     sample: open.slice(0,5).map(e=>e.tagName+'.'+(e.className||''))}; }"""
    )

    # 2) 日历打开后，闭 shadow 是否有增加（间接判断）
    page.get_by_text("2026-10-31", exact=False).first.click()
    page.wait_for_timeout(1200)
    R["shadow_after_open"] = page.evaluate("() => document.querySelectorAll('*').length")
    R["dom_texts_with_month"] = page.evaluate(
        """() => [...document.querySelectorAll('*')].filter(e => (e.children.length===0)
                 && /\\d{4}\\s*年\\s*\\d{1,2}\\s*月/.test((e.textContent||'').trim())).slice(0,5)
                 .map(e => e.tagName + ' | ' + (e.textContent||'').trim())"""
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    # 3) 隐藏 input 直接填日期，再点搜索，看结果 URL 的 dDate
    before = page.locator("#label-departDate").input_value()
    try:
        page.locator("#label-departDate").fill("2026-11-07", force=True)
        filled = page.locator("#label-departDate").input_value()
    except Exception as exc:
        filled = f"ERR {type(exc).__name__}: {str(exc)[:100]}"
    R["date_fill"] = {"before": before, "after_fill": filled}

    page.get_by_role("button", name="搜索").first.click()
    page.wait_for_load_state("load", timeout=30000)
    page.wait_for_timeout(1500)
    R["result_url"] = page.url
    m = re.search(r"[?&]dDate=(\d{4}-\d{2}-\d{2})", page.url)
    R["result_dDate"] = m.group(1) if m else None
    page.screenshot(path=str(OUT / "after-fill-search.png"), full_page=False)

    (OUT / "shadow.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:2200])
    ctx.close()
    browser.close()
