"""用例数据的质量门。

数据是配置文件，配置写错必须在**开浏览器之前**就红 —— 否则表现会变成
「某条 UI 用例莫名失败」，而真正的原因是一个数字写错在 yaml 里。

这里只测数据本身该满足的约束，不测浏览器行为（那些在 suites/ 里）。
`-m ui` 不会选中本文件：它不带任何浏览器标记，跟着 `scope=unit` 走。

2026-11-02 方案 B：公共校验体抽到 utils/case_schema.py（单一事实源，
审批工具同源复用），本文件保留全部 test 函数名与断言语义，只改调用。
抽取前后逐条一致性对比证据：reports/logs/schema_before.json / schema_after.json。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from utils import case_data, case_schema, hotel_data, round_trip_data
from utils.case_data import (
    case_ids,
    describe,
    load_hotel_search_cases,
    load_round_trip_cases,
    load_train_smoke_cases,
)

TODAY = date(2026, 11, 1)


# ---------------------------------------------------------------- 酒店

def test_hotel_cases_are_valid():
    cases = load_hotel_search_cases(today=TODAY)
    case_schema.validate_hotel_cases(cases, today=TODAY)


# ---------------------------------------------------------------- 火车票

def test_train_cases_are_valid():
    cases = load_train_smoke_cases(today=TODAY)
    case_schema.validate_train_cases(cases, today=TODAY)


# ---------------------------------------------------------------- 偏移日期边界
#
# 基准日可注入（`parse_case(today=…)`），所以下面全是确定断言：
# 不依赖 `date.today()`，也不会因为正好跑在 23:59 而漂。

BASE_SAMPLE = {"id": "edge", "city": "上海"}


def _edge(offset: int, nights: int, base: date):
    return hotel_data.parse_case(
        {**BASE_SAMPLE, "checkin_offset_days": offset, "nights": nights}, today=base
    )


@pytest.mark.parametrize(
    "base, offset, nights, want_in, want_out",
    [
        # 跨月
        (date(2026, 11, 25), 7, 2, date(2026, 12, 2), date(2026, 12, 4)),
        # 跨年
        (date(2026, 12, 28), 7, 2, date(2027, 1, 4), date(2027, 1, 6)),
        # 月末 31 号 + 1
        (date(2026, 12, 31), 1, 1, date(2027, 1, 1), date(2027, 1, 2)),
        # 闰年 2 月末：2028-02-28 + 1 = 02-29（闰日），再住 2 晚跨到 03-02
        (date(2028, 2, 28), 1, 2, date(2028, 2, 29), date(2028, 3, 2)),
        # 平年 2 月末：2027-02-28 + 1 = 03-01（没有 02-29 可落）
        (date(2027, 2, 28), 1, 1, date(2027, 3, 1), date(2027, 3, 2)),
    ],
)
def test_offset_date_arithmetic(base, offset, nights, want_in, want_out):
    """跨月 / 跨年 / 月末 / 闰年与平年的 2 月末，入住与退房都要落对。"""
    case = _edge(offset, nights, base)
    assert (case.checkin, case.checkout) == (want_in, want_out), (
        f"基准 {base} + {offset} 天住 {nights} 晚 → 实际 {case.checkin}/{case.checkout}"
    )
    assert (case.checkout - case.checkin).days == nights


def test_base_date_is_injectable():
    """同一个偏移量、不同基准日 ⇒ 日期整体平移，证明基准日真的是参数而不是写死今天。"""
    a = load_hotel_search_cases(today=date(2026, 12, 24))
    b = load_hotel_search_cases(today=date(2027, 1, 24))
    shift = (date(2027, 1, 24) - date(2026, 12, 24)).days
    assert [c.id for c in a] == [c.id for c in b], "基准日不该影响用例集合"
    for older, newer in zip(a, b):
        assert (newer.checkin - older.checkin).days == shift
        assert (newer.checkout - older.checkout).days == shift


def test_base_date_captured_once(monkeypatch):
    """午夜边界：入住与退房必须由**同一次**取到的基准日推出。

    若写成 `checkin = date.today() + …` 再 `checkout = date.today() + …`，
    在 23:59:59.9 跨到次日时会多出一天，且只在午夜前后偶发红。
    这里把 `date.today()` 换成「第一次返回 11-01、之后返回 11-02」的假 date：
    真实现只调一次 ⇒ 稳定；调两次 ⇒ 立刻红。
    """
    calls: list[int] = []

    class FlakyDate(date):
        @classmethod
        def today(cls):  # noqa: D102 - 只在测试里用
            calls.append(1)
            return date(2026, 11, 1) if len(calls) == 1 else date(2026, 11, 2)

    monkeypatch.setattr(hotel_data, "date", FlakyDate)
    case = hotel_data.parse_case({**BASE_SAMPLE, "checkin_offset_days": 7, "nights": 2})

    assert len(calls) == 1, f"基准日取了 {len(calls)} 次 —— 午夜跨天时 checkout 会漂"
    assert case.checkin == date(2026, 11, 8)
    assert case.checkout == date(2026, 11, 10)
    assert (case.checkout - case.checkin).days == 2


# ---------------------------------------------------------------- 包装头与目录

def test_describe_reports_version_and_counts():
    text = describe()
    assert "suite=hotels" in text, f"酒店数据的 suite 头不对：{text}"
    assert "suite=trains" in text, f"火车票数据的 suite 头不对：{text}"
    assert "suite=trains_round_trip" in text, f"往返数据的 suite 头不对：{text}"
    # 三份数据文件都应是 version=1（2026-11-02 加往返，从 2 变 3）
    assert text.count("version=1") == 3, f"三份数据都应是 version=1：{text}"
    assert "cases=0" not in text, f"有空的数据文件：{text}"


def test_marker_matches_registered_pytest_marker():
    """数据里写的 marker 必须真的注册过，否则 `-m hotels` 会直接报 unknown marker。"""
    import pathlib

    ini = pathlib.Path("pytest.ini").read_text(encoding="utf-8")
    for marker in ("hotels", "ui"):
        assert "markers" in ini and marker in ini, f"pytest.ini 没注册 marker {marker!r}"


def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))
    assert case_data.data_dir() == tmp_path


def test_missing_data_dir_raises(tmp_path, monkeypatch):
    """路径写错必须报错，不能静默返回空列表 —— 空列表会让参数化「零用例」而测试仍绿。"""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        load_hotel_search_cases()


def test_bad_version_rejected(tmp_path):
    target = tmp_path / "search_cases.yaml"
    target.write_text("version: 99\ncases:\n  - id: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_hotel_search_cases(target)


def test_case_ids_are_ascii_and_unique():
    ids = case_ids(load_hotel_search_cases(today=TODAY)) + case_ids(
        load_train_smoke_cases(today=TODAY)
    )
    assert all(i.isascii() for i in ids), f"出现非 ASCII 参数化 id：{[i for i in ids if not i.isascii()]}"
    assert len(ids) == len(set(ids)), f"两条轨道的参数化 id 撞了：{ids}"


# ---------------------------------------------------------------- 火车票往返（2026-11-02）
#
# 往返数据比单程多一个 nights 与"周末锚点"。锚点解析涉及"下一个星期几 + 预售期边界"，
# 这类算术不测就会在某个特定日期悄悄错位 —— 而且错位方式是"跑了一条日期不对的用例"，
# 不会红。所以这里用固定基准日把边界全钉死。


def test_round_trip_cases_are_valid():
    cases = load_round_trip_cases(today=TODAY)
    case_schema.validate_round_trip_cases(cases, today=TODAY)


def test_round_trip_param_ids_are_ascii_and_unique():
    cases = load_round_trip_cases(today=TODAY)
    ids = case_ids(cases)
    assert all(i.isascii() for i in ids), f"往返参数化 id 含非 ASCII：{ids}"
    assert len(ids) == len(set(ids)), f"往返参数化 id 重复：{ids}"


@pytest.mark.parametrize(
    "base, weekday, want_date, want_offset",
    [
        # 2026-11-02 是周一。+3 天 = 11-05 周四，下一个周五 = 11-06，T+4。
        (date(2026, 11, 2), 4, "2026-11-06", 4),
        # 同一天锚周三：+3 = 周四，下一个周三 = 11-11，T+9。
        (date(2026, 11, 2), 2, "2026-11-11", 9),
        # 锚周一：+3 = 周四，下一个周一 = 11-09，T+7。
        (date(2026, 11, 2), 0, "2026-11-09", 7),
        # 2026-11-05 是周四，锚周五 = 次日 11-06（T+1）但低于下限 ⇒ 顺延到 11-13（T+8）。
        (date(2026, 11, 5), 4, "2026-11-13", 8),
        # 预售期边界：2026-11-02 锚周日 6：+3=11-05 周四，下一个周日 = 11-08（T+6），不越界。
        (date(2026, 11, 2), 6, "2026-11-08", 6),
        # 越界回退：2026-11-12 是周四，+3 = 11-15 周日，锚周六 ⇒ 11-21（T+9）。仍在内。
        (date(2026, 11, 12), 5, "2026-11-21", 9),
    ],
)
def test_round_trip_weekday_anchor(base, weekday, want_date, want_offset):
    got_date, got_offset = round_trip_data.resolve_weekday(weekday, base)
    assert (got_date, got_offset) == (want_date, want_offset), (
        f"基准日 {base} 锚 weekday={weekday}：得到 {got_date}(T+{got_offset})，"
        f"期望 {want_date}(T+{want_offset})"
    )
    offset = (date.fromisoformat(got_date) - base).days
    assert offset == got_offset, "返回的偏移量与日期不自洽"
    assert round_trip_data.MIN_OFFSET_DAYS <= offset <= round_trip_data.MAX_OFFSET_DAYS, (
        f"锚点解析越界：T+{offset}"
    )


def test_round_trip_weekday_anchor_rolls_back_within_presale():
    """越界必须**往前退一周**（仍是同一个星期几），不能抛错也不能夹到预售期末。"""
    # 2026-11-09 是周一。+3 = 11-12 周四，锚周五 = 11-13（T+4），远未越界；
    # 换成锚周二：11-12 周四 → 下一个周二 11-17（T+8）。要造越界得从"极限"那一天试：
    # 2026-11-10（周二）+3 = 11-13 周五，锚周四 ⇒ 11-19（T+9）。
    date_a, offset_a = round_trip_data.resolve_weekday(3, date(2026, 11, 10))
    assert date_a == "2026-11-19" and offset_a == 9
    # 该日期必须仍是周四（0=周一 … 3=周四）
    assert date.fromisoformat(date_a).weekday() == 3


def test_round_trip_requires_exactly_one_anchor():
    """offset 与 weekday 必须二选一：两个都给、都不给都要报错，不能默认取一个。"""
    base = {"id": "rt-x", "from_city": "上海", "to_city": "北京", "nights": 1}
    with pytest.raises(round_trip_data.DataError, match="二选一"):
        round_trip_data.parse_case({**base, "depart_offset_days": 5, "depart_weekday": 4}, TODAY)
    with pytest.raises(round_trip_data.DataError, match="二选一"):
        round_trip_data.parse_case(base, TODAY)


def test_round_trip_rejects_bad_train_type_and_scope():
    base = {
        "id": "rt-x", "from_city": "上海", "to_city": "北京",
        "nights": 1, "depart_offset_days": 5,
    }
    with pytest.raises(round_trip_data.DataError, match="合法字头"):
        round_trip_data.parse_case({**base, "train_types": ["G", "X"]}, TODAY)
    with pytest.raises(round_trip_data.DataError, match="city_scope"):
        round_trip_data.parse_case({**base, "city_scope": "nearby"}, TODAY)


def test_round_trip_rejects_out_of_presale_offset():
    base = {"id": "rt-x", "from_city": "上海", "to_city": "北京", "nights": 1}
    with pytest.raises(round_trip_data.DataError, match="预售期"):
        round_trip_data.parse_case({**base, "depart_offset_days": 20}, TODAY)
