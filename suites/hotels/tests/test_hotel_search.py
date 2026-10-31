"""酒店搜索一期用例：目的地 / 入住 / 退房 / 房间及住客。

断言挂在哪，是这份用例的核心决定：

1. **不再断言「结果列表出现」。** 实测（reports/probe_www_hotels_04.json）未登录态下
   连直接打开 `/hotels/list` 都会被 passport 登录墙接管，列表 DOM 根本拿不到。
   若坚持断列表，用例在匿名 CI 里永远红，且红得没有信息量。
2. **改断「提交时真正发出去的请求参数」。** 参数是页面按用户输入自己生成的，
   不是我们回读自己写进去的值 —— 这是外部可验证结果，不是自证。
3. **表单摘要作为第二道独立断言。** 日期/晚数/房间人数必须同时落在可见文本里，
   这样即使请求断言因页面改版失效，也能从人可读状态看出问题在哪。
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from config import settings
from pages.hotel_search_page import HotelSearchPage
from utils.hotel_data import load_cases

pytestmark = [pytest.mark.ui, pytest.mark.hotels]

CASES = load_cases(settings.PROJECT_ROOT / "test_data" / "hotel_cases.yaml")


@pytest.mark.parametrize("case", CASES, ids=[c.param_id for c in CASES])
def test_search_submits_expected_params(page, request, blocked_guard, case):
    hotel = HotelSearchPage(page)

    with blocked_guard():
        hotel.open()
        hotel.set_destination(case.city)
        hotel.set_checkin(case.checkin)
        hotel.set_checkout(case.checkout)
        hotel.close_calendar()
        hotel.set_rooms(case.rooms, case.adults, case.children)
        summary = hotel.form_text()
        params = hotel.submit_and_capture()

    # ---------- 断言 1：可见摘要（日期 / 晚数 / 房间人数） ----------
    label_in = f"{case.checkin.month}月{case.checkin.day}日"
    label_out = f"{case.checkout.month}月{case.checkout.day}日"
    assert label_in in summary, f"入住摘要未更新为 {label_in}；实际摘要：{summary[:200]}"
    assert label_out in summary, f"退房摘要未更新为 {label_out}；实际摘要：{summary[:200]}"

    nights_hit = re.search(r"(\d+)\s*晚", summary)
    assert nights_hit and int(nights_hit.group(1)) == case.nights, (
        f"晚数应为 {case.nights}；实际摘要：{summary[:200]}"
    )

    guest_hit = re.search(r"(\d+)\s*间,\s*(\d+)\s*位", summary)
    assert guest_hit, f"摘要里找不到「X间, Y位」；实际：{summary[:200]}"
    assert (int(guest_hit.group(1)), int(guest_hit.group(2))) == (case.rooms, case.people), (
        f"房间人数应为 {case.rooms}间{case.people}位，实际 {guest_hit.group(0)}"
    )

    # ---------- 断言 2：真正发出去的 /hotels/list 参数 ----------
    assert params["checkin"][0] == case.checkin.strftime("%Y/%m/%d"), (
        f"请求里的 checkin 与所选日期不一致：{params.get('checkin')}"
    )
    assert params["checkout"][0] == case.checkout.strftime("%Y/%m/%d"), (
        f"请求里的 checkout 与所选日期不一致：{params.get('checkout')}"
    )
    assert params["crn"][0] == str(case.rooms), f"crn（房间数）不符：{params.get('crn')}"
    assert params["adult"][0] == str(case.adults), f"adult（成人数）不符：{params.get('adult')}"
    assert params["children"][0] == str(case.children), (
        f"children（儿童数）不符：{params.get('children')}"
    )
    assert case.city in hotel.display_of(params), (
        f"display 参数里没有 {case.city}：{hotel.display_of(params)!r}"
    )

    # 落点写进报告：本期望必被登录墙接管，记录它是为了对照，不作为判定条件。
    request.node.add_report_section(
        "call",
        "落点",
        f"login_wall={hotel.landed_on_login_wall()} url={hotel.current_url[:160]}",
    )


def test_list_page_renders_with_full_params(page, blocked_guard):
    """列表页在匿名态**可以**渲染 —— 前提是 URL 参数齐全。

    这条替换掉了我上一版写错的 `test_login_wall_is_global`（当时断言「直开 list 必被拦」），
    它一跑就红了，红得有价值：实测（reports/probe_www_hotels_06.json）
      slim 参数（只有 city/checkin/checkout/crn/adult/children） → 踢回 passport
      full 参数（再加 optionId/optionType/display/domestic…） → 正常出 13 条酒店
    所以登录墙挂在「参数完整性 + 会话状态」上，不是「列表页本身不可访问」。

    两种落点都算诚实结果：出卡片 → pass；被拦 → blocked（进 blocked 统计，不重试）。
    """
    hotel = HotelSearchPage(page)
    far = dt.date.today() + dt.timedelta(days=30)
    with blocked_guard():
        hotel.open_list_directly(
            city_id=2, city_name="上海", checkin=far, checkout=far + dt.timedelta(days=2)
        )
        hotel.assert_not_login_wall()

    assert hotel.result_card_count() >= 1, (
        f"列表页没有酒店卡片；url={hotel.current_url[:160]}"
    )


@pytest.mark.skip(
    reason="known-limit: K-04 首页提交路径被 passport 接管，『提交→列表渲染』的端到端不可验证"
)
def test_submit_then_list_renders(page, blocked_guard):
    """有合法登录态后启用。现在留着，是为了让这条缺口可见，而不是删掉假装没有。"""
    hotel = HotelSearchPage(page)
    with blocked_guard():
        hotel.open()
        hotel.set_destination("上海")
        hotel.submit_and_capture()
    assert hotel.result_card_count() >= 1
