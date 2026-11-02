"""火车票**往返**用例（首页点「往返」进入，D+3..+15）。

流程：首页 → 点「往返」→ 填出发/到达城市 → 点出发日 → 点返回日 → 点搜索 → 三层断言。

2026-11-02 用户裁决：子菜单必须靠**点按钮 + 填输入框**进，不允许改 URL 直达；
只有回到一级菜单（trains.ctrip.com 首页）才允许用 URL。所以本轨道从"URL 直达结果页"
改成了"完整走首页表单"，URL 参数只作为**断言依据**（ticketType / dDate / rDate 回读）。

2026-11-03 用户裁决：数据多条，**每次随机挑 1 条**跑，靠多次运行覆盖全量 —— 见
`utils/case_rotation.py`（轮换 + 随机，一个 cycle 内 9 条不重复）。全量 `E2E_FULL=1`，
复现某条 `E2E_CASE_ID=rt-…`。

断言分三层（口径与单程一致，维度按往返扩展）：
  结构层：URL 的 ticketType=1、dDate/rDate 真的生效；页面渲染出**两栏**；每栏条数达标
  内容层：每栏都能解析出车次号，覆盖率达标；栏头区间与用例方向一致
  辅助层：车型字头分布、时段分布只写进报告，不参与 pass/fail
         （prefer_window 是**软指标** —— OTA 不保证某时段必有车，硬断言只会误红）

两条硬证据（2026-11-02 探测，reports/probe/round_trip*_20261102-*.json）：
  · `.list-item` 在往返页被复用：112 个车次卡 + 2 个「精确搜索」卡（带 .station-filter-bd）
    ⇒ 条数与覆盖率必须用排除后的集合，否则「去程有车次」会永远成立（假断言）。
  · 往返页只有一个 `div.return-box`，里面两栏 `div.return-list`，0=去程 1=返程
    ⇒ **没有** `.depart-box` / `.return-box` 两套独立容器这种东西，别按那个写。

blocked 走 xfail 并留证据；其余失败一律普通 fail。
"""

from __future__ import annotations

import pytest

from pages.train_search_page import TrainSearchPage
from utils.case_data import case_ids, round_trip_cases_for_run
from utils.case_rotation import note as rotation_note
from utils.round_trip_data import WINDOW_HOURS

CASES = round_trip_cases_for_run()

pytestmark = pytest.mark.ui

# 内容层门槛：能抽出车次号的条目占比。与单程同一口径（TRAIN_NO_COVERAGE）。
TRAIN_NO_COVERAGE = 0.9

LEG_NAME = {TrainSearchPage.LEG_DEPART: "去程", TrainSearchPage.LEG_RETURN: "返程"}


