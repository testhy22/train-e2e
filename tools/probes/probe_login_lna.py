"""只读探测 14：授予 local-network-access 权限后，「微信快捷登录」是否出现。

**根因（2026-11-01 用户截图坐实）**：
    Chromium 156 在 `open.weixin.qq.com` 上会弹原生权限气泡
    「open.weixin.qq.com 想要 访问此设备上的其他应用和服务」——
    这是 Local Network Access（LNA）权限。微信页面只有在拿到它之后，
    才能探测本机微信客户端，才会把模板从「二维码」切成「微信快捷登录」。
    本项目 `permissions=[]`（为防原生权限框挡点击而设）把它**预拒绝**了，
    所以自动化里永远只有二维码。

本探针验证一件事：授予 `local-network-access` 后，
    `div.js_quick_login` 是否变为可见、`button.js_quick_login_btn` 是否可点。

**仍然不填账号、不伪造客户端探测结果。** 只在按钮出现时点它一下，
看微信客户端是否弹确认、主页面是否变成已登录。

跑法：
    python tools/probes/probe_login_lna.py
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
from utils.auth import is_logged_in  # noqa: E402
from utils.overlay import dismiss_promo_overlays  # noqa: E402

OUT = settings.REPORTS_DIR / "probe_login_lna" / datetime.now().strftime("%Y%m%d-%H%M%S")

_STATE_JS = r"""
() => {
  const one = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      w: Math.round(r.width), h: Math.round(r.height),
      display: s.display,
      text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 50),
    };
  };
  return {
    qr: one('img.js_qrcode_img'),
    quick_login: one('.js_quick_login'),
    quick_btn: one('.js_quick_login_btn'),
    avatar: one('.js_quick_login img'),
    body_class: document.body.className,
  };
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log("=" * 70)
    log(f"落盘：{OUT}")
    log("=" * 70)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        # 关键改动：唯一一处权限放行，其余照旧（不做任何 UA/指纹改写）
        context = browser.new_context(
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
            timezone_id=settings.TIMEZONE_ID,
            permissions=["local-network-access"],
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
            probe_page(popup, OUT, "01_popup_initial")

            log("\n[2] 轮询 30s 等快捷登录模板出现")
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

            log(f"    t={time.time() - started:.1f}s 状态：{json.dumps(snapshot, ensure_ascii=False)}")
            (OUT / "02_state.json").write_text(
                json.dumps({"appeared_at": appeared_at, "snapshot": snapshot},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            probe_page(popup, OUT, "03_popup_after_wait")

            if appeared_at is None:
                log("✗ 30s 内快捷登录模板仍未显示 —— LNA 权限不是唯一条件")
                return 0

            log(f"✓ 快捷登录模板已出现（{appeared_at:.1f}s）")

            log("\n[3] 点「微信快捷登录」")
            try:
                popup.locator(".js_quick_login_btn").first.click(timeout=8000)
                log("    ✓ 已点击")
            except Exception as exc:
                log(f"    ✗ 点击失败：{type(exc).__name__}: {exc}")
            probe_page(popup, OUT, "04_after_quick_click")

            log("\n[4] 观察 60s：微信客户端可能弹确认框；主页面是否登录")
            started = time.time()
            closed_at: float | None = None
            logged_at: float | None = None
            evidence = ""
            while time.time() - started < 60:
                now = time.time() - started
                try:
                    if closed_at is None and popup.is_closed():
                        closed_at = now
                        log(f"    popup 于 {now:.1f}s 关闭")
                except Exception:
                    pass
                ok, ev = is_logged_in(page)
                evidence = ev
                if ok:
                    logged_at = now
                    log(f"    ✓ 主页面判为已登录（{now:.1f}s）：{ev}")
                    break
                if int(now) % 10 == 9:
                    log(f"    …{int(now) + 1}s  popup_closed={popup.is_closed()}  主页面={ev}")
                page.wait_for_timeout(1000)

            probe_page(page, OUT, "05_home_after_flow")

            log("\n" + "=" * 70)
            log(f"结论：快捷登录出现={appeared_at is not None}  popup关闭={closed_at}  主页登录={logged_at is not None}")
            log(f"现场：{OUT}")
            log("=" * 70)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
