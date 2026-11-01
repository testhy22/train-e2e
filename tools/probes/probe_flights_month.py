"""只读探测 35：机票日历的「月份切换」—— 为什么有时点不到目标日。

**为什么探**（2026-11-01，e2e 第五轮）：串联跑 4 组机票，第 4 组（上海→北京 T+7）
提交后 URL 的 depdate 是 `2026-12-01` —— 那是**第 3 组**的日期。
说明第 4 组根本没改成 11-08，而页面沿用了上一组的值。

推断（待本轮验证）：日历打开时的起始月份跟着「当前选中日期」走。
上一组选了 12-01，日历就从 12 月开始渲染两个月，`11月` 那个块压根不存在，
`month_block(11月)` 等不到，`set_depart_date` 返回 False，日期没改，
提交时用的还是 12-01。

要拿到：月份切换箭头的 selector（点它能把日历往回拨），以及拨动后月份块的标题文本。
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

OUT = settings.REPORTS_DIR / "probe_flights_month" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

_DUMP_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const months = [];
  for (const el of document.querySelectorAll('div.date-calendar, div.date-picker')) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 24);
    months.push({cls: (el.className || '').toString().slice(0, 80), head: t});
  }
  const arrows = [];
  for (const el of document.querySelectorAll('div, span, a, button, i')) {
    if (!vis(el)) continue;
    const cls = (el.className || '').toString();
    const r = el.getBoundingClientRect();
    if (r.width > 60 || r.height > 60) continue;
    if (!/prev|next|arrow|month|nav|switch|left|right/i.test(cls)) continue;
    arrows.push({tag: el.tagName.toLowerCase(), cls: cls.slice(0, 90),
                 text: (el.innerText || '').trim().slice(0, 12),
                 x: Math.round(r.left), y: Math.round(r.top),
                 w: Math.round(r.width), h: Math.round(r.height)});
  }
  return {months, arrows};
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    facts: dict[str, object] = {}
    far = date.today() + timedelta(days=30)
    log("=" * 70)
    log(f"落盘：{OUT}   先把日历拨到 {far}，再回来看 11 月还在不在")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            storage_state=str(state) if state.exists() else None,
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
            dump = page.evaluate(_DUMP_JS)
            facts["first_open"] = dump
            log(f"[1] 首次打开日历：月份块 {dump['months']}")
            log(f"    箭头候选 {len(dump['arrows'])}：")
            for a in dump["arrows"][:10]:
                log(f"      <{a['tag']}> {a['w']}×{a['h']} at({a['x']},{a['y']}) "
                    f"cls={a['cls']!r} text={a['text']!r}")
            page.screenshot(path=str(OUT / "01_cal.png"))

            # 点目标日（远日期，跨月）
            block = page.locator("div.date-picker").filter(has_text=f"{far.month}月").first
            if block.is_visible():
                cell = block.locator("span.date-d").filter(
                    has_text=__import__("re").compile(rf"^{far.day}$")
                ).first
                cell.click()
                page.wait_for_timeout(1000)
            facts["picked_far"] = box.input_value()
            log(f"[2] 选了远期日期 → {facts['picked_far']!r}")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

            # 再打开一次日历，看起始月份
            box.click()
            page.wait_for_timeout(1200)
            second = page.evaluate(_DUMP_JS)
            facts["second_open"] = second
            log(f"[3] 第二次打开日历：月份块 {second['months']}")

            # 试试点「上个月」箭头，看能否拨回去
            toggled = []
            for a in second["arrows"]:
                sel = f"{a['tag']}[class*='{a['cls'].split()[0]}']" if a["cls"] else ""
                if not sel:
                    continue
                try:
                    loc = page.locator(sel).first
                    if not loc.is_visible():
                        continue
                    before = page.evaluate(_DUMP_JS)["months"]
                    loc.click()
                    page.wait_for_timeout(800)
                    after = page.evaluate(_DUMP_JS)["months"]
                    toggled.append({"sel": sel, "before": before, "after": after})
                    log(f"[4] 点 {sel!r} → 月份块 {after}")
                    break
                except Exception as exc:
                    log(f"    点 {sel!r} 失败：{type(exc).__name__}")
            facts["toggle"] = toggled
            page.screenshot(path=str(OUT / "02_after_toggle.png"))

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
