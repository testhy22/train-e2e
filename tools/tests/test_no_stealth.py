"""红线门禁：不允许把「反检测」写进代码（2026-11-07 用户明确要求「立刻阻止并打回」）。

为什么用一条门禁测试来守，而不是写在文档里：
  约定只对读到它的人有效。这条规矩一旦被破，坏掉的不是某个用例，而是**全部结论** ——
  改过指纹之后，K-05「无头被 432 是有没有显示设备」那组对照就再也说明不了问题，
  报告里的拦截率序列也会失去意义（不知道是站点变了，还是我们自己变了）。
  所以把它变成一条会红的测试。

拦什么（都是「改变对外身份」的动作，不是「读」）：
  1. `add_init_script(...)` —— 唯一的注入入口；本工程没有正当用途（一个都没有）。
  2. `user_agent=` / `extra_http_headers=` —— 覆盖浏览器自报身份或请求头。
  3. 字符串里出现 `navigator.webdriver` **并且** 带 `defineProperty`/`delete` —— 改写指纹。
     **只读不算**：`page.evaluate("() => navigator.webdriver")` 是取证，
     `tools/probes/probe_headless_binary.py` 就靠它出证据，那种用法必须留着。
  4. 打码平台 / stealth 库：`2captcha`、`anticaptcha`、`playwright-stealth` 等。
  5. `--disable-blink-features=AutomationControlled` —— K-05 已实测它不影响 432，
     加上只是自我安慰，却会让人误以为「已经处理过反爬」。

不拦什么（同样重要）：
  · 注释与 docstring 里提到这些词 —— 本文件、`config/settings.py`、`conftest.py`
    都在**解释为什么不这么做**，那些说明恰恰要保留。
  · 有头模式、`permissions: []`、locale/timezone、拟人节奏、节流 —— 都不改变身份。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_SKIP_DIRS = {".venv", "venv", "reports", "__pycache__", "user-data", ".git", "node_modules"}

#: 出现即违规的库/服务名（大小写不敏感）。都是「用第三方能力伪装或代答」。
FORBIDDEN_LIBS = ("2captcha", "anticaptcha", "playwright-stealth", "playwright_stealth",
                  "puppeteer-extra-plugin-stealth")

#: 覆盖身份/请求头的关键字参数名。
FORBIDDEN_KWARGS = ("user_agent", "extra_http_headers")

#: 注入脚本的方法名。
FORBIDDEN_CALLS = ("add_init_script",)


def _python_files() -> list[Path]:
    """全仓 .py，**排除本文件** —— 它自己就写着这些禁词（常量与反面样例）。"""
    me = Path(__file__).resolve()
    files: list[Path] = []
    for path in REPO.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == me:
            continue
        files.append(path)
    return sorted(files)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """收集「docstring 字符串节点」的 id —— 这些是解释性文字，不参与违规判定。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                value = body[0].value
                if isinstance(value.value, str):
                    ids.add(id(value))
    return ids


def _scan(path: Path) -> list[str]:
    """返回该文件的违规说明（空列表 = 干净）。解析失败不判违规，交给别的门禁。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError:
        return []
    docstrings = _docstring_nodes(tree)
    problems: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in FORBIDDEN_CALLS:
                problems.append(f"{path.name}:{node.lineno} 调用了 {name}()（注入脚本）")
            for kw in node.keywords:
                if kw.arg in FORBIDDEN_KWARGS:
                    problems.append(f"{path.name}:{node.lineno} 传了 {kw.arg}=（覆盖身份/请求头）")

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings or node.lineno == 0:
                continue
            text = node.value.lower()
            if any(lib in text for lib in FORBIDDEN_LIBS):
                problems.append(f"{path.name}:{node.lineno} 引用打码/stealth 能力：{node.value[:60]!r}")
            if "navigator" in text and "webdriver" in text and (
                "defineproperty" in text or "delete " in text
            ):
                problems.append(f"{path.name}:{node.lineno} 改写 navigator.webdriver")
            if "disable-blink-features" in text and "automationcontrolled" in text:
                problems.append(
                    f"{path.name}:{node.lineno} 加 AutomationControlled 启动参数"
                    "（K-05 已实测对 432 无效）"
                )
    return problems


def test_no_fingerprint_patching() -> None:
    """全仓扫描：不许出现任何「改变浏览器对外身份」的写法。"""
    problems: list[str] = []
    for path in _python_files():
        problems.extend(_scan(path))
    assert not problems, "发现伪装身份的写法（用户红线，见本文件头）：\n" + "\n".join(problems)


def test_scanner_itself_catches_patch() -> None:
    """自检：把一段反面样例喂给扫描器，必须被判违规。

    没有这条，上面那条测试在「扫描器其实什么都没扫到」时也会绿 —— 假绿比红更贵。
    """
    sample = REPO / "tools" / "tests" / "fixtures" / "_stealth_sample.py.fixture"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(
        "def f(context, page):\n"
        "    context.add_init_script(\"Object.defineProperty(navigator, 'webdriver', {get: () => 1})\")\n"
        "    page.goto('https://x', user_agent='Mozilla/5.0 HeadlessChrome')\n",
        encoding="utf-8",
    )
    try:
        problems = _scan(sample)
    finally:
        sample.unlink(missing_ok=True)
    joined = "\n".join(problems)
    assert "add_init_script" in joined
    assert "user_agent" in joined
    assert "navigator.webdriver" in joined


@pytest.mark.parametrize("allowed", [
    'page.evaluate("() => navigator.webdriver")',      # 只读取证
    'page.goto("https://www.ctrip.com")',
])
def test_reader_is_not_flagged(allowed: str, tmp_path: Path) -> None:
    """只读用法必须放过：否则「取证」会被门禁逼成不可写，等于逼人删证据。"""
    sample = tmp_path / "ok_sample.py"
    sample.write_text(allowed + "\n", encoding="utf-8")
    assert _scan(sample) == []
