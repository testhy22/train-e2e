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
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from config import settings
from pages.base_page import BasePage
from utils.logger import logger_info


def _norm(text: str) -> str:
    """去掉所有空白再比。

    DOM 里的「2026年 11月」带一个空格（探测实测），而我们拼出来的标签没有。
    逐字符比会在这类「只是排版不同」的地方误红，所以统一归一化后比。
    """
    return re.sub(r"\s+", "", text or "")


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

    # 城市联想面板（2026-11-01 只读探测，reports/probe_dom{1,4}_2026-11-01.json）：
    #   每个输入框各有一个 wrapper，面板挂在 wrapper 里，结构是
    #     div.<field>-station-picker-wrap > div.city-fade-enter-done
    #       > div.widget-box.widget-search > ul.widget-search-bd > li > div.cont
    #   **必须限定在 wrapper 内查找**：页面底部「热门路线」里也有「上海 / 北京 / 杭州…」，
    #   全局 get_by_text(city).first 会先命中那一排，点下去等于什么都没选（CI 就是这么红的）。
    CITY_WRAPPER = {
        "depart": "div.depart-station-picker-wrap",
        "arrive": "div.arrive-station-picker-wrap",
    }
    SUGGEST_LIST = "ul.widget-search-bd"
    SUGGEST_ITEM = "li"
    # 每一项的可见文本是「<span class=tblue>北京</span>北」——li 文本是「北京北」。
    # 按 div.cont 的**完整文本**比，才能真正选中「北京」而不是第一个以北京开头的站。
    SUGGEST_ITEM_LABEL = "div.cont"
    CITY_MASK = "div.widget-city-mask"
    # 点选最多重试几轮。见 select_city 里的实测根因（K-11）：面板会在点选前后重排。
    # 3 轮的取舍：1 轮挡不住重排，再多只会把「控件真坏了」的失败拖成几分钟超时。
    CITY_PICK_RETRY = 3

    # 日期：可见文本是 <strong>2026-10-31</strong>，真实 <input> 带 assist-ib-dom 且**不可见**
    #   ⇒ 直接点 input 必报 "Element is not visible"，必须点外层可见块 div.date。
    DATE_INPUT = "#label-departDate"
    DATE_BLOCK = "div.search-date div.date"

    # 日历是自定义 widget + 遮罩层（遮罩不关会拦住后续点击）
    CALENDAR = ".widget-calendar-depart-wrap"
    CALENDAR_MASK = ".widget-calendar-mask"
    # 双月视图：标题 ul.widget-calendar-hd > li > h3（文本「2026年 11月」），
    # 日期格 ul.date-bd（两栏顺序与标题一致）> li > strong（日号）
    CALENDAR_MONTH_HEAD = "ul.widget-calendar-hd h3"
    CALENDAR_MONTH_BODY = "ul.date-bd"
    CALENDAR_DAY = "li"
    CALENDAR_DAY_LABEL = "strong"
    # 「日历已弹出」的判定锚点必须是这个，不能是 CALENDAR 本身：
    # 探测实测 .widget-calendar-depart-wrap 的边界框是 [228,285,**0**,76]（宽 0，
    # 它是 overflow 容器，真正的内容画在后代节点上），而 Playwright 的 to_be_visible
    # 要求边界框非空 ⇒ 拿 CALENDAR 当锚点会永远等不到，误报成「日历未唤起」。
    CALENDAR_READY = "ul.widget-calendar-hd"

    # 结果页
    RESULT_ITEM = ".list-item"
    SEARCH_BUTTON_NAME = "搜索"

    # 车次号形态：G/D/C/Z/T/K/Y + 1~4 位数字，**或 4 位纯数字**（普速车次，如 1462 次）。
    # 2026-11-01 实测（reports/probe_items_2026-11-01.json）：
    #   上海→北京 的结果里既有 G/C/D 字头，也有「1462」这种纯数字普速车次；
    #   只认字头会让这类条目被当成「抽不到车次号」，把覆盖率从 91.7% 压到 88.3%。
    # 纯数字限定 4 位：票价/座数这类 1~3 位数字（如 553）不能被误认成车次号。
    # 2026-11-02 补 Y：汉口↔武昌（同城两站）实测出现 `Y719 / Y713 / Y720` 三趟旅游/临客列车，
    #   旧口径漏认 ⇒ 该栏覆盖率 4/6=67% 直接红。现场文本：`19:19 汉口 0时18分 Y719 19:37 武昌`。
    #   只加 Y，不顺手加 L/S —— 那两个没有实测记录，遇到再按同样流程补，不凭"应该也有"扩字头。
    TRAIN_NO_RE = re.compile(r"^(?:[GDCZTKY]\d{1,4}|\d{4})$")
    RESULT_COUNT_RE = re.compile(r"共\s*(\d+)\s*车次")
    DATE_IN_URL_RE = re.compile(r"[?&]dDate=(\d{4}-\d{2}-\d{2})")
    # 中转/换乘卡片：一个行程由两段拼成，结构上没有「单一车次号」
    TRANSFER_MARKERS = ("换乘",)

    def __init__(self, page: Page, base_url: str | None = None) -> None:
        super().__init__(page, base_url)
        # 最近一次点选时面板里的候选项（纯文本快照），只在失败时进异常消息。
        # 留着它的理由：这类「点 A 填成 B」的问题，没有现场快照就只能靠猜。
        self._pick_trace: list[str] = []

    # ---------------------------------------------------------------- 页面动作

    def open(self) -> None:
        self.open_and_check("/")

    def search_button(self) -> Locator:
        return self.page.get_by_role("button", name=self.SEARCH_BUTTON_NAME).first

    def select_city(self, field: str, city: str) -> None:
        """在城市输入框里真正选中一个城市。

        field: "depart" | "arrive"

        2026-11-01 改法（起因见 docs/KNOWN-LIMITATIONS.md K-01 与 PR #1 §0.1）：
          1. 联想项**只在属于该输入框的面板容器里找**，不再全局 get_by_text —— 全局会命中
             页面底部「热门路线」那一排同名城市，点下去表单不生效却让 input_value 看起来正常。
          2. 匹配键是 li 内 div.cont 的**完整文本**。li 文本是「北京北」，按 exact 匹配
             「北京」会命中内层 span，结果填成「北京北」——这就是 K-01 记录的现场。
          3. 面板不出现时不是立刻失败：联想接口有返回「对不起，找不到：X」再刷成真实列表的过程，
             所以用有界等待轮询「面板里是否已出现该城市的完整项」，超时才算控件不可用。
          4. 选完必须把 div.widget-city-mask 关掉，否则它会拦掉后面点日期的每一次点击。
          5. 最后核对输入框实际值，值不对直接抛错，不带着错状态往下走。
        """
        selector = self.DEPART_CITY if field == "depart" else self.ARRIVE_CITY
        box = self.page.locator(selector)
        expect(box).to_be_visible()

        self.human.click(box)
        box.fill("")

        # 点选要重试 + 每次都读回核对（2026-11-01 实测根因，K-11）：
        # 联想面板在输入停止后**还会继续刷新**，「读到 li[0] 是『南京』」到
        # 「鼠标点下去」之间列表会重排，点完输入框变成「南京南」。
        # 只用「点到了精确匹配项」判成功是不够的 —— 杭州→南京 那条用例连续 3 轮
        # 稳定填成「南京南」，而孤立探针里同样的代码 2/2 正确，差异就在重排时机。
        # 所以判据改成「点完读回的值等于 city」，不对就清空重来。
        last = ""
        for attempt in range(1, self.CITY_PICK_RETRY + 1):
            if not self._type_and_pick(field, box, city, by_typing=True):
                # 退路：逐字输入时联想接口偶尔不刷新，改成整串 fill 再触发一次。
                # 这一步是**功能必要**（否则面板一直停在「对不起，找不到：X」），不是规避检测。
                if not self._type_and_pick(field, box, city, by_typing=False):
                    raise WidgetUnavailable(
                        f"{field} 城市下拉里没有「{city}」的精确项"
                        f"（逐字输入与整串填入都试过，等待 {settings.EXPECT_TIMEOUT_MS * 2}ms）"
                    )

            self._close_city_panel()
            actual = box.input_value().strip()
            if actual == city:
                if attempt > 1:
                    logger_info(f"[火车票] {field} 城市第 {attempt} 次点选才生效（面板重排）")
                return
            last = actual
            logger_info(
                f"[火车票] {field} 城市第 {attempt} 次点选后值为 {actual!r}"
                f"（期望 {city!r}），重来；面板当时："
                f"{' | '.join(self._pick_trace) or '(未记录)'}"
            )
            self.human.click(box)
            box.fill("")

        raise WidgetUnavailable(
            f"{field} 城市点了「{city}」但输入框始终是 {last!r}"
            f"（重试 {self.CITY_PICK_RETRY} 轮；最后一次点选时的面板项："
            f"{' | '.join(self._pick_trace) or '(未记录)'}）"
        )

    # ---------------------------------------------------------------- 城市面板内部

    def _panel(self, field: str) -> Locator:
        """该输入框对应的联想面板列表。限定在 wrapper 内，避开「热门路线」。"""
        return self.page.locator(f"{self.CITY_WRAPPER[field]} {self.SUGGEST_LIST}").first

    def _type_and_pick(self, field: str, box: Locator, city: str, *, by_typing: bool) -> bool:
        """输入城市名，等面板里出现完整匹配项，点它。成功返回 True。

        等待用 wait_for_function 轮询 DOM 谓词（有界、可超时），不用固定 sleep ——
        本文件的规矩是「等待一律有界等待」，这里同样遵守。
        """
        box.fill("")
        if by_typing:
            if self.human.disabled:
                # 关闭时保持原行为（固定 80ms），字节级不变；开启才换非均匀节奏。
                box.press_sequentially(city, delay=settings.TYPE_DELAY_MS)
            else:
                # 110–190ms 非均匀 + 每 2–3 字符一次加顿；城市名 2–4 字符，单字段远低于 3s。
                self.human.type_text(box, city)
        else:
            box.fill(city)

        wrapper = self.CITY_WRAPPER[field]
        try:
            self.page.wait_for_function(
                """
                ({wrapper, city}) => {
                  const root = document.querySelector(wrapper);
                  if (!root) return false;
                  const want = city.replace(/\\s+/g, '');
                  for (const ul of root.querySelectorAll('ul.widget-search-bd')) {
                    if (!ul.offsetHeight) continue;
                    for (const li of ul.querySelectorAll('li')) {
                      const cont = li.querySelector('div.cont');
                      if (cont && (cont.innerText || '').replace(/\\s+/g, '') === want) return true;
                    }
                  }
                  return false;
                }
                """,
                arg={"wrapper": wrapper, "city": city},
                timeout=settings.EXPECT_TIMEOUT_MS * 2,
                # 显式定时轮询：默认的 polling="raf" 依赖页面帧回调，
                # 被挂起的渲染器上会一直等不到下一帧（K-06 那类「无响应」的成因之一）。
                polling=200,
            )
        except PlaywrightTimeout:
            return False

        return self._click_option(field, city)

    def _click_option(self, field: str, city: str) -> bool:
        """在面板里点「完整文本等于 city」的那一项。

        不用 get_by_text(city, exact=True)：面板里前几项是「北京北 / 北京东 / 北京南」，
        内层 span 的文本恰好是「北京」，exact 匹配会先命中它们。

        点击前把面板快照记进 `self._pick_trace`：K-11 那类「点南京填成南京南」的问题，
        现场只留下「值不对」三个字是没法定位的，必须知道点下去那一刻列表长什么样。
        """
        items = self._panel(field).locator(self.SUGGEST_ITEM)
        want = _norm(city)
        self._pick_trace = []
        for i in range(items.count()):
            label = items.nth(i).locator(self.SUGGEST_ITEM_LABEL).first
            if not label.count():
                continue
            text = _norm(label.inner_text())
            self._pick_trace.append(f"[{i}]{text}")
            if text == want:
                self.human.click(items.nth(i))
                return True
        return False

    def _close_city_panel(self) -> None:
        """关掉城市面板的遮罩。Escape 实测无效，只能点遮罩本身。

        探测记录：选完城市后 div.widget-city-mask 仍可见（1440×900 全覆盖），
        不关掉它后面点日期块会一直 "intercepts pointer events"。
        """
        mask = self.page.locator(self.CITY_MASK).first
        try:
            expect(mask).to_be_hidden(timeout=1000)
            return
        except AssertionError:
            pass
        if mask.is_visible():
            mask.click(force=True)
        expect(mask).to_be_hidden(timeout=settings.EXPECT_TIMEOUT_MS)

    # ---------------------------------------------------------------- 日期

    def select_date(self, depart_date: str) -> None:
        """在日历控件里点选出发日期。depart_date 形如 2026-11-07。

        2026-11-01 改法：
          1. 点外层可见块 div.search-date div.date，不再点 #label-departDate
             —— 那个 input 带 assist-ib-dom，Playwright 判为不可见，force 也点不动。
          2. 不再尝试 get_by_text(depart_date) 兜底：日历没打开时该文本不在页面上
             （页面只显示当前选中日期），这条兜底只会把错误变成 15s 的 click 超时。
          3. 点不动/日历不出，一律转 WidgetUnavailable（由用例按 K-01 记 blocked），
             不让 Playwright 的 TimeoutError 漏成 failure 并计入分母。
        """
        target_month, target_day = self._split_date(depart_date)
        logger_info(f"[火车票] 选出发日期 {depart_date}")

        block = self.page.locator(self.DATE_BLOCK).first
        try:
            expect(block).to_be_visible(timeout=settings.EXPECT_TIMEOUT_MS)
            self.human.click(block)
        except (AssertionError, PlaywrightTimeout) as exc:
            raise WidgetUnavailable(
                f"日期块 {self.DATE_BLOCK} 点不动（可能被城市面板遮罩拦住），"
                f"无法点选 {depart_date}"
            ) from exc

        try:
            expect(self.page.locator(f"{self.CALENDAR} {self.CALENDAR_READY}").first).to_be_visible(
                timeout=settings.EXPECT_TIMEOUT_MS
            )
        except AssertionError as exc:
            raise WidgetUnavailable(
                f"日历未唤起（{self.CALENDAR} {self.CALENDAR_READY} 仍不可见），无法点选 {depart_date}"
            ) from exc

        self._goto_month(target_month)
        self._click_day(target_month, target_day)

        # 关掉日历（遮罩不关会拦住搜索按钮）
        self._close_calendar()

        actual = self.page.locator(self.DATE_INPUT).input_value().strip()
        if actual != depart_date:
            raise WidgetUnavailable(f"点选后日期为 {actual!r}，期望 {depart_date!r}")

    def _month_heads(self) -> Locator:
        return self.page.locator(self.CALENDAR).locator(self.CALENDAR_MONTH_HEAD)

    def _goto_month(self, month_label: str) -> None:
        """翻到目标月份。widget 是双月视图，目标常在右栏，翻页只在必要时发生。"""
        for _ in range(3):
            if self._month_visible(month_label):
                return
            nxt = self.page.locator(self.CALENDAR).get_by_text("›", exact=True).first
            if not nxt.is_visible():
                break
            # 翻月可能连点：用 quick 档（单次 150–350ms），避免 3×400ms 的累积机械节奏。
            self.human.click_quick(nxt)
        if not self._month_visible(month_label):
            raise WidgetUnavailable(f"日历里翻不到 {month_label}")

    def _month_visible(self, month_label: str) -> bool:
        heads = self._month_heads()
        want = _norm(month_label)
        try:
            for i in range(heads.count()):
                if _norm(heads.nth(i).inner_text()) == want:
                    return heads.nth(i).is_visible()
        except Exception:  # 日历在动画中被重建，这一轮就当没看到
            return False
        return False

    def _click_day(self, month_label: str, day: str) -> None:
        """点具体某一天。

        双月视图里两栏各有一套 ul.date-bd，顺序与标题栏一致 —— 按标题下标取对应那栏，
        避免「同一天在两栏都出现时点错月份」（旧的 filter(has_text=...) 做法会漂）。
        """
        heads = self._month_heads()
        bodies = self.page.locator(self.CALENDAR).locator(self.CALENDAR_MONTH_BODY)
        want_month = _norm(month_label)

        index = None
        for i in range(heads.count()):
            if _norm(heads.nth(i).inner_text()) == want_month:
                index = i
                break
        if index is None:
            raise WidgetUnavailable(f"日历里找不到 {month_label}（标题栏 {heads.count()} 个）")
        if bodies.count() <= index:
            raise WidgetUnavailable(
                f"{month_label} 没有对应的日期格（标题 {heads.count()} 个，日期栏 {bodies.count()} 个）"
            )

        cells = bodies.nth(index).locator(self.CALENDAR_DAY)
        want_day = _norm(day)
        for i in range(cells.count()):
            label = cells.nth(i).locator(self.CALENDAR_DAY_LABEL).first
            if not label.count():
                continue
            if _norm(label.inner_text()) == want_day:
                self.human.click(cells.nth(i))
                return
        raise WidgetUnavailable(f"{month_label} 里找不到 {day} 日")

    def _close_calendar(self) -> None:
        """关日历遮罩。选完日期后 widget 通常自己就关了，所以先判在不在。

        2026-11-01 实测踩到：单日选择完成后遮罩会被整个摘除，此时再 click 会一直等
        「waiting for locator(...)」直到 15s 超时 —— 关一个已经不在的东西不该算失败。
        只有「遮罩确实还在且关不掉」才算控件问题，抛 WidgetUnavailable。
        """
        mask = self.page.locator(self.CALENDAR_MASK).first
        try:
            if not mask.is_visible():
                return
            mask.click(force=True, timeout=3000)
        except (AssertionError, PlaywrightTimeout):
            pass  # 检查与点击之间被摘除，属于正常时序；下面统一判定
        try:
            expect(mask).to_be_hidden(timeout=settings.EXPECT_TIMEOUT_MS)
        except AssertionError as exc:
            raise WidgetUnavailable(
                f"日历遮罩 {self.CALENDAR_MASK} 关不掉，会拦住搜索按钮"
            ) from exc

    def submit(self) -> None:
        """点搜索并等结果页。"""
        logger_info("[火车票] 点击搜索并等待结果页")
        self.human.click(self.search_button())
        self.page.wait_for_url(re.compile(r"/webapp/train/list"), timeout=settings.NAV_TIMEOUT_MS)
        self.wait_ready()
        self.assert_not_blocked()

    # ---------------------------------------------------------------- 取状态（不断言）

    def result_texts(self) -> list[str]:
        """结果列表项的全部文本，**一次**取回。返回空列表表示没有结果。

        为什么不用逐个 Locator.inner_text()：
        2026-11-01 CI run 41288151390 实测，广州→深圳 一页 **538 条**，
        「换乘判定 ×2 遍 + 车次号解析」会让 inner_text 往返 1600+ 次，
        在 xvfb runner 上直接撞 pytest-timeout 的 180s（stack 停在 inner_text 里）。
        evaluate_all 只往返一次，解析全部放回 Python 侧。

        等待窗用 NAV_TIMEOUT_MS：实测同一份列表单独跑 2.4s 出现（538 条），
        连跑时 5s 内没出现会误报「0 条」。放宽的只是等待，判定门槛一个没动。
        """
        locator = self.page.locator(self.RESULT_ITEM)
        try:
            expect(locator.first).to_be_visible(timeout=settings.NAV_TIMEOUT_MS)
        except AssertionError:
            return []
        return locator.evaluate_all("els => els.map(el => el.innerText || '')")

    @classmethod
    def is_transfer_text(cls, text: str) -> bool:
        """这段卡片文本是不是「中转/换乘」方案。

        2026-11-01 实测：单程结果列表里会混入「上海虹桥 → 北京南转 → 亦庄 / 换乘 16 分」
        这种两段拼成的卡片，它的文本里没有、也不该有**单一**车次号。
        把它们算进「车次号覆盖率」的分母是口径错误 —— 覆盖率要衡量的是
        「点到点车次卡片里，车次号能不能解析出来」。纯字符串判断，不碰 DOM。
        """
        return all(marker in text for marker in cls.TRANSFER_MARKERS) and "转" in text

    @classmethod
    def train_number_of(cls, text: str) -> str | None:
        """从一条卡片文本里抽车次号；抽不到返回 None。"""
        for token in text.split():
            if cls.TRAIN_NO_RE.match(token):
                return token
        return None

    def declared_result_count(self, text: str | None = None) -> int | None:
        """页面自称的车次数（「共 557 车次」）。与真实列表条数分开，不混用。

        传入 text 时复用调用方已经取到的正文，不再对整页做一次 inner_text：
        2026-11-01 CI 实测，在 538 条卡片的列表页上对整页取文本会强制全量布局，
        慢 runner 上这一步本身就够慢，重复做没有意义。
        """
        if text is None:
            text = self.page.locator("body").inner_text()
        match = self.RESULT_COUNT_RE.search(text)
        return int(match.group(1)) if match else None

    def url_depart_date(self) -> str | None:
        """结果页 URL 里的 dDate —— 用它验证「日期真的生效了」。"""
        match = self.DATE_IN_URL_RE.search(self.page.url)
        return match.group(1) if match else None

    @staticmethod
    def _split_date(depart_date: str) -> tuple[str, str]:
        """拆成 (月份标题, 日号)。月份标题必须带空格 —— DOM 里就是「2026年 11月」。"""
        year, month, day = depart_date.split("-")
        return f"{year}年 {int(month)}月", str(int(day))
