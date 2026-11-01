"""只读探测 24：结果页怎么「重新搜索」。

**背景（2026-11-01 用户截图指出）**：登录态提交搜索后落在
`hotels.ctrip.com/hotels/list?...`，页面上没有明显的「回到首页重新搜」入口，
用户的实际操作是 **点左侧竖排导航里的「酒店」**（`@image` 里箭头指的那一项）。
顶部搜索框虽然能改条件再搜，但它回填的是上一次的日期/人数，改起来要先清空。

本探针要取证三件事，写用例前必须知道，不能猜：
  1. 左侧导航「酒店」这一项的稳定 selector（站点 class 带构建哈希，不能写全名）；
  2. 点下去落在哪个 URL —— 回 www 首页 / 回 hotels 首页 / 只滚回顶部；
  3. 落点之后 `#hotels-destination` 表单还在不在（决定了「改条件再搜」可不可行）。

跑法：
    python tools/probes/probe_hotel_re_search.py
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
from utils.auth import is_logged_in  # noqa: E402
from utils.case_data import load_hotel_search_cases  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_hotel_research" / datetime.now().strftime("%Y%m%d-%H%M%S")

# 左侧竖排导航：只收视口最左 90px 内、可见、带短文本的元素。
_LEFT_RAIL_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.querySelectorAll('a, li, div, span')) {
    const r = el.getBoundingClientRect();
    if (r.left > 90 || r.width > 90) continue;
    if (!vis(el)) continue;
    const t = (el.innerText || el.textContent || '').trim();
    if (!t || t.length > 8) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 100),
      text: t,
      href: el.getAttribute('href') || '',
      x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
      parent_cls: (el.parentElement && el.parentElement.className || '').toString().slice(0, 80),
    });
  }
  return out;
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state = settings.auth_state_path("www")
    case = load_hotel_search_cases()[0]
    log("=" * 70)
    log(f"落盘：{OUT}   state 存在={state.exists()}")
    log("=" * 70)
    if not state.exists():
        log("✗ 没有 state 文件，先跑 scripts/setup_auth.py")
        return 0

    facts: dict[str, object] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=settings.HEADLESS, args=list(settings.BROWSER_UI_ARGS)
        )
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            storage_state=str(state),
            permissions=[],
        )
        try:
            page = context.new_page()
            hotel = HotelSearchPage(page)
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

            # ---------- 先搜一次，制造「已在结果页」这个前提 ----------
            hotel.open()
            ok, ev = is_logged_in(page)
            log(f"[1] 首页登录：{ok} / {ev}")
            hotel.set_destination(case.city)
            hotel.set_checkin(case.checkin)
            hotel.set_checkout(case.checkout)
            hotel.close_calendar()
            hotel.set_rooms(case.rooms, case.adults, case.children)
            hotel.submit_and_capture()
            ready = hotel.wait_result_list()
            facts["list_url"] = page.url
            facts["list_ready"] = ready
            facts["cards"] = hotel.result_card_count()
            log(f"[2] 结果页：ready={ready} 卡片={facts['cards']}")
            log(f"    url={page.url[:160]}")
            page.screenshot(path=str(OUT / "01_list.png"))

            # ---------- 左侧导航取证 ----------
            rail = page.evaluate(_LEFT_RAIL_JS)
            facts["left_rail"] = rail
            log(f"\n[3] 左侧元素 {len(rail)} 个：")
            for n in rail[:30]:
                log(f"    <{n['tag']}> {n['w']}×{n['h']} at({n['x']},{n['y']}) "
                    f"text={n['text']!r} href={n['href']!r}")
                log(f"        cls={n['cls']!r} parent={n['parent_cls']!r}")
            (OUT / "left_rail.json").write_text(
                json.dumps(rail, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # ---------- 点「酒店」，看落点 ----------
            # 选择器必须限定在左侧竖排导航的容器里（`lsn_first_nav_wrap`）。
            # 第一版用了 `a:has-text('酒店')`，命中的是列表页正文里某个 56×16 的
            # 「酒店」文字链接（bbox y=3568，在页面深处），落点是
            # hotels.ctrip.com/hotels/homepage（海外酒店首页，没有 #hotels-destination）
            # —— 那就是错误目标：宽松文本选择器在真实站点上必然先命中正文。
            target = None
            for cand in (
                "div[class*='lsn_first_nav_wrap']:has-text('酒店')",
                "[class*='lsn_first_nav_wrap'] >> text=酒店",
                "[class*='lsn_nav_content_wrap'] >> text=酒店",
            ):
                loc = page.locator(cand).first
                try:
                    if loc.is_visible():
                        target = (cand, loc)
                        break
                except Exception:
                    continue
            if target is None:
                log("[4] ✗ 没找到左侧「酒店」入口")
                return 0

            cand, loc = target
            box = loc.bounding_box()
            facts["click_candidate"] = cand
            facts["click_box"] = box
            log(f"\n[4] 点击 {cand!r}  bbox={box}")
            pages_before = len(context.pages)
            loc.click()
            page.wait_for_timeout(2500)

            # 左侧导航可能是 target=_blank。实测一下，别假设同页跳转。
            new_tab = len(context.pages) > pages_before
            facts["new_tab"] = new_tab
            if new_tab:
                page = context.pages[-1]
                page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
                hotel = HotelSearchPage(page)
                page.wait_for_timeout(1500)
                log(f"    新开标签页（共 {len(context.pages)} 个），已切到最新那个")

            facts["after_url"] = page.url
            facts["after_title"] = page.title()
            facts["form_visible"] = page.locator("#hotels-destination").count()
            facts["card_count_after"] = page.locator("[class*='hotel-card']").count()
            log(f"    落点：{page.url[:200]}")
            log(f"    title={page.title()!r}")
            log(f"    #hotels-destination 数量={facts['form_visible']}  "
                f"卡片={facts['card_count_after']}")
            page.screenshot(path=str(OUT / "02_after_click.png"))

            # ---------- 表单能用吗：填一次改条件 ----------
            if facts["form_visible"]:
                hotel.set_destination(case.city)
                hotel.set_checkin(case.checkin)
                hotel.set_checkout(case.checkout)
                hotel.close_calendar()
                summary = hotel.form_text()
                facts["form_summary"] = summary
                log(f"[5] 重新填表 OK：{summary[:160]}")
                page.screenshot(path=str(OUT / "03_refilled.png"))

            (OUT / "facts.json").write_text(
                json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log(f"\n落盘：{OUT}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
