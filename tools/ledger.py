"""ledger 记录的结构、校验与幂等键 —— 门禁写入与 CI 读回校验共用这一份。

为什么单拎一个模块：写入端（tools/ledger_append.py）和校验端（CI 推完读回）
必须是同一套规则，否则「写进去的」和「校验过的」可以不是同一个格式。

字段语义的唯一说明在 ops/metrics 分支的 ops/README.md；
判定规则（gate 取值含义等）的唯一说明在 docs/BLOCKED-GATE.md。本文件只做机械校验。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# 当前产物版本。缺字段 = 0（历史种子行），旧行不改写是 append-only 的硬约束。
SCHEMA_VERSION = 1

VERDICTS = frozenset({"GREEN", "YELLOW", "RED"})
SCOPES = frozenset({"ui", "unit"})
SOURCES = frozenset({"ci", "local"})
GATE_CODES = frozenset({"G0", "G1", "G2", "G3", "G4", "G5", "G6"})

INT_FIELDS = ("total", "counted", "blocked", "effective_pass", "failed", "known_limit")
# v1 必填（v0 种子行只有前 8 个，缺 counted/known_limit/provisional，属于历史形态）
V1_FIELDS = (
    "schema_version",
    "ts",
    "run",
    "source",
    "scope",
    "total",
    "counted",
    "blocked",
    "effective_pass",
    "failed",
    "known_limit",
    "ratio",
    "verdict",
    "gate",
    "top_signal",
)
V0_FIELDS = ("ts", "run", "source", "scope", "total", "blocked", "effective_pass", "failed", "ratio", "verdict", "gate")

RATIO_TOL = 1e-9


class SchemaError(ValueError):
    """记录不符合约定的结构。调用方必须当成错误，不允许跳过这一行继续。"""


def normalize_ts(value: str) -> str:
    """把 ISO 8601 时间截到秒并保留时区 —— 幂等键用 ts，微秒抖动会造成重复行。"""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SchemaError(f"ts 不是合法的 ISO 8601：{value!r}（{exc}）") from exc
    if parsed.tzinfo is None:
        raise SchemaError(f"ts 缺少时区：{value!r}；本地时间放进 ledger 后无法跨机器比较")
    return parsed.replace(microsecond=0).isoformat()


def record_key(record: dict[str, Any]) -> str:
    """幂等键：`ts|scope`。

    同一份 junit 重跑（workflow 重试 / 本地重放）会得到相同的 ts（取自 junit 的
    testsuite timestamp，不是墙上时钟）与相同的 scope，因此第二次写入被识别为重复。
    """
    return f"{record.get('ts')}|{record.get('scope')}"


def validate_record(record: Any, *, line_no: int | None = None) -> list[str]:
    """校验一条记录，返回警告列表；结构错误抛 SchemaError。

    可修复的小问题给警告（例如 xfail 用了 known-limit 前缀），
    不可解释的问题一律报错 —— 「看不懂就当通过」是账本最危险的退化方式。
    """
    where = f"第 {line_no} 行" if line_no is not None else "记录"
    if not isinstance(record, dict):
        raise SchemaError(f"{where}不是 JSON 对象")

    warnings: list[str] = []

    raw_version = record.get("schema_version", 0)
    if not isinstance(raw_version, int) or isinstance(raw_version, bool):
        raise SchemaError(f"{where} schema_version 必须是整数，实际 {raw_version!r}")
    if raw_version > SCHEMA_VERSION:
        raise SchemaError(
            f"{where} schema_version={raw_version} 比本工具支持的 {SCHEMA_VERSION} 新；"
            "拒绝猜测新字段语义，请先升级工具"
        )
    required = V0_FIELDS if raw_version == 0 else V1_FIELDS
    missing = [f for f in required if f not in record]
    if missing:
        raise SchemaError(f"{where} 缺字段：{', '.join(missing)}")

    if raw_version == 0:
        warnings.append(f"{where} 是 v0 历史记录（无 schema_version），只按旧字段校验")

    try:
        record["ts"] = normalize_ts(str(record["ts"]))
    except SchemaError as exc:
        raise SchemaError(f"{where} {exc}") from exc

    if record["scope"] not in SCOPES:
        raise SchemaError(f"{where} scope 只能是 {sorted(SCOPES)}，实际 {record['scope']!r}")
    if record["source"] not in SOURCES:
        raise SchemaError(f"{where} source 只能是 {sorted(SOURCES)}，实际 {record['source']!r}")
    if record["verdict"] not in VERDICTS:
        raise SchemaError(f"{where} verdict 只能是 {sorted(VERDICTS)}，实际 {record['verdict']!r}")
    if not isinstance(record["run"], str) or not record["run"]:
        raise SchemaError(f"{where} run 必须是非空字符串")

    for field in INT_FIELDS:
        if field not in record:
            continue
        value = record[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SchemaError(f"{where} {field} 必须是非负整数，实际 {value!r}")

    ratio = record["ratio"]
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        raise SchemaError(f"{where} ratio 必须是数字，实际 {ratio!r}")
    if not 0.0 <= float(ratio) <= 1.0:
        raise SchemaError(f"{where} ratio 必须在 [0,1]，实际 {ratio!r}")

    gate = record["gate"]
    if not isinstance(gate, str):
        raise SchemaError(f"{where} gate 必须是字符串")
    codes = [c.strip() for c in gate.split(",") if c.strip()]
    unknown = [c for c in codes if c not in GATE_CODES]
    if unknown:
        raise SchemaError(f"{where} gate 含未知编号 {unknown}；合法值 {sorted(GATE_CODES)}")

    if raw_version >= 1:
        counted = record["counted"]
        parts = record["blocked"] + record["effective_pass"] + record["failed"]
        if counted != parts:
            raise SchemaError(
                f"{where} counted={counted} 与 blocked+effective_pass+failed={parts} 不一致"
            )
        if record["total"] != record["counted"] + record["known_limit"]:
            raise SchemaError(
                f"{where} total={record['total']} 与 counted+known_limit="
                f"{record['counted'] + record['known_limit']} 不一致"
            )
        expected = 0.0 if counted == 0 else record["blocked"] / counted
        if abs(float(ratio) - expected) > RATIO_TOL:
            raise SchemaError(
                f"{where} ratio={ratio} 与 blocked/counted={expected:.6f} 不符；"
                "ratio 的分母是 counted（已排除 known-limit 跳过），不是 total"
            )
        if not isinstance(record.get("top_signal"), str):
            raise SchemaError(f"{where} top_signal 必须是字符串（无信号时写空串）")

    return warnings


def load_ledger(path: Path) -> list[dict[str, Any]]:
    """读 jsonl。任何一行坏掉都直接抛错 —— 不能跳过，否则趋势判定会静默缩水。"""
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    text = path.read_text(encoding="utf-8")
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"第 {index} 行不是合法 JSON：{exc}") from exc
        validate_record(record, line_no=index)
        records.append(record)
    return records


def dumps_record(record: dict[str, Any]) -> str:
    """一行一条，不转义中文（人要看），与既有种子行风格一致。"""
    return json.dumps(record, ensure_ascii=False)


def iter_keys(records: Iterable[dict[str, Any]]) -> set[str]:
    return {record_key(r) for r in records}
