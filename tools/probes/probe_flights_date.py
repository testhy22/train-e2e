"""只读探测 28：机票的日期日历 + 城市选择 + 搜索落点（第三轮，前两轮踩到的坑已避开）。

27 号探针的结论（都是实测，不是猜）：
    · 出发地/目的地是 `input.form-input-v3[name='owDCity' / 'owACity']`，
      候选项是 `li[title='上海']` 这种形态 —— **不是**文本节点，
      用 `li:has-text('上海')` 会命中 8 个，其中第一个可能不可见
      （`element is not visible`，25 次重试后超时）；
    · 日期框是 **readonly**：`fill()` 直接报 `element is not editable`，
      只能点开日历再点日期；
    · 日历打开后会**盖住搜索按钮**：`<span class="date-m">` 拦截指针事件，
      点 `button.search-btn` 会一直重试到超时 —— 搜索前必须先把日历收起来。

本轮补齐：日历面板结构、可点日期节点的稳定选择器、收起日历的方式、搜索后落点与卡片 class。
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

OUT = settings.REPORTS_DIR / "probe_flights_date" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

# 日历面板：把所有 class 里带 date/calendar 的可见容器，连内部可点节点一起 dump。
_CAL_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 5 && r.height > 5 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = {panels: [], days: []};
  for (const el of document.querySelectorAll('div,table')) {
    if (!vis(el)) continue;
    const cls = (el.className || '').toString();
    if (!/date|calendar/i.test(cls)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 100) continue;
    out.panels.push({
      tag: el.tagName.toLowerCase(), cls: cls.slice(0, 140),
      w: Math.round(r.width), h: Math.round(r.height),
      text: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 160),
    });
  }
  for (const el of document.querySelectorAll('td, li, span, div')) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').trim();
    if (!/^\d{1,2}$/.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.width > 120) continue;
    out.days.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 100),
      text: t, x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
      parent_cls: (el.parentElement && el.parentElement.className || '').toString().slice(0, 90),
    });
  }
  return out;
}
"""

_CLASS_STATS_JS = r"""
() => {
  const cnt = {};
  for (const el of document.querySelectorAll('div,li')) {
    const c = (el.className || '').toString();
    for (const part of c.split(/\s+/)) {
      if (part.length < 6) continue;
      if (!/flight|result|list|card|item|price|airline|plane/i.test(part)) continue;
      cnt[part] = (cnt[part] || 0) + 1;
    }
  }
  return Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 30);
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    facts: dict[str, object] = {}
    target = date.today() + timedelta(days=9)
    log("=" * 70)
    log(f"落盘：{OUT}   目标日期 {target}")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            storage_state=str(state) if state.exists() else None,
            permissions=[],
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            page.goto(HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            page.locator("li", has_text="单程").first.click()
            page.wait_for_timeout(600)

            # ---------- 城市：用 li[title=...] ----------
            dep = page.locator("input[name='owDCity']").first
            dep.click()
            dep.fill("")
            dep.type("上海", delay=80)
            dep_item = page.locator("li[title='上海']").first
            try:
                dep_item.wait_for(state="visible", timeout=8000)
                dep_item.click()
                page.wait_for_timeout(600)
            except Exception as exc:
                log(f"    （出发地候选项点击失败：{exc}）")
            facts["dep_value"] = dep.input_value()
            log(f"[1] 出发地 → {facts['dep_value']!r}")

            arr = page.locator("input[name='owACity']").first
            arr.click()
            arr.fill("")
            arr.type("北京", delay=80)
            arr_item = page.locator("li[title='北京']").first
            try:
                arr_item.wait_for(state="visible", timeout=8000)
                arr_item.click()
                page.wait_for_timeout(600)
            except Exception as exc:
                log(f"    （目的地候选项点击失败：{exc}）")
            facts["arr_value"] = arr.input_value()
            log(f"[2] 目的地 → {facts['arr_value']!r}")
            page.screenshot(path=str(OUT / "01_cities.png"))

            # ---------- 日历 ----------
            date_box = page.locator("input[placeholder='yyyy-mm-dd']").first
            facts["date_before"] = date_box.input_value()
            date_box.click()
            page.wait_for_timeout(1200)
            cal = page.evaluate(_CAL_JS)
            facts["cal_panels"] = cal["panels"]
            facts["cal_days_sample"] = cal["days"][:25]
            log(f"[3] 日历面板 {len(cal['panels'])} 个：")
            for p in cal["panels"][:5]:
                log(f"    <{p['tag']}> {p['w']}×{p['h']} cls={p['cls']!r}")
                log(f"        text={p['text'][:120]!r}")
            log(f"    可点数字节点 {len(cal['days'])} 个，前 10：")
            for d in cal["days"][:10]:
                log(f"      <{d['tag']}> {d['w']}×{d['h']} at({d['x']},{d['y']}) "
                    f"text={d['text']!r} cls={d['cls']!r} parent={d['parent_cls']!r}")
            page.screenshot(path=str(OUT / "02_calendar.png"))

            # 点目标日期（先试 title / data-* 属性；再退回文本精确匹配 + 可见过滤）
            got = False
            for cand in (
                f"[title='{target.strftime('%Y-%m-%d')}']",
                f"td[data-date='{target.strftime('%Y-%m-%d')}']",
                f"[date='{target.strftime('%Y-%m-%d')}']",
            ):
                loc = page.locator(cand).first
                if loc.count() and loc.is_visible():
                    loc.click()
                    got = True
                    facts["date_selector_used"] = cand
                    break
            if not got:
                # 文本 + 可见：日历可能同屏两个月，用 nth(0) 会点到上个月的同一天
                loc = page.locator(
                    f"td:has-text('{target.day}'), div[class*='date'] :text-is('{target.day}')"
                ).filter(visible=True)
                if loc.count():
                    loc.nth(0).click()
                    got = True
                    facts["date_selector_used"] = "text+visible"
            page.wait_for_timeout(1000)
            facts["date_after"] = date_box.input_value()
            facts["date_clicked"] = got
            log(f"[4] 点日期 succeeded={got}  → {facts['date_after']!r}")
            page.screenshot(path=str(OUT / "03_date_picked.png"))

            # ---------- 收日历（关键：不收会挡住搜索按钮） ----------
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
            facts["cal_open_after_esc"] = page.locator("div[class*='calendar']").count()
            btn = page.locator("button.search-btn").first
            facts["btn_visible"] = btn.is_visible()
            log(f"[5] Esc 后日历容器数={facts['cal_open_after_esc']} 搜索按钮可见={btn.is_visible()}")

            try:
                btn.click(timeout=8000)
            except Exception as exc:
                log(f"    直接点被挡（{type(exc).__name__}），改用强制点击再试一次")
                btn.click(force=True)
            page.wait_for_timeout(7000)
            facts["result_url"] = page.url
            facts["result_title"] = page.title()
            facts["class_stats"] = page.evaluate(_CLASS_STATS_JS)
            facts["body_head"] = page.locator("body").inner_text()[:400]
            log(f"[6] 落点：{page.url[:220]}")
            log(f"    title={page.title()!r}")
            for name, n in facts["class_stats"][:12]:
                log(f"      {n:4d}  {name}")
            log(f"    正文前 200：{facts['body_head'][:200]!r}")
            page.screenshot(path=str(OUT / "04_result.png"))

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"\n落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
