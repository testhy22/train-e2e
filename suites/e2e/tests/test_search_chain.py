"""跨模块串联：**一次登录、一个浏览器、一个 context、一个 page**，
依次跑完 酒店 → 火车 → 机票，每模块多组数据，单条失败不中断后续。

与既有三条轨道的关系（不替代，也不合并）：

| 轨道 | scope | 形态 | 回答什么 |
|---|---|---|---|
| `suites/thirdparty`（火车票） | `ui` | 每条一个 context | 匿名态下功能对不对 |
| `suites/hotels`（酒店） | `ui_hotels` | 每条一个 context | 匿名态下提交参数对不对 |
| `suites/auth`（登录态） | `ui_auth` | 每条一个 context | 这份会话还能不能用 |
| **本文件（串联）** | `ui_e2e` | **整轮共用一个 context/page** | 登录态下连续搜索会不会「越搜越坏」 |

为什么要单独这条轨道，而不是把上面三条改成共享会话：
它们的断言都依赖「干净 context」这个实验条件（匿名态、单条独立）。改成共享，
`ui` / `ui_hotels` 的 ratio 序列会一起变成「今天扫没扫码 + 上一轮留下的状态」，
K-04 / K-05 的结论也就不可比了。

本轨道要暴露的是另一类问题：**状态疲劳**（连搜九次之后页面还能不能正常响应）、
**浮层累积**（每站都弹一次营销层，叠到第三站会不会挡住控件）、
**page 崩溃后的恢复**（某站超时把 page 打死，下一站还能不能跑）。
这些只有「共享会话 + 连续多组」才测得出来。

顺序是有意的：酒店（www，表单最复杂）→ 火车（独立站，无头会被 WAF 拦）→
机票（独立站，日历不关会挡住搜索按钮）。pytest 按文件内定义顺序执行，
同一用例的多组数据连续跑，所以「模块串联」是由定义顺序保证的，不靠调度插件。
"""

from __future__ import annotations

import allure
import pytest

from utils.waf import check_waf_block
from pages.base_page import BasePage
from utils.case_data import (
    case_ids,
    load_flight_cases,
    load_hotel_search_cases,
    load_train_smoke_cases,
)
from utils import discover
from utils.search_flow import SearchOutcome, search_flight, search_hotel, search_train

pytestmark = [pytest.mark.e2e]

HOTELS = load_hotel_search_cases()
TRAINS = load_train_smoke_cases()
FLIGHTS = load_flight_cases()


# ---------------------------------------------------------------- 公共断言与取证

def _capture(page, request) -> list[str]:
    """失败即证据：视口 + 全页两张，不裁剪（最大化后宽度随物理窗口变化）。"""
    return [str(p) for p in BasePage(page).capture(request.node.nodeid + "__e2e")]


def _waf_guard(page, request) -> None:
    """WAF 守卫：命中就 xfail，留截图与信号，**不影响后续用例**。

    用 xfail 而不是 fail 的理由与 blocked 门禁一致（docs/BLOCKED-GATE.md）：
    被对方拦下是外部事实，不是代码缺陷；但必须留证据，否则「真缺陷」会混进来。
    """
    signal = check_waf_block(page)
    if signal is None:
        return
    shots = _capture(page, request)
    request.node.add_report_section(
        "call",
        "WAF 守卫",
        f"信号：{signal}\nurl：{page.url}\n截图：{', '.join(shots) or '(截图失败)'}",
    )
    pytest.xfail(f"blocked: {signal.kind}（{signal.detail}）")


def _discover_quietly(page, site: str, source_case: str, request) -> None:
    """旁路采集：DISCOVER=1 才执行；任何异常都不许影响主判定（discover 铁律 1）。

    hook 式已否决（teardown 时页面停在末尾状态，采到的是静默错数据），
    所以挂在「结果页已断言」之后的确定时刻显式调用。
    """
    if not discover.enabled():
        return
    try:
        result = discover.collect(page, site=site, source_case=source_case)
        note = discover.coverage_note(result, site, source_case)
        request.node.add_report_section("call", "自动发现（旁路）", note)
    except Exception as exc:  # noqa: BLE001 —— 双保险：discover 内部已兜底
        request.node.add_report_section(
            "call", "自动发现（旁路）", f"[discover:{site}] 采集层异常（已吞掉）：{exc}"
        )


