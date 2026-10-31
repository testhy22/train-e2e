"""从 junit.xml 里统计 blocked 数量，并在 CI 里打黄色告警。

设计意图：blocked 不是红。它说明「今天被风控拦了」，是有效情报而不是构建失败，
所以 Job 保持绿色，只出一行 warning annotation，进 summary 供人看。
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BLOCKED_MARK = "blocked by anti-bot"


def collect(junit_path: Path) -> dict[str, int]:
    root = ET.parse(junit_path).getroot()
    stats = {"total": 0, "failed": 0, "blocked": 0, "other_skipped": 0}
    for case in root.iter("testcase"):
        stats["total"] += 1
        if case.find("failure") is not None or case.find("error") is not None:
            stats["failed"] += 1
            continue
        skipped = case.find("skipped")
        if skipped is not None:
            message = skipped.get("message") or ""
            if BLOCKED_MARK in message:
                stats["blocked"] += 1
            else:
                stats["other_skipped"] += 1
    return stats


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python tools/ci_blocked_report.py <junit.xml>")
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"::warning::找不到 {path}，无法统计 blocked")
        return 0

    stats = collect(path)
    line = (
        f"用例 {stats['total']} 条：失败 {stats['failed']}，"
        f"blocked {stats['blocked']}，其他跳过 {stats['other_skipped']}"
    )
    print(line)

    if stats["blocked"]:
        print(f"::warning::目标站点返回风控拦截 {stats['blocked']} 条（blocked 不是构建失败）")

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### 本轮结果\n\n")
            fh.write(f"- 用例总数：{stats['total']}\n")
            fh.write(f"- 失败：{stats['failed']}\n")
            fh.write(f"- blocked（被风控拦下）：{stats['blocked']}\n")
            fh.write(f"- 其他跳过：{stats['other_skipped']}\n")
            if stats["blocked"]:
                fh.write("\n> blocked 不计入失败：它说明站点当天拒绝了自动化访问，属有效情报。\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
