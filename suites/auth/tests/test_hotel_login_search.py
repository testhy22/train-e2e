"""登录态轨道的酒店搜索：带 state 走完「首页填表 → 提交 → 列表出酒店」。

**这条用例存在的理由**（2026-11-01 用户要求「搜索酒店功能在登录的情况下测试一下」）：

`suites/hotels` 的 `test_submit_then_list_renders` 从一期就一直 skip 着，
原因是匿名态提交必落 passport 登录墙（K-04）。K-07 把它记成
「有登录态后**可能**可以启用，但这次没验证，别默认它能绿」。
本文件就是把那个「没验证」补上，并且**单独一条轨道**，不动匿名轨道：

    匿名（suites/hotels，scope=ui_hotels）  提交 → passport 登录墙（K-04 仍成立）
    登录（本文件，scope=ui_auth）           提交 → hotels/list → 出酒店卡片

为什么不直接把 hotels 那条 skip 打开：那会把 `ui_hotels` 的比例从
「匿名能拿到什么」换成「今天扫没扫码」，两条既有序列一起污染
（conftest 的 scope 分轨注释写的就是这件事）。

**实测事实**（2026-11-01 17:56，`tools/probes/probe_hotel_auth_search.py`，
证据落 `reports/probe_hotel_auth/20261101-175636/`，`reports/` 未跟踪）：

    首页登录判据        True（顶部用户名容器）
    表单摘要            1间, 1位 / 11月8日-11月10日 2晚
    提交参数            city=2 checkin=2026/11/08 checkout=2026/11/10 crn=1 adult=1 children=0
    落点                hotels.ctrip.com/hotels/list?…  登录墙=False
    卡片                `[class*='hotel-card']` = 13，页面文案「找到9382家酒店」

**本条不断言什么**：
  · 不重复断言表单摘要（日期/晚数/房间人数）—— 那是 `suites/hotels` 的
    `test_search_submits_expected_params` 的职责，抄一份只会让两边日后各自漂移；
  · 不断言「列表页顶部用户名」—— 实测它在，但列表页 header 与 www 首页不是同一个
    组件，拿它当硬断言会造出与功能无关的脆弱红。只写进报告。
"""

from __future__ import annotations

import datetime as dt

import pytest

from config import settings
from pages.hotel_search_page import HotelSearchPage
from utils.auth import is_logged_in, looks_like_login_wall, wait_logged_in
from utils.case_data import case_ids, load_hotel_search_cases

pytestmark = [pytest.mark.auth]

# 与匿名轨道共用同一份数据（data/hotels/search_cases.yaml）：
# 城市 / 天数 / 房间人数的口径必须一致，否则「登录后能搜」和「匿名能搜」
# 比的不是同一件事。city_id 也从这里取，不另抄。
CASES = load_hotel_search_cases()


def _fill_and_submit(hotel, *, city, checkin, checkout, rooms, adults, children):
    """填表并提交，返回（表单摘要, 提交参数）。

    抽出来只为一件事：本文件两条用例都要走「填 → 搜」，
    抄两遍的话以后改定位器要改两处，而漏改的那处会以「登录态下搜索莫名失败」出现。
    """
    hotel.set_destination(city)
    hotel.set_checkin(checkin)
    hotel.set_checkout(checkout)
    hotel.close_calendar()
    hotel.set_rooms(rooms, adults, children)
    summary = hotel.form_text()
    params = hotel.submit_and_capture()
    return summary, params


