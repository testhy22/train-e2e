"""blocked 门禁判定器。**规范唯一来源是 docs/BLOCKED-GATE.md，本文件只是它的实现。**

它要回答的问题只有一个：这份"绿了"的 junit，到底验证成功了几条？
blocked → xfail 让 pytest 永远退出 0，所以必须有一个独立判定器把
「绿了但什么都没验」判出来，否则 CI 的绿色没有任何信息量。

用法：
    python tools/blocked_gate.py reports/junit-ui.xml \
        --ledger reports/ledger-view.jsonl \
        --record-out reports/gate-record.json

退出码（与文档一致）：
    0  绿 / 黄（黄也要让 job 绿）
    1  红（G0/G1/G2/G3/G4 任一触发）
    2  契约不成立（口径混算、前缀不认识、记录结构错）—— 这类问题不是"今天被拦"，
       而是"统计本身坏了"，必须比红更早暴露，所以单独给一个码。
"""

from __future__ import annotations

import argparse
import os
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import ledger

# ---------------------------------------------------------------- 契约常量

PREFIX_BLOCKED = "blocked:"
PREFIX_KNOWN_LIMIT = "known-limit:"

# 本门禁一次只判一条轨道；junit 里出现别的轨道（含 unit）直接判契约不成立。
# 轨道清单与账本共用一份（ledger.GATE_SCOPES），避免两边各记一套。
DEFAULT_SCOPE = "ui"
GATE_SCOPES = ledger.GATE_SCOPES
REQUIRED_SCOPE_TAG = "scope"

# 阈值。**全部未校准**，ledger 攒够 14 天真实数据后重算并把依据写回 docs/BLOCKED-GATE.md §6.4。
# 代码里标 provisional 而不是等文档说：跑起来的人先看到的是输出。
PROVISIONAL = True
T_RATIO_HIGH = 0.5        # G2 单次高比例
T_RATIO_TREND = 0.3       # G3 连续 3 次
T_TREND_RUNS = 3
T_RATIO_CHRONIC = 0.1     # G4 长期
T_CHRONIC_DAYS = 7
T_MIN_SAMPLE = 4          # 小样本豁免门槛（按 counted 算）

EXIT_OK, EXIT_RED, EXIT_CONTRACT = 0, 1, 2


class ContractError(RuntimeError):
    """junit 与契约不符：口径混算、前缀不认识、属性缺失。"""


# ---------------------------------------------------------------- 解析

@dataclass
class Case:
    classname: str
    name: str
    outcome: str                 # passed / xfailed / skipped / failed
    message: str = ""
    kind: str = ""               # blocked / known-limit / unknown / ""


def _text(node: ET.Element | None) -> str:
    return (node.get("message") or "") if node is not None else ""


def classify(message: str) -> str:
    """把 message 前缀翻成统计口径。不认识的一律 unknown，绝不静默当 skip。"""
    # 防御：某些 pytest 版本会加 "Skipped: " 前缀（9.2.0 实测不加）
    text = message.lstrip().removeprefix("Skipped: ")
    if text.startswith(PREFIX_BLOCKED):
        return "blocked"
    if text.startswith(PREFIX_KNOWN_LIMIT):
        return "known-limit"
    return "unknown"


# ---------------------------------------------------------------- 统计

@dataclass
class Stats:
    total: int = 0
    blocked: int = 0
    effective_pass: int = 0
    failed: int = 0
    known_limit: int = 0
    signals: Counter = field(default_factory=Counter)

    @property
    def counted(self) -> int:
        """分母：真正被计入判定的用例。known-limit 跳过不进分母，这是口径的核心。"""
        return self.blocked + self.effective_pass + self.failed

    @property
    def ratio(self) -> float:
        return 0.0 if self.counted == 0 else self.blocked / self.counted

    @property
    def top_signal(self) -> str:
        if not self.signals:
            return ""
        # 取最多的一种；若有并列，按名称排序保证结果稳定（否则两次跑同一份 junit 得到不同记录）。
        best = max(sorted(self.signals), key=lambda k: self.signals[k])
        extra = len(self.signals) - 1
        return f"{best}(+{extra})" if extra else best


def _scope_of(node: ET.Element) -> str | None:
    for prop in node.findall("./properties/property"):
        if prop.get("name") == REQUIRED_SCOPE_TAG:
            return prop.get("value")
    return None


