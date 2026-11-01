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
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from config import settings
from pages.base_page import BasePage, BlockedByAntiBot, BlockSignal
from utils.logger import logger_info

# 表单根：包含目的地输入框的那个 ul
FORM_ROOT = "ul:has(#hotels-destination)"


class HotelSearchPage(BasePage):
    """首页酒店模块。方法只做操作与取值，判定留在测试层。"""

    DESTINATION = "#hotels-destination"
    SUGGEST_ITEM = "[class*='associative-item']"
    CAL_DAYS = ".c-calendar-month__days"
    CAL_BLOCK = ".c-calendar-month"
    CAL_TITLE = ".c-calendar-month__title"
    # 翻月箭头（探针 43）：span.c-calendar-icon-prev / span.c-calendar-icon-next，
    # 各一个、均可见，14×20，分别贴在左右两个月块的表头上。
    CAL_PREV = "span[class*='c-calendar-icon-prev']"
    CAL_NEXT = "span[class*='c-calendar-icon-next']"
    MAX_MONTH_STEPS = 24
    ROOM_GUEST = "[class*='room-guest']"
    PLUS = "[class*='plus']"
    MINUS = "[class*='minus']"
    DONE = "[class*='done-span']"
    SEARCH_BTN = "[class*='search-btn']"

    # 弹层里三个计数（房间 / 成人 / 儿童）的容器与数值节点。
    # 实测（probe_hotel_room_popup.py，2026-11-01）：
    #   div.hs_guest-select_MyRAh 内含 3 个 span.hs_count_Jdquu，顺序 房间 / 成人 / 儿童
    #   满值 1/1/0 时再点减号无效（值不变），所以「减到下限」是安全的操作
    GUEST_BOX = "[class*='guest-select']"
    COUNT = "[class*='count']"

    # 左侧竖排导航（结果页回首页的唯一入口，2026-11-01 用户截图指出）。
    # 实测结构：div.lsn_first_nav_wrap_LZamG（46×42 at(15,68)，text=「酒店」），
    # 父容器 div.lsn_nav_content_wrap_ci7QT。class 带构建哈希，只取语义前缀。
    #
    # **不能用 `a:has-text('酒店')`**：第一版探针就是这么写的，命中的是列表正文里
    # 一个 56×16 的文字链接（bbox y=3568），落点是 hotels/homepage（海外酒店首页，
    # 没有 #hotels-destination）。宽泛文本选择器在真实站点上必然先命中正文。
    NAV_ITEM = "[class*='lsn_first_nav_wrap']"
    NAV_ITEM_TEXT = re.compile(r"^酒店$")

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
        logger_info(f"[酒店] 输入目的地 {city}")
        box = self.page.locator(self.DESTINATION)
        self.human.click(box)
        box.fill("")
        # 逐字符输入（每字符独立采样延迟）：fill 只发一次 input 事件，联想面板未必触发，
        # 也不像人。详见 utils/humanize.py。
        self.human.type_text(box, city)

        item = self.page.locator(self.SUGGEST_ITEM).first
        item.wait_for(state="visible")
        self.human.click(item)
        # 回填是异步的，等到值真的变了再返回；不 sleep。
        expect(box).to_have_value(re.compile(re.escape(city)))
        # 人看完联想结果再动下一步。
        self.human.read()

    # ------------------------------------------------------------ 日期

    def _open_calendar(self, which: str) -> None:
        """点「入住」/「退房」标签唤出面板。面板已开时不重复点。

        注意必须用 `.first`：面板同屏渲染两个月，`.c-calendar-month__days` 恒为 2 个，
        直接 wait_for 会触发 Playwright 严格模式报错。
        """
        if self.page.locator(self.CAL_DAYS).count() > 0:
            return
        self.human.click(self.form.locator("label", has_text=re.compile(rf"^{which}$")).first)
        self.page.locator(self.CAL_DAYS).first.wait_for(state="visible")

    def _pick_day(self, target: date) -> None:
        """在指定月份块里点某一天（必要时先翻月）。

        必须先按月份标题限定块：面板同时渲染当月和下月，两个「8」都在 DOM 里。

        **为什么必须翻月（探针 42/43，2026-11-01）**：面板的起始月份跟着**已选日期**
        走 —— 选了 12-01 之后重开，面板只有「2026年12月 + 2027年1月」，
        `2026年11月` 那个块压根不存在，老实现在这里 15 秒超时。
        e2e 那 3 组之所以过，是因为数据恰好都落在可见两个月里，属于运气。
        """
        self._goto_month(target)
        title = f"{target.year}年{target.month}月"
        block = self.page.locator(self.CAL_BLOCK).filter(
            has=self.page.locator(f"{self.CAL_TITLE}:text-is('{title}')")
        ).first
        day = block.locator("li").filter(has_text=re.compile(rf"^{target.day}$")).first
        self.human.click(day)
        # 选完日人会看一下新日期，再动下一步。
        self.human.read()

    def shown_months(self) -> list[tuple[int, int]]:
        """面板当前渲染的月份块 [(年, 月), …]（双月视图 = 2 个）。"""
        out: list[tuple[int, int]] = []
        blocks = self.page.locator(self.CAL_TITLE)
        for index in range(blocks.count()):
            title = blocks.nth(index)
            try:
                if not title.is_visible():
                    continue
                text = title.inner_text()
            except Exception:
                continue
            hit = re.search(r"(\d{4})年(\d{1,2})月", text)
            if hit:
                out.append((int(hit.group(1)), int(hit.group(2))))
        return out

    def _goto_month(self, target: date) -> None:
        """把面板翻到目标年月。翻不动就抛错 —— 不静默地用错日期。"""
        wanted = (target.year, target.month)
        for _ in range(self.MAX_MONTH_STEPS):
            shown = self.shown_months()
            if not shown:
                raise RuntimeError(f"日期面板打开了但读不到月份块（url={self.page.url}）")
            if wanted in shown:
                return
            forward = wanted > shown[-1]
            arrow = self.page.locator(self.CAL_NEXT if forward else self.CAL_PREV).first
            before = shown
            self.human.click(arrow)
            for _ in range(20):                       # 等翻页动画，最多 3 秒
                if self.shown_months() != before:
                    break
                self.page.wait_for_timeout(150)
            else:
                raise RuntimeError(
                    f"翻月没生效：目标 {wanted[0]}-{wanted[1]:02d}，"
                    f"当前 {before}，方向={'后' if forward else '前'}"
                )
        raise RuntimeError(
            f"翻了 {self.MAX_MONTH_STEPS} 次仍未出现 {wanted[0]}-{wanted[1]:02d}，"
            f"当前 {self.shown_months()}"
        )

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
        """收起日历面板（面板展开时会盖住下方控件）。

        **不能只按 Escape。** 探针 42 实测：面板开着时按 Esc，`.c-calendar-month__days`
        仍是 2 个，原来的 `expect(...).to_have_count(0)` 直接抛断言 —— 也就是
        「关闭失败」被报成「用例断言失败」，看不出真正原因。
        改用「点面板外的空白」：探针 42/43 两次都一次生效，且不改动任何输入值。
        """
        if self.page.locator(self.CAL_DAYS).count() == 0:
            return
        # 探针 42：Esc 关不掉这个面板，坐标点空白是一次生效的正解（Esc 兜底保留）；
        # HUMAN=1 时坐标加抖动，避免固定 (20, 20) 的指纹级特征。
        self.human.click_neutral(20, 20)
        try:
            expect(self.page.locator(self.CAL_DAYS)).to_have_count(0)
        except AssertionError:
            # 点空白没关掉时再按一次 Esc 兜底；仍关不掉就抛出，让调用方带现场失败。
            self.human.press("Escape")
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

    def guest_counts(self) -> tuple[int, int, int]:
        """弹层里当前的（房间, 成人, 儿童）。顺序由实测确定，不是猜的。"""
        box = self.page.locator(self.GUEST_BOX).first
        nodes = box.locator(self.COUNT)
        values = []
        for index in range(3):
            text = (nodes.nth(index).inner_text() or "").strip()
            values.append(int(text) if text.isdigit() else -1)
        if len(values) != 3 or any(v < 0 for v in values):
            raise ValueError(f"房间住客计数读不出来：{values}")
        return values[0], values[1], values[2]

    def _reset_guest_counts(self) -> None:
        """把三个计数压回下限（房间 1 / 成人 1 / 儿童 0）。

        为什么必须重置（2026-11-01 实测踩到，不是预防性设计）：
        `HotelSearchPage` 原先的实现假定「页面默认 1 间 1 位」，只做加法。
        单条用例一个干净 context 时这个前提成立；**共享会话连续搜索时不成立** ——
        站点把上一次的住客数留在表单里，于是「2间3位」之后再来一轮 1间1位，
        会得到 2间4位（e2e 串联轨道第一轮就是这么红的）。

        减到下限时再点减号无效（实测值不变），所以这里不需要判断按钮是否置灰，
        多余点击是安全的 —— 但仍然用「先读再点」避免几十次无谓点击。
        """
        floors = (1, 1, 0)
        minus = self.page.locator(self.MINUS)
        if minus.count() < 3:
            raise ValueError(f"弹层里减号数量不足：{minus.count()}")
        for index, floor in enumerate(floors):
            for _ in range(6):                      # 6 次足够覆盖页面允许的最大值
                if self.guest_counts()[index] <= floor:
                    break
                self.human.click(minus.nth(index))

    def set_rooms(self, rooms: int = 1, adults: int = 1, children: int = 0) -> None:
        """设置房间/成人/儿童数，**按当前值增量调整**（先压回下限再加）。

        **页面耦合（2026-11-01 实测踩到）**：把房间数从 1 加到 2 时，页面会自动把成人
        从 1 补到 2（每间至少一位）。所以加法顺序必须是 **成人 → 儿童 → 房间**，
        否则会多出一位（bj 用例首跑就得到「2间, 4位」而不是 3 位）。

        因此本方法要求 `adults >= rooms`：这是数据集层面的约束，不满足时直接报错，
        而不是让结果悄悄错开。

        **2026-11-01 追加**：共享会话里表单会带上一次的住客数，所以先把三个值压回
        下限再按目标加，否则新值是在旧值上叠加的。加完回读一次，与目标不符就报错 ——
        不静默放过，因为「参数悄悄错开」比「直接红」难查得多。
        """
        if adults < rooms:
            raise ValueError(
                f"adults({adults}) < rooms({rooms}) 不支持：加房间会自动补住客，"
                "无法把人数压回低于房间数的状态"
            )
        if rooms < 1 or adults < 1 or children < 0:
            raise ValueError(f"房间/住客取值不合法：{rooms}间{adults}成人{children}儿童")

        self.human.click(self.form.locator(self.ROOM_GUEST).first)
        plus = self.page.locator(self.PLUS)
        expect(plus).to_have_count(3)          # 房间 / 成人 / 儿童

        self._reset_guest_counts()

        # 顺序是契约的一部分：成人 → 儿童 → 房间（房间最后加，才不会顺带补人）
        for index, target, low in ((1, adults, 1), (2, children, 0), (0, rooms, 1)):
            for _ in range(target - low):
                self.human.click(plus.nth(index))

        actual = self.guest_counts()
        if actual != (rooms, adults, children):
            raise ValueError(
                f"住客设置未生效：期望 {(rooms, adults, children)}，实际 {actual}"
            )

        self.human.click(self.page.locator(self.DONE).first)

    # ------------------------------------------------------------ 提交

    def submit_and_capture(self) -> dict[str, list[str]]:
        """点搜索，并抓回真正发出去的 `/hotels/list` 请求参数。

        为什么断言挂在这里：未登录状态下列表页被 passport 登录墙接管（实测
        `probe_www_hotels_04.json`：直接打开 list URL 也回登录页），所以「结果列表渲染」
        在本期拿不到。但请求参数是页面**根据用户输入自己生成**的，恰好是
        「搜索行为真的发生了」的外部证据，而不是我们回读自己写进去的值。
        """
        logger_info("[酒店] 点击搜索")
        with self.page.expect_request(
            lambda req: "/hotels/list" in req.url, timeout=settings.NAV_TIMEOUT_MS
        ) as info:
            # 点搜索前停一下：真人填完表单通常要扫一眼再提交。
            self.human.think()
            self.human.click(self.form.locator(self.SEARCH_BTN).first)
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
        # 直连列表页同样导航后停一拍（HUMAN=1），与 BasePage.goto 口径一致（边界 3）。
        self.human.read()

    def result_card_count(self) -> int:
        """酒店卡片数量。类名语义前缀 `hotel-card`（实测 13 条，见 probe 06）。"""
        return self.page.locator("[class*='hotel-card']").count()

    def wait_result_list(self, timeout_ms: int | None = None) -> bool:
        """等「结果列表就绪」：URL 进 `/hotels/list`，且至少一张卡片可见。

        **返回 bool 而不是抛异常**，因为调用方需要在这个时刻拿到现场再断言：
        落 passport、被 WAF 拦、列表慢，三种情况超时后都长一个样，
        直接抛 TimeoutError 会把「为什么会话来着」埋进异常栈。

        实测（2026-11-01，probe_hotel_auth_search.py）：
          登录态提交 → `hotels.ctrip.com/hotels/list?...` → 13 张 `[class*='hotel-card']`
          匿名态提交 → `passport.ctrip.com/user/login?...`（K-04）
        """
        limit = timeout_ms or settings.NAV_TIMEOUT_MS
        try:
            self.page.wait_for_url(re.compile(r"/hotels/list"), timeout=limit)
        except PlaywrightTimeout:
            return False
        try:
            self.page.locator("[class*='hotel-card']").first.wait_for(
                state="visible", timeout=limit
            )
        except PlaywrightTimeout:
            return False
        return True

    @staticmethod
    def display_of(params: dict[str, list[str]]) -> str:
        """`display` 参数是 URL 编码的城市全名，解码后便于断言。"""
        return unquote(params.get("display", [""])[0])

    # ------------------------------------------------------------ 结果页 → 重新搜索

    def back_to_home_via_left_nav(self) -> bool:
        """从结果页点左侧竖排导航「酒店」回首页，返回是否回到带表单的首页。

        为什么这是「重新搜索」的正路（2026-11-01 用户截图指出的实际路径）：
        结果页顶部虽然也有搜索框，但它回填的是上一次的条件，改城市要先清空、
        改日期要走它自己的一套日历，路径与首页不是同一个组件。
        点左侧「酒店」回到 `www.ctrip.com/` 首页，拿到的是那个有
        `#hotels-destination` 的完整表单 —— 五段齐全（目的地/入住/退房/房间住客/
        酒店级别/关键词），和 `HotelSearchPage.open()` 打开的是同一张表单。

        实测（probe_hotel_re_search.py，2026-11-01 18:00）：
          点击 `div[class*='lsn_first_nav_wrap']`（46×42 at(15,68)，text=「酒店」）
          → 落 `https://www.ctrip.com/`（同页跳转，不开新标签）
          → `#hotels-destination` count=1，可重新填表

        返回 bool 的理由与 wait_result_list 一致：判不了就返回 False，
        由用例带着现场（URL / 元素计数）去断言，而不是抛一个盖住原因的异常。
        """
        entry = self.page.locator(self.NAV_ITEM).filter(has_text=self.NAV_ITEM_TEXT).first
        try:
            entry.wait_for(state="visible", timeout=settings.DEFAULT_TIMEOUT_MS)
        except PlaywrightTimeout:
            return False
        self.human.click(entry)
        try:
            self.page.locator(self.DESTINATION).wait_for(
                state="visible", timeout=settings.NAV_TIMEOUT_MS
            )
        except PlaywrightTimeout:
            return False
        # 回首页后站点还会自己弹营销浮层，顺手关掉，避免挡住下面的控件。
        self.dismiss_overlays()
        return True
