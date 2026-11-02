"""探针 4：核验往返数据里**最不确定的两个区间**到底有几条车次。

为什么单独跑这一趟：
  `data/trains/round_trip_cases.yaml` 里 `min_results_each_leg` 是个**下界断言**，
  只有汉口→深圳有实测（去程 55 / 返程 57）。同城两站（汉口↔武昌）这条我不敢按
  "武汉三镇之间有城际"的常识写数字 —— 常识不是证据，写成 2 就会让用例第一次跑就红。

本探针只数条数，不点任何控件，输出每个区间的（去程条数 / 返程条数）。

    HEADED=1 python tools/probes/probe_round_trip_verify.py
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
D_DATE = (TODAY + timedelta(days=5)).isoformat()
R_DATE = (TODAY + timedelta(days=6)).isoformat()

PAIRS = [("汉口", "武昌"), ("武昌", "汉口")]

COUNT_JS = """
() => {
  const cols = Array.from(document.querySelectorAll('div.return-box div.return-list'));
  return cols.map(col => {
    const cards = Array.from(col.querySelectorAll('.card-white.list-item'))
      .filter(el => !(el.className || '').includes('station-filter'));
    return { count: cards.length, head: (col.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 30) };
  });
}
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"d_date": D_DATE, "r_date": R_DATE, "pairs": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(STATE) if STATE.is_file() else None,
            viewport={"width": 1600, "height": 1000}, locale="zh-CN", timezone_id="Asia/Shanghai",
        )
        page = ctx.new_page()
        page.set_default_timeout(10000)
        for frm, to in PAIRS:
            url = f"{BASE}?" + urlencode(
                {"ticketType": "1", "dStation": frm, "aStation": to, "dDate": D_DATE, "rDate": R_DATE},
                safe="",
            )
            entry: dict = {"from": frm, "to": to}
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(4500)
                entry["columns"] = page.evaluate(COUNT_JS)
                entry["title"] = page.title()
            except Exception as exc:                  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            report["pairs"].append(entry)
            cols = entry.get("columns") or []
            print(f"{frm}→{to}: " + (
                " | ".join(f"第{i}栏 {c['count']} 条 ({c['head'][:20]})" for i, c in enumerate(cols))
                or f"ERROR {entry.get('error')}"
            ))
        ctx.close()
        browser.close()

    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"round_trip_verify_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
