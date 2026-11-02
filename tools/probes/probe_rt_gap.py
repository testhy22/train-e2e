"""诊断探针：往返去程里「抽不到车次号」的卡片到底长什么样。

背景（2026-11-02）：往返改走首页表单后，`rt-hk-sz-weekend-T+3`（汉口→深圳 2026-11-06）
去程覆盖率只有 78%（43/55），低于 90% 门槛；同一区间改前（URL 直达）是全绿。
URL 参数、栏数、日期都对得上，所以要看的不是"有没有进对页"，而是"这 12 张卡是什么"。

做法：走 URL 直达（诊断用途，不冒充用例的 UI 路径），把两栏逐条打印：
  覆盖 / 抽不到 / 中转判定，以及抽不到那几条的**原文前 120 字**。
再对比一次"走表单"的结果，确认两条路径拿到的列表是否一致。

    HEADED=1 python tools/probes/probe_rt_gap.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

# 探针从 tools/probes/ 直接跑时，项目根不在 sys.path 上
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pages.train_search_page import TrainSearchPage  # noqa: E402

OUT = Path("reports/probe")
STATE = Path("config/auth/ctrip_state_trains.json")
FROM, TO = "汉口", "深圳"
D_DATE, R_DATE = "2026-11-06", "2026-11-08"


def analyze(train: TrainSearchPage) -> dict:
    out = {}
    for leg, name in ((train.LEG_DEPART, "去程"), (train.LEG_RETURN, "返程")):
        texts = train.leg_texts(leg)
        rows = []
        for t in texts:
            number = train.train_number_of(t)
            rows.append(
                {
                    "number": number,
                    "transfer": train.is_transfer_text(t),
                    "text": " ".join((t or "").split())[:120],
                }
            )
        missing = [r for r in rows if not r["number"] and not r["transfer"]]
        out[name] = {
            "total": len(texts),
            "numbers": sum(1 for r in rows if r["number"]),
            "transfer": sum(1 for r in rows if r["transfer"]),
            "missing": len(missing),
            "missing_samples": missing[:14],
        }
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result: dict = {"from": FROM, "to": TO, "d": D_DATE, "r": R_DATE}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(STATE), viewport={"width": 1600, "height": 1000},
            locale="zh-CN", timezone_id="Asia/Shanghai",
        )
        page = ctx.new_page()
        train = TrainSearchPage(page)
        # 诊断专用：直接拼 URL 打开，**不往 POM 里加方法** —— 那会让「必须点按钮」
        # 的裁决在代码里出现第二条路，探针自己拼完就算了。
        query = urlencode(
            {"ticketType": "1", "dStation": FROM, "aStation": TO, "dDate": D_DATE, "rDate": R_DATE},
            safe="",
        )
        page.goto(f"https://trains.ctrip.com/webapp/train/list?{query}", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        result["url_path"] = analyze(train)
        result["url"] = page.url
        ctx.close()
        browser.close()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"rt_gap_{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[probe] {page.url if False else result['url']}")
    for leg in ("去程", "返程"):
        rec = result["url_path"][leg]
        print(
            f"\n=== {leg}：{rec['total']} 条，车次号 {rec['numbers']}，"
            f"中转 {rec['transfer']}，抽不到 {rec['missing']} ==="
        )
        for i, row in enumerate(rec["missing_samples"], 1):
            print(f"  ({i}) {row['text']!r}")
    print(f"\n[probe] 写入 {path}")


if __name__ == "__main__":
    main()
