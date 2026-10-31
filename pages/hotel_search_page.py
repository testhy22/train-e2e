"""携程首页「酒店」模块的 Page Object（D1 裁决：www.ctrip.com）。

所有定位器都来自 2026-11-01 的 5 次只读探针（证据在 `reports/probe_www_hotels_*.json`，
`reports/` 已 gitignore）。写这个文件时遵守两条硬约束：

1. **不用会漂的 class 名。** 页面 class 是「语义前缀 + 构建哈希」形式，例如
   `hs_time-tab_cFMNR`、`hs_associative-item_z55zG`。哈希每次构建都变，所以只取
   语义前缀作子串匹配（`[class*='associative-item']`），绝不写全名。
   `ul` 的 `id="kakxi"` 同理是生成值，因此表单根用「包含 #hotels-destination 的 ul」
   这种结构关系定位，不用 id。
2. **不写裸 sleep、不写自证断言。** 等待一律 `expect` / `wait_for_selector`；
   日期不写回 input.value（www 首页根本没有日期 input），只走面板点击，
   最终以「提交时真实发出的 /hotels/list 查询参数」作为外部可验证结果。

被测页面事实（实测，非推测）：
  目的地  `#hotels-destination`（count=1、editable、value 回填为「上海, 中国」）
  联想项  `[class*='associative-item']`（输入后 count=15）
  日期    无 input；面板 `.c-calendar-month__days` → `li > span.day`，且**同屏渲染两个月**，
          所以选日必须先按 `.c-calendar-month__title`（如「2026年11月」）限定月份块，
          否则 `text=8` 会同时命中两个月的 8 号。
  房间    `[class*='room-guest']` 弹层；加减按钮 `[class*='plus']` / `[class*='minus']`
          共 3 组，顺序为 房间 / 成人 / 儿童；确认按钮 `[class*='done-span']`（在弹层根之外）
  搜索    `[class*='search-btn']`（是 div，不是 button）
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, quote, unquote, urlparse

from playwright.sync_api import Locator, expect

from config import settings
from pages.base_page import BasePage, BlockedByAntiBot, BlockSignal

# 表单根：包含目的地输入框的那个 ul
FORM_ROOT = "ul:has(#hotels-destination)"


class HotelSearchPage(BasePage):
    """首页酒店模块。方法只做操作与取值，判定留在测试层。"""

    DESTINATION = "#hotels-destination"
    SUGGEST_ITEM = "[class*='associative-item']"
    CAL_DAYS = ".c-calendar-month__days"
    CAL_BLOCK = ".c-calendar-month"
    CAL_TITLE = ".c-calendar-month__title"
    ROOM_GUEST = "[class*='room-guest']"
    PLUS = "[class*='plus']"
    DONE = "[class*='done-span']"
    SEARCH_BTN = "[class*='search-btn']"

    def __init__(self, page, base_url: str | None = None) -> None:
        super().__init__(page, base_url or settings.hotel_base_url())

    # ------------------------------------------------------------ 导航

    def open(self) -> None:
        self.open_and_check("/")
        self.form.locator(self.DESTINATION).wait_for(state="visible")

    @property
    def form(self) -> Locator:
        """表单根。用结构关系而不是 id —— `id="kakxi"` 是每次构建都变的生成值。"""
        return self.page.locator(FORM_ROOT).first

    def form_text(self) -> str:
        """表单当前的摘要文案（入住/退房/房间人数都在这段文本里）。

        这是本页唯一的「人可读状态出口」：www 首页没有日期 input、没有隐藏域，
        选完日期后唯一能看到结果的地方就是这段文本。
        """
        return self.form.inner_text().replace("\n", " ")

    # ------------------------------------------------------------ 目的地

    def set_destination(self, city: str) -> None:
        """输入城市并从联想面板选第一项（第一条恒为「<城市>, 中国 城市」）。"""
        box = self.page.locator(self.DESTINATION)
        box.click()
        box.fill("")
        box.press_sequentially(city, delay=settings.TYPE_DELAY_MS)

        item = self.page.locator(self.SUGGEST_ITEM).first
        item.wait_for(state="visible")
        item.click()
        # 回填是异步的，等到值真的变了再返回；不 sleep。
        expect(box).to_have_value(re.compile(re.escape(city)))

    # ------------------------------------------------------------ 日期

    def _open_calendar(self, which: str) -> None:
        """点「入住」/「退房」标签唤出面板。面板已开时不重复点。

        注意必须用 `.first`：面板同屏渲染两个月，`.c-calendar-month__days` 恒为 2 个，
        直接 wait_for 会触发 Playwright 严格模式报错。
        """
        if self.page.locator(self.CAL_DAYS).count() > 0:
            return
        self.form.locator("label", has_text=re.compile(rf"^{which}$")).first.click()
        self.page.locator(self.CAL_DAYS).first.wait_for(state="visible")

    def _pick_day(self, target: date) -> None:
        """在指定月份块里点某一天。

        必须先按月份标题限定块：面板同时渲染当月和下月，两个「8」都在 DOM 里。
        """
        title = f"{target.year}年{target.month}月"
        block = self.page.locator(self.CAL_BLOCK).filter(
            has=self.page.locator(f"{self.CAL_TITLE}:text-is('{title}')")
        ).first
        block.locator("li").filter(has_text=re.compile(rf"^{target.day}$")).first.click()

    def set_checkin(self, target: date) -> None:
        self._open_calendar("入住")
        self._pick_day(target)

    def set_checkout(self, target: date) -> None:
        """选退房日。

        实测：选完入住后页面会自动补 1 晚并**保持面板打开**，此时直接点就是设退房；
        面板已关（例如中途按了 Esc）则先点「退房」标签重新唤出。
        """
        self._open_calendar("退房")
        self._pick_day(target)

    def close_calendar(self) -> None:
        """收起日历面板（面板展开时会盖住下方控件）。"""
        if self.page.locator(self.CAL_DAYS).count() > 0:
            self.page.keyboard.press("Escape")
            expect(self.page.locator(self.CAL_DAYS)).to_have_count(0)

    # ------------------------------------------------------------ 房间及住客

    def _popup(self) -> Locator:
        """弹层根：同时含「成人」「儿童」文案的最内层 div。"""
        return (
            self.page.locator("div")
            .filter(has_text=re.compile("成人"))
            .filter(has_text=re.compile("儿童"))
            .last
        )

    def set_rooms(self, rooms: int = 1, adults: int = 1, children: int = 0) -> None:
        """设置房间/成人/儿童数。

        **页面耦合（2026-11-01 实测踩到）**：把房间数从 1 加到 2 时，页面会自动把成人
        从 1 补到 2（每间至少一位）。所以必须**先加成人数、再加房间数**，否则会多出一位
        （bj 用例首跑就得到「2间, 4位」而不是 3 位）。

        因此本方法要求 `adults >= rooms`：这是数据集层面的约束，不满足时直接报错，
        而不是让结果悄悄错开。
        """
        if adults < rooms:
            raise ValueError(
                f"adults({adults}) < rooms({rooms}) 不支持：加房间会自动补住客，"
                "无法把人数压回低于房间数的状态"
            )

        self.form.locator(self.ROOM_GUEST).first.click()
        popup = self._popup()
        plus = popup.locator(self.PLUS)
        expect(plus).to_have_count(3)          # 房间 / 成人 / 儿童

        # 顺序是契约的一部分：成人 → 儿童 → 房间（房间最后加，才不会顺带补人）
        for index, target, low in ((1, adults, 1), (2, children, 0), (0, rooms, 1)):
            delta = target - low
            if delta < 0:
                raise ValueError(f"set_rooms 只支持增加，收到 {target} < 默认值 {low}")
            for _ in range(delta):
                plus.nth(index).click()

        self.page.locator(self.DONE).first.click()

    # ------------------------------------------------------------ 提交

    def submit_and_capture(self) -> dict[str, list[str]]:
        """点搜索，并抓回真正发出去的 `/hotels/list` 请求参数。

        为什么断言挂在这里：未登录状态下列表页被 passport 登录墙接管（实测
        `probe_www_hotels_04.json`：直接打开 list URL 也回登录页），所以「结果列表渲染」
        在本期拿不到。但请求参数是页面**根据用户输入自己生成**的，恰好是
        「搜索行为真的发生了」的外部证据，而不是我们回读自己写进去的值。
        """
        with self.page.expect_request(
            lambda req: "/hotels/list" in req.url, timeout=settings.NAV_TIMEOUT_MS
        ) as info:
            self.form.locator(self.SEARCH_BTN).first.click()
        return parse_qs(urlparse(info.value.url).query)

    # ------------------------------------------------------------ 结果侧取值

    @property
    def current_url(self) -> str:
        return self.page.url

    def landed_on_login_wall(self) -> bool:
        """是否被 passport 登录墙接管。

        实测（2026-11-01）：从首页**提交搜索**必然落到 passport；而把同一组参数
        直接当地址打开却能正常出结果（`reports/probe_www_hotels_06.json`）。
        也就是说登录墙挂的是「提交路径」，不是「列表页本身」。

        但它带会话状态：同一 context 里先被拦一次、再访问同一 URL 曾放行（probe 06 的
        slim → full 顺序）。所以调用方必须把「被拦」当成一次性事实记录，不做重试。
        """
        return "passport.ctrip.com" in self.page.url

    def assert_not_login_wall(self) -> None:
        """落登录墙就抛 blocked（由 blocked_guard 转 xfail），不当成失败。"""
        if self.landed_on_login_wall():
            raise BlockedByAntiBot(
                BlockSignal("login_wall", self.page.url[:120]),
                url=self.page.url,
                title=self.page.title(),
            )

    # 列表页直连模板。参数取自真实回跳 URL（probe 03/06），不是猜的：
    # 缺 optionId/optionType/display/domestic 时会被踢回登录（probe 06 的 slim 分支）。
    LIST_TEMPLATE = (
        "https://hotels.ctrip.com/hotels/list?countryId=1&city={city_id}&provinceId=0"
        "&checkin={checkin}&checkout={checkout}&optionId=2&optionType=City&directSearch=0"
        "&display={display}&crn={rooms}&adult={adults}&children={children}"
        "&searchBoxArg=t&travelPurpose=0&domestic=1"
    )

    def open_list_directly(
        self,
        city_id: int,
        city_name: str,
        checkin: date,
        checkout: date,
        rooms: int = 1,
        adults: int = 1,
        children: int = 0,
    ) -> None:
        """直连列表页（普通 URL 导航，不是绕过：不注入任何登录态或指纹）。"""
        url = self.LIST_TEMPLATE.format(
            city_id=city_id,
            checkin=checkin.strftime("%Y/%m/%d"),
            checkout=checkout.strftime("%Y/%m/%d"),
            display=quote(f"{city_name}, 中国"),
            rooms=rooms,
            adults=adults,
            children=children,
        )
        self.page.goto(url, wait_until="domcontentloaded")
        self.wait_ready()

    def result_card_count(self) -> int:
        """酒店卡片数量。类名语义前缀 `hotel-card`（实测 13 条，见 probe 06）。"""
        return self.page.locator("[class*='hotel-card']").count()

    @staticmethod
    def display_of(params: dict[str, list[str]]) -> str:
        """`display` 参数是 URL 编码的城市全名，解码后便于断言。"""
        return unquote(params.get("display", [""])[0])