def aggregate(path: Path, scope: str = DEFAULT_SCOPE) -> tuple[Stats, list[str]]:
    """解析 + 校验口径，返回统计与契约问题清单（非空即 exit 2）。

    `scope` 是本门禁要判的那条轨道。junit 是 `pytest -m <轨道>` 产出的，
    里面每一条的 scope 属性都必须等于它，否则就是口径混算。
    """
    if not path.is_file():
        raise ContractError(f"找不到 junit：{path}（先跑 pytest ... --junitxml={path}）")
    root = ET.parse(path).getroot()

    stats = Stats()
    problems: list[str] = []
    other_scopes: Counter[str] = Counter()
    missing_scope = 0

    for node in root.iter("testcase"):
        label = f"{node.get('classname', '')}::{node.get('name', '')}"
        case_scope = _scope_of(node)
        if case_scope is None:
            missing_scope += 1
        elif case_scope != scope:
            other_scopes[case_scope] += 1

        case = parse_one(node)
        stats.total += 1
        if case.outcome == "failed":
            stats.failed += 1
        elif case.outcome == "xfailed":
            if case.kind == "blocked":
                stats.blocked += 1
                stats.signals[_signal_of(case.message)] += 1
            elif case.kind == "known-limit":
                stats.known_limit += 1
                problems.append(f"{label} 用的是 xfail 但前缀是 known-limit；已知限制应用 skip")
            else:
                problems.append(f"{label} 的 xfail 前缀不认识：{case.message[:60]!r}")
        elif case.outcome == "skipped":
            if case.kind == "known-limit":
                stats.known_limit += 1
            elif case.kind == "blocked":
                stats.blocked += 1
                stats.signals[_signal_of(case.message)] += 1
                problems.append(f"{label} 用 skip 声明被拦（blocked:）；被拦必须用 xfail 才会进报告")
            else:
                problems.append(
                    f"{label} 被跳过但 reason 没有 known-limit: 前缀：{case.message[:60]!r}"
                    "（未知前缀不允许静默算作 skip）"
                )
        else:
            stats.effective_pass += 1

    if other_scopes:
        seen = "、".join(f"{name}×{count}" for name, count in sorted(other_scopes.items()))
        problems.append(
            f"junit 里混入了 {sum(other_scopes.values())} 条别的轨道（{seen}），"
            f"本次要判的是 scope={scope}（共 {stats.total} 条）。"
            f"门禁只吃 `pytest -m <轨道>` 产出的那份 junit；混算会把 ratio 稀释到无意义"
            "（31 条口径下 ratio 只有 12.9%，G1/G2 双双失守），也会让 G3/G4 跨轨道串味"
        )
    if missing_scope:
        problems.append(
            f"{missing_scope} 条用例没有 scope 属性：junit 不是由本工程的 conftest 产出的，"
            "无法判断口径"
        )
    return stats, problems


def parse_one(node: ET.Element) -> Case:
    case = Case(classname=node.get("classname", ""), name=node.get("name", ""), outcome="passed")
    if node.find("failure") is not None or node.find("error") is not None:
        case.outcome = "failed"
        return case
    skipped = node.find("skipped")
    if skipped is not None:
        case.message = _text(skipped)
        case.outcome = "xfailed" if skipped.get("type") == "pytest.xfail" else "skipped"
        case.kind = classify(case.message)
    return case


def _signal_of(message: str) -> str:
    """`blocked: waf_block（whaleguard）` → `waf_block(whaleguard)`。"""
    payload = message.lstrip()[len(PREFIX_BLOCKED):].strip()
    for open_paren, close_paren in (("（", "）"), ("(", ")")):
        if open_paren in payload:
            kind, _, detail = payload.partition(open_paren)
            detail = detail.rstrip(close_paren).strip()
            return f"{kind.strip()}({detail})" if detail else kind.strip()
    return payload


# ---------------------------------------------------------------- 判定

