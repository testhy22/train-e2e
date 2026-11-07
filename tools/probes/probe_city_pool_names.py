"""只读探针 46：校验「准备加进数据池」的城市名能否命中机票 / 火车票的联想面板。

为什么必须先验：数据池是按轮换随机抽的，某条城市名不被站点接受时，失败会在**以后
某次随机运行**里冒出来，看起来像「随机失败」。与其等它随机出现，不如加之前先验一遍。

**不提交搜索**：只调页面对象的城市选择方法（`set_route` / `select_city`），
选中后即返回上一个城市，不点搜索。因此不产生结果页请求。

用法：
    HEADED=1 python tools/probes/probe_city_pool_names.py
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
from pages.flight_search_page import FlightSearchPage  # noqa: E402
from pages.train_search_page import TrainSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_city_pool_names" / datetime.now().strftime("%Y%m%d-%H%M%S")

#: 机票侧要新增的城市（会作为出发地逐一试选，目的地固定「北京」）
FLIGHT_CITIES = ["青岛", "长沙", "郑州", "昆明", "武汉", "厦门", "成都", "贵阳"]
#: 火车票侧要新增的城市（同样作为出发站试选，到达站固定「北京」）
TRAIN_CITIES = ["南京", "苏州", "成都", "重庆", "武汉", "长沙"]


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {"flights": {}, "trains": {}}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            no_viewport=True,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

            log("[1] 机票城市")
            flight = FlightSearchPage(page)
            flight.open()
            for city in FLIGHT_CITIES:
                try:
                    flight.set_route(city, "北京")
                    facts["flights"][city] = {"ok": True}
                    log(f"  {city}: OK")
                except Exception as exc:  # noqa: BLE001
                    facts["flights"][city] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    log(f"  {city}: FAIL {type(exc).__name__}: {exc}")

            log("[2] 火车票城市")
            train = TrainSearchPage(page)
            train.open()
            for city in TRAIN_CITIES:
                try:
                    train.select_city("depart", city)
                    train.select_city("arrive", "北京")
                    facts["trains"][city] = {"ok": True}
                    log(f"  {city}: OK")
                except Exception as exc:  # noqa: BLE001
                    facts["trains"][city] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    log(f"  {city}: FAIL {type(exc).__name__}: {exc}")

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            bad = [k for grp in facts.values() for k, v in grp.items() if not v["ok"]]
            log(f"落盘：{OUT / 'facts.json'}；不通过 {len(bad)} 个 {bad}")
            return 0 if not bad else 2
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
