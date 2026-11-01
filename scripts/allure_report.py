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

依赖（**只读环境变量，本文件不含任何本机路径**）：
    JAVA_HOME   → JRE 8+ 根目录
    ALLURE_BIN  → allure 可执行文件；不设则回落到 PATH 上的 allure
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

def _resolve() -> tuple[Path, Path]:
    """解析 JRE 与 allure CLI 的位置。

    **刻意不写任何本机默认路径**（2026-11-01 用户裁决）。原先这里是两条
    写死的本机绝对路径当兜底，看着省事，代价有三个：
      1. 换机器/进 CI/进 Docker 直接失效，报错还指向一个根本不存在的盘符；
      2. 把「某个人的装机位置」固化成项目配置，别人无从判断这是不是必需；
      3. 一台机器上装了新版也不会被用上 —— 兜底值永远赢过 PATH 上的正确版本。

    现在只认环境变量；`ALLURE_BIN` 额外允许回落 `PATH`（官方安装包/包管理器
    装好的情形都走这条，不需要再手动指路径）。缺了就报「该配什么」，不报路径。
    """
    raw_home = (os.getenv("JAVA_HOME") or "").strip()
    if not raw_home:
        raise SystemExit(
            "未设置 JAVA_HOME（Allure CLI 是 Java 程序，必需）。\n"
            "  1) 准备一份 JRE 8+，例如 Adoptium 镜像：\n"
            "     https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jre/x64/windows/\n"
            "  2) 指到 JRE 根目录（其下应有 bin/java）：\n"
            '     Windows：setx JAVA_HOME "<你的 JRE 目录>"   # 之后重开终端\n'
            '     Unix   ：export JAVA_HOME="/path/to/jre"'
        )
    java_home = Path(raw_home)

    raw_bin = (os.getenv("ALLURE_BIN") or "").strip()
    if raw_bin:
        allure_bin = Path(raw_bin)
    else:
        # 不设 ALLURE_BIN 时回落 PATH：包管理器装好的 allure 就走这条，
        # 不需要再手动指一遍路径。找不到才报错。
        found = shutil.which("allure")
        if not found:
            raise SystemExit(
                "找不到 allure CLI：未设置 ALLURE_BIN，且 PATH 里也没有 allure。\n"
                "  1) 从 Maven 中央仓库取（无需翻墙，且带 .sha256）：\n"
                "     https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/\n"
                "  2) 解压后指向其中的 bin/allure.bat（Windows）或 bin/allure（Unix）：\n"
                '     setx ALLURE_BIN "<解压目录>/bin/allure.bat"'
            )
        allure_bin = Path(found)

    has_java = (java_home / "bin" / "java.exe").is_file() or (java_home / "bin" / "java").is_file()
    if not has_java:
        raise SystemExit(
            f"JAVA_HOME 无效：{java_home}\n"
            "  需要 JRE 8+ 的根目录，且其下存在 bin/java（Windows 为 bin/java.exe）。\n"
            "  从 Adoptium 解压后通常还多一层目录，注意别少写一层。"
        )
    if not allure_bin.is_file():
        raise SystemExit(
            f"ALLURE_BIN 指向的文件不存在：{allure_bin}\n"
            "  应为解压目录下的 bin/allure.bat（Windows）或 bin/allure（Unix）。"
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
