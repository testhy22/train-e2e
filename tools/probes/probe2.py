"""探针 2：fill vs press_sequentially 的 A/B、日历控件结构、结果列表项定位。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
R: dict = {}
HOME = "https://trains.ctrip.com/"
SUGGEST_TIMEOUT = 3000


def visible_panel_text(page) -> str:
    out = []
    for sel in ("[class*=city]", "[class*=suggest]", "[class*=drop]", "[class*=panel]"):
        try:
            for i in range(min(page.locator(sel).count(), 6)):
                el = page.locator(sel).nth(i)
                if el.is_visible():
                    t = el.inner_text(timeout=1000).strip().replace("\n", " / ")
                    if "广州" in t or "上海" in t:
                        out.append(f"{sel}#{i}: {t[:120]}")
        except Exception:
            continue
    return " || ".join(out[:4])


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(8000)

    # ================= A/B：fill vs 逐字 =================
    page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_load_state("load", timeout=20000)
    loc = page.locator("#label-departStation")

    loc.click()
    loc.fill("")
    loc.fill("广州")
    try:
        page.get_by_text("广州南", exact=False).first.wait_for(state="visible", timeout=SUGGEST_TIMEOUT)
        fill_hit = True
    except Exception:
        fill_hit = False
    R["fill"] = {"suggest_visible": fill_hit, "panel": visible_panel_text(page)}
    page.screenshot(path=str(OUT / "ab-fill.png"))

    page.reload(wait_until="load")
    loc = page.locator("#label-departStation")
    loc.click()
    loc.fill("")
    loc.press_sequentially("广州", delay=120)
    try:
        page.get_by_text("广州南", exact=False).first.wait_for(state="visible", timeout=SUGGEST_TIMEOUT)
        type_hit = True
    except Exception:
        type_hit = False
    R["press_sequentially"] = {"suggest_visible": type_hit, "panel": visible_panel_text(page)}
    page.screenshot(path=str(OUT / "ab-type.png"))

    # ================= 日历控件 =================
    page.reload(wait_until="load")
    try:
        trig = page.locator("[class*=date]").filter(has_text="2026-10-31").first
        if not trig.is_visible():
            trig = page.get_by_text("2026-10-31").first
        R["date_trigger"] = {"tag": trig.evaluate("e => e.tagName"),
                             "cls": trig.get_attribute("class"),
                             "text": trig.inner_text()[:40]}
        trig.click()
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "date-panel.png"))
        R["date_panel"] = page.eval_on_selector_all(
            "[class*=calendar], [class*=date-picker], [class*=month]",
            """els => els.filter(e => (e.offsetWidth||e.offsetHeight))
                 .map(e => ({cls: (e.className||'').toString().slice(0,70),
                             text: (e.innerText||'').trim().replace(/\\n+/g,' / ').slice(0,90)}))
                 .slice(0, 12)""",
        )
        # 日期单元格候选
        R["day_cells"] = page.eval_on_selector_all(
            "[class*=day], [class*=cell], td",
            """els => els.filter(e => (e.offsetWidth||e.offsetHeight) && /^\\d{1,2}$/.test((e.innerText||'').trim()))
                 .slice(0, 12).map(e => ({cls: (e.className||'').toString().slice(0,50),
                                          text: e.innerText.trim()}))""",
        )
    except Exception as exc:
        R["date_error"] = f"{type(exc).__name__}: {str(exc)[:150]}"

    # ================= 结果列表项 =================
    try:
        page.goto("https://trains.ctrip.com/webapp/train/list?ticketType=0&dStation=%E5%B9%BF%E5%B7%9E"
                  "&aStation=%E6%B7%B1%E5%9C%B3&dDate=2026-11-07&rDate=&trainsType=&hubCityName=&highSpeedOnly=0",
                  wait_until="load", timeout=45000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "list.png"), full_page=False)
        R["list"] = {
            "url": page.url,
            "title": page.title(),
            "train_no_count": page.get_by_text(__import__("re").compile(r"^[GDCZTK]\d{1,4}$")).count(),
            "rows": page.eval_on_selector_all(
                "[class*=train-item], [class*=train-box], [class*=list-item], [class*=item-wrap]",
                """els => els.filter(e => (e.offsetWidth||e.offsetHeight))
                     .map(e => ({cls: (e.className||'').toString().slice(0,50),
                                 len: (e.innerText||'').length,
                                 head: (e.innerText||'').trim().replace(/\\n+/g,' ').slice(0,60)}))
                     .filter(x => x.len > 40).slice(0, 8)""",
            ),
            "body_head": page.locator("body").inner_text()[:500],
        }
    except Exception as exc:
        R["list_error"] = f"{type(exc).__name__}: {str(exc)[:150]}"

    (OUT / "interact2.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:2600])
    ctx.close()
    browser.close()
