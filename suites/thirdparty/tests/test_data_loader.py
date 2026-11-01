"""数据层的单元测试。不需要浏览器，纯离线。"""

from datetime import date

import pytest

from utils import data_loader
from utils.data_loader import DataError, load_cases, param_ids

TODAY = date(2026, 10, 31)
# 2026-11-01 数据迁移：旧用例目录（113d30a 已删）→ data/trains/，并加了 version/cases 包装。
# 本文件测的是解析器，包装的定位与版本校验在 tools/tests/test_case_data.py。
YAML_PATH = "data/trains/smoke_cases.yaml"


def write(tmp_path, body: str):
    p = tmp_path / "cases.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def one(**overrides) -> str:
    item = {
        "id": "sh-bj",
        "from_city": "上海",
        "to_city": "北京",
        "depart_offset_days": 7,
    }
    item.update(overrides)
    lines = "\n".join(f"  {k}: {v!r}" for k, v in item.items())
    return "- " + lines.lstrip() + "\n"


# ---------------------------------------------------------------- 正常路径

def test_loads_three_cases_from_repo_file():
    cases = load_cases(YAML_PATH, today=TODAY)
    assert len(cases) == 3
    assert [c.id for c in cases] == ["sh-bj", "gz-sz", "hz-nj"]
    assert [c.depart_offset_days for c in cases] == [7, 10, 13]


def test_offset_is_resolved_to_concrete_date():
    cases = load_cases(YAML_PATH, today=TODAY)
    assert cases[0].depart_date == "2026-11-07"
    assert cases[1].depart_date == "2026-11-10"
    assert cases[2].depart_date == "2026-11-13"


def test_param_id_carries_offset_and_date():
    cases = load_cases(YAML_PATH, today=TODAY)
    assert param_ids(cases)[0] == "sh-bj-T+7-2026-11-07"


def test_min_results_defaults_to_one(tmp_path):
    cases = load_cases(write(tmp_path, one()), today=TODAY)
    assert cases[0].min_results == 1


def test_expected_keyword_defaults_to_empty(tmp_path):
    cases = load_cases(write(tmp_path, one()), today=TODAY)
    assert cases[0].expected_keyword == ""


# ---------------------------------------------------------------- 偏移量边界

@pytest.mark.parametrize("offset", [2, 0, -1, 16, 30, 60])
def test_offset_out_of_range_rejected(tmp_path, offset):
    with pytest.raises(DataError, match="depart_offset_days"):
        load_cases(write(tmp_path, one(depart_offset_days=offset)), today=TODAY)


@pytest.mark.parametrize("offset", [3, 15])
def test_offset_boundaries_accepted(tmp_path, offset):
    cases = load_cases(write(tmp_path, one(depart_offset_days=offset)), today=TODAY)
    assert cases[0].depart_offset_days == offset


@pytest.mark.parametrize("bad", ["7", "2026-11-07", None, True, 7.5])
def test_non_integer_offset_rejected(tmp_path, bad):
    with pytest.raises(DataError, match="depart_offset_days"):
        load_cases(write(tmp_path, one(depart_offset_days=bad)), today=TODAY)


def test_past_date_guard_fires_when_floor_is_lowered(tmp_path, monkeypatch):
    """默认下限 +3 下这条分支不可达；把下限调低后才可触发。

    保留这个守卫是因为下限本身可配置——一旦有人为了「当天也能测」把下限改成 0 或负数，
    这里必须拦住，而不是静默产出一个过去的日期。
    """
    monkeypatch.setattr(data_loader, "MIN_OFFSET_DAYS", -10)
    with pytest.raises(DataError, match="早于今天"):
        load_cases(write(tmp_path, one(depart_offset_days=-1)), today=TODAY)


# ---------------------------------------------------------------- 结构校验

def test_min_results_must_be_positive(tmp_path):
    with pytest.raises(DataError, match="min_results"):
        load_cases(write(tmp_path, one(min_results=0)), today=TODAY)


def test_duplicate_id_rejected(tmp_path):
    body = one() + one()
    with pytest.raises(DataError, match="重复"):
        load_cases(write(tmp_path, body), today=TODAY)


def test_missing_required_field_rejected(tmp_path):
    body = "- id: sh-bj\n  from_city: 上海\n  to_city: 北京\n"
    with pytest.raises(DataError, match="depart_offset_days"):
        load_cases(write(tmp_path, body), today=TODAY)


def test_empty_city_rejected(tmp_path):
    with pytest.raises(DataError, match="from_city"):
        load_cases(write(tmp_path, one(from_city="  ")), today=TODAY)


def test_id_with_path_separator_rejected(tmp_path):
    with pytest.raises(DataError, match="id 只能用"):
        load_cases(write(tmp_path, one(id="../../etc/passwd")), today=TODAY)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(DataError, match="不存在"):
        load_cases(tmp_path / "nope.yaml", today=TODAY)


def test_broken_yaml_rejected(tmp_path):
    with pytest.raises(DataError, match="YAML"):
        load_cases(write(tmp_path, "- id: [unclosed\n"), today=TODAY)


def test_wrapped_without_version_rejected(tmp_path):
    """有包装就必须有 version：没有版本号的格式以后没法和新格式区分开。"""
    with pytest.raises(DataError, match="version 必须是 1"):
        load_cases(write(tmp_path, "cases: []\n"), today=TODAY)


def test_top_level_must_be_list_or_wrapped(tmp_path):
    with pytest.raises(DataError, match="顶层必须是列表或含 cases 的映射"):
        load_cases(write(tmp_path, '"just a string"\n'), today=TODAY)


def test_wrapped_format_is_accepted(tmp_path):
    """version: 1 + cases: 包装要被接受 —— data/ 下的文件都是这个形状。"""
    body = "version: 1\nsuite: trains\ncases:\n" + one()
    cases = load_cases(write(tmp_path, body), today=TODAY)
    assert [c.id for c in cases] == ["sh-bj"]


def test_empty_cases_rejected(tmp_path):
    with pytest.raises(DataError, match="没有任何用例"):
        load_cases(write(tmp_path, "version: 1\ncases: []\n"), today=TODAY)
