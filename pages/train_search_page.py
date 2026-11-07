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
from utils import throttle
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
    #
    # 2026-11-02 补（probe_form_mode2.py 实测，reports/probe/form_mode2_*.json）：
    #   `div.search-date div.date` 在**任何态**都返回 2 个节点 —— 第 2 个恒为「返回日期取消返程…」，
    #   单程 / 中转态下它的 visible=false、边界框全 [0,0,0,0]。
    #   所以取日期块必须按「可见 + 文本前缀（出发/返回）」筛，不能按 index 写死，
    #   也不能用 .count() 判断「这态有没有返程日期」（永远返回 2，会误判）。
    DATE_INPUT = {"depart": "#label-departDate", "back": "#label-returnDate"}
    DATE_BLOCK_ANY = "div.search-date div.date"
    DATE_BLOCK_WORD = {"depart": "出发", "back": "返回"}

    # 日历是自定义 widget + 遮罩层（遮罩不关会拦住后续点击）
    # 两个日历各一个 wrap：出发 `.widget-calendar-depart-wrap`、返程 `.widget-calendar-back-wrap`
    #   —— 注意是 **back 不是 return**：2026-11-02 实测 `.widget-calendar-return-wrap` 数量为 0，
    #     按"返回→return"的直觉写必然找不到，然后误报成"返程日历没弹出来"。
    # 两者内部同构：ul.widget-calendar-hd h3（双月标题）+ ul.date-bd ×2 + li>strong（日号），
    #   所以下面的翻月/点日逻辑可以按 which 复用，不需要两套实现。
    CALENDAR = {
        "depart": ".widget-calendar-depart-wrap",
        "back": ".widget-calendar-back-wrap",
    }
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

    # ---------------------------------------------------------------- 首页三态（2026-11-02 探测）
    #
    # 来源：tools/probes/probe_form_mode.py / probe_form_mode2.py 两次有头探测，
    # 产物 reports/probe/form_mode{,_2}_20261102-*.json。三条结论：
    #   1. 「单程 / 往返 / 中转」**不是 radio，是 ul.search-select > li > button**；
    #      选中态只落在 `li.cur` 上（aria-checked 与 input[checked] 实测都是 None）。
    #      ⇒ 判"当前在哪一态"只能读 li.cur，用 get_by_role("radio") 永远拿不到。
    #   2. 三态的 <input> 集合**完全相同**：label-departStation / label-arriveStation /
    #      label-departDate / label-returnDate / label-transitStation 恒在 DOM，靠显隐切换。
    #      这正是"返程日历当年没探过"的真相 —— 它一直存在，只是单程态不可见。
    #   3. 点按钮再搜索，结果页 URL 与 URL 直达**逐参数等价**：
    #        往返 ticketType=1&dStation=…&aStation=…&dDate=…&rDate=…
    #        中转 ticketType=2&…&rDate=（空串）
    #      ⇒ 改走 UI 不改变被测对象，只是把"表单这条路通不通"也纳入覆盖。
    MODE_SELECT = "ul.search-select"
    MODE_NAMES = {"oneway": "单程", "round": "往返", "transfer": "中转"}
    # 中转态表单多出的一块（2026-11-02 实测可留空直接搜，本轨道不填 —— 见 search_transfer）
    TRANSIT_CITY = "#label-transitStation"

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
    # 2026-11-02 补 S：中转方案里实测出现 `S4793 / S4771 / S4907` 等市郊车次，
    #   文本形如 `第二程 S4793 抢票`（广州→东莞）。不加就整条中转轨道抽不到第二程。
    # 仍**不加** L / X：至今没有一条实测样本，遇到再按同样流程补（不凭"应该也有"扩字头）。
    TRAIN_NO_RE = re.compile(r"^(?:[GDCZTKYS]\d{1,4}|\d{4})$")
    RESULT_COUNT_RE = re.compile(r"共\s*(\d+)\s*车次")
    DATE_IN_URL_RE = re.compile(r"[?&]dDate=(\d{4}-\d{2}-\d{2})")
    # 结果页的行程类型：0=单程、1=往返、2=中转（三次实测）。断言"点对了子菜单按钮"用它。
    TICKET_TYPE_IN_URL_RE = re.compile(r"[?&]ticketType=(\d+)")
    # 中转/换乘卡片：一个行程由两段拼成，结构上没有「单一车次号」
    TRANSFER_MARKERS = ("换乘",)

    # ---------------------------------------------------------------- 往返（2026-11-02 探测）
    #
    # 来源：tools/probes/probe_round_trip.py / probe_round_trip2.py 两次有头探测，
    # 结果在 reports/probe/round_trip*_20261102-*.json。三条结论直接决定下面怎么写：
    #   1. 往返结果页 `div.return-box` 里挂**两栏** `div.return-list`，0=去程 1=返程；
    #      两栏各自带车型筛选（div.item）与 5 天日期条（ul.date-list）。
    #   2. `.list-item` 在往返页**被复用**：112 个是车次卡（.card-white.list-item），
    #      另有 2 个「精确搜索」卡带 .station-filter-bd。直接数 .list-item 会把车次数虚报 +2，
    #      并把「抽不到车次号」的垃圾条目塞进覆盖率分母 —— 必须显式排除。
    #   3. URL 参数口径来自实测：ticketType=1 是往返（0=单程），配 dStation/aStation/dDate/rDate。
    #      **2026-11-02 起这个常量只用于断言**（校验点「往返」确实把页面带进了往返结果页），
    #      不再用于 goto —— 用户裁决往返必须靠点按钮进，见 search_round_trip。
    RT_TICKET_TYPE_ROUND = "1"
    RT_COLUMN = "div.return-box div.return-list"
    RT_TRAIN_CARD = ".card-white.list-item:not(.station-filter-bd)"
    RT_EXPECTED_COLUMNS = 2
    LEG_DEPART, LEG_RETURN = 0, 1
    RETURN_DATE_IN_URL_RE = re.compile(r"[?&]rDate=(\d{4}-\d{2}-\d{2})")
    # 卡片里的出发时刻。**不用 `^` 锚定**：2026-11-02 实测，抢票卡片的文本以
    # 「成功率：较高」开头（`成功率：较高 20:21 汉口 0时18分 D105 …`），
    # 锚定行首会让这类卡片统统取不到时刻，时段统计凭空少一截。
    # 卡片里第一个 HH:MM 就是出发时刻（到达时刻在其后），用 search 取第一个最稳。
    DEPART_CLOCK_RE = re.compile(r"(\d{1,2}:\d{2})")

    # ---------------------------------------------------------------- 中转（2026-11-02 探测）
    #
    # 来源：tools/probes/probe_transfer.py / probe_transfer2.py / probe_transfer3.py
    # 三次有头探测，产物在 reports/probe/transfer_*.json。四条结论直接决定下面怎么写：
    #   1. **URL 口径**：`ticketType=2` 是中转（`1` 往返、`0` 单程）。**2026-11-02 起只用于断言**，
    #      不再用于 goto —— 用户裁决中转必须靠点「中转」按钮进，见 search_transfer。
    #   2. `.transfer-box` 是**整块容器**（含 h3 标题 + 全部卡片），它的父才是一层 `section`。
    #      第一轮把它当成单卡，于是 `querySelectorAll("section")` 返回 0 —— 层级读反了。
    #   3. 卡片仍是 `div.card-white.list-item`（汉口→深圳 205 张），**与单程/往返同一个 class**；
    #      但 `.list-open` 是每张卡可展开的详情面板，数量与卡片一一对应（205=205），
    #      数它会把方案数正好翻倍。这是本页特有的坑，往返页没有。
    #   4. 卡内结构：`.from .time/.station`、`.trans-station`（「深圳北转」）、
    #      `.to .time/.station`、`.rbox .price`、`.rbox .surplus-list li`（「第一程 G3785 二等座有票」）、
    #      `.tag-tzhc`（「同站换乘」标签，非必有）。
    TICKET_TYPE_TRANSFER = "2"
    TRANSFER_BOX = ".transfer-box"
    TRANSFER_HEADING = ".transfer-box h3"
    TRANSFER_HEADING_TEXT = "中转方案推荐"
    # `:not(.list-open)` 是防御：当前 `.list-open` 不带 list-item，但两者数量一一对应，
    # 万一哪天站点给它加上 list-item，方案数就会翻倍，这里先挡住。
    TRANSFER_CARD = ".transfer-box .card-white.list-item:not(.list-open)"
    # 「第一程 G3785」「第二程 D7565」；车次号交给 TRAIN_NO_RE 校验形态，不在这里写死字头。
    LEG_LABEL_RE = re.compile(r"第([一二])程\s*([A-Za-z]?\d{1,4})")

    def __init__(self, page: Page, base_url: str | None = None) -> None:
        super().__init__(page, base_url)
        # 最近一次点选时面板里的候选项（纯文本快照），只在失败时进异常消息。
        # 留着它的理由：这类「点 A 填成 B」的问题，没有现场快照就只能靠猜。
        self._pick_trace: list[str] = []
        # 操作链（2026-11-02 用户要求）：每个真实 UI 动作记一行
        # [序号] 控件类型 | 定位方式 | 执行动作 | 等待条件 | 结果断言
        self.actions: list[dict[str, str]] = []
        # 页面导航次数。本轨道全程只允许 open() 导航一次（子菜单必须点击进入），
        # 用例断它 == 1 就是"没有任何 URL 直达"的硬证据。
        self.navigations = 0

    # ---------------------------------------------------------------- 操作链记录

    def goto(self, path: str = "/") -> None:
        """覆写只为计数：子菜单禁止 URL 直达，用例断言导航次数恒为 1。"""
        self.navigations += 1
        super().goto(path)

    def _log(self, kind: str, locate: str, action: str, wait: str, result: str) -> None:
        self.actions.append(
            {
                "kind": kind,
                "locate": locate,
                "action": action,
                "wait": wait,
                "result": result,
            }
        )

    def action_log(self) -> str:
        """操作链的可读文本（markdown 表格），写进报告也方便贴到交付说明里。"""
        lines = ["| # | 控件类型 | 定位方式 | 执行动作 | 等待条件 | 结果断言 |", "|---|---|---|---|---|---|"]
        for i, rec in enumerate(self.actions, 1):
            lines.append(
                f"| {i} | {rec['kind']} | {rec['locate']} | {rec['action']} "
                f"| {rec['wait']} | {rec['result']} |"
            )
        lines.append("")
        lines.append(f"页面导航次数（只允许 `open()` 那一次）：{self.navigations}")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 页面动作

    def open(self) -> None:
        """打开一级菜单。**全程唯一允许的 URL 导航**（用户 2026-11-02 裁决）。"""
        self.open_and_check("/")
        self._log(
            "一级菜单",
            self.base_url + "/",
            "open（全程唯一一次 URL 导航）",
            "wait_ready + 风控体检",
            f"title={self.page.title()[:24]!r}｜navigations={self.navigations}",
        )

    def select_mode(self, mode: str) -> None:
        """点首页「单程 / 往返 / 中转」，并确认选中态真的切过去了。

        mode: "oneway" | "round" | "transfer"

        选中态的唯一判据是 `ul.search-select li.cur` —— 实测三态的 aria-checked 与
        input[checked] 都是 None，用 get_by_role("radio") 拿不到任何东西。
        点完只等"点击成功"不够：按钮是 button，点下去到表单重排之间有一拍，
        此时立刻去点城市输入框会点到旧布局上。所以切完还要等该态专属控件就位
        （见 _wait_mode_ready），这一步同时充当"切换真的生效了"的证据。
        """
        name = self.MODE_NAMES[mode]
        button = self.page.locator(f"{self.MODE_SELECT} button:text-is('{name}')").first
        expect(button).to_be_visible()

        if self.current_mode() == name:
            logger_info(f"[火车票] 已在「{name}」态，跳过切换")
            self._wait_mode_ready(mode)
            self._log(
                "按钮（子菜单切换）",
                f'{self.MODE_SELECT} button:text-is("{name}")',
                "未点击（已在该态）",
                f"该态专属控件就位（{mode}）",
                f"current_mode={self.current_mode()!r}",
            )
            return

        self.human.click(button)
        expect(self.page.locator(f"{self.MODE_SELECT} li.cur button")).to_have_text(
            name, timeout=settings.EXPECT_TIMEOUT_MS
        )
        self._wait_mode_ready(mode)
        logger_info(f"[火车票] 已切换到「{name}」态")
        self._log(
            "按钮（子菜单切换）",
            f'{self.MODE_SELECT} button:text-is("{name}")',
            "click",
            f'li.cur 文本 == "{name}" 且该态专属控件就位（{mode}）',
            f"current_mode={self.current_mode()!r}",
        )

    def current_mode(self) -> str:
        """当前选中的态（「单程」/「往返」/「中转」）；读不到返回空串。"""
        cur = self.page.locator(f"{self.MODE_SELECT} li.cur button").first
        if not cur.count():
            return ""
        return (cur.inner_text() or "").strip()

    def _wait_mode_ready(self, mode: str) -> None:
        """等该态专属控件就位：只等"点了"不够，要等"布局换完了"。

        三个态的期望形态都来自 2026-11-02 实测：
          round    → 可见日期块变 2 个（第 2 个是返回日期）
          transfer → 「指定中转城市」输入框可见
          oneway   → 可见日期块只剩 1 个
        """
        if mode == "transfer":
            expect(self.page.locator(self.TRANSIT_CITY)).to_be_visible(
                timeout=settings.EXPECT_TIMEOUT_MS
            )
            return
        want = 2 if mode == "round" else 1
        expect(self.page.locator(f"{self.DATE_BLOCK_ANY}:visible")).to_have_count(
            want, timeout=settings.EXPECT_TIMEOUT_MS
        )

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
                self._log(
                    "输入框 + 自定义下拉（联想面板）",
                    f"{selector}（面板限定在 {self.CITY_WRAPPER[field]} 内）",
                    f"click → 逐字输入「{city}」→ click 联想项「{city}」",
                    "ul.widget-search-bd 里出现内层文本 == 城市的项",
                    f"input_value={actual!r}（第 {attempt} 轮点选）",
                )
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
                try:
                    self.human.click(items.nth(i))
                except PlaywrightTimeout:
                    # 点下去的那一瞬间面板被重建（K-11 的变体，2026-11-02 实测于
                    # 武昌→汉口 同城区间）：Playwright 的 click 会一直等这个 li，
                    # 15s 后抛 TimeoutError。把它算作「这一轮没点上」交给外层 3 轮重试，
                    # 不让一次瞬时重排直接变成用例失败。
                    self._pick_trace.append(f"[{i}]{text}(点击瞬间消失)")
                    logger_info(f"[火车票] 点「{city}」时面板项消失，本轮作废重来")
                    return False
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

    def select_date(self, target_date: str, which: str = "depart") -> None:
        """在日历控件里点选日期。target_date 形如 2026-11-07；which = "depart" | "back"。

        2026-11-01 改法（保留）：
          1. 点外层可见块 div.search-date div.date，不再点 #label-departDate
             —— 那个 input 带 assist-ib-dom，Playwright 判为不可见，force 也点不动。
          2. 不做 get_by_text(target_date) 兜底：日历没打开时该文本不在页面上
             （页面只显示当前选中日期），这条兜底只会把错误变成 15s 的 click 超时。
          3. 点不动/日历不出，一律转 WidgetUnavailable（由用例按 K-01 记 blocked），
             不让 Playwright 的 TimeoutError 漏成 failure 并计入分母。

        2026-11-02 增（往返返程）：按 which 区分两个日期块与两个日历 wrap。
          取日期块的方式从 `.first` 改成「可见 + 文本前缀匹配」—— 实测任何态下
          `div.search-date div.date` 都是 2 个节点，第 2 个（返回日期）在单程/中转态
          不可见；按 index 取会在换态后偷偷点空，还会把报错指向错误的方向。
        """
        target_month, target_day = self._split_date(target_date)
        word = self.DATE_BLOCK_WORD[which]
        label = "出发日期" if which == "depart" else "返回日期"
        logger_info(f"[火车票] 选{label} {target_date}")

        block = self._date_block(which)
        try:
            expect(block).to_be_visible(timeout=settings.EXPECT_TIMEOUT_MS)
            self.human.click(block)
        except (AssertionError, PlaywrightTimeout) as exc:
            raise WidgetUnavailable(
                f"{label}块（含「{word}」的 div.date）点不动，可能被城市面板或日历遮罩拦住，"
                f"无法点选 {target_date}"
            ) from exc

        wrap = self.CALENDAR[which]
        try:
            expect(self.page.locator(f"{wrap} {self.CALENDAR_READY}").first).to_be_visible(
                timeout=settings.EXPECT_TIMEOUT_MS
            )
        except AssertionError as exc:
            raise WidgetUnavailable(
                f"{label}的日历未唤起（{wrap} {self.CALENDAR_READY} 仍不可见），"
                f"无法点选 {target_date}"
            ) from exc

        self._goto_month(target_month, which)

        # 点之前先记基线值，失败时能区分「从来没变」和「变了又被改回去」。
        box = self.page.locator(self.DATE_INPUT[which])
        before = box.input_value().strip()
        self._click_day(target_month, target_day, which)

        # 点完**回读校验**，不生效就报错 —— **不重复点击**（2026-11-07 用户裁决）。
        # 旧版在这里「重点一次格子」：实测该分支从未触发过（全部运行日志里
        # 「重点一次」出现 0 次，而真正失败的那两次是直接读出错值），
        # 也就是说它没救过任何一次，却留下「对同一个死靶子连击」这种典型机器特征。
        # 正确处置：点一次 → 校验 → 不过就带着诊断信息失败，让人去看页面到底怎么了。
        try:
            expect(box).to_have_value(target_date, timeout=2500)
        except AssertionError as exc:
            raise WidgetUnavailable(
                f"{label}点选后为 {box.input_value().strip()!r}，期望 {target_date!r}"
                f"（点击前 {before!r}，点的是 {target_month} {target_day} 日）"
            ) from exc

        # 关掉日历（遮罩不关会拦住搜索按钮）
        self._close_calendar(which)

        actual = self.page.locator(self.DATE_INPUT[which]).input_value().strip()
        if actual != target_date:
            raise WidgetUnavailable(
                f"{label}点选后为 {actual!r}，期望 {target_date!r}"
                f"（点击前 {before!r}，点的是 {target_month} {target_day} 日；"
                f"这次差异出现在**关闭日历之后**，属 K-14）"
            )
        self._log(
            "日期控件（日历弹层）",
            f"含「{word}」的 div.date → {wrap} 内 ul.date-bd li>strong",
            f"click 日期块 → click 日期格「{target_date}」",
            "ul.widget-calendar-hd 可见后按标题下标取对应日期栏",
            f"input_value={actual!r}",
        )

    def _date_block(self, which: str) -> Locator:
        """按「可见 + 文本前缀」找日期块，找不到抛 WidgetUnavailable。

        不看 index 的理由见 select_date 的 2026-11-02 段。
        """
        want = self.DATE_BLOCK_WORD[which]
        blocks = self.page.locator(self.DATE_BLOCK_ANY)
        total = blocks.count()
        for i in range(total):
            item = blocks.nth(i)
            if not item.is_visible():
                continue
            if want in (item.inner_text() or ""):
                return item
        raise WidgetUnavailable(
            f"页面上没有可见的「{want}日期」块（div.date 共 {total} 个）；"
            f"返程日期需要先 select_mode('round')，单程/中转态下它只是隐藏，不是不存在"
        )

    def _month_heads(self, which: str) -> Locator:
        return self.page.locator(self.CALENDAR[which]).locator(self.CALENDAR_MONTH_HEAD)

    def _goto_month(self, month_label: str, which: str) -> None:
        """翻到目标月份。widget 是双月视图，目标常在右栏，翻页只在必要时发生。"""
        for _ in range(3):
            if self._month_visible(month_label, which):
                return
            nxt = self.page.locator(self.CALENDAR[which]).get_by_text("›", exact=True).first
            if not nxt.is_visible():
                break
            # 翻月可能连点：用 quick 档（单次 150–350ms），避免 3×400ms 的累积机械节奏。
            self.human.click_quick(nxt)
        if not self._month_visible(month_label, which):
            raise WidgetUnavailable(f"日历里翻不到 {month_label}")

    def _month_visible(self, month_label: str, which: str) -> bool:
        heads = self._month_heads(which)
        want = _norm(month_label)
        try:
            for i in range(heads.count()):
                if _norm(heads.nth(i).inner_text()) == want:
                    return heads.nth(i).is_visible()
        except Exception:  # 日历在动画中被重建，这一轮就当没看到
            return False
        return False

    def _click_day(self, month_label: str, day: str, which: str) -> None:
        """点具体某一天。

        双月视图里两栏各有一套 ul.date-bd，顺序与标题栏一致 —— 按标题下标取对应那栏，
        避免「同一天在两栏都出现时点错月份」（旧的 filter(has_text=...) 做法会漂）。
        """
        heads = self._month_heads(which)
        bodies = self.page.locator(self.CALENDAR[which]).locator(self.CALENDAR_MONTH_BODY)
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

        body = bodies.nth(index)
        want_day = _norm(day)

        # 先把指针挪开，让上一次 hover 引起的重绘结束（2026-11-07 加）。
        # 为什么必须在解析格子**之前**：日历格悬停会刷新票价/余票并重绘网格，
        # 指针如果还停在日历上，重绘就可能在「解析」与「点击」之间插队 ——
        # 实测两次点错日期（11-14→11-17、11-15→11-17）都发生在这种情形下。
        self.human.move_neutral()

        # **按内容定位，不按位置定位**（2026-11-07 改）。
        # 旧写法是 `cells.nth(i)`：先遍历读出「第 i 个格子的数字」，再回手点 nth(i)。
        # 这两步之间日历可能重绘（鼠标经过就会刷新价格/余票），重绘后同一个**位置**
        # 可能已经是另一天 —— 实测踩到：目标 11-15 落成 11-17（同日同用例单跑不复现，
        # 只在「共享 page + 上一个模块刚跑完」时出现）。
        # `li:has(strong:text-is('15'))` 每次点击前按内容重新解析，重绘也不影响。
        cell = body.locator(
            f"{self.CALENDAR_DAY}:has({self.CALENDAR_DAY_LABEL}:text-is('{want_day}'))"
        )
        if not cell.count():
            raise WidgetUnavailable(f"{month_label} 里找不到 {day} 日")
        target = cell.first
        cell_text = target.inner_text() or ""
        logger_info(f"[火车票] 点 {month_label} {day} 日 → {cell_text!r}")
        # 未开售的日期格显示「预约」而不是票价：点它不会改日期，输入框停在默认值，
        # 后面只会得到「点选后为 X，期望 Y」这种说不清原因的报错。
        # 实测 2026-11-07：T+15 正好压在预售期边界，落在这里。
        if "预约" in cell_text:
            raise WidgetUnavailable(
                f"{month_label}{day} 日尚未开售（日历格显示「预约」），无法选中；"
                f"请把 depart_offset_days 收进可售窗口（实测 +13 以内）"
            )

        # 点之前先验「这个坐标上最上层的是谁」（2026-11-07 加）。
        # 为什么不能只靠 `locator.click()`：它自带「元素可接收事件」检查，但通过标准是
        # 「命中的元素在目标**内部**」。实测失败现场（面板自动关闭、日期仍为默认值）说明
        # 命中的是**面板外**（遮罩），而那种情况 click 不会拦 —— 点完只留下「值没变」。
        # 这里自己看一眼：命中的不是这一格就**不点**，等重绘后重新解析（最多 3 轮）。
        # 注意这不是「连击」：3 轮里最多点 **1** 次，前 2 轮只重新解析、不产生点击。
        last_hit = ""
        for attempt in range(3):
            hit = self._hit_test(target)
            if hit is True:
                break
            if hit is None:            # 取不到坐标：交给 click 用 Playwright 自己的错误说清楚
                break
            last_hit = hit
            logger_info(f"[火车票] 目标格未就位（第 {attempt + 1} 轮）：命中 {hit}，重解析后再点")
            self.human.pause(self.human.rhythm.hover_clear_ms)
            cell = body.locator(
                f"{self.CALENDAR_DAY}:has({self.CALENDAR_DAY_LABEL}:text-is('{want_day}'))"
            )
            if not cell.count():
                raise WidgetUnavailable(f"{month_label} 里找不到 {day} 日（重解析后消失）")
            target = cell.first
        else:
            raise WidgetUnavailable(
                f"{month_label}{day} 日的格子被其它元素压住（命中「{last_hit}」），"
                f"不盲点；请检查日历是否被浮层/遮罩盖住"
            )

        self.human.click_soon(target)

    def _hit_test(self, target: Locator) -> "bool | str | None":
        """看 `target` 中心坐标上最上层的元素是不是**这一格**。

        返回：`True` 是同一格；`str` 是命中的别的元素（人可读描述，用于报错）；
        `None` 表示取不到坐标（元素没布局，交给 click 自己失败）。
        比较用 `getBoundingClientRect()` 与 Playwright 的 `bounding_box()` 对齐 ——
        两者都是视口坐标，容差 2px 覆盖亚像素取整。
        """
        box = target.bounding_box()
        if not box:
            return None
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        if x < 0 or y < 0:
            return f"目标在视口外（{x:.0f},{y:.0f}）"
        result = self.page.evaluate(
            """([x, y, tx, ty, tw, th]) => {
                const el = document.elementFromPoint(x, y);
                if (!el) return { same: false, desc: 'null' };
                const li = el.closest('li');
                const own = el.tagName + '.' + String(el.className || '').slice(0, 28);
                if (!li) return { same: false, desc: own };
                const text = (li.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 20);
                const desc = 'li[' + text + '] ← ' + own;
                const r = li.getBoundingClientRect();
                const same = Math.abs(r.x - tx) < 2 && Math.abs(r.y - ty) < 2
                          && Math.abs(r.width - tw) < 2 && Math.abs(r.height - th) < 2;
                return { same, desc };
            }""",
            [x, y, box["x"], box["y"], box["width"], box["height"]],
        )
        if not isinstance(result, dict):
            return None
        if result.get("same"):
            return True
        return str(result.get("desc") or "unknown")

    def _close_calendar(self, which: str = "depart") -> None:
        """关日历遮罩。选完日期后 widget 通常自己就关了，所以先判在不在。

        2026-11-01 实测踩到：单日选择完成后遮罩会被整个摘除，此时再 click 会一直等
        「waiting for locator(...)」直到 15s 超时 —— 关一个已经不在的东西不该算失败。

        2026-11-07 修（这是 K-14「点了 A 落到 B」的真因）：
          旧实现 `mask.click(force=True)` 点的是**遮罩中心**，而遮罩中心正好压在日历面板上，
          `force=True` 又跳过了命中校验 ⇒ 这一下实际点在**面板内部的日期格**上，
          把刚选好的日期改成别的天。
          证据：三次失败的报错文本都来自**关闭后的复查**（`select_date` 里第一次回读拿到的是
          目标值，报错那句不带「点击前 …」），也就是「点对了 → 关日历时被改掉」。
          现在的做法：① 先给它 800ms 自己关；② 关不掉就在**遮罩上、面板外**找一个点，
          用**普通点击**（不 force）关，让遮罩自己收到这次点击；③ 还不行再按 Esc。
        """
        mask = self.page.locator(self.CALENDAR_MASK).first
        try:
            if not mask.is_visible():
                return
        except (AssertionError, PlaywrightTimeout):
            return

        # ① 点完日期后通常自动关闭，先等一段，别急着补点击
        try:
            expect(mask).to_be_hidden(timeout=800)
            return
        except AssertionError:
            pass

        # ② 面板还在：点遮罩上、面板外的位置（点面板内部会改日期）
        point = self._point_outside_panel(which)
        if point is not None:
            logger_info(f"[火车票] 日历未自动关闭，点面板外 ({point[0]}, {point[1]})")
            self.page.mouse.click(point[0], point[1])
            try:
                expect(mask).to_be_hidden(timeout=settings.EXPECT_TIMEOUT_MS)
                return
            except AssertionError:
                pass

        # ③ 兜底：Esc
        logger_info("[火车票] 点面板外仍未关闭，按 Esc")
        self.page.keyboard.press("Escape")
        try:
            expect(mask).to_be_hidden(timeout=1500)
        except AssertionError as exc:
            raise WidgetUnavailable(
                f"日历遮罩 {self.CALENDAR_MASK} 关不掉（已试过点面板外与 Esc），会拦住搜索按钮"
            ) from exc

    def _point_outside_panel(self, which: str) -> "tuple[int, int] | None":
        """在遮罩上找一个**不落在日历面板里**的坐标，找不到返回 None。

        做法不是猜：把面板矩形交给页面，按「面板上/下方各 16px、视口四角与左右中点」
        生成候选点，再用 `elementFromPoint` 挑第一个**最上层确实是遮罩**的点。
        之所以要验层级：面板可能比视口还宽/高（贴边布局），单纯算几何会算到面板里。
        """
        panel = self.page.locator(f"{self.CALENDAR[which]} {self.CALENDAR_READY}").first
        box = panel.bounding_box()
        point = self.page.evaluate(
            """(box) => {
                const vw = window.innerWidth, vh = window.innerHeight;
                const cands = [];
                if (box) {
                    const cx = Math.round(box.x + box.width / 2);
                    cands.push([cx, Math.max(8, Math.round(box.y - 16))]);
                    cands.push([cx, Math.min(vh - 8, Math.round(box.y + box.height + 16))]);
                }
                cands.push([8, 8], [vw - 8, 8], [8, vh - 8], [vw - 8, vh - 8],
                           [8, Math.round(vh / 2)], [vw - 8, Math.round(vh / 2)]);
                for (const [x, y] of cands) {
                    const el = document.elementFromPoint(x, y);
                    if (el && String(el.className || '').includes('widget-calendar-mask')) {
                        return [x, y];
                    }
                }
                return null;
            }""",
            box,
        )
        if isinstance(point, list) and len(point) == 2:
            return int(point[0]), int(point[1])
        return None

    def submit(self) -> None:
        """点搜索并等结果页。"""
        logger_info("[火车票] 点击搜索并等待结果页")
        # 全局节流：提交是对外动作，与上一次导航/提交至少隔 N 秒。
        throttle.wait_action()
        self.human.click(self.search_button())
        self.page.wait_for_url(re.compile(r"/webapp/train/list"), timeout=settings.NAV_TIMEOUT_MS)
        self.wait_ready()
        self.assert_not_blocked()
        self._log(
            "按钮",
            f'role=button[name="{self.SEARCH_BUTTON_NAME}"]',
            "click",
            "URL 跳到 /webapp/train/list 且 load 完成",
            f"url={self.page.url[:96]}…（ticketType={self.url_ticket_type()}）",
        )

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

    def url_ticket_type(self) -> str | None:
        """结果页 URL 里的 ticketType —— 用它验证「点对了子菜单按钮」。

        2026-11-02 起往返/中转都走首页表单，`ticketType` 是"点的那个按钮真的生效"的
        唯一硬证据：0=单程、1=往返、2=中转（三次实测）。不查它就可能出现
        「点了往返但页面其实还是单程态」却因结果页有列表而蒙混过关的情况。
        """
        match = self.TICKET_TYPE_IN_URL_RE.search(self.page.url)
        return match.group(1) if match else None

    @staticmethod
    def _split_date(depart_date: str) -> tuple[str, str]:
        """拆成 (月份标题, 日号)。月份标题必须带空格 —— DOM 里就是「2026年 11月」。"""
        year, month, day = depart_date.split("-")
        return f"{year}年 {int(month)}月", str(int(day))

    # ---------------------------------------------------------------- 往返：打开与取状态

    def search_round_trip(
        self, from_city: str, to_city: str, depart_date: str, return_date: str
    ) -> None:
        """走首页表单：点「往返」→ 填两地 → 选出发日 → 选返回日 → 点搜索。

        2026-11-02 用户裁决：往返必须靠**点按钮 + 填输入框**进子菜单，不能改 URL 直达；
        只有回到一级菜单（trains.ctrip.com 首页）才允许用 URL。

        旧版走 URL 直达的理由是「返程日历没有探测记录，硬写就是猜」。该理由现在已经消失：
        probe_form_mode2.py 探明返程 wrap 是 `.widget-calendar-back-wrap`（back，不是 return），
        且与出发日历同构 —— 于是把 URL 直达那条路**删掉**，而不是与 UI 路径并存。
        并存会让两条路各自跑出不同结论（本项目已有「两套口径就是两套真相」的教训）。

        实测（2026-11-02，汉口→深圳 D+4/D+6）：点出来的结果页 URL 为
        `?ticketType=1&dStation=…&aStation=…&dDate=2026-11-06&rDate=2026-11-08&…`，
        与旧版 URL 直达**逐参数相同**，结果页 2 栏 / 112 卡。
        """
        self.select_mode("round")
        self.select_city("depart", from_city)
        self.select_city("arrive", to_city)
        self.select_date(depart_date)
        # 返程日联动：选完出发日后返回日期块会自动顺延（实测 +2 天），
        # 但那是页面的猜测，不是用例要的日期 —— 一律显式点一遍。
        self.select_date(return_date, which="back")
        self.submit()

    def leg_columns(self) -> int:
        """结果页渲染出几栏（去程/返程）。正常是 2；1 或 0 由用例断言去红，这里只报数。"""
        return self.page.locator(self.RT_COLUMN).count()

    def leg_scope(self, leg: int) -> str:
        """某栏头部那段文本，形如「去程 汉口-深圳 11-05 周四 …」。

        取前 40 字就够：要的是「这栏是哪一段行程」，不是整栏 56 条卡片。
        整栏 inner_text 会把 56 条卡片全读回来（单程那次的 538 条教训）。
        """
        column = self.page.locator(self.RT_COLUMN).nth(leg)
        try:
            expect(column).to_be_visible(timeout=settings.EXPECT_TIMEOUT_MS)
        except AssertionError:
            return ""
        return column.evaluate(
            "el => (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 40)"
        )

    def leg_texts(self, leg: int) -> list[str]:
        """某栏的车次卡文本，**一次**取回。返回空列表表示这栏没有卡。

        排除 `.station-filter-bd`（「精确搜索」卡）的理由写在定位器区那段注释里：
        它带 `.list-item` 却不是车次，会让条数虚高、覆盖率分母被污染。

        等待窗用 NAV_TIMEOUT_MS（与单程 `result_texts` 同口径）：实测往返页
        首屏 2~4s 出列表，连跑时会慢；放宽的只是等待，判定门槛没动。

        2026-11-02 补「文本就绪」等待（起因见 _wait_leg_text_ready）：
        首卡可见 ≠ 整栏文本填好。只等首卡就会拿到一批空壳卡，覆盖率凭空掉一截。
        """
        cards = self.page.locator(self.RT_COLUMN).nth(leg).locator(self.RT_TRAIN_CARD)
        try:
            expect(cards.first).to_be_visible(timeout=settings.NAV_TIMEOUT_MS)
        except AssertionError:
            return []
        self._wait_leg_text_ready(leg)
        return cards.evaluate_all("els => els.map(el => el.innerText || '')")

    def _wait_leg_text_ready(self, leg: int) -> None:
        """等某栏的卡片文本真的填进去，超时就用当前快照（由覆盖率断言去红）。

        为什么需要这一步（2026-11-02 实测，`rt-hk-sz-weekend-T+3`）：
          走首页表单进结果页时，去程 **55 张卡**都在 DOM 里、第一张也可见，
          但只有 43 张的 innerText 有内容，另 12 张是空壳 ⇒ 覆盖率算成 78% 红掉。
          同一 URL 直接 goto（等得更久）时 55/55 全有文本，覆盖率 100%。
          ⇒ 差异不是数据不同，是**取值时机**不同。首卡可见只是个过早的信号。

        判据用「非空文本的卡片占比 ≥ 95%」而不是"车次号覆盖率"：
        后者要在 JS 里复刻 TRAIN_NO_RE，等于把车次号口径写成两套（本项目明确禁止）。
        留 5% 余量是给「本来就无文本的占位卡」—— 真要卡住，也该由覆盖率的 90% 门槛去红，
        不该在这里变成 15s 超时。
        """
        try:
            self.page.wait_for_function(
                """
                ({sel, leg}) => {
                  const cols = document.querySelectorAll(sel);
                  const col = cols[leg];
                  if (!col) return false;
                  const cards = col.querySelectorAll(".card-white.list-item:not(.station-filter-bd)");
                  if (!cards.length) return false;
                  let filled = 0;
                  for (const card of cards) {
                    if ((card.innerText || "").trim().length > 4) filled++;
                  }
                  return filled >= cards.length * 0.95;
                }
                """,
                arg={"sel": self.RT_COLUMN, "leg": leg},
                timeout=settings.NAV_TIMEOUT_MS,
                polling=200,  # 显式定时轮询：raf 在挂起的渲染器上等不到下一帧（K-06）
            )
        except PlaywrightTimeout:
            # 不抛：没填满这件事由覆盖率断言给出可读的失败信息，这里只负责"等到不能再等"。
            logger_info(f"[火车票] 第 {leg} 栏文本未在 {settings.NAV_TIMEOUT_MS}ms 内填满，按当前快照继续")

    def url_return_date(self) -> str | None:
        """结果页 URL 里的 rDate —— 用它验证「返程日真的生效了」。"""
        match = self.RETURN_DATE_IN_URL_RE.search(self.page.url)
        return match.group(1) if match else None

    @classmethod
    def depart_clock_of(cls, text: str) -> str | None:
        """从卡片文本里取出发时刻（HH:MM）；取不到返回 None。

        时段维度只做**软统计**（见 round_trip_cases.yaml 的 prefer_window 注释）：
        页面确实有「6点 - 12点」这类芯片，但它的归属容器没探明
        （probe_round_trip3.py 里两次点击都超时），所以不点它、只读结果做分布统计。
        """
        match = cls.DEPART_CLOCK_RE.search(text or "")
        return match.group(1) if match else None

    @staticmethod
    def in_window(clock: str, start_hour: int, end_hour: int) -> bool:
        """HH:MM 是否落在 [start_hour, end_hour) 内。纯字符串→整数，不碰 DOM。"""
        try:
            hour = int(clock.split(":")[0])
        except (ValueError, AttributeError):
            return False
        return start_hour <= hour < end_hour

    # ---------------------------------------------------------------- 中转：打开与取数

    def search_transfer(self, from_city: str, to_city: str, depart_date: str) -> None:
        """走首页表单：点「中转」→ 填两地 → 选出发日 → 点搜索。

        2026-11-02 用户裁决：中转与往返一样，必须靠点按钮进子菜单，不能改 URL 直达。
        旧版走 URL 的理由是「首页那个『中转』是 button 不是 radio，点完还要填 4 个控件」，
        现在 4 个控件全探完了（probe_form_mode2.py），理由不成立，URL 路径删除。

        「指定中转城市」**留空**：实测该框（`#label-transitStation`，placeholder「中转城市」）
        不填也能出方案（汉口→深圳 201 张卡，标题「中转方案推荐」），
        而填它需要把联想面板/候选城市再探一遍 —— 没有实测样本就不写定位器，避免 K-01 那类
        "填了个看起来对的值、其实没选上"的假通过。留空会在报告里写明，不让它变成隐形假设。

        实测（2026-11-02，汉口→深圳 D+4）：结果页 URL 为
        `?ticketType=2&dStation=…&aStation=…&dDate=2026-11-06&rDate=&…`，
        与旧版 URL 直达逐参数相同（rDate 是空串）。
        """
        self.select_mode("transfer")
        self.select_city("depart", from_city)
        self.select_city("arrive", to_city)
        self.select_date(depart_date)
        self.submit()

    def transfer_heading_text(self) -> str:
        """中转结果页的标题（正常是「中转方案推荐」）。取不到返回空串，由用例断言去红。"""
        loc = self.page.locator(self.TRANSFER_HEADING).first
        try:
            expect(loc).to_be_visible(timeout=settings.EXPECT_TIMEOUT_MS)
        except AssertionError:
            return ""
        return loc.evaluate("el => (el.innerText || '').trim()")

    def transfer_card_count(self) -> int:
        """中转方案卡条数。`.list-open`（详情面板）不算 —— 见定位器区那段注释。"""
        return self.page.locator(self.TRANSFER_CARD).count()

    def transfer_cards(self) -> list[dict]:
        """一次取回全部中转卡的结构化字段；没有卡时返回空列表。

        为什么一次 evaluate_all 而不是逐条 locator：汉口→深圳 实测 205 张卡，
        逐条取字段会产生 205×9 次协议往返（单程 538 条那次已经吃过这个亏）。
        """
        cards = self.page.locator(self.TRANSFER_CARD)
        try:
            expect(cards.first).to_be_visible(timeout=settings.NAV_TIMEOUT_MS)
        except AssertionError:
            return []
        return cards.evaluate_all(
            r"""
            els => els.map(el => {
              const norm = (s) => (s == null ? "" : String(s)).replace(/\s+/g, " ").trim();
              const q = (sel) => { const x = el.querySelector(sel); return x ? norm(x.innerText) : ""; };
              return {
                from_time: q(".from .time"),
                from_station: q(".from .station"),
                to_time: q(".to .time"),
                to_station: q(".to .station"),
                trans_station: q(".trans-station"),
                trans_text: q(".trans"),
                price: q(".rbox .price"),
                legs: Array.from(el.querySelectorAll(".rbox .surplus-list li")).map(li => norm(li.innerText)),
                same_station_tag: !!el.querySelector(".tag-tzhc"),
              };
            })
            """
        )

    @classmethod
    def transfer_leg_numbers(cls, legs: list[str]) -> list[str]:
        """从「第一程 G3785 二等座有票」这类文本里抽两段车次号（按第一程/第二程顺序）。

        形态仍由 `TRAIN_NO_RE` 校验，不在这里另写一套字头 —— 两套口径就是两套真相。
        实测存在 `第二程 S4793 抢票`（抢票态）这类文本，它同样是合法车次号；
        抽不出的（例如「第二程 暂无」）直接跳过，**不补、不猜**。
        """
        found: dict[str, str] = {}
        for item in legs or []:
            match = cls.LEG_LABEL_RE.search(item)
            if not match:
                continue
            number = match.group(2).upper()
            if cls.TRAIN_NO_RE.match(number):
                found.setdefault(match.group(1), number)
        return [found[key] for key in ("一", "二") if key in found]
