"""生成 / 打开 Allure 报告。

为什么要有这个脚本，而不是让人记两行命令：

  1. **JAVA_HOME 必须指对**。Allure 2 的 CLI 是 Java 程序，本机没有系统级 JRE，
     用的是项目外的一份便携 JRE。少写这一句，`allure` 只会报
     "JAVA_HOME is not set"，而这句话看不出「到底该指到哪」。
  2. **history 的续接顺序不能错**：必须「先续历史 → 再 generate」。
     顺序反了不会报错，只会让趋势图永远只有一根柱子 —— 这种「静默无效」
     正是最该被脚本固化的一类操作（同 conftest 里 cleaner/trace 的处理原则）。

用法：
    python scripts/allure_report.py            # 生成到 reports/allure-report
    python scripts/allure_report.py --open     # 生成后直接用默认浏览器打开
    python scripts/allure_report.py --serve    # 起临时服务看（带趋势交互）

依赖（本机位置，可用环境变量覆盖）：
    JAVA_HOME   → 便携 JRE 17
    ALLURE_BIN  → allure 可执行文件（.bat）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from utils import allure_support
from utils.logger import get_logger

# 本机实测路径（2026-11-01 装好并验证过 `allure --version` → 2.46.1）。
# 不写进 settings 的理由：这是**开发机事实**，不是项目配置；写进去会让 CI 误以为必需。
DEFAULT_JAVA_HOME = Path(r"G:\Program Files\jre-17.0.20.1\jdk-17.0.20.1+1-jre")
DEFAULT_ALLURE_BIN = Path(r"G:\Program Files\allure-2.46.1\allure-2.46.1\bin\allure.bat")


def _resolve() -> tuple[Path, Path]:
    java_home = Path(os.getenv("JAVA_HOME") or DEFAULT_JAVA_HOME)
    allure_bin = Path(os.getenv("ALLURE_BIN") or DEFAULT_ALLURE_BIN)
    if not (java_home / "bin" / "java.exe").is_file():
        raise SystemExit(
            f"JAVA_HOME 无效：{java_home}\n"
            "  需要一份 JRE 8+；本机便携版在 G:\\Program Files\\jre-17.0.20.1\\...\n"
            "  可用环境变量 JAVA_HOME 覆盖。"
        )
    if not allure_bin.is_file():
        raise SystemExit(
            f"找不到 allure CLI：{allure_bin}\n"
            "  解压 allure-2.46.*.zip 后指向其中的 bin/allure.bat；可用 ALLURE_BIN 覆盖。"
        )
    return java_home, allure_bin


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Allure 报告")
    parser.add_argument("--open", action="store_true", help="生成后用默认浏览器打开 index.html")
    parser.add_argument("--serve", action="store_true", help="改为起本地服务预览（可交互看趋势）")
    parser.add_argument(
        "--multi-file",
        action="store_true",
        help="生成传统的多文件报告（需要 HTTP 服务才能看；file:// 打开会报 500 Failed to fetch）",
    )
    args = parser.parse_args()

    log = get_logger()
    results = settings.ALLURE_RESULTS
    report = settings.ALLURE_REPORT
    if not results.is_dir() or not any(results.glob("*-result.json")):
        raise SystemExit(f"没有 Allure 结果：{results}\n先跑一次 pytest（pytest.ini 已带 --alluredir）。")

    java_home, allure_bin = _resolve()
    env = {**os.environ, "JAVA_HOME": str(java_home), "PATH": f"{java_home / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}

    # 关键顺序：先续历史，再 generate。build_history 会读**上一份报告**里的 history/。
    resumed = allure_support.build_history(report)
    log.info("历史续接：%s", "是" if resumed else "否（首次运行）")

    if args.serve:
        cmd = [str(allure_bin), "serve", str(results)]
    else:
        # --clean 只清报告目录，不动结果目录（结果里的 history 是刚续上的）
        cmd = [str(allure_bin), "generate", str(results), "-o", str(report), "--clean"]

    log.info("执行：%s", " ".join(cmd))
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + "\n" + proc.stderr[-2000:] + "\n")
        return proc.returncode

    # 再出一份**单文件**报告，专供双击打开。
    # 为什么两份都要（2026-11-01 实测教训）：
    #   · 多文件报告的数据靠 XHR 读 data/*.json，用 file:// 打开会被浏览器拒绝，
    #     页面只显示「500 Failed to fetch」—— 看起来像报告坏了，其实是打开方式不对；
    #   · 但单文件报告**不落 history 目录**，只留它的话趋势图每次都从零开始。
    # 所以「多文件做底（留 history）+ 单文件做门面（能双击）」是唯一两头都不丢的组合。
    single = None
    if not args.multi_file:
        single_dir = report.parent / "allure-report-single"
        cmd2 = [str(allure_bin), "generate", str(results), "-o", str(single_dir), "--clean", "--single-file"]
        proc2 = subprocess.run(cmd2, env=env, capture_output=True, text=True)
        if proc2.returncode == 0:
            single = single_dir / "index.html"
        else:
            log.warning("单文件报告生成失败（多文件那份仍可用）：%s", proc2.stderr[-500:])

    index = report / "index.html"
    print(f"报告已生成（多文件，需 HTTP 服务）：{index}")
    if single:
        print(f"报告已生成（单文件，可直接双击）：{single}")
    print(f"历史续接：{'是' if resumed else '否'}")
    target = single or index
    if args.open and target.is_file():
        webbrowser.open(target.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
