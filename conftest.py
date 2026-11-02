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
import os
import re
import time
import warnings
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from config import settings
from pages.base_page import BasePage, BlockedByAntiBot, BlockSignal
from pages.train_search_page import WidgetUnavailable
from utils import allure_support
from utils.auth import merged_storage_state
from utils.cleaner import clean_old_reports
from utils.waf import check_waf_block as _check_waf_block
from utils.diagnostics import diagnostics_of, forget_page, instrument_page
from utils.logger import get_logger, log_file_path

# ---------------------------------------------------------------- 会话级

def pytest_configure(config: pytest.Config) -> None:
    settings.ensure_dirs()
    config.addinivalue_line("markers", "smoke: 只打开页面、检测风控、记录标题与 URL，不做任何交互")


def pytest_sessionstart(session: pytest.Session) -> None:
    """会话最早期：建日志、清旧产物、把这次运行的关键条件记一行。

    为什么放 `sessionstart` 而不是某个 fixture 里：清理必须在**任何文件写入之前**
    完成，否则刚创建的新日志可能被当成「旧文件」参与统计；而 fixture 是按用例
    惰性触发的，第一条用例是什么不确定，卡不住这个时机。
    """
    log = get_logger()
    log.info("=" * 72)
    log.info("测试会话启动｜headless=%s｜base_url=%s", settings.HEADLESS, settings.base_url())
    log.info("日志文件：%s", log_file_path())
    state = settings.auth_state_path()
    log.info("登录态：%s（%s）", "存在" if state.exists() else "缺失",
             state.name if state.exists() else "登录态轨道将 skip")
    # Allure：续历史 → 写环境信息 → 写失败分类。
    # 顺序不能反：续历史必须先于本次写入，否则新结果会先落到目录里，
    # generate 时就分不清哪些是上一轮的 history。
    # **整段包 try**：这是在 sessionstart 里跑的，一旦抛异常 pytest 会以
    # INTERNALERROR 整体崩掉（2026-11-01 真发生过一次：playwright 没有 __version__）。
    # 报告能力不该有权限让测试跑不起来。
    try:
        if allure_support.enabled():
            resumed = allure_support.build_history()
            env = allure_support.write_environment({"HistoryResumed": str(resumed)})
            cats = allure_support.write_categories()
            log.info(
                "Allure：结果目录 %s｜历史续接=%s｜environment=%s｜categories=%s",
                settings.ALLURE_RESULTS,
                "是" if resumed else "否（首次运行，趋势图从本次开始）",
                env.name if env else "(未写)",
                cats.name if cats else "(未写)",
            )
        else:
            log.info("Allure：未安装 allure-pytest，本次不产 Allure 结果（不影响用例）")
    except Exception as exc:                      # noqa: BLE001
        log.warning("Allure 初始化失败（已忽略，不影响用例）：%s", exc)
    # 清理失败不允许影响会话：cleaner 自己吞掉了所有异常，这里再加一层保险。
    try:
        clean_old_reports()
    except Exception as exc:                      # noqa: BLE001
        log.warning("清理旧产物时出错（已忽略）：%s", exc)


# ---------------------------------------------------------------- 统计口径

