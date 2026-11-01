"""只读探测 23：登录态下「搜索酒店」到底能走到哪一步。

**要回答的问题**（2026-11-01 用户要求「搜索酒店功能在登录的情况下测试一下」）：

    匿名态下首页提交 → 必落 passport 登录墙（K-04，有 probe_www_hotels_04/06 佐证）。
    那么带上 config/auth/ctrip_state.json 之后，同一条提交路径会怎样？

  可能结果三种，都必须靠现场判定，不能靠推测：
    A. 提交后正常进 hotels.ctrip.com/hotels/list，出酒店卡片  → K-04 可销号
    B. 仍落 passport                                         → 登录态没生效或站点另有门槛
    C. 被 WAF 拦 / 弹验证码                                   → blocked，另一类事实

**同时取证**（写用例前必须知道，否则断言只能靠猜）：
  · 登录态首页表单的摘要（登录后是否多出会员价/协议项，是否有浮层）
  · 提交时真实发出的 /hotels/list 参数是否与匿名态一致
  · 列表页卡片容器的 class 语义前缀是否仍是 `hotel-card`

跑法：
    python tools/probes/probe_hotel_auth_search.py            # 有头
    HEADED=0 python tools/probes/probe_hotel_auth_search.py   # 无头对照
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
from utils.auth import is_logged_in, looks_like_login_wall, state_summary  # noqa: E402
from utils.case_data import load_hotel_search_cases  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_hotel_auth" / datetime.now().strftime("%Y%m%d-%H%M%S")

# 列表页里能证明「真的出了酒店」的三类候选容器。
# 不写死一个：站点改版时先看哪一类还在，再决定用例挂哪。
CARD_CANDIDATES = (
    "[class*='hotel-card']",
    "[class*='hotel-info']",
    "[id*='hotel_list']",
    "[class*='list-item']",
)


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    case = load_hotel_search_cases()[0]
    log("=" * 70)
    log(f"落盘：{OUT}")
    log(f"state：{state}  存在={state.exists()}")
    log(f"用例：{case.id}  {case.city}  {case.checkin}→{case.checkout}  "
        f"{case.rooms}间{case.adults}成人")
    log("=" * 70)
    if not state.exists():
        log("✗ 没有 state 文件，先跑 scripts/setup_auth.py")
        return 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=settings.HEADLESS,
            args=list(settings.BROWSER_UI_ARGS),
        )
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            storage_state=str(state),
            permissions=[],          # 与 conftest 对齐：预先拒绝位置权限，免弹窗挡路
        )
        facts: dict[str, object] = {}
        try:
            page = context.new_page()
            hotel = HotelSearchPage(page)
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

            # ---------- 1. 首页：确认登录态真的加载了 ----------
            hotel.open()
            logged, evidence = is_logged_in(page)
            facts["home_logged_in"] = logged
            facts["home_evidence"] = evidence
            facts["home_url"] = page.url
            facts["overlay_log"] = hotel.overlay_log
            log(f"[1] 登录判据：{logged} / {evidence}")
            log(f"    url={page.url}  浮层={hotel.overlay_log or '无'}")
            page.screenshot(path=str(OUT / "01_home.png"))

            # ---------- 2. 填表 ----------
            hotel.set_destination(case.city)
            hotel.set_checkin(case.checkin)
            hotel.set_checkout(case.checkout)
            hotel.close_calendar()
            hotel.set_rooms(case.rooms, case.adults, case.children)
            summary = hotel.form_text()
            facts["form_summary"] = summary
            log(f"[2] 表单摘要：{summary[:220]}")
            page.screenshot(path=str(OUT / "02_form.png"))

            # ---------- 3. 提交并抓请求 ----------
            params = hotel.submit_and_capture()
            facts["submit_params"] = {k: v for k, v in params.items()}
            log(f"[3] 提交参数：city={params.get('city')} "
                f"checkin={params.get('checkin')} checkout={params.get('checkout')} "
                f"crn={params.get('crn')} adult={params.get('adult')} "
                f"children={params.get('children')}")
            log(f"    display={params.get('display')}")

            # ---------- 4. 落点 ----------
            # 给列表页一点自己的渲染时间；不用 sleep 等状态，这里只是让结果稳定可读，
            # 真正判定靠下面的 URL / 卡片数，不靠这 4 秒。
            page.wait_for_timeout(4000)
            facts["landed_url"] = page.url
            facts["landed_title"] = page.title()
            facts["login_wall"] = looks_like_login_wall(page.url)
            facts["card_counts"] = {
                sel: page.locator(sel).count() for sel in CARD_CANDIDATES
            }
            facts["body_head"] = page.locator("body").inner_text()[:600]
            facts["block_signal"] = None

            try:
                hotel.assert_not_blocked()
            except Exception as exc:  # BlockedByAntiBot
                facts["block_signal"] = f"{type(exc).__name__}: {exc}"
            facts["result_cards"] = hotel.result_card_count()

            log(f"[4] 落点：{page.url}")
            log(f"    title={page.title()!r}  登录墙={facts['login_wall']}  "
                f"block={facts['block_signal']}")
            log(f"    卡片计数：{facts['card_counts']}  result_card_count={facts['result_cards']}")
            log(f"    正文前 300：{facts['body_head'][:300]!r}")
            page.screenshot(path=str(OUT / "03_landed.png"), full_page=False)
            page.screenshot(path=str(OUT / "04_landed_full.png"), full_page=True)

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (OUT / "state_summary.txt").write_text(
                state_summary(json.loads(state.read_text(encoding="utf-8"))), encoding="utf-8"
            )
            log(f"\n落盘完成：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
