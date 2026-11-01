"""只读探测 36：机票日历「翻月箭头」到底长什么样。

**为什么探**（2026-11-01，e2e 第五轮）：第 4 组（上海→北京 T+7）提交后 depdate
仍是第 3 组的 `2026-12-01`。原因是日历的起始月份跟着「当前选中日期」走，
选了 12-01 之后重开日历只渲染 `2026年12月` + `2027年1月`，`11月` 块不存在，
`set_depart_date` 等不到块就返回 False，日期没改。

探针 35 想找箭头，但按 class 关键词（prev/next/arrow/month…）筛出来的 84 个
候选全是左侧导航图标 —— 说明箭头**没有语义化 class**。所以这次改成
：把 `div.calendar-modal` 里所有可见、尺寸 < 60×60 的元素连 tag/class/bbox 一起
倒出来，按 y 坐标聚在表头那一行，肉眼定位两个箭头；然后真的点一下右侧那个，
看月份块怎么变。
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

OUT = settings.REPORTS_DIR / "probe_flights_calnav" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

_DUMP_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity) > 0.1;
  };
  const modal = document.querySelector('div.calendar-modal');
  if (!modal) return {error: 'no calendar-modal'};

  const months = [];
  for (const el of modal.querySelectorAll('div.date-picker')) {
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    const r = el.getBoundingClientRect();
    months.push({head: t.slice(0, 20), x: Math.round(r.left), y: Math.round(r.top),
                 w: Math.round(r.width), h: Math.round(r.height)});
  }

  // 表头行：月份块顶部往下 60px 内，所有小元素
  const small = [];
  for (const el of modal.querySelectorAll('*')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 70 || r.height > 70) continue;
    small.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 100),
      text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 16),
      x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
      childCount: el.children.length,
    });
  }
  // 表头 y 带：取所有月份块的最上沿，往下 70px
  const top = months.length ? Math.min(...months.map(m => m.y)) : 0;
  const header = small.filter(e => e.y >= top - 6 && e.y <= top + 70);

  return {months, header, modalRect: (() => {
    const r = modal.getBoundingClientRect();
    return {x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height)};
  })()};
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
            dump = page.evaluate(_DUMP_JS)
            facts["first"] = dump
            log(f"[1] modal={dump.get('modalRect')}")
            log(f"    月份块：{dump.get('months')}")
            log(f"    表头小元素 {len(dump.get('header') or [])}：")
            for e in dump.get("header") or []:
                log(f"      <{e['tag']}> {e['w']}×{e['h']} at({e['x']},{e['y']}) "
                    f"cls={e['cls']!r} text={e['text']!r} kids={e['childCount']}")
            page.screenshot(path=str(OUT / "01_cal.png"))

            # 试：点 bbox 最靠右的那个小元素（期望是「下个月」）
            header = dump.get("header") or []
            right = max(header, key=lambda e: e["x"]) if header else None
            if right:
                log(f"[2] 点最右小元素 <{right['tag']}> at({right['x']},{right['y']}) "
                    f"cls={right['cls']!r}")
                loc = page.locator(
                    f"div.calendar-modal {right['tag']}[class='{right['cls']}']"
                    if right["cls"] else f"div.calendar-modal {right['tag']}"
                ).first
                try:
                    loc.click(timeout=5000)
                    page.wait_for_timeout(900)
                    after = page.evaluate(_DUMP_JS)
                    facts["after_right"] = after
                    log(f"    点后月份块：{[m['head'] for m in after['months']]}")
                except Exception as exc:
                    log(f"    点击失败：{type(exc).__name__}: {exc}")
                    facts["right_click_error"] = f"{type(exc).__name__}: {exc}"

            page.screenshot(path=str(OUT / "02_after.png"))
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
