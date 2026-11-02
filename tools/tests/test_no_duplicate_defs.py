"""防「同名覆盖」造成的静默漏测。

背景（2026-11-02 真实事故）：`tools/tests/test_train_no_regex.py` 里
`test_depart_clock_of` / `test_in_window` 被写了两份（追加时没检查已有同名函数）。
Python 后者覆盖前者，pytest 收集时**不报错**，报告照样显示绿色 ——
实际少跑了一半参数，属于「假绿灯」里最难发现的一种。

为什么不用 ruff/flake8 的 F811：
  · 本机 `ruff: command not found`，项目也没有 pyproject/setup.cfg/.flake8；
  · 引入一个新依赖只为一个规则，还得同时改 CI 与本地流程；
  · AST 十几行就能判准「同一模块内顶层 `test_` 函数重名」，且能跟着 pytest 一起跑。
  ⇒ 先用零依赖的静态检查兜住，将来真引入 ruff 时这条测试可以退化成双保险，不冲突。

本文件自己也有一个「判据自证」用例：喂一段故意重名的源码进去，检查器必须报出来。
否则 OCR 式的空断言（扫描 0 个文件、或永远返回空列表）会让这条门禁静默失效。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# 与 pytest.ini 的 testpaths 对齐：只扫真正会被收集的两棵树。
SEARCH_DIRS = ("suites", "tools/tests")


def _test_modules() -> list[Path]:
    modules: list[Path] = []
    for relative in SEARCH_DIRS:
        base = ROOT / relative
        if not base.exists():
            continue
        modules.extend(sorted(base.rglob("test_*.py")))
    return modules


def _duplicate_defs(source: str) -> list[tuple[str, int, int]]:
    """返回 [(函数名, 首次行号, 重复行号), ...]；只统计**顶层** `test_` 函数。

    只看顶层：`ClassA.test_x` 与 `ClassB.test_x` 是两条不同的用例，不算重名。
    """
    tree = ast.parse(source)
    seen: dict[str, int] = {}
    duplicates: list[tuple[str, int, int]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        first = seen.get(node.name)
        if first is None:
            seen[node.name] = node.lineno
        else:
            duplicates.append((node.name, first, node.lineno))
    return duplicates


MODULES = _test_modules()


@pytest.mark.parametrize(
    "module", MODULES, ids=[m.relative_to(ROOT).as_posix() for m in MODULES]
)
def test_no_duplicate_test_function(module: Path) -> None:
    """同一模块内不允许出现两个同名顶层 `test_` 函数。"""
    duplicates = _duplicate_defs(module.read_text(encoding="utf-8"))
    detail = "\n".join(
        f"  {name}：第 {first} 行已定义，第 {again} 行又被定义（后者覆盖前者，前者不会被执行）"
        for name, first, again in duplicates
    )
    assert not duplicates, (
        f"{module.relative_to(ROOT).as_posix()} 存在同名测试函数，会静默漏测：\n{detail}"
    )


def test_scan_actually_covers_modules() -> None:
    """扫描范围不能是空的 —— 否则上面的参数化一条都不会生成，门禁等于没有。"""
    assert len(MODULES) >= 5, f"只扫到 {len(MODULES)} 个测试模块，路径口径可能错了"
    assert any("test_train_no_regex" in m.name for m in MODULES), "没扫到已知存在的模块"


def test_detector_catches_planted_duplicate() -> None:
    """判据自证：喂进一段故意重名的源码，检查器必须报出来。"""
    planted = """
def test_alpha():
    assert True

def test_beta():
    assert True

def test_alpha():
    assert True
"""
    duplicates = _duplicate_defs(planted)
    assert duplicates == [("test_alpha", 2, 8)], f"检查器没抓到植入的重名：{duplicates}"


def test_detector_ignores_same_name_in_different_classes() -> None:
    """两个类里各有 `test_x` 是合法写法（各自的用例），不能被误报。"""
    source = """
class TestA:
    def test_x(self):
        pass

class TestB:
    def test_x(self):
        pass
"""
    assert _duplicate_defs(source) == []
