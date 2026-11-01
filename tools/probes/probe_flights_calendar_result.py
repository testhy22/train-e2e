"""只读探测 29：机票日历的日格结构 + 结果页真实卡片（两件事一次跑完）。

28 号探针的结果里有两个「差点就写成错代码」的地方，必须再取证：

1. **日期只点对了，但方式是错的。** 28 号最后用了「文本 + 可见」的兜底
   （`td:has-text('10')` 里取第一个可见），当时目标 11-10 正好落在当前月，
   所以碰巧对。跨月就必然点错 —— 面板同屏渲染两个月，`nth(0)` 拿到的是上个月的同一天。
   要写成「先按月份标题限定块，再点日」，就得先知道日格的真实 tag/class。

2. **结果页抓早了。** 搜索后等 7 秒抓到的全是骨架屏 class
   （`fake-flight-price` ×10、`fake-flight-summary` ×5），真实卡片还没渲染。
   断言若挂在骨架屏上会假绿（骨架屏可见 ≠ 有航班）。

跑法：
    python tools/probes/probe_flights_calendar_result.py
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

OUT = settings.REPORTS_DIR / "probe_flights_cal_result" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

# 日历内部结构：把每个「月块」的标题与日格一起 dump。
_CAL_BLOCK_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 5 && r.height > 5 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const block of document.querySelectorAll('div.date-calendar, div.date-picker')) {
    if (!vis(block)) continue;
    const title = (block.querySelector('div.date-month-title, .date-month-title, [class*="month"]') || {}).innerText || '';
    const cells = [];
    for (const el of block.querySelectorAll('td, li, div, span')) {
      if (!vis(el)) continue;
      const t = (el.innerText || '').trim();
      if (!/^\d{1,2}$/.test(t)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 120 || r.height > 120) continue;
      cells.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 90),
        text: t,
        y: Math.round(r.top), x: Math.round(r.left),
        parent_tag: (el.parentElement || {}).tagName || '',
        parent_cls: (el.parentElement && el.parentElement.className || '').toString().slice(0, 90),
        title_attr: el.getAttribute('title') || '',
        date_attr: el.getAttribute('date') || el.getAttribute('data-date') || '',
      });
    }
    out.push({
      cls: (block.className || '').toString().slice(0, 100),
      title_head: (title || '').replace(/\s+/g, ' ').slice(0, 60),
      cells: cells.slice(0, 60),
    });
  }
  return out;
}
"""

_CLASS_STATS_JS = r"""
() => {
  const cnt = {};
  for (const el of document.querySelectorAll('div,li,span')) {
    const c = (el.className || '').toString();
    for (const part of c.split(/\s+/)) {
      if (part.length < 5) continue;
      if (!/flight|result|list|card|item|price|airline|plane|segment/i.test(part)) continue;
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
    target = date.today() + timedelta(days=40)      # 故意跨月：11 月 → 12 月
    log("=" * 70)
    log(f"落盘：{OUT}  目标日期 {target}（跨月，用来验月份限定）")
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
            page.wait_for_timeout(500)

            # ---------- 1. 日历结构 ----------
            date_box = page.locator("input[placeholder='yyyy-mm-dd']").first
            date_box.click()
            page.wait_for_timeout(1500)
            blocks = page.evaluate(_CAL_BLOCK_JS)
            facts["cal_blocks"] = blocks
            log(f"[1] 月块 {len(blocks)} 个：")
            for i, b in enumerate(blocks):
                log(f"  #{i} cls={b['cls']!r} title_head={b['title_head']!r} 日格 {len(b['cells'])}")
                for c in b["cells"][:4]:
                    log(f"      <{c['tag']}> '{c['text']}' cls={c['cls']!r} "
                        f"parent=<{c['parent_tag']} {c['parent_cls']!r}> "
                        f"title={c['title_attr']!r} date={c['date_attr']!r}")
            page.screenshot(path=str(OUT / "01_calendar.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            # ---------- 2. 填城市（纯文本，28 号实测可行） ----------
            dep = page.locator("input[name='owDCity']").first
            dep.click()
            dep.fill("")
            dep.type("上海", delay=80)
            page.keyboard.press("Escape")
            arr = page.locator("input[name='owACity']").first
            arr.click()
            arr.fill("")
            arr.type("北京", delay=80)
            page.keyboard.press("Escape")
            facts["dep_value"] = dep.input_value()
            facts["arr_value"] = arr.input_value()
            log(f"[2] 城市：{facts['dep_value']!r} → {facts['arr_value']!r}")

            # ---------- 3. 按月份限定选日期 ----------
            date_box.click()
            page.wait_for_timeout(1200)
            month_label = f"{target.year}年{target.month}月"
            blocks = page.locator("div.date-calendar").filter(has_text=month_label)
            facts["month_blocks"] = blocks.count()
            picked = False
            if blocks.count():
                cell = blocks.first.locator("td, div, span").filter(
                    has_text=__import__("re").compile(rf"^\s*{target.day}\s*(¥|$)")
                )
                # 退回：按精确文本取第一个日格
                if not cell.count():
                    cell = blocks.first.locator("li, td, div").filter(
                        has_text=__import__("re").compile(rf"^{target.day}$")
                    )
                if cell.count():
                    cell.first.click()
                    picked = True
            page.wait_for_timeout(1000)
            facts["date_after"] = date_box.input_value()
            facts["picked_by_month"] = picked
            log(f"[3] 月份限定选日 picked={picked} → {facts['date_after']!r} "
                f"（月块命中 {facts['month_blocks']}）")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

            # ---------- 4. 搜索，等到真实结果 ----------
            page.locator("button.search-btn").first.click()
            page.wait_for_url(__import__("re").compile(r"/online/list/"), timeout=30000)
            facts["result_url"] = page.url
            log(f"[4] 落点：{page.url[:200]}")

            for wait_ms in (6000, 6000, 6000):
                page.wait_for_timeout(wait_ms)
                stats = page.evaluate(_CLASS_STATS_JS)
                body = page.locator("body").inner_text()
                facts.setdefault("snapshots", []).append(
                    {"stats": stats[:12], "body_len": len(body)}
                )
                log(f"    等待累计 +{wait_ms}ms → body={len(body)} 字符，class top: "
                    f"{[s[0] for s in stats[:6]]}")

            # 滚动一次，触发懒加载后再抓
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(2500)
            facts["class_stats"] = page.evaluate(_CLASS_STATS_JS)
            facts["body_sample"] = page.locator("body").inner_text()[:600]
            candidates = {}
            for sel in (
                "[class*='flight-item']",
                "[class*='result-item']",
                "[class*='flight-list-item']",
                "[class*='segment-item']",
                "[class*='list-item']",
                "[class*='flight-card']",
                "[class*='airline-name']",
            ):
                candidates[sel] = page.locator(sel).count()
            facts["card_candidates"] = candidates
            log(f"[5] class top15：")
            for name, n in facts["class_stats"][:15]:
                log(f"      {n:4d}  {name}")
            log(f"    卡片候选：{candidates}")
            log(f"    正文前 300：{facts['body_sample'][:300]!r}")
            page.screenshot(path=str(OUT / "02_result.png"))
            page.screenshot(path=str(OUT / "03_result_full.png"), full_page=True)

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"\n落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