def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """给每条用例盖上 scope 属性，随 junit 一起落盘，供门禁读取。

    scope=ui_hotels → hotels 轨道（www.ctrip.com 首页酒店模块），账本里独立一条序列
    scope=ui        → 其余浏览器用例（火车票），账本里另一条序列
    scope=ui_auth   → 登录态轨道（2026-11-01 新增）。**刻意不并入上面两条**：
                      它依赖一份人工扫码产生的凭据，CI 里永远 skip，
                      混进任何一条 ratio 序列都会让分母变成「看有没有扫码」。
    scope=ui_e2e    → 跨模块串联轨道（2026-11-01 新增）。实验条件是「一个 context
                      连续跑完三站」，与上面每条一个干净 context 的形态互斥，
                      所以也单列一条，不与 ui_auth 合并。
    scope=unit      → 数据层单测，门禁必须拒收

    **为什么按标记分轨而不是共用一个 ui**（裁决 2026-11-01）：两条轨道打的是
    两个站点，拦截率不可比。共用一个 scope 时，酒店那条 25% 会把火车票的
    1.0 序列打断——G3（连续 3 次 ≥0.3）和 G4（连续 7 天 ≥0.1）会跨轨道串味。
    标记优先取细分的那条：e2e > auth > hotels > ui > unit。

    为什么不用目录名 / classname 判断：junit 的 classname 是 rootdir 相对路径，
    在别的 rootdir 下跑一次就变；`user_properties` 是跟着用例走的，与调用方式无关。
    实测（2026-10-31）：pytest 9.2.0 会把 user_properties 写成
    `<properties><property name="scope" value="ui"/></properties>`，pass / xfail / fail 三种结果都写。
    """
    for item in items:
        if item.get_closest_marker("e2e"):
            scope = "ui_e2e"
        elif item.get_closest_marker("auth"):
            scope = "ui_auth"
        elif item.get_closest_marker("hotels"):
            scope = "ui_hotels"
        elif item.get_closest_marker("ui"):
            scope = "ui"
        else:
            scope = "unit"
        item.user_properties.append(("scope", scope))


# ---------------------------------------------------------------- Allure 标签

# scope → 报告里的业务模块名。键与 `pytest_collection_modifyitems` 算出的 scope 一致。
_FEATURE_BY_SCOPE = {
    "ui_e2e": "跨模块串联（登录态三站）",
    "ui_auth": "登录态搜索",
    "ui_hotels": "酒店搜索",
    "ui": "火车票与第三方站点",
    "unit": "数据层与门禁自检",
}


@pytest.fixture(autouse=True)
def _allure_labels(request: pytest.FixtureRequest) -> None:
    """给每条用例统一打 epic/feature，让报告按业务模块分层。

    为什么用 autouse fixture 而不是逐文件装饰器：见 `utils/allure_support.label`
    的说明 —— 口径集中在 scope 一处，新用例自动继承，不会出现「加了一半」。
    装饰器只在需要更细的 story/title 时才补。
    """
    if not allure_support.enabled():
        return
    marker_names = {m.name for m in request.node.iter_markers()}
    if "e2e" in marker_names:
        scope = "ui_e2e"
    elif "auth" in marker_names:
        scope = "ui_auth"
    elif "hotels" in marker_names:
        scope = "ui_hotels"
    elif "ui" in marker_names:
        scope = "ui"
    else:
        scope = "unit"
    # story 必须有默认值（2026-11-01 实测）：Allure 的 Behaviors 树是
    # epic → feature → story 三层，只到 feature 的用例**不会进树** ——
    # 首轮 125 条里只有 2 条出现在 Behaviors 页，其余全丢。
    # 默认 story 取用例所在模块名；带 e2e 标记的用例已有 @allure.story 装饰器，
    # 这里**必须让位**，否则会多出一层 module 名的多余节点（同一用例两个 story）。
    story = None if scope == "ui_e2e" else getattr(
        getattr(request.node, "parent", None), "name", "未分组"
    )
    allure_support.label(
        epic="携程网页自动化测试",
        feature=_FEATURE_BY_SCOPE.get(scope, scope),
        story=story,
        severity="CRITICAL" if scope == "ui_e2e" else "NORMAL",
    )


