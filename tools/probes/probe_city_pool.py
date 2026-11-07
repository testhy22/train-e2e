"""只读探针 44：从酒店「目的地联想」接口取城市 id，供扩充数据池用。

为什么需要它：`data/hotels/search_cases.yaml` 的 `expected.city_id` 是**站点侧编号**，
无法从城市名推导（上海=2、北京=1、广州=32 这种映射只能实测）。现有 4 条数据的 id
是 2026-11-01 的探针跑出来的，要加城市就得同样实测，否则只能编 —— 那是本工程明令禁止的。

链路（探针 45 定位）：
    XHR `https://m.ctrip.com/restapi/soa2/21881/json/gaHotelSearchEngine`
    响应 `Response.searchResults[]`，每项含 `word`（输入词）、`cityId`、`type`（City/…）。

**不提交搜索**：只加载一次酒店首页，对每个城市「清空 → 逐字输入 → 读接口响应」，
因此不会产生 `/hotels/list` 请求，也不会触到「提交即登录墙」（见 landed_on_login_wall 注释）。

口径校验：先跑已知城市，取到的 id 必须等于 2 / 1 / 32；对不上则整份结果作废
（说明取到的不是同一个 id，不能写进数据）。

用法：
    HEADED=1 python tools/probes/probe_city_pool.py
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
from pages.hotel_search_page import HotelSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_city_pool" / datetime.now().strftime("%Y%m%d-%H%M%S")

#: 已知实测值（2026-11-01），用于校验取数口径。
CONTROLS = {"上海": 2, "北京": 1, "广州": 32}

#: 待测候选：一线之外的区域枢纽，用于扩池。
CANDIDATES = [
    "成都", "深圳", "杭州", "西安", "南京",
    "武汉", "长沙", "青岛", "厦门", "重庆",
]

API_HINT = "gaHotelSearchEngine"


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {"controls": {}, "cities": {}, "api": API_HINT}
    #: city -> 该次输入命中的 searchResults 全量（用于事后核对，不只留一个数字）
    captured: dict[str, list[dict]] = {}

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

            def on_response(resp) -> None:
                if API_HINT not in resp.url:
                    return
                try:
                    data = resp.json()
                except Exception:
                    return
                items = (data.get("Response") or {}).get("searchResults") or []
                rows = [
                    {
                        "word": it.get("word"),
                        "cityId": it.get("cityId"),
                        "type": it.get("type"),
                        "cityName": it.get("cityName"),
                        "provinceName": it.get("provinceName"),
                    }
                    for it in items
                    if isinstance(it, dict)
                ]
                if rows:
                    captured["_last"] = rows

            context.on("response", on_response)

            hotel = HotelSearchPage(page)
            hotel.open()
            box = page.locator(HotelSearchPage.DESTINATION)
            first = page.locator(HotelSearchPage.SUGGEST_ITEM).first

            def probe(city: str) -> dict:
                captured.pop("_last", None)
                hotel.human.click(box)
                box.fill("")
                hotel.human.type_text(box, city)
                first.wait_for(state="visible")
                # 面板刷新有滞后：等到第一条文本真的包含该城市再看（探针 44 第一版踩过）。
                try:
                    page.wait_for_function(
                        "(sel, city) => {const el = document.querySelector(sel);"
                        "return !!el && el.innerText.includes(city);}",
                        arg=HotelSearchPage.SUGGEST_ITEM,
                        timeout=8000,
                    )
                except Exception:
                    pass
                rows = captured.get("_last") or []
                hit = next(
                    (r for r in rows if r["word"] == city and r["type"] == "City"),
                    None,
                ) or next((r for r in rows if r["word"] == city), None)
                text = (first.inner_text() or "").strip().splitlines()[0]
                hotel.human.click(first)  # 选中，关面板
                page.wait_for_timeout(350)
                return {
                    "first_item": text,
                    "cityId": (hit or {}).get("cityId"),
                    "matched": hit,
                    "rows": rows[:6],
                }

            log("[1] 对照组（期望 2 / 1 / 32）")
            ok = True
            for city, want in CONTROLS.items():
                got = probe(city)
                got["want"] = want
                got["ok"] = got["cityId"] == want
                ok = ok and got["ok"]
                facts["controls"][city] = got
                log(f"  {city}: cityId={got['cityId']} 期望={want} {'OK' if got['ok'] else 'MISMATCH'}")

            if not ok:
                log("口径校验失败：控制组 id 与已知值不一致，候选结果不采信。")

            log("[2] 候选城市")
            for city in CANDIDATES:
                got = probe(city)
                facts["cities"][city] = got
                log(f"  {city}: cityId={got['cityId']} 第一条={got['first_item']!r}")

            page.screenshot(path=str(OUT / "01_after.png"))
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"落盘：{OUT / 'facts.json'}")
            return 0 if ok else 2
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