def _assert_outcome(page, request, out: SearchOutcome, rebuilds: int) -> None:
    """把一次搜索的产出落进报告，并按契约断言。

    先后顺序有意：先把现场写进报告，再断言。
    反过来的话，断言一抛，报告里只剩 pytest 的 assert 文本，没有 URL / 参数 / 截图索引。
    """
    request.node.add_report_section(
        "call",
        f"{out.module} 产出",
        out.summary() + f"\n共享 page 重建次数：{rebuilds}",
    )
    if not out.ok:
        shots = _capture(page, request)
        request.node.add_report_section(
            "call", "失败现场", f"url：{page.url}\n截图：{', '.join(shots) or '(截图失败)'}"
        )

    assert not out.error, f"{out.module} 流程抛异常：{out.error}\n条件：{out.query}"
    assert out.landed, f"{out.module} 没有进入结果页；url={out.url[:200]}"
    assert out.cards >= 1, f"{out.module} 结果页没有条目；url={out.url[:200]}"


# ---------------------------------------------------------------- 酒店（3 组）

@allure.story("酒店：首页填写 → 提交 → 结果列表")
@allure.title("酒店搜索 {case.city} {case.checkin} 起 {case.nights} 晚 {case.rooms} 间")
@pytest.mark.parametrize("case", HOTELS, ids=case_ids(HOTELS))
def test_chain_hotels(shared_page, shared_session, request, case):
    """酒店：首页填表 → 提交 → 列表出卡片。"""
    out = search_hotel(shared_page, case)
    _waf_guard(shared_page, request)
    _assert_outcome(shared_page, request, out, shared_session.rebuilds)

    # 页面自己生成的参数，不是我们回读自己写进去的值
    assert out.params["checkin"][0] == case.checkin.strftime("%Y/%m/%d"), (
        f"checkin 不符：{out.params.get('checkin')}"
    )
    assert out.params["checkout"][0] == case.checkout.strftime("%Y/%m/%d"), (
        f"checkout 不符：{out.params.get('checkout')}"
    )
    assert out.params["crn"][0] == str(case.rooms), f"crn 不符：{out.params.get('crn')}"
    assert out.params["adult"][0] == str(case.adults), (
        f"adult 不符：{out.params.get('adult')}"
    )
    _discover_quietly(shared_page, "hotels", case.id, request)


# ---------------------------------------------------------------- 火车票（3 组）

@allure.story("火车票：填区间 → 选日期 → 提交 → 车次列表")
@allure.title("火车票搜索 {case.from_city} → {case.to_city} {case.depart_date}")
@pytest.mark.parametrize("case", TRAINS, ids=case_ids(TRAINS))
def test_chain_trains(shared_page, shared_session, request, case):
    """火车票：填区间 → 选日期 → 提交 → 出车次。

    无头下这一站会被 WAF 拦（K-05），由 _waf_guard 转 xfail。
    """
    out = search_train(shared_page, case)
    _waf_guard(shared_page, request)
    _assert_outcome(shared_page, request, out, shared_session.rebuilds)

    assert out.params["dDate"] == case.depart_date, (
        f"结果页 URL 的 dDate 是 {out.params.get('dDate')}，期望 {case.depart_date}"
    )
    _discover_quietly(shared_page, "trains", case.id, request)


# ---------------------------------------------------------------- 机票（4 组）

@allure.story("机票：切单程 → 填区间 → 日历选日（跨月）→ 提交 → 航班列表")
@allure.title("机票搜索 {case.from_city} → {case.to_city} {case.depart}")
@pytest.mark.parametrize("case", FLIGHTS, ids=case_ids(FLIGHTS))
def test_chain_flights(shared_page, shared_session, request, case):
    """机票：切单程 → 填区间 → 日历选日期（跨月）→ 收日历 → 提交 → 出航班。"""
    out = search_flight(shared_page, case)
    _waf_guard(shared_page, request)
    _assert_outcome(shared_page, request, out, shared_session.rebuilds)

    assert out.params["depdate"][0] == case.depart.isoformat(), (
        f"结果页 URL 的 depdate 是 {out.params.get('depdate')}，"
        f"期望 {case.depart.isoformat()}"
    )
    _discover_quietly(shared_page, "flights", case.id, request)