# ---------------------------------------------------------------- 浏览器上下文

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """让 settings.HEADLESS 真正生效：HEADED=1 时用有头模式，CI 用无头。

    只追加 `settings.BROWSER_UI_ARGS`（关翻译条 / 首次运行引导 / 密码保存气泡）。
    这些是**浏览器自己的浮层开关**，不是自动化隐藏参数 —— 不加
    --disable-blink-features=AutomationControlled 之类，本工程不改变浏览器
    暴露给站点的身份。两者边界见 config/settings.py 的 BROWSER_UI_ARGS 注释。
    """
    existing = list(browser_type_launch_args.get("args") or [])
    # `--start-maximized` 已在 settings.BROWSER_UI_ARGS 里（与探针共用同一份）。
    # 与 _common_context_args 的 no_viewport 是**一对**，缺一个就不生效：
    # 只有 --start-maximized 没有 no_viewport → 窗口最大了，但页面仍按固定视口排版；
    # 只有 no_viewport 没有 --start-maximized → 视口跟随窗口，而窗口是默认大小。
    return {
        **browser_type_launch_args,
        "headless": settings.HEADLESS,
        "args": [*existing, *settings.BROWSER_UI_ARGS],
    }


def _common_context_args() -> dict:
    """context 的公共参数（function 级与 session 级两处共用）。

    抽出来的理由不是整洁：session 级共享 context 不能依赖
    `browser_context_args`（那是 function 级 fixture，依赖它会 ScopeMismatch），
    所以它只能自己拼一份参数。两处各拼一份的话，视口/语言/时区的口径迟早漂移，
    而漂移的表现是「共享会话里布局与单用例里不一样」这种极难查的问题。

    视口策略（2026-11-01 用户追加要求「物理最大化」）：
      · 有头 → `no_viewport=True` + 启动参数 `--start-maximized`，
        视口完全跟随物理窗口。**不能同时写 viewport** —— 一旦给了固定 viewport，
        Playwright 会用它覆盖窗口内容区，最大化就白做了；
      · 无头 → 仍然固定 `settings.VIEWPORT`。headless 没有窗口可最大化，
        `no_viewport` 会把页面退到 1280×720，比 1920 更小，
        而本站的筛选区的折叠/平铺正好在这两个宽度之间切换。
    """
    args = {
        "locale": settings.LOCALE,
        "timezone_id": settings.TIMEZONE_ID,
        "base_url": settings.base_url(),
        # permissions=[] 是预先拒绝位置权限，理由见下面 browser_context_args 的注释。
        "permissions": [],
        **settings.context_view_args(),
    }
    return args


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
    args = {**browser_context_args, **_common_context_args()}
    if settings.AUTH_GLOBAL and settings.auth_state_path().exists():
        # 默认关，理由见 config/settings.py 的 AUTH_GLOBAL 注释。
        # 文件不存在时静默跳过：全局挂载不该因为缺一份本地凭据就让所有用例炸。
        args["storage_state"] = str(settings.auth_state_path())
    return args


# ---------------------------------------------------------------- 登录态轨道（2026-11-01）

def _require_auth_state(entry: str, code: str) -> Path:
    """取某个入口站的 state 文件路径；缺失就 skip 整条轨道。

    为什么是 skip 不是 fail：这份文件由**真人扫码**产生，CI 里永远不会有
    （config/settings.py 的「登录态」段已写明凭据不进 CI）。用 fail 会让 main 上
    永久挂一条红，红的原因与代码质量无关 —— 那种红最终必然被无视，
    被忽视的门禁等于没有。

    为什么用 known-limit 前缀而不是 blocked：这不是「对方拦我们」，
    是我们自己缺一份本地凭据。blocked 会计入 ratio，known-limit 不进分母。

    为什么按入口分文件（2026-11-01 实测）：www 的 state 打开 trains.ctrip.com
    仍判未登录 —— 两站登录态不互通。共用一份文件会让 `--entry trains`
    静默覆盖掉 www 会话，之后 www 用例莫名报「未登录」而没人知道为什么。
    """
    path = settings.auth_state_path(entry)
    if not path.exists():
        flag = "" if entry == "www" else f" --entry {entry}"
        pytest.skip(
            f"known-limit: {code} 未找到登录态文件 "
            f"{path.relative_to(settings.PROJECT_ROOT)}；"
            f"先在有头环境运行 python scripts/setup_auth.py{flag}"
        )
    return path


