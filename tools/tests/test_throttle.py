"""门禁：全局节流器（utils/throttle.py）。

只测「算得对、关得掉」，不测「有没有防住风控」—— 后者本工程没有可复现的判据，
不在这里假装有。所有等待都用 0.1–0.3 秒量级，整套 < 2s。
"""

from __future__ import annotations

import time

import pytest

from utils import throttle


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每条用例前后清空进程级状态与两个阈值，避免互相污染。"""
    throttle.reset()
    monkeypatch.delenv("E2E_THROTTLE", raising=False)
    monkeypatch.setenv("E2E_MIN_ACTION_INTERVAL_S", "0.3")
    monkeypatch.setenv("E2E_CASE_GAP_S", "0.3")
    yield
    throttle.reset()


def test_first_action_does_not_wait():
    started = time.monotonic()
    slept = throttle.wait_action()
    assert slept == 0.0
    assert time.monotonic() - started < 0.2


def test_second_action_fills_the_interval():
    throttle.wait_action()
    slept = throttle.wait_action()
    # 补足量应当接近 0.3s（允许调度抖动），而不是 0
    assert 0.15 <= slept <= 0.6


def test_interval_is_measured_from_last_action(monkeypatch):
    monkeypatch.setenv("E2E_MIN_ACTION_INTERVAL_S", "0.25")
    throttle.wait_action()
    time.sleep(0.25)
    # 已经等够了，再调不应再等
    assert throttle.wait_action() == 0.0


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("E2E_THROTTLE", "0")
    throttle.wait_action()
    started = time.monotonic()
    assert throttle.wait_action() == 0.0
    assert time.monotonic() - started < 0.1
    assert throttle.enabled() is False


def test_illegal_interval_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("E2E_MIN_ACTION_INTERVAL_S", "abc")
    assert throttle.min_action_s() == throttle.DEFAULT_MIN_ACTION_S
    monkeypatch.setenv("E2E_MIN_ACTION_INTERVAL_S", "-5")
    assert throttle.min_action_s() == 0.0


def test_case_gap_only_between_cases():
    # 首条用例不等
    assert throttle.wait_case_gap() == 0.0
    throttle.mark_case_end()
    assert throttle.wait_case_gap() >= 0.15


def test_case_gap_independent_of_action_interval(monkeypatch):
    monkeypatch.setenv("E2E_CASE_GAP_S", "0")
    throttle.mark_case_end()
    assert throttle.wait_case_gap() == 0.0
    # 动作间隔不受影响
    throttle.wait_action()
    assert throttle.wait_action() >= 0.15


# ---------------------------------------------------------------- 每小时配额
# 2026-11-07 加：动作间隔只管「一次运行内部」，跨运行的密度由配额管。
# 这里只验「数得对、窗会滑、坏文件不致命」，不验「有没有降低被拦概率」。

def _quota(tmp_path, monkeypatch, limit: str = "2", window: str = "3600") -> None:
    monkeypatch.setenv("E2E_QUOTA_FILE", str(tmp_path / "quota.json"))
    monkeypatch.setenv("E2E_HOURLY_QUOTA", limit)
    monkeypatch.setenv("E2E_QUOTA_WINDOW_S", window)


def test_quota_counts_up_then_blocks(tmp_path, monkeypatch):
    _quota(tmp_path, monkeypatch, limit="2")
    assert throttle.reserve_search() == 1
    assert throttle.reserve_search() == 2
    with pytest.raises(throttle.QuotaExhausted) as exc:
        throttle.reserve_search()
    assert exc.value.used == 2 and exc.value.limit == 2
    assert exc.value.wait_s >= 0


def test_quota_persists_across_processes(tmp_path, monkeypatch):
    """配额必须落盘：新进程 = 新内存，只有文件能让「上一次运行」被记住。"""
    _quota(tmp_path, monkeypatch, limit="1")
    assert throttle.reserve_search() == 1
    assert (tmp_path / "quota.json").exists()
    throttle.reset()                       # 模拟进程重启（内存状态清空）
    with pytest.raises(throttle.QuotaExhausted):
        throttle.reserve_search()


def test_quota_window_slides(tmp_path, monkeypatch):
    _quota(tmp_path, monkeypatch, limit="1", window="60")
    path = tmp_path / "quota.json"
    path.write_text('{"searches": [%f]}' % (time.time() - 3600), encoding="utf-8")
    assert throttle.reserve_search() == 1   # 一小时前那次已滑出窗口


def test_quota_zero_means_unlimited(tmp_path, monkeypatch):
    _quota(tmp_path, monkeypatch, limit="0")
    for _ in range(5):
        assert throttle.reserve_search() == 0


def test_quota_off_when_throttle_disabled(tmp_path, monkeypatch):
    _quota(tmp_path, monkeypatch, limit="1")
    monkeypatch.setenv("E2E_THROTTLE", "0")
    assert throttle.reserve_search() == 0
    assert not (tmp_path / "quota.json").exists()


def test_corrupt_quota_file_is_tolerated(tmp_path, monkeypatch):
    """坏文件按「无历史」处理 —— 一个坏 JSON 不该让整轮跑不动。"""
    _quota(tmp_path, monkeypatch, limit="1")
    (tmp_path / "quota.json").write_text("{not json", encoding="utf-8")
    assert throttle.reserve_search() == 1
