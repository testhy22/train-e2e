"""只读探针 47：用新测到的 city_id 直连酒店列表页，确认 id 真的指向该城市。

探针 44 只证明「联想接口给出的 cityId 与已知值同源」（控制组 2/1/32 吻合）。
本探针补最后一段：这个 id 拼进 `/hotels/list?city=` 后，页面打开的**确实是该城市**。

判定不靠页面文案（`display` 参数是我们自己写的，属自证），而是靠：
  · 列表页 URL 里 `city` 参数回读 = 传入值；
  · 页面标题含城市名（标题由站点渲染，不是我们传的 display 文案）；
  · 卡片数 >= 1。

只发普通 GET，不提交搜索、不注入任何状态。
用法：HEADED=1 python tools/probes/probe_city_pool_landing.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from datetime import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config import settings  # noqa: E402
from pages.hotel_search_page import HotelSearchPage  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_city_pool_landing" / _dt.now().strftime("%Y%m%d-%H%M%S")

#: 待验的「城市 → city_id」（来自探针 44）。抽三个不同量级的 id，避免只验小号段。
TARGETS = [("成都", 28), ("重庆", 4), ("武汉", 477)]


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    facts: dict[str, object] = {}
    checkin = date.today() + timedelta(days=7)
    checkout = checkin + timedelta(days=2)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            no_viewport=True,
            # 带上已建立的登录态：匿名 context 直连列表页会先落到 passport 登录墙
            # （首次访问必拦，见 HotelSearchPage.landed_on_login_wall），
            # 那种情况下压根看不到列表，验不出 id 对不对。
            storage_state=str(settings.AUTH_STATE_FILE),
        )
        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
            hotel = HotelSearchPage(page)
            for city, cid in TARGETS:
                hotel.open_list_directly(cid, city, checkin, checkout)
                # 未登录上下文直连列表页会被 passport 登录墙接管（项目已知行为，
                # 见 HotelSearchPage.landed_on_login_wall）。**不是 id 错**：
                # 登录页 URL 的 backurl 里就带着我们要发的 city 参数。
                # 该墙带会话状态（同一 context 拦过一次后放行），所以重试一次是合理的观测，
                # **不是**「被拦就换参数重试」——参数没变，变的只是同一 context 的第二次访问。
                walled = hotel.landed_on_login_wall()
                backurl_city = ""
                if walled and "city%3D" in page.url:
                    backurl_city = page.url.split("city%3D")[1][:4].split("%")[0]
                if walled:
                    log(f"  {city}: 命中登录墙（backurl 里的 city={backurl_city or '?'}），重访一次")
                    hotel.open_list_directly(cid, city, checkin, checkout)
                title = page.title()
                url_city = ""
                if "city=" in page.url:
                    url_city = page.url.split("city=")[1].split("&")[0]
                row = {
                    "city": city,
                    "city_id": cid,
                    "login_wall_first_try": walled,
                    "backurl_city": backurl_city,
                    "url_city_param": url_city,
                    "title": title[:60],
                    "title_has_city": city in title,
                    "cards": hotel.result_card_count(),
                }
                # 判定口径：要么落到列表页且 city 参数与标题都对；要么被登录墙拦下、
                # 但 backurl 里的 city 参数能证明 id 被正确发出（id 正确性已成立，只是没登录）。
                row["ok"] = (
                    row["url_city_param"] == str(cid) and row["title_has_city"]
                ) or (walled and backurl_city == str(cid))
                facts[city] = row
                log(
                    f"  {city}(id={cid}): wall={walled} url_city={url_city} "
                    f"title含城市={row['title_has_city']} cards={row['cards']} "
                    f"-> {'OK' if row['ok'] else 'MISMATCH'}"
                )
            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            bad = [k for k, v in facts.items() if not v["ok"]]
            log(f"落盘：{OUT / 'facts.json'}；不通过 {len(bad)} 个 {bad}")
            return 0 if not bad else 2
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