def _build_auth_context(
    browser: Browser,
    state: Path,
    context_args: dict,
    request: pytest.FixtureRequest,
    label: str,
) -> BrowserContext:
    """用指定 state 开一个独立 context。

    为什么独立开 context、而不是给全局 `page` 挂 storage_state：
    酒店轨道的断言依赖「匿名态」这个实验条件（提交路径落 passport、落点写进报告），
    混进登录态就把条件改了，报告里 `login_wall=` 会从 True 变 False ——
    读报告的人会以为登录墙放开了，而不是我们换了张脸。
    要全局生效只能显式设 AUTH_GLOBAL=1，并自己承担上面这个语义变化。
    """
    context = browser.new_context(**{**context_args, "storage_state": str(state)})

    age_days = (time.time() - state.stat().st_mtime) / 86400
    stale = age_days > settings.AUTH_STATE_TTL_DAYS
    detail = (
        f"文件：{state.name}\n"
        f"入口：{label}\n"
        f"已存放：{age_days:.1f} 天（建议阈值 {settings.AUTH_STATE_TTL_DAYS} 天）\n"
        f"超阈值：{'是 —— 只提示，不判失效' if stale else '否'}"
    )
    request.node.add_report_section("setup", "登录态", detail)

    if stale:
        # 过期只告警：mtime 说明「存了多久」，说明不了「还有没有效」，
        # 拿它判失效会造出假失败。真失效时下面的断言会红，那才是证据。
        warnings.warn(
            f"登录态（{state.name}）已存放 {age_days:.1f} 天，超过建议阈值 "
            f"{settings.AUTH_STATE_TTL_DAYS} 天；若断言红，先重跑 scripts/setup_auth.py",
            stacklevel=2,
        )
    return context


@pytest.fixture(scope="session")
def auth_state() -> Path:
    """www 站登录态文件（默认入口）。"""
    return _require_auth_state("www", "AUTH-01")


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
    """带 www 站登录态的独立 context，只给 `@pytest.mark.auth` 的用例用。"""
    context = _build_auth_context(browser, auth_state, browser_context_args, request, "www")
    yield context
    context.close()


@pytest.fixture
def auth_page(auth_context: BrowserContext) -> Page:
    """登录态 context 里的页面。"""
    page = auth_context.new_page()
    page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
    instrument_page(page, label="auth_page")
    yield page
    forget_page(page)
    page.close()


@pytest.fixture
def auth_base_page(auth_page: Page) -> BasePage:
    """登录轨道的页面对象，指向 www.ctrip.com。

    用 hotel_base_url() 而不是 base_url()：登录入口在 www 顶部导航
    （2026-11-01 用户截图确认）。火车票站是另一条轨道，见 auth_trains_base_page。
    """
    return BasePage(auth_page, base_url=settings.hotel_base_url())


# ---- 火车票站登录态（2026-11-01 新增，两站登录态不互通）

@pytest.fixture(scope="session")
def auth_state_trains() -> Path:
    """trains 站登录态文件，由 `setup_auth.py --entry trains` 单独采集。"""
    return _require_auth_state("trains", "AUTH-03")


@pytest.fixture
def auth_trains_context(
    browser: Browser,
    auth_state_trains: Path,
    browser_context_args: dict,
    request: pytest.FixtureRequest,
) -> BrowserContext:
    """带火车票站登录态的独立 context。

    与 auth_context 分开的理由不是洁癖：www 那份 state 打开 trains 站时，
    顶部账号区是「登录 / 注册」，用它跑 trains 用例会稳定判未登录，
    看起来像「退出功能坏了」或「state 失效」，实际只是拿错了文件。
    """
    context = _build_auth_context(
        browser, auth_state_trains, browser_context_args, request, "trains"
    )
    yield context
    context.close()


@pytest.fixture
def auth_trains_page(auth_trains_context: BrowserContext) -> Page:
    page = auth_trains_context.new_page()
    page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
    instrument_page(page, label="auth_trains_page")
    yield page
    forget_page(page)
    page.close()


