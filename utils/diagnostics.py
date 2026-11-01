"""页面级诊断：把「前端 JS 报错」和「HTTP ≥400 / 请求失败」采集到用例报告里。

为什么要它（2026-11-01 用户要求「四位一体」）：
  失败现场现在只有截图 + URL + title。但「元素点不到」的真实原因常常在另外两个地方：
    · 前端抛了 JS 异常，组件根本没渲染出来；
    · 某个接口 4xx/5xx（WAF 的 432、限流的 429），页面停在骨架屏。
  这两类信息 Playwright 不会主动给，只能挂事件监听。

设计上刻意四条：

1. **只监听、不拦截**。`page.on("response")` 是观察者，不 route、不改请求 ——
   route 会改掉被测对象，那不是测试。
2. **URL 一律去掉 query 再记录**。query 里有 `token`、`cticket` 这类东西，
   记全量等于把凭据写进报告；记 path 足够定位「哪个接口挂了」。
3. **去重 + 封顶**。真站点一屏几十条 4xx（埋点、广告）很常见，全记会把报告刷爆，
   真正有用的那几条反而被埋掉。
4. **监听里绝不抛异常**。事件回调抛错会污染 Playwright 的事件循环，
   症状是「用例莫名卡住」，比丢日志严重得多。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from playwright.sync_api import Page

from utils.logger import redact, logger_debug, logger_warning

MAX_ENTRIES = 20
# 埋点/统计/图标类噪音，记进来只是干扰
_IGNORED_HOSTS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net")
_IGNORED_SUFFIXES = (".ico", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2")

_LOCK = threading.Lock()


@dataclass
class PageDiagnostics:
    """一个 page 的诊断积累。"""

    label: str
    js_errors: list[str] = field(default_factory=list)
    http_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """给报告用的多行文本。空的话也明说，避免「没记录」被读成「没问题」。"""
        if not (self.js_errors or self.http_errors or self.failed_requests):
            return "（本次未捕获 JS 报错 / 4xx / 请求失败）"
        lines: list[str] = []
        if self.js_errors:
            lines.append(f"JS 报错 {len(self.js_errors)} 条：")
            lines.extend(f"  · {item}" for item in self.js_errors)
        if self.http_errors:
            lines.append(f"HTTP ≥400 共 {len(self.http_errors)} 条：")
            lines.extend(f"  · {item}" for item in self.http_errors)
        if self.failed_requests:
            lines.append(f"请求失败 {len(self.failed_requests)} 条：")
            lines.extend(f"  · {item}" for item in self.failed_requests)
        return "\n".join(lines)


# page 对象上不好挂自定义属性（Playwright 的同步封装类没保证），所以用外部登记表。
# 键用 id(page)：page 生命周期内唯一即可，泄漏量级是「一次会话的页面数」，可忽略。
_REGISTRY: dict[int, PageDiagnostics] = {}


def _path_of(url: str) -> str:
    """URL → `host/path`（丢掉 query 与 fragment，凭据都在 query 里）。"""
    try:
        parts = urlsplit(url)
        return f"{parts.netloc}{parts.path}"
    except Exception:
        return redact(url)


def _noise(url: str) -> bool:
    path = _path_of(url).lower()
    if any(host in path for host in _IGNORED_HOSTS):
        return True
    return any(path.endswith(suffix) for suffix in _IGNORED_SUFFIXES)


def instrument_page(page: Page, label: str = "page") -> PageDiagnostics:
    """给 page 挂上 console / response / requestfailed 三个监听（幂等）。"""
    with _LOCK:
        existing = _REGISTRY.get(id(page))
        if existing is not None:
            return existing
        diag = PageDiagnostics(label=label)
        _REGISTRY[id(page)] = diag

    def on_console(msg) -> None:
        try:
            if msg.type != "error":
                return
            text = redact(msg.text)[:300]
            if len(diag.js_errors) < MAX_ENTRIES and text not in diag.js_errors:
                diag.js_errors.append(text)
            logger_debug(f"[{label}] JS error: {text}")
        except Exception:
            return

    def on_pageerror(error) -> None:
        """未捕获异常走的是 `pageerror`，**不是** console。

        2026-11-01 实测：`setTimeout(() => { throw new Error('boom') })` 只触发
        `pageerror`，`page.on("console")` 一条都收不到。只挂 console 会漏掉
        「组件初始化就抛异常」这种最要命的失败。
        """
        try:
            text = redact(str(error))[:300]
            entry = f"未捕获异常：{text}"
            if len(diag.js_errors) < MAX_ENTRIES and entry not in diag.js_errors:
                diag.js_errors.append(entry)
            logger_warning(f"[{label}] 未捕获异常：{text}")
        except Exception:
            return

    def on_response(response) -> None:
        try:
            if response.status < 400:
                return
            if _noise(response.url):
                return
            entry = f"{response.status} {response.request.method} {_path_of(response.url)}"
            if len(diag.http_errors) < MAX_ENTRIES and entry not in diag.http_errors:
                diag.http_errors.append(entry)
                logger_warning(f"[{label}] HTTP {entry}")
        except Exception:
            return

    def on_requestfailed(request) -> None:
        try:
            if _noise(request.url):
                return
            entry = f"{request.method} {_path_of(request.url)} → {request.failure}"
            if len(diag.failed_requests) < MAX_ENTRIES and entry not in diag.failed_requests:
                diag.failed_requests.append(entry)
                logger_warning(f"[{label}] 请求失败：{entry}")
        except Exception:
            return

    try:
        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)
        page.on("requestfailed", on_requestfailed)
    except Exception:
        # 页面已关闭时挂监听会抛；拿不到诊断不该让用例失败。
        pass
    return diag


def diagnostics_of(page: Page) -> PageDiagnostics | None:
    """取已登记的诊断；没登记过返回 None（不自动补挂，避免在收尾阶段动页面）。"""
    if page is None:
        return None
    return _REGISTRY.get(id(page))


def forget_page(page: Page) -> None:
    """页面销毁时清登记，避免 id() 复用导致串味。"""
    with _LOCK:
        _REGISTRY.pop(id(page), None)
