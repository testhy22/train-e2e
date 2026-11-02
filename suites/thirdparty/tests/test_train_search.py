"""火车票**单程**用例（登录态，首页表单：选城市 → 日历 → 搜索）。

2026-11-02 用户裁决两条，本文件都跟了：
  1. **只做有登录的测试**，不登录的不测 —— 所以 fixture 从 `page`（匿名 context）
     换成 `shared_page`（session 级、加载合并后的登录态）。顺带把这条轨道并进
     「一次登录跑完」，不再每条用例重开 context。
  2. **每个功能最多 3 条用例**，只有明确要求全量回归时才跑全部。

2026-11-03 用户再裁决：数据准备多条，**每次随机挑 1 条**跑，靠多次运行覆盖全量
（`utils/case_rotation.py` 的轮换 + 随机，一个 cycle 内不重复）。
数据仍留在 `data/trains/smoke_cases.yaml`，运行集由 `train_smoke_cases_for_run()` 给；
要全量用 `E2E_FULL=1`，要复现某条用 `E2E_CASE_ID=<id>`。

断言分三层（对应裁决 D3）：
  结构层：结果条目数 >= min_results
  内容层：条目里能抽到形如 G6025 的车次号，且覆盖率达标
  辅助层：expected_keyword 只写进报告，不参与 pass/fail

blocked 与「日历控件唤不起」都走 xfail/skip 并留证据；其余失败一律是普通 fail。
"""

from __future__ import annotations

import pytest

from pages.train_search_page import TrainSearchPage
from utils.case_data import case_ids, train_smoke_cases_for_run
from utils.case_rotation import note as rotation_note

# 数据在 data/trains/smoke_cases.yaml（可用 CASE_DATA_DIR 覆盖）。
CASES = train_smoke_cases_for_run()

# ui：驱动浏览器，纳入 blocked 门禁的统计范围。数据层单测不得带这个标记。
pytestmark = pytest.mark.ui

# 内容层门槛：能抽出车次号的条目占比。真实结果里存在「成功率：较高」这类附加行，
# 抽不到车次号的条目可能是正常形态，所以用比例而不是要求 100%。
TRAIN_NO_COVERAGE = 0.9


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_train_search(case, shared_page, blocked_guard, widget_guard, request):
    """完整流程：点单程（首页默认态）→ 填城市 → 选日期 → 搜索 → 三层断言。"""
    train = TrainSearchPage(shared_page)
    # 失败/被拦时取证要截**这个**页面对象的现场；widget_guard 也靠它取页面对象
    # （惰性取，不登记就会回退去拉 function 级 base_page ⇒ 多起一个匿名 context）。
    request.node._base_page = train

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
            "操作链",
            train.action_log(),
        )
        # 子菜单零 URL 直达的硬证据：整条用例只允许 open() 导航一次。
        assert train.navigations == 1, (
            f"本次用例发生了 {train.navigations} 次页面导航，期望 1 次（只有 open()）"
        )

        request.node.add_report_section(
            "call",
            "查询信息",
            f"用例：{case.id}\n区间：{case.from_city}→{case.to_city}\n"
            f"本次运行集：{rotation_note('train_smoke')}\n"
            f"日期：{case.depart_date}（T+{case.depart_offset_days}）\n"
            f"URL 的 ticketType：{train.url_ticket_type()} / dDate：{train.url_depart_date()}\n"
            f"页面自称车次数：{train.declared_result_count(body)}\n"
            f"关键词「{case.expected_keyword}」是否出现：{case.expected_keyword in body}",
        )

        # 一次取回全部卡片文本（538 条／页，逐条 inner_text 会撞 180s 超时）
        texts = train.result_texts()

        # 站点降级的最早信号（2026-11-03 00:30 实测）：页面自称「共 0 车次」且出现
        # 「未找到符合条件的车次」，列表里只剩「中转方案推荐」的几张卡 ⇒ 覆盖率必然 0。
        # 这时如果只报"抽不到车次号"，排查方向会被引到选择器上，而真实原因是
        # **直达车次列表整体为空**（同期往返 ticketType=1 / 中转 ticketType=2 仍能取到 200+ 卡，
        # 匿名与登录态、三组不同区间与日期全部复现 ⇒ 站点侧状态，不是本仓库代码）。
        # 单独判这一条，让失败信息直接说明白。
        declared = train.declared_result_count(body)
        if declared == 0 and "未找到符合条件的车次" in body:
            pytest.fail(
                f"站点返回「共 {declared} 车次」：{case.from_city}→{case.to_city} "
                f"{case.depart_date} 的直达车次列表为空，列表里只剩 {len(texts)} 条中转推荐卡。\n"
                "这不是选择器/交互问题（表单回填与 URL 的 dDate 都正确）：同期往返与中转轨道"
                "仍能取到 200+ 张卡，匿名态与登录态、多组区间日期全部同样为空。\n"
                "复核方法见 docs/locator-evidence/FORM-MODE.md 第九节；恢复后重跑即可，"
                "不要为此改选择器或放宽阈值。"
            )

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
