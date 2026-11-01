"""只读探测 33：机票「城市选择面板」里可点的城市项到底是什么元素。

**为什么必须探**（用户 2026-11-01 截图 + 失败现场共同指向）：

    e2e 串联跑 szx-xiy（深圳→西安）时没跳转，失败截图显示：
      出发地输入框里是「深圳」，框上方红字「请选择出发城市」，城市选择面板正展开着。
    → **纯文本输入不被接受**，必须从面板里选中一个城市项。

而前几轮探针想点 `li[title='上海']` 时，它一直 `hidden` —— 说明面板里的可点项
根本不是那个 li（那个大概在「国际及中国港澳台」tab 的隐藏列表里）。
截图里能看到真实面板：左侧「国内 / 国际及中国港澳台」两个 tab，
右侧「热门 / ABCDEF / GHU / …」索引，下面是一格格**城市按钮**。

本探针要拿到：那个「城市按钮」的 tag + class 前缀 + 它在哪个容器里，
以及点中之后 input 的值变成什么、面板是否自动收起。
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

OUT = settings.REPORTS_DIR / "probe_flights_citypanel" / datetime.now().strftime("%Y%m%d-%H%M%S")
HOME = "https://flights.ctrip.com/online/channel/domestic"

# 找出所有「可见且文本正好是城市名」的元素，连同祖先链一起带回来。
_HIT_JS = r"""
(city) => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 3 && r.height > 3 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const chain = (el) => {
    const out = [];
    let p = el;
    for (let i = 0; i < 4 && p; i++) {
      out.push({tag: p.tagName.toLowerCase(),
                cls: (p.className || '').toString().slice(0, 90)});
      p = p.parentElement;
    }
    return out;
  };
  const hits = [];
  for (const el of document.querySelectorAll('div, li, span, a, button, dt, dd, em')) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').trim();
    if (t !== city) continue;
    const r = el.getBoundingClientRect();
    hits.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 110),
      text: t,
      x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
      title: el.getAttribute('title') || '',
      ancestors: chain(el),
    });
  }
  // 面板容器候选
  const panels = [];
  for (const el of document.querySelectorAll('div, ul')) {
    if (!vis(el)) continue;
    const cls = (el.className || '').toString();
    if (!/picker|city|panel|dropdown|suggest/i.test(cls)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 100) continue;
    panels.push({tag: el.tagName.toLowerCase(), cls: cls.slice(0, 110),
                 w: Math.round(r.width), h: Math.round(r.height),
                 text: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 90)});
  }
  return {hits, panels};
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
            page.goto(HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            dep = page.locator("input[name='owDCity']").first
            dep.click()
            page.wait_for_timeout(1200)

            dump = page.evaluate(_HIT_JS, "上海")
            facts["dep_panel"] = dump
            log(f"[1] 面板候选 {len(dump['panels'])} 个：")
            for p in dump["panels"][:8]:
                log(f"    <{p['tag']}> {p['w']}×{p['h']} cls={p['cls']!r}")
                log(f"        text={p['text'][:80]!r}")
            log(f"[2] 文本恰为「上海」的可见元素 {len(dump['hits'])} 个：")
            for h in dump["hits"][:8]:
                log(f"    <{h['tag']}> {h['w']}×{h['h']} at({h['x']},{h['y']}) "
                    f"cls={h['cls']!r} title={h['title']!r}")
                log(f"        祖先：{h['ancestors']}")
            page.screenshot(path=str(OUT / "01_panel.png"))

            # 点第一个可见的「上海」，看 input 值与面板
            clicked = None
            for h in dump["hits"]:
                sel = f"{h['ancestors'][0]['tag']}[class='{h['ancestors'][0]['cls']}']"
                loc = page.locator(sel).filter(has_text="上海").first
                try:
                    if loc.is_visible():
                        loc.click()
                        clicked = sel
                        break
                except Exception:
                    continue
            page.wait_for_timeout(1000)
            facts["clicked_selector"] = clicked
            facts["dep_value_after"] = dep.input_value()
            facts["panel_after"] = page.evaluate(_HIT_JS, "上海")["panels"]
            log(f"[3] 点击 selector={clicked!r}")
            log(f"    input 值 → {facts['dep_value_after']!r}")
            log(f"    面板剩余 {len(facts['panel_after'])} 个")
            page.screenshot(path=str(OUT / "02_after_click.png"))

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
