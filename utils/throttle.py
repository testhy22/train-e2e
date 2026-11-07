"""全局节流：两次「对外动作」之间强制冷却，从**频率**维度限制访问强度。

为什么单独一个模块，而不是散在 conftest / page object 里：
  · 判定只有一处 —— 「距上次动作不足 N 秒就补足」，改一个数字全仓生效；
  · 状态是**进程级**的，不挂在 fixture 上。e2e 轨道一轮用同一个 page 连跑三个模块，
    fixture 级的锁会在 page 重建时丢掉历史，等于没锁。

三条边界（与 utils/humanize.py 的四条边界同源，别违背）：

1. **它不是反检测手段，是「访问强度上限」。** 实测依据（K-05 系列）：无头被 432 是
   http 层判定，改 UA / 换二进制都不改变结果；本模块只是把「单位时间内打对方几次」
   压到人类尺度，**不承诺降低被拦概率**。真正解封的是有头模式。
2. **不替代等待条件。** `wait_action()` 只发生在「一次对外动作之前」，不参与等页面加载；
   任何 wait_for_selector / expect 都不许换成它。
3. **可整体关闭。** `E2E_THROTTLE=0` 关闭（CI 想跑快时用）；默认开启，
   因为「快」在这里是风险而不是优点。

两个时间参数（均可由环境变量覆盖，秒，允许小数）：
  `E2E_MIN_ACTION_INTERVAL_S`  同一会话内两次导航 / 提交之间的最小间隔，默认 15
  `E2E_CASE_GAP_S`             两条浏览器用例之间的最小间隔，默认 30

第三个维度是**跨运行**的配额（2026-11-07 加，见文末「每小时配额」）：
  `E2E_HOURLY_QUOTA`           每小时最多几次搜索，默认 5；0 = 不限
  `E2E_QUOTA_WINDOW_S`         配额窗口，默认 3600
  `E2E_QUOTA_FILE`             配额状态文件，默认 `.e2e-state/quota.json`

为什么动作间隔 15s、用例间隔 30s：不是拟合出来的，是「人类搜索一个东西 → 看结果 →
再搜下一个」的量级。同一会话里连着点三次搜索（现状 3 条 e2e 用例）属于典型批量特征，
所以用例之间给到 30s，模块内部的动作之间给到 15s。

与 HUMAN（utils/humanize.py）的关系：
  HUMAN 管**动作内部**的节奏（移动几步、字符间隔），本模块管**动作之间**的空档。
  两者叠加时以本模块的下限为准 —— 它是「不低于」，不是「替换」。
"""

from __future__ import annotations

import os
import threading
import time

_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}

#: 动作间隔与用例间隔的默认值（秒）。读环境变量时实时解析，不在 import 期固化 ——
#: 单测要 monkeypatch 环境变量，固化会让它测的是上一次的值。
DEFAULT_MIN_ACTION_S = 15.0
DEFAULT_CASE_GAP_S = 30.0

_LOCK = threading.Lock()
_last_action: float = 0.0
_last_case_end: float = 0.0


def _env_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def enabled() -> bool:
    """`E2E_THROTTLE=0` 关闭。非法值按「开启」处理 —— 宁可慢，不可裸奔。"""
    raw = os.getenv("E2E_THROTTLE", "").strip().lower()
    if raw in _OFF:
        return False
    return True


def min_action_s() -> float:
    return _env_seconds("E2E_MIN_ACTION_INTERVAL_S", DEFAULT_MIN_ACTION_S)


def case_gap_s() -> float:
    return _env_seconds("E2E_CASE_GAP_S", DEFAULT_CASE_GAP_S)


def _sleep_until(target: float) -> float:
    """睡到 `target`（单调钟秒）。返回实际睡了多久，供日志引用。"""
    gap = target - time.monotonic()
    if gap <= 0:
        return 0.0
    time.sleep(gap)
    return gap


def _log_wait(kind: str, slept: float) -> None:
    """只有真的等了才写日志（<1s 的补足不值得刷屏）。惰性导入避免循环依赖。"""
    if slept < 1.0:
        return
    try:
        from utils.logger import logger_info

        logger_info(f"[throttle] {kind} 冷却补足 {slept:.1f}s")
    except Exception:  # noqa: BLE001 - 日志不该影响主流程
        pass


def wait_action() -> float:
    """在**一次对外动作**（导航 / 提交搜索）之前调用，补足冷却。

    返回实际等待秒数（0 表示无需等待），调用方可据此写日志。
    进程内首调用不等待：首条用例前面的空白没有「上一个请求」可比。
    """
    global _last_action
    if not enabled():
        return 0.0
    interval = min_action_s()
    with _LOCK:
        slept = _sleep_until(_last_action + interval)
        _last_action = time.monotonic()
    _log_wait("动作间隔", slept)
    return slept


