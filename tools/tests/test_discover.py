"""discover / candidate_pool 的无浏览器单测。

重点验「铁律 1」：采集器任何异常都不许抛出 —— 用假 page 对象模拟各种坏情况
（evaluate 抛异常、返回形状不对、返回空），collect 必须全部吞掉。
候选池用 CANDIDATE_DIR 注入临时目录，不碰真实 reports/。
"""

from __future__ import annotations

import json

import pytest

from utils import candidate_pool, discover


class FakePage:
    """假 page：按脚本返回 / 抛异常，替代真浏览器。"""

    def __init__(self, script=None, url="https://trains.ctrip.com/webapp/train/list?dDate=2026-11-09&foo=bar"):
        self.script = script or {}
        self._url = url
        self.queries = 0

    @property
    def url(self):
        return self._url

    def evaluate(self, js, arg=None):
        item = self.script.get("evaluate")
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _isolated_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("CANDIDATE_DIR", str(tmp_path / "pool"))
    monkeypatch.setattr(discover, "_ROUND", "TESTROUND", raising=False)
    yield


# ---------------------------------------------------------------- 兜底（铁律 1）

def test_collect_swallows_evaluate_exception():
    page = FakePage(script={"evaluate": RuntimeError("boom")})
    result = discover.collect(page, site="trains", source_case="t")
    assert result["found"] == []
    assert any("RuntimeError" in e for e in result["collect_errors"])


def test_collect_reports_shape_mismatch_not_throw():
    page = FakePage(script={"evaluate": {"not": "a list"}})
    result = discover.collect(page, site="trains", source_case="t")
    assert result["found"] == []
    assert any("shape mismatch" in e for e in result["collect_errors"])


def test_collect_reports_not_present_on_none():
    page = FakePage(script={"evaluate": None})
    result = discover.collect(page, site="trains", source_case="t")
    assert result["not_present"], "None 应记 not_present"
    assert result["found"] == []


def test_collect_never_raises_on_uncaught(monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("sanitize_url 炸了")  # 模拟 collect 内部任何环节的意外异常

    monkeypatch.setattr(discover, "sanitize_url", explode)
    result = discover.collect(FakePage(), site="trains", source_case="t")
    assert "uncaught" in result["collect_errors"][0]


def test_unknown_site_records_error():
    result = discover.collect(FakePage(), site="nope", source_case="t")
    assert any("no hints" in e for e in result["collect_errors"])


# ---------------------------------------------------------------- 闸与池

def test_gates_dedupe_and_round_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "PER_DIMENSION_LIMIT", 2)
    page = FakePage(script={"evaluate": ["二等座", "一等座", "商务座", "无座"]})
    r1 = discover.collect(page, site="trains", source_case="c1")
    by_dim: dict[str, int] = {}
    for e in r1["found"]:
        by_dim[e["dimension"]] = by_dim.get(e["dimension"], 0) + 1
    assert by_dim and all(n <= 2 for n in by_dim.values()), "闸 3：每维度每轮最多 2 条"
    # 闸 3 是**进程级**计数（跨 collect 调用共享）：第 1 轮被阈值截掉的值
    # （商务座/无座）要等下一轮（新 pytest 进程）才收，本进程内不再吸纳。
    r2 = discover.collect(page, site="trains", source_case="c1")
    assert r2["found"] == [], "闸 3 进程级：本进程内不超阈值；闸 2：已入池值去重"
    assert (tmp_path / "pool" / "candidates.jsonl").is_file()


def test_normalize_strips_pua_and_collapses_ws():
    assert discover.normalize("中转\ue60c") == "中转"
    assert discover.normalize(" a   b ") == "a b"
    assert discover.normalize("更多") == "更多"  # 黑名单在 collect 层生效


def test_sanitize_url_drops_unlisted_query():
    out = discover.sanitize_url(
        "https://trains.ctrip.com/list?dDate=2026-11-09&token=secret&city=2"
    )
    assert "token" not in out and "secret" not in out
    assert out.startswith("https://trains.ctrip.com/list")


def test_pool_roundtrip_and_status_mark(tmp_path):
    entries = [{
        "value": "高铁(G/C)", "site": "trains", "dimension": "train_type",
        "source_case": "c1", "source_selector": "js:train_type",
        "url": "https://trains.ctrip.com/list", "collected_at": "2026-11-02T01:00:00+08:00",
        "round_id": "TESTROUND", "est_cost_s": None, "status": "candidate",
        "batch_id": "", "promoted_to": "",
    }]
    assert candidate_pool.append(entries) == 1
    first = json.loads(
        (tmp_path / "pool" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert first["v"] == candidate_pool.SCHEMA_VERSION, "jsonl 每条首字段必须是 schema 版本"
    assert candidate_pool.existing_keys() == {("trains", "train_type", "高铁(G/C)")}
    assert candidate_pool.mark(entries, "approved", batch_id="b1") == 1
    rec = json.loads(
        (tmp_path / "pool" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert rec["status"] == "approved" and rec["batch_id"] == "b1"


def test_summary_highlights_stale_pending(tmp_path):
    old = "2026-09-01T00:00:00+08:00"  # 远超 14 天
    candidate_pool.append([{
        "value": "v1", "site": "s", "dimension": "d", "source_case": "c",
        "source_selector": "x", "url": "", "collected_at": old,
        "round_id": "r", "est_cost_s": None, "status": "candidate",
        "batch_id": "", "promoted_to": "",
    }])
    summary = candidate_pool.summarize()
    assert summary["pending_count"] == 1
    assert summary["stale_pending"], "待审超 14 天必须高亮"
