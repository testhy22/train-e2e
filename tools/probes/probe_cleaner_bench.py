"""清理器吞吐基准（**不碰 reports/**）。

用途：把「全量 pytest 卡在 `pytest_sessionstart` 的清理器上」这件事量出具体速率，
用于技术债台账与 Issue 的证据。所有文件都在自建的临时目录里创建并删除，
`reports/` 全程只读不写。

跑法：`PYTHONIOENCODING=utf-8 python tools/probes/probe_cleaner_bench.py`

为什么不能直接在 reports/ 上测：那里的产物是可再生的，但按用户的红线——
生命周期类测试不得在生产目录做破坏性操作，必须在临时目录里验。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

# 探针以 `python tools/probes/xxx.py` 直接跑时，sys.path[0] 是 tools/probes，
# 项目根不在路径上，`from utils...` 会 ModuleNotFoundError。
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.cleaner import clean_old_reports  # noqa: E402

# 临时目录放在项目根内：cleaner._guard_target 要求目标解析后仍在项目根之内。
BENCH = ROOT / "_tmp_cleaner_bench"
N_FILES = int(os.getenv("BENCH_N", "2000"))
OLD_HOURS = 48  # 比默认 24h 阈值更旧，确保一定被判定为过期
SUBDIRS = 20


def build(n: int) -> None:
    """造 n 个「48 小时前修改」的小文件，分散在 20 个子目录里（近似 allure/screenshots 形态）。"""
    if BENCH.exists():
        shutil.rmtree(BENCH)
    old_ts = time.time() - OLD_HOURS * 3600
    for i in range(SUBDIRS):
        (BENCH / f"sub{i:02d}").mkdir(parents=True)
    for i in range(n):
        path = BENCH / f"sub{i % SUBDIRS:02d}" / f"artifact_{i:05d}.txt"
        path.write_text("x" * 256, encoding="utf-8")
        os.utime(path, (old_ts, old_ts))


def main() -> None:
    print(f"临时目录：{BENCH}（项目根内、非 reports/）")
    build(N_FILES)
    print(f"已造 {N_FILES} 个过期文件，单文件 256B")

    # ① 只扫描不删除：量 rglob + stat 的成本
    os.environ["E2E_CLEAN_DRY_RUN"] = "1"
    os.environ["E2E_CLEAN_HOURS"] = "24"
    t0 = time.perf_counter()
    dry = clean_old_reports(BENCH)
    scan_s = time.perf_counter() - t0
    print(f"[dry-run] scanned={dry.scanned} would_delete={dry.deleted} 耗时={scan_s:.2f}s")

    # ② 真删：量 unlink 的成本（同一批文件）
    os.environ["E2E_CLEAN_DRY_RUN"] = "0"
    t0 = time.perf_counter()
    real = clean_old_reports(BENCH)
    del_s = time.perf_counter() - t0
    left = sum(1 for p in BENCH.rglob("*") if p.is_file())
    rate = real.deleted / del_s if del_s else 0.0
    print(
        f"[real]    deleted={real.deleted} 耗时={del_s:.2f}s "
        f"速率={rate:.0f} 文件/秒 剩余={left}"
    )
    print(f"[拆分]   扫描 {scan_s / N_FILES * 1000:.2f} ms/文件｜删除 {del_s / max(real.deleted, 1) * 1000:.2f} ms/文件")
    print(f"[外推]   2383 个过期文件（2026-11-02 实测 reports/ 的待删量）≈ {2383 / rate:.0f}s" if rate else "[外推] 无法计算")

    os.environ.pop("E2E_CLEAN_DRY_RUN", None)
    os.environ.pop("E2E_CLEAN_HOURS", None)
    shutil.rmtree(BENCH, ignore_errors=True)
    print(f"临时目录已清理：{not BENCH.exists()}")


if __name__ == "__main__":
    main()