@pytest.fixture
def auth_trains_base_page(auth_trains_page: Page) -> BasePage:
    """火车票站登录轨道的页面对象。

    **无头下会被 WAF 拦**（K-05：整页只剩 `whaleguard block`，body 约 17 字符），
    所以引用它的用例必须用 `blocked_guard()`：`open_and_check` 识别到拦截信号
    会抛 BlockedByAntiBot，guard 转成 xfail 计入 blocked，而不是记成断言失败。
    """
    return BasePage(auth_trains_page, base_url=settings.base_url())


# ---------------------------------------------------------------- 共享会话（e2e 串联轨道，2026-11-01）
#
# 用户 2026-11-01 要求：**一次登录、一个浏览器、一个 context、一个 page**，
# 依次跑完酒店 → 火车 → 机票，多组数据参数化，单条失败不中断后续。
#
# 为什么不能直接用 pytest-playwright 的 `page`：它是 function 级 —— 每条用例新建
# context，9 组数据就是 9 次冷启动 + 9 次加载 storage_state，与「严禁每个用例
# 重新启动浏览器或重新登录」正面冲突。
#
# 与 `auth_context` 的分工：auth_context 是 function 级，一条用例一个干净 context，
# 适合「这条用例能否登录」类断言；共享会话适合「一条流程走到底」。两者并存，
# 谁也替代不了谁。

class SharedSession:
    """一个 session 只开一个 context、一个 page；page 崩了重建 page，context 不动。

    为什么 page 可以重建而 context 不能：
      page 出问题是单页问题（超时、被重定向到错误页），重开一页就够；
      context 一换就得重新加载 storage_state —— 那等于重新登录一次，
      正是这条轨道要避免的事。重建次数记进报告，异常增长能被看见。
    """

    def __init__(self, context: BrowserContext) -> None:
        self.context = context
        self._page: Page | None = None
        self.rebuilds = 0

    def get_page(self) -> Page:
        if self._page is None:
            self._page = self.context.new_page()
        elif self._page.is_closed():
            self._page = self.context.new_page()
            self.rebuilds += 1
        self._page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
        self._page.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
        # 共享 page 也要挂诊断：e2e 轨整场只有一个 page，漏挂等于整轨没有 JS/4xx 证据。
        instrument_page(self._page, label="shared_page")
        return self._page

    def drop_extra_pages(self, keep: Page) -> None:
        """关掉意外弹出的标签页。

        不关的后果很具体：站点把某次搜索开在新标签里，下一次 `goto` 仍作用在旧页上，
        失败会表现成「搜索没结果」，排查方向从「会话」直接歪到「选择器」。
        """
        for other in list(self.context.pages):
            if other is not keep and not other.is_closed():
                other.close()

    def close(self) -> None:
        if self._page is not None and not self._page.is_closed():
            self._page.close()