def _assert_logged_in(page) -> None:
    """登录态是本轨道所有用例的实验条件，先判它再判业务。

    两处都不是预防性设计，是 2026-11-01 跑出来的：

    1. 用 `wait_logged_in` 而不是 `is_logged_in`：首页 header 是 JS 水合的，
       打开就判会把已登录会话判成未登录（广州那组偶发红一次）。
    2. **判失败且没落登录墙时，重载一次再判。** 实测（全量 6 条跑两轮，两轮各红 1 条，
       红的位置还不一样）失败现场都是：URL 是正常首页、右上角是「登录 / 注册」、
       8 秒有界等待也没等到用户名容器。而
       `tools/probes/probe_hotel_auth_state_stability.py` 用同一份 state 连开 6 个
       context，前 5 轮**即时判定就是已登录**，第 6 轮 `goto` 直接 15 s 超时
       —— 所以根因是首页偶尔加载慢/加载不全，不是会话失效，也不是判据抓错容器。

       只重载一次，且**不换 state、不换账号、不改身份** —— 与「被风控拦下就重试」
       是两回事（红线见 pages/base_page.py 文件头）。重载后仍判不出来照样 fail，
       不然真失效会被这条兜底掩盖。
    """
    logged, evidence = wait_logged_in(page, timeout_ms=15000)
    if not logged and not looks_like_login_wall(page.url):
        page.reload(wait_until="domcontentloaded")
        logged, evidence = wait_logged_in(page, timeout_ms=15000)
        evidence = f"[重载一次后] {evidence}"

    assert not looks_like_login_wall(page.url), (
        f"落在 passport 登录墙，说明 state 已失效或未被加载：{page.url}"
    )
    assert logged, (
        f"未检测到已登录态（{evidence}）。"
        "先确认 config/auth/ctrip_state.json 是不是超过一周没重扫："
        "python scripts/setup_auth.py"
    )


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_login_search_lands_on_result_list(auth_page, request, blocked_guard, case):
    """带登录态从首页提交搜索，断言真的进了结果列表并且出了酒店卡片。

    断言顺序是刻意的，每一步都对应一种真实的失败形态：
      1. 先判登录态 —— 这是本用例的实验条件。state 失效时不先判，后面会红在
         「没进 /hotels/list」，看起来像站点改版，实际只是要重扫码；
      2. 再判没落 passport —— 与 1 互补：登录判据可能因页面容器改版而落空，
         但 URL 落不落 passport 是硬事实；
      3. 最后判 URL 与卡片数 —— 这才是「搜索功能在登录态下可用」的正面证据。
    """
    hotel = HotelSearchPage(auth_page)
    # 登记给 blocked_guard：被拦时截图要截这张页面，不是另起一个匿名空页。
    request.node._base_page = hotel                 # type: ignore[attr-defined]

    with blocked_guard():
        hotel.open()

    _assert_logged_in(auth_page)

    with blocked_guard():
        summary, params = _fill_and_submit(
            hotel,
            city=case.city,
            checkin=case.checkin,
            checkout=case.checkout,
            rooms=case.rooms,
            adults=case.adults,
            children=case.children,
        )

    ready = hotel.wait_result_list()
    cards = hotel.result_card_count()
    url = hotel.current_url
    logged_on_list, list_evidence = is_logged_in(auth_page)

    request.node.add_report_section(
        "call",
        "落点",
        f"提交参数：city={params.get('city')} checkin={params.get('checkin')} "
        f"checkout={params.get('checkout')} crn={params.get('crn')} "
        f"adult={params.get('adult')} children={params.get('children')}\n"
        f"表单摘要：{summary[:160]}\n"
        f"落点 url：{url[:200]}\n"
        f"title：{auth_page.title()}\n"
        f"进列表：{ready}  卡片数：{cards}  登录墙：{looks_like_login_wall(url)}\n"
        f"列表页登录判据：{logged_on_list}（{list_evidence}）",
    )

    # ---------- 断言 1：没被登录墙接管 ----------
    assert not looks_like_login_wall(url), (
        "提交后被 passport 接管 —— 与匿名态的表现一样，说明登录态没有随提交带过去："
        f"{url[:200]}"
    )

    # ---------- 断言 2：真的进了结果列表 ----------
    assert ready, (
        f"提交后 {settings.NAV_TIMEOUT_MS}ms 内没进 /hotels/list 或没出卡片；"
        f"url={url[:200]} 卡片数={cards}"
    )

    # ---------- 断言 3：列表里确实有酒店 ----------
    assert cards >= 1, f"列表页一张酒店卡片都没有；url={url[:200]}"

    # ---------- 断言 4：发出去的参数 == 输入（外部可验证证据） ----------
    # 参数是页面按输入自己生成的，不是我们回读自己写进去的值。
    assert params["city"][0] == str(case.expected["city_id"]), (
        f"city（城市 id）不符：{params.get('city')}"
    )
    assert params["checkin"][0] == case.checkin.strftime("%Y/%m/%d"), (
        f"checkin 不符：{params.get('checkin')}"
    )
    assert params["checkout"][0] == case.checkout.strftime("%Y/%m/%d"), (
        f"checkout 不符：{params.get('checkout')}"
    )
    assert params["crn"][0] == str(case.rooms), f"crn（房间数）不符：{params.get('crn')}"
    assert params["adult"][0] == str(case.adults), f"adult（成人数）不符：{params.get('adult')}"
    assert params["children"][0] == str(case.children), (
        f"children（儿童数）不符：{params.get('children')}"
    )


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_re_search_from_result_page_via_left_nav(auth_page, request, blocked_guard, case):
    """结果页 → 点左侧「酒店」回首页 → 改条件 → 再搜一次，仍然出酒店。

    这条对应用户 2026-11-01 的实际操作路径（截图里箭头指向左侧竖排导航的「酒店」）：
    **在登录态下想重新搜索，不是在结果页顶部的搜索框里改，而是点左侧「酒店」回首页。**

    为什么值得单独一条用例，而不是并进上一条：
      上半段验的是「能不能搜出来」，下半段验的是「搜完之后还能不能换条件再搜」。
      后者会踩到一类独立问题 —— 会话跨子域（www → hotels）之后表单组件是否还正常、
      回首页时营销浮层会不会盖住控件、第二次提交是否带着上一次的参数缓存。
      实测这三点都正常，但只有分开写才能在某天出问题时说清是哪一环坏了。

    第二次搜索条件**刻意与第一次不同**（入住日 +1、晚数 +1），否则「重新搜」
    只是把同一组参数再发一遍，证明不了条件确实能改。
    """
    hotel = HotelSearchPage(auth_page)
    request.node._base_page = hotel                 # type: ignore[attr-defined]

    with blocked_guard():
        hotel.open()
    _assert_logged_in(auth_page)

    # ---------- 第一次搜索 ----------
    with blocked_guard():
        _, first_params = _fill_and_submit(
            hotel,
            city=case.city,
            checkin=case.checkin,
            checkout=case.checkout,
            rooms=case.rooms,
            adults=case.adults,
            children=case.children,
        )
    first_ready = hotel.wait_result_list()
    first_cards = hotel.result_card_count()
    first_url = hotel.current_url
    assert first_ready and first_cards >= 1, (
        f"第一次搜索没出结果，后面的「重新搜索」无从谈起：ready={first_ready} "
        f"卡片={first_cards} url={first_url[:200]}"
    )

    # ---------- 点左侧「酒店」回首页 ----------
    with blocked_guard():
        back_ok = hotel.back_to_home_via_left_nav()
    home_url = hotel.current_url

    request.node.add_report_section(
        "call",
        "重新搜索",
        f"第一次：{first_url[:160]}\n"
        f"  参数 checkin={first_params.get('checkin')} checkout={first_params.get('checkout')} "
        f"卡片={first_cards}\n"
        f"点左侧「酒店」后：back_ok={back_ok} url={home_url[:160]}\n"
        f"  表单可见={auth_page.locator('#hotels-destination').count()} "
        f"浮层={hotel.overlay_log or '无'}",
    )

    assert back_ok, (
        "点左侧竖排导航「酒店」没回到带表单的首页；"
        f"url={home_url[:200]} 表单数={auth_page.locator('#hotels-destination').count()}"
    )
    assert "www.ctrip.com" in home_url, (
        f"落点不是 www.ctrip.com 首页，而是：{home_url[:200]}"
    )

    # ---------- 改条件再搜 ----------
    second_in = case.checkin + dt.timedelta(days=1)
    second_out = second_in + dt.timedelta(days=case.nights + 1)

    with blocked_guard():
        second_summary, second_params = _fill_and_submit(
            hotel,
            city=case.city,
            checkin=second_in,
            checkout=second_out,
            rooms=case.rooms,
            adults=case.adults,
            children=case.children,
        )
    second_ready = hotel.wait_result_list()
    second_cards = hotel.result_card_count()
    second_url = hotel.current_url

    request.node.add_report_section(
        "call",
        "第二次落点",
        f"表单摘要：{second_summary[:160]}\n"
        f"参数 checkin={second_params.get('checkin')} checkout={second_params.get('checkout')}\n"
        f"url：{second_url[:200]}\n"
        f"进列表：{second_ready}  卡片数：{second_cards}",
    )

    assert second_params["checkin"][0] == second_in.strftime("%Y/%m/%d"), (
        f"第二次的 checkin 没跟着改：{second_params.get('checkin')}"
    )
    assert second_params["checkout"][0] == second_out.strftime("%Y/%m/%d"), (
        f"第二次的 checkout 没跟着改：{second_params.get('checkout')}"
    )
    assert second_ready, (
        f"重新搜索后没进结果列表；url={second_url[:200]} 卡片数={second_cards}"
    )
    assert second_cards >= 1, (
        f"重新搜索后一张酒店卡片都没有；url={second_url[:200]}"
    )
