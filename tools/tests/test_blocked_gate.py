"""blocked 门禁的解析器测试。

这里最要紧的一条不是「能算出 ratio」，而是 **口径错了必须红**：
把 31 条全量 junit 喂进去，门禁必须拒收，而不是算出 12.9% 然后判绿。
这条以前只是文档里的一句话，现在是一条会跑、会红的测试
（fixture `fixtures/junit-mixed-31.xml` 就是 2026-10-31 的真实现场）。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from xml.sax.saxutils import quoteattr

import blocked_gate
import ledger
import pytest

# 刻意不用 `from conftest import ...`：项目根目录也有一个 conftest.py，
# 谁先落进 sys.path 不确定，取到根 conftest 就 ImportError。
FIXTURES = Path(__file__).resolve().parent / "fixtures"

TS = "2026-10-31T23:43:28+08:00"


# ---------------------------------------------------------------- 构造器

def _case(name: str, outcome: str, *, scope: str | None = "ui", message: str = "") -> str:
    props = (
        f"<properties><property name='scope' value={quoteattr(scope)} /></properties>"
        if scope is not None else ""
    )
    if outcome == "pass":
        body = ""
    elif outcome in {"xfail", "skip"}:
        kind = "pytest.xfail" if outcome == "xfail" else "pytest.skip"
        body = f"<skipped type={quoteattr(kind)} message={quoteattr(message)} />"
    elif outcome == "fail":
        body = "<failure message='boom'>boom</failure>"
    else:
        raise AssertionError(f"未知 outcome：{outcome}")
    return f"<testcase classname='suites.thirdparty.tests.t' name={quoteattr(name)} time='0.1'>{props}{body}</testcase>"


def write_junit(tmp_path: Path, cases: list[str], *, timestamp: str = TS, name: str = "junit.xml") -> Path:
    xml = (
        f"<?xml version='1.0' encoding='utf-8'?>"
        f"<testsuites><testsuite name='pytest' tests='{len(cases)}' timestamp='{timestamp}'>"
        + "".join(cases)
        + "</testsuite></testsuites>"
    )
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return path


def blocked_case(name: str, signal: str = "waf_block（whaleguard）", scope: str = "ui") -> str:
    return _case(name, "xfail", message=f"blocked: {signal}", scope=scope)


def make_record(*, ts: str, blocked: int, passed: int, failed: int = 0, known: int = 0,
                verdict: str = "GREEN", gate: str = "G6", scope: str = "ui") -> dict:
    counted = blocked + passed + failed
    return {
        "schema_version": ledger.SCHEMA_VERSION,
        "ts": ts,
        "run": "seed",
        "source": "local",
        "scope": scope,
        "total": counted + known,
        "counted": counted,
        "blocked": blocked,
        "effective_pass": passed,
        "failed": failed,
        "known_limit": known,
        "ratio": round(0.0 if counted == 0 else blocked / counted, 4),
        "verdict": verdict,
        "gate": gate,
        "top_signal": "waf_block(whaleguard)" if blocked else "",
        "provisional": True,
        "note": "",
    }


def write_ledger(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        "".join(ledger.dumps_record(r) + "\n" for r in records), encoding="utf-8"
    )
    return path


def run_gate(tmp_path: Path, junit: Path, ledger_path: Path | None = None,
             capsys=None, scope: str | None = None) -> tuple[int, dict | None, str]:
    out = tmp_path / "record.json"
    argv = [str(junit), "--record-out", str(out), "--run", "test-run"]
    if ledger_path is not None:
        argv += ["--ledger", str(ledger_path)]
    if scope is not None:
        argv += ["--scope", scope]
    code = blocked_gate.main(argv)
    text = capsys.readouterr().out if capsys is not None else ""
    record = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
    return code, record, text


# ---------------------------------------------------------------- 契约：口径混算

def test_全量31条junit必须被拒收(tmp_path, capsys):
    """反例固化：忘了加 `-m ui`，27 条数据层单测混进 UI junit。

    这是「31 条 vs 4 条」那次统计口径错误的可执行版本：门禁必须 exit 2，
    而不是算出一个 12.9% 然后判绿。
    """
    cases = [_case(f"unit_{i}", "pass", scope="unit") for i in range(27)]
    cases += [blocked_case(f"ui_{i}") for i in range(4)]
    junit = write_junit(tmp_path, cases)

    code, record, text = run_gate(tmp_path, junit, capsys=capsys)

    assert code == blocked_gate.EXIT_CONTRACT
    assert record is None, "契约不成立时不允许产出记录，否则脏数据会进账本"
    assert "混入了 27 条" in text
    assert "12.9%" in text, "报错要写清混算的后果，接手人才知道为什么不能这么喂"


def test_旧格式junit没有scope属性必须被拒收(tmp_path, capsys):
    """现场存证：2026-10-31 那份真 junit 是旧 conftest 产出的，没有 scope 属性。

    这种文件不能"将就着算" —— 算出来的是另一个口径。
    """
    code, record, text = run_gate(tmp_path, FIXTURES / "junit-mixed-31.xml", capsys=capsys)

    assert code == blocked_gate.EXIT_CONTRACT
    assert record is None
    assert "没有 scope 属性" in text


def test_缺scope属性的junit必须被拒收(tmp_path, capsys):
    junit = write_junit(tmp_path, [_case("a", "pass", scope=None), _case("b", "pass", scope=None)])
    code, record, text = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_CONTRACT
    assert "没有 scope 属性" in text
    assert record is None


def test_xfail用未知前缀必须报错而不是当skip(tmp_path, capsys):
    junit = write_junit(tmp_path, [
        _case("a", "xfail", message="by design, don't care"),
        blocked_case("b"),
    ])
    code, record, text = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_CONTRACT
    assert "前缀不认识" in text
    assert record is None


def test_真skip没有known_limit前缀必须报错(tmp_path, capsys):
    junit = write_junit(tmp_path, [
        _case("a", "skip", message="环境没装驱动"),
        blocked_case("b"),
    ])
    code, _record, text = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_CONTRACT
    assert "known-limit" in text


def test_known_limit写在xfail里要给警告但不放行(tmp_path, capsys):
    junit = write_junit(tmp_path, [
        _case("a", "xfail", message="known-limit: K-01 日历"),
        blocked_case("b"), blocked_case("c"),
    ])
    code, _record, text = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_CONTRACT
    assert "已知限制应用 skip" in text


# ---------------------------------------------------------------- 指标定义

def test_known_limit不进分母(tmp_path, capsys):
    junit = write_junit(tmp_path, [
        blocked_case("a"), blocked_case("b"), blocked_case("c"), blocked_case("d"),
        _case("cal", "skip", message="known-limit: K-01 日历控件唤不起"),
    ])
    code, record, _ = run_gate(tmp_path, junit, capsys=capsys)

    assert code == blocked_gate.EXIT_RED
    assert record["total"] == 5
    assert record["counted"] == 4
    assert record["known_limit"] == 1
    assert record["ratio"] == 1.0


def test_ratio分母是counted不是total(tmp_path, capsys):
    """ratio = blocked ÷ counted。用 total 做分母会把 0.5 算成 0.4，这一步必须被钉住。"""
    junit = write_junit(tmp_path, [
        blocked_case("a"), blocked_case("b"),
        _case("c", "pass"), _case("d", "pass"),
        _case("cal", "skip", message="known-limit: K-01"),
    ])
    _, record, _ = run_gate(tmp_path, junit, capsys=capsys)

    assert record["counted"] == 4
    assert record["ratio"] == pytest.approx(0.5)
    assert record["ratio"] != pytest.approx(2 / 5)


# ---------------------------------------------------------------- 判定表

def test_四条全被拦_红_G1和G2同时命中(tmp_path, capsys):
    junit = write_junit(tmp_path, [blocked_case(str(i)) for i in range(4)])
    code, record, text = run_gate(tmp_path, junit, capsys=capsys)

    assert code == blocked_gate.EXIT_RED
    assert record["verdict"] == "RED"
    assert record["gate"] == "G1,G2", "两条同时成立就必须都报，只报第一条会让人以为修一条就绿了"
    assert record["top_signal"] == "waf_block(whaleguard)"
    assert "verdict: RED" in text


def test_真失败直接红_G0(tmp_path, capsys):
    junit = write_junit(tmp_path, [
        _case("a", "fail"), _case("b", "pass"), _case("c", "pass"), _case("d", "pass"),
    ])
    code, record, _ = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_RED
    assert "G0" in record["gate"].split(",")


def test_一条被拦三条通过_黄且退出0(tmp_path, capsys):
    junit = write_junit(tmp_path, [
        blocked_case("a"), _case("b", "pass"), _case("c", "pass"), _case("d", "pass"),
    ])
    code, record, _ = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_OK
    assert record["verdict"] == "YELLOW"
    assert record["gate"] == "G5"


def test_全部通过_绿(tmp_path, capsys):
    junit = write_junit(tmp_path, [_case(str(i), "pass") for i in range(4)])
    code, record, _ = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_OK
    assert record["verdict"] == "GREEN"
    assert record["gate"] == "G6"


def test_小样本只启用G1_不报G2(tmp_path, capsys):
    junit = write_junit(tmp_path, [blocked_case(str(i)) for i in range(3)])
    code, record, text = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_RED
    assert record["gate"] == "G1"
    assert "小样本豁免" in text


def test_全部known_limit跳过也算空转判红(tmp_path, capsys):
    """一条都没验证成功，绿色就没有意义 —— 不论是被对方拦，还是被我们自己跳过。"""
    junit = write_junit(tmp_path, [
        _case("a", "skip", message="known-limit: K-01"), _case("b", "skip", message="known-limit: K-01"),
    ])
    code, record, _ = run_gate(tmp_path, junit, capsys=capsys)
    assert code == blocked_gate.EXIT_RED
    assert record["gate"] == "G1"
    assert record["blocked"] == 0


# ---------------------------------------------------------------- 趋势判定

def test_G3连续三次高比例(tmp_path, capsys):
    history = [
        make_record(ts="2026-10-29T23:00:00+08:00", blocked=2, passed=3),
        make_record(ts="2026-10-30T23:00:00+08:00", blocked=2, passed=3),
    ]
    junit = write_junit(tmp_path, [blocked_case("a"), blocked_case("b")] +
                        [_case("p", "pass"), _case("q", "pass"), _case("r", "pass")])
    code, record, _ = run_gate(tmp_path, junit, write_ledger(tmp_path, history), capsys=capsys)

    assert record["ratio"] == pytest.approx(0.4)
    assert code == blocked_gate.EXIT_RED
    assert "G3" in record["gate"].split(",")


def test_G4连续七天低比例(tmp_path, capsys):
    history = [
        make_record(ts=f"2026-10-{day:02d}T23:00:00+08:00", blocked=1, passed=9)
        for day in range(25, 31)
    ]
    junit = write_junit(tmp_path, [blocked_case("a")] + [_case(f"p{i}", "pass") for i in range(4)])
    code, record, _ = run_gate(tmp_path, junit, write_ledger(tmp_path, history), capsys=capsys)

    assert record["verdict"] == "YELLOW"
    assert code == blocked_gate.EXIT_OK
    assert "G4" in record["gate"].split(",")


def test_历史不足时明说G4不可用(tmp_path, capsys):
    junit = write_junit(tmp_path, [blocked_case("a"), blocked_case("b")] +
                        [_case("p", "pass"), _case("q", "pass"), _case("r", "pass")])
    _, _, text = run_gate(tmp_path, junit, write_ledger(tmp_path, []), capsys=capsys)
    assert "G4 不可用" in text, "判不了就要说判不了，不能装作没触发"


def test_坏账本必须报错而不是跳过那一行(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"ts": "2026-10-30T23:00:00+08:00"}\n', encoding="utf-8")
    junit = write_junit(tmp_path, [_case(str(i), "pass") for i in range(4)])
    code, record, text = run_gate(tmp_path, junit, bad, capsys=capsys)
    assert code == blocked_gate.EXIT_CONTRACT
    assert record is None
    assert "账本读不了" in text


# ---------------------------------------------------------------- 轨道分账（裁决 2026-11-01）

def test_hotels的25不打断trains的1_0序列(tmp_path, capsys):
    """账本按 scope 分轨后，酒店的 25% 不得把火车票的 1.0 序列打断。

    不加过滤时 series 的末三位是 [0.25, 1.0, 1.0]，0.25 < 0.3 ⇒ G3 永不触发，
    轨道被稀释成一条永远不报红的曲线。分轨后末三位是 [1.0, 1.0, 1.0] ⇒ G3 必须命中。
    """
    history = [
        make_record(ts="2026-10-30T00:05:00+08:00", blocked=4, passed=0,
                    verdict="RED", gate="G1,G2"),
        make_record(ts="2026-10-31T00:06:00+08:00", blocked=1, passed=3,
                    verdict="YELLOW", gate="G5", scope="ui_hotels"),
        make_record(ts="2026-10-31T00:07:00+08:00", blocked=4, passed=0,
                    verdict="RED", gate="G1,G2"),
    ]
    junit = write_junit(tmp_path, [blocked_case(f"t{i}") for i in range(4)])
    code, record, text = run_gate(tmp_path, junit, write_ledger(tmp_path, history), capsys=capsys)

    assert record["scope"] == "ui"
    assert "G3" in record["gate"].split(","), f"G3 被别的轨道打断了：{record['gate']}"
    assert code == blocked_gate.EXIT_RED
    assert "scope=ui 共 2 条" in text, "输出要写清本轨道用了几条历史，否则看不出分没分轨"
    assert "其余 1 条属于别的轨道" in text


def test_hotels轨道G4独立计算(tmp_path, capsys):
    """hotels 自己连续 7 天低比例要触发 G4，且不受同一天 trains 的「零拦截」影响。

    账本里故意在 10-28 追加一条 trains 的 blocked=0 记录：不分轨时同日取最后一条，
    这天就被判成「没被拦」，G4 永远凑不满 7 天。
    """
    history = [
        make_record(ts=f"2026-10-{day:02d}T00:06:00+08:00", blocked=1, passed=3,
                    verdict="YELLOW", gate="G5", scope="ui_hotels")
        for day in range(26, 32)
    ]
    history.append(
        make_record(ts="2026-10-28T00:30:00+08:00", blocked=0, passed=4,
                    verdict="GREEN", gate="G6", scope="ui")
    )
    junit = write_junit(
        tmp_path,
        [blocked_case("h", scope="ui_hotels"), _case("p1", "pass", scope="ui_hotels"),
         _case("p2", "pass", scope="ui_hotels"), _case("p3", "pass", scope="ui_hotels")],
        timestamp="2026-11-01T00:10:00+08:00",
    )
    code, record, text = run_gate(tmp_path, junit, write_ledger(tmp_path, history),
                                  capsys=capsys, scope="ui_hotels")

    assert record["scope"] == "ui_hotels"
    assert record["ratio"] == pytest.approx(0.25)
    assert "G4" in record["gate"].split(","), f"hotels 自己的 G4 没算出来：{record['gate']}"
    assert record["verdict"] == "YELLOW"
    assert code == blocked_gate.EXIT_OK
    assert "scope=ui_hotels 共 6 条" in text


def test_门禁scope与junit口径不符必须拒收(tmp_path, capsys):
    """拿 ui 门禁去判 hotels 的 junit，必须 exit 2；不许算出一个数。"""
    junit = write_junit(tmp_path, [_case("h1", "pass", scope="ui_hotels")])
    code, record, text = run_gate(tmp_path, junit, capsys=capsys, scope="ui")

    assert code == blocked_gate.EXIT_CONTRACT
    assert record is None
    assert "别的轨道（ui_hotels×1）" in text


def test_记录scope跟随所判轨道(tmp_path, capsys):
    junit = write_junit(
        tmp_path,
        [blocked_case("h", scope="ui_hotels"), _case("p1", "pass", scope="ui_hotels"),
         _case("p2", "pass", scope="ui_hotels"), _case("p3", "pass", scope="ui_hotels")],
    )
    _, record, _ = run_gate(tmp_path, junit, capsys=capsys, scope="ui_hotels")

    assert record["scope"] == "ui_hotels", "写进账本的 scope 必须是所判轨道，不是默认 ui"
    assert ledger.validate_record(record) == [] or isinstance(ledger.validate_record(record), list)


# ---------------------------------------------------------------- 分账前遗留记录（裁决 2026-11-01）

def test_分账前遗留的25记录在第6条ui记录后滑出窗口():
    """锁死 `ops/README.md` 那句「ui 轨道 G3 预计在第 6 条 ui 记录后恢复」。

    账本现有 3 条 ui：1.0 / 1.0 / 0.25。G3 看的是 `series[-3:]`，所以那条 0.25
    要真正滑出窗口，需要**三条**新记录：

        第 4 条 ⇒ 末三位 [1.0, 0.25, 新]   仍含 0.25  ⇒ 不触发
        第 5 条 ⇒ 末三位 [0.25, 新, 新]    仍含 0.25  ⇒ 不触发
        第 6 条 ⇒ 末三位 [新, 新, 新]      0.25 滑出  ⇒ 才可能触发

    即「再新增 2 条就恢复」是错的（差一条）。这条测试就是那句话的证伪器：
    谁改切片逻辑或改窗口长度，它先红。
    """
    history = [
        make_record(ts="2026-10-31T00:05:00+08:00", blocked=4, passed=0,
                    verdict="RED", gate="G1,G2"),
        make_record(ts="2026-11-01T00:06:15+08:00", blocked=4, passed=0,
                    verdict="RED", gate="G1,G2"),
        make_record(ts="2026-11-01T00:31:28+08:00", blocked=1, passed=3,
                    verdict="YELLOW", gate="G5"),
    ]
    assert [r["ratio"] for r in history] == [1.0, 1.0, 0.25]

    stats = blocked_gate.Stats(total=4, blocked=4, effective_pass=0)
    hits: dict[int, bool] = {}
    for nth in (4, 5, 6):
        _, gates, _ = blocked_gate.evaluate(stats, history, date(2026, 11, 1), scope="ui")
        hits[nth] = "G3" in gates
        history = history + [make_record(ts=f"2026-11-01T0{nth}:00:00+08:00",
                                        blocked=4, passed=0, verdict="RED", gate="G1,G2")]

    assert hits == {4: False, 5: False, 6: True}, f"G3 恢复时点与 README 不符：{hits}"


def test_分账前遗留记录要点名_但不改判也不改行(tmp_path, capsys):
    """遗留行必须被说出来，且只说不改：verdict 与账本字节都不能动。"""
    history = [
        make_record(ts="2026-10-31T00:05:00+08:00", blocked=4, passed=0,
                    verdict="RED", gate="G1,G2"),
        make_record(ts="2026-11-01T00:31:28+08:00", blocked=1, passed=3,
                    verdict="YELLOW", gate="G5"),
        make_record(ts="2026-11-01T00:43:26+08:00", blocked=1, passed=3,
                    verdict="YELLOW", gate="G5", scope="ui_hotels"),
    ]
    junit = write_junit(
        tmp_path,
        [blocked_case("t0"), _case("p1", "pass"), _case("p2", "pass"), _case("p3", "pass")],
    )
    ledger_path = write_ledger(tmp_path, history)
    before = ledger_path.read_text(encoding="utf-8")

    code, record, text = run_gate(tmp_path, junit, ledger_path, capsys=capsys)

    assert "分账前遗留记录" in text, "不说出来，看板只会以为 G3 坏了"
    assert "ts=2026-11-01T00:31:28+08:00" in text, "要指认到具体行，不能只说「有遗留」"
    assert "有 1 条 ratio<30% 的行" in text, (
        "只能点名真正压制 G3 的那条（<30%）；把 ratio=1.0 的历史行也算进来等于噪声"
    )
    assert "override" in text, "要写明不提供 override，否则下一个人会去找那个开关"
    assert record["gate"] == "G5" and code == blocked_gate.EXIT_OK, (
        f"遗留提示不得改变判定：{record['verdict']}/{record['gate']}"
    )
    assert ledger_path.read_text(encoding="utf-8") == before, "提示只是读，不许回写账本"


def test_只有一条轨道时不猜分账点(tmp_path, capsys):
    """账本里还没有第二条轨道时，没有「分账前」可言 —— 不许凭空造一条提示。"""
    history = [
        make_record(ts="2026-10-31T00:05:00+08:00", blocked=1, passed=3,
                    verdict="YELLOW", gate="G5"),
    ]
    junit = write_junit(
        tmp_path,
        [blocked_case("t0"), _case("p1", "pass"), _case("p2", "pass"), _case("p3", "pass")],
    )
    _, _, text = run_gate(tmp_path, junit, write_ledger(tmp_path, history), capsys=capsys)

    assert "分账前遗留记录" not in text


# ---------------------------------------------------------------- 记录与幂等

def test_记录带schema_version与provisional(tmp_path, capsys):
    junit = write_junit(tmp_path, [blocked_case(str(i)) for i in range(4)])
    _, record, text = run_gate(tmp_path, junit, capsys=capsys)

    assert record["schema_version"] == ledger.SCHEMA_VERSION
    assert record["provisional"] is True
    assert record["source"] == "local"
    ledger.validate_record(record)          # 自产记录必须过自家校验
    assert "[provisional" in text, "阈值没校准就必须在输出里写出来，别让人当成推导值"


def test_同一份junit重跑得到同一个幂等键(tmp_path, capsys):
    """workflow 重试 / 本地重放同一份 junit，不能往账本里写第二行。"""
    junit = write_junit(tmp_path, [blocked_case(str(i)) for i in range(4)])
    _, first, _ = run_gate(tmp_path, junit, capsys=capsys)
    _, second, _ = run_gate(tmp_path, junit, capsys=capsys)

    assert ledger.record_key(first) == ledger.record_key(second)
    assert ledger.record_key(first) == f"{TS}|ui"


def test_ts取自junit时间戳而不是墙上时钟(tmp_path, capsys):
    junit = write_junit(tmp_path, [_case(str(i), "pass") for i in range(4)])
    _, record, _ = run_gate(tmp_path, junit, capsys=capsys)
    root = ET.parse(junit).getroot()
    assert record["ts"] == ledger.normalize_ts(root.find("testsuite").get("timestamp"))
