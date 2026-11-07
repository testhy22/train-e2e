"""携程「国内机票」搜索页的 Page Object。

所有定位器都来自 2026-11-01 的四轮只读探针（`tools/probes/probe_flights_*.py`，
证据在 `reports/probe_flights*/`，`reports/` 未跟踪）。写在这里的每一条都对应一次
实测踩坑，不是照模板猜的：

  出发地 / 目的地  `input.form-input-v3[name='owDCity' / 'owACity']`
                 —— 直接 type 城市名即可，站点的 JS 会把值补成「上海(SHA)」；
                    **不要**去点 `li[title='上海']`：探针 28/29 里它一直是
                   `hidden`，wait_for 8 秒后超时，而输入本身其实已经生效。
  日期框          `input[placeholder='yyyy-mm-dd']`，**readonly**，
                 `fill()` 会报 `element is not editable`，只能点开日历选。
  日历            根 `div.calendar-modal`，月份块 `div.date-picker`，块头是
                 `<span class="year">2026年</span><span class="month">11月</span>`，
                 日格 `span.date-d`（class **恒为** `date-d`，禁用/选中/区间这些状态
                 全挂在父节点 `div.date-day` 上）。
                 同屏渲染两个月 ⇒ 必须先按「年 + 月」定位块，否则全局按数字找会点到
                 另一个月里的同一天（1 月的「1」和 12 月的「1」长得一模一样）。
  日历起始月份    跟着**当前选中日期**走，不是跟着今天。上一组选了 12-01，重开日历就
                 只渲染 12 月 + 次年 1 月，`11月` 那个块压根不存在 ⇒ 必须能翻月。
                 翻月箭头 `span.next-ico` / `span.prev-ico`，**各有两个、只有一个可见**
                 （见 `_first_visible`）；「往前翻到当前月之前」时 prev 是隐藏的。
  日历不关会挡路  没选中任何日期时日历不关闭，`span.date-m` 会拦住 `button.search-btn`，
                 点搜索会重试到超时（探针 29 就是这么死的）—— 所以提交前必须确认日历已关。
                 关法：点日历外空白。**`Escape` 关不掉**（探针 39 实测，按完仍可见）。
  搜索            `button.search-btn`（是 button，不是 div）。
  结果卡片        `[class*='flight-item']`（实测 12 张）。
                 **不能用 `fake-*` 那批 class**：那是骨架屏，`fake-flight-price` 在真实
                 结果出来前就有 10 个 —— 拿它当断言条件会假绿。
  落点            `/online/list/oneway-<三字码>-<三字码>?depdate=YYYY-MM-DD&…`，
                 depdate 是页面自己生成的，属外部可验证证据。

不做的事：不点「往返」/「多程」（本期只覆盖单程）、不选舱等、不加儿童婴儿。
"""

from __future__ import annotations

import re
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Locator

from config import settings
from pages.base_page import BasePage
from utils import throttle
from utils.city_input import CityPicker, select_city_by_click
from utils.logger import logger_info, logger_warning

ONE_WAY_TEXT = "单程"


