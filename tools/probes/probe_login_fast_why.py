"""只读探测 13：为什么 Playwright 里只出二维码、出不来「微信快捷登录」？

**问题（2026-11-01）**：同一台机器、同一个 appid（wx29cad1c2709a80bc），
    用户在**日常 Edge** 里打开授权页看到「使用微信快捷登录 + 头像 + 微信快捷登录按钮」，
    而 Playwright 里打开只有二维码。DOM 里两套模板都在：
        div.js_normal_login     ← 二维码模式（当前显示）
        div.js_quick_login      ← 快捷登录模式（0×0 未激活）
        button.js_switchToFast  ← 二维码模式下的「快捷登录」切换入口（0×0）
        button.js_quick_login_btn ← 真正的「微信快捷登录」按钮（0×0）

三组对照，逐层排除：
    A. popup.bring_to_front() 后再等 10s —— 排除「窗口没焦点，页面按后台处理」
    B. force click button.js_switchToFast —— 排除「只是没点切换入口」
    C. 全程记录 document.visibilityState / hasFocus —— 拿到判断依据

**不做的事**：不改 UA、不注入 stealth、不伪造微信客户端探测结果。
探测不到就是探测不到，工程上记录它。

跑法：
    python tools/probes/probe_login_fast_why.py
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
    Page,
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

OUT = settings.REPORTS_DIR / "probe_login_fast_why" / datetime.now().strftime("%Y%m%d-%H%M%S")

_STATE_JS = r"""
() => {
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height)};
  };
  const one = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const s = getComputedStyle(el);
    return {
      ...box(el),
      display: s.display,
      visibility: s.visibility,
      text: (el.innerText || '').trim().slice(0, 40),
    };
  };
  return {
    visibilityState: document.visibilityState,
    hasFocus: document.hasFocus(),
    hidden: document.hidden,
    qr: one('img.js_qrcode_img'),
    normal_login: one('.js_normal_login'),
    quick_login: one('.js_quick_login'),
    quick_btn: one('.js_quick_login_btn'),
    switch_to_fast: one('.js_switchToFast'),
    // 微信页面可能把判定结果挂在 body class 上
    body_class: document.body.className,
  };
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def state(page: Page, label: str) -> dict:
    try:
        s = page.evaluate(_STATE_JS)
    except Exception as exc:
        s = {"error": str(exc)}
    log(f"  [{label}] {json.dumps(s, ensure_ascii=False)}")
    return s


def visible_count(page: Page) -> int:
    """当前有几套模板真的显示出来了（宽高都 > 10 才算）。"""
    try:
        return page.evaluate(
            """() => {
              const n = (el) => {
                if (!el) return 0;
                const r = el.getBoundingClientRect();
                return (r.width > 10 && r.height > 10) ? 1 : 0;
              };
              return n(document.querySelector('.js_quick_login'))
                   + n(document.querySelector('img.js_qrcode_img'));
            }"""
        )
    except Exception:
        return 0


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log("=" * 70)
    log(f"落盘：{OUT}")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=[],
        )
        install_dirty_url_guard(context)
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
            log(f"    协议 is_checked={page.locator(AGREE_INPUT).is_checked()}")

            popup = None
            for selector in WECHAT_ENTRY_CANDIDATES:
                try:
                    loc = page.locator(selector).first
                    if loc.count() == 0 or not loc.is_visible():
                        continue
                    with context.expect_page(timeout=12000) as info:
                        loc.click(timeout=8000)
                    popup = info.value
                    log(f"    ✓ popup：{popup.url[:90]}")
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

            log("\n[A] 先等 10s（不干预），看是否自己切到快捷登录")
            popup.wait_for_timeout(10000)
            state(popup, "A1 静置10s")

            log("\n[B] popup.bring_to_front() 后再等 10s")
            try:
                popup.bring_to_front()
                log("    ✓ 已前置")
            except Exception as exc:
                log(f"    ✗ 前置失败：{exc}")
            popup.wait_for_timeout(10000)
            state(popup, "B1 前置后10s")

            log("\n[C] force click button.js_switchToFast（快捷登录切换）")
            clicked = False
            for sel in ("button.js_switchToFast", ".js_switchToFast", "text=快捷登录"):
                try:
                    loc = popup.locator(sel).first
                    if loc.count() == 0:
                        log(f"    {sel} 不存在")
                        continue
                    loc.click(timeout=4000, force=True)
                    clicked = True
                    log(f"    ✓ 已 force 点击 {sel}")
                    break
                except Exception as exc:
                    log(f"    {sel} 点击失败：{type(exc).__name__}")
                    continue
            popup.wait_for_timeout(5000)
            after = state(popup, "C1 点击切换后5s")
            probe_page(popup, OUT, "after_switch_fast")

            quick_ok = bool(after.get("quick_login") and after["quick_login"]["w"] > 10)
            log(f"\n结论：切换按钮 clicked={clicked}  快捷登录面板可见={quick_ok}")

            if quick_ok:
                log("\n[D] 快捷登录面板已出现，尝试点 js_quick_login_btn")
                try:
                    popup.locator(".js_quick_login_btn").first.click(timeout=6000, force=True)
                    log("    ✓ 已点击「微信快捷登录」")
                except Exception as exc:
                    log(f"    ✗ 点击失败：{type(exc).__name__}: {exc}")
                probe_page(popup, OUT, "after_quick_login_click")
                log("    观察 40s（人工观察微信客户端是否弹确认框）")
                for i in range(40):
                    popup.wait_for_timeout(1000)
                    if i % 10 == 9:
                        log(f"    …{i + 1}s  popup_closed={popup.is_closed()}")
                    if popup.is_closed():
                        break
            else:
                log("    → 切不过去。下一步应查：微信页面靠什么判定本机有客户端")
                log("      （大概率是本地回环探测被 Chromium 的 PNA 策略挡了，见报告）")

            log("\n" + "=" * 70)
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
