"""只读探测 15：微信页面到底在探测什么？关掉 LNA 检查能不能让快捷登录出现？

已知（探针 13/14）：
    - 页面 DOM 里两套模板都在，`div.js_quick_login` 是 display:none（未激活）；
    - popup 有焦点、visibilityState=visible，所以不是"后台标签"问题；
    - `grant_permissions(["local-network-access"])` 授了权，仍未切换。

本探针换两个角度：
    1. **抓请求**：监听所有出站请求，筛 localhost / 127.0.0.1 / weixin 相关，
       记录命中与失败原因 —— 微信探测客户端必须落在回环地址上。
    2. **关掉 LNA 检查**：`--disable-features=LocalNetworkAccessChecks,...`。
       这等价于用户点「允许」，只是免掉那个点不到的**原生气泡**；
       不伪造探测结果、不改 UA、不影响页面拿到真实返回值。

跑法：
    python tools/probes/probe_login_lna2.py
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
from scripts.setup_auth import (  # noqa: E402
    AGREE_INPUT,
    WECHAT_ENTRY_CANDIDATES,
    clean_url,
    install_dirty_url_guard,
    probe_page,
)
from utils.overlay import dismiss_promo_overlays  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_login_lna2" / datetime.now().strftime("%Y%m%d-%H%M%S")

LOCAL_HOSTS = ("localhost", "127.0.0.1", "weixin", "wechat", "wx.qq.com")

LAUNCH_ARGS = [
    "--disable-features=LocalNetworkAccessChecks,"
    "PrivateNetworkAccessSendPreflights,"
    "PrivateNetworkAccessRespectPreflightResults,"
    "BlockInsecurePrivateNetworkRequests",
]

_STATE_JS = r"""
() => {
  const one = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height),
            display: getComputedStyle(el).display,
            text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 50)};
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
    log(f"启动参数：{LAUNCH_ARGS}")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=LAUNCH_ARGS)
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=["local-network-access"],
        )
        install_dirty_url_guard(context)

        requests: list[dict] = []
        failed: list[dict] = []

        def on_request(req):
            url = req.url
            if any(h in url.lower() for h in LOCAL_HOSTS):
                requests.append({"url": url[:200], "method": req.method,
                                 "type": req.resource_type})

        def on_failed(req):
            url = req.url
            if any(h in url.lower() for h in LOCAL_HOSTS):
                failed.append({"url": url[:200], "failure": req.failure})

        context.on("request", on_request)
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

            log("\n[2] 轮询 30s 等快捷登录模板")
            appeared_at: float | None = None
            started = time.time()
            snapshot: dict = {}
            while time.time() - started < 30:
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
            log(f"\n[3] 回环/微信相关请求 {len(requests)} 条，失败 {len(failed)} 条")
            for r in requests[:20]:
                log(f"    → {r['method']} {r['url'][:110]} [{r['type']}]")
            for f in failed[:20]:
                log(f"    ✗ {f['url'][:110]} → {f['failure']}")

            (OUT / "requests.json").write_text(
                json.dumps({"requests": requests, "failed": failed,
                            "appeared_at": appeared_at, "snapshot": snapshot},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            probe_page(popup, OUT, "popup_after_wait")

            log("\n" + "=" * 70)
            log(f"结论：快捷登录出现={appeared_at is not None}  "
                f"（等待 {appeared_at:.1f}s）" if appeared_at else
                f"结论：快捷登录仍未出现；回环请求 {len(requests)} 条")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
