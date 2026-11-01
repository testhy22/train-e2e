"""只读探测 43：酒店日历的翻月箭头 + 「点外面关面板」的可靠坐标。

已被探针 42 证实：酒店日历**和机票一样**起始月份跟着选中日期走 ——
选了 `2026-12-01` 后重开面板只有 `2026年12月 + 2027年1月`，
再选 `2026-11-08` 直接 15 秒超时（`.c-calendar-month` 里根本没有 11 月块）。
而 e2e 那 3 组之所以能过，是因为数据刚好都落在「当前可见两个月」里，
属于**运气**，不是健壮。

本探针只回答两件事：
  1. 酒店日历里「上一月 / 下一月」按钮的定位器（class token + 可见性规律）；
  2. `close_calendar` 现在用 `Escape + to_have_count(0)`，实测关不掉 ——
     换成点 `(20,20)` 是否稳定关得上。
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.hotel_search_page import HotelSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_hotel_calnav" / datetime.now().strftime("%Y%m%d-%H%M%S")
FAR = date(2026, 12, 1)

_DUMP_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    if (!vis(el)) continue;
    const cls = (el.className || '').toString();
    if (!/prev|next|arrow|switch|month/i.test(cls)) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 80 || r.height > 80) continue;
    out.push({tag: el.tagName.toLowerCase(), cls: cls.slice(0, 90),
              text: (el.innerText || '').trim().slice(0, 8),
              x: Math.round(r.left), y: Math.round(r.top),
              w: Math.round(r.width), h: Math.round(r.height)});
  }
  return out;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            base_url=settings.base_url(),
            no_viewport=True,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            hotel = HotelSearchPage(page)
            hotel.open()
            hotel.set_checkin(FAR)
            page.mouse.click(20, 20)
            page.wait_for_timeout(600)

            # 重开面板（已经是 12 月起，prev 应该可见）
            page.locator("label", has_text="入住").first.click()
            page.wait_for_timeout(1200)
            cands = page.evaluate(_DUMP_JS)
            facts["arrows"] = cands
            log(f"[1] 箭头候选 {len(cands)}：")
            for c in cands[:12]:
                log(f"    <{c['tag']}> {c['w']}×{c['h']} at({c['x']},{c['y']}) "
                    f"cls={c['cls']!r} text={c['text']!r}")

            # 试：点最靠左的候选，看月份变不变
            if cands:
                left = min(cands, key=lambda c: c["x"])
                sel = f"{left['tag']}[class~='{left['cls'].split()[0]}']" if left["cls"] else None
                log(f"[2] 试最左候选 {sel!r}")
                if sel:
                    loc = page.locator(sel).first
                    before = page.locator(".c-calendar-month__title").all_inner_texts()
                    loc.click(timeout=8000)
                    page.wait_for_timeout(900)
                    after = page.locator(".c-calendar-month__title").all_inner_texts()
                    facts["click_left"] = {"before": before, "after": after}
                    log(f"    月份 {before} → {after}")

            # 关面板：点 (20,20)
            page.mouse.click(20, 20)
            page.wait_for_timeout(700)
            facts["closed_by_click"] = page.locator(hotel.CAL_DAYS).count() == 0
            log(f"[3] 点(20,20) 关面板 → 已关={facts['closed_by_click']}")

            page.screenshot(path=str(OUT / "01_nav.png"))
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
