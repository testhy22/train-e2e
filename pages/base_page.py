"""Page Object 基类：只放「任何页面都会用到」的五件事。

1. 有界等待 —— 等待状态一律 expect / 自动等待，没有 time.sleep。
   **唯一允许的固定停顿**来自 `utils/humanize.py` 的拟人节奏：它只发生在动作之间、
   只为了让人能看清，且**不得**用来等页面状态（那会退化回硬 sleep）。默认仅在有头模式开启。
2. 风控识别 —— blocked 是一等结果：识别出来就抛 BlockedByAntiBot，交给上层转 xfail。
3. 失败证据 —— 视口 + 全页截图，文件名取自 nodeid，能直接对回报告里那一条。
4. 导航 —— 只做 goto + 载入状态等待，不做任何头部/指纹改写。
5. 营销浮层 —— 导航后关掉站点自己弹的领券条（`utils/overlay.py`）。
   挂在这里而不是挂成 autouse fixture：autouse 一旦请求 `page`，数据层单测也会被
   拉起来起一个浏览器，代价远大于收益。导航是所有 UI 用例的必经点，挂这里最省。

不做什么（重要）：
  不改 User-Agent、不改 navigator.webdriver、不注入 stealth 脚本、不按「哪里被拦」调节奏。
  被风控拦下就是被拦下，工程上承认它、记录它、不假装通过。

关于拟人节奏的边界（2026-11-01 用户要求「模拟人正常的速度操作」后新增）：
  只调**时间**、不碰**身份**；只在有头模式默认开启；**不因被拦而调慢重试**。
  三条任一被打破，它就变成了反检测手段 —— 见 `utils/humanize.py` 文件头的四条边界。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from config import settings
from utils.diagnostics import instrument_page
from utils.humanize import Humanizer, human_enabled
from utils.logger import logger_info
from utils.overlay import dismiss_promo_overlays

# ---------------------------------------------------------------- 风控特征

# 文案类特征：出现即视为被拦。宁可多拦几次（xfail 可接受），也不要漏判成「页面没加载出来」。
BLOCK_TEXT_MARKERS: tuple[str, ...] = (
    "访问过于频繁",
    "请求过于频繁",
    "操作过于频繁",
    "请稍后再试",
    "安全验证",
    "人机验证",
    "拖动滑块",
    "滑动验证",
    "请完成验证",
    "异常访问",
)

# 元素类特征：滑块容器、验证码容器、验证码 iframe。
BLOCK_SELECTORS: tuple[str, ...] = (
    "[class*='slide-verify']",
    "[class*='slider-verify']",
    "[class*='captcha']",
    "[id*='captcha']",
    "[class*='verify-container']",
    "iframe[src*='captcha']",
    "iframe[src*='verify']",
    "iframe[src*='validate']",
)

# 拦截页 / 错误页特征。
BLOCK_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*40[13]\b"),
    re.compile(r"访问被拒绝"),
    re.compile(r"Access Denied", re.IGNORECASE),
)

# 风控域名：整页被重定向到这些域时，DOM 可能长得完全正常（甚至搜索框都在），
# 只有 URL 能说明问题。所以它是**第一条**判据，先于所有元素/文本判据。
#
# 与 `passport.ctrip.com` 严格区分，别混：
#   passport = 要求登录，属业务门槛（`utils/auth.looks_like_login_wall`）
#   safe/sec/verify = 被判为风险流量，属 blocked（本表）
# 两者混用的后果很具体：把「没登录」记成「被风控拦」，然后去查风控策略，
# 而真正要做的是重新扫码。
BLOCK_HOST_PATTERNS: tuple[tuple[str, str], ...] = (
    ("safe.ctrip.com", "waf_redirect"),
    ("sec.ctrip.com", "waf_redirect"),
    ("verify.ctrip.com", "waf_redirect"),
)

# 正文正则特征：有些 WAF 不留标题、也不弹验证码，只回一句纯文本。
# 2026-10-31 实机取证：连续访问约十余次后，整页只剩 "whaleguard block"，<title> 为空。
BLOCK_BODY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bwhaleguard\b", re.IGNORECASE), "waf_block"),
    (re.compile(r"访问被拒绝|拒绝访问|非法请求|请求异常|请求被拦截"), "waf_block"),
    (re.compile(r"^\s*block(ed)?\s*$", re.IGNORECASE | re.MULTILINE), "waf_block"),
)


@dataclass(frozen=True)
class BlockSignal:
    """一次判定为「被风控拦下」的证据。"""

    kind: str      # captcha_iframe / slider / rate_limit_text / http_error_page / ...
    detail: str    # 命中的原文或选择器，写进报告用

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"{self.kind}: {self.detail}"


class BlockedByAntiBot(RuntimeError):
    """目标站点返回风控拦截。由测试层转成 xfail，不当作普通失败。"""

    def __init__(self, signal: BlockSignal, *, url: str = "", title: str = "") -> None:
        self.signal = signal
        self.url = url
        self.title = title
        super().__init__(f"blocked by anti-bot [{signal.kind}] {signal.detail} | url={url}")


class BasePage:
    """所有页面对象的最小基类。"""

    def __init__(self, page: Page, base_url: str | None = None) -> None:
        self.page = page
        self.base_url = (base_url or settings.base_url()).rstrip("/")
        self.page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)
        self.page.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
        # 挂诊断监听（幂等）：JS 报错 / HTTP≥400 / 请求失败会攒在这个 page 上，
        # 用例失败时由 conftest 的 makereport 钩子写进报告。
        # 放在基类构造函数里，是因为所有页面对象都从这里过一遍 —— 没有第二个更靠谱的入口。
        self.diagnostics = instrument_page(page, label=self.__class__.__name__)
        # 每次导航后关浮层的处理记录。用例可以读它写进报告，
        # 用来回答「浮层到底关掉没有」—— 不然只能靠猜。
        self.overlay_log: list[str] = []
        # 拟人节奏：默认跟随 HEADED（有头开、无头关），可用 HUMAN=1/0 强制覆盖。
        # HUMAN_SEED 固定后每次跑同一套节奏，便于复现时序问题；不设则每次随机。
        self.human = Humanizer(
            page,
            enabled=human_enabled(settings.HEADLESS, settings.HUMAN_RAW),
            seed=settings.HUMAN_SEED,
        )

    # ------------------------------------------------------------ 导航

    def goto(self, path: str = "/") -> None:
        """打开页面并等载入完成。不传 path 时打开站点根。"""
        url = self.base_url + ("" if path == "/" else path)
        logger_info(f"[{self.__class__.__name__}] 打开 {url}")
        self.page.goto(url, wait_until="domcontentloaded")
        self.wait_ready()
        self.dismiss_overlays()
        logger_info(f"[{self.__class__.__name__}] 已载入 title={self._safe_title()!r}")

    def _safe_title(self) -> str:
        """读标题，读不到就返回占位 —— 只为日志服务，不该影响任何流程。"""
        try:
            return self.page.title()[:80]
        except Exception:
            return "(不可读)"

    def dismiss_overlays(self) -> list[str]:
        """关掉站点自己的营销浮层。

        为什么不失败：浮层是站点随时可加可撤的东西，不该由它决定用例红绿。
        真挡住操作时，红的是被挡的那一步（有截图），不是这里。
        """
        if not settings.OVERLAY_AUTOCLOSE:
            self.overlay_log = []
            return self.overlay_log
        self.overlay_log = dismiss_promo_overlays(self.page)
        return self.overlay_log

    def wait_ready(self) -> None:
        """等 load 事件。用有界等待，超时即抛，不留裸 sleep。"""
        try:
            self.page.wait_for_load_state("load", timeout=settings.NAV_TIMEOUT_MS)
        except PlaywrightTimeout:
            # 载入超时本身不一定是被拦，交给 detect_block 判断；这里只保证不无限等。
            pass

    def open_and_check(self, path: str = "/") -> None:
        """打开并立刻做一次风控体检。被拦就抛，不等后续定位器超时。"""
        self.goto(path)
        self.assert_not_blocked()

    # ------------------------------------------------------------ 风控识别

    def detect_block(self) -> BlockSignal | None:
        """返回命中的风控证据；没有则返回 None。

        三条独立判据，任一条命中即算 blocked：
          元素（滑块/验证码/iframe）、文案、标题或拦截页特征。
        全部用零等待的查询（is_visible / count），不会为了判定而卡住用例。
        """
        # 0. URL 域：被重定向到风控域时页面 DOM 可能一切正常，只有 URL 露馅。
        for host, kind in BLOCK_HOST_PATTERNS:
            if host in (self.page.url or ""):
                return BlockSignal(kind, host)

        # 1. 元素类
        for selector in BLOCK_SELECTORS:
            try:
                if self.page.locator(selector).first.is_visible():
                    return BlockSignal("captcha_element", selector)
            except Exception:  # 选择器语法或页面正在跳转，跳过这一条继续判
                continue

        # 2. 文案类（只查可见文本，避免命中 <script> 里的字符串）
        try:
            body_text = self.page.locator("body").inner_text(timeout=2000)
        except Exception:
            body_text = ""
        for marker in BLOCK_TEXT_MARKERS:
            if marker in body_text:
                return BlockSignal("block_text", marker)

        # 2b. 正文正则（WAF 只回一句纯文本、没有标题的情况）
        for pattern, kind in BLOCK_BODY_PATTERNS:
            match = pattern.search(body_text)
            if match:
                return BlockSignal(kind, match.group(0).strip()[:60])

        # 3. 标题 / 拦截页
        title = self.page.title()
        for pattern in BLOCK_TITLE_PATTERNS:
            if pattern.search(title):
                return BlockSignal("error_page", title.strip()[:60])

        return None

    def assert_not_blocked(self) -> None:
        signal = self.detect_block()
        if signal is not None:
            raise BlockedByAntiBot(signal, url=self.page.url, title=self.page.title())

    # ------------------------------------------------------------ 失败证据

    def capture(self, tag: str) -> list[Path]:
        """截两张：当前视口 + 整页。返回落盘路径，写进报告用。

        截图失败不抛异常 —— 取证失败不应该盖掉真正的失败原因。
        """
        settings.ensure_dirs()
        safe = re.sub(r"[^0-9A-Za-z_.\-]+", "_", tag)[:120] or "shot"
        saved: list[Path] = []
        for suffix, full_page in (("viewport", False), ("full", True)):
            path = settings.SCREENSHOT_DIR / f"{safe}__{suffix}.png"
            try:
                self.page.screenshot(path=str(path), full_page=full_page)
                saved.append(path)
            except Exception:
                continue
        return saved

    # ------------------------------------------------------------ 小工具

    def body_text(self, timeout: int = 5000) -> str:
        """整页可见文本。测试层不要在文件里直接写 page.locator("body")。"""
        return self.page.locator("body").inner_text(timeout=timeout)

    def first_visible(self, *selectors: str) -> Locator | None:
        """按顺序返回第一个可见的定位器。用于「多种页面形态」的容错，不做重试。"""
        for selector in selectors:
            locator = self.page.locator(selector).first
            try:
                if locator.is_visible():
                    return locator
            except Exception:
                continue
        return None
