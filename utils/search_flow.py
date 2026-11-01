"""三个模块的「一次搜索」公共层：同一个产出契约，各自调自己的 Page Object。

**为什么公共的是「产出」而不是「填表动作」**（这条要说清，否则会有人来「优化」它）：

    用户在需求里写「提取公共的搜索函数（输入框清理、自动补全下拉框选择、
    日期选择、点击搜索）」。这三个站点看起来是同一个站，实际是三套完全不同的控件：

      酒店   www.ctrip.com 首页表单  目的地 `#hotels-destination` + 联想项 + 自定义日历
                                     + 房间住客弹层（3 组加减按钮）
      火车票 trains.ctrip.com        城市面板（含「热门城市」两列）+ 日历 + 车次结果页
      机票   flights.ctrip.com       出发地/目的地 readonly 之外的普通 input + readonly
                                     日期框 + modal 日历（不关会挡住搜索按钮）

    硬抽一个 `fill_search(page, city, date)` 出来，只会有两种下场：
    要么它内部按站点分支（等于把三个 Page Object 塞进一个函数），
    要么它只覆盖最简单的那种（机票的 readonly 日期和酒店的日历就会各自再写一遍）。

    所以公共层定在**契约**上：`SearchOutcome` 统一回答「有没有进结果页、出了几条、
    页面自己生成了什么参数、失败时现场是什么」。用例只对着这个契约断言，
    三个站点各自的控件差异留在各自的 Page Object 里。

**为什么不抛异常**：用户要求「某条失败不能中断后续」。这里把异常收进
`outcome.error`，由用例断言失败（fail）—— 失败而不是 error，报告里能直接看到
「哪个模块、哪组数据、卡在哪一步」，且 pytest 不会因此中止后续用例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pages.flight_search_page import FlightSearchPage
from pages.hotel_search_page import HotelSearchPage
from pages.train_search_page import TrainSearchPage
from utils import allure_support


@dataclass
class SearchOutcome:
    """一次搜索的产出。字段刻意少而稳定，用例只依赖这几个。"""

    module: str                      # hotels / trains / flights
    query: str                       # 人可读条件摘要，写进报告
    url: str = ""
    landed: bool = False             # 是否进了「结果页」（不是首页、不是登录墙）
    cards: int = 0                   # 结果条目数
    params: dict[str, Any] = field(default_factory=dict)   # 页面自己生成的参数
    error: str = ""                  # 非空表示中途抛了；用例据此 fail
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.landed and self.cards >= 1

    def summary(self) -> str:
        return (
            f"模块：{self.module}\n条件：{self.query}\n落点：{self.url[:200]}\n"
            f"进结果页：{self.landed}  条目数：{self.cards}\n"
            f"参数：{self.params}\n"
            f"{('错误：' + self.error) if self.error else ''}"
            f"{('备注：' + self.note) if self.note else ''}"
        )


def search_hotel(page, case) -> SearchOutcome:
    """酒店：首页填目的地 / 日期 / 房间住客 → 提交 → 等列表卡片。"""
    out = SearchOutcome(
        module="hotels",
        query=f"{case.city} {case.checkin}→{case.checkout} "
        f"{case.rooms}间{case.adults}成人{case.children}儿童",
    )
    try:
        hotel = HotelSearchPage(page)
        with allure_support.step("打开酒店首页并检查风控"):
            hotel.open()
        with allure_support.step("填目的地（点选联想面板）"):
            hotel.set_destination(case.city)
        with allure_support.step(f"选日期 {case.checkin} → {case.checkout}"):
            hotel.set_checkin(case.checkin)
            hotel.set_checkout(case.checkout)
            hotel.close_calendar()
        with allure_support.step(
            f"设置房间住客 {case.rooms}间/{case.adults}成人/{case.children}儿童"
        ):
            hotel.set_rooms(case.rooms, case.adults, case.children)
        with allure_support.step("提交并等待结果列表"):
            out.params = hotel.submit_and_capture()
            out.landed = hotel.wait_result_list()
            out.cards = hotel.result_card_count()
        out.url = hotel.current_url
    except Exception as exc:                      # noqa: BLE001 - 见文件头「为什么不抛」
        out.error = f"{type(exc).__name__}: {exc}"
        out.url = page.url
    return out


def search_train(page, case) -> SearchOutcome:
    """火车票：填区间 / 选日期 → 提交 → 数车次卡片。"""
    out = SearchOutcome(
        module="trains", query=f"{case.from_city}→{case.to_city} {case.depart_date}"
    )
    try:
        train = TrainSearchPage(page)
        with allure_support.step("打开火车票首页并检查风控"):
            train.open()
        with allure_support.step(f"选区间 {case.from_city} → {case.to_city}"):
            train.select_city("depart", case.from_city)
            train.select_city("arrive", case.to_city)
        with allure_support.step(f"选出发日 {case.depart_date}"):
            train.select_date(case.depart_date)
        with allure_support.step("提交并读取车次"):
            train.submit()
            out.landed = bool(train.url_depart_date())
            out.params = {"dDate": train.url_depart_date()}
            out.cards = len(train.result_texts())
        out.url = page.url
    except Exception as exc:                      # noqa: BLE001
        out.error = f"{type(exc).__name__}: {exc}"
        out.url = page.url
    return out


def search_flight(page, case) -> SearchOutcome:
    """机票：切单程 / 填区间 / 选日期（必须关日历）→ 提交 → 数航班卡片。"""
    out = SearchOutcome(
        module="flights", query=f"{case.from_city}→{case.to_city} {case.depart}"
    )
    try:
        flight = FlightSearchPage(page)
        with allure_support.step("打开机票首页并切单程"):
            flight.open()
            flight.choose_one_way()
        with allure_support.step(f"选航线 {case.from_city} → {case.to_city}"):
            flight.set_route(case.from_city, case.to_city)
        # select_date 内部会翻月、按「年+月」定位月份块、点日、关日历，
        # 并在收尾后断言 input 真的变成了目标日期 —— 失败会抛异常带着原因，
        # 不再是「日期没生效但照样提交」那种看起来像站点改版的假失败。
        with allure_support.step(f"选乘机日 {case.depart}（含跨月翻页）"):
            flight.select_date(case.depart)
            # 兜一次：万一 select_date 走到一半抛了，日历留在页面上会拦住搜索按钮。
            flight.close_calendar()
        with allure_support.step("提交并等待航班列表"):
            out.landed = flight.submit_and_wait()
            out.params = flight.query_params()
            out.cards = flight.result_card_count()
            if out.cards == 0 and out.landed:
                flight.wait_result_cards()
                out.cards = flight.result_card_count()
        out.url = flight.current_url
        if flight.route_in_url():
            out.params = {**out.params, "route": flight.route_in_url()}
    except Exception as exc:                      # noqa: BLE001
        out.error = f"{type(exc).__name__}: {exc}"
        out.url = page.url
    return out
