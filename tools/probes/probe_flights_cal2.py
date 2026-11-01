"""只读探测 30：机票日历按月份限定选日 + 结果页真实卡片（第二轮，修正 29 号的两个错）。

29 号拿到的事实（结构层面已确定，这轮直接用）：
    日历根        div.calendar-modal.date-custom
    月份块        div.date-picker（其标题文本是「11月」「12月」，**不是**「2026年11月」）
    日期格        div.date-day 里的 span.date-d（纯数字文本，可能带 ¥价格尾巴）
    坑            选日失败时日历不关，`span.date-m` 会拦住 `button.search-btn` 的点击，
                 重试 27 次后超时 —— 29 号就是这么死的

29 号的两个错，本轮改掉：
  1. 月份限定用 `div.date-calendar` + has_text('2026年12月') → 命中 0。
     真实标题只有「12月」，要挂在 `div.date-picker` 上。
  2. 没做「日历是否关掉」的检查。选日成功后日历会自己关，失败则必须手动关，
     否则后面所有点击都打在日期面板上。

本轮同时回答：搜索结果里**真实卡片**的 class 前缀（29 号那次抓到的是骨架屏
`fake-flight-price`，等 7 秒不够）。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_flights_cal2" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

_CLASS_STATS_JS = r"""
() => {
  const cnt = {};
  for (const el of document.querySelectorAll('div,li,span')) {
    const c = (el.className || '').toString();
    for (const part of c.split(/\s+/)) {
      if (part.length < 5) continue;
      if (!/flight|result|list|card|item|price|airline|plane|segment|plane/i.test(part)) continue;
      cnt[part] = (cnt[part] || 0) + 1;
    }
  }
  return Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 25);
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    facts: dict[str, object] = {}
    target = date.today() + timedelta(days=40)          # 跨月：11 月 → 12 月
    month_label = f"{target.month}月"
    log("=" * 70)
    log(f"落盘：{OUT}   目标 {target}（{month_label}，跨月）")
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

            # ---------- 城市：纯文本输入（29 号实测 input 值会被补成「上海(SHA)」） ----------
            dep = page.locator("input[name='owDCity']").first
            dep.click()
            dep.fill("")
            dep.type("广州", delay=70)
            arr = page.locator("input[name='owACity']").first
            arr.click()
            arr.fill("")
            arr.type("成都", delay=70)
            facts["dep_value"], facts["arr_value"] = dep.input_value(), arr.input_value()
            log(f"[1] 城市：{facts['dep_value']!r} → {facts['arr_value']!r}")

            # ---------- 日期：按月份块限定 ----------
            date_box = page.locator("input[placeholder='yyyy-mm-dd']").first
            facts["date_before"] = date_box.input_value()
            date_box.click()
            page.wait_for_timeout(1200)

            block = page.locator("div.date-picker").filter(
                has_text=re.compile(month_label)
            ).first
            facts["month_block_visible"] = block.is_visible()
            log(f"[2] 月份块「{month_label}」可见={block.is_visible()}")

            cell = block.locator("span.date-d").filter(
                has_text=re.compile(rf"^{target.day}$")
            ).first
            facts["cell_count"] = block.locator("span.date-d").filter(
                has_text=re.compile(rf"^{target.day}$")
            ).count()
            cell.click()
            page.wait_for_timeout(1200)
            facts["date_after"] = date_box.input_value()
            log(f"[3] 点日 cell命中={facts['cell_count']} → {facts['date_after']!r}")

            # ---------- 日历必须关掉，否则搜索按钮点不到（29 号教训） ----------
            modal = page.locator("div.calendar-modal")
            if modal.count():
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            if modal.count():
                page.mouse.click(20, 20)          # 点日历外的空白
                page.wait_for_timeout(500)
            facts["calendar_closed"] = modal.count() == 0
            log(f"[4] 日历已关={facts['calendar_closed']}")

            btn = page.locator("button.search-btn").first
            btn.click(timeout=10000)
            page.wait_for_url(re.compile(r"/online/list/"), timeout=30000)
            facts["result_url"] = page.url
            log(f"[5] 落点：{page.url[:200]}")

            # ---------- 等真实结果：轮询到出现「非 fake」的航班容器 ----------
            stats: list = []
            real = 0
            for i in range(5):
                page.wait_for_timeout(5000)
                stats = page.evaluate(_CLASS_STATS_JS)
                fake = sum(n for c, n in stats if c.startswith("fake"))
                real = sum(n for c, n in stats if not c.startswith("fake"))
                log(f"    +{(i + 1) * 5}s  fake={fake}  real={real}  top={[c for c, _ in stats[:5]]}")
                if real > 3:
                    break

            facts["class_stats"] = stats
            facts["body_sample"] = page.locator("body").inner_text()[:800]
            candidates = {}
            for sel in (
                "[class*='flight-item']",
                "[class*='result-item']",
                "[class*='flight-list']",
                "[class*='airline']",
                "[class*='plane']",
                "[class*='price']",
                "div[class*='list'] [class*='item']",
            ):
                candidates[sel] = page.locator(sel).count()
            facts["card_candidates"] = candidates
            log(f"[6] 卡片候选：{candidates}")
            log(f"    正文前 400：{facts['body_sample'][:400]!r}")
            page.screenshot(path=str(OUT / "01_result.png"))
            page.screenshot(path=str(OUT / "02_result_full.png"), full_page=True)

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"\n落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