@pytest.fixture(scope="session")
def shared_context(browser: Browser) -> BrowserContext:
    """session 级登录态 context。缺 state 时整条轨道 skip（AUTH-06，不进分母）。

    **三站一次性登录**（2026-11-01 用户要求「只登录一次、每个功能不重启浏览器」）：
    这里加载的是**合并后的** state —— www 打底，再补火车票那份里的
    `trains.ctrip.com` 专属 cookie。只加载 www 那份的后果是实测过的：飞机票、
    酒店绿，火车票整站回到「登录/注册」（K-07 两站不互通）。
    合并策略与三选一的实测对照见 `utils/auth.merged_storage_state`。

    Trace 说明：pytest-playwright 的 `--tracing=retain-on-failure` 只作用于它自己
    造的函数级 context，**管不到这个手搓的 session context**，所以 e2e 轨默认没有
    trace。要录就设 `E2E_TRACE=1`，失败时存到 `reports/trace/<用例>.zip`。
    默认关的理由很实在：整场只有一个 context，全程录制能到几十 MB，
    而 e2e 的失败现场已有截图 + DOM + 4xx 清单（见 `_report_diagnostics`）。
    """
    state = _require_auth_state("www", "AUTH-06")
    trains_state = settings.auth_state_path("trains")
    merged = merged_storage_state(state, trains_state)
    was_merged = bool(merged.pop("_merged", False))
    get_logger().info(
        "共享会话登录态：%s + %s ⇒ cookies=%d（跨站合并=%s）",
        state.name,
        trains_state.name if was_merged else "(缺失，火车票将未登录)",
        len(merged["cookies"]),
        "是" if was_merged else "否",
    )
    context = browser.new_context(**{**_common_context_args(), "storage_state": merged})
    if os.getenv("E2E_TRACE") == "1":
        try:
            context.tracing.start(screenshots=True, snapshots=True, sources=False)
            context._e2e_trace_active = True      # type: ignore[attr-defined]
            get_logger().info("trace 已开启（E2E_TRACE=1），失败时保存")
        except Exception as exc:                  # noqa: BLE001
            get_logger().warning("trace 启动失败（忽略）：%s", exc)
    yield context
    context.close()


@pytest.fixture(scope="session")
def shared_session(shared_context: BrowserContext) -> SharedSession:
    session = SharedSession(shared_context)
    yield session
    session.close()


@pytest.fixture
def shared_page(shared_session: SharedSession, request: pytest.FixtureRequest) -> Page:
    """共享 context 里那个页面的**当前**实例。

    名字是 function 级，但浏览器与 context 都是 session 级 —— 调用方拿到的是同一个
    会话里的同一个页；只有页真的崩了才重建（重建次数写进报告）。
    """
    page = shared_session.get_page()
    # 把真实视口尺寸写进报告：最大化到底生效没有，靠这个数字，不靠感觉。
    # 期望值 = 物理窗口内容区（1920 屏常见 1536~1920，取决于 DPI 缩放）。
    try:
        width, height = page.evaluate("() => [window.innerWidth, window.innerHeight]")
        request.node.add_report_section(
            "setup",
            "视口",
            f"innerWidth×innerHeight = {width}×{height}\n"
            f"物理最大化：{settings.window_maximized()}（HEADED={not settings.HEADLESS}）",
        )
    except Exception:
        pass
    yield page
    shared_session.drop_extra_pages(page)


def check_waf_block(page: Page) -> BlockSignal | None:
    """用户需求里点名的守卫：命中风控返回信号，未命中返回 None。

    **实现已搬到 `utils/waf.py`**（2026-11-01）：这里只保留同名转发，因为既有用例
    仍写 `from conftest import check_waf_block`。搬家的原因是实测的 import 顺序耦合：
    `tools/tests` 与 `suites` 一起收集时，`conftest` 会被先导入的那个占用，
    报 `ImportError: cannot import name 'check_waf_block' from 'conftest'`。
    新代码请直接 `from utils.waf import check_waf_block`。
    """
    return _check_waf_block(page)


@pytest.fixture
def base_page(page: Page, request: pytest.FixtureRequest) -> BasePage:
    """页面对象基类实例，并登记到 item 上供失败钩子取证。"""
    obj = BasePage(page)
    request.node._base_page = obj          # type: ignore[attr-defined]
    return obj


# ---------------------------------------------------------------- blocked → xfail

