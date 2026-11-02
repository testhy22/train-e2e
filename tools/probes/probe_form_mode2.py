"""探针：把「点按钮 + 填框 + 点搜索」这条路真正走一遍（有头，会跳结果页）。

上一轮 `probe_form_mode.py` 的结论（已确认）：
  · 三态不是 radio，是 `ul.search-select > li > button`，选中态落在 `li.cur`；
  · 三个态的 **input 集合完全相同**（label-departStation / label-arriveStation /
    label-departDate / **label-returnDate** / **label-transitStation** 恒在 DOM），
    只是按态显隐 —— 所以"返回日历没探过"其实只是没人点过它。

本轮要回答四个问题：
  Q1 往返态下 `div.search-date div.date` 有几个？第 2 个点开弹的是哪个日历 wrap？
  Q2 返回日历的结构与出发日历是否同构（ul.widget-calendar-hd / ul.date-bd / li>strong）？
  Q3 中转态「指定中转城市」留空能不能搜？
  Q4 点出来的结果页 URL 是否带 ticketType=1 / 2（即与 URL 直达等价与否）？

做法：两个独立 context，各自从首页走完整流程，逐步 dump 状态；不猜定位器，
      每一步都打印现场，失败时能立刻看出卡在哪一控件。

    HEADED=1 python tools/probes/probe_form_mode2.py
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("reports/probe")
STATE = Path("config/auth/ctrip_state_trains.json")
HOME = "https://trains.ctrip.com/"

TODAY = date.today()
D_DATE = (TODAY + timedelta(days=4)).isoformat()
R_DATE = (TODAY + timedelta(days=6)).isoformat()

DATE_BLOCKS_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  return Array.from(document.querySelectorAll("div.search-date div.date")).map((el, i) => ({
    i,
    text: norm(el.innerText).slice(0, 40),
    cls: String(el.className || ""),
    visible: !!el.offsetHeight,
    box: (() => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })(),
  }));
}
"""

