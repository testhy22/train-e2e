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

from utils import case_data
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
        city_display = case.expected.get("city_display", "")
        assert city_display.startswith(case.city), (
            f"{case.id}: expected.city_display 应以城市名开头，收到 {city_display!r}"
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
        assert case.expected.get("http_status") == 200, (
            f"{case.id}: 期望 http_status 应为 200 —— 无头下的 432 是 K-05 记录的环境问题，"
            "不是这条数据该编码的东西"
        )
        assert case.expected.get("title_contains"), (
            f"{case.id}: expected.title_contains 不能为空"
        )
        assert case.depart_date == (TODAY + timedelta(days=case.depart_offset_days)).isoformat()


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
