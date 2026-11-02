"""火车票**中转**用例（首页点「中转」进入，出发日 D+3..+15）。

流程：首页 → 点「中转」→ 填出发/到达城市 → 点出发日 → 点搜索 → 三层断言。
「指定中转城市」**留空**：实测不填也能出方案（2026-11-02，汉口→深圳 201 张卡），
填它需要另探一套联想面板，没有实测样本就不写定位器（避免 K-01 那类假通过）。

2026-11-02 用户裁决：子菜单必须靠**点按钮 + 填输入框**进，不允许改 URL 直达；
只有回到一级菜单（trains.ctrip.com 首页）才允许用 URL。URL 参数只作**断言依据**。

与另外两条轨道分工：
  `test_train_search.py`      单程，走首页表单 —— 守"匿名态表单通不通"
  `test_train_round_trip.py`  往返，走首页表单（点「往返」+ 两个日历）
  本文件                        中转，走首页表单（点「中转」）—— 守"方案是不是**真两段拼成**"

断言分三层：
  结构层：URL 的 ticketType=2、dDate 真的生效；`.transfer-box` 存在且标题是「中转方案推荐」；条数达标
  内容层：每张方案卡都能抽出**两段**车次号（第一程 + 第二程），占比达标；起终点站名非空
  辅助层：中转站分布、字头分布、同站换乘标签数只写进报告，不参与 pass/fail
         （中转站随余票与开行方案天天变，写死"必须经过深圳北"只会误红）

两条硬证据（2026-11-02 三次有头探测，reports/probe/transfer_*.json）：
  · `.transfer-box` 是**整块容器**（含 h3 + 全部卡片），它的父才是一层 `section`。
    第一轮把层级读反了，于是 `querySelectorAll("section")` 返回 0 —— 别再用 section 数卡片。
  · 卡片仍是 `div.card-white.list-item`（汉口→深圳 205 张），但 `.list-open` 是每张卡
    可展开的详情面板，数量与卡片一一对应（205=205）⇒ 数错就会把方案数正好翻倍。

blocked 走 xfail 并留证据；其余失败一律普通 fail。

**用 `shared_page`**（2026-11-02 用户要求「在一次登录的情况下测试」）：3 条数据共用
session 级 context，只登录一次；原因与往返轨道完全相同，见那边的注释。
"""

from __future__ import annotations

from collections import Counter

import pytest

from pages.train_search_page import TrainSearchPage
from utils.case_data import case_ids, transfer_cases_for_run

CASES = transfer_cases_for_run()

pytestmark = pytest.mark.ui

# 起终点站名为空的卡片占比上限。实测全都有，留一点余量给渲染差的卡片。
MAX_BLANK_ENDPOINT_RATIO = 0.05


def _numbers_of(train: TrainSearchPage, card: dict) -> list[str]:
    return train.transfer_leg_numbers(card.get("legs") or [])


