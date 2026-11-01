"""探针：火车票「到达城市」面板的真实结构，定位「选南京却填成南京南」的成因。

要回答的问题（不猜，只取证）：
  1. `div.arrive-station-picker-wrap` 里有几个 `ul.widget-search-bd`？各自可见吗？
     —— 现有 `_panel()` 用 `.first`，如果面板里存在隐藏副本，它会一直取错 ul。
  2. 每个 li 的 `div.cont` 完整文本到底是什么？「南京」与「南京南」是两个 li 还是同一个？
     `div.cont` 有没有嵌套？`label.first` 会不会取到内层那个？
  3. 按现有算法（cont 文本归一化 == '南京' 就点）点下去之后，input 实际变成什么？
     如果变成「南京南」，说明点与读的不是同一个节点，或点击后面板又刷新了一次。
  4. 面板里的 li 是否在点击瞬间被重排（异步联想回来后插到前面）。

跑法：HEADED=1 python tools/probes/probe_train_city_panel.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from playwright.sync_api import sync_playwright

from config import settings

WRAPPER = "div.arrive-station-picker-wrap"
ARRIVE = "#label-arriveStation"

DUMP_JS = """
(wrapper) => {
  const root = document.querySelector(wrapper);
  if (!root) return {error: 'no wrapper'};
  const uls = [...root.querySelectorAll('ul.widget-search-bd')];
  return {
    ulCount: uls.length,
    uls: uls.map((ul, ui) => ({
      idx: ui,
      offsetHeight: ul.offsetHeight,
      display: getComputedStyle(ul).display,
      visibility: getComputedStyle(ul).visibility,
      cls: ul.className,
      liCount: ul.querySelectorAll('li').length,
      lis: [...ul.querySelectorAll('li')].slice(0, 14).map((li, i) => {
        const conts = [...li.querySelectorAll('div.cont')];
        const bb = li.getBoundingClientRect();
        return {
          i,
          liText: (li.innerText || '').replace(/\\s+/g, ''),
          contCount: conts.length,
          contTexts: conts.map(c => (c.innerText || '').replace(/\\s+/g, '')),
          contHtml: conts.length ? conts[0].innerHTML.slice(0, 160) : '',
          box: [Math.round(bb.x), Math.round(bb.y), Math.round(bb.width), Math.round(bb.height)],
        };
      }),
    })),
  };
}
"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.HEADLESS, args=settings.BROWSER_UI_ARGS)
        ctx = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            **settings.context_view_args(),
        )
        page = ctx.new_page()
        page.goto(settings.base_url(), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)

        # 关键：**先选出发城市**，复刻链路里的真实顺序。
        # 只填到达城市时面板是正确的（li[0]=南京），只有先选过「杭州」才会复现
        # 「点南京却填成南京南」——说明站点会按出发站重排到达站的联想结果。
        from pages.train_search_page import TrainSearchPage

        tr = TrainSearchPage(page)
        try:
            tr.select_city("depart", "杭州")
            print("出发城市已选：", repr(page.locator("#label-departStation").input_value()))
        except Exception as exc:                   # noqa: BLE001
            print("出发城市选取失败（继续取证到达面板）：", type(exc).__name__, exc)

        box = page.locator(ARRIVE)
        box.click()
        box.fill("")
        box.press_sequentially("南京", delay=settings.TYPE_DELAY_MS)
        page.wait_for_timeout(2500)

        dump = page.evaluate(DUMP_JS, WRAPPER)
        print("=== 面板结构 ===")
        print(json.dumps(dump, ensure_ascii=False, indent=1)[:4000])

        # 复刻生产算法：看 .first 的 ul 里有没有精确匹配项，点它，再读 input
        items = page.locator(f"{WRAPPER} ul.widget-search-bd").first.locator("li")
        n = items.count()
        print(f"\n=== 生产算法（.first ul）共 {n} 项 ===")
        hit = -1
        for i in range(n):
            label = items.nth(i).locator("div.cont").first
            if not label.count():
                continue
            t = (label.inner_text() or "").replace(" ", "").replace("\n", "")
            print(f"  [{i}] cont={t!r}")
            if t == "南京" and hit < 0:
                hit = i
        print("首个精确匹配下标:", hit)

        # 扫全部 ul（不只用 .first），看匹配项到底在第几个 ul
        allul = page.locator(f"{WRAPPER} ul.widget-search-bd")
        print(f"\n=== 全部 ul（共 {allul.count()} 个）里的匹配分布 ===")
        for u in range(allul.count()):
            lis = allul.nth(u).locator("li")
            for i in range(lis.count()):
                lab = lis.nth(i).locator("div.cont").first
                if not lab.count():
                    continue
                t = (lab.inner_text() or "").replace(" ", "").replace("\n", "")
                if t == "南京":
                    print(f"  ul[{u}] li[{i}] -> {t!r}  可见={lis.nth(i).is_visible()}")

        if hit >= 0:
            items.nth(hit).click()
            page.wait_for_timeout(1200)
            print("\n点击后 input 值 =", repr(box.input_value()))
            print("点击后 url =", page.url)
        browser.close()
    return 0


def probe_retry() -> int:
    """强制触发一次「点错」，验证 select_city 的重试 + 读回核对真的能救回来。

    为什么要有这一段：竞态不是每次都发生，跑绿一次并不能证明重试有效。
    这里把第一次点选替换成「故意点南京南」，其余全部走生产代码与真实 DOM，
    看 select_city 会不会自己纠正成「南京」。
    """
    from pages.train_search_page import TrainSearchPage, _norm

    print("=== 强制点错一次，验证重试逻辑 ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.HEADLESS, args=settings.BROWSER_UI_ARGS)
        ctx = browser.new_context(
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
            **settings.context_view_args(),
        )
        page = ctx.new_page()
        page.goto(settings.base_url(), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)

        origin = TrainSearchPage._click_option
        state = {"n": 0}

        def flaky(self, field, city):            # noqa: ANN001
            state["n"] += 1
            if state["n"] == 1 and city == "南京":
                items = self._panel(field).locator(self.SUGGEST_ITEM)
                for i in range(items.count()):
                    lab = items.nth(i).locator(self.SUGGEST_ITEM_LABEL).first
                    if lab.count() and _norm(lab.inner_text()) == "南京南":
                        print(f"  第 {state['n']} 次点选：故意点错成「南京南」")
                        items.nth(i).click()
                        return True
                return False
            print(f"  第 {state['n']} 次点选：走正常逻辑")
            return origin(self, field, city)

        TrainSearchPage._click_option = flaky     # type: ignore[method-assign]
        try:
            tr = TrainSearchPage(page)
            tr.select_city("arrive", "南京")
            value = page.locator("#label-arriveStation").input_value()
            print(f"最终 input = {value!r}｜点选次数 = {state['n']}")
            print("结论：", "重试救回来了 ✅" if value == "南京" else "重试没救回来 ❌")
        except Exception as exc:                 # noqa: BLE001
            print("抛异常：", type(exc).__name__, exc)
        finally:
            TrainSearchPage._click_option = origin  # type: ignore[method-assign]
        browser.close()
    return 0


if __name__ == "__main__":
    if "--retry" in sys.argv:
        raise SystemExit(probe_retry())
    raise SystemExit(main())