@pytest.fixture
def blocked_guard(request: pytest.FixtureRequest):
    """把 BlockedByAntiBot 转成 xfail，并留下证据。

    只有 BlockedByAntiBot 会被转；AssertionError / TimeoutError 等照常 fail。

    **取证对象是惰性取的**（2026-11-01 改）：优先用用例自己登记在
    `item._base_page` 上的页面对象，没有才回退去拉 `base_page` fixture。
    原先直接在签名里依赖 `base_page`，副作用有两个，都是实打实的：
      · 登录态轨道（`auth_page`/`auth_trains_page`）每调一次 guard 就**多起一个
        匿名浏览器 context** —— 而 guard 本身对匿名页毫无用处；
      · blocked 截图截的是那个空白匿名页，不是真正被拦的页面，取证等于白取。
    惰性取法对既有匿名用例零影响：它们没登记 `_base_page`，照样回退到 `base_page`。
    """

    @contextlib.contextmanager
    def _guard():
        try:
            yield
        except BlockedByAntiBot as exc:
            request.node._blocked_signal = exc.signal       # type: ignore[attr-defined]
            holder = getattr(request.node, "_base_page", None)
            if holder is None:
                holder = request.getfixturevalue("base_page")
            paths = holder.capture(request.node.nodeid + "__blocked")
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
def widget_guard(request: pytest.FixtureRequest):
    """自定义控件（日历）没按预期响应时转 **skip**，reason 前缀固定 known-limit:。

    与 blocked 的区别（M1-2 改的口径）：blocked 是对方拒绝访问，属于外部事实，
    要进 blocked 统计；本例是「我们自己的控件交互受限」，属于已知限制，
    永远不该算进 blocked，也不该进分母——否则一条 K-01 会让 ratio 永久虚高。

    只捕获 WidgetUnavailable；断言失败仍然是 fail。

    **取证对象改成惰性取（2026-11-02）**，与 blocked_guard 同款改法：
    原先签名直接依赖 `base_page`，而 `base_page` 依赖 function 级 `page`
    ⇒ 任何引用本 guard 的用例都会**多起一个浏览器 context**。往返/中转改走
    共享会话（`shared_page`，一次登录跑完）之后，这一点会当场把「只登录一次」破掉。
    改成先读 `item._base_page`、没有再回退 `base_page`：对单程那类匿名用例零影响
    （它们用的 `page` 与 `base_page` 是同一次 fixture 缓存，不会多开页）。
    """

    @contextlib.contextmanager
    def _guard():
        try:
            yield
        except WidgetUnavailable as exc:
            holder = getattr(request.node, "_base_page", None)
            if holder is None:
                holder = request.getfixturevalue("base_page")
            paths = holder.capture(request.node.nodeid + "__widget")
            request.node.add_report_section(
                "call",
                "控件不可用",
                f"原因：{exc}\nurl：{holder.page.url}\n截图：{', '.join(str(p) for p in paths) or '(截图失败)'}",
            )
            pytest.skip(f"known-limit: K-01 日历控件唤不起（{exc}）")

    return _guard


# ---------------------------------------------------------------- 失败取证

def _pages_of(item: pytest.Item) -> list[Page]:
    """用例可能用到的 page，按「最可能是现场」的顺序返回。

    先看用例自己登记的页面对象（`_base_page`），再退回到 fixture 里的裸 page。
    后者覆盖「还没构造页面对象就挂了」的情况 —— 那种失败以前一张图都没有。
    """
    pages: list[Page] = []
    holder: BasePage | None = getattr(item, "_base_page", None)
    if holder is not None:
        pages.append(holder.page)
    for name in ("shared_page", "auth_page", "auth_trains_page", "page"):
        candidate = item.funcargs.get(name)
        if isinstance(candidate, Page) and candidate not in pages:
            pages.append(candidate)
    alive: list[Page] = []
    for page in pages:
        try:
            if not page.is_closed():
                alive.append(page)
        except Exception:
            continue
    return alive


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
    if report.failed:
        _report_failure_scene(item, report, base_page)
        _report_diagnostics(item, report)

    return report


