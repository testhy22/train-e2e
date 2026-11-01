"""只读探测 27：机票「填表 → 搜索 → 结果页」全流程取证。

27 号是首页结构（`probe_flights_home.py`），拿到的是：
    出发地/目的地  input.form-input-v3[name='owDCity' / 'owACity']（默认 重庆 / 北京）
    出发/返回日期  input[placeholder='yyyy-mm-dd']（默认 T+1 / T+4）
    搜索          button.search-btn
    单程/往返     li 里 text 含「单程」「往返」，当前「往返」active

还差四件事，本轮补齐：
  1. 点出发地后，联想下拉的容器 class 前缀 + 候选项文本形态（决定选择器怎么写）
  2. 日期能不能直接 fill（能就不用点日历，省掉一整套控件耦合）
  3. 点搜索后的落点 URL 形态（是否带 sha/bjs 这类三字码）
  4. 结果页卡片容器的 class 语义前缀（断言要挂在它上面）

跑法：
    python tools/probes/probe_flights_flow.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_flights_flow" / __import__("datetime").datetime.now().strftime(
    "%Y%m%d-%H%M%S"
)
HOME = "https://flights.ctrip.com/online/channel/domestic"

# 点完输入框之后，把「新出现的浮层」按候选关键字捞出来。
_POPUP_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 10 && r.height > 10 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('div, ul, li')) {
    if (!vis(el)) continue;
    const cls = (el.className || '').toString();
    if (!/dropdown|suggest|city|panel|list|result|calendar|date/i.test(cls)) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t) continue;
    out.push({
      cls: cls.slice(0, 140),
      text: t.slice(0, 120),
      tag: el.tagName.toLowerCase(),
      w: Math.round(el.getBoundingClientRect().width),
      h: Math.round(el.getBoundingClientRect().height),
    });
  }
  return out.slice(0, 25);
}
"""

# 结果页：统计 class 语义前缀出现频次，找出「卡片」那一类。
_CLASS_STATS_JS = r"""
() => {
  const cnt = {};
  for (const el of document.querySelectorAll('div,li')) {
    const c = (el.className || '').toString();
    for (const part of c.split(/\s+/)) {
      if (part.length < 6) continue;
      if (!/flight|result|list|card|item|price|airline/i.test(part)) continue;
      cnt[part] = (cnt[part] || 0) + 1;
    }
  }
  return Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 40);
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    facts: dict[str, object] = {}
    log("=" * 70)
    log(f"落盘：{OUT}  state 存在={state.exists()}")
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

            # ---------- 1. 切单程 ----------
            one_way = page.locator("li", has_text="单程").first
            try:
                one_way.click()
                page.wait_for_timeout(800)
                facts["oneway_ok"] = True
            except Exception as exc:
                facts["oneway_ok"] = f"点击失败：{exc}"
            log(f"[1] 切单程：{facts['oneway_ok']}")
            page.screenshot(path=str(OUT / "01_oneway.png"))

            # ---------- 2. 出发地联想 ----------
            dep = page.locator("input[name='owDCity']").first
            dep.click()
            dep.fill("")
            dep.type("上海", delay=80)
            page.wait_for_timeout(1500)
            popups = page.evaluate(_POPUP_JS)
            facts["dep_popups"] = popups
            log(f"[2] 出发地联想浮层 {len(popups)} 个：")
            for p in popups[:8]:
                log(f"    <{p['tag']}> {p['w']}×{p['h']} cls={p['cls']!r} text={p['text'][:80]!r}")
            page.screenshot(path=str(OUT / "02_dep_suggest.png"))

            # 点第一条含「上海」的候选项（结构未知，先用文本近似，取最内层）
            cand = page.locator(
                "li:has-text('上海'), div[class*='city'] :text-matches('上海'), "
                "[class*='suggest'] :text-matches('上海')"
            ).first
            facts["dep_candidate_count"] = page.locator("li:has-text('上海')").count()
            try:
                cand.click()
                page.wait_for_timeout(800)
                facts["dep_picked"] = dep.input_value()
            except Exception as exc:
                facts["dep_picked"] = f"点击失败：{exc}"
            log(f"[2b] 选完出发地 → {facts['dep_picked']!r}（li 命中数 {facts['dep_candidate_count']}）")

            # ---------- 3. 目的地 ----------
            arr = page.locator("input[name='owACity']").first
            arr.click()
            arr.fill("")
            arr.type("北京", delay=80)
            page.wait_for_timeout(1500)
            try:
                page.locator(
                    "li:has-text('北京'), [class*='suggest'] :text-matches('北京')"
                ).first.click()
                page.wait_for_timeout(800)
            except Exception as exc:
                log(f"    （目的地候选项点击失败：{exc}）")
            facts["arr_value"] = arr.input_value()
            log(f"[3] 目的地 → {facts['arr_value']!r}")
            page.screenshot(path=str(OUT / "03_cities.png"))

            # ---------- 4. 日期：先试直接 fill ----------
            target = date.today() + timedelta(days=9)
            date_box = page.locator("input[placeholder='yyyy-mm-dd']").first
            facts["date_before"] = date_box.input_value()
            try:
                date_box.click()
                date_box.fill(target.strftime("%Y-%m-%d"))
                page.keyboard.press("Enter")
                page.wait_for_timeout(1200)
            except Exception as exc:
                log(f"    （日期 fill 失败：{exc}）")
            facts["date_after"] = date_box.input_value()
            facts["date_target"] = target.strftime("%Y-%m-%d")
            log(f"[4] 日期 {facts['date_before']!r} → fill 后 {facts['date_after']!r} "
                f"（目标 {facts['date_target']}）")
            page.screenshot(path=str(OUT / "04_date.png"))

            # ---------- 5. 搜索 ----------
            btn = page.locator("button.search-btn").first
            facts["search_btn_visible"] = btn.is_visible()
            btn.click()
            page.wait_for_timeout(6000)
            facts["result_url"] = page.url
            facts["result_title"] = page.title()
            facts["class_stats"] = page.evaluate(_CLASS_STATS_JS)
            facts["body_head"] = page.locator("body").inner_text()[:400]
            log(f"[5] 落点：{page.url[:200]}")
            log(f"    title={page.title()!r}")
            log("    结果页 class 频次（含 flight/list/card/item/price）：")
            for name, n in facts["class_stats"][:15]:
                log(f"      {n:4d}  {name}")
            log(f"    正文前 200：{facts['body_head'][:200]!r}")
            page.screenshot(path=str(OUT / "05_result.png"))
            page.screenshot(path=str(OUT / "06_result_full.png"), full_page=True)

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"\n落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
