"""只读探测 37：日历翻月箭头的可用定位器（第 2 轮）。

探针 36 已确认箭头长这样：
    <span class="in-date-picker icon next-ico iconf-right">  32×32 在 (1115,296)

但用 `[class='...']` 精确匹配拿到的是**另一个隐藏的同名节点**（站点在两个月份
块里各放了一个箭头，只有一个可见）⇒ 点击超时。这里改成按单个 class token 定位，
列出所有候选 + 可见性，再点「可见的那个」，并回读月份块确认它真的翻了页。
同时确认「上个月」箭头叫什么（`prev-ico`？），因为第 4 组失败正是因为需要往回拨。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_flights_calnav2" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

_MONTHS_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity) > 0.1;
  };
  const out = [];
  for (const el of document.querySelectorAll('div.date-picker')) {
    if (!vis(el)) continue;
    const y = el.querySelector('span.year');
    const m = el.querySelector('span.month');
    out.push({
      year: y ? y.innerText.trim() : '',
      month: m ? m.innerText.trim() : '',
      head: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 18),
    });
  }
  return out;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def probe_arrow(page, token: str, facts: dict, label: str) -> None:
    loc = page.locator(f"div.calendar-modal span[class*='{token}']")
    total = loc.count()
    visible = [i for i in range(total) if loc.nth(i).is_visible()]
    log(f"[{label}] span[class*='{token}'] 共 {total} 个，可见 {visible}")
    facts[label] = {"total": total, "visible_index": visible}
    for i in range(total):
        box = loc.nth(i).bounding_box()
        log(f"      #{i} visible={loc.nth(i).is_visible()} box={box}")


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
            no_viewport=True,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            page.goto(HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            box = page.locator("input[placeholder='yyyy-mm-dd']").first
            box.click()
            page.wait_for_timeout(1200)
            log(f"[1] 打开日历：{[m['year'] + m['month'] for m in page.evaluate(_MONTHS_JS)]}")

            probe_arrow(page, "next-ico", facts, "next")
            probe_arrow(page, "prev-ico", facts, "prev")
            probe_arrow(page, "iconf-left", facts, "iconf-left")
            probe_arrow(page, "iconf-right", facts, "iconf-right")

            # 点「可见的下一个月」
            next_loc = page.locator("div.calendar-modal span[class*='next-ico']")
            clicked = False
            for i in range(next_loc.count()):
                cand = next_loc.nth(i)
                if not cand.is_visible():
                    continue
                cand.click(timeout=5000)
                page.wait_for_timeout(900)
                after = page.evaluate(_MONTHS_JS)
                facts["after_next"] = after
                log(f"[2] 点 next #{i} → {[m['year'] + m['month'] for m in after]}")
                clicked = True
                break
            if not clicked:
                log("[2] ✗ 没有可见的 next-ico")

            # 再点两次「上个月」，看能否拨回去
            prev_loc = page.locator("div.calendar-modal span[class*='prev-ico']")
            for step in range(2):
                hit = False
                for i in range(prev_loc.count()):
                    cand = prev_loc.nth(i)
                    if not cand.is_visible():
                        continue
                    cand.click(timeout=5000)
                    page.wait_for_timeout(900)
                    after = page.evaluate(_MONTHS_JS)
                    log(f"[3.{step}] 点 prev #{i} → {[m['year'] + m['month'] for m in after]}")
                    facts[f"after_prev_{step}"] = after
                    hit = True
                    break
                if not hit:
                    log(f"[3.{step}] ✗ 没有可见的 prev-ico")
                    break

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
