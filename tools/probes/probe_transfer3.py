"""探针：把 `.transfer-box` 的子树结构打出来（只读）。

为什么还要再探一次：前两次的层级判断是矛盾的 ——
  · `probe_transfer.py` 里那条链打印的是 `DIV.transfer-box > SECTION > DIV.train-content`
    （**从内往外**），说明 `.transfer-box` 可能只是**一张卡**；
  · 但 `H3「中转方案推荐」` 的父又是 `.transfer-box`，而标题在页面上只出现一次。
两者不能同时成立，必须看真实子树。

做法：抓 `.transfer-box` 的直接子/孙的 tag+class+子元素数+文本开头，
并统计全页重复出现、且文本含「换乘」的 class 组合 —— 重复次数就是卡片数。
不做任何点击。

    HEADED=1 python tools/probes/probe_transfer3.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
STATE = Path("config/auth/ctrip_state_trains.json")
LIST_BASE = "https://trains.ctrip.com/webapp/train/list"
TODAY = date.today()
D_DATE = TODAY + timedelta(days=4)

TREE_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const desc = (el) => ({
    tag: el.tagName,
    cls: String(el.className || "").slice(0, 80),
    kids: el.children.length,
    text: norm(el.innerText).slice(0, 70),
    hasTrans: norm(el.innerText).includes("换乘"),
  });
  const box = document.querySelector(".transfer-box");
  const out = { box_found: !!box, box_count: document.querySelectorAll(".transfer-box").length };
  if (!box) {
    out.body_head = norm(document.body.innerText).slice(0, 300);
    return out;
  }
  out.box = desc(box);
  out.box_children = Array.from(box.children).map(desc);
  out.box_grandchildren = [];
  for (const c of box.children) {
    for (const g of c.children) out.box_grandchildren.push({ parent: desc(c).cls, ...desc(g) });
  }
  // 从 box 往上 6 层，看清它被谁包着
  out.ancestors = [];
  let cur = box;
  for (let i = 0; i < 6 && cur; i++, cur = cur.parentElement) {
    out.ancestors.push(desc(cur));
  }
  // 全页重复 class 组合统计：同一 tag+class 出现 >=2 次且含「换乘」
  const counter = new Map();
  for (const el of document.querySelectorAll("*")) {
    if (!el.offsetHeight) continue;
    const t = norm(el.innerText);
    if (!t.includes("换乘")) continue;
    const key = el.tagName + "." + String(el.className || "");
    const rec = counter.get(key) || { key, n: 0, text: t.slice(0, 90) };
    rec.n += 1;
    counter.set(key, rec);
  }
  out.repeated_transfer_containers = Array.from(counter.values())
    .filter((r) => r.n >= 2)
    .sort((a, b) => b.n - a.n)
    .slice(0, 8);
  out.html_head = box.outerHTML.slice(0, 3000);
  return out;
}
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(STATE),
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        url = LIST_BASE + "?" + urlencode(
            {"ticketType": "2", "dStation": "汉口", "aStation": "深圳", "dDate": D_DATE.isoformat()}
        )
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        data = page.evaluate(TREE_JS)
        data["url"] = page.url
        data["date"] = D_DATE.isoformat()
        path = OUT / f"transfer_tree_{stamp}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()
        browser.close()

    print(f"[probe] 写入 {path}\n")
    print("box 存在:", data.get("box_found"), "｜页面里 .transfer-box 个数:", data.get("box_count"))
    if not data.get("box_found"):
        print("页面开头:", data.get("body_head"))
        return
    print("\n=== .transfer-box 自身 ===")
    print(" ", data["box"])
    print("\n=== 直接子元素 ===")
    for c in data["box_children"]:
        print(f"  {c['tag']}.{c['cls'][:50]!r} kids={c['kids']} 换乘={c['hasTrans']} {c['text'][:60]!r}")
    print("\n=== 祖先链（由内往外）===")
    for a in data["ancestors"][:6]:
        print(f"  {a['tag']}.{a['cls'][:55]!r} kids={a['kids']}")
    print("\n=== 全页重复、且含「换乘」的容器 ===")
    for r in data["repeated_transfer_containers"]:
        print(f"  n={r['n']:>3}  {r['key'][:60]!r}  {r['text'][:60]!r}")
    print("\n=== outerHTML 开头 ===")
    print(data["html_head"][:1200])


if __name__ == "__main__":
    main()