def _report(train: TrainSearchPage, case, cards: list[dict], heading: str) -> dict:
    """算统计并写报告，返回给断言用的字典。"""
    two_legs = [c for c in cards if len(_numbers_of(train, c)) == 2]
    blank_endpoints = [
        c for c in cards if not c.get("from_station") or not c.get("to_station")
    ]
    # 同一张卡里两段车次号相同 —— 结构上不该出现，单独计数用于报告
    same_leg = [c for c in two_legs if len(set(_numbers_of(train, c))) == 1]

    letters: Counter[str] = Counter()
    stations: Counter[str] = Counter()
    for card in cards:
        for number in _numbers_of(train, card):
            letters[number[0] if number[0].isalpha() else "普速"] += 1
        if card.get("trans_station"):
            stations[card["trans_station"]] += 1

    ratio = len(two_legs) / len(cards) if cards else 0.0
    blank_ratio = len(blank_endpoints) / len(cards) if cards else 1.0
    same_station = sum(1 for c in cards if c.get("same_station_tag"))

    top_stations = "、".join(f"{name}×{n}" for name, n in stations.most_common(6)) or "(无)"

    return {
        "count": len(cards),
        "two_legs": len(two_legs),
        "ratio": ratio,
        "blank_ratio": blank_ratio,
        "same_leg": len(same_leg),
        "same_station_tag": same_station,
        "letters": dict(sorted(letters.items())),
        "top_stations": top_stations,
        "heading": heading,
        "text": (
            f"区间：{case.from_city}→{case.to_city}　日期：{case.depart_date}"
            f"（T+{case.depart_offset_days}）\n"
            f"页面标题：{heading or '(缺失)'}\n"
            f"方案卡：{len(cards)} 条（下界 {case.min_cards}）\n"
            f"能抽出两段车次号：{len(two_legs)} 条，占比 {ratio:.1%}"
            f"（下界 {case.min_two_leg_ratio:.0%}）\n"
            f"起终点站名为空：{len(blank_endpoints)} 条\n"
            f"两段车次号相同的卡：{len(same_leg)} 条\n"
            f"「同站换乘」标签命中：{same_station} 条\n"
            f"字头分布（按两段计）：{dict(sorted(letters.items())) or '(无)'}\n"
            f"中转站 Top：{top_stations}\n"
            "（本页 `.list-open` 详情面板与卡片 1:1，数它会把方案数翻倍 ⇒ 一律不数）"
        ),
    }


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_train_transfer(case, shared_page, blocked_guard, widget_guard, request):
    """中转正例：点「中转」→ 填两地 → 选日期 → 搜索 → 标题/条数 → 每卡两段车次号。"""
    train = TrainSearchPage(shared_page)
    # 失败/被拦时取证要截**这个**页面对象的现场（conftest 先看 _base_page）。
    request.node._base_page = train

    with blocked_guard():
        train.open()

    with blocked_guard(), widget_guard():
        train.search_transfer(case.from_city, case.to_city, case.depart_date)

    with blocked_guard():
        cards = train.transfer_cards()
        heading = train.transfer_heading_text()
        stats = _report(train, case, cards, heading)

        # ---------------- 操作链（用户 2026-11-02 要求：可追溯到每一次人类化动作）
        request.node.add_report_section("call", "操作链", train.action_log())

        # 子菜单零 URL 直达的硬证据：整条用例只允许 open() 导航一次。
        assert train.navigations == 1, (
            f"本次用例发生了 {train.navigations} 次页面导航，期望 1 次（只有 open()）"
            "—— 多出来的那次就是子菜单 URL 直达，违反用户裁决"
        )

        request.node.add_report_section(
            "call",
            "中转查询信息",
            stats["text"]
            + f"\nURL 的 ticketType：{train.url_ticket_type()}"
            + f"\n「指定中转城市」：留空（实测不填可出方案）",
        )

        # ---------------- 结构层 0：点的确实是「中转」这个子菜单
        # ticketType 传错/按钮没生效时页面照样有车次列表，只是没有中转方案区。
        assert train.url_ticket_type() == TrainSearchPage.TICKET_TYPE_TRANSFER, (
            f"结果页 URL 的 ticketType 是 {train.url_ticket_type()!r}，期望 "
            f"{TrainSearchPage.TICKET_TYPE_TRANSFER}（2=中转），说明「中转」按钮没真正生效"
        )

        # ---------------- 结构层 1：日期真的生效
        # 不查这一条，"方案非空"可能来自 URL 被改写后的另一天。
        assert train.url_depart_date() == case.depart_date, (
            f"结果页 URL 的 dDate 是 {train.url_depart_date()}，期望 {case.depart_date}"
        )

        # ---------------- 结构层 2：这是**中转页**，不是单程页
        # ticketType 传错时页面照样有车次列表，只是没有中转方案区 —— 不查标题就会误判通过。
        assert heading == TrainSearchPage.TRANSFER_HEADING_TEXT, (
            f"没找到「{TrainSearchPage.TRANSFER_HEADING_TEXT}」标题，实际拿到 {heading!r}"
            f"（URL：{shared_page.url}）"
        )

        # ---------------- 结构层 3：方案条数
        assert stats["count"] >= case.min_cards, (
            f"只渲染出 {stats['count']} 条中转方案，少于下界 {case.min_cards}"
            f"（{case.from_city}→{case.to_city} {case.depart_date}）"
        )

        # ---------------- 内容层 1：两段车次号
        # 这是中转卡与点到点卡最本质的区别：一张卡必须能拆出两段车次号。
        # 用**比例**而不是逐条断言：实测存在抽不出第二程的卡（如「第二程 暂无」）。
        assert stats["ratio"] >= case.min_two_leg_ratio, (
            f"能抽出两段车次号的方案占比 {stats['ratio']:.1%}"
            f"（{stats['two_legs']}/{stats['count']}），低于 {case.min_two_leg_ratio:.0%}"
        )

        # ---------------- 内容层 2：起终点站名
        assert stats["blank_ratio"] <= MAX_BLANK_ENDPOINT_RATIO, (
            f"{stats['blank_ratio']:.1%} 的方案卡起终点站名为空"
            f"（上限 {MAX_BLANK_ENDPOINT_RATIO:.0%}），选择器可能整体错位"
        )
