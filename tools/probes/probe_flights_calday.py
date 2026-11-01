"""只读探测 38：日历日格的 class / 禁用标记 / 跨月尾巴日。

要回答三件事（写 `select_date` 之前必须知道）：
  1. 日格的**精确**结构：`span.date-d` 的 class 里，正常日 / 不可选日 / 属于相邻月的
     尾巴日分别带什么 token。用户截图里 1 月块末尾有个灰 "31"、1月1日带「元旦」、
     1月2日带「休」—— 如果只按数字匹配，很可能点到尾巴日或点到带标签的容器。
  2. 点日之后日历是**自动收起**还是要手动关（现有 `close_calendar` 是按 Esc 兜底）。
  3. 月份块能不能用 `span.year` + `span.month` 精确定位（比按文本 filter 稳）。
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

OUT = settings.REPORTS_DIR / "probe_flights_calday" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

_DAYS_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const months = [];
  for (const el of document.querySelectorAll('div.date-picker')) {
    if (!vis(el)) continue;
    const y = el.querySelector('span.year');
    const m = el.querySelector('span.month');
    const days = [];
    for (const d of el.querySelectorAll('span.date-d')) {
      if (!vis(d)) continue;
      days.push({
        text: (d.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 10),
        cls: (d.className || '').toString(),
        title: d.getAttribute('title') || '',
        dataKeys: Object.keys(d.dataset || {}),
        parentCls: (d.parentElement && d.parentElement.className || '').toString().slice(0, 60),
      });
    }
    months.push({
      year: y ? y.innerText.trim() : '',
      month: m ? m.innerText.trim() : '',
      dayCount: days.length,
      days,
    });
  }
  return months;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}
    target = date.today() + timedelta(days=45)

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

            months = page.evaluate(_DAYS_JS)
            facts["open"] = months
            log(f"[1] 打开日历：目标 {target}")
            for blk in months:
                log(f"    {blk['year']}{blk['month']} 日格 {blk['dayCount']} 个")
                cls_tokens = set()
                for d in blk["days"]:
                    for tok in (d["cls"] or "").split():
                        cls_tokens.add(tok)
                log(f"      class token 集合：{sorted(cls_tokens)}")
                # 打几个样本：第一个 / 带标签的 / 最后一个
                for d in (blk["days"][:2] + blk["days"][-2:]):
                    log(f"      sample text={d['text']!r} cls={d['cls']!r} "
                        f"title={d['title']!r} data={d['dataKeys']} parent={d['parentCls']!r}")
                odd = [d for d in blk["days"] if not d["text"].isdigit()]
                if odd:
                    log(f"      非纯数字日格 {len(odd)} 个：{[(d['text'], d['cls']) for d in odd[:6]]}")

            # 点目标日，看日历是否自动收起
            log(f"[2] 点 {target} 的日格")
            block = page.locator("div.date-picker").filter(
                has=page.locator(f"span.month:text-is('{target.month}月')")
            ).first
            cell = block.locator("span.date-d").filter(
                has_text=__import__("re").compile(rf"^{target.day}$")
            ).first
            cell.click()
            page.wait_for_timeout(1200)
            facts["value_after_click"] = box.input_value()
            facts["modal_still_open"] = page.locator("div.calendar-modal").count() > 0 and \
                page.locator("div.calendar-modal").first.is_visible()
            log(f"    input={facts['value_after_click']!r} 日历仍可见={facts['modal_still_open']}")
            page.screenshot(path=str(OUT / "01_after_day.png"))

            # Esc 是否能关
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
            modal_open = page.locator("div.calendar-modal").count() > 0 and \
                page.locator("div.calendar-modal").first.is_visible()
            facts["modal_after_esc"] = modal_open
            log(f"[3] Esc 后日历仍可见={modal_open}")

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
