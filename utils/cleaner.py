"""`reports/` 旧产物清理：按「最后修改时间」删过期文件，防止磁盘被日志/截图/trace 打满。

与 `utils/logger.py` 的轮转是**互补**关系，不是重复：
  · 轮转管「单次运行别写爆」（10MB × 5 份封顶）；
  · 本模块管「跨运行别堆爆」（默认 24 小时前的产物删掉）。

刻意守住的三条：

1. **只清 `reports/`，且必须解析在项目根之内**。路径写错（比如传了 `""` 或 `../`）
   时宁可拒绝执行 —— 清理器最危险的失败不是「没删干净」，是「删到了别的地方」。
2. **按 mtime 而不是 ctime**。Linux 的 ctime 是 inode 变更时间，语义对不上；
   mtime 才是「这个文件最近还在写吗」。
3. **`PermissionError` 一律跳过并计数**。Windows 上文件被 Trace Viewer / 编辑器
   打开时删不掉（WinError 32），这属于正常情况，不该让整个测试会话崩掉。

环境变量：
  `E2E_CLEAN_HOURS=48`  改过期阈值（默认 24）
  `E2E_CLEAN_DISABLE=1` 完全跳过（需要留证据排查时用）
  `E2E_CLEAN_DRY_RUN=1` 只统计不删
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from config import settings
from utils.logger import get_logger

DEFAULT_EXPIRE_HOURS = 24.0
# 保留占位文件，否则空目录会被顺手删掉、Git 里的结构就丢了
KEEP_NAMES = {".gitkeep", ".keep"}
# 即使清空了也**不删**的一级目录：它们是 settings.ensure_dirs() 约定好的证据出口，
# 空着也是结构的一部分。删掉之后 pytest-playwright 的 --output / 截图目录
# 会按自己的逻辑重建，两边对不上时排查成本远高于省下的一个空目录。
KEEP_DIRS = {"logs", "screenshots", "trace", "html"}


@dataclass
class CleanResult:
    deleted: int = 0
    freed_bytes: int = 0
    skipped: int = 0
    scanned: int = 0
    removed_dirs: int = 0
    dry_run: bool = False
    disabled: bool = False

    @property
    def freed_mb(self) -> float:
        return self.freed_bytes / (1024 * 1024)

    def describe(self) -> str:
        if self.disabled:
            return "清理器已禁用（E2E_CLEAN_DISABLE=1），未做任何删除"
        if self.dry_run:
            return (
                f"演练模式：扫描 {self.scanned} 个文件，"
                f"其中 {self.deleted} 个超过阈值（{self.freed_mb:.2f} MB），**未删除**"
            )
        if self.deleted == 0:
            return f"扫描 {self.scanned} 个文件，没有超过阈值的旧产物，无需清理"
        return (
            f"删除 {self.deleted} 个旧文件，释放 {self.freed_mb:.2f} MB"
            f"（跳过被占用 {self.skipped} 个，清理空目录 {self.removed_dirs} 个）"
        )


def _guard_target(target: Path) -> None:
    """目标目录必须存在、是目录、且解析后仍在项目根之内。"""
    project_root = settings.PROJECT_ROOT.resolve()
    resolved = target.resolve()
    if resolved != project_root and project_root not in resolved.parents:
        raise ValueError(f"拒绝清理项目之外的路径：{resolved}（项目根 {project_root}）")


def clean_old_reports(
    target_dir: str | Path | None = None,
    expire_hours: float | None = None,
) -> CleanResult:
    """删除 `target_dir`（默认 `reports/`）下 mtime 超过 `expire_hours` 的文件。

    返回统计结果，不抛异常 —— 清理失败绝不能盖掉「测试本身跑了什么」这个主角。
    """
    log = get_logger()
    result = CleanResult()

    if os.getenv("E2E_CLEAN_DISABLE") == "1":
        result.disabled = True
        log.info("[cleaner] %s", result.describe())
        return result

    hours = expire_hours if expire_hours is not None else float(
        os.getenv("E2E_CLEAN_HOURS", DEFAULT_EXPIRE_HOURS)
    )
    result.dry_run = os.getenv("E2E_CLEAN_DRY_RUN") == "1"
    target = Path(target_dir) if target_dir is not None else settings.REPORTS_DIR

    if not target.exists():
        log.info("[cleaner] %s 不存在，跳过", target)
        return result
    try:
        _guard_target(target)
    except ValueError as exc:
        log.error("[cleaner] %s", exc)
        return result

    cutoff = time.time() - hours * 3600
    log.info("[cleaner] 开始清理 %s（阈值 %.0f 小时）", target, hours)

    for path in target.rglob("*"):
        if not path.is_file() or path.name in KEEP_NAMES:
            continue
        result.scanned += 1
        try:
            stat = path.stat()
        except OSError:
            result.skipped += 1
            continue
        if stat.st_mtime >= cutoff:
            continue
        result.deleted += 1
        result.freed_bytes += stat.st_size
        if result.dry_run:
            continue
        try:
            path.unlink()
        except PermissionError:
            # Windows：文件被 Trace Viewer / 编辑器占用（WinError 32）。跳过就好。
            result.deleted -= 1
            result.freed_bytes -= stat.st_size
            result.skipped += 1
            log.warning("[cleaner] 跳过被占用的文件：%s", path)
        except OSError as exc:
            result.deleted -= 1
            result.freed_bytes -= stat.st_size
            result.skipped += 1
            log.warning("[cleaner] 删除失败 %s：%s", path, exc)

    if not result.dry_run:
        # 自底向上删空目录（`rglob` 已按层级排好，这里单独走一遍更直观）
        for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if not path.is_dir():
                continue
            if path.parent == target and path.name in KEEP_DIRS:
                continue
            try:
                if not any(path.iterdir()):
                    path.rmdir()
                    result.removed_dirs += 1
            except OSError:
                continue

    log.info("[cleaner] %s", result.describe())
    return result
