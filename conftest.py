"""pytest 装配：证据目录、浏览器上下文、blocked→xfail、失败取证。

三条硬约定：
1. tracing 用 retain-on-failure（见 pytest.ini 的 --tracing），失败才留 trace，不做重试遮羞。
2. 任何 call 阶段的失败，都自动截「视口 + 整页」两张图，并把 URL / title 写进报告。
3. blocked 不是 fail。BlockedByAntiBot 只在 blocked_guard() 里转成 xfail；
   普通断言失败原样是 fail，不允许被 xfail 吃掉。

两种「不是代码缺陷的未通过」必须用固定前缀区分，否则门禁统计不出来（规范见 docs/BLOCKED-GATE.md）：
  pytest.xfail("blocked: <signal>")         → 被对方拦下，计入 blocked
  pytest.skip("known-limit: K-xx <原因>")   → 我们自己的已知限制，排除出分母
"""

from __future__ import annotations

import contextlib
import time
import warnings
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from config import settings
from pages.base_page import BasePage, BlockedByAntiBot, BlockSignal
from pages.train_search_page import WidgetUnavailable

# ---------------------------------------------------------------- 会话级

def pytest_configure(config: pytest.Config) -> None:
    settings.ensure_dirs()
    config.addinivalue_line("markers", "smoke: 只打开页面、检测风控、记录标题与 URL，不做任何交互")


# ---------------------------------------------------------------- 统计口径

