"""城市输入的公共封装：输入 → 等面板 → **点选候选项** → 验证。

为什么必须有这一层（2026-11-01 实测，两张用户截图 + 一次失败现场）：

    机票填「深圳」后直接点搜索，页面没跳转，输入框上方红字「请选择出发城市」，
    城市选择面板还展开着。原因不是选择器写错，是 `fill()` / `type()` 只改了
    input 的显示值，没有触发前端框架的「选中」事件 —— 页面内部的出发城市变量
    仍然是空的。所以**必须点面板里的那一项**，让站点自己把值写进内部状态。

三个站的面板结构完全不同，这个函数用 `CityPicker` 把差异参数化：

    机票  box   input[name='owDCity']
          panel div[class*='city-picker-selector']
          option li[title='{city}']   ← 必须限定在 `div.city-picker-body` 内，
                                         否则 `.first` 会命中「国际及中国港澳台」
                                         那个 tab 里**隐藏**的同名 li（探针 28/29/30
                                         三次都死在这里：`wait_for(visible)` 8 秒超时）
    酒店  box   #hotels-destination
          panel ul:has(#hotels-destination) 的兄弟面板
          option [class*='associative-item']
    火车  已有 `TrainSearchPage.select_city()` 的成熟实现（含「热门城市」两列分支），
          不纳入本模块 —— 换掉它等于把一条已经跑了 27 项单测的路径重写一遍。

**不做的事**：不按 Escape 收面板。实测机票页面自己拦了这个键，按了没反应；
真正有效的是「点下一个控件」让它失焦（`set_depart_date` 点日期框那一步就是）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from playwright.sync_api import Locator, Page, expect

from config import settings


@dataclass(frozen=True)
class CityPicker:
    """一个站点城市选择器的配置。

    `option` 里用 `{city}` 占位；没有占位符时表示「面板里的第一个可见项」。

    `fallback_panel` / `fallback_option` 是为机票准备的第二形态：
    点输入框会先弹出「热门城市网格」（`div.city-picker-selector`），
    打字后切换成「联想列表」（`div.cflt-poi`）。两者是不同组件，
    selector 不能通用；联想列表没渲染出来时（网络慢/联想接口没回）
    退回网格点选，比直接失败更接近真人操作。
    """

    box: str
    panel: str
    option: str
    fallback_panel: str = ""
    fallback_option: str = ""


def _first_visible(scope, selector: str, timeout_ms: int, page: Page) -> Locator:
    """在 scope 里找**第一个可见**的匹配元素。

    **不能用 `.first`。** 2026-11-01 实测两次踩同一个坑：
      · 面板：页面上存在 `class="city-picker-selector  hide"` 的副本，`.first`
        拿到它，`wait_for(visible)` 一直等到超时；
      · 候选项：`li[title='上海']` 在「国内」和「国际及中国港澳台」两个 tab 的
        列表里各有一份，DOM 里排前面的是隐藏的。
    所以统一按「谁是可见的就取谁」处理，不依赖 class 名里有没有 `hide`
    （那个 token 是构建产物，随时可能改）。
    """
    limit = time.monotonic() + timeout_ms / 1000
    while True:
        found = scope.locator(selector)
        for index in range(found.count()):
            candidate = found.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        if time.monotonic() > limit:
            raise TimeoutError(
                f"{timeout_ms}ms 内没有可见的匹配元素：{selector}（候选 {found.count()} 个）"
            )
        # 轮询间隔，不是「等状态」：状态由 is_visible 判定，这里只控制重试频率。
        page.wait_for_timeout(200)


def select_city_by_click(
    page: Page,
    picker: CityPicker,
    city: str,
    *,
    human=None,
    timeout_ms: int | None = None,
) -> tuple[str, bool]:
    """输入城市并点选候选项，返回（最终 input 值, 面板是否已收起）。

    面板没收起**不抛异常**：机票页面就是这么设计的（点完城市面板仍开着，
    要靠点下一个控件才收）。抛异常会把「正常形态」记成失败，
    而真正该关心的是「input 的值有没有变成带三字码的城市」——
    那一条由调用方断言。
    """
    limit = timeout_ms or settings.DEFAULT_TIMEOUT_MS
    click = human.click if human else (lambda loc: loc.click())
    type_text = human.type_text if human else (lambda loc, text: loc.type(text, delay=80))

    box = page.locator(picker.box).first
    click(box)
    box.fill("")
    type_text(box, city)

    try:
        panel = _first_visible(page, picker.panel, limit, page)
        option_selector = picker.option.format(city=city)
    except TimeoutError:
        if not picker.fallback_panel:
            raise
        panel = _first_visible(page, picker.fallback_panel, limit, page)
        option_selector = picker.fallback_option.format(city=city)

    option = _first_visible(panel, option_selector, limit, page)
    click(option)

    # 验证：input 值必须真的变成含城市名的值（站点会补成「上海(SHA)」这种形态）。
    # 只断言「含城市名」而不是完整格式 —— 三字码是站点口径，写死会误红。
    expect(box).to_have_value(re.compile(re.escape(city)), timeout=limit)

    closed = _wait_panel_closed(panel)
    return box.input_value(), closed


def _wait_panel_closed(panel: Locator, timeout_ms: int | None = None) -> bool:
    """面板是否已收起。等不到就返回 False，**不点坐标、不按 Escape**。

    理由见模块头：机票页面的面板在选完城市后仍然开着，是正常形态；
    这里只做观测，收面板交给「点下一个控件」这条实测有效的路径。
    """
    try:
        expect(panel).to_be_hidden(timeout=timeout_ms or settings.EXPECT_TIMEOUT_MS)
        return True
    except AssertionError:
        return False
