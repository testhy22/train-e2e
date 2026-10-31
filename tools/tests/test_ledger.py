"""ledger 记录的 schema 测试。

重点不是「能读」，而是「读到不对的东西时必须喊」：
账本一旦能被静默跳过坏行，G3/G4 的趋势判定就会在无人察觉的情况下变松。
"""

from __future__ import annotations

from pathlib import Path

import ledger
import pytest


def good(**over) -> dict:
    record = {
        "schema_version": 1,
        "ts": "2026-10-31T23:43:28+08:00",
        "run": "gh-1",
        "source": "ci",
        "scope": "ui",
        "total": 5,
        "counted": 4,
        "blocked": 4,
        "effective_pass": 0,
        "failed": 0,
        "known_limit": 1,
        "ratio": 1.0,
        "verdict": "RED",
        "gate": "G1,G2",
        "top_signal": "waf_block(whaleguard)",
        "provisional": True,
        "note": "",
    }
    record.update(over)
    return record


def test_合法记录通过校验():
    assert ledger.validate_record(good()) == []


def test_v0历史种子行被容忍但给警告():
    legacy = {
        "ts": "2026-10-31T23:44:11+08:00", "run": "local-seed-2026-10-31", "source": "local",
        "scope": "ui", "total": 4, "blocked": 4, "effective_pass": 0, "failed": 0,
        "skipped": 0, "ratio": 1.0, "verdict": "RED", "gate": "G1,G2",
        "top_signal": "waf_block(whaleguard)", "note": "首条",
    }
    warnings = ledger.validate_record(legacy)
    assert any("v0" in w for w in warnings), "历史行不改写，但必须让人知道它没 schema_version"


def test_未来版本拒绝而不是猜():
    with pytest.raises(ledger.SchemaError, match="比本工具支持"):
        ledger.validate_record(good(schema_version=2))


def test_缺字段直接报错():
    record = good()
    del record["known_limit"]
    with pytest.raises(ledger.SchemaError, match="缺字段"):
        ledger.validate_record(record)


def test_ratio与counted不一致必须报错():
    with pytest.raises(ledger.SchemaError, match="ratio"):
        ledger.validate_record(good(ratio=0.4, blocked=4, counted=4))


def test_counted不等于三项之和必须报错():
    with pytest.raises(ledger.SchemaError, match="counted"):
        ledger.validate_record(good(counted=5))


def test_total不等于counted加known_limit必须报错():
    with pytest.raises(ledger.SchemaError, match="total"):
        ledger.validate_record(good(total=9))


def test_ts没有时区必须报错():
    with pytest.raises(ledger.SchemaError, match="时区"):
        ledger.validate_record(good(ts="2026-10-31T23:43:28"))


def test_gate含未知编号必须报错():
    with pytest.raises(ledger.SchemaError, match="未知编号"):
        ledger.validate_record(good(gate="G9"))


def test_scope只认ui和unit():
    with pytest.raises(ledger.SchemaError, match="scope"):
        ledger.validate_record(good(scope="all"))


def test_读账本遇到坏行立刻报错并给行号(tmp_path: Path):
    path = tmp_path / "l.jsonl"
    path.write_text(
        ledger.dumps_record(good()) + "\n" + '{"ts": "不是时间"}\n', encoding="utf-8"
    )
    with pytest.raises(ledger.SchemaError, match="第 2 行"):
        ledger.load_ledger(path)


def test_幂等键只看ts和scope():
    a = good()
    b = good(verdict="GREEN", ratio=0.0, blocked=0, effective_pass=4)
    assert ledger.record_key(a) == ledger.record_key(b)
    assert ledger.record_key(a) != ledger.record_key(good(scope="unit"))
    assert ledger.record_key(a) != ledger.record_key(good(ts="2026-11-01T00:00:00+08:00"))


def test_ts归一化到秒避免微秒抖动造重复行():
    assert ledger.normalize_ts("2026-10-31T23:43:28.410210+08:00") == "2026-10-31T23:43:28+08:00"


def test_坏JSON给出行号(tmp_path: Path):
    path = tmp_path / "l.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ledger.SchemaError, match="第 1 行"):
        ledger.load_ledger(path)