def _leg_report(train: TrainSearchPage, leg: int, texts: list[str]) -> tuple[str, dict]:
    """算一栏的统计，返回（写进报告的文本, 给断言用的字典）。

    中转卡单独计数并扣出分母 —— 它们由两段拼成，结构上没有单一车次号，
    算进覆盖率分母是口径错误（同单程 2026-11-01 的结论）。
    """
    transfers = [t for t in texts if train.is_transfer_text(t)]
    point_to_point = [t for t in texts if not train.is_transfer_text(t)]
    numbers = [n for n in (train.train_number_of(t) for t in point_to_point) if n]
    coverage = len(numbers) / len(point_to_point) if point_to_point else 0.0

    letters: dict[str, int] = {}
    for number in numbers:
        key = number[0] if number[0].isalpha() else "普速"
        letters[key] = letters.get(key, 0) + 1

    clocks = [c for c in (train.depart_clock_of(t) for t in texts) if c]
    stats = {
        "count": len(texts),
        "transfers": len(transfers),
        "point_to_point": len(point_to_point),
        "numbers": numbers,
        "coverage": coverage,
        "letters": letters,
        "clocks": clocks,
    }
    text = (
        f"{LEG_NAME[leg]}：卡片 {len(texts)} 条"
        f"（点到点 {len(point_to_point)} / 中转 {len(transfers)}）\n"
        f"  抽出车次号 {len(numbers)} 条，覆盖率 {coverage:.1%}\n"
        f"  字头分布：{dict(sorted(letters.items())) or '(无)'}\n"
        f"  前 5 个车次号：{numbers[:5]}"
    )
    return text, stats


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_train_round_trip(case, shared_page, blocked_guard, widget_guard, request):
    """往返正例：点「往返」→ 填两地 → 两个日历 → 搜索 → 两栏结构 → 每栏内容 → 软指标进报告。

    **用 `shared_page` 而不是 `page`（2026-11-02 用户要求「在一次登录的情况下测试」）**：
    `page` 是 function 级，9 条数据 = 9 次冷启动 + 9 次重载 storage_state，等于登录 9 次；
    `shared_page` 挂在 session 级 context 上，整条轨道只登录一次、只开一个浏览器。
    page 真崩了由 SharedSession 重建并记 `rebuilds`，context 不动（换 context 就得重新登录）。

    `widget_guard` 自 2026-11-02 起可用了：它的取证对象改成惰性取 `_base_page`
    （原先签名硬依赖 `base_page` ⇒ 每引用一次就多起一个 context，直接破掉"一次登录"）。
    本用例先把 `request.node._base_page` 登记好，guard 拿到的就是这个共享页。
    """
    train = TrainSearchPage(shared_page)
    # 失败/被拦时取证要截**这个**页面对象的现场（conftest 先看 _base_page）。
    request.node._base_page = train

    with blocked_guard():
        train.open()

    with blocked_guard(), widget_guard():
        train.search_round_trip(
            case.from_city, case.to_city, case.depart_date, case.return_date
        )

    with blocked_guard():
        # ---------------- 操作链（用户 2026-11-02 要求：可追溯到每一次人类化动作）
        request.node.add_report_section("call", "操作链", train.action_log())

        # 子菜单零 URL 直达的硬证据：整条用例只允许 open() 导航一次。
        assert train.navigations == 1, (
            f"本次用例发生了 {train.navigations} 次页面导航，期望 1 次（只有 open()）"
            "—— 多出来的那次就是子菜单 URL 直达，违反用户裁决"
        )

        request.node.add_report_section(
            "call",
            "往返查询信息",
            f"用例：{case.id}（{case.name}）\n"
            f"区间：{case.from_city}→{case.to_city}（{case.city_scope}）\n"
            f"本次运行集：{rotation_note('round_trip')}\n"
            f"日期：去 {case.depart_date} ~ 回 {case.return_date}（{case.nights} 晚，锚点={case.anchor}）\n"
            f"URL 的 ticketType：{train.url_ticket_type()} / "
            f"dDate：{train.url_depart_date()} / rDate：{train.url_return_date()}\n"
            f"车型期望：{list(case.train_types)}　时段偏好：{case.prefer_window}（软指标）\n"
            f"关键词「{case.expected_keyword}」是否出现：{case.expected_keyword in train.body_text(timeout=5000)}",
        )

        # ---------------- 结构层 0：点的确实是「往返」这个子菜单
        # 不查这一条，"结果页有车次列表"在**单程态**同样成立 —— 会掩盖"按钮没生效"。
        assert train.url_ticket_type() == TrainSearchPage.RT_TICKET_TYPE_ROUND, (
            f"结果页 URL 的 ticketType 是 {train.url_ticket_type()!r}，期望 "
            f"{TrainSearchPage.RT_TICKET_TYPE_ROUND}（1=往返），说明「往返」按钮没真正生效"
        )

        # ---------------- 结构层 1：日期真的生效
        # 不查这一条的话，后面的"结果非空"可能来自 URL 被改写后的另一天。
        assert train.url_depart_date() == case.depart_date, (
            f"结果页 URL 的 dDate 是 {train.url_depart_date()}，期望 {case.depart_date}"
        )
        assert train.url_return_date() == case.return_date, (
            f"结果页 URL 的 rDate 是 {train.url_return_date()}，期望 {case.return_date}"
            "（返程日期有「选完出发日自动顺延」的联动逻辑，不显式点就会带上页面猜的那天）"
        )

        # ---------------- 结构层 2：必须渲染成两栏
        # 探测实测正常态恒为 2 栏。1 栏说明返程没出来（异步/降级），
        # 这时"返程也有车次"根本无从谈起，必须先在结构上说清楚。
        columns = train.leg_columns()
        assert columns == TrainSearchPage.RT_EXPECTED_COLUMNS, (
            f"往返结果页应有 {TrainSearchPage.RT_EXPECTED_COLUMNS} 栏（去程/返程），"
            f"实际 {columns} 栏；url={shared_page.url}"
        )

        # ---------------- 结构层 3 + 内容层：逐栏核对方向、条数、车次号
        expectations = (
            (TrainSearchPage.LEG_DEPART, f"{case.from_city}-{case.to_city}"),
            (TrainSearchPage.LEG_RETURN, f"{case.to_city}-{case.from_city}"),
        )
        summaries: list[str] = []
        for leg, want_scope in expectations:
            scope = train.leg_scope(leg)
            assert want_scope.replace(" ", "") in scope.replace(" ", ""), (
                f"{LEG_NAME[leg]}栏头部是 {scope!r}，没出现「{want_scope}」"
                "（两栏方向串了，后面的断言全部无意义）"
            )

            texts = train.leg_texts(leg)
            text, stats = _leg_report(train, leg, texts)
            summaries.append(text)

            assert stats["count"] >= case.min_results_each_leg, (
                f"{LEG_NAME[leg]}只有 {stats['count']} 条，少于下界 {case.min_results_each_leg}"
                f"（{case.from_city}→{case.to_city} 往返 {case.depart_date}~{case.return_date}）"
            )
            assert stats["numbers"], (
                f"{LEG_NAME[leg]}里没有任何车次号，前三条文本："
                f"{[t[:40] for t in texts[:3]]}"
            )
            assert stats["coverage"] >= TRAIN_NO_COVERAGE, (
                f"{LEG_NAME[leg]}点到点条目里能抽出车次号的占比 {stats['coverage']:.0%}"
                f"（{len(stats['numbers'])}/{stats['point_to_point']}），低于 {TRAIN_NO_COVERAGE:.0%}"
            )
            # 车型期望只要求"至少命中一种"，不断言"结果里只有这些" ——
            # URL 不带车型过滤（枚举值未探明），页面默认全车型，断言"只有"必然误红。
            hit = set(stats["letters"]) & set(case.train_types)
            assert hit or "普速" in stats["letters"], (
                f"{LEG_NAME[leg]}的车次字头 {sorted(stats['letters'])} 与期望 {list(case.train_types)} 无交集"
            )

        # ---------------- 辅助层：时段分布（软指标，只报告）
        # 不点页面的时段芯片：它的归属容器至今没探明（probe_round_trip3.py 两次点击超时），
        # 所以只统计"结果里有多少趟落在偏好时段"，不做断言。样本足够大时这仍是有效信号。
        start, end = WINDOW_HOURS[case.prefer_window]
        window_hits = 0
        window_total = 0
        for leg in (TrainSearchPage.LEG_DEPART, TrainSearchPage.LEG_RETURN):
            texts = train.leg_texts(leg)
            for text in texts:
                clock = train.depart_clock_of(text)
                if clock is None:
                    continue
                window_total += 1
                if train.in_window(clock, start, end):
                    window_hits += 1
        ratio = window_hits / window_total if window_total else 0.0

        request.node.add_report_section(
            "call",
            "逐栏明细",
            "\n\n".join(summaries)
            + f"\n\n时段偏好 {case.prefer_window}（{start}-{end} 点）："
            f"{window_hits}/{window_total} 趟（{ratio:.0%}，软指标，不判定）",
        )
