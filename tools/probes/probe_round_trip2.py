"""探针 2：往返结果页的**列表项分类**与**两栏归属**。

第一份探测（probe_round_trip.py）已确认：
  · 整个往返页只有一个 `div.return-box`，里面 `div.return-hd` 放「去程 / 返程」两个 h3，
    各自下挂一个 `div.return-list`；
  · `.list-item` 一共 114 个，**但第一个的文本是「精确搜索 汉口」** —— 说明 `.list-item`
    在这个页面上被复用了，不全是车次卡。这条必须查清：单程用例拿 `.list-item` 当条数
    分母，往返页直接沿用会把「精确搜索」这类卡片算进车次数。

本探针只回答三件事（只读，不点任何控件）：
  1. `.list-item` 按 className 分组 → 每种形态各多少个、第一段文本长什么样；
  2. 每个 `div.return-list` 下各有几个列表项（= 去程/返程条数）；
  3. 哪种元素带车次号（G/D/C/Z/T/K+数字 或 4 位纯数字），它的 className 是什么。

    HEADED=1 python tools/probes/probe_round_trip2.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
STATE = Path("config/auth/ctrip_state_trains.json")
BASE = "https://trains.ctrip.com/webapp/train/list"

TODAY = date.today()
D_DATE = TODAY + timedelta(days=4)
R_DATE = TODAY + timedelta(days=6)

GROUP_JS = """
() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const cls = el => (el.getAttribute('class') || '').trim();
  const TRAIN_RE = /^(?:[GDCZTK]\\d{1,4}|\\d{4})$/;

  const items = Array.from(document.querySelectorAll('.list-item'));

  // 1) 按 className 分组
  const groups = {};
  for (const el of items) {
    const key = cls(el) || '(无 class)';
    const g = groups[key] || (groups[key] = { count: 0, samples: [] });
    g.count += 1;
    if (g.samples.length < 2) g.samples.push(norm(el.innerText).slice(0, 160));
  }

  // 2) 每个 return-list 下的条目数 + 该栏标题
  const columns = Array.from(document.querySelectorAll('div.return-box div.return-list')).map((col, i) => {
    const box = col.closest('div.return-box');
    const hd = box ? box.querySelector('div.return-hd') : null;
    const heads = hd ? Array.from(hd.querySelectorAll('h3')).map(h => norm(h.innerText)) : [];
    return {
      index: i,
      head_text: heads[i] || heads[0] || '',
      item_count: col.querySelectorAll('.list-item').length,
      train_like: Array.from(col.querySelectorAll('.list-item')).filter(el => {
        const toks = norm(el.innerText).split(' ');
        return toks.some(t => TRAIN_RE.test(t));
      }).length,
      first_text: (norm(col.innerText) || '').slice(0, 120),
    };
  });

  // 3) 带车次号的元素，是什么 class
  const carriers = [];
  for (const el of document.querySelectorAll('div,span,a,strong')) {
    const t = norm(el.innerText);
    if (!t || t.length > 12) continue;
    if (!TRAIN_RE.test(t)) continue;
    if (!el.offsetHeight) continue;
    carriers.push({ text: t, tag: el.tagName.toLowerCase(), cls: cls(el).slice(0, 120),
                    parentCls: cls(el.parentElement || document.body).slice(0, 120) });
    if (carriers.length >= 10) break;
  }

  return {
    url: location.href,
    total_list_item: items.length,
    groups,
    columns,
    carriers,
  };
}
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    params = {"ticketType": "1", "dStation": "汉口", "aStation": "深圳",
              "dDate": D_DATE.isoformat(), "rDate": R_DATE.isoformat()}
    url = f"{BASE}?{urlencode(params, safe='')}"
    report: dict = {"url": url}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(STATE) if STATE.is_file() else None,
            viewport={"width": 1600, "height": 1000}, locale="zh-CN", timezone_id="Asia/Shanghai",
        )
        page = ctx.new_page()
        page.set_default_timeout(10000)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        report["structure"] = page.evaluate(GROUP_JS)
        report["title"] = page.title()
        ctx.close()
        browser.close()

    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"round_trip2_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["structure"]
    print(f"WROTE {path}\ntotal .list-item = {s['total_list_item']}")
    print("=== 按 class 分组 ===")
    for key, g in sorted(s["groups"].items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {g['count']:4d} × {key[:70]!r}")
        for sample in g["samples"][:1]:
            print(f"        样本：{sample[:90]!r}")
    print("=== 两栏 ===")
    for col in s["columns"]:
        print(f"  第{col['index']}栏 head={col['head_text']!r} items={col['item_count']} "
              f"带车次号={col['train_like']} first={col['first_text'][:60]!r}")
    print("=== 车次号承载元素 ===")
    for c in s["carriers"][:6]:
        print(f"  {c['text']!r} tag={c['tag']} cls={c['cls'][:50]!r} parent={c['parentCls'][:50]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
