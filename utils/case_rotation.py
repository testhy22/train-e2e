"""每次运行从全量数据里挑「一小撮」用例：默认**每个功能 1 条**，按「轮换 + 随机」。

用户 2026-11-03 裁决：数据准备多条，每次随机挑 1 条跑 —— 省时间，同时靠多次运行覆盖全量。

为什么不是纯随机（`random.choice` 有放回）：9 条数据跑 9 次，期望只覆盖约
`9 × (1 - (8/9)^9) ≈ 5.9` 条 —— 「随机」并不等于「覆盖更多」，重复抽到同一条是常态。
所以取的是**无放回轮换**：把全集洗成一个 cycle，一个 cycle 内不重复，跑满才开下一轮
⇒ 9 条数据跑 9 次必定全部见到；单次仍然只花 1 条的时间。

为什么 cycle 内还要洗牌（而不是按文件顺序取）：数据里的日期是 `today + offset`
算出来的。顺序固定 ⇒ 某些 offset 永远落在同一星期几、同一个提前量上，
等于「跑了很多次但从没在别的工作日验证过」。洗牌让工作日分布也铺开。

可复现性**不用 seed**：seed 只决定"在当前轮换状态下这次挑哪条"，而轮换状态每次前进，
所以照抄 seed 也复现不了。要复现某条就钉 id —— `E2E_CASE_ID=rt-gz-cs-near`，
比记 seed 可靠一个数量级。

环境变量
  `E2E_CASE_QUOTA=3`    每功能本次挑几条（默认 1）
  `E2E_FULL=1`          全量回归，忽略配额（同时把该轨道的一轮标记为跑完）
  `E2E_CASE_ID=a,b`     钉死指定 id（按轨道各自过滤；不属于本轨道的 id 自动忽略）
  `E2E_SEED=123`        固定本次洗牌的随机源
  `E2E_ROTATION_STATE=` 轮换状态文件路径（单测用；默认 `<项目根>/.e2e-state/case_rotation.json`）
"""

from __future__ import annotations

import json
import os
import random
import secrets
from pathlib import Path

from config import settings

DEFAULT_QUOTA = 1
QUOTA_ENV = "E2E_CASE_QUOTA"
FULL_ENV = "E2E_FULL"
PIN_ENV = "E2E_CASE_ID"
SEED_ENV = "E2E_SEED"
STATE_ENV = "E2E_ROTATION_STATE"
STATE_VERSION = 1
DEFAULT_STATE_FILE = Path(".e2e-state") / "case_rotation.json"

# 每条轨道最近一次挑选的说明，供报告引用（进程内，不需要落盘）
_LAST: dict[str, str] = {}


# ---------------------------------------------------------------- 开关

def full_run() -> bool:
    """本次是否为「全量用例回归」。默认 False。"""
    return os.getenv(FULL_ENV, "").strip() == "1"


def quota() -> int:
    """每功能本次挑几条。非法值退回默认 1，不抛 —— 参数化期间抛异常等于整轮不跑。"""
    raw = os.getenv(QUOTA_ENV, "").strip()
    if not raw:
        return DEFAULT_QUOTA
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_QUOTA


def pinned_ids() -> list[str]:
    """`E2E_CASE_ID` 里点名的 id（逗号分隔）。空列表表示没钉。"""
    raw = os.getenv(PIN_ENV, "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def state_path() -> Path:
    raw = os.getenv(STATE_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(settings.PROJECT_ROOT) / DEFAULT_STATE_FILE


# ---------------------------------------------------------------- 状态读写

def _blank() -> dict:
    return {"version": STATE_VERSION, "tracks": {}}


def _load() -> dict:
    """读状态。损坏/版本不符/不存在一律当"从没跑过"，不抛 —— 轮换退化为随机比崩掉好。"""
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return _blank()
    if not isinstance(raw.get("tracks"), dict):
        raw["tracks"] = {}
    return raw


def _save(state: dict) -> None:
    """原子写（临时文件 + os.replace）。写不进去只丢轮换记忆，不该让测试红。"""
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        pass


def _track(state: dict, kind: str) -> dict:
    """取某轨道的状态，顺手清掉历史里失效的 id（数据被删改过时不至于卡死）。"""
    tracks = state.setdefault("tracks", {})
    item = tracks.get(kind)
    if not isinstance(item, dict):
        item = {}
        tracks[kind] = item
    used = item.get("used")
    item["used"] = [u for u in used if isinstance(u, str)] if isinstance(used, list) else []
    return item


def _seed() -> int:
    raw = os.getenv(SEED_ENV, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return secrets.randbits(32)


# ---------------------------------------------------------------- 主入口

def pick(kind: str, cases: list) -> list:
    """按「钉 id > 全量 > 轮换 + 随机」的优先级给出本次运行集。

    `kind` 只是状态里的键（`train_smoke` / `round_trip` / `transfer`），
    不同轨道各记各的进度，互不干扰。
    """
    cases = list(cases)
    if not cases:
        _LAST[kind] = f"{kind}: 数据为空"
        return cases

    ids = [getattr(case, "id", None) for case in cases]
    by_id = {cid: case for cid, case in zip(ids, cases)}
    state = _load()
    track = _track(state, kind)
    used = [u for u in track["used"] if u in by_id]

    pinned = [cid for cid in pinned_ids() if cid in by_id]
    if pinned:
        chosen = pinned
        track["used"] = used + [cid for cid in chosen if cid not in used]
        _LAST[kind] = f"{kind}: 钉死（{PIN_ENV}）{len(chosen)}/{len(ids)} 条：{', '.join(chosen)}"
    elif full_run():
        chosen = list(ids)
        # 全量跑过一轮 ⇒ 该轨道的一轮清空，下次单挑从头开始（避免"刚跑完全量又只跑尾巴"）
        track["used"] = []
        _LAST[kind] = f"{kind}: 全量（{FULL_ENV}=1）共 {len(ids)} 条"
    else:
        want = min(quota(), len(ids))
        pool = [cid for cid in ids if cid not in used]
        if len(pool) < want:          # 本轮剩余不够 ⇒ 洗新一轮
            used = []
            pool = list(ids)
        seed = _seed()
        random.Random(seed).shuffle(pool)
        chosen = pool[:want]
        track["used"] = used + chosen
        track["seed"] = seed
        _LAST[kind] = (
            f"{kind}: 轮换 {len(track['used'])}/{len(ids)} 条"
            f"（本轮 seed={seed}，累计运行 {int(track.get('runs', 0)) + 1} 次）"
        )

    track["runs"] = int(track.get("runs", 0)) + 1
    track["last"] = list(chosen)
    _save(state)
    return [by_id[cid] for cid in chosen]


def note(kind: str) -> str:
    """本次挑选的一句说明，写进用例报告（参数 id 只说"跑了哪条"，不说"为什么是它"）。"""
    return _LAST.get(kind, f"{kind}: 未经 pick()（可能是直接调了 loader）")


def summary() -> str:
    """所有轨道的一句话摘要，供会话级报告使用。"""
    return " | ".join(note(kind) for kind in sorted(_LAST)) or "（本次没有挑选记录）"