CALENDARS_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  return Array.from(document.querySelectorAll("[class*=calendar]")).map((el) => {
    const r = el.getBoundingClientRect();
    return {
      cls: String(el.className || "").slice(0, 70),
      visible: !!el.offsetHeight,
      box: [Math.round(r.width), Math.round(r.height)],
      heads: Array.from(el.querySelectorAll("ul.widget-calendar-hd h3")).map((h) => norm(h.innerText)).slice(0, 4),
      bodies: el.querySelectorAll("ul.date-bd").length,
      days: el.querySelectorAll("ul.date-bd li").length,
    };
  });
}
"""

TRANS_JS = r"""
() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const box = document.querySelector("div.search-trans");
  if (!box) return { found: false };
  return {
    found: true,
    visible: !!box.offsetHeight,
    html: box.outerHTML.slice(0, 1200),
    inputs: Array.from(box.querySelectorAll("input")).map((el) => ({
      id: el.id, type: el.type, placeholder: el.placeholder || "", value: el.value || "", visible: !!el.offsetHeight,
    })),
    text: norm(box.innerText).slice(0, 120),
  };
}
"""


def dump(page, label: str) -> None:
    print(f"\n--- {label} ---")
    print("  url:", page.url)
    print("  日期块:", json.dumps(page.evaluate(DATE_BLOCKS_JS), ensure_ascii=False))
    cals = [c for c in page.evaluate(CALENDARS_JS) if c["visible"] or c["days"]]
    print("  日历相关节点:")
    for c in cals[:8]:
        print(f"    {c['cls'][:55]!r} visible={c['visible']} box={c['box']} heads={c['heads']} bodies={c['bodies']} days={c['days']}")


def fill_city(page, field: str, city: str) -> None:
    """城市：点输入框 → 输入 → 点面板里完整文本匹配的项（与 POM 同法，探针自带一份以免耦合）。"""
    selector = "#label-departStation" if field == "depart" else "#label-arriveStation"
    wrapper = "div.depart-station-picker-wrap" if field == "depart" else "div.arrive-station-picker-wrap"
    box = page.locator(selector)
    box.click()
    box.fill("")
    box.type(city, delay=120)
    page.wait_for_timeout(1200)
    items = page.locator(f"{wrapper} ul.widget-search-bd li")
    for i in range(items.count()):
        cont = items.nth(i).locator("div.cont").first
        if not cont.count():
            continue
        if (cont.inner_text() or "").replace(" ", "").strip() == city:
            items.nth(i).click()
            page.wait_for_timeout(600)
            break
    mask = page.locator("div.widget-city-mask").first
    if mask.count() and mask.is_visible():
        mask.click(force=True)
        page.wait_for_timeout(300)
    print(f"    城市 {field}={box.input_value()!r}（期望 {city!r}）")


def pick_day_in(scope, target: str) -> bool:
    """在给定日历 scope 里点某天：scope 必须是可见日历年月头所在的容器。"""
    year, month, day = target.split("-")
    want_month = f"{year}年 {int(month)}月"
    want_day = str(int(day))
    heads = scope.locator("ul.widget-calendar-hd h3")
    bodies = scope.locator("ul.date-bd")
    idx = None
    for i in range(heads.count()):
        if (heads.nth(i).inner_text() or "").replace(" ", "") == want_month.replace(" ", ""):
            idx = i
            break
    print(f"    目标 {want_month} 在标题栏下标={idx}（共 {heads.count()} 个），日期栏 {bodies.count()} 个")
    if idx is None or bodies.count() <= idx:
        return False
    cells = bodies.nth(idx).locator("li")
    for i in range(cells.count()):
        strong = cells.nth(i).locator("strong").first
        if not strong.count():
            continue
        if (strong.inner_text() or "").strip() == want_day:
            cells.nth(i).click()
            page_wait(800)
            return True
    return False


def page_wait(ms: int) -> None:
    from time import sleep

    sleep(ms / 1000)


def run_round_trip(pw) -> dict:
    print("\n" + "=" * 78 + "\n【往返】首页 → 点往返 → 填两地 → 选两日期 → 搜索\n" + "=" * 78)
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(
        storage_state=str(STATE), viewport={"width": 1600, "height": 1000},
        locale="zh-CN", timezone_id="Asia/Shanghai",
    )
    page = ctx.new_page()
    out: dict = {}
    try:
        page.goto(HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        page.locator('button:text-is("往返")').first.click()
        page.wait_for_timeout(1500)
        dump(page, "点了「往返」")

        fill_city(page, "depart", "汉口")
        fill_city(page, "arrive", "深圳")

        blocks = page.locator("div.search-date div.date")
        blocks.nth(0).click()
        page.wait_for_timeout(1500)
        dump(page, "点开出发日期")
        # 出发日历：取含「出发」的那个 wrap（文本里有「出发日期」的不一定在 wrap 内，按可见+有日格筛）
        dep_wraps = [w for w in (".widget-calendar-depart-wrap",) if page.locator(w).count()]
        dep_scope = page.locator(dep_wraps[0]) if dep_wraps else page.locator("body")
        ok = pick_day_in(dep_scope, D_DATE)
        print("    出发日期点选:", ok)
        page.wait_for_timeout(800)

        # 关掉可能残留的遮罩
        for sel in ("div.widget-calendar-mask", "div.widget-city-mask"):
            m = page.locator(sel).first
            if m.count() and m.is_visible():
                m.click(force=True)
                page.wait_for_timeout(400)

        dump(page, "出发日期选完")
        print("    出发日期 input:", page.locator("#label-departDate").input_value())

        blocks = page.locator("div.search-date div.date")
        print(f"    日期块数量（选完出发后）: {blocks.count()}")
        blocks.nth(1).click()
        page.wait_for_timeout(1500)
        dump(page, "点开返回日期")

        ret_wrap = page.locator(".widget-calendar-return-wrap")
        print("    返回日历专属 wrap 存在:", ret_wrap.count())
        scope = ret_wrap if ret_wrap.count() else page.locator("body")
        ok = pick_day_in(scope, R_DATE)
        print("    返回日期点选:", ok)
        page.wait_for_timeout(800)
        for sel in ("div.widget-calendar-mask", "div.widget-city-mask"):
            m = page.locator(sel).first
            if m.count() and m.is_visible():
                m.click(force=True)
                page.wait_for_timeout(400)
        print("    返回日期 input:", page.locator("#label-returnDate").input_value())
        dump(page, "两个日期都选完")

        page.get_by_role("button", name="搜索").first.click()
        page.wait_for_timeout(6000)
        out["url"] = page.url
        out["columns"] = page.locator("div.return-box div.return-list").count()
        out["cards"] = page.locator(".card-white.list-item:not(.station-filter-bd)").count()
        print(f"\n  ▸ 结果页 url={page.url}")
        print(f"  ▸ 栏数={out['columns']} 卡数={out['cards']}")
    except Exception as exc:  # noqa: BLE001 —— 探针要把失败现场留下来
        out["error"] = repr(exc)
        print("\n  !! 失败:", repr(exc))
        try:
            page.screenshot(path=str(OUT / "form_mode2_round_trip_fail.png"), full_page=False)
        except Exception:
            pass
    finally:
        ctx.close()
        browser.close()
    return out


def run_transfer(pw) -> dict:
    print("\n" + "=" * 78 + "\n【中转】首页 → 点中转 → 填两地 → 选日期 → 搜索（中转城市留空）\n" + "=" * 78)
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(
        storage_state=str(STATE), viewport={"width": 1600, "height": 1000},
        locale="zh-CN", timezone_id="Asia/Shanghai",
    )
    page = ctx.new_page()
    out: dict = {}
    try:
        page.goto(HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        page.locator('button:text-is("中转")').first.click()
        page.wait_for_timeout(1500)
        dump(page, "点了「中转」")
        trans = page.evaluate(TRANS_JS)
        print("  指定中转城市区:", json.dumps(trans, ensure_ascii=False)[:600])
        out["trans_block"] = trans

        fill_city(page, "depart", "汉口")
        fill_city(page, "arrive", "深圳")

        blocks = page.locator("div.search-date div.date")
        print(f"    日期块数量: {blocks.count()}")
        blocks.nth(0).click()
        page.wait_for_timeout(1500)
        dump(page, "点开出发日期")
        ok = pick_day_in(page.locator(".widget-calendar-depart-wrap"), D_DATE)
        print("    出发日期点选:", ok)
        page.wait_for_timeout(800)
        for sel in ("div.widget-calendar-mask", "div.widget-city-mask"):
            m = page.locator(sel).first
            if m.count() and m.is_visible():
                m.click(force=True)
                page.wait_for_timeout(400)
        print("    出发日期 input:", page.locator("#label-departDate").input_value())

        page.get_by_role("button", name="搜索").first.click()
        page.wait_for_timeout(6000)
        out["url"] = page.url
        out["heading"] = page.locator(".transfer-box h3").first.inner_text() if page.locator(".transfer-box h3").count() else ""
        out["cards"] = page.locator(".transfer-box .card-white.list-item:not(.list-open)").count()
        print(f"\n  ▸ 结果页 url={page.url}")
        print(f"  ▸ 标题={out['heading']!r} 卡数={out['cards']}")
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
        print("\n  !! 失败:", repr(exc))
        try:
            page.screenshot(path=str(OUT / "form_mode2_transfer_fail.png"), full_page=False)
        except Exception:
            pass
    finally:
        ctx.close()
        browser.close()
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result: dict = {"d_date": D_DATE, "r_date": R_DATE}
    with sync_playwright() as pw:
        result["round_trip"] = run_round_trip(pw)
        result["transfer"] = run_transfer(pw)
    path = OUT / f"form_mode2_{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[probe] 写入 {path}")
    print("\n=== 汇总 ===")
    print("往返:", json.dumps(result["round_trip"], ensure_ascii=False)[:400])
    print("中转:", json.dumps(result["transfer"], ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
