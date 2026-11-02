"""探针：中转结果页的**可量化口径**与三组候选数据的可用性（只读）。

要回答三个问题，都不能靠猜：

1. 中转页的卡片容器到底该怎么数 —— `.transfer-box section` 是不是一张卡？
   （首轮探测只看到 `DIV.cont`，那是「全程/换乘」那一行的两段文本，数它会虚高。）
2. 卡内能不能稳定抽出**两段车次号**（`第一程 G3785` / `第二程 D7565`）——
   这是中转卡与点到点卡最本质的区别，也是断言的分母。
3. 用户给的三个日期里 `2026-11-20` 是「今天 +18」，而本仓库预售期口径是 15 天
   （`settings.MAX_PRESALE_DAYS`）—— 超期可能直接查不到，得先量出来再决定写不写进数据。

输出：reports/probe/transfer_matrix_<时间戳>.json

    HEADED=1 python tools/probes/probe_transfer2.py
"""

from __future__ import annotations

import json
import re
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

# 用户方案给的 3 组 + 一个对照：同区间换成 +12 天（预售期内）
CASES = [
    ("汉口", "深圳", 4, "用户 Case 1（= 截图 2 那条）"),
    ("北京", "上海", 13, "用户 Case 2"),
    ("广州", "东莞", 12, "用户 Case 3 的区间，日期改到预售期内做对照"),
    ("广州", "东莞", 18, "用户 Case 3 原日期 2026-11-20（+18 天）"),
]

# 抓一页的中转结果：标题、卡片数、每条卡的关键字段
#
# 选择器依据 `probe_transfer3.py` 的子树实测（2026-11-02）：
#   `.transfer-box` 是**整块容器**（含 h3 + 全部卡片），它的父才是一层 `section`；
#   卡片是 `div.card-white.list-item`（与单程/往返同一个 class，205 张/汉口→深圳）；
#   `.list-open` 是每张卡对应可展开的详情，**不是**卡片，数它会正好翻倍。
EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const box = document.querySelector(".transfer-box");
  if (!box) {
    const body = norm(document.body.innerText);
    return { has_box: false, body_head: body.slice(0, 300) };
  }
  const h3 = box.querySelector("h3");
  const cards = Array.from(box.querySelectorAll(".card-white.list-item"))
    .filter((el) => el.offsetHeight);
  const details = box.querySelectorAll(".list-open").length;
  const out = cards.map((sec) => {
    const q = (sel) => {
      const el = sec.querySelector(sel);
      return el ? norm(el.innerText) : "";
    };
    return {
      from_time: q(".from .time"),
      from_station: q(".from .station"),
      to_time: q(".to .time"),
      to_station: q(".to .station"),
      trans_station: q(".trans-station"),
      trans_all: q(".trans"),
      price: q(".rbox .price"),
      legs: Array.from(sec.querySelectorAll(".rbox .surplus-list li")).map((li) => norm(li.innerText)),
      same_station_tag: sec.querySelector(".tag-tzhc") ? norm(sec.querySelector(".tag-tzhc").innerText) : "",
      text: norm(sec.innerText).slice(0, 200),
    };
  });
  return {
    has_box: true,
    heading: h3 ? norm(h3.innerText) : "",
    card_count: cards.length,
    detail_panels: details,
    cards: out,
  };
}
"""


def leg_legs_of(legs: list[str]) -> list[str]:
    """从 `第一程 G3785 二等座有票` 这类文本里抽车次号。"""
    out = []
    for item in legs or []:
        m = re.search(r"第[一二]程\s*([GDCZTKY]?\d{1,4})", item)
        if m:
            out.append(m.group(1))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(STATE),
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()

        for frm, to, offset, label in CASES:
            d = TODAY + timedelta(days=offset)
            url = LIST_BASE + "?" + urlencode(
                {"ticketType": "2", "dStation": frm, "aStation": to, "dDate": d.isoformat()}
            )
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            try:
                data = page.evaluate(EXTRACT_JS)
            except Exception as exc:                       # noqa: BLE001
                data = {"has_box": False, "body_head": f"evaluate 失败：{exc}"}

            row = {
                "label": label,
                "from": frm,
                "to": to,
                "date": d.isoformat(),
                "offset_days": offset,
                "final_url": page.url,
                **data,
            }
            if data.get("cards"):
                cards = data["cards"]
                two_legs = [c for c in cards if len(leg_legs_of(c["legs"])) >= 2]
                row["cards_with_two_legs"] = len(two_legs)
                row["sample_cards"] = [
                    {
                        "from": f"{c['from_time']} {c['from_station']}",
                        "to": f"{c['to_time']} {c['to_station']}",
                        "trans": c["trans_station"],
                        "legs": leg_legs_of(c["legs"]),
                        "price": c["price"],
                    }
                    for c in cards[:3]
                ]
            rows.append(row)

            print(
                f"[{frm}→{to} {d}] box={data.get('has_box')} "
                f"卡={data.get('card_count')} 详情面板={data.get('detail_panels')} "
                f"两段车次号卡={row.get('cards_with_two_legs')}"
            )

        context.close()
        browser.close()

    path = OUT / f"transfer_matrix_{stamp}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[probe] 写入 {path}")

    print("\n=== 逐条摘要 ===")
    for r in rows:
        print(f"\n· {r['label']}  {r['from']}→{r['to']}  {r['date']}（+{r['offset_days']}）")
        if not r.get("has_box"):
            print(f"  ✗ 无 .transfer-box；页面开头：{r.get('body_head', '')[:160]}")
            continue
        print(f"  标题={r.get('heading')!r}  卡={r['card_count']}  详情面板={r['detail_panels']}"
              f"  含两段车次号的卡={r.get('cards_with_two_legs')}")
        for c in (r.get("sample_cards") or [])[:2]:
            print(f"    {c['from']} → {c['to']}｜{c['trans']}｜{c['legs']}｜¥{c['price']}")


if __name__ == "__main__":
    main()
