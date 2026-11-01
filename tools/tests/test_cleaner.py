"""`utils/cleaner.py` 与 `utils/logger.py` 的行为测试（纯本地、不起浏览器）。

为什么这些要有单测：清理器是**会删文件**的代码。写错一个路径判断，
表现不是「测试挂了」，而是「证据没了」——所以它的边界必须在毫秒级跑完的单测里锁死，
而不是等某次真跑之后发现报告目录是空的。

两个刻意的写测方式：
  · 用 `monkeypatch` 把 `settings.PROJECT_ROOT` 指到 `tmp_path`，
    而不是给清理器开「测试模式」后门 —— 后门一旦存在，线上也会被用到；
  · 用 `os.utime` 造「48 小时前的文件」，不 sleep、不改系统时钟。
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from config import settings
from utils.cleaner import CleanResult, clean_old_reports
from utils.logger import redact


@pytest.fixture
def reports_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把项目根指到 tmp_path，使 `tmp_path/reports` 成为「项目内的 reports」。"""
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    root = tmp_path / "reports"
    (root / "logs").mkdir(parents=True)
    (root / "screenshots").mkdir()
    return root


def _age(path: Path, hours: float) -> None:
    """把文件 mtime 推到 hours 小时之前。"""
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


def test_deletes_only_expired_files(reports_root: Path) -> None:
    old = reports_root / "logs" / "run_old.log"
    new = reports_root / "logs" / "run_new.log"
    shot = reports_root / "screenshots" / "old__full.png"
    keep = reports_root / "logs" / ".gitkeep"
    for path in (old, new, shot, keep):
        path.write_text("x", encoding="utf-8")
    _age(old, 48)
    _age(shot, 30)
    _age(keep, 48)                     # 占位文件再老也要留

    result = clean_old_reports(reports_root, expire_hours=24)

    assert not old.exists(), "超过阈值的日志应被删除"
    assert not shot.exists(), "超过阈值的截图应被删除"
    assert new.exists(), "新鲜文件不能删"
    assert keep.exists(), ".gitkeep 必须保留，否则目录结构会丢"
    assert result.deleted == 2
    assert result.freed_bytes > 0
    assert "释放" in result.describe()


def test_empty_dirs_are_removed(reports_root: Path) -> None:
    nested = reports_root / "probe_x"
    nested.mkdir()
    target = nested / "old.json"
    target.write_text("{}", encoding="utf-8")
    _age(target, 48)

    result = clean_old_reports(reports_root, expire_hours=24)

    assert result.deleted == 1
    assert not nested.exists(), "清空后的子目录应被删除"
    assert result.removed_dirs >= 1


def test_keeps_evidence_root_dirs_even_when_empty(reports_root: Path) -> None:
    """`logs/`、`screenshots/` 这些一级证据出口即使清空了也不删。"""
    empty_logs = reports_root / "logs"
    assert not any(empty_logs.iterdir())

    clean_old_reports(reports_root, expire_hours=24)

    assert empty_logs.is_dir(), "一级证据目录必须保留"


def test_dry_run_deletes_nothing(reports_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_CLEAN_DRY_RUN", "1")
    target = reports_root / "logs" / "old.log"
    target.write_text("x", encoding="utf-8")
    _age(target, 48)

    result = clean_old_reports(reports_root, expire_hours=24)

    assert target.exists(), "演练模式绝不能真删"
    assert result.dry_run is True
    assert result.deleted == 1, "演练模式仍要统计「本来会删几个」"
    assert "未删除" in result.describe()


def test_disable_env_short_circuits(reports_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_CLEAN_DISABLE", "1")
    target = reports_root / "logs" / "old.log"
    target.write_text("x", encoding="utf-8")
    _age(target, 48)

    result = clean_old_reports(reports_root, expire_hours=24)

    assert target.exists()
    assert result.disabled is True
    assert result.deleted == 0


def test_refuses_path_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """项目外的路径必须被拒绝，且不抛异常（清理失败不能盖掉测试本身的结果）。"""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr(settings, "PROJECT_ROOT", project)

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    victim = outside / "important.txt"
    victim.write_text("keep me", encoding="utf-8")
    _age(victim, 48)

    result = clean_old_reports(outside, expire_hours=24)

    assert victim.exists(), "项目之外的目录一律不碰"
    assert result.deleted == 0


def test_missing_dir_is_noop(reports_root: Path) -> None:
    result = clean_old_reports(reports_root / "not-there", expire_hours=24)
    assert isinstance(result, CleanResult)
    assert result.deleted == 0


def test_expire_hours_can_be_overridden_by_env(
    reports_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("E2E_CLEAN_HOURS", "1")
    target = reports_root / "logs" / "two_hours.log"
    target.write_text("x", encoding="utf-8")
    _age(target, 2)

    result = clean_old_reports(reports_root)      # 不传 expire_hours，走环境变量

    assert not target.exists()
    assert result.deleted == 1


# ---------------------------------------------------------------- 脱敏

def test_redact_masks_session_ticket() -> None:
    text = "Cookie: cticket=ABC123DEF; login_uid=99"
    out = redact(text)
    assert "ABC123DEF" not in out
    # 命中 Cookie 规则时整段值都被抹掉 —— 比逐字段打码更保险，
    # 所以断言的是「凭据没漏」，不是某个具体替换形态。
    assert "login_uid=99" not in out
    assert "***" in out


def test_redact_masks_bare_cticket() -> None:
    """单独出现的 cticket 也要打码（很多时候它不在 Cookie 头里，而在 URL 里）。"""
    out = redact("https://passport.ctrip.com/x?cticket=SECRET123&r=1")
    assert "SECRET123" not in out
    assert "cticket=***" in out


def test_redact_masks_phone_and_bearer() -> None:
    out = redact("手机 13800138000 token Bearer abc.def.ghi")
    assert "13800138000" not in out
    assert "abc.def.ghi" not in out


def test_redact_keeps_ordinary_text() -> None:
    text = "2026-11-01 19:05 用例 test_chain_flights 通过"
    assert redact(text) == text


def test_log_file_name_is_stable_within_process() -> None:
    from utils.logger import log_file_path

    first = log_file_path()
    second = log_file_path()
    assert first == second, "同一进程内日志文件名必须固定，否则每次调用都新开文件"
    assert first.name.startswith("run_")
    assert datetime.now().strftime("%Y%m%d") in first.name
