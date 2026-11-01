"""只读探测 26：机票首页的 DOM 长什么样（本项目第一次碰机票）。

**为什么必须先取证**：机票模块在本工程里从未取过证，README 第 4 条写的就是「机票未实现」。
长文模板里给的 `input[placeholder*='出发']` / `button:has-text('搜索')` 是通用猜测 ——
携程的商务舱页面用的是自绘控件，placeholder 大概率是「城市/机场」而不是「出发」，
照抄模板必然全红，而且红得看不出是选择器错还是站点变了。

本探针要拿到四件事：
  1. 出发 / 到达输入框的真实 selector（id / class 前缀 / placeholder）
  2. 联想下拉项的容器 class 前缀（酒店是 `associative-item`，火车票是另一套，机票未必同）
  3. 日期控件的唤起入口与可点日期节点
  4. 「搜索」按钮是什么元素（div 还是 button）

跑法：
    python tools/probes/probe_flights_home.py
    HEADED=0 python tools/probes/probe_flights_home.py      # 无头对照
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

OUT = settings.REPORTS_DIR / "probe_flights" / datetime.now().strftime("%Y%m%d-%H%M%S")
FLIGHTS_HOME = "https://flights.ctrip.com/online/channel/domestic"

_DUMP_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height)};
  };
  const inputs = [];
  for (const el of document.querySelectorAll('input, [contenteditable="true"], [role="combobox"]')) {
    if (!vis(el)) continue;
    inputs.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      id: el.id || '',
      cls: (el.className || '').toString().slice(0, 120),
      placeholder: el.getAttribute('placeholder') || '',
      name: el.getAttribute('name') || '',
      value: el.value || '',
      ...rect(el),
    });
  }
  const clicks = [];
  for (const el of document.querySelectorAll('button, a, div, span, li')) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 12) continue;
    if (!/搜索|查询|出发|到达|单程|往返|今天|明天/.test(t)) continue;
    clicks.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 120),
      id: el.id || '',
      text: t,
      href: el.getAttribute('href') || '',
      ...rect(el),
    });
  }
  return {
    title: document.title,
    url: location.href,
    inputs,
    clicks: clicks.slice(0, 60),
    body_head: (document.body.innerText || '').slice(0, 400),
  };
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    log("=" * 70)
    log(f"落盘：{OUT}  state 存在={state.exists()}  headless={settings.HEADLESS}")
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
            page.goto(FLIGHTS_HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            data = page.evaluate(_DUMP_JS)
            log(f"title={data['title']!r}")
            log(f"url={data['url']}")
            log(f"\n[输入框 {len(data['inputs'])} 个]")
            for n in data["inputs"]:
                log(f"  <{n['tag']} type={n['type']}> {n['w']}×{n['h']} at({n['x']},{n['y']}) "
                    f"id={n['id']!r} placeholder={n['placeholder']!r} value={n['value']!r}")
                log(f"      cls={n['cls']!r} name={n['name']!r}")
            log(f"\n[可点元素 {len(data['clicks'])} 个]")
            for n in data["clicks"]:
                log(f"  <{n['tag']}> {n['w']}×{n['h']} at({n['x']},{n['y']}) text={n['text']!r}")
                log(f"      cls={n['cls']!r} id={n['id']!r} href={n['href'][:80]!r}")
            log(f"\n[正文前 300]\n{data['body_head'][:300]}")

            (OUT / "dump.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            page.screenshot(path=str(OUT / "01_flights_home.png"))
            log(f"\n落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
