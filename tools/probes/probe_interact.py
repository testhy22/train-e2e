"""一次性交互探针：城市联想下拉 → 日期控件 → 结果页结构。

只做一次真实查询（等价于一个普通用户查一次车票），用于确定定位器。
每个步骤单独 try，失败也把已完成的部分落盘。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from pages.base_page import BasePage

OUT = Path("reports/probe")
OUT.mkdir(parents=True, exist_ok=True)
R: dict = {}


def dump(name, fn):
    try:
        R[name] = fn()
    except Exception as exc:
        R[name] = f"ERR {type(exc).__name__}: {str(exc)[:200]}"


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(8000)
    page.goto("https://trains.ctrip.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_load_state("load", timeout=20000)
    bp = BasePage(page, "https://trains.ctrip.com")
    R["blocked_at_start"] = None if bp.detect_block() is None else str(bp.detect_block())

    # ---- 步骤 1：出发城市联想下拉 ----
    def city_dropdown():
        loc = page.locator("#label-departStation")
        loc.click()
        loc.fill("")
        loc.fill("广州")                      # 用户要求：优先 fill
        page.get_by_text("广州南", exact=False).first.wait_for(state="visible", timeout=6000)
        page.screenshot(path=str(OUT / "city-dropdown.png"))
        items = page.eval_on_selector_all(
            "li, [role=option], .city-item, dd, .search-list-item",
            """els => els.filter(e => (e.offsetWidth||e.offsetHeight))
                 .map(e => ({tag: e.tagName, cls: (e.className||'').toString().slice(0,60),
                             text: (e.innerText||'').trim().slice(0,30)}))
                 .filter(x => x.text && x.text.length < 30).slice(0, 40)""",
        )
        return {"value_after_fill": loc.input_value(), "items": items}

    dump("city_dropdown", city_dropdown)

    # ---- 步骤 2：点选下拉里的「广州」 ----
    def pick_city():
        page.get_by_text("广州", exact=True).first.click()
        return {
            "depart_value": page.locator("#label-departStation").input_value(),
            "arrive_value": page.locator("#label-arriveStation").input_value(),
        }

    dump("pick_city", pick_city)

    # ---- 步骤 3：日期控件结构 ----
    def date_widget():
        box = page.locator("#label-departDate")
        box.click(force=True)
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "date-calendar.png"))
        return page.eval_on_selector_all(
            "[class*=calendar], [class*=date], [class*=month]",
            """els => els.filter(e => (e.offsetWidth||e.offsetHeight))
                 .map(e => ({cls: (e.className||'').toString().slice(0,80),
                             text: (e.innerText||'').trim().slice(0,40)}))
                 .slice(0, 30)""",
        )

    dump("date_widget", date_widget)

    # ---- 步骤 4：到达城市 ----
    def arrive_city():
        loc = page.locator("#label-arriveStation")
        loc.click()
        loc.fill("")
        loc.fill("深圳")
        page.get_by_text("深圳北", exact=False).first.wait_for(state="visible", timeout=6000)
        page.get_by_text("深圳", exact=True).first.click()
        return {"arrive_value": loc.input_value()}

    dump("arrive_city", arrive_city)

    # ---- 步骤 5：搜索 + 结果页结构 ----
    def search_and_read():
        page.get_by_role("button", name="搜索").first.click()
        page.wait_for_load_state("load", timeout=30000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "result.png"), full_page=False)
        return {
            "url": page.url,
            "title": page.title(),
            "blocked": None if bp.detect_block() is None else str(bp.detect_block()),
            "list_candidates": page.eval_on_selector_all(
                "[class*=train], [class*=list], [class*=item]",
                """els => els.filter(e => (e.offsetWidth||e.offsetHeight))
                     .map(e => ({cls: (e.className||'').toString().slice(0,70),
                                 len: (e.innerText||'').length}))
                     .filter(x => x.len > 20).slice(0, 25)""",
            ),
            "body_head": page.locator("body").inner_text()[:600],
        }

    dump("search", search_and_read)

    (OUT / "interact.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3000])
    ctx.close()
    browser.close()
