"""用例数据的公共校验（单一事实源）。

从 tools/tests/test_case_data.py **逐字搬入**（2026-11-02 方案 B，用户批准）：
断言消息一条未改，保证抽取前后同一批 case 校验结果逐条一致
（对比证据：reports/logs/schema_before.json vs schema_after.json）。

为什么独立成模块：审批工具（阶段 3）要直接 import 同一套校验，
避免「复制一份字段检查」日后与 CI 门禁漂移。

tools/tests/test_case_data.py 保留全部 test 函数名与断言语义，
只把公共校验体替换为对这里的调用 —— CI 门禁行为不变。

本模块只做数据约束，不触网、不开浏览器。
"""

from __future__ import annotations

import re
from datetime import timedelta

# id 必须是 ASCII slug：中文 id 进 junit 会变成 \u665a 这种转义，日志里读不出来。
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# 只做粗筛：手机号、18 位身份证、15 位旧身份证。不追求严谨识别，追求「写进去就会被拦」。
SENSITIVE = re.compile(r"(\b1[3-9]\d{9}\b|\b\d{17}[\dXx]\b|\b\d{15}\b)")

# expected 的**闭集**：只允许用例真的会消费的键。写进来却没人读 = 会漂的第二个真相。
# 酒店的 display 文案**不在**闭集里：段数随城市变（广州是「城市, 省, 中国」），
# 落进数据就是把会变的东西钉成预期；断言只做「包含城市中文名」，中文名来自输入 city。
HOTEL_EXPECTED_KEYS = {"city_id"}
TRAIN_EXPECTED_KEYS = {"http_status", "title_contains"}


def common(cases, label: str) -> None:
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), f"{label}: id 重复 —— {ids}"
    for case in cases:
        assert ID_PATTERN.fullmatch(case.id), (
            f"{label}: id 必须是小写 ASCII slug（字母数字与 - _），收到 {case.id!r}"
        )
        assert case.tags, f"{label}: {case.id} 没有 tags"
        assert case.name, f"{label}: {case.id} 没有中文 name（报告里只显示 id 不好读）"
        assert case.expected, f"{label}: {case.id} 没有 expected —— 没有预期就不是用例"
        assert not SENSITIVE.search(repr(case)), f"{label}: {case.id} 里像是写了手机号/身份证"
        assert case.param_id.isascii(), (
            f"{label}: {case.id} 的参数化 id 含非 ASCII 字符：{case.param_id!r}"
        )


def validate_hotel_cases(cases, today=None) -> None:
    """酒店数据全量校验（test_hotel_cases_are_valid 的校验体，断言顺序原样保留）。

    today：基准日。test 传固定基准日；审批工具传真实今天。
    不传时跳过「入住日必须晚于今天」这条依赖基准日的断言。
    本模块不引用 date.today()，保持纯函数。
    """
    common(cases, "hotels")
    assert len(cases) >= 3, "一期至少保留三条（上海/北京/广州）"

    for case in cases:
        assert case.nights >= 1, f"{case.id}: nights 必须 >= 1"
        assert case.rooms >= 1 and case.adults >= 1, f"{case.id}: 房间/成人必须 >= 1"
        # 页面耦合：从 1 间加到 2 间时页面会把成人补到 2，所以 adults < rooms 无解
        assert case.adults >= case.rooms, (
            f"{case.id}: adults({case.adults}) < rooms({case.rooms}) —— "
            "加房间会自动补住客，set_rooms 会直接拒绝这种数据"
        )
        assert case.children == 0, (
            f"{case.id}: 一期不覆盖儿童（加儿童会多出年龄选择控件，探针未覆盖）"
        )
        assert case.checkout == case.checkin + timedelta(days=case.nights), (
            f"{case.id}: 退房日必须等于入住日 + nights"
        )
        if today is not None:
            assert case.checkin > today, f"{case.id}: 入住日必须晚于今天（偏移量应当为正）"
        assert case.expected.keys() <= HOTEL_EXPECTED_KEYS, (
            f"{case.id}: expected 里有没人消费的键 "
            f"{sorted(set(case.expected) - HOTEL_EXPECTED_KEYS)}"
        )
        city_id = case.expected.get("city_id")
        assert isinstance(city_id, int) and not isinstance(city_id, bool) and city_id > 0, (
            f"{case.id}: expected.city_id 必须是正整数（站点侧城市 id，"
            f"实测见 reports/probe_city_id.py），收到 {city_id!r}"
        )


def validate_train_cases(cases, today) -> None:
    """火车票数据全量校验（test_train_cases_are_valid 的校验体）。"""
    common(cases, "trains")
    assert cases, "至少一条冒烟"

    for case in cases:
        assert case.from_city != case.to_city, f"{case.id}: 出发与到达不能相同"
        assert 3 <= case.depart_offset_days <= 15, (
            f"{case.id}: depart_offset_days 必须在 +3..+15（预售期）"
        )
        assert case.min_results >= 1, f"{case.id}: min_results 必须 >= 1"
        assert case.expected.keys() <= TRAIN_EXPECTED_KEYS, (
            f"{case.id}: expected 里有没人消费的键 "
            f"{sorted(set(case.expected) - TRAIN_EXPECTED_KEYS)}"
        )
        assert case.expected.get("http_status") == 200, (
            f"{case.id}: 期望 http_status 应为 200 —— 无头下的 432 是 K-05 记录的环境问题，"
            "不是这条数据该编码的东西"
        )
        assert case.expected.get("title_contains"), (
            f"{case.id}: expected.title_contains 不能为空"
        )
        assert case.depart_date == (today + timedelta(days=case.depart_offset_days)).isoformat()
