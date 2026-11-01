"""只读探测 40：日历里「已过去 / 不可售」的日格在 DOM 上长什么样。

用户特意问「日期被置灰（不可选）怎么应对」。当前月里 `今天` 之前的日期就是天然的
不可选样本 —— 把它们的 `div.date-day` 父节点 class 与 `span.date-d` 的 class 全部
倒出来，才能写「点之前先判禁用」而不是猜 token 名。

顺带确认：点一个已过去的日期会怎样（input 不变？面板不关？），以及
`span.month` / `span.year` 是否存在，用于「按年月精确定位月份块」。
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

OUT = settings.REPORTS_DIR / "probe_flights_calpast" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const blk of document.querySelectorAll('div.date-picker')) {
    if (!vis(blk)) continue;
    const y = blk.querySelector('span.year');
    const m = blk.querySelector('span.month');
    const days = [];
    for (const d of blk.querySelectorAll('span.date-d')) {
      if (!vis(d)) continue;
      days.push({
        text: (d.innerText || '').trim(),
        cls: (d.className || '').toString(),
        parent: (d.parentElement.className || '').toString(),
        parentTag: d.parentElement.tagName.toLowerCase(),
        cursor: getComputedStyle(d.parentElement).cursor,
      });
    }
    out.push({year: y ? y.innerText.trim() : '', month: m ? m.innerText.trim() : '', days});
  }
  return {today: new Date().toISOString().slice(0, 10), months: out};
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

            dump = page.evaluate(_JS)
            facts["dump"] = dump
            log(f"[1] 今天={dump['today']}")
            first = dump["months"][0] if dump["months"] else None
            if first:
                log(f"    当前月块 {first['year']}{first['month']}")
                for d in first["days"][:12]:
                    log(f"      {d['text']:>3} span.cls={d['cls']!r} parent={d['parent']!r} "
                        f"cursor={d['cursor']}")
                tokens = set()
                for d in first["days"]:
                    for t in d["parent"].split():
                        tokens.add(t)
                log(f"    父节点 token 集合：{sorted(tokens)}")

            # 点一个过去的日期（本月 1 号，若今天是 1 号就跳过）
            today = date.fromisoformat(dump["today"])
            if today.day > 3:
                block = page.locator("div.date-picker").first
                cell = block.locator("span.date-d").filter(
                    has_text=__import__("re").compile(r"^1$")
                ).first
                before = box.input_value()
                cell.click()
                page.wait_for_timeout(900)
                facts["past_click"] = {
                    "before": before, "after": box.input_value(),
                    "parent_cls": cell.locator("xpath=..").get_attribute("class"),
                }
                log(f"[2] 点本月 1 号：{before!r} → {box.input_value()!r} "
                    f"parent={facts['past_click']['parent_cls']!r}")

            page.screenshot(path=str(OUT / "01_cal.png"))
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