class FlightSearchPage(BasePage):
    """国内机票首页（单程）。方法只做操作与取值，判定留在测试层。"""

    DEP_CITY = "input[name='owDCity']"
    ARR_CITY = "input[name='owACity']"
    # 输入后的联想列表（探针 34 取证）：
    #   div.cflt-poi > div.cflt-poi-selector-new
    #     ├ div.poi-address > div.address.active-poi  「城市 广州CANGZ」  ← 要点的
    #     ├ div.poi-address > div.address             「机场 广州白云国际机场CAN」
    #     └ ul.airport > li                           「邻近机场：佛山沙堤机场FUO」
    # **城市行才有 `span.code`**，机场行只有 `span.city` + `span.name`。
    # 用文本点会点错：探针里按 `li:has-text('城市')` 取第一个，直接点到了
    # 「邻近机场：佛山沙堤机场」，input 值变成「广州(沙堤机场)(FUO)」。
    CITY_PANEL = "div[class*='cflt-poi']"
    CITY_ITEM = "div[class*='address']:has(span[class*='code'])"
    # 兜底：点框即弹的「热门城市网格」（不输入时才有）。联想列表没渲染出来时用它。
    CITY_GRID_PANEL = "div[class*='city-picker-selector']"
    CITY_GRID_ITEM = "li[title='{city}']"
    DATE_BOX = "input[placeholder='yyyy-mm-dd']"
    CAL_MODAL = "div.calendar-modal"
    CAL_MONTH = "div.date-picker"
    # 月份块的块头（探针 38）：<span class="year">2026年</span>
    #                      <span class="month">11月</span>
    CAL_YEAR = "span.year"
    CAL_MONTH_TITLE = "span.month"
    CAL_DAY = "span.date-d"
    # 翻月箭头（探针 37）：<span class="in-date-picker icon next-ico iconf-right">
    # 两个月份块各挂一个，只有一个可见 —— 取用时必须按可见性筛（见 _first_visible）。
    CAL_NEXT = "div.calendar-modal span[class*='next-ico']"
    CAL_PREV = "div.calendar-modal span[class*='prev-ico']"
    # `div.date-day` 上疑似「禁用」的 token 片段。⚠️[未查实] 见 select_date。
    DISABLED_TOKENS = ("disab", "not-allowed", "forbid", "invalid", "unselectable")
    MAX_MONTH_STEPS = 24
    SEARCH_BTN = "button.search-btn"
    RESULT_CARD = "[class*='flight-item']"
    HOME_PATH = "/online/channel/domestic"

    def __init__(self, page, base_url: str | None = None) -> None:
        super().__init__(page, base_url or settings.flight_base_url())

    # ------------------------------------------------------------ 导航

    def open(self) -> None:
        self.open_and_check(self.HOME_PATH)
        self.page.locator(self.DEP_CITY).first.wait_for(state="visible")

    def choose_one_way(self) -> None:
        """切「单程」。

        默认是「往返」，会多出一个「返回日期」控件；单程少一个变量，
        断言也能只盯 depdate。
        """
        self.human.click(self.page.locator("li", has_text=ONE_WAY_TEXT).first)

    # ------------------------------------------------------------ 日期

    def set_route(self, from_city: str, to_city: str) -> None:
        """填出发地 / 目的地：**必须点面板里的城市项**，不能只输入文本。

        为什么（2026-11-01 实测，用户两张截图）：只 `fill`/`type` 时
        input 上有值、站点内部变量仍是空的，点搜索后页面不跳转，
        输入框上方出现红字「请选择出发城市」。前几组数据偶尔能过，是因为
        站点有时会自己把「上海」补成「上海(SHA)」——**能不能过取决于站点心情**，
        这种「偶发通过」比稳定失败更糟。

        面板结构（探针 33 取证）：
            div.city-picker-selector
              └ div.cflt-city-picker-selector-content
                  └ div.city-picker-wrapper
                      └ div.city-picker-body
                          └ ul.cities > li[title='上海']   ← 可点项，94×32
        「国际及中国港澳台」tab 的列表里也有同名 `li[title='上海']` 且**隐藏**，
        所以选可见项这一步交给 `utils/city_input.select_city_by_click`。
        """
        for selector, city in ((self.DEP_CITY, from_city), (self.ARR_CITY, to_city)):
            logger_info(f"[机票] 输入城市 {city}")
            value, _closed = select_city_by_click(
                self.page,
                CityPicker(
                    box=selector,
                    panel=self.CITY_PANEL,
                    option=self.CITY_ITEM,
                    fallback_panel=self.CITY_GRID_PANEL,
                    fallback_option=self.CITY_GRID_ITEM,
                ),
                city,
                human=self.human,
            )
            if city not in value:
                raise ValueError(f"城市未选中：期望含 {city!r}，实际 {value!r}")

    def _read_month_head(self, block: Locator) -> tuple[int, int] | None:
        """读一个月份块的「年、月」数字。读不出来返回 None（块可能还没渲染完）。"""
        try:
            year_text = block.locator(self.CAL_YEAR).first.inner_text()
            month_text = block.locator(self.CAL_MONTH_TITLE).first.inner_text()
        except Exception:
            return None
        digits_y = "".join(ch for ch in year_text if ch.isdigit())
        digits_m = "".join(ch for ch in month_text if ch.isdigit())
        if digits_y and digits_m:
            return int(digits_y), int(digits_m)
        return None

    def shown_months(self) -> list[tuple[int, int]]:
        """日历当前可见的月份块，按页面顺序返回 [(年, 月), …]（双月视图 = 2 个）。

        用 `span.year` + `span.month` 读，而不是把整块 innerText 拿去做正则：
        块内的日格只有数字，块头之外还夹着「今天/去程/返程」这些标记，
        文本匹配迟早误伤。
        """
        out: list[tuple[int, int]] = []
        blocks = self.page.locator(self.CAL_MONTH)
        for index in range(blocks.count()):
            block = blocks.nth(index)
            try:
                if not block.is_visible():
                    continue
            except Exception:
                continue
            head = self._read_month_head(block)
            if head:
                out.append(head)
        return out

    def month_block(self, target: date) -> Locator:
        """按「年 + 月」定位月份块。

        不用 `.filter(has_text=...)`：`12月` 会同时命中 `2026年12月` 块里的日格，
        年份又可能出现在两个块上（双月视图跨年时是 `2026年12月` + `2027年1月`），
        拼字符串过滤的边界条件比「自己读一遍再比」多得多。
        """
        blocks = self.page.locator(self.CAL_MONTH)
        for index in range(blocks.count()):
            block = blocks.nth(index)
            try:
                if not block.is_visible():
                    continue
            except Exception:
                continue
            if self._read_month_head(block) == (target.year, target.month):
                return block
        raise LookupError(
            f"日历里没有 {target.year}年{target.month}月 的月份块，"
            f"当前可见 {self.shown_months()}"
        )

    def _first_visible(self, selector: str) -> Locator | None:
        """在日历里找第一个**可见**的匹配元素。

        **必须按可见性筛，不能用 `.first`。** 探针 36/37 实测：两个月份块各挂一个
        翻月箭头，DOM 里排前面那个是隐藏的；`span[class='in-date-picker icon next-ico
        iconf-right']` 精确匹配拿到的正是隐藏的那个，`click()` 直接超时。
        """
        found = self.page.locator(selector)
        for index in range(found.count()):
            candidate = found.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    def page_month(self, forward: bool) -> bool:
        """翻一个月。返回「月份块真的变了」——不是「点击没报错」。

        探针 37 的事实：往后翻点 `span.next-ico`，往前翻点 `span.prev-ico`；
        **往前那个在「首屏已经是最小可售月」时是隐藏的**（不能翻到当前月之前），
        所以「找不到可见箭头」是合法状态，返回 False 让上层决定怎么报。
        """
        before = self.shown_months()
        arrow = self._first_visible(self.CAL_NEXT if forward else self.CAL_PREV)
        if arrow is None:
            return False
        self.human.click(arrow)
        # 等月份块变化（翻页动画约 300ms），最多 3 秒。
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.shown_months() != before:
                return True
            self.page.wait_for_timeout(150)
        return False

    def select_date(self, target: date) -> str:
        """在日历里选 `target`，返回 input 的最终值（页面自己格式化后的）。

        流程（每一步都对应一次实测失败）：
          1. 点日期框开日历 —— 该框是 readonly，`fill()` 会报 not editable；
          2. **读到当前显示的月份**再决定翻页方向。不做这一步就是 e2e 第五轮的死法：
             上一组选了 12-01，日历就从 12 月起渲染，`11月` 块不存在，
             老实现直接返回 False，提交时用的还是上一组的 12-01，
             报错却是「depdate 不等于期望值」，看不出真正原因；
          3. 按「年 + 月」定位月份块，再在块内点纯数字日格 —— 双月视图里
             两个月都可能有同一个数字，全局按数字找会点错月；
          4. 收起日历并确认它真的不可见了 —— 留着它，`span.date-m` 会拦住搜索按钮。
        """
        box = self.page.locator(self.DATE_BOX).first
        self.human.click(box)
        modal = self.page.locator(self.CAL_MODAL).first
        modal.wait_for(state="visible", timeout=settings.DEFAULT_TIMEOUT_MS)

        wanted = (target.year, target.month)
        logger_info(f"[机票] 选出发日期 {target}，日历当前 {self.shown_months()}")
        for _ in range(self.MAX_MONTH_STEPS):
            shown = self.shown_months()
            if not shown:
                raise RuntimeError(f"日历打开了但读不到月份块（url={self.page.url}）")
            if wanted in shown:
                break
            forward = wanted > shown[-1]
            if not self.page_month(forward):
                raise RuntimeError(
                    f"翻月失败：目标 {wanted[0]}-{wanted[1]:02d}，"
                    f"当前可见 {shown}，方向={'后' if forward else '前'}"
                )
        else:
            raise RuntimeError(
                f"翻了 {self.MAX_MONTH_STEPS} 次月仍未出现 {wanted[0]}-{wanted[1]:02d}，"
                f"当前 {self.shown_months()}"
            )

        block = self.month_block(target)
        cell = block.locator(self.CAL_DAY).filter(
            has_text=re.compile(rf"^{target.day}$")
        ).first
        cell.wait_for(state="visible", timeout=settings.EXPECT_TIMEOUT_MS)

        # 禁用标记在**父节点** `div.date-day` 上（探针 38/40：`span.date-d` 的 class
        # 恒为 'date-d'，区别全在父节点的 token 里）。token 名无法穷举，所以这里只做
        # 「看起来像禁用就明确报错」的防御；真正判「能不能选」的是下一步回读 input。
        # ⚠️[未查实] 过去/不可售日期到底带哪个 token：2026-11-01 取证当天正好是月初
        # 1 号，本月没有过去日期可当样本，未取得实证。
        parent_cls = (cell.locator("xpath=..").get_attribute("class") or "").lower()
        if any(token in parent_cls for token in self.DISABLED_TOKENS):
            raise RuntimeError(
                f"{target} 在日历上被禁用（父节点 class={parent_cls!r}），"
                "多半是超出可售范围或已过期"
            )

        self.human.click(cell)
        self.close_calendar()

        value = box.input_value()
        if value != target.strftime("%Y-%m-%d"):
            raise RuntimeError(
                f"点完日期后输入框是 {value!r}，期望 {target.strftime('%Y-%m-%d')}"
                f"（该日可能不可售，或点到了别的月份的同一天）"
            )
        return value

    # 兼容旧调用点：等价于 select_date，但吞掉异常返回 bool。
    # 新代码请直接用 select_date —— 它会把「为什么没选上」写进异常信息。
    def set_depart_date(self, target: date) -> bool:
        try:
            self.select_date(target)
        except Exception:
            return False
        return True

    def close_calendar(self) -> bool:
        """确保日历收起，返回「是否已确认不可见」。

        **不做这一步，点搜索会一直重试到超时（探针 29 的现场）。**

        关闭方式是「点日历外的空白」，不是 Esc：探针 39 三次对照里
        `Escape` 按完 `div.calendar-modal` 仍然可见（页面自己把这个键吃掉了），
        而 `page.mouse.click(20, 20)` 与「点模态框上方的中性区」都一次生效，
        且 input 值不变、URL 不变。所以这里直接用坐标点，不再先按 Esc 白等一轮。
        """
        modal = self.page.locator(self.CAL_MODAL)
        if modal.count() == 0:
            return True
        try:
            if not modal.first.is_visible():
                return True
        except Exception:
            return True

        # 探针 39：Esc 被页面吃掉，坐标点空白一次生效；HUMAN=1 时坐标加抖动。
        self.human.click_neutral(20, 20)
        try:
            modal.first.wait_for(state="hidden", timeout=settings.EXPECT_TIMEOUT_MS)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ 提交

    def submit_and_wait(self) -> bool:
        """点搜索并等落进 `/online/list/`。返回是否落进去了。"""
        self.human.think()
        logger_info("[机票] 点击搜索")
        # 全局节流：提交是对外动作，与上一次导航/提交至少隔 N 秒。
        throttle.wait_action()
        self.human.click(self.page.locator(self.SEARCH_BTN).first)
        try:
            self.page.wait_for_url(re.compile(r"/online/list/"), timeout=settings.NAV_TIMEOUT_MS)
        except Exception:
            logger_warning(f"[机票] 没进结果页，当前 url={self.page.url[:160]}")
            return False
        self.wait_ready()
        logger_info(f"[机票] 已进结果页 {self.page.url[:160]}")
        return True

    # ------------------------------------------------------------ 结果侧取值

    @property
    def current_url(self) -> str:
        return self.page.url

    def result_card_count(self) -> int:
        """真实航班卡片数。语义前缀 `flight-item`（实测 12）。"""
        return self.page.locator(self.RESULT_CARD).count()

    def wait_result_cards(self, timeout_ms: int | None = None) -> bool:
        """等真实卡片出现。

        **明确排除骨架屏**：`fake-flight-*` 那批 class 在数据回来之前就存在，
        拿它判「有结果」会假绿（探针 29 第一次抓到的全是 fake）。
        """
        limit = timeout_ms or settings.NAV_TIMEOUT_MS
        try:
            self.page.locator(self.RESULT_CARD).first.wait_for(state="visible", timeout=limit)
        except Exception:
            return False
        return True

    def query_params(self) -> dict[str, list[str]]:
        """当前 URL 的查询参数（depdate 是页面自己生成的证据）。"""
        return parse_qs(urlparse(self.page.url).query)

    def route_in_url(self) -> str:
        """URL 里的航线片段，例如 `oneway-sha-bjs`；取不到返回空串。"""
        hit = re.search(r"/online/list/(oneway-[a-z]+-[a-z]+)", self.page.url)
        return hit.group(1) if hit else ""