def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """给每条用例盖上 scope 属性，随 junit 一起落盘，供门禁读取。

    scope=ui_hotels → hotels 轨道（www.ctrip.com 首页酒店模块），账本里独立一条序列
    scope=ui        → 其余浏览器用例（火车票），账本里另一条序列
    scope=ui_auth   → 登录态轨道（2026-11-01 新增）。**刻意不并入上面两条**：
                      它依赖一份人工扫码产生的凭据，CI 里永远 skip，
                      混进任何一条 ratio 序列都会让分母变成「看有没有扫码」。
    scope=unit      → 数据层单测，门禁必须拒收

    **为什么按标记分轨而不是共用一个 ui**（裁决 2026-11-01）：两条轨道打的是
    两个站点，拦截率不可比。共用一个 scope 时，酒店那条 25% 会把火车票的
    1.0 序列打断——G3（连续 3 次 ≥0.3）和 G4（连续 7 天 ≥0.1）会跨轨道串味。
    标记优先取 `hotels`：酒店用例同时带 `ui`（也算浏览器用例），必须先判细分的那条。
    标记优先级从高到低：auth > hotels > ui > unit（越细分越靠前）。

    为什么不用目录名 / classname 判断：junit 的 classname 是 rootdir 相对路径，
    在别的 rootdir 下跑一次就变；`user_properties` 是跟着用例走的，与调用方式无关。
    实测（2026-10-31）：pytest 9.2.0 会把 user_properties 写成
    `<properties><property name="scope" value="ui"/></properties>`，pass / xfail / fail 三种结果都写。
    """
    for item in items:
        if item.get_closest_marker("auth"):
            scope = "ui_auth"
        elif item.get_closest_marker("hotels"):
            scope = "ui_hotels"
        elif item.get_closest_marker("ui"):
            scope = "ui"
        else:
            scope = "unit"
        item.user_properties.append(("scope", scope))


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

    `permissions: []` 是另一回事，它不伪装身份：有头模式会弹出「www.ctrip.com 想要
    获取您的位置」这类原生权限询问框（2026-11-01 截图证据），弹窗在页面上会挡住点击目标，
    让失败原因从「站点行为」变成「弹窗挡路」。空列表 = 预先拒绝，等价于用户点「不允许」，
    对站点的可见结果与用户拒绝完全一致。
    """
    args = {
        **browser_context_args,
        "viewport": settings.VIEWPORT,
        "locale": settings.LOCALE,
        "timezone_id": settings.TIMEZONE_ID,
        "base_url": settings.base_url(),
        "permissions": [],
    }
    if settings.AUTH_GLOBAL and settings.auth_state_path().exists():
        # 默认关，理由见 config/settings.py 的 AUTH_GLOBAL 注释。
        # 文件不存在时静默跳过：全局挂载不该因为缺一份本地凭据就让所有用例炸。
        args["storage_state"] = str(settings.auth_state_path())
    return args


# ---------------------------------------------------------------- 登录态轨道（2026-11-01）

@pytest.fixture(scope="session")
def auth_state() -> Path:
    """登录态文件路径；缺失就 skip 整条 auth 轨道。

    为什么是 skip 不是 fail：这份文件由**真人扫码**产生，CI 里永远不会有
    （config/settings.py 的「登录态」段已写明凭据不进 CI）。用 fail 会让 main 上
    永久挂一条红，红的原因与代码质量无关 —— 那种红最终必然被无视，
    被忽视的门禁等于没有。

    为什么用 known-limit 前缀而不是 blocked：这不是「对方拦我们」，
    是我们自己缺一份本地凭据。blocked 会计入 ratio，known-limit 不进分母。
    """
    path = settings.auth_state_path()
    if not path.exists():
        pytest.skip(
            "known-limit: AUTH-01 未找到登录态文件 "
            f"{path.relative_to(settings.PROJECT_ROOT)}；"
            "先在有头环境运行 python scripts/setup_auth.py"
        )
    return path


@pytest.fixture(scope="session")
def auth_state_age_days(auth_state: Path) -> float:
    """state 文件已存放的天数。只用于提示，绝不作为失效判据。"""
    return (time.time() - auth_state.stat().st_mtime) / 86400


@pytest.fixture
def auth_context(
    browser: Browser,
    auth_state: Path,
    auth_state_age_days: float,
    browser_context_args: dict,
    request: pytest.FixtureRequest,
) -> BrowserContext:
    """带登录态的独立 context，只给 `@pytest.mark.auth` 的用例用。

    为什么独立开 context、而不是给全局 `page` 挂 storage_state：
    酒店轨道的断言依赖「匿名态」这个实验条件（提交路径落 passport、落点写进报告），
    混进登录态就把条件改了，报告里 `login_wall=` 会从 True 变 False ——
    读报告的人会以为登录墙放开了，而不是我们换了张脸。
    要全局生效只能显式设 AUTH_GLOBAL=1，并自己承担上面这个语义变化。
    """
    context = browser.new_context(
        **{**browser_context_args, "storage_state": str(auth_state)}
    )

    stale = auth_state_age_days > settings.AUTH_STATE_TTL_DAYS
    detail = (
        f"文件：{auth_state.name}\n"
        f"已存放：{auth_state_age_days:.1f} 天（建议阈值 {settings.AUTH_STATE_TTL_DAYS} 天）\n"
        f"超阈值：{'是 —— 只提示，不判失效' if stale else '否'}"
    )
    request.node.add_report_section("setup", "登录态", detail)

    if stale:
        # 过期只告警：mtime 说明「存了多久」，说明不了「还有没有效」，
        # 拿它判失效会造出假失败。真失效时下面的断言会红，那才是证据。
        warnings.warn(
            f"登录态已存放 {auth_state_age_days:.1f} 天，超过建议阈值 "
            f"{settings.AUTH_STATE_TTL_DAYS} 天；若断言红，先重跑 scripts/setup_auth.py",
            stacklevel=2,
        )

    yield context
    context.close()


@pytest.fixture
def auth_page(auth_context: BrowserContext) -> Page:
    """登录态 context 里的页面。"""
    page = auth_context.new_page()
    page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
    yield page
    page.close()


@pytest.fixture
def auth_base_page(auth_page: Page) -> BasePage:
    """登录轨道的页面对象，指向 www.ctrip.com。

    用 hotel_base_url() 而不是 base_url()：登录入口在 www 顶部导航
    （2026-11-01 用户截图确认），火车票站 trains.ctrip.com 的登录入口未取证。
    """
    return BasePage(auth_page, base_url=settings.hotel_base_url())


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
            # 前缀固定为 "blocked:"，门禁靠它把 xfail 归类为「被对方拦」。
            pytest.xfail(f"blocked: {exc.signal.kind}（{exc.signal.detail}）")

    return _guard


@pytest.fixture
def widget_guard(request: pytest.FixtureRequest, base_page: BasePage):
    """自定义控件（日历）没按预期响应时转 **skip**，reason 前缀固定 known-limit:。

    与 blocked 的区别（M1-2 改的口径）：blocked 是对方拒绝访问，属于外部事实，
    要进 blocked 统计；本例是「我们自己的控件交互受限」，属于已知限制，
    永远不该算进 blocked，也不该进分母——否则一条 K-01 会让 ratio 永久虚高。

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
            pytest.skip(f"known-limit: K-01 日历控件唤不起（{exc}）")

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
