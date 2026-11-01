"""只读探测 16：去掉请求路由拦截器，看 check-login 能否打通、快捷登录是否出现。

**探针 15 的发现（2026-11-01）**：
    微信页面会 POST 六个回环端口 `/api/check-login`：
        localhost.weixin.qq.com:14013 / 14014 / 14015 / 13013 / 13014 / 13015
    实测结果：**14013 返回 ERR_CONNECTION_CLOSED，其余五个 ERR_CONNECTION_REFUSED**。
    `CLOSED` 与 `REFUSED` 的区别是关键：后者是没有服务在听，前者是握手过了、
    连接被中途切断 —— 说明微信客户端**确实在 14013 上跑着**（netstat 已证实：
    `127.0.0.1:14013 LISTENING  PID 18884`，且有 ESTABLISHED 连接）。

**本轮怀疑**：本项目给 context 注册了 `context.route("**/*", ...)` 来做 URL 清洗。
    Playwright 的路由拦截会把**每个**请求（包括回环上的 HTTPS 请求）改由它自己
    重新发起，这会破坏微信本地服务依赖的连接特性（客户端证书 / 连接复用 / SNI 等），
    表现就是 ERR_CONNECTION_CLOSED。

本探针**唯一变量**：不注册 route。
    - 保留 `permissions=["local-network-access"]`（否则有原生气泡，点不到）；
    - 不装 `install_dirty_url_guard`；
    - 其余（viewport/locale/timezone/入口/勾协议/点微信）完全一致。
    首屏导航仍用 `clean_url()` 直接在 URL 上剥参数，不依赖路由。

跑法：
    python tools/probes/probe_login_no_route.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import (  # noqa: E402
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from config import settings  # noqa: E402
from scripts.setup_auth import AGREE_INPUT, WECHAT_ENTRY_CANDIDATES, clean_url, probe_page  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_login_no_route" / datetime.now().strftime("%Y%m%d-%H%M%S")

_STATE_JS = r"""
() => {
  const one = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height),
            display: getComputedStyle(el).display,
            text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60)};
  };
  return {qr: one('img.js_qrcode_img'), quick_login: one('.js_quick_login'),
          quick_btn: one('.js_quick_login_btn'), body_class: document.body.className};
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log("=" * 70)
    log(f"落盘：{OUT}")
    log("变量：不注册 context.route()，其余同前")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=["local-network-access"],
        )
        # 刻意不调用 install_dirty_url_guard(context)

        check_logins: list[dict] = []

        def on_response(resp):
            if "/api/check-login" in resp.url:
                body = ""
                try:
                    body = resp.text()[:300]
                except Exception:
                    body = "(body 不可读)"
                check_logins.append({"url": resp.url, "status": resp.status, "body": body})

        def on_failed(req):
            if "/api/check-login" in req.url:
                check_logins.append({"url": req.url, "failure": str(req.failure)})

        context.on("response", on_response)
        context.on("requestfailed", on_failed)

        try:
            page = context.new_page()
            page.set_default_timeout(settings.DEFAULT_TIMEOUT_MS)

            log("[1] www → 登录 → 勾协议 → 微信")
            page.goto(clean_url("https://www.ctrip.com/"), wait_until="domcontentloaded")
            dismiss_promo_overlays(page, tag="首页")
            page.get_by_text("登录", exact=True).first.click(timeout=10000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeout:
                pass
            page.locator(AGREE_INPUT).check(timeout=4000, force=True)

            popup = None
            for selector in WECHAT_ENTRY_CANDIDATES:
                try:
                    loc = page.locator(selector).first
                    if loc.count() == 0 or not loc.is_visible():
                        continue
                    with context.expect_page(timeout=12000) as info:
                        loc.click(timeout=8000)
                    popup = info.value
                    log(f"    ✓ popup：{popup.url[:88]}")
                    break
                except PlaywrightTimeout:
                    continue
                except Exception:
                    continue
            if popup is None:
                log("✗ 没拿到 popup")
                return 0

            try:
                popup.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightTimeout:
                pass

            log("\n[2] 轮询 40s 等快捷登录模板")
            appeared_at = None
            started = __import__("time").time()
            snapshot: dict = {}
            while __import__("time").time() - started < 40:
                try:
                    snapshot = popup.evaluate(_STATE_JS)
                except Exception as exc:
                    snapshot = {"error": str(exc)}
                ql = snapshot.get("quick_login") or {}
                qb = snapshot.get("quick_btn") or {}
                if (ql.get("w") or 0) > 10 or (qb.get("w") or 0) > 10:
                    appeared_at = __import__("time").time() - started
                    break
                popup.wait_for_timeout(1000)

            log(f"    t={__import__('time').time() - started:.1f}s：{json.dumps(snapshot, ensure_ascii=False)}")

            log(f"\n[3] check-login 结果 {len(check_logins)} 条")
            for one in check_logins:
                log(f"    {json.dumps(one, ensure_ascii=False)[:220]}")

            (OUT / "result.json").write_text(
                json.dumps({"appeared_at": appeared_at, "snapshot": snapshot,
                            "check_logins": check_logins}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            probe_page(popup, OUT, "popup_after_wait")

            log("\n" + "=" * 70)
            if appeared_at is not None:
                log(f"✓ 快捷登录出现，等待 {appeared_at:.1f}s")
            else:
                log("✗ 仍未出现")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
