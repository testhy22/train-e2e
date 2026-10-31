"""探针 5：定位日历面板容器并导出其 DOM 结构。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
R: dict = {}

JS_PANEL = """
() => {
  const cands = [...document.querySelectorAll('div')].filter(e => {
    const t = e.innerText || '';
    const r = e.getBoundingClientRect();
    return t.includes('2026年10月') && t.includes('2026年11月') && r.height > 180 && r.height < 460 && r.width > 300;
  });
  if (!cands.length) return {found: false};
  const el = cands[cands.length - 1];
  return {found: true, cls: (el.className||'').toString(), id: el.id,
          html: el.outerHTML.slice(0, 2600),
          path: (() => { let p=[], n=el; while(n && n !== document.body){ p.unshift(n.tagName + (n.className ? '.' + (n.className||'').toString().split(' ')[0] : '')); n = n.parentElement; } return p.join(' > '); })()};
}
"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.set_default_timeout(8000)
    page.goto("https://trains.ctrip.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_load_state("load", timeout=20000)
    page.get_by_text("2026-10-31", exact=False).first.click()
    page.wait_for_timeout(1200)

    R["panel"] = page.evaluate(JS_PANEL)

    # 天数单元格：找文本是 1..31 且无子元素文本的叶子节点
    R["cells"] = page.evaluate(
        """() => [...document.querySelectorAll('div,span,td')].filter(e => {
             const t=(e.innerText||'').trim(); const r=e.getBoundingClientRect();
             return r.width>0 && r.height>0 && /^\\d{1,2}$/.test(t) && +t>=1 && +t<=31;
           }).slice(0,10).map(e => ({tag:e.tagName, cls:(e.className||'').toString(),
                                     data:JSON.stringify(e.dataset), text:e.innerText.trim(),
                                     attrs:[...e.attributes].map(a=>a.name).join(',')}))"""
    )

    R["arrows"] = page.evaluate(
        """() => [...document.querySelectorAll('div,span,i,a')].filter(e => {
             const t=(e.innerText||'').trim(); const r=e.getBoundingClientRect();
             return r.width>0 && r.height>0 && (t==='‹'||t==='›'||t==='<'||t==='>'||/next|prev/i.test((e.className||'').toString()));
           }).slice(0,8).map(e => ({tag:e.tagName, cls:(e.className||'').toString().slice(0,60), text:e.innerText.trim()}))"""
    )

    (OUT / "cal2.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=2)[:3000])
    ctx.close()
    browser.close()
