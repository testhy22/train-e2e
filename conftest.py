"""pytest 装配：证据目录、浏览器上下文、blocked→xfail、失败取证。

三条硬约定：
1. tracing 用 retain-on-failure（见 pytest.ini 的 --tracing），失败才留 trace，不做重试遮羞。
2. 任何 call 阶段的失败，都自动截「视口 + 整页」两张图，并把 URL / title 写进报告。
3. blocked 不是 fail。BlockedByAntiBot 只在 blocked_guard() 里转成 xfail；
   普通断言失败原样是 fail，不允许被 xfail 吃掉。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from playwright.sync_api import Page

from config import settings
from pages.base_page import BasePage, BlockedByAntiBot, BlockSignal
from pages.train_search_page import WidgetUnavailable


# ---------------------------------------------------------------- 会话级

def pytest_configure(config: pytest.Config) -> None:
    settings.ensure_dirs()
    config.addinivalue_line("markers", "smoke: 只打开页面、检测风控、记录标题与 URL，不做任何交互")


# ---------------------------------------------------------------- 浏览器上下文

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """让 settings.HEADLESS 真正生效：HEADED=1 时用有头模式，CI 用无头。

    启动参数保持默认。不加 --disable-blink-features=AutomationControlled 之类的
    自动化隐藏参数：本工程不改变浏览器暴露给站点的身份。
    """
    return {**browser_type_launch_args, "headless": settings.HEADLESS}


@pytest.fixture
def browser_context_args(browser_context_args):
    """在 pytest-playwright 的默认上下文参数上追加视口 / 语言 / 时区。

    刻意不改的：user_agent（默认就是真实 Chromium UA）、任何 stealth 脚本、
    任何 AutomationControlled 相关启动参数。
    """
    return {
        **browser_context_args,
        "viewport": settings.VIEWPORT,
        "locale": settings.LOCALE,
        "timezone_id": settings.TIMEZONE_ID,
        "base_url": settings.base_url(),
    }


@pytest.fixture
def base_page(page: Page, request: pytest.FixtureRequest) -> BasePage:
    """页面对象基类实例，并登记到 item 上供失败钩子取证。"""
    obj = BasePage(page)
    request.node._base_page = obj          # type: ignore[attr-defined]
    return obj


# ---------------------------------------------------------------- blocked → xfail

@pytest.fixture
def blocked_guard(request: pytest.FixtureRequest, base_page: BasePage):
    """把 BlockedByAntiBot 转成 xfail，并留下证据。

    只有 BlockedByAntiBot 会被转；AssertionError / TimeoutError 等照常 fail。
    """

    @contextlib.contextmanager
    def _guard():
        try:
            yield
        except BlockedByAntiBot as exc:
            request.node._blocked_signal = exc.signal       # type: ignore[attr-defined]
            paths = base_page.capture(request.node.nodeid + "__blocked")
            detail = (
                f"判定：{exc.signal.kind}\n命中：{exc.signal.detail}\n"
                f"url：{exc.url}\ntitle：{exc.title}\n"
                f"截图：{', '.join(str(p) for p in paths) or '(截图失败)'}"
            )
            request.node.add_report_section("call", "blocked 证据", detail)
            pytest.xfail(f"blocked by anti-bot: {exc.signal.kind}（{exc.signal.detail}）")

    return _guard


@pytest.fixture
def widget_guard(request: pytest.FixtureRequest, base_page: BasePage):
    """自定义控件（日历）没按预期响应时转 xfail。

    与 blocked 分开：blocked 是对方拒绝访问，这里是页面控件行为不符合预期。
    两种情况都不算通过，但报告里必须能分辨是哪一种。
    只捕获 WidgetUnavailable；断言失败仍然是 fail。
    """

    @contextlib.contextmanager
    def _guard():
        try:
            yield
        except WidgetUnavailable as exc:
            paths = base_page.capture(request.node.nodeid + "__widget")
            request.node.add_report_section(
                "call",
                "控件不可用",
                f"原因：{exc}\nurl：{base_page.page.url}\n截图：{', '.join(str(p) for p in paths) or '(截图失败)'}",
            )
            pytest.xfail(f"控件不可用: {exc}")

    return _guard


# ---------------------------------------------------------------- 失败取证

@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield

    if report.when != "call":
        return report

    blocked: BlockSignal | None = getattr(item, "_blocked_signal", None)
    if blocked is not None:
        # 标记进报告，CI 靠它把 Job 标黄而不是标红
        report.user_properties.append(("blocked", blocked.kind))
        report.sections.append(("blocked", f"{blocked.kind}: {blocked.detail}"))

    base_page: BasePage | None = getattr(item, "_base_page", None)
    if report.failed and base_page is not None:
        page = base_page.page
        try:
            url, title = page.url, page.title()
        except Exception:
            url, title = "(页面已关闭)", "(不可读)"
        paths = base_page.capture(item.nodeid)
        report.sections.append(
            (
                "失败现场",
                f"url：{url}\ntitle：{title}\n截图：{', '.join(str(p) for p in paths) or '(截图失败)'}",
            )
        )

    return report
