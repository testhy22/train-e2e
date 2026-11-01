"""Allure 报告的粘合层：失败证据、环境信息、失败分类、步骤。

**为什么要单独一层，而不是直接在 conftest 里写 `allure.attach(...)`**：

  1. conftest 已经 600 多行，再塞四类附件会让人不敢动它；本模块只做「把东西交给
     Allure」这一件事，conftest 只负责「什么时候交」。
  2. `allure` 是**可选依赖**。CI 的数据层单测 job 没装它也应当照常跑完 —— 所以这里
     统一走 `_HAS_ALLURE` 开关，没装就退化成空操作，绝不让报告能力反过来卡住用例。
     这一点和 trace 的处理一致（见 conftest 的 `_save_e2e_trace`）：证据是辅助，
     不能因为证据能力缺失就把「测试能不能跑」变成前提。

**一个刻意的取舍**：不往每条用例里手写 `with allure.step(...)`。
步骤化只在三个搜索流程的公共层（`utils/search_flow.py`）里做 —— 那里本来就是
「一条业务动作链」，加步骤是顺手的；而在用例里逐行包 step，只会让断言和步骤
互相遮蔽，读起来更像装饰而不是证据。
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
from pathlib import Path
from typing import Any, Iterator

from config import settings
from utils.logger import get_logger

try:                                                    # pragma: no cover
    import allure
    from allure_commons.types import AttachmentType

    _HAS_ALLURE = True
except Exception:                                       # noqa: BLE001
    allure = None                                       # type: ignore[assignment]
    AttachmentType = None                               # type: ignore[assignment]
    _HAS_ALLURE = False


def enabled() -> bool:
    """Allure 是否可用。测试用例可以不关心，钩子里用它决定要不要干活。"""
    return _HAS_ALLURE


@contextlib.contextmanager
def step(title: str) -> Iterator[None]:
    """一个 Allure 步骤；没装 Allure 时就是空上下文。

    用 `allure.step` 而不是手工 `step_ctx`，是为了让「步骤里抛异常 ⇒ 该步骤标红」
    这个行为由 Allure 自己保证，不靠我们复刻。
    """
    if not _HAS_ALLURE:
        yield
        return
    with allure.step(title):                            # type: ignore[union-attr]
        yield


def label(
    *,
    epic: str | None = None,
    feature: str | None = None,
    story: str | None = None,
    severity: str | None = None,
    title: str | None = None,
) -> None:
    """在**运行时**给当前用例打标签（`allure.dynamic.*`）。

    为什么不全用装饰器：本工程用例分散在 5 个轨道、几十个文件里，逐个加装饰器
    会制造大量「加了一半」的漂移点。轨道的口径本来就已经集中在
    `conftest.pytest_collection_modifyitems` 的 scope 计算里，这里复用同一套判据，
    用一条 autouse fixture 统一打 epic/feature —— 新增用例自动继承，不需要记得加。
    需要更细语义（某条用例的 story / title）时再在用例上写装饰器覆盖。
    """
    if not _HAS_ALLURE:
        return
    try:
        if epic:
            allure.dynamic.epic(epic)                   # type: ignore[union-attr]
        if feature:
            allure.dynamic.feature(feature)             # type: ignore[union-attr]
        if story:
            allure.dynamic.story(story)                 # type: ignore[union-attr]
        if title:
            allure.dynamic.title(title)                 # type: ignore[union-attr]
        if severity:
            allure.dynamic.severity(getattr(allure.severity_level, severity))  # type: ignore[union-attr]
    except Exception as exc:                            # noqa: BLE001
        get_logger().debug("Allure 标签设置失败（忽略）：%s", exc)


def attach_text(name: str, body: str) -> None:
    if not _HAS_ALLURE or not body:
        return
    try:
        allure.attach(body, name=name, attachment_type=AttachmentType.TEXT)   # type: ignore[union-attr]
    except Exception as exc:                            # noqa: BLE001
        get_logger().debug("Allure 文本附件失败（忽略）：%s", exc)


def attach_file(path: Path | str | None, name: str, mime: str = "application/octet-stream") -> None:
    """挂一个已落盘的文件（截图 / trace / 日志）。

    文件不存在就静默跳过：失败取证本身可能失败，取证失败不该再造一个错误。
    """
    if not _HAS_ALLURE or path is None:
        return
    p = Path(path)
    if not p.is_file():
        return
    try:
        allure.attach.file(str(p), name=name, attachment_type=mime)           # type: ignore[union-attr]
    except Exception as exc:                            # noqa: BLE001
        get_logger().debug("Allure 文件附件失败（忽略）：%s", exc)


def attach_screenshot(page: Any, name: str = "失败截图") -> None:
    """直接从 page 取 PNG 字节挂上去，不落盘再读。

    为什么不用已有的 `BasePage.capture()` 产出的文件：那两张图（视口 + 整页）
    已经落在 `reports/screenshots/` 供人直接翻，而 Allure 里再挂一遍同一份
    会让报告体积翻倍。这里只挂**视口图**，整页图留在磁盘上做深度排查。
    """
    if not _HAS_ALLURE or page is None:
        return
    try:
        png = page.screenshot(full_page=False)
    except Exception as exc:                            # noqa: BLE001
        get_logger().debug("Allure 截图失败（忽略）：%s", exc)
        return
    try:
        allure.attach(png, name=name, attachment_type=AttachmentType.PNG)     # type: ignore[union-attr]
    except Exception as exc:                            # noqa: BLE001
        get_logger().debug("Allure 截图附件失败（忽略）：%s", exc)


def write_environment(extra: dict[str, str] | None = None) -> Path | None:
    """写 `environment.properties`：让每份报告都自证「什么环境跑出来的」。

    为什么必须写：Allure 的 Overview 只有通过率，没有环境信息时，一份历史报告
    过两周就没人说得清「当时用的是哪个 base_url、哪个登录态」。
    文件名固定为 `environment.properties`，是 Allure 的约定，不是我们的选择。
    """
    if not _HAS_ALLURE:
        return None
    # 版本号从包元数据取：`playwright` 模块**没有** `__version__`
    # （2026-11-01 实测 ImportError），别用 `from playwright import __version__`。
    try:
        from importlib.metadata import version as _pkg_version

        pw_version = _pkg_version("playwright")
    except Exception:                                   # noqa: BLE001
        pw_version = "unknown"

    data: dict[str, str] = {
        "Project": settings.PROJECT_ROOT.name,
        "Browser": "Chromium（Playwright 自带，未改 UA / 未加指纹参数）",
        "Headless": str(settings.HEADLESS),
        "BaseURL_trains": settings.base_url(),
        "BaseURL_hotels": settings.hotel_base_url(),
        "BaseURL_flights": settings.flight_base_url(),
        "Python": platform.python_version(),
        "Playwright": pw_version,
        "Platform": f"{platform.system()} {platform.release()}",
        "CI": os.getenv("CI", "false"),
    }
    data.update(extra or {})
    path = settings.ALLURE_RESULTS / "environment.properties"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(f"{k}={v}" for k, v in data.items() if v != "") + "\n",
            encoding="utf-8",
        )
    except Exception as exc:                            # noqa: BLE001
        get_logger().warning("写 environment.properties 失败（忽略）：%s", exc)
        return None
    return path


# 失败分类。键名是给人看的，`matchedStatuses` 是 Allure 的枚举。
# **顺序有意义**：Allure 从上往下匹配，先命中先归类。
_CATEGORIES: list[dict[str, Any]] = [
    {
        "name": "对方拦截（WAF / 风控 / 登录墙）",
        # blocked 在我们的口径里走 xfail，正常不会出现在 failed 里；
        # 但「守卫没兜住的拦截」会以 failed 出现，这里按消息特征接住它，
        # 免得把对方的行为算成产品缺陷。
        "matchedStatuses": ["failed", "broken"],
        "messageRegex": ".*(blocked|whaleguard|passport\\.ctrip\\.com|人机|验证码).*",
    },
    {
        "name": "脚本问题（超时 / 选择器）",
        "matchedStatuses": ["failed", "broken"],
        "traceRegex": ".*(TimeoutError|Timeout [0-9]+ms exceeded|waiting for locator|strict mode violation).*",
    },
    {
        "name": "断言不符（先当产品缺陷看）",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*(AssertionError|assert).*",
    },
    {
        "name": "环境或数据问题",
        "matchedStatuses": ["broken"],
    },
]


def write_categories() -> Path | None:
    """写 `categories.json`：把「红了几条」变成「红在哪一类」。

    这几类的边界按本项目既有口径定，不是通用模板：
      · 对方拦截 —— 对应 docs/BLOCKED-GATE.md 的 blocked 语义；
      · 脚本问题 —— 超时与选择器不稳，属于我们自己的技术债；
      · 断言不符 —— 默认按产品缺陷看，但**必须人看**：K-04 证明过
        「断言红」也可能是站点把匿名态送去登录墙，不是产品坏了。
    """
    if not _HAS_ALLURE:
        return None
    path = settings.ALLURE_RESULTS / "categories.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_CATEGORIES, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:                            # noqa: BLE001
        get_logger().warning("写 categories.json 失败（忽略）：%s", exc)
        return None
    return path


def build_history(destination: Path | None = None) -> bool:
    """把上一份报告的 history 续到本次结果目录，让趋势图接得上。

    Allure 2 的趋势数据不是自动累积的：`allure generate` 会从
    `<results>/history` 读上一次的 `history.json`，生成后再把新的写回报告目录。
    所以「跑完就 generate」永远只有一根柱子 —— 必须在下一次**运行前**把上一份
    报告的 `history/` 拷回结果目录。这个顺序没有捷径。

    返回是否真的续上了历史（第一次跑必然 False，这不是错误）。
    """
    report = destination or settings.ALLURE_REPORT
    src = report / "history"
    dst = settings.ALLURE_RESULTS / "history"
    if not src.is_dir():
        return False
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in src.iterdir():
        if item.is_file():
            (dst / item.name).write_bytes(item.read_bytes())
            copied += 1
    get_logger().info("Allure 历史已续：%s → %s（%d 个文件）", src, dst, copied)
    return copied > 0
