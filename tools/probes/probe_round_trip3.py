"""探针 3：往返页的**时段筛选**与**车型筛选**是否可驱动、点击后 URL 怎么变。

为什么必须探这一步：用户要求把「出发时间偏好（早晨/下午/夜间）」当一个用例维度。
要做**硬断言**（点了「6点-12点」后结果必须全在该时段），前提是知道：
  1. 那个 chip 的真实 class（截图里只看到文案，看不到 DOM）；
  2. 点完之后的证据落在哪 —— URL 参数（可直接断言）还是仅前端状态（只能数列表）。
做不到硬断言就老实降级成软指标，不能凭"应该生效"写 assert。

    HEADED=1 python tools/probes/probe_round_trip3.py
"""

from __future__ import annotations

import json
import re
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

# 一栏内的「出发时间」chip：文案形如 0点-6点 / 6点-12点 / 12点-18点 / 18点-24点
CHIP_JS = """
() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const cls = el => (el.getAttribute('class') || '').trim();
  const out = { chips: [], columns: [] };
  for (const el of document.querySelectorAll('div,span,li,label,a')) {
    const t = norm(el.innerText);
    // 用 contains 而不是精确匹配：第一版按 `^\\d+点-\\d+点$` 一条都没命中（返回空数组），
    // 说明真实文本带了别的东西（图标字符 / 全角连字符 / 换行）。先用宽松匹配把候选捞出来，
    // 再看它们的 className 长什么样 —— 探针的第一职责是"看见"，不是"筛对"。
    if (!/\\d{1,2}点\\s*[-~—－]\\s*\\d{1,2}点/.test(t) || t.length > 24 || !el.offsetHeight) continue;
    const col = el.closest('div.return-list');
    out.chips.push({ text: t, tag: el.tagName.toLowerCase(), cls: cls(el).slice(0, 120),
                     parentCls: cls(el.parentElement || document.body).slice(0, 120),
                     colIndex: col ? Array.from(document.querySelectorAll('div.return-box div.return-list')).indexOf(col) : -1 });
    if (out.chips.length >= 12) break;
  }
  const cols = Array.from(document.querySelectorAll('div.return-box div.return-list'));
  cols.forEach((col, i) => {
    out.columns.push({ index: i, cls: cls(col).slice(0, 120),
                       head: norm(col.innerText).slice(0, 24) });
  });
  return out;
}
"""

# 点完之后：URL + 每条卡的出发时间（卡片首段是 HH:MM）
AFTER_JS = """
() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const cols = Array.from(document.querySelectorAll('div.return-box div.return-list'));
  return {
    url: location.href,
    columns: cols.map((col, i) => {
      const items = Array.from(col.querySelectorAll('.card-white.list-item'))
        .filter(el => !el.className.includes('station-filter'));
      const times = items.map(el => (norm(el.innerText).match(/^\\d{1,2}:\\d{2}/) || [''])[0])
        .filter(Boolean);
      return { index: i, count: items.length, times: times.slice(0, 12) };
    }),
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
        report["before"] = page.evaluate(CHIP_JS)

        clicked = None
        try:
            # 只点**去程栏**的「6点-12点」：两栏各有一套同名 chip，全局 .first 会点到去程
            # 也可能点到返程，这里显式限定在第 0 栏内。
            col = page.locator("div.return-box div.return-list").first
            chip = col.get_by_text("6点 - 12点", exact=True).first
            chip.click()
            clicked = "6点 - 12点 @ 第0栏"
            page.wait_for_timeout(4000)
        except Exception as exc:                     # noqa: BLE001
            report["click_error"] = f"{type(exc).__name__}: {exc}"
        report["clicked"] = clicked
        report["after"] = page.evaluate(AFTER_JS)
        report["title"] = page.title()

        ctx.close()
        browser.close()

    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"round_trip3_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"WROTE {path}")
    print("=== 时段 chip ===")
    for c in report["before"]["chips"][:8]:
        print(f"  col={c['colIndex']} {c['text']!r} tag={c['tag']} cls={c['cls'][:60]!r} parent={c['parentCls'][:40]!r}")
    print(f"=== 点击：{clicked} err={report.get('click_error')}")
    after = report["after"]
    print("  URL:", after["url"])
    for c in after["columns"]:
        print(f"  第{c['index']}栏 count={c['count']} 前几个出发时间={c['times'][:8]}")
    # 直接给出「时段是否真的生效」的判定，省得回头再算
    first = after["columns"][0]["times"] if after["columns"] else []
    hours = [int(t.split(":")[0]) for t in first if re.match(r"^\d{1,2}:\d{2}$", t)]
    inside = [h for h in hours if 6 <= h < 12]
    print(f"  第0栏 前 {len(hours)} 条落在 6-12 点：{len(inside)}/{len(hours)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
