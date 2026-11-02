"""诊断探针：单程直达车次列表是否被站点降级成「共 0 车次」（只读）。

起因（2026-11-03 00:27）：单程轨道三条全红，报告里条目只有 5 条、点到点 0 条，
截图显示「广州-深圳 单程 2026-11-13（共0车次）」+「未找到符合条件的车次」。
8 分钟前同一条用例还是 521 条。需要区分三种可能：
  A. 与登录态有关   → 匿名 / 登录态各跑一次
  B. 与某条数据有关 → 三组不同区间与日期各跑一次
  C. 整站不可用     → 顺带看往返（ticketType=1）与中转（ticketType=2）是否正常

结论见 docs/KNOWN-LIMITATIONS.md K-13。本探针只读，不改任何状态。

    HEADED=1 python tools/probes/probe_degrade.py

产物：stdout 一张矩阵表；`reports/probe/degrade_<时间戳>.json`。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils.auth import merged_storage_state  # noqa: E402

OUT = Path("reports/probe")
STATE_WWW = Path("config/auth/ctrip_state.json")
STATE_TRAINS = Path("config/auth/ctrip_state_trains.json")

# (行程类型, 出发城市, 到达城市, 日期, 说明)
MATRIX = [
    ("0", "广州", "深圳", "2026-11-13", "单程｜出问题的那条"),
    ("0", "广州", "深圳", "2026-11-14", "单程｜换一天"),
    ("0", "上海", "北京", "2026-11-10", "单程｜换一条线"),
    ("1", "广州", "深圳", "2026-11-13", "往返｜对照组"),
    ("2", "广州", "深圳", "2026-11-13", "中转｜对照组"),
]

PROBE_JS = r"""
() => {
  const t = document.body.innerText || "";
  const m = t.match(/共\s*(\d+)\s*车次/);
  const items = Array.from(document.querySelectorAll(".list-item"))
    .map((e) => (e.innerText || "").replace(/\s+/g, " ").trim());
  const withTrain = items.filter((x) => /(^|\s)([GDCZTKYS]\d{1,4}|\d{4})(\s|$)/.test(x)).length;
  return {
    declared: m ? m[1] : null,
    items: items.length,
    with_train: withTrain,
    not_found: t.includes("未找到符合条件的车次"),
    transfer_heading: t.includes("中转方案推荐"),
    logged_in: !(t.includes("登录") && t.includes("注册")),
  };
}
"""


def url_of(ticket_type: str, from_city: str, to_city: str, day: str) -> str:
    params = {"ticketType": ticket_type, "dStation": from_city, "aStation": to_city, "dDate": day}
    if ticket_type == "1":
        params["rDate"] = day  # 往返必须带 rDate，否则页面会回填一个默认返程日
    return "https://trains.ctrip.com/webapp/train/list?" + urlencode(params, safe="")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        # 先跑匿名组，再跑登录态组 —— 两组各一个 context，避免 cookie 串味
        contexts = [
            ("匿名", None),
            ("登录态", merged_storage_state(STATE_WWW, STATE_TRAINS)),
        ]
        for label, state in contexts:
            for ticket_type, from_city, to_city, day, note in MATRIX:
                kw = {
                    "viewport": {"width": 1600, "height": 1000},
                    "locale": "zh-CN",
                    "timezone_id": "Asia/Shanghai",
                }
                if state is not None:
                    kw["storage_state"] = state
                ctx = browser.new_context(**kw)
                page = ctx.new_page()
                page.goto(url_of(ticket_type, from_city, to_city, day), wait_until="domcontentloaded")
                page.wait_for_timeout(8000)
                data = page.evaluate(PROBE_JS)
                data.update(
                    {
                        "会话": label,
                        "ticketType": ticket_type,
                        "区间": f"{from_city}→{to_city}",
                        "日期": day,
                        "说明": note,
                        "url": page.url,
                    }
                )
                rows.append(data)
                print(
                    f"[{label}] {note:<16} {from_city}→{to_city} {day} ticketType={ticket_type}"
                    f" ⇒ 共{data['declared']}车次｜.list-item={data['items']}"
                    f"｜含车次号={data['with_train']}｜未找到提示={data['not_found']}"
                    f"｜中转区={data['transfer_heading']}｜已登录={data['logged_in']}"
                )
                ctx.close()
        browser.close()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"degrade_{stamp}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[probe] 写入 {path}")
    print("判读：单程(ticketType=0)全空 + 往返/中转正常 ⇒ 站点对单程列表做了降级，"
          "不是登录态、不是单条数据、不是选择器（见 docs/KNOWN-LIMITATIONS.md K-13）")


if __name__ == "__main__":
    main()
