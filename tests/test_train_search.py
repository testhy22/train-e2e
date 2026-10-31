"""火车票查询的功能用例。

断言分三层（对应裁决 D3）：
  结构层：结果条目数 >= min_results
  ��容层：条目里能抽到形如 G6025 的车次号，且覆盖率达标
  辅助层：expected_keyword 只写进报告，不参与 pass/fail

blocked 与「日历控件唤不起」都走 xfail 并留证据；其余失败一律是普通 fail。
"""

from __future__ import annotations

import pytest

from config import settings
from pages.train_search_page import TrainSearchPage
from utils.data_loader import load_cases

CASES = load_cases(settings.PROJECT_ROOT / "test_data" / "search_cases.yaml")

# 内容层门槛：能抽出车次号的条目占比。真实结果里存在「成功率：较高」这类附加行，
# 抽不到车次号的条目可能是正常形态，所以用比例而不是要求 100%。
TRAIN_NO_COVERAGE = 0.9


@pytest.mark.parametrize("case", CASES, ids=[c.param_id for c in CASES])
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
            f"页面自称车次数：{train.declared_result_count()}\n"
            f"关键词「{case.expected_keyword}」是否出现：{case.expected_keyword in body}",
        )

        items = train.result_items()
        numbers = train.item_train_numbers(items)

        # 结构层：日期真的生效（URL 里必须是目标日期，否则后面的结果没有意义）
        assert train.url_depart_date() == case.depart_date, (
            f"结果页 URL 的 dDate 是 {train.url_depart_date()}，期望 {case.depart_date}"
        )

        # 结构层：条目数达标
        assert len(items) >= case.min_results, (
            f"{case.from_city}→{case.to_city} {case.depart_date} 只返回 {len(items)} 条，"
            f"少于 min_results={case.min_results}"
        )

        # 内容层：车次号可解析且覆盖率达标
        coverage = len(numbers) / len(items) if items else 0
        assert numbers, f"结果里没有任何形如 G6025 的车次号，前三条文本：{[i.inner_text()[:40] for i in items[:3]]}"
        assert coverage >= TRAIN_NO_COVERAGE, (
            f"能抽出车次号的条目占比 {coverage:.0%}，低于 {TRAIN_NO_COVERAGE:.0%}"
        )
