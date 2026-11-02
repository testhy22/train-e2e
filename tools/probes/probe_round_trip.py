"""探针：携程火车票**往返**结果页 DOM 结构（只读，不改表单、不提交搜索）。

背景（为什么需要这一份探测）：
  往返搜索要走「去程 + 返程」双列表，而现有 `pages/train_search_page.py` 的全部定位器
  都来自**单程**探测（`.list-item` / `共 N 车次` / `?dDate=`）。往返页多出一个
  `rDate` 参数与第二栏列表，直接复用单程定位器等于假设「两栏共用同一个类名」——
  这正是 K-11「点南京填成南京南」那类靠猜踩出来的坑。

做法：直接用 URL 打开结果页（参数口径来自 2026-11-02 用户截图里的真实地址
  `.../webapp/train/list?ticketType=1&dStation=…&aStation=…&dDate=…&rDate=…`），
  只读 DOM，不点任何筛选、不点预订。

为什么有头：KNOWN-LIMITATIONS K-05 实测无头下整站 432 被拦，无头探测拿不到 DOM。

输出：reports/probe/round_trip_<时间戳>.json（reports/ 未跟踪，不入库）。

    HEADED=1 python tools/probes/probe_round_trip.py
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

# 参数用「今天 +N」现算：写死日期第二天就是过去时（预售期 15 天，见 settings）。
TODAY = date.today()
D_DATE = TODAY + timedelta(days=4)
R_DATE = TODAY + timedelta(days=6)

# 页面自己在 URL 里的类目文案：gaotie-dongche 是截图里被截断的 trainType 的常见取值之一，
# 探不准就留空，让页面按默认全车型渲染 —— 本次要探的是「列表结构」，不是筛选项的枚举值。
CANDIDATES = [
    {"ticketType": "1", "dStation": "汉口", "aStation": "深圳",
     "dDate": D_DATE.isoformat(), "rDate": R_DATE.isoformat()},
]

# 把「结构 + 归属」一次问回：列表项各在哪个容器下、容器文案是什么。
STRUCT_JS = """
() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const cls = el => (el.getAttribute('class') || '');
  const pick = el => ({
    tag: el.tagName.toLowerCase(),
    cls: cls(el).slice(0, 160),
    id: el.id || '',
  });
  const chain = (el, depth = 6) => {
    const out = [];
    let node = el;
    while (node && node !== document.body && out.length < depth) {
      out.push(pick(node));
      node = node.parentElement;
    }
    return out;
  };

  // 1) 列表项：单程页用的 .list-item，往返页是否还在、总共有多少
  const items = Array.from(document.querySelectorAll('.list-item'));
  const itemSample = items.slice(0, 3).map(el => ({
    text: norm(el.innerText).slice(0, 240),
    chain: chain(el),
  }));

  // 2) 「去程 / 返程」标题锚点：找可见文本恰为去程/返程的元素
  const heads = [];
  for (const el of document.querySelectorAll('div,span,h1,h2,h3,li')) {
    const t = norm(el.innerText);
    if ((t === '去程' || t === '返程' || t === '往返购票') && el.offsetHeight) {
      heads.push({ text: t, ...pick(el), chain: chain(el) });
    }
  }

  // 3) 含「去程/返程」的容器候选（class 里带 box / list / listBox 的）
  const containers = [];
  for (const el of document.querySelectorAll("[class*='box'],[class*='listBox'],[class*='list-box']")) {
    const t = norm(el.innerText);
    if (!el.offsetHeight || t.length < 20) continue;
    containers.push({
      ...pick(el),
      textLen: t.length,
      head: t.slice(0, 60),
      items: el.querySelectorAll('.list-item').length,
    });
    if (containers.length >= 24) break;
  }

  // 4) 车型筛选控件（截图里有 高铁(G/C) / 动车(D) / 普通(Z/T/K) / 其他(L/Y)）
  const filters = [];
  for (const el of document.querySelectorAll('label,div,span')) {
    const t = norm(el.innerText);
    if (!/^高铁|^动车|^普通|^其他/.test(t) || t.length > 16 || !el.offsetHeight) continue;
    const input = el.querySelector('input');
    filters.push({
      text: t, ...pick(el),
      hasInput: !!input,
      inputType: input ? input.type : '',
      checked: input ? input.checked : null,
      inputCls: input ? cls(input).slice(0, 80) : '',
    });
    if (filters.length >= 12) break;
  }

  // 5) 日期切换条（去程 11-05/11-06…，返程 12-08/12-09…）
  const dateBars = [];
  for (const el of document.querySelectorAll("[class*='date'],[class*='day']")) {
    const t = norm(el.innerText);
    if (!/周[一二三四五六日]/.test(t) || t.length > 120 || !el.offsetHeight) continue;
    dateBars.push({ ...pick(el), text: t.slice(0, 120) });
    if (dateBars.length >= 12) break;
  }

  return {
    url: location.href,
    title: document.title,
    bodyHead: norm(document.body.innerText).slice(0, 400),
    declared: (norm(document.body.innerText).match(/共\\s*\\d+\\s*车次/g) || []).slice(0, 4),
    itemCount: items.length,
    itemSample,
    heads,
    containers,
    filters,
    dateBars,
  };
}
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"candidates": [], "d_date": D_DATE.isoformat(), "r_date": R_DATE.isoformat()}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        kwargs = dict(viewport={"width": 1600, "height": 1000}, locale="zh-CN",
                      timezone_id="Asia/Shanghai")
        ctx = browser.new_context(storage_state=str(STATE) if STATE.is_file() else None, **kwargs)
        page = ctx.new_page()
        page.set_default_timeout(10000)

        for params in CANDIDATES:
            url = f"{BASE}?{urlencode(params, safe='')}"
            entry: dict = {"params": params, "url": url}
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)          # 列表首屏渲染；探针里允许固定等
                entry["structure"] = page.evaluate(STRUCT_JS)
                entry["has_login_wall"] = "passport" in page.url
            except Exception as exc:                  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            report["candidates"].append(entry)

        ctx.close()
        browser.close()

    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"round_trip_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {path}")
    for entry in report["candidates"]:
        s = entry.get("structure") or {}
        print(f"url={entry['url']}")
        print(f"  error={entry.get('error')} login_wall={entry.get('has_login_wall')}")
        print(f"  title={s.get('title')!r} items={s.get('itemCount')} declared={s.get('declared')}")
        print(f"  heads={[h['text'] + '|' + h['cls'] for h in (s.get('heads') or [])][:6]}")
        print(f"  filters={[f['text'] + '|checked=' + str(f['checked']) for f in (s.get('filters') or [])][:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
