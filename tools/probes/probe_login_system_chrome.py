"""只读探测 17：同一套代码，只把浏览器从「Playwright 自带 Chromium」换成「系统 Chrome」。

**为什么做这个对照（2026-11-01）**：
    用户在自己的 Chrome 里打开同一个 qrconnect URL，能出现「使用微信快捷登录 + 头像」；
    而 Playwright 自带 Chromium（156.0.8078.4）里始终只有二维码。
    已排除的因素：
      - 域名解析：nslookup → 127.0.0.1，正常
      - 系统代理：WinHTTP 直连，无代理
      - 请求路由拦截：去掉 context.route() 后仍 ERR_CONNECTION_CLOSED
      - 窗口焦点：visibilityState=visible / hasFocus=true
      - 权限：已 grant local-network-access
    微信本地服务本身是通的：`curl -k -X POST https://localhost.weixin.qq.com:14013/api/check-login`
    返回 HTTP 200（body 是缺参数的 errcode 10000）。

**唯一变量**：`channel="chrome"`，用系统安装的正式版 Chrome 跑。
    这不是伪装身份 —— Playwright 会为它新建临时 profile，不碰用户日常数据；
    只是换一个真实用户在用的浏览器二进制。

跑法：
    python tools/probes/probe_login_system_chrome.py
"""

from __future__ import annotations

import json
import sys
import time
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

OUT = settings.REPORTS_DIR / "probe_login_chrome" / datetime.now().strftime("%Y%m%d-%H%M%S")

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
    log("浏览器：系统 Chrome（channel='chrome'）")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=False)
        log(f"实际版本：{browser.version}")
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=["local-network-access"],
        )
        # 依旧不注册 route（上一轮已排除它不是主因，这里保持最小变量）

        checks: list[dict] = []

        def on_response(resp):
            if "/api/check-login" in resp.url:
                body = ""
                try:
                    body = resp.text()[:300]
                except Exception:
                    body = "(body 不可读)"
                checks.append({"url": resp.url, "status": resp.status, "body": body})

        def on_failed(req):
            if "/api/check-login" in req.url:
                checks.append({"url": req.url, "failure": str(req.failure)})

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
            started = time.time()
            snapshot: dict = {}
            while time.time() - started < 40:
                try:
                    snapshot = popup.evaluate(_STATE_JS)
                except Exception as exc:
                    snapshot = {"error": str(exc)}
                ql = snapshot.get("quick_login") or {}
                qb = snapshot.get("quick_btn") or {}
                if (ql.get("w") or 0) > 10 or (qb.get("w") or 0) > 10:
                    appeared_at = time.time() - started
                    break
                popup.wait_for_timeout(1000)

            log(f"    t={time.time() - started:.1f}s：{json.dumps(snapshot, ensure_ascii=False)}")
            log(f"\n[3] check-login {len(checks)} 条")
            for one in checks:
                log(f"    {json.dumps(one, ensure_ascii=False)[:220]}")

            (OUT / "result.json").write_text(
                json.dumps({"appeared_at": appeared_at, "snapshot": snapshot, "checks": checks},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            probe_page(popup, OUT, "popup_after_wait")

            if appeared_at is not None:
                log(f"\n[4] 快捷登录已出现（{appeared_at:.1f}s），点它")
                try:
                    popup.locator(".js_quick_login_btn").first.click(timeout=8000)
                    log("    ✓ 已点击「微信快捷登录」")
                except Exception as exc:
                    log(f"    ✗ 点击失败：{type(exc).__name__}: {exc}")
                probe_page(popup, OUT, "after_quick_click")
                log("    观察 30s（微信客户端可能弹确认框）")
                for i in range(30):
                    popup.wait_for_timeout(1000)
                    if popup.is_closed():
                        log(f"    popup 于 {i + 1}s 关闭")
                        break
                    if i % 10 == 9:
                        log(f"    …{i + 1}s 仍开着")

            log("\n" + "=" * 70)
            log(f"结论：快捷登录出现={appeared_at is not None}")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
