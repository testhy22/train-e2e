"""携程火车票查询页 POM。

本文件里每一个定位器都来自 2026-10-31 的实机探测（reports/probe/*.json），不是按经验猜的。
探测方法与结论见 README「定位器来源」。

职责边界（对应工程规则）：
  - 只做「操作 + 取状态」，不做业务断言。assert 全部留在 tests/。
  - 等待一律用 expect / wait_for，本文件不允许出现 time.sleep 与 wait_for_timeout。
  - 风控拦截抛 BlockedByAntiBot；控件唤不起来抛 WidgetUnavailable。两者语义不同，上层分别处理。
"""

from __future__ import annotations

import re

from playwright.sync_api import Locator, Page, expect

from config import settings
from pages.base_page import BasePage


class WidgetUnavailable(RuntimeError):
    """页面上的自定义控件没有按预期响应（例如日历没弹出来）。

    与 blocked 区分开：blocked 是对方主动拒绝，这里是控件行为不符合预期。
    两者都不算通过，但原因不同，报告里必须能分辨。
    """


class TrainSearchPage(BasePage):
    """火车票首页 + 结果页。"""

    # ---------------------------------------------------------------- 定位器（集中管理）

    # 出发/到达城市输入框：探测确认 id 稳定，且 placeholder 可读
    DEPART_CITY = "#label-departStation"
    ARRIVE_CITY = "#label-arriveStation"

    # 日期：可见文本是 <strong>2026-10-31</strong>，真实 <input> 被 assist-* 类隐藏
    DATE_INPUT = "#label-departDate"
    DATE_MODULE = "div.search-date"

    # 日历是自定义 widget + 遮罩层（探测时遮罩会拦住搜索按钮的点击）
    CALENDAR = ".widget-calendar-depart-wrap"
    CALENDAR_MASK = ".widget-calendar-mask"

    # 结果页
    RESULT_ITEM = ".list-item"
    SEARCH_BUTTON_NAME = "搜索"

    # 车次号形态：G/D/C/Z/T/K + 1~4 位数字
    TRAIN_NO_RE = re.compile(r"^[GDCZTK]\d{1,4}$")
    RESULT_COUNT_RE = re.compile(r"共\s*(\d+)\s*车次")
    DATE_IN_URL_RE = re.compile(r"[?&]dDate=(\d{4}-\d{2}-\d{2})")

    def __init__(self, page: Page, base_url: str | None = None) -> None:
        super().__init__(page, base_url)

    # ---------------------------------------------------------------- 页面动作

    def open(self) -> None:
        self.open_and_check("/")

    def search_button(self) -> Locator:
        return self.page.get_by_role("button", name=self.SEARCH_BUTTON_NAME).first

    def select_city(self, field: str, city: str) -> None:
        """在城市输入框里选中一个城市。

        field: "depart" | "arrive"

        策略（先 fill，不行再退逐字输入）：
          1. fill(city) —— 实测能唤起联想面板（见 probe2.json: fill.suggest_visible=true）。
          2. 等联想项出现，点与城市名精确匹配的那一项。
          3. 若面板里找不到精确项，回退 press_sequentially(delay=...) 逐字输入再找一次。
             这一步是**功能必要**（部分情况下 fill 只触发本地过滤、不触发联想接口，
             面板会显示「对不起，找不到：广州」，见 probe 记录），**不是规避检测的手段**。
          4. 最后核对输入框的实际值，值不对就直接抛错，不带着错状态往下走。
        """
        selector = self.DEPART_CITY if field == "depart" else self.ARRIVE_CITY
        box = self.page.locator(selector)
        expect(box).to_be_visible()

        box.click()
        box.fill("")
        box.fill(city)

        if not self._click_option(city):
            box.fill("")
            box.press_sequentially(city, delay=settings.TYPE_DELAY_MS)
            if not self._click_option(city):
                raise WidgetUnavailable(
                    f"{field} 城市下拉未出现「{city}」的精确匹配项（fill 与逐字输入都试过）"
                )

        actual = box.input_value().strip()
        if actual != city:
            raise WidgetUnavailable(f"{field} 城市输入后值为 {actual!r}，期望 {city!r}")

    def _click_option(self, city: str) -> bool:
        """点下拉里的精确匹配项。找不到返回 False，不抛错（由调用方决定退路）。"""
        option = self.page.get_by_text(city, exact=True).first
        try:
            expect(option).to_be_visible(timeout=settings.EXPECT_TIMEOUT_MS)
        except AssertionError:
            return False
        option.click()
        return True

    def select_date(self, depart_date: str) -> None:
        """在日历控件里点选出发日期。depart_date 形如 2026-11-07。

        注意：本方法依赖自定义日历 widget。2026-10-31 实测中，该 widget 的唤起
        不稳定（同一天内多次运行，前两次能弹出、后续几次点不开），因此这里不做重试，
        唤不起就抛 WidgetUnavailable，由用例标记 xfail 并留证据 —— 不假装选中了。
        """
        target_month, target_day = self._split_date(depart_date)

        trigger = self.page.locator(self.DATE_INPUT)
        if not trigger.is_visible():
            trigger = self.page.get_by_text(depart_date, exact=False).first

        trigger.click(force=True)

        calendar = self.page.locator(self.CALENDAR)
        try:
            expect(calendar).to_be_visible(timeout=settings.EXPECT_TIMEOUT_MS)
        except AssertionError as exc:
            raise WidgetUnavailable(
                f"日历未唤起（{self.CALENDAR} 仍不可见），无法点选 {depart_date}"
            ) from exc

        self._goto_month(target_month)
        self._click_day(target_month, target_day)

        # 关掉日历（遮罩不关会拦住搜索按钮）
        self._close_calendar()

        actual = self.page.locator(self.DATE_INPUT).input_value().strip()
        if actual != depart_date:
            raise WidgetUnavailable(f"点选后日期为 {actual!r}，期望 {depart_date!r}")

    def _goto_month(self, month_label: str) -> None:
        """翻到目标月份。widget 是双月视图，目标常在右栏，翻页只在必要时发生。"""
        for _ in range(3):
            if self._month_visible(month_label):
                return
            nxt = self.page.locator(self.CALENDAR).get_by_text("›", exact=True).first
            if not nxt.is_visible():
                break
            nxt.click()
        if not self._month_visible(month_label):
            raise WidgetUnavailable(f"日历里翻不到 {month_label}")

    def _month_visible(self, month_label: str) -> bool:
        return self.page.locator(self.CALENDAR).get_by_text(month_label, exact=False).first.is_visible()

    def _click_day(self, month_label: str, day: str) -> None:
        """点具体某一天。日期格子里同一天会出现两次（双月视图），必须限定在目标月份那一栏。"""
        column = self.page.locator(self.CALENDAR).locator("div,table").filter(has_text=month_label)
        candidates = column.last.get_by_text(day, exact=True)
        if candidates.count() == 0:
            raise WidgetUnavailable(f"{month_label} 里找不到 {day} 日")
        candidates.first.click()

    def _close_calendar(self) -> None:
        mask = self.page.locator(self.CALENDAR_MASK).first
        if mask.is_visible():
            mask.click(force=True)
            expect(mask).to_be_hidden(timeout=settings.EXPECT_TIMEOUT_MS)

    def submit(self) -> None:
        """点搜索并等结果页。"""
        self.search_button().click()
        self.page.wait_for_url(re.compile(r"/webapp/train/list"), timeout=settings.NAV_TIMEOUT_MS)
        self.wait_ready()
        self.assert_not_blocked()

    # ---------------------------------------------------------------- 取状态（不断言）

    def result_items(self) -> list[Locator]:
        """结果列表项。只返回元素，是否达标由用例判断。"""
        locator = self.page.locator(self.RESULT_ITEM)
        try:
            expect(locator.first).to_be_visible(timeout=settings.EXPECT_TIMEOUT_MS)
        except AssertionError:
            return []
        return locator.all()

    def item_train_numbers(self, items: list[Locator]) -> list[str]:
        """从每个列表项的文本里抽车次号。抽不到的不返回，由用例判定覆盖率。"""
        numbers: list[str] = []
        for item in items:
            text = item.inner_text()
            for token in text.split():
                if self.TRAIN_NO_RE.match(token):
                    numbers.append(token)
                    break
        return numbers

    def declared_result_count(self) -> int | None:
        """页面自称的车次数（「共 557 车次」）。与真实列表条数分开，不混用。"""
        text = self.page.locator("body").inner_text()
        match = self.RESULT_COUNT_RE.search(text)
        return int(match.group(1)) if match else None

    def url_depart_date(self) -> str | None:
        """结果页 URL 里的 dDate —— 用它验证「日期真的生效了」。"""
        match = self.DATE_IN_URL_RE.search(self.page.url)
        return match.group(1) if match else None

    @staticmethod
    def _split_date(depart_date: str) -> tuple[str, str]:
        year, month, day = depart_date.split("-")
        return f"{year}年{int(month)}月", str(int(day))