def _report_failure_scene(
    item: pytest.Item, report: pytest.TestReport, base_page: BasePage | None
) -> None:
    """失败现场：URL + title + 视口/整页两张截图。

    **两条来源都要覆盖**（2026-11-01 补）：以前只处理 `_base_page`，结果是
    「用例还没构造页面对象就失败」时一张图都没有 —— 而这类失败（比如 goto 就挂了）
    恰恰最需要图。没有页面对象时，退回到 fixture 里的裸 page。
    """
    pages = _pages_of(item)
    target = base_page.page if base_page is not None else (pages[0] if pages else None)
    if target is None:
        return
    try:
        url, title = target.url, target.title()
    except Exception:
        url, title = "(页面已关闭)", "(不可读)"

    capturer = base_page if base_page is not None else BasePage(target)
    try:
        paths = capturer.capture(item.nodeid)
    except Exception:                                  # noqa: BLE001
        paths = []
    report.sections.append(
        (
            "失败现场",
            f"url：{url}\ntitle：{title}\n截图：{', '.join(str(p) for p in paths) or '(截图失败)'}",
        )
    )
    # Allure 侧同时挂：视口图（报告内直接看）+ 现场文本。整页图仍只在磁盘上，
    # 避免报告体积翻倍（见 utils/allure_support.attach_screenshot 的说明）。
    allure_support.attach_screenshot(target, name="失败现场（视口）")
    allure_support.attach_text("失败现场", f"url：{url}\ntitle：{title}")
    get_logger().error("失败现场｜url=%s｜title=%s｜截图=%s", url, title, paths or "(截图失败)")


def _report_diagnostics(item: pytest.Item, report: pytest.TestReport) -> None:
    """把 JS 报错 / HTTP≥400 / 请求失败写进失败报告，并同步落一行日志。

    没有页面对象也要跑：这一段的用途正是「连页面对象都没造出来就失败了」。
    """
    log = get_logger()
    pages = _pages_of(item)
    if not pages:
        # 数据层单测没有页面，往它的报告里塞一段「没有页面诊断」纯属噪音。
        log.debug("用例失败（无页面）：%s", item.nodeid)
        return

    chunks: list[str] = []
    for page in pages:
        diag = diagnostics_of(page)
        if diag is None:
            continue
        try:
            url = page.url
        except Exception:
            url = "(不可读)"
        chunks.append(f"—— {diag.label} @ {url}\n{diag.summary()}")

    if not chunks:
        chunks.append("（页面存在但没挂诊断监听：页面可能不是由本工程的 fixture 创建的）")

    trace_path = _save_e2e_trace(item)
    if trace_path:
        chunks.append(f"trace：{trace_path}")

    report.sections.append(("页面诊断", "\n\n".join(chunks)))
    # Allure 侧：诊断文本 + trace 文件（trace 在 Allure 里是个可下载附件，
    # 用 trace.playwright.dev 打开就能逐帧回放网络与 DOM）。
    allure_support.attach_text("页面诊断", "\n\n".join(chunks))
    if trace_path:
        allure_support.attach_file(trace_path, name="Playwright trace", mime="application/zip")
    log.error("用例失败：%s", item.nodeid)
    for chunk in chunks:
        log.error("%s", chunk)


def _save_e2e_trace(item: pytest.Item) -> Path | None:
    """失败时把 e2e 共享 context 的 trace 落盘，并立刻重新开始录制。

    只有 `E2E_TRACE=1` 且 context 还活着才做。任何异常都吞掉：trace 是辅助证据，
    让它把「失败原因」挤掉就本末倒置了。
    """
    context: BrowserContext | None = item.funcargs.get("shared_context")
    if context is None or not getattr(context, "_e2e_trace_active", False):
        return None
    settings.ensure_dirs()
    safe = re.sub(r"[^0-9A-Za-z_.\-]+", "_", item.nodeid)[:100] or "trace"
    path = settings.TRACE_DIR / f"{safe}.zip"
    try:
        context.tracing.stop(path=str(path))
    except Exception as exc:                      # noqa: BLE001
        get_logger().warning("trace 保存失败（忽略）：%s", exc)
        return None
    try:                                          # 继续录后面的用例
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
    except Exception:                             # noqa: BLE001
        context._e2e_trace_active = False         # type: ignore[attr-defined]
    get_logger().info("trace 已保存：%s", path)
    return path