def evaluate(
    stats: Stats, history: list[dict], today: date, scope: str = DEFAULT_SCOPE
) -> tuple[str, list[str], list[str]]:
    """逐条计算所有判定（**不短路**），返回 (verdict, 触发的编号, 不可用的说明)。

    为什么不做"命中即停"：G1 与 G2 常常同时成立，只报第一个会让人以为修掉一条就能变绿。
    看板要看的是"同时命中几条"。

    **G3/G4 只在同一 scope 内累计**（裁决 2026-11-01）：火车票与酒店打的是两个站点，
    拦截率不可比。混在一起算时，酒店的一条 25% 会把火车票的 1.0 序列打断，
    G3 永远不会触发 —— 那不是"阈值灵敏"，那是阈值被稀释。
    """
    triggered: list[str] = []
    unavailable: list[str] = []

    if stats.failed >= 1:
        triggered.append("G0")

    # G1 空转：一条都没验证成功。原写法是 `blocked>=2 且 pass==0`，
    # 今天两种写法同判红；扩成 `pass==0 且 total>=2` 是为了覆盖
    # 「全部被 known-limit 跳过」这种同样什么都没测的情形。
    if stats.effective_pass == 0 and stats.total >= 2:
        triggered.append("G1")

    # 小样本豁免：counted < 4 时比例没有统计意义，关掉 G2/G3/G4，但 G0/G1/G5/G6 照常
    # （它们不是比例判定，不受样本量影响）。
    small = stats.counted < T_MIN_SAMPLE

    if not small and stats.counted >= T_MIN_SAMPLE and stats.ratio >= T_RATIO_HIGH:
        triggered.append("G2")

    track_history = [r for r in history if r.get("scope") == scope]
    if small:
        unavailable.append(f"counted={stats.counted} < {T_MIN_SAMPLE}，G2/G3/G4 按小样本豁免关闭")
    else:
        series = [float(r["ratio"]) for r in track_history] + [stats.ratio]
        if len(series) >= T_TREND_RUNS and all(r >= T_RATIO_TREND for r in series[-T_TREND_RUNS:]):
            triggered.append("G3")

        window_start = today - timedelta(days=T_CHRONIC_DAYS - 1)
        by_day: dict[date, dict] = {}
        for record in track_history:
            day = datetime.fromisoformat(record["ts"]).date()
            if window_start <= day <= today:
                by_day[day] = record          # 同一天多条取最后一条
        by_day[today] = {"ratio": stats.ratio, "blocked": stats.blocked}
        if len(by_day) >= T_CHRONIC_DAYS and all(
            r["blocked"] >= 1 and float(r["ratio"]) >= T_RATIO_CHRONIC for r in by_day.values()
        ):
            triggered.append("G4")
        elif len(by_day) < T_CHRONIC_DAYS:
            unavailable.append(
                f"最近 {T_CHRONIC_DAYS} 天只有 {len(by_day)} 天有 scope={scope} 的记录，"
                f"G4 不可用（不是不触发）"
            )

    if not small and stats.blocked >= 1 and not triggered:
        triggered.append("G5")

    if stats.blocked == 0 and stats.effective_pass >= 1 and not triggered:
        triggered.append("G6")

    red = {"G0", "G1", "G2", "G3"}
    yellow = {"G4", "G5"}
    if red & set(triggered):
        verdict = "RED"
    elif yellow & set(triggered):
        verdict = "YELLOW"
    else:
        verdict = "GREEN"
    return verdict, triggered, unavailable


# ---------------------------------------------------------------- 记录

def build_record(stats: Stats, verdict: str, gates: list[str], *, ts: str, run: str, source: str,
                 note: str, scope: str = DEFAULT_SCOPE) -> dict:
    return {
        "schema_version": ledger.SCHEMA_VERSION,
        "ts": ledger.normalize_ts(ts),
        "run": run,
        "source": source,
        "scope": scope,
        "total": stats.total,
        "counted": stats.counted,
        "blocked": stats.blocked,
        "effective_pass": stats.effective_pass,
        "failed": stats.failed,
        "known_limit": stats.known_limit,
        "ratio": round(stats.ratio, 4),
        "verdict": verdict,
        "gate": ",".join(gates),
        "top_signal": stats.top_signal,
        "provisional": PROVISIONAL,
        "note": note,
    }


def junit_ts(path: Path) -> str:
    """取 junit 的 testsuite timestamp 作为记录的 ts。

    不用墙上时钟：workflow 重试 / 本地重放同一份 junit 必须得到同一个 ts，
    否则 `ts|scope` 幂等键失效，账本会出现两行一模一样的结果。
    """
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    raw = (suite.get("timestamp") if suite is not None else None) or ""
    return raw


# ---------------------------------------------------------------- 输出

def render(
    stats: Stats, verdict: str, gates: list[str], unavailable: list[str], problems: list[str],
    scope: str = DEFAULT_SCOPE, history_note: str = "",
) -> str:
    mark = "  [provisional：阈值未校准，见 docs/BLOCKED-GATE.md §6]" if PROVISIONAL else ""
    lines = [
        f"blocked 门禁{mark}",
        (
            f"  scope={scope}  total={stats.total}  counted={stats.counted}  "
            f"blocked={stats.blocked}  pass={stats.effective_pass}  failed={stats.failed}  "
            f"known-limit={stats.known_limit}  ratio={stats.ratio:.1%}"
        ),
    ]
    if history_note:
        lines.append(f"  {history_note}")
    lines.append(f"  verdict: {verdict}   触发: {', '.join(gates) or '(无)'}")
    if stats.top_signal:
        lines.append(f"  主要信号: {stats.top_signal}")
    for item in unavailable:
        lines.append(f"  · {item}")
    for item in problems:
        lines.append(f"  ! {item}")
    return "\n".join(lines)


