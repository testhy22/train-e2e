"""候选池：自动发现的「正常值域新样本」的唯一落盘处。

**定位（2026-11-02 定案）**：候选池是旁路观测产物，不参与任何断言，
不改 passed/failed、不改 XFAIL 语义。主用例红不红与这里无关；
这里坏一行，主用例也必须照样绿（discover.collect 的兜底保证）。

文件布局：

    reports/candidates/candidates.jsonl        主文件，append-only
    reports/candidates/archive/candidates-YYYYMM.jsonl   按月归档
    reports/candidates/summary.json            最近一次汇总（可重生成）

每条记录（首字段恒为 v，schema 版本）：

    {"v": 1, "value": ..., "site": ..., "dimension": ...,
     "source_case": ..., "source_selector": ..., "url": <脱敏后>,
     "collected_at": ISO8601, "round_id": ..., "est_cost_s": ...,
     "status": "candidate|approved|rejected|promoted",
     "batch_id": ..., "promoted_to": ...}

生命周期与清理策略（2026-11-02 用户裁决）：

- 主文件按月滚动：归档动作把「上月」整月条目移入 archive/；
- ``status`` 为 promoted / rejected 且超过 30 天的条目压缩移出主文件
  （30 天 = 覆盖一个完整月度回归周期，审批价值已耗尽，档案里可查）；
- ``status == "candidate"``（待审）**永不清理**，无论多久；
- 待审超过 14 天的条目在汇总里**高亮**（STALE_DAYS），防止池子堆积无人看。

本模块不做网络、不开浏览器；只做 jsonl 的增查与整理。
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1

#: 待审条目超过这个天数，在汇总里高亮（不清理，只提醒）。
STALE_DAYS = 14
#: promoted / rejected 条目在主文件里的最长保留天数，超过移入归档。
COMPRESS_DAYS = 30

REPO_ROOT = Path(__file__).resolve().parents[1]


def pool_dir() -> Path:
    """候选池根目录；可用环境变量 CANDIDATE_DIR 覆盖（测试注入用）。"""
    override = os.getenv("CANDIDATE_DIR", "").strip()
    if override:
        return Path(override)
    return REPO_ROOT / "reports" / "candidates"


def main_file() -> Path:
    return pool_dir() / "candidates.jsonl"


def archive_dir() -> Path:
    return pool_dir() / "archive"


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def append(entries: list[dict]) -> int:
    """把采集条目追加进主文件。返回实际写入条数。

    append-only：绝不改写已有行；重复值由调用方（discover 的去重闸）保证。
    """
    if not entries:
        return 0
    path = main_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            record = {"v": SCHEMA_VERSION, **entry}
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")
            written += 1
    return written


def load_all() -> list[dict]:
    """读全部条目（主文件 + 归档）。坏行跳过不抛 —— 读操作也不许影响任何主流程。"""
    records: list[dict] = []
    files = [main_file()]
    if archive_dir().is_dir():
        files.extend(sorted(archive_dir().glob("candidates-*.jsonl")))
    for path in files:
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 坏行归档为观测问题，不中断
        except OSError:
            continue
    return records


def existing_keys() -> set[tuple[str, str, str]]:
    """已存在的 (site, dimension, value) 三元组 —— discover 去重闸用。"""
    return {
        (r.get("site", ""), r.get("dimension", ""), r.get("value", ""))
        for r in load_all()
    }


def summarize() -> dict:
    """汇总：待审清单（含 >14 天高亮）、各状态计数、每站点×维度分布。"""
    records = [r for r in load_all() if r.get("status") != "promoted" or _in_main(r)]
    now = _now()
    pending, stale = [], []
    counts = {"candidate": 0, "approved": 0, "rejected": 0, "promoted": 0}
    by_site_dim: dict[str, int] = {}
    for r in records:
        status = r.get("status", "candidate")
        counts[status] = counts.get(status, 0) + 1
        if status == "candidate":
            pending.append(r)
            collected = _parse_ts(r.get("collected_at"))
            if collected and (now - collected).days >= STALE_DAYS:
                stale.append(r)
        key = f"{r.get('site')}/{r.get('dimension')}"
        by_site_dim[key] = by_site_dim.get(key, 0) + 1

    summary = {
        "generated_at": now.isoformat(timespec="seconds"),
        "counts": counts,
        "by_site_dimension": dict(sorted(by_site_dim.items())),
        "pending_count": len(pending),
        "stale_pending_over_days": STALE_DAYS,
        "stale_pending": [
            {k: r.get(k) for k in ("value", "site", "dimension", "source_case", "collected_at")}
            for r in stale
        ],
        "pending": pending,
    }
    path = pool_dir() / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _in_main(record: dict) -> bool:
    """promoted 条目转正后会带 promoted_to；主文件里的旧副本归档时会被压缩。
    这里只做展示过滤：promoted 且已标 promoted_to 的不进分布统计。"""
    return not record.get("promoted_to")


def archive_and_compress(force_month: str | None = None) -> dict:
    """按月归档 + 压缩超期条目。

    - 上月（或 force_month 指定的 YYYYMM 之前）的条目移入 archive/candidates-<月>.jsonl；
    - promoted / rejected 超过 COMPRESS_DAYS 天的直接留在归档（主文件不再保留副本）；
    - candidate 状态永不清理，永远留在主文件。
    """
    path = main_file()
    if not path.is_file():
        return {"archived": 0, "compressed": 0}

    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    now = _now()
    keep_main: list[dict] = []
    by_month: dict[str, list[dict]] = {}
    archived = compressed = 0

    for r in records:
        ts = _parse_ts(r.get("collected_at"))
        status = r.get("status", "candidate")
        month_key = ts.strftime("%Y%m") if ts else force_month or now.strftime("%Y%m")
        # 上月及更早 → 归档
        if ts and ts.strftime("%Y%m") < now.strftime("%Y%m"):
            by_month.setdefault(month_key, []).append(r)
            archived += 1
            continue
        # 本月内：promoted/rejected 超期 → 只留在归档，不回主文件
        if status in ("promoted", "rejected") and ts and (now - ts).days >= COMPRESS_DAYS:
            by_month.setdefault(month_key, []).append(r)
            compressed += 1
            continue
        keep_main.append(r)  # 含 candidate（永不清理）

    archive_dir().mkdir(parents=True, exist_ok=True)
    for month, items in by_month.items():
        target = archive_dir() / f"candidates-{month}.jsonl"
        with target.open("a", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep_main),
        encoding="utf-8",
    )
    shutil.move(str(tmp), str(path))
    return {"archived": archived, "compressed": compressed}


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def mark(records: list[dict], status: str, *, batch_id: str = "",
         promoted_to: str = "") -> int:
    """把主文件中匹配 (site, dimension, value) 的条目改为目标状态。

    append-only 原则的例外：状态机推进是候选池的本职，重写时保持行序不变。
    返回实际修改条数。
    """
    path = main_file()
    if not path.is_file():
        return 0
    keys = {(r.get("site"), r.get("dimension"), r.get("value")) for r in records}
    changed = 0
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            lines.append(line)
            continue
        if (r.get("site"), r.get("dimension"), r.get("value")) in keys and (
            r.get("status") == "candidate"
        ):
            r["status"] = status
            if batch_id:
                r["batch_id"] = batch_id
            if promoted_to:
                r["promoted_to"] = promoted_to
            changed += 1
        lines.append(json.dumps(r, ensure_ascii=False))
    if changed:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        shutil.move(str(tmp), str(path))
    return changed
