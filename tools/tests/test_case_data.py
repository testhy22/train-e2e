"""用例数据的质量门。

数据是配置文件，配置写错必须在**开浏览器之前**就红 —— 否则表现会变成
「某条 UI 用例莫名失败」，而真正的原因是一个数字写错在 yaml 里。

这里只测数据本身该满足的约束，不测浏览器行为（那些在 suites/ 里）。
`-m ui` 不会选中本文件：它不带任何浏览器标记，跟着 `scope=unit` 走。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from utils import case_data, hotel_data
from utils.case_data import (
    case_ids,
    describe,
    load_hotel_search_cases,
    load_train_smoke_cases,
)

TODAY = date(2026, 11, 1)

# id 必须是 ASCII slug：中文 id 进 junit 会变成 \u665a 这种转义，日志里读不出来。
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# 只做粗筛：手机号、18 位身份证、15 位旧身份证。不追求严谨识别，追求「写进去就会被拦」。
SENSITIVE = re.compile(r"(\b1[3-9]\d{9}\b|\b\d{17}[\dXx]\b|\b\d{15}\b)")

# expected 的**闭集**：只允许用例真的会消费的键。写进来却没人读 = 会漂的第二个真相。
# 酒店的 display 文案**不在**闭集里：段数随城市变（广州是「城市, 省, 中国」），
# 落进数据就是把会变的东西钉成预期；断言只做「包含城市中文名」，中文名来自输入 city。
HOTEL_EXPECTED_KEYS = {"city_id"}
TRAIN_EXPECTED_KEYS = {"http_status", "title_contains"}


def _common(cases, label: str) -> None:
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


# ---------------------------------------------------------------- 酒店

def test_hotel_cases_are_valid():
    cases = load_hotel_search_cases(today=TODAY)
    _common(cases, "hotels")
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
        assert case.checkin > TODAY, f"{case.id}: 入住日必须晚于今天（偏移量应当为正）"
        assert case.expected.keys() <= HOTEL_EXPECTED_KEYS, (
            f"{case.id}: expected 里有没人消费的键 "
            f"{sorted(set(case.expected) - HOTEL_EXPECTED_KEYS)}"
        )
        city_id = case.expected.get("city_id")
        assert isinstance(city_id, int) and not isinstance(city_id, bool) and city_id > 0, (
            f"{case.id}: expected.city_id 必须是正整数（站点侧城市 id，"
            f"实测见 reports/probe_city_id.py），收到 {city_id!r}"
        )


# ---------------------------------------------------------------- 火车票

def test_train_cases_are_valid():
    cases = load_train_smoke_cases(today=TODAY)
    _common(cases, "trains")
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
        assert case.depart_date == (TODAY + timedelta(days=case.depart_offset_days)).isoformat()


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
