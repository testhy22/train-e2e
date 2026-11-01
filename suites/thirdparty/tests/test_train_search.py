"""火车票查询的功能用例。

断言分三层（对应裁决 D3）：
  结构层：结果条目数 >= min_results
  ��容层：条目里能抽到形如 G6025 的车次号，且覆盖率达标
  辅助层：expected_keyword 只写进报告，不参与 pass/fail

blocked 与「日历控件唤不起」都走 xfail 并留证据；其余失败一律是普通 fail。
"""

from __future__ import annotations

import pytest

from pages.train_search_page import TrainSearchPage
from utils.case_data import case_ids, load_train_smoke_cases

# 数据在 data/trains/smoke_cases.yaml（可用 CASE_DATA_DIR 覆盖）。
CASES = load_train_smoke_cases()

# ui：驱动浏览器，纳入 blocked 门禁的统计范围。数据层单测不得带这个标记。
pytestmark = pytest.mark.ui

# 内容层门槛：能抽出车次号的条目占比。真实结果里存在「成功率：较高」这类附加行，
# 抽不到车次号的条目可能是正常形态，所以用比例而不是要求 100%。
TRAIN_NO_COVERAGE = 0.9


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_train_search(case, page, blocked_guard, widget_guard, request):
    """完整流程：填城市 → 选日期 → 搜索 → 三层断言。"""
    train = TrainSearchPage(page)

    with blocked_guard():
        train.open()

    with blocked_guard(), widget_guard():
        train.select_city("depart", case.from_city)
        train.select_city("arrive", case.to_city)
        train.select_date(case.depart_date)
        train.submit()

        # 辅助层：关键词只记录，不断言
        body = train.body_text(timeout=5000)
        request.node.add_report_section(
            "call",
            "查询信息",
            f"用例：{case.id}\n区间：{case.from_city}→{case.to_city}\n"
            f"日期：{case.depart_date}（T+{case.depart_offset_days}）\n"
            f"URL 的 dDate：{train.url_depart_date()}\n"
            f"页面自称车次数：{train.declared_result_count(body)}\n"
            f"关键词「{case.expected_keyword}」是否出现：{case.expected_keyword in body}",
        )

        # 一次取回全部卡片文本（538 条／页，逐条 inner_text 会撞 180s 超时）
        texts = train.result_texts()

        # 结构层：日期真的生效（URL 里必须是目标日期，否则后面的结果没有意义）
        assert train.url_depart_date() == case.depart_date, (
            f"结果页 URL 的 dDate 是 {train.url_depart_date()}，期望 {case.depart_date}"
        )

        # 结构层：条目数达标
        assert len(texts) >= case.min_results, (
            f"{case.from_city}→{case.to_city} {case.depart_date} 只返回 {len(texts)} 条，"
            f"少于 min_results={case.min_results}"
        )

        # 内容层：车次号可解析且覆盖率达标。
        # 分母扣掉「中转/换乘」卡片 —— 它们由两段拼成，结构上没有单一车次号
        # （2026-11-01 实测：60 条里 5 条是这种卡）。阈值仍是 90%，没有放宽；
        # 但要同时断言这些卡片确实带换乘信息，不能靠「识别成中转」把垃圾条目排除掉。
        transfers = [t for t in texts if train.is_transfer_text(t)]
        point_to_point = [t for t in texts if not train.is_transfer_text(t)]
        for card in transfers:
            assert "换乘" in card, "被判定为中转的卡片里没有换乘信息，判定口径有误"

        numbers = [n for n in (train.train_number_of(t) for t in point_to_point) if n]
        coverage = len(numbers) / len(point_to_point) if point_to_point else 0
        request.node.add_report_section(
            "call",
            "车次号解析",
            f"条目 {len(texts)} 条（点到点 {len(point_to_point)} / 中转 {len(transfers)}）\n"
            f"抽出车次号 {len(numbers)} 条，覆盖率 {coverage:.1%}\n"
            f"前 5 个车次号：{numbers[:5]}",
        )
        assert numbers, (
            f"结果里没有任何车次号，前三条文本：{[t[:40] for t in point_to_point[:3]]}"
        )
        assert coverage >= TRAIN_NO_COVERAGE, (
            f"点到点条目里能抽出车次号的占比 {coverage:.0%}"
            f"（{len(numbers)}/{len(point_to_point)}），低于 {TRAIN_NO_COVERAGE:.0%}"
        )
