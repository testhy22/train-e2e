"""只读探测 32：机票首页「第二次访问」会不会被重定向到上次的结果页。

**为什么探它**：e2e 串联轨道里机票 4 组，第 1 组（杭州→重庆）通过，后 3 组全部红在

    Locator.click: Timeout 15000ms exceeded
    - <ul class="city-picker-tabs">…</ul> from <div class="modify-search-box">…</div>
      subtree intercepts pointer events

`modify-search-box` + `city-picker-tabs` 是**结果页顶部「修改搜索」面板**的组件，
说明当时页面根本不是首页 —— 而 `open()` 明明 goto 的是
`/online/channel/domestic`，并且 `input[name='owDCity']` 也等到了可见
（结果页顶部的修改搜索面板里同样有这个 input）。

假设：站点把「上次查询」记住了，第二次访问首页时直接重定向到结果页。
本探针只验证这一件事，不猜：连续 goto 两次首页，分别记录 URL / title / 关键元素计数。
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

OUT = settings.REPORTS_DIR / "probe_flights_repeat" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

_PROBE_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const count = (sel) => {
    let n = 0;
    for (const el of document.querySelectorAll(sel)) if (vis(el)) n++;
    return n;
  };
  const ls = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    ls[k] = (localStorage.getItem(k) || '').slice(0, 60);
  }
  return {
    url: location.href,
    title: document.title,
    search_btn: count('button.search-btn'),
    modify_box: count('div[class*="modify-search-box"]'),
    city_tabs: count('ul[class*="city-picker-tabs"]'),
    dep_city: count('input[name="owDCity"]'),
    card: count('[class*="flight-item"]'),
    ls_keys: Object.keys(ls).slice(0, 20),
    ls_sample: ls,
  };
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    facts: dict[str, object] = {}
    log("=" * 70)
    log(f"落盘：{OUT}")
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

            rounds = []
            for i in (1, 2, 3):
                page.goto(HOME, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                info = page.evaluate(_PROBE_JS)
                rounds.append({"round": i, **info})
                log(f"[{i}] url={info['url'][:120]}")
                log(f"    title={info['title']!r}")
                log(f"    search_btn={info['search_btn']} modify_box={info['modify_box']} "
                    f"city_tabs={info['city_tabs']} dep_city={info['dep_city']} "
                    f"cards={info['card']}")
                log(f"    localStorage keys={info['ls_keys']}")

            facts["rounds"] = rounds

            # 第 3 轮之后：强制回到首页（goto 两次）看能否摆脱重定向
            page.goto("about:blank")
            page.wait_for_timeout(300)
            page.goto(HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            after = page.evaluate(_PROBE_JS)
            facts["after_blank"] = after
            log(f"[4] 先 about:blank 再进首页：url={after['url'][:120]} "
                f"modify_box={after['modify_box']} search_btn={after['search_btn']}")

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            page.screenshot(path=str(OUT / "01_last.png"))
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
