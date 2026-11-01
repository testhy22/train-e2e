"""查某个包的真实最新稳定版（SemVer 排序，排除预发布）。

**为什么需要这个脚本**（2026-11-01 实际踩到的坑）：

    PyPI 的 `/simple/<pkg>/` 页面**不保证排序**。肉眼取「末尾那个」拿到的是
    `allure-pytest 2.9.45`，而真正的稳定最新是 `2.16.2`。更糟的是 2.9.x 与
    pytest 9 不兼容 —— 装成功了，跑起来 125 条全 ERROR。
    「列表末尾即最新」这个假设没有任何依据，必须用版本号比较。

**为什么直连 pypi.org 而不走国内镜像**：镜像有同步延迟，查「最新」恰恰需要上游。
查不到时会退回到镜像，并在输出里标明——不静默降级。

用法：
    python scripts/check_latest.py allure-pytest
    python scripts/check_latest.py allure-pytest pytest-playwright
"""

from __future__ import annotations

import json
import sys
import urllib.request

PYPI_JSON = "https://pypi.org/pypi/{pkg}/json"
MIRROR_JSON = "https://pypi.tuna.tsinghua.edu.cn/pypi/{pkg}/json"


def _fetch(url: str, timeout: int = 15) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:                                   # noqa: BLE001
        return None


def _sort_key(v: str):
    """版本排序键。用 packaging；缺它时退回「按数字段比较」的近似实现。

    不直接用字符串排序：`2.9.45` > `2.16.2` 在字典序下成立，在语义化版本下不成立。
    这正是上面那个坑的成因。
    """
    try:
        from packaging.version import parse as _parse

        return _parse(v)
    except Exception:                                   # noqa: BLE001
        parts = []
        for chunk in v.replace("-", ".").split("."):
            parts.append(int(chunk) if chunk.isdigit() else 0)
        return tuple(parts + [0] * (6 - len(parts)))


def _is_prerelease(v: str) -> bool:
    try:
        from packaging.version import parse as _parse

        return _parse(v).is_prerelease
    except Exception:                                   # noqa: BLE001
        low = v.lower()
        return any(tag in low for tag in ("a", "b", "rc", "dev", "post")) and not low.replace(".", "").isdigit()


def latest(pkg: str) -> tuple[str, str]:
    """返回 (版本号, 来源说明)。"""
    for url, source in ((PYPI_JSON.format(pkg=pkg), "pypi.org"),
                        (MIRROR_JSON.format(pkg=pkg), "清华镜像（上游不可达时的降级）")):
        data = _fetch(url)
        if not data:
            continue
        releases = data.get("releases")
        if not releases:
            continue
        stable = [v for v in releases if not _is_prerelease(v) and releases[v]]
        if not stable:
            continue
        return max(stable, key=_sort_key), source
    raise SystemExit(f"查不到 {pkg}：pypi.org 与镜像都不可达，或该包不存在")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for pkg in sys.argv[1:]:
        version, source = latest(pkg)
        print(f"{pkg}=={version}    (来源：{source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
