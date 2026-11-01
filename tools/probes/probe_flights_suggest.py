"""只读探测 34：机票**输入文字后**的联想列表（与「点框弹出的热门城市网格」不是同一个面板）。

**为什么还要探**（2026-11-01，e2e 第四轮全红）：

    探针 33 是在「点一下输入框、不打字」的状态下取证，拿到的是
    `div.city-picker-selector`（738×229 的热门城市网格，含「国内/国际」两个 tab）。
    但用例里的流程是 点框 → `fill("")` → `type("上海")`，此时
    `div.city-picker-selector` **不可见**（12 个候选全部 hidden），
    面板换成了另一种形态 —— 用户截图里那种带 `[城市]` / `[机场]` 分类标签的联想列表。

两者是不同的组件，selector 不能通用。本探针取后者：
  1. 联想列表的容器 class
  2. 带「城市」标签的那一行的元素结构
  3. 点击后 input 的值、以及列表是否收起

跑法：
    python tools/probes/probe_flights_suggest.py
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

OUT = settings.REPORTS_DIR / "probe_flights_suggest" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"
CITY = "广州"

_DUMP_JS = r"""
(city) => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height)};
  };
  const panels = [];
  for (const el of document.querySelectorAll('div, ul')) {
    if (!vis(el)) continue;
    const cls = (el.className || '').toString();
    if (!/suggest|dropdown|picker|city|list|panel/i.test(cls)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 150 || r.height < 60) continue;
    panels.push({tag: el.tagName.toLowerCase(), cls: cls.slice(0, 110),
                 ...rect(el),
                 text: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 120)});
  }
  const hits = [];
  for (const el of document.querySelectorAll('div, li, span, a, dd, dt, em, b')) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 40) continue;
    if (!(t.includes(city) || t.includes('城市') || t.includes('机场'))) continue;
    hits.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 100),
      text: t,
      title: el.getAttribute('title') || '',
      ...rect(el),
      parent: {tag: (el.parentElement || {}).tagName || '',
               cls: ((el.parentElement && el.parentElement.className) || '').toString().slice(0, 80)},
    });
  }
  return {panels, hits: hits.slice(0, 40)};
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    facts: dict[str, object] = {}
    log("=" * 70)
    log(f"落盘：{OUT}   目标城市 {CITY}")
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

            box = page.locator("input[name='owDCity']").first
            box.click()
            box.fill("")
            box.type(CITY, delay=120)
            page.wait_for_timeout(1500)

            dump = page.evaluate(_DUMP_JS, CITY)
            facts["after_typing"] = dump
            log(f"[1] 面板候选 {len(dump['panels'])} 个：")
            for p in dump["panels"][:10]:
                log(f"    <{p['tag']}> {p['w']}×{p['h']} at({p['x']},{p['y']}) cls={p['cls']!r}")
                log(f"        text={p['text'][:100]!r}")
            log(f"[2] 命中元素 {len(dump['hits'])} 个：")
            for h in dump["hits"][:18]:
                log(f"    <{h['tag']}> {h['w']}×{h['h']} at({h['x']},{h['y']}) "
                    f"cls={h['cls']!r} title={h['title']!r}")
                log(f"        text={h['text']!r} parent=<{h['parent']['tag']} {h['parent']['cls']!r}>")
            page.screenshot(path=str(OUT / "01_suggest.png"))

            # 试着点「带城市标签」的那一行
            cand = page.locator(
                "li:has-text('城市'), div:has-text('城市')"
            ).filter(has_text=CITY)
            facts["label_candidates"] = cand.count()
            log(f"[3] 「城市」标签候选 {cand.count()} 个")
            picked = False
            for i in range(cand.count()):
                node = cand.nth(i)
                try:
                    if node.is_visible():
                        log(f"    点第 {i} 个：{node.inner_text()[:60]!r}")
                        node.click()
                        picked = True
                        break
                except Exception as exc:
                    log(f"    第 {i} 个不可点：{type(exc).__name__}")
            page.wait_for_timeout(1200)
            facts["picked"] = picked
            facts["dep_value"] = box.input_value()
            facts["panels_after"] = page.evaluate(_DUMP_JS, CITY)["panels"]
            log(f"[4] picked={picked} input 值 → {facts['dep_value']!r}")
            log(f"    面板剩余 {len(facts['panels_after'])} 个")
            page.screenshot(path=str(OUT / "02_after_pick.png"))

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