def wait_case_gap() -> float:
    """在**一条浏览器用例开始前**调用，补齐用例间隔。首条用例不等待。"""
    global _last_case_end
    if not enabled():
        return 0.0
    gap = case_gap_s()
    if gap <= 0:
        return 0.0
    with _LOCK:
        slept = _sleep_until(_last_case_end + gap)
    _log_wait("用例间隔", slept)
    return slept


def mark_case_end() -> None:
    """用例结束时调用（fixture 的 teardown），作为下一条用例的间隔起点。"""
    global _last_case_end
    if not enabled():
        return
    with _LOCK:
        _last_case_end = time.monotonic()


def reset() -> None:
    """清空进程内状态。只给单测用 —— 生产路径不需要「忘掉上次请求」。"""
    global _last_action, _last_case_end
    with _LOCK:
        _last_action = 0.0
        _last_case_end = 0.0


# ---------------------------------------------------------------- 每小时配额
#
# 为什么需要它（2026-11-07 用户要求）：
#   动作间隔 15s / 用例间隔 30s 只能压住**一次运行内部**的密度。真正让访问变成
#   「批量」的是**跨运行**的重复 —— 比如 CI 每 10 分钟跑一次，一轮 3 条搜索，
#   一小时就是 18 次。间隔再大也救不了，必须在「每小时总共几次」这一层设上限。
#
# 为什么落盘而不是只在内存里数：
#   每次 `pytest` 都是新进程，内存计数在进程结束时就没了 —— 而配额要管的恰恰是
#   「上一个进程刚跑过」。所以记在 `.e2e-state/quota.json`（与轮换状态同目录，
#   已在 .gitignore 里）。

DEFAULT_HOURLY_QUOTA = 5
DEFAULT_WINDOW_S = 3600.0


class QuotaExhausted(RuntimeError):
    """本时间窗内的搜索次数已用尽。调用方决定是等还是跳过。"""

    def __init__(self, used: int, limit: int, wait_s: float) -> None:
        self.used = used
        self.limit = limit
        self.wait_s = wait_s
        super().__init__(
            f"本小时已发 {used} 次搜索（上限 {limit} 次），"
            f"最近一次在 {wait_s:.0f}s 前；要等配额滑出请设 E2E_QUOTA_WAIT=1"
        )


def hourly_quota() -> int:
    """`E2E_HOURLY_QUOTA`，默认 5；**设 0 表示不限**（临时排障用，日志会写明）。"""
    raw = os.getenv("E2E_HOURLY_QUOTA", "").strip()
    if not raw:
        return DEFAULT_HOURLY_QUOTA
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_HOURLY_QUOTA


def quota_window_s() -> float:
    return _env_seconds("E2E_QUOTA_WINDOW_S", DEFAULT_WINDOW_S)


def quota_file() -> "os.PathLike[str] | str":
    """配额状态文件。默认 `.e2e-state/quota.json`（与 `case_rotation` 同目录）。"""
    raw = os.getenv("E2E_QUOTA_FILE", "").strip()
    if raw:
        return raw
    from pathlib import Path

    return Path(".e2e-state") / "quota.json"


def _read_stamps(path, now: float, window: float) -> list[float]:
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        stamps = [float(x) for x in data.get("searches", [])]
    except (ValueError, OSError, AttributeError):
        # 文件坏了按「无历史」处理：宁可多放一次，也不要因为一个坏文件让整轮跑不动。
        return []
    return [s for s in stamps if 0 <= now - s < window]


def _write_stamps(path, stamps: list[float]) -> None:
    import json
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"searches": stamps}), encoding="utf-8")
    tmp.replace(p)          # 原子替换：中途被打断也不会留下半个 JSON


def reserve_search() -> int:
    """登记**一次**搜索请求并返回本窗已用次数。超额抛 `QuotaExhausted`。

    调用点在 conftest 的用例级 fixture：每条浏览器用例 = 一次搜索请求
    （三个模块各自只发一次提交）。放在这里而不是各 Page Object 的 submit 里，
    是因为兜底范围更大 —— 将来新增轨道只要带 `ui/hotels/auth/e2e` 任一标记就自动计入。
    """
    if not enabled():
        return 0
    limit = hourly_quota()
    if limit <= 0:
        return 0
    window = quota_window_s()
    path = quota_file()
    now = time.time()
    with _LOCK:
        stamps = _read_stamps(path, now, window)
        if len(stamps) >= limit:
            oldest = min(stamps)
            raise QuotaExhausted(len(stamps), limit, now - oldest)
        stamps.append(now)
        _write_stamps(path, stamps)
    try:
        from utils.logger import logger_info

        logger_info(f"[throttle] 配额 {len(stamps)}/{limit}（窗口 {window / 60:.0f} 分钟）")
    except Exception:  # noqa: BLE001 - 日志不该影响主流程
        pass
    return len(stamps)
