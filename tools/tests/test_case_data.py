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

from utils import case_data, case_schema, hotel_data
from utils.case_data import (
    case_ids,
    describe,
    load_hotel_search_cases,
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
    assert text.count("version=1") == 2, f"两份数据都应是 version=1：{text}"
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