def annotate(verdict: str, stats: Stats, gates: list[str]) -> None:
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    if verdict == "RED":
        print(f"::error::blocked 门禁判定 RED（{'/'.join(gates)}）："
              f"blocked {stats.blocked}/{stats.counted}，有效通过 {stats.effective_pass}")
    elif verdict == "YELLOW":
        print(f"::warning::blocked 门禁判定 YELLOW（{'/'.join(gates)}）："
              f"blocked {stats.blocked}/{stats.counted}，属提示不阻断")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### blocked 门禁（provisional）\n\n")
            fh.write("| 项 | 值 |\n|---|---|\n")
            fh.write(f"| verdict | **{verdict}** |\n")
            fh.write(f"| 触发 | {', '.join(gates) or '(无)'} |\n")
            fh.write(f"| total / counted | {stats.total} / {stats.counted} |\n")
            fh.write(f"| blocked / pass / failed / known-limit | "
                     f"{stats.blocked} / {stats.effective_pass} / {stats.failed} / {stats.known_limit} |\n")
            fh.write(f"| ratio（blocked÷counted） | {stats.ratio:.1%} |\n")


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="blocked 门禁判定器（规范见 docs/BLOCKED-GATE.md）")
    parser.add_argument("junit", type=Path)
    parser.add_argument("--scope", default=DEFAULT_SCOPE, choices=list(GATE_SCOPES),
                        help=f"本次要判的轨道（默认 {DEFAULT_SCOPE}）；junit 里混入别的轨道直接 exit 2")
    parser.add_argument("--ledger", type=Path, help="历史记录（jsonl），用于 G3/G4；缺省则两条判为不可用")
    parser.add_argument("--record-out", type=Path, default=Path("reports/gate-record.json"))
    parser.add_argument("--ts", help="覆盖 ts，默认取 junit 的 testsuite timestamp")
    parser.add_argument("--run", default=os.getenv("GITHUB_RUN_ID", "local"))
    parser.add_argument("--source", default="ci" if os.getenv("GITHUB_ACTIONS") == "true" else "local")
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)

    try:
        stats, problems = aggregate(args.junit, args.scope)
    except ContractError as exc:
        print(f"契约不成立：{exc}")
        print("::error::blocked 门禁无法判定（契约问题）" if os.getenv("GITHUB_ACTIONS") == "true" else "")
        return EXIT_CONTRACT

    if problems:
        print(render(stats, "CONTRACT", [], [], problems, scope=args.scope))
        if os.getenv("GITHUB_ACTIONS") == "true":
            print(f"::error::blocked 门禁无法判定：{problems[0]}")
        return EXIT_CONTRACT

    history: list[dict] = []
    if args.ledger:
        try:
            history = ledger.load_ledger(args.ledger)
        except ledger.SchemaError as exc:
            print(f"账本读不了，G3/G4 判为不可用：{exc}")
            print("::error::ledger 结构错误，必须先修复账本" if os.getenv("GITHUB_ACTIONS") == "true" else "")
            return EXIT_CONTRACT

    ts = args.ts or junit_ts(args.junit)
    if not ts:
        print("junit 里没有 testsuite timestamp，无法生成稳定 ts（幂等键会失效），请用 --ts 指定")
        return EXIT_CONTRACT

    try:
        today = datetime.fromisoformat(ledger.normalize_ts(ts)).date()
    except ledger.SchemaError as exc:
        print(f"ts 不合法：{exc}")
        return EXIT_CONTRACT

    track_rows = sum(1 for r in history if r.get("scope") == args.scope)
    history_note = (
        f"轨道历史：scope={args.scope} 共 {track_rows} 条"
        f"（账本总计 {len(history)} 条，其余 {len(history) - track_rows} 条属于别的轨道，不计入 G3/G4）"
        if args.ledger else "未提供 --ledger：G3/G4 判为不可用"
    )

    verdict, gates, unavailable = evaluate(stats, history, today, scope=args.scope)
    print(render(stats, verdict, gates, unavailable, [], scope=args.scope, history_note=history_note))
    annotate(verdict, stats, gates)

    record = build_record(
        stats, verdict, gates, ts=ts, run=args.run, source=args.source,
        note=args.note, scope=args.scope,
    )
    try:
        warnings = ledger.validate_record(record)
    except ledger.SchemaError as exc:
        print(f"生成的记录自身不合法（实现缺陷，不是数据问题）：{exc}")
        return EXIT_CONTRACT
    for warning in warnings:
        print(f"  · {warning}")

    args.record_out.parent.mkdir(parents=True, exist_ok=True)
    args.record_out.write_text(ledger.dumps_record(record) + "\n", encoding="utf-8")
    print(f"记录已写入 {args.record_out}")

    return EXIT_RED if verdict == "RED" else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
