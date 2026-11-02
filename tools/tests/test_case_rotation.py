"""轮换挑选的门禁（`utils/case_rotation.py`）。

这条机制的要害不在「随机」，在**承诺**：单次只花 1 条的时间，但一个 cycle 内
把全量覆盖完且不重复。这条承诺破了，测试仍然全绿 —— 只是悄悄变成了「每天只跑同一条」，
没人会发现。所以下面把承诺、开关优先级、状态损坏的退化全钉死。

本文件不碰浏览器、不带 `-m ui`，跟着 `scope=unit` 走。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils import case_rotation


class Fake:
    """只需要 `id` —— pick() 对用例对象的唯一要求，用真数据类反而绑定字段。"""

    def __init__(self, cid: str) -> None:
        self.id = cid

    def __repr__(self) -> str:  # 断言失败时看得懂是什么
        return f"Fake({self.id})"


def make_cases(count: int) -> list[Fake]:
    return [Fake(f"c{index}") for index in range(count)]


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """每个用例独立的轮换状态 + 清干净开关，避免相互串味。"""
    path = tmp_path / ".e2e-state" / "case_rotation.json"
    monkeypatch.setenv("E2E_ROTATION_STATE", str(path))
    for env in ("E2E_CASE_QUOTA", "E2E_FULL", "E2E_CASE_ID", "E2E_SEED"):
        monkeypatch.delenv(env, raising=False)
    return path


def ids(selected: list[Fake]) -> list[str]:
    return [case.id for case in selected]


# ---------------------------------------------------------------- 默认行为

def test_default_picks_exactly_one():
    """默认每功能 1 条 —— 这是"减少测试时间"的全部来源，多挑一条就白省。"""
    assert len(case_rotation.pick("t", make_cases(9))) == 1
    assert case_rotation.quota() == case_rotation.DEFAULT_QUOTA == 1


def test_empty_cases_returns_empty():
    """数据为空时不能崩（否则整个模块 import 失败，等于整条轨道消失）。"""
    assert case_rotation.pick("t", []) == []


def test_single_case_dataset_is_returned_as_is():
    """数据只有 1 条时不进轮换逻辑，原样返回（否则会算出一轮已跑完）。"""
    only = make_cases(1)
    assert case_rotation.pick("t", only) == only
    assert case_rotation.pick("t", only) == only


# ---------------------------------------------------------------- 覆盖承诺（核心）

def test_one_cycle_covers_every_case_exactly_once():
    """9 条数据连跑 9 次，必须 9 条全见到且各一次。

    这是「随机挑 1 条」与「覆盖更多数据」能同时成立的唯一理由。换回
    `random.choice`（有放回）这条断言在多数情况下就会红（期望只覆盖 ~5.9 条）。
    """
    cases = make_cases(9)
    picked: list[str] = []
    for _ in range(9):
        picked.extend(ids(case_rotation.pick("t", cases)))

    assert len(picked) == 9
    assert len(set(picked)) == 9, f"一轮里重复挑了：{picked}"
    assert set(picked) == {case.id for case in cases}, f"一轮没覆盖全：{picked}"


def test_new_cycle_restarts_after_full_coverage():
    """跑满一轮后开新循环，而不是永远返回空 / 卡在最后一条。"""
    cases = make_cases(3)
    for _ in range(3):
        case_rotation.pick("t", cases)
    state = json.loads(case_rotation.state_path().read_text(encoding="utf-8"))
    assert len(state["tracks"]["t"]["used"]) == 3

    again = ids(case_rotation.pick("t", cases))
    assert len(again) == 1 and again[0] in {c.id for c in cases}
    state = json.loads(case_rotation.state_path().read_text(encoding="utf-8"))
    assert state["tracks"]["t"]["used"] == again, "新一轮应从 1 条重新计数"


def test_tracks_keep_separate_progress():
    """三条轨道各记各的 —— 共用一份进度会让"往返跑过 3 次"把"单程"也顶到下一轮。"""
    for _ in range(2):
        case_rotation.pick("a", make_cases(3))
    case_rotation.pick("b", make_cases(3))
    state = json.loads(case_rotation.state_path().read_text(encoding="utf-8"))
    assert len(state["tracks"]["a"]["used"]) == 2
    assert len(state["tracks"]["b"]["used"]) == 1


# ---------------------------------------------------------------- 开关

def test_quota_env_takes_several_distinct(monkeypatch):
    monkeypatch.setenv("E2E_CASE_QUOTA", "3")
    picked = ids(case_rotation.pick("t", make_cases(9)))
    assert len(picked) == 3 and len(set(picked)) == 3, f"同一次里重复挑了：{picked}"


@pytest.mark.parametrize("raw", ["abc", "0", "-2", "  "])
def test_bad_quota_falls_back_to_default(monkeypatch, raw):
    """配额写错不能让轨道变成 0 条，也不能崩 —— 退回默认 1 条。"""
    monkeypatch.setenv("E2E_CASE_QUOTA", raw)
    assert case_rotation.quota() == 1
    assert len(case_rotation.pick("t", make_cases(5))) == 1


def test_full_run_returns_all_and_clears_progress(monkeypatch):
    """`E2E_FULL=1` 跑全量，并把该轨道的一轮标记为跑完（否则下次只挑"剩下的尾巴"）。"""
    cases = make_cases(9)
    case_rotation.pick("t", cases)
    monkeypatch.setenv("E2E_FULL", "1")
    assert len(case_rotation.pick("t", cases)) == 9

    state = json.loads(case_rotation.state_path().read_text(encoding="utf-8"))
    assert state["tracks"]["t"]["used"] == [], "全量跑完应清空本轮进度"
    monkeypatch.delenv("E2E_FULL")
    assert len(case_rotation.pick("t", cases)) == 1


def test_pinned_id_wins_over_rotation(monkeypatch):
    """钉死 id 时连挑 5 次都是同一条 —— 复现失败用例靠这个，不靠 seed。"""
    monkeypatch.setenv("E2E_CASE_ID", "c4")
    for _ in range(5):
        assert ids(case_rotation.pick("t", make_cases(9))) == ["c4"]


def test_pinned_id_can_name_several(monkeypatch):
    monkeypatch.setenv("E2E_CASE_ID", "c1, c7")
    assert ids(case_rotation.pick("t", make_cases(9))) == ["c1", "c7"]


def test_pinned_id_of_other_track_is_ignored(monkeypatch):
    """钉的是别的轨道的 id 时，本轨道退回轮换，而不是返回空列表。

    返回空列表的后果是「本次 0 条用例」，报告里看起来全绿，实际什么都没测。
    """
    monkeypatch.setenv("E2E_CASE_ID", "rt-gz-cs-near")
    picked = ids(case_rotation.pick("t", make_cases(9)))
    assert len(picked) == 1 and picked[0].startswith("c")


def test_same_seed_same_choice_on_fresh_state(monkeypatch, tmp_path):
    """同一 seed + 同一轮换状态 ⇒ 同一条。这是 seed 唯一能保证的事，别当复现手段用。"""
    monkeypatch.setenv("E2E_SEED", "42")
    monkeypatch.setenv("E2E_ROTATION_STATE", str(tmp_path / "a.json"))
    first = ids(case_rotation.pick("t", make_cases(9)))
    monkeypatch.setenv("E2E_ROTATION_STATE", str(tmp_path / "b.json"))
    second = ids(case_rotation.pick("t", make_cases(9)))
    assert first == second


# ---------------------------------------------------------------- 状态文件的健壮性

def test_corrupt_state_does_not_crash(isolated_state):
    """状态文件被写坏（半截 JSON）时当"没跑过"，不能把整轮测试带崩。"""
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text("{not json", encoding="utf-8")
    picked = ids(case_rotation.pick("t", make_cases(4)))
    assert len(picked) == 1
    assert json.loads(isolated_state.read_text(encoding="utf-8"))["version"] == 1


def test_wrong_state_version_is_discarded(isolated_state):
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text(
        json.dumps({"version": 99, "tracks": {"t": {"used": ["c0", "c1", "c2"]}}}),
        encoding="utf-8",
    )
    picked = ids(case_rotation.pick("t", make_cases(3)))
    assert len(picked) == 1, "版本不符应整套丢弃，而不是继续用旧进度"


def test_stale_ids_in_state_are_dropped(isolated_state):
    """数据被删改后，状态里的失效 id 必须过滤掉，否则「剩余池」会算错甚至空转。"""
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text(
        json.dumps({"version": 1, "tracks": {"t": {"used": ["c0", "gone"]}}}),
        encoding="utf-8",
    )
    picked = ids(case_rotation.pick("t", make_cases(3)))
    assert picked and picked[0] != "c0", f"已用过的 c0 不该再被挑到：{picked}"
    used = json.loads(isolated_state.read_text(encoding="utf-8"))["tracks"]["t"]["used"]
    assert "gone" not in used, f"失效 id 没清掉：{used}"


def test_state_lives_outside_reports(isolated_state, monkeypatch):
    """状态**不能**放 reports/：清理器按 mtime 删，删了等于每 24h 重置一轮覆盖。

    同时确认它被 .gitignore 挡住 —— 运行产物不该进仓库。
    """
    monkeypatch.delenv("E2E_ROTATION_STATE")
    default = case_rotation.state_path()
    assert default.parts[-2] == ".e2e-state", f"默认路径应在 .e2e-state 下：{default}"
    assert "reports" not in default.parts, f"状态落进了 reports/：{default}"

    ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert ".e2e-state/" in ignore, ".gitignore 没挡住轮换状态目录"


# ---------------------------------------------------------------- 报告可追溯

def test_note_reports_progress_and_seed():
    """报告里要能看出「这次为什么是这条」，参数 id 只说跑了哪条，不说轮换到第几次。"""
    case_rotation.pick("round_trip", make_cases(9))
    text = case_rotation.note("round_trip")
    assert "轮换 1/9" in text, f"进度没写进报告：{text}"
    assert "seed=" in text, f"seed 没写进报告：{text}"


def test_note_without_pick_is_explicit():
    """没走过 pick() 就说清楚，而不是返回空串让人以为"没有筛选"。"""
    assert "未经 pick()" in case_rotation.note("never_picked")
